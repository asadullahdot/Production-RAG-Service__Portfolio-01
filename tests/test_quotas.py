"""Per-user limits: documents, chunks and daily model tokens (0 = no limit)."""
from __future__ import annotations

import pytest

from app import db as db_module
from app.config import Settings
from app.db import Database
from app.errors import QuotaExceeded
from tests.conftest import Env
from tests.make_files import make_pdf
from tests.test_auth import hdr, login, signup


def pdf(i: int, repeat: int = 1) -> bytes:
    return make_pdf([f"Document number {i}. " + (f"Section about topic {i} with its own distinct wording for the tests. " * repeat)])


def upload(client, i: int, repeat: int = 1, headers=None):
    return client.post("/documents", files={"file": (f"doc{i}.pdf", pdf(i, repeat), "application/pdf")}, headers=headers or {})


def ask(client, headers=None, q="What does document number 1 say about its topic?"):
    return client.post("/ask", json={"question": q}, headers=headers or {})


# ---- defaults ------------------------------------------------------------------------------------------------------
def test_the_default_limits_are_ten_documents_five_thousand_chunks_and_a_hundred_thousand_tokens():
    s = Settings(_env_file=None)
    assert (s.max_docs_per_user, s.max_chunks_per_user, s.daily_token_limit_per_user) == (10, 5000, 100_000)


# ---- documents -------------------------------------------------------------------------------------------------------
def test_the_document_limit_rejects_the_next_upload_without_storing_anything(tmp_path):
    e = Env(tmp_path, max_docs_per_user=2)
    assert [upload(e.client, i).status_code for i in (1, 2)] == [201, 201]
    stored = e.count("local")
    r = upload(e.client, 3)
    body = r.json()["error"]
    assert r.status_code == 403 and body["code"] == "quota_exceeded" and "2 documents" in body["message"]
    assert body["details"] == {"kind": "documents", "limit": 2, "used": 2}
    assert e.count("local") == stored and len(e.client.get("/documents").json()["documents"]) == 2


def test_a_duplicate_at_the_limit_is_still_a_409_and_deleting_makes_room(tmp_path):
    e = Env(tmp_path, max_docs_per_user=1)
    first = upload(e.client, 1)
    assert first.status_code == 201
    assert upload(e.client, 1).status_code == 409                 # the same file: duplicate, not a limit error
    assert upload(e.client, 2).status_code == 403
    assert e.client.delete(f"/documents/{first.json()['doc_id']}").status_code == 204
    assert upload(e.client, 2).status_code == 201


def test_the_limits_apply_to_each_user_separately(tmp_path):
    e = Env(tmp_path, auth_enabled=True, max_docs_per_user=1)
    c = e.client
    signup(c, "a@example.com"); signup(c, "b@example.com")
    ta = hdr(login(c, "a@example.com").json()["access_token"])
    tb = hdr(login(c, "b@example.com").json()["access_token"])
    assert upload(c, 1, headers=ta).status_code == 201
    assert upload(c, 2, headers=ta).status_code == 403
    assert upload(c, 3, headers=tb).status_code == 201            # B is not affected by A's use


def test_zero_means_no_limit(tmp_path):
    e = Env(tmp_path, max_docs_per_user=0, max_chunks_per_user=0)
    assert all(upload(e.client, i).status_code == 201 for i in range(1, 13))


# ---- chunks ----------------------------------------------------------------------------------------------------------
def test_the_chunk_limit_is_checked_before_anything_is_embedded(tmp_path):
    e = Env(tmp_path, chunk_size=200, chunk_overlap=20, max_chunks_per_user=8)
    first = upload(e.client, 1, repeat=3)
    assert first.status_code == 201
    used = first.json()["num_chunks"]
    assert 1 < used < 8
    embeds_before = e.openai.embed_calls
    r = upload(e.client, 2, repeat=30)
    assert r.status_code == 403 and r.json()["error"]["details"]["kind"] == "chunks"
    assert e.openai.embed_calls == embeds_before                  # rejected before any embedding was paid for
    assert e.count("local") == used


def test_the_final_check_in_the_registry_is_atomic(tmp_path):
    """Two uploads that both passed the early check cannot both be stored: add_document checks again under the lock."""
    db = Database(str(tmp_path / "q.db"))
    kw = dict(owner_id="o", file_type="pdf", num_pages=1, num_chunks=3)
    db.add_document(doc_id="d1", file_name="a.pdf", file_hash="h1", max_docs=1, max_chunks=10, **kw)
    with pytest.raises(QuotaExceeded):
        db.add_document(doc_id="d2", file_name="b.pdf", file_hash="h2", max_docs=1, max_chunks=10, **kw)
    with pytest.raises(QuotaExceeded):
        db.add_document(doc_id="d3", file_name="c.pdf", file_hash="h3", max_docs=0, max_chunks=5, **kw)
    assert db.usage_counts("o") == (1, 3)


# ---- tokens ----------------------------------------------------------------------------------------------------------
def test_the_daily_token_limit_blocks_before_any_model_call_and_says_when_it_resets(tmp_path):
    e = Env(tmp_path, daily_token_limit_per_user=40)               # the test model reports 15 tokens per call
    upload(e.client, 1)
    assert [ask(e.client).status_code for _ in range(3)] == [200, 200, 200]   # 0, 15, 30 used before each: all below 40
    calls = e.openai.llm_calls
    r = ask(e.client)                                              # 45 used
    err = r.json()["error"]
    assert r.status_code == 429 and err["code"] == "token_quota_exceeded" and "45" in err["message"] and "00:00 UTC" in err["message"]
    assert err["details"]["kind"] == "tokens" and err["details"]["limit"] == 40 and "resets_at" in err["details"]
    assert int(r.headers["Retry-After"]) == err["details"]["retry_after_s"] > 0
    assert e.openai.llm_calls == calls                             # no model call was made for the refused request


def test_the_count_starts_again_on_the_next_utc_day(tmp_path, monkeypatch):
    e = Env(tmp_path, daily_token_limit_per_user=20)
    upload(e.client, 1)
    ask(e.client); ask(e.client)
    assert ask(e.client).status_code == 429
    monkeypatch.setattr(db_module, "utc_day", lambda: "2099-01-01")
    assert ask(e.client).status_code == 200


def test_a_refused_question_still_counts_the_tokens_it_used(tmp_path):
    e = Env(tmp_path, daily_token_limit_per_user=1000)
    upload(e.client, 1)
    e.openai.script.append("NOT_IN_DOCUMENTS")
    assert ask(e.client).json()["refused"] is True
    assert e.container.db.tokens_today("local") == 15


def test_zero_token_limit_never_blocks(tmp_path):
    e = Env(tmp_path, daily_token_limit_per_user=0)
    upload(e.client, 1)
    assert all(ask(e.client).status_code == 200 for _ in range(6))


# ---- GET /usage ------------------------------------------------------------------------------------------------------
def test_usage_endpoint_reports_use_against_the_limits_per_user(tmp_path):
    e = Env(tmp_path, auth_enabled=True, max_docs_per_user=5, max_chunks_per_user=500, daily_token_limit_per_user=1000)
    c = e.client
    signup(c, "a@example.com"); signup(c, "b@example.com")
    ta = hdr(login(c, "a@example.com").json()["access_token"])
    tb = hdr(login(c, "b@example.com").json()["access_token"])
    upload(c, 1, headers=ta)
    ask(c, headers=ta)
    a = c.get("/usage", headers=ta).json()
    assert a["documents"] == {"used": 1, "limit": 5} and a["chunks"]["limit"] == 500 and a["chunks"]["used"] >= 1
    assert a["tokens_today"] == {"used": 15, "limit": 1000} and a["tokens_reset_at"].endswith("00:00:00+00:00")
    b = c.get("/usage", headers=tb).json()
    assert b["documents"]["used"] == 0 and b["tokens_today"]["used"] == 0
    assert c.get("/usage").status_code == 401


def test_usage_is_not_traced_and_reports_zero_limit_as_no_limit(tmp_path):
    e = Env(tmp_path)  # test settings: all limits 0
    assert e.client.get("/usage").json()["documents"]["limit"] == 0
    assert e.client.get("/metrics/requests").json()["requests"] == []
