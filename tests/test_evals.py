"""Metrics with hand-computed toy cases, threshold tuning, judge parsing and an offline end-to-end eval run."""
from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.ingestion.store import InMemoryStore
from app.openai_client import LLMResult
from evals import metrics
from evals.cache import CachedOpenAI, DiskCache
from evals.judge import parse_verdict
from evals.run_eval import evaluate_config, pick_winner, summarise
from tests.fakes import FakeOpenAI
from tests.make_files import make_pdf


# ----------------------------------------------------------------------------- metrics
def test_normalise_and_span_matching():
    assert metrics.normalise("Re-\nfund  POLICY­") == "refund policy"
    chunk = "Returns are accepted within thirty days of purchase, with a valid receipt."
    assert metrics.span_matches(chunk, "within thirty days of purchase")
    assert metrics.span_matches(chunk, "WITHIN   thirty days\nof purchase")
    assert not metrics.span_matches(chunk, "shipping takes five business days")
    # span cut by a chunk boundary: 80% of its words still in order
    assert metrics.span_matches("accepted within thirty days of", "accepted within thirty days of purchase")
    assert not metrics.span_matches("", "anything")


def test_hit_mrr_recall():
    assert metrics.hit_at_k([False, False, True]) == 1.0 and metrics.hit_at_k([False]) == 0.0
    assert metrics.mrr([False, True, True]) == 0.5 and metrics.mrr([False, False]) == 0.0
    chunks = [{"file_name": "a.pdf", "text": "alpha beta gamma delta"}, {"file_name": "b.pdf", "text": "epsilon zeta eta"}]
    assert metrics.span_coverage(chunks, "a.pdf", ["alpha beta gamma", "epsilon zeta eta"]) == (0.5, False)
    assert metrics.span_coverage(chunks, "a.pdf", ["alpha beta gamma"]) == (1.0, True)
    assert metrics.chunk_relevant(chunks[0], "a.pdf", ["beta gamma"]) and not metrics.chunk_relevant(chunks[1], "a.pdf", ["zeta"])


def test_threshold_tuning_picks_balanced_accuracy_and_lower_tie():
    scores = [(0.9, True), (0.8, True), (0.7, True), (0.2, False), (0.1, False)]
    t, curve = metrics.tune_threshold(scores)
    assert 0.2 < t <= 0.7 and dict(curve)[t] == 1.0
    assert t == min(x for x, ba in curve if ba == 1.0)  # ties resolve to the lower threshold


def test_wilson_kappa_and_percentile():
    lo, hi = metrics.wilson_interval(8, 10)
    assert 0.4 < lo < 0.8 < hi < 1.0
    assert metrics.cohen_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == 1.0
    assert metrics.cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == 0.0
    assert metrics.percentile([1, 2, 3, 4, 5], 0.5) == 3 and metrics.percentile([], 0.5) is None


def test_assign_split_is_stratified_and_deterministic():
    qs = [{"id": f"q{i}", "answerable": i % 3 != 0} for i in range(30)]
    other = [dict(q) for q in qs]
    metrics.assign_split(qs); metrics.assign_split(other)
    assert [q["split"] for q in qs] == [q["split"] for q in other]
    for flag in (True, False):
        group = [q for q in qs if q["answerable"] == flag]
        assert any(q["split"] == "dev" for q in group) and any(q["split"] == "test" for q in group)


def test_parse_verdict():
    v = parse_verdict('noise {"claims":[{"text":"a","kind":"document","supported":true},{"text":"b","kind":"document","supported":false},'
                      '{"text":"c","kind":"general"}],"correctness":0.7,"declines":false} tail')
    assert v.faithfulness == 0.5 and v.correctness == 0.5 and not v.parse_error
    assert parse_verdict("not json").parse_error
    assert parse_verdict('{"claims":[],"correctness":1}').faithfulness is None


# ----------------------------------------------------------------------------- cache
def test_cache_keys_change_with_prompt_and_config_and_keep_original_stats(tmp_path):
    settings = Settings(_env_file=None)
    inner = FakeOpenAI()
    cache = DiskCache(tmp_path / "c")
    a = CachedOpenAI(inner, settings, cache, "A")
    asyncio.run(a.generate("sys", "user"))
    asyncio.run(a.generate("sys", "user"))
    assert inner.llm_calls == 1 and a.last["cached"] is True
    asyncio.run(a.generate("sys", "different user"))
    asyncio.run(CachedOpenAI(inner, settings, cache, "B").generate("sys", "user"))
    assert inner.llm_calls == 3  # new input and new config id both miss
    asyncio.run(a.embed(["x", "y"])); asyncio.run(a.embed(["x", "y"]))
    assert inner.embed_calls == 1


# ----------------------------------------------------------------------------- offline end-to-end
class JudgeFake(FakeOpenAI):
    async def judge(self, system, user):
        return LLMResult(text='{"claims":[{"text":"x","kind":"document","supported":true}],"correctness":1,"declines":false}',
                         input_tokens=5, output_tokens=5)


def _corpus(tmp_path):
    d = tmp_path / "corpus"
    d.mkdir()
    texts = {
        "policy.pdf": "The refund policy allows returns within thirty days of purchase with a valid receipt. Items must be unused.",
        "shipping.pdf": "Domestic shipping takes five business days. International shipping takes two weeks and costs extra.",
        "support.pdf": "Customer support is available by email from Monday to Friday between nine and five local time.",
    }
    for name, text in texts.items():
        (d / name).write_bytes(make_pdf([text]))
    return sorted(d.iterdir())


def test_evaluate_config_offline_end_to_end(tmp_path):
    files = _corpus(tmp_path)
    questions = [
        {"id": "q1", "question": "What is the refund policy for returns?", "type": "single", "answerable": True,
         "source_doc": "policy.pdf", "source_page": 1, "evidence": "returns within thirty days of purchase",
         "reference_answer": "Thirty days.", "split": "test"},
        {"id": "q2", "question": "How long does domestic shipping take?", "type": "single", "answerable": True,
         "source_doc": "shipping.pdf", "source_page": 1, "evidence": "Domestic shipping takes five business days",
         "reference_answer": "Five business days.", "split": "test"},
        {"id": "q3", "question": "When is customer support available by email?", "type": "reworded", "answerable": True,
         "source_doc": "support.pdf", "source_page": 1, "evidence": "available by email from Monday to Friday",
         "reference_answer": "Mon-Fri.", "split": "dev"},
        {"id": "q4", "question": "zzzz qqqq wwww unrelated", "type": "unanswerable", "answerable": False,
         "source_doc": None, "source_page": None, "evidence": None, "reference_answer": None, "split": "dev"},
        {"id": "q5", "question": "qqqq vvvv jjjj nothing matches", "type": "unanswerable", "answerable": False,
         "source_doc": None, "source_page": None, "evidence": None, "reference_answer": None, "split": "test"},
    ]
    settings = Settings(_env_file=None, index_ready_timeout_s=1, similarity_threshold=0.15)
    results = {}
    for name in ("A", "B"):
        results[name] = asyncio.run(evaluate_config(
            name, questions, files, settings, inner_openai=JudgeFake(), store=InMemoryStore(),
            cache=DiskCache(tmp_path / f"cache{name}"), work_dir=tmp_path))
        results[name]["summary_test"] = summarise(results[name], "test")
    for r in results.values():
        s = r["summary_test"]
        assert s["hit_at_k"] == 1.0 and s["mrr"] == 1.0 and s["recall_at_k"] == 1.0
        assert s["unanswerable_refused"] == 1.0 and s["over_refusal_rate"] == 0.0
        assert s["faithfulness"] == 1.0 and s["correctness"] == 1.0
        assert r["threshold"] > 0 and r["documents"] == 3
    verdict = pick_winner(results["A"], results["B"])
    assert verdict["winner"] in {"A", "B", "tie"} and verdict["n_questions"] == 2
