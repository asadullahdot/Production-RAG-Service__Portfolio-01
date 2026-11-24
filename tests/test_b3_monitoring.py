"""B3: request tracing and monitoring numbers (spans per step, p50/p95, tokens, cost, error rate)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from app import metrics, telemetry
from app.db import Database
from app.telemetry import Telemetry
from scripts.make_edge_case_fixtures import too_big_pdf
from tests.conftest import Env, make_settings
from tests.make_files import make_pdf
from tests.test_auth import hdr, login, signup
from tests.test_llm_guard import make, ok_response, server_error

TEXT = "The refund policy allows returns within thirty days of purchase with a receipt kept by the customer."
PDF = make_pdf([TEXT])


def upload(client, name="refund.pdf", data=PDF, headers=None):
    return client.post("/documents", files={"file": (name, data, "application/pdf")}, headers=headers or {})


def ask(client, q="What does the refund policy say about returns within thirty days?", headers=None):
    return client.post("/ask", json={"question": q}, headers=headers or {})


def last_trace(client, route="ask", headers=None):
    items = client.get("/metrics/requests", headers=headers or {}).json()["requests"]
    item = next(i for i in items if i["route"] == route)
    return item, client.get(f"/metrics/requests/{item['request_id']}", headers=headers or {}).json()


# ---- the arithmetic ----------------------------------------------------------------------------------------------
def test_percentiles_interpolate_between_ranks():
    assert metrics.percentile([10, 20, 30, 40, 50], 50) == 30
    assert metrics.percentile([10, 20, 30, 40, 50], 95) == pytest.approx(48.0)
    assert metrics.percentile([7], 95) == 7 and metrics.percentile([], 50) is None
    assert metrics.latency_stats([])["p50"] is None
    assert metrics.latency_stats([100, 200, 300, 400])["p50"] == 250.0


def row(route="ask", status=200, outcome="answered", latency=1000.0, tin=100, tout=50, emb=10, cost=0.001, reason=None,
        ts="2026-11-25T12:00:00.000+00:00"):
    return {"request_id": "r", "ts": ts, "route": route, "status": status, "outcome": outcome, "latency_ms": latency,
            "input_tokens": tin, "output_tokens": tout, "embed_tokens_est": emb, "cost_usd": cost, "reason": reason}


def test_summary_counts_errors_refusals_tokens_and_cost_per_question():
    now = datetime(2026, 11, 25, 12, 30, tzinfo=timezone.utc)
    rows = [row(latency=1000), row(latency=2000), row(latency=3000, outcome="refused", reason="not_in_documents"),
            row(status=503, outcome="error", latency=9000, tin=20, tout=0, cost=0.0005),
            row(status=429, outcome="error", latency=1, tin=0, tout=0, emb=0, cost=0),
            row(route="upload", latency=4000, tin=0, tout=0, emb=500, cost=0.00001)]
    steps = [{"route": "ask", "name": "embed", "duration_ms": 300.0, "offset_ms": 5.0, "status": "ok"},
             {"route": "ask", "name": "answer", "duration_ms": 900.0, "offset_ms": 400.0, "status": "ok"},
             {"route": "ask", "name": "answer", "duration_ms": 1100.0, "offset_ms": 410.0, "status": "error"}]
    s = metrics.summarize(rows, steps, "1h", now)
    assert s["requests"]["total"] == 6 and s["requests"]["server_errors"] == 1 and s["requests"]["rate_limited"] == 1
    assert s["requests"]["error_rate"] == round(1 / 6, 4)
    q = s["questions"]
    assert q["count"] == 5 and q["answered"] == 2 and q["refused"] == 1 and q["refusal_reasons"] == {"not_in_documents": 1}
    assert q["latency_ms"]["count"] == 3 and q["latency_ms"]["p50"] == 2000.0     # only HTTP 200 questions
    assert s["tokens"]["input"] == 100 * 3 + 20 and s["tokens"]["per_question"] == round((320 + 150) / 5, 1)
    assert s["cost"]["total_usd"] == pytest.approx(0.001 * 3 + 0.0005) and s["cost"]["estimate"] is True
    assert s["cost"]["per_question_usd"] == pytest.approx((0.001 * 3 + 0.0005) / 5, abs=1e-6)
    assert [x["name"] for x in s["steps"]["ask"]] == ["embed", "answer"]            # in the order they happen
    assert s["steps"]["ask"][1]["errors"] == 1 and s["steps"]["ask"][1]["count"] == 2
    assert sum(p["requests"] for p in s["series"]) == 6 and sum(p["errors"] for p in s["series"]) == 1


def test_summary_of_nothing_is_empty_not_an_error():
    s = metrics.summarize([], [], "24h", datetime.now(timezone.utc))
    assert s["requests"]["total"] == 0 and s["requests"]["error_rate"] is None and s["cost"]["per_question_usd"] is None
    assert s["questions"]["latency_ms"]["p50"] is None


# ---- tracing through the API --------------------------------------------------------------------------------------
def test_a_question_is_traced_step_by_step_with_tokens_and_cost(tmp_path):
    e = Env(tmp_path, input_screen=True)  # as in production: the model screen runs in parallel with the embedding
    assert upload(e.client).status_code == 201
    r = ask(e.client)
    assert r.status_code == 200 and not r.json()["refused"]
    item, trace = last_trace(e.client)
    assert item["request_id"] == r.json()["request_id"] == r.headers["X-Request-ID"]   # the trace id is the request id
    assert item["status"] == 200 and item["outcome"] == "answered" and item["latency_ms"] > 0
    names = [s["name"] for s in trace["spans"]]
    assert names[0] == "request" and trace["spans"][0]["depth"] == 0
    for step in ("input_guardrails", "registry_check", "screen", "embed", "vector_search", "registry_filter",
                 "answer", "output_guardrails", "citations"):
        assert step in names, step
    assert [s["seq"] for s in trace["spans"]] == list(range(len(names)))
    assert all(s["duration_ms"] >= 0 and s["offset_ms"] >= 0 for s in trace["spans"])
    root_end = trace["spans"][0]["duration_ms"]
    assert all(s["offset_ms"] + s["duration_ms"] <= root_end + 1 for s in trace["spans"])  # nothing outlives the request
    llm = [s for s in trace["spans"] if s["name"] == "llm.generate"]
    assert len(llm) == 2 and all(s["depth"] == 2 and s["attrs"]["tokens.input"] == 10 for s in llm)  # screen call + answer call
    assert item["input_tokens"] == 20 and item["output_tokens"] == 10 and item["cost_usd"] > 0


def test_an_upload_is_traced_through_its_steps(tmp_path):
    e = Env(tmp_path)
    assert upload(e.client).status_code == 201
    item, trace = last_trace(e.client, "upload")
    names = [s["name"] for s in trace["spans"]]
    for step in ("validate", "extract", "chunk", "embed_chunks", "vector_upsert", "registry_write"):
        assert step in names, step
    assert item["status"] == 201 and item["embed_tokens_est"] > 0
    assert e.client.delete(f"/documents/{e.client.get('/documents').json()['documents'][0]['doc_id']}").status_code == 204
    assert "vector_delete" in [s["name"] for s in last_trace(e.client, "delete")[1]["spans"]]


def test_a_failing_step_is_recorded_as_an_error_with_the_error_type_only(tmp_path):
    e = Env(tmp_path)
    upload(e.client)
    e.openai.fail_embed = True
    assert ask(e.client).status_code == 503
    item, trace = last_trace(e.client)
    assert item["status"] == 503 and item["outcome"] == "error" and item["error_code"] == "upstream_unavailable"
    failed = [s for s in trace["spans"] if s["status"] == "error"]
    assert {s["name"] for s in failed} >= {"embed"} and failed[0]["attrs"]["error.type"] == "UpstreamUnavailable"
    summary = e.client.get("/metrics/summary", params={"window": "1h"}).json()
    assert summary["requests"]["server_errors"] == 1 and summary["requests"]["error_rate"] > 0


def test_refusals_are_not_errors(tmp_path):
    e = Env(tmp_path)
    assert ask(e.client).json()["reason"] == "no_documents"
    item, _ = last_trace(e.client)
    assert item["status"] == 200 and item["outcome"] == "refused" and item["reason"] == "no_documents"
    s = e.client.get("/metrics/summary").json()
    assert s["requests"]["error_rate"] == 0 and s["questions"]["refused"] == 1 and s["questions"]["refusal_reasons"] == {"no_documents": 1}


def test_rejected_requests_are_recorded_too(tmp_path):
    e = Env(tmp_path, rate_limit_per_min=2)
    upload(e.client)
    assert [ask(e.client).status_code for _ in range(3)] == [200, 200, 429]
    big = upload(e.client, "huge.pdf", too_big_pdf())
    assert big.status_code == 413
    items = e.client.get("/metrics/requests").json()["requests"]
    assert {(i["route"], i["status"], i["error_code"]) for i in items} >= {("ask", 429, "rate_limited"), ("upload", 413, "file_too_large")}
    assert e.client.get("/metrics/summary").json()["requests"]["rate_limited"] == 1


def test_health_docs_static_and_the_metrics_calls_are_not_traced(tmp_path):
    e = Env(tmp_path)
    e.client.get("/health")
    e.client.get("/metrics/summary")
    e.client.get("/")
    e.client.get("/docs")
    assert e.client.get("/metrics/requests").json()["requests"] == []


# ---- privacy and isolation -------------------------------------------------------------------------------------------
def test_questions_answers_and_document_text_are_never_stored(tmp_path):
    e = Env(tmp_path)
    secret_doc = "The vault code is zephyrquartz-4412 and nobody else should read it in any trace."
    upload(e.client, "vault.pdf", make_pdf([secret_doc]))
    e.openai.script.extend(["OK", "The code is zephyrquartz-4412 [1]"])
    ask(e.client, "What is the vault code zephyrquartz-4412 mentioned in the policy?")
    dump = json.dumps([dict(r) for r in e.container.db._run("SELECT * FROM requests")] +
                      [dict(r) for r in e.container.db._run("SELECT * FROM spans")])
    assert "zephyrquartz" not in dump and "vault" not in dump


def test_each_user_sees_only_their_own_traces(tmp_path):
    e = Env(tmp_path, auth_enabled=True)
    c = e.client
    signup(c, "a@example.com"); signup(c, "b@example.com")
    ta = hdr(login(c, "a@example.com").json()["access_token"])
    tb = hdr(login(c, "b@example.com").json()["access_token"])
    upload(c, headers=ta)
    rid = ask(c, headers=ta).json()["request_id"]
    assert c.get(f"/metrics/requests/{rid}", headers=ta).status_code == 200
    assert c.get(f"/metrics/requests/{rid}", headers=tb).status_code == 404
    assert c.get("/metrics/requests", headers=tb).json()["requests"] == []
    assert c.get("/metrics/summary", headers=tb).json()["requests"]["total"] == 0
    assert c.get("/metrics/summary", headers=ta).json()["requests"]["total"] == 2
    for path in ("/metrics/summary", "/metrics/requests", f"/metrics/requests/{rid}"):
        assert c.get(path).status_code == 401


def test_bad_window_is_a_422_and_unknown_trace_a_404(tmp_path):
    e = Env(tmp_path)
    assert e.client.get("/metrics/summary", params={"window": "forever"}).status_code == 422
    assert e.client.get("/metrics/requests/nope").json()["error"]["code"] == "not_found"


# ---- the store ----------------------------------------------------------------------------------------------------------
def test_old_traces_are_pruned_to_the_configured_number(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    for i in range(120):
        r = row(ts=f"2026-11-25T12:{i // 60:02d}:{i % 60:02d}.000+00:00")
        r.update(request_id=f"r{i}", owner_id="o", method="POST")
        db.add_trace(r, [{"seq": 0, "name": "request", "depth": 0, "offset_ms": 0, "duration_ms": 1, "status": "ok", "attrs": "{}"}])
    db.prune_traces(100)
    kept = db.trace_requests("o")
    assert len(kept) == 100 and kept[0]["request_id"] == "r119" and db.get_trace("o", "r0") is None
    assert db._run("SELECT COUNT(*) AS n FROM spans")[0]["n"] == 100


def test_the_same_request_id_twice_replaces_instead_of_failing(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    r = row(); r.update(request_id="same", owner_id="o", method="POST")
    span = {"seq": 0, "name": "request", "depth": 0, "offset_ms": 0, "duration_ms": 1, "status": "ok", "attrs": "{}"}
    db.add_trace(r, [span]); db.add_trace(r, [span])
    assert len(db.trace_requests("o")) == 1


# ---- the model wrapper: tokens and retries on the span --------------------------------------------------------
def test_model_calls_record_tokens_retries_and_cost(tmp_path):
    settings = make_settings(tmp_path)
    db = Database(settings.sqlite_path)
    tel = Telemetry(settings, db)
    telemetry.configure(tel)
    try:
        client, _, _ = make([server_error(), ok_response("fine")], max_retries=3)
        with tel.request("ask", "rid-1", "POST") as tr:
            tr.owner = None
            asyncio.run(client.generate("system", "user"))
            tr.set(status=200, owner_id="o")
        found = db.get_trace("o", "rid-1")
        assert found is not None
        req, spans = found
        llm = next(s for s in spans if s["name"] == "llm.generate")
        attrs = json.loads(llm["attrs"])
        assert attrs["tokens.input"] == 3 and attrs["tokens.output"] == 2 and attrs["retries"] == 1
        expected = (3 * settings.llm_price_in_per_m + 2 * settings.llm_price_out_per_m) / 1e6
        assert req["cost_usd"] == pytest.approx(expected)
    finally:
        telemetry.configure(None)


def test_model_calls_outside_a_request_are_not_traced(tmp_path):
    """The evaluation, notebooks and unit tests call the wrapper with no request: nothing may be written."""
    settings = make_settings(tmp_path)
    db = Database(settings.sqlite_path)
    telemetry.configure(Telemetry(settings, db))
    try:
        client, _, _ = make([ok_response("fine")])
        assert asyncio.run(client.generate("s", "u")).text == "fine"
        assert db._run("SELECT COUNT(*) AS n FROM requests")[0]["n"] == 0
    finally:
        telemetry.configure(None)


def test_monitoring_can_be_switched_off(tmp_path):
    e = Env(tmp_path, telemetry_enabled=False)
    upload(e.client)
    assert ask(e.client).status_code == 200
    assert e.client.get("/metrics/requests").json()["requests"] == []


def test_a_telemetry_failure_never_breaks_the_request(tmp_path, monkeypatch):
    e = Env(tmp_path)
    upload(e.client)
    monkeypatch.setattr(e.container.db, "add_trace", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    assert ask(e.client).status_code == 200
