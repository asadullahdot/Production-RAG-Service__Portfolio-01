"""Runs evals/guardrail_cases.json through the app (input-side cases; off-topic needs real embeddings)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_guardrail_cases import check, run_mocked

CASES = json.loads((Path(__file__).parent.parent / "evals" / "guardrail_cases.json").read_text(encoding="utf-8"))["cases"]
INPUT_CASES = [c for c in CASES if not c.get("needs_documents")]


def test_case_file_has_enough_cases_per_category():
    counts: dict[str, int] = {}
    for c in CASES:
        counts[c["category"]] = counts.get(c["category"], 0) + 1
    for cat in ("injection", "jailbreak", "prompt_leak", "off_topic", "benign_near_miss"):
        assert counts[cat] >= 5, cat
    assert counts["empty"] >= 3 and counts["too_long"] >= 3


@pytest.mark.parametrize("case", INPUT_CASES, ids=[c["id"] for c in INPUT_CASES])
def test_input_guardrail_cases(client, case):
    r = client.post("/ask", json={"question": case["question"]})
    ok, why = check(case, r.status_code, r.json())
    assert ok, why


def test_mocked_output_guardrail_cases():
    assert all(run_mocked())
