"""Judge reliability check (PLAN.md §12): compare the LLM judge with human-style labels on a stratified sample.

Two steps:
  python scripts/judge_reliability.py --sample   # writes evals/results/judge_sample.json (what to label)
  python scripts/judge_reliability.py --score    # reads evals/judge_labels.json, writes evals/results/judge_reliability.json

The sample is drawn from the answered (non-refused) rows of the latest evaluation, stratified by answerable /
unanswerable and by the judge's own verdict, so agreement is not biased towards the judge's failures only.
A label file looks like {"labeller": "...", "labels": {"A:q01": {"correct": true, "faithful": true}, ...}} where
"correct" = the answer states what the reference answer states, and "faithful" = every claim taken from the documents
is supported by the document text. The judge's verdicts are binarised: correct = correctness 1.0, faithful = faithfulness 1.0.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals import metrics  # noqa: E402

RESULTS = ROOT / "evals" / "results"
LABELS = ROOT / "evals" / "judge_labels.json"
TARGET = 28


def latest_results() -> dict:
    path = RESULTS / "latest.json"
    if not path.exists():
        raise SystemExit("evals/results/latest.json not found: run `python -m evals.run_eval --config all` first")
    return json.loads(path.read_text(encoding="utf-8"))


def judged_rows(results: dict) -> list[dict]:
    golden = {q["id"]: q for q in json.loads((ROOT / "evals" / "golden_set.json").read_text(encoding="utf-8"))["questions"]}
    items = []
    for cfg, res in results["configs"].items():
        for r in res["rows"]:
            if r.get("refused") or "correctness" not in r or r.get("judge_parse_error"):
                continue
            g = golden[r["id"]]
            items.append({"key": f"{cfg}:{r['id']}", "config": cfg, "id": r["id"], "answerable": r["answerable"], "question": g["question"],
                          "reference_answer": g.get("reference_answer"), "answer": r["answer"], "judge_correct": r["correctness"] >= 1.0,
                          "judge_faithful": (r.get("faithfulness") is not None and r["faithfulness"] >= 1.0) if r.get("faithfulness") is not None else None,
                          "judge_correctness": r["correctness"], "judge_faithfulness": r.get("faithfulness")})
    return items


def sample(items: list[dict], n: int = TARGET, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    strata: dict[tuple, list[dict]] = {}
    for it in items:
        strata.setdefault((it["answerable"], it["judge_correct"]), []).append(it)
    for group in strata.values():
        rng.shuffle(group)
    picked: list[dict] = []
    while len(picked) < min(n, len(items)):  # round-robin over strata so every kind is represented
        progressed = False
        for key in sorted(strata, key=str):
            if strata[key] and len(picked) < n:
                picked.append(strata[key].pop())
                progressed = True
        if not progressed:
            break
    return picked


def confusion(pairs: list[tuple[bool, bool]]) -> dict:
    tp = sum(1 for h, j in pairs if h and j)
    tn = sum(1 for h, j in pairs if not h and not j)
    fp = sum(1 for h, j in pairs if not h and j)   # judge says pass, human says fail
    fn = sum(1 for h, j in pairs if h and not j)
    n = len(pairs)
    agree = tp + tn
    lo, hi = metrics.wilson_interval(agree, n) if n else (0.0, 0.0)
    return {"n": n, "both_pass": tp, "both_fail": tn, "judge_pass_human_fail": fp, "judge_fail_human_pass": fn,
            "agreement": agree / n if n else None, "agreement_ci95": [round(lo, 3), round(hi, 3)],
            "cohen_kappa": metrics.cohen_kappa([h for h, _ in pairs], [j for _, j in pairs]) if n else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--score", action="store_true")
    args = ap.parse_args()
    results = latest_results()
    items = judged_rows(results)
    if args.sample:
        chosen = sample(items)
        (RESULTS / "judge_sample.json").write_text(json.dumps({"n": len(chosen), "items": chosen}, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{len(chosen)} items sampled from {len(items)} judged answers -> evals/results/judge_sample.json")
        return 0
    if args.score:
        labels_doc = json.loads(LABELS.read_text(encoding="utf-8"))
        labels = labels_doc["labels"]
        by_key = {it["key"]: it for it in items}
        corr, faith = [], []
        for key, lab in labels.items():
            it = by_key.get(key)
            if not it:
                print(f"skipping {key}: not in the judged rows of the latest run")
                continue
            corr.append((bool(lab["correct"]), bool(it["judge_correct"])))
            if lab.get("faithful") is not None and it["judge_faithful"] is not None:
                faith.append((bool(lab["faithful"]), bool(it["judge_faithful"])))
        out = {"labeller": labels_doc.get("labeller"), "natural_sample": {"correctness": confusion(corr), "faithfulness": confusion(faith)}}
        ctl_path = RESULTS / "judge_controls.json"
        if ctl_path.exists():  # control answers with known labels (see scripts/judge_controls.py)
            ctl = json.loads(ctl_path.read_text(encoding="utf-8"))
            c_corr = [(r["human_correct"], r["judge_correctness"] >= 1.0) for r in ctl["rows"] if not r["judge_parse_error"]]
            c_faith = [(r["human_faithful"], r["judge_faithfulness"] is not None and r["judge_faithfulness"] >= 1.0) for r in ctl["rows"] if not r["judge_parse_error"]]
            out["controls"] = {"labeller": ctl["labeller"], "correctness": confusion(c_corr), "faithfulness": confusion(c_faith)}
            out["combined"] = {"correctness": confusion(corr + c_corr), "faithfulness": confusion(faith + c_faith)}
        (RESULTS / "judge_reliability.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
        print(json.dumps(out, indent=1))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
