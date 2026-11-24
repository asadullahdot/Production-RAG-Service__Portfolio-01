"""Validate evals/golden_set.json against the documents in corpus/ (PLAN.md §12, evals/README_GOLDEN.md).

Checks the schema and the mix (>= 25 questions, >= 10 unanswerable, >= 5 reworded, >= 5 multi-part), and that every
evidence span matches at least one chunk of its source document in BOTH chunking configs, using the same
extraction, chunking and matching code the evaluation uses. Exit code 1 if anything fails.

Run: python scripts/check_golden.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ingestion.chunking import chunk_sections  # noqa: E402
from app.ingestion.extraction import extract  # noqa: E402
from evals import metrics  # noqa: E402
from evals.run_eval import CONFIGS  # noqa: E402

TYPES = {"single", "reworded", "multi_part", "unanswerable"}
FILE_TYPES = {".pdf": "pdf", ".docx": "docx", ".pptx": "pptx", ".txt": "txt", ".md": "md"}


def main() -> int:
    golden = json.loads((ROOT / "evals" / "golden_set.json").read_text(encoding="utf-8"))
    questions = golden["questions"] if isinstance(golden, dict) else golden
    problems: list[str] = []

    ids = [q["id"] for q in questions]
    if len(ids) != len(set(ids)):
        problems.append("duplicate question ids")
    counts = Counter(q["type"] for q in questions)
    print(f"{len(questions)} questions: {dict(counts)}")
    for need, label, got in ((25, "questions", len(questions)), (10, "unanswerable", counts["unanswerable"]),
                             (5, "reworded", counts["reworded"]), (5, "multi_part", counts["multi_part"])):
        if got < need:
            problems.append(f"need at least {need} {label}, found {got}")

    docs = {}
    for p in sorted((ROOT / "corpus").iterdir()):
        if p.is_file() and p.suffix.lower() in FILE_TYPES:
            docs[p.name] = extract(p.read_bytes(), FILE_TYPES[p.suffix.lower()])
    print(f"corpus documents: {sorted(docs)}")

    for q in questions:
        where = q["id"]
        if q["type"] not in TYPES:
            problems.append(f"{where}: unknown type {q['type']!r}")
        if bool(q["answerable"]) != (q["type"] != "unanswerable"):
            problems.append(f"{where}: 'answerable' does not match type")
        if not q["answerable"]:
            if q.get("evidence") or q.get("source_doc"):
                problems.append(f"{where}: an unanswerable question must have no evidence or source_doc")
            continue
        if q["source_doc"] not in docs:
            problems.append(f"{where}: source_doc {q['source_doc']!r} is not in corpus/")
            continue
        spans = metrics.as_spans(q["evidence"])
        if q["type"] == "multi_part" and len(spans) < 2:
            problems.append(f"{where}: multi_part needs a list of at least 2 spans")
        for span in spans:
            words = len(span.split())
            if not 8 <= words <= 25:
                print(f"  note {where}: span has {words} words (README_GOLDEN suggests 10-20): {span[:50]!r}")
            for name, (size, overlap) in CONFIGS.items():
                chunks = chunk_sections(docs[q["source_doc"]].sections, size, overlap)
                if not any(metrics.span_matches(c.text, span) for c in chunks):
                    problems.append(f"{where}: span does not match any chunk in config {name} ({size}/{overlap}): {span[:70]!r}")

    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(" -", p)
        return 1
    print("\nOK: schema, mix, and every evidence span matches a chunk in both configs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
