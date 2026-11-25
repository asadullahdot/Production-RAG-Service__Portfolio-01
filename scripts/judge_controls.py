"""Control answers for the judge reliability check (PLAN.md §12).

The system answers almost every golden question correctly, so a sample of its real answers contains almost no wrong
answer and says little about whether the judge can detect one. This script builds control answers whose correctness and
faithfulness are known by construction (a wrong number, a wrong entity, swapped values, an invented extra claim, half of
a two-part answer, and a few correct paraphrases), gives each the chunks the system would really have retrieved, and asks
the real judge. Output: evals/results/judge_controls.json (consumed by scripts/judge_reliability.py --score).

Run (needs keys; uses the evaluation collection and its own SQLite file):  python scripts/judge_controls.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.container import build_container  # noqa: E402
from app.db import Database  # noqa: E402
from app.ingestion.service import IngestionService  # noqa: E402
from app.ingestion.store import QdrantStore  # noqa: E402
from app.llm_guard import LLMGuard  # noqa: E402
from app.retrieval import retrieve  # noqa: E402
from evals.cache import CachedOpenAI, DiskCache  # noqa: E402
from evals.judge import judge_answer  # noqa: E402
from evals.run_eval import chunks_in_prompt  # noqa: E402

OWNER = "judge_ctl"

# (id, golden question id, kind, answer text, human_correct, human_faithful)
CONTROLS = [
    ("c01", "q01", "wrong number", "Halvora's Q3 FY2026 revenue was $54.8M, up 22% year over year. [1]", False, False),
    ("c02", "q06", "wrong number", "The annual churn rate is 1.3%. [1]", False, False),
    ("c03", "q07", "wrong entity", "EMEA contributes the largest share of revenue, with 54%. [1]", False, False),
    ("c04", "q03", "wrong entity", "Kestrel AMR was the fastest-growing product line in Q3. [1]", False, False),
    ("c05", "q16", "swapped values", "The Full Image CapsNet reached 95.57% accuracy, higher than the Cropped Image CapsNet's 93.07%. [1]", False, False),
    ("c06", "q18", "wrong number", "A patience of 50 epochs was used for early stopping. [1]", False, False),
    ("c07", "q14", "wrong number", "The deployments reached 99.99% application uptime. [1]", False, False),
    ("c08", "q08", "opposite answer", "Yes, a machine-learning model is required to remove the identifiers. [1]", False, False),
    ("c09", "q39", "invented extra claim", "Halvora is headquartered in Tallinn, Estonia, and has a second head office in Berlin. [1]", True, False),
    ("c10", "q12", "invented extra claim", "Over 6 years, including 3 years as a team lead. [1]", True, False),
    ("c11", "q22", "half of a two-part answer", "Voltaris in Chile provides all the lithium cells. [1]", False, True),
    ("c12", "q42", "half of a two-part answer", "Norlith (Norway) is being qualified as a second battery supplier. [1]", False, True),
    ("c13", "q01", "correct paraphrase", "Revenue for the quarter came to $48.6M, up 22% on the same quarter a year earlier. [1]", True, True),
    ("c14", "q07", "correct paraphrase", "North America is the largest region, accounting for 54% of revenue. [1]", True, True),
    ("c15", "q16", "correct paraphrase", "The cropped-image model scored 95.57% accuracy against 93.07% for the full-image model. [1]", True, True),
    ("c16", "q10", "correct paraphrase", "The API responds with a 422 error and stores nothing. [1]", True, True),
]


async def main() -> int:
    golden = {q["id"]: q for q in json.loads((ROOT / "evals" / "golden_set.json").read_text(encoding="utf-8"))["questions"]}
    settings = get_settings().model_copy(update={"chunk_size": 1000, "chunk_overlap": 200, "similarity_threshold": 0.15,
                                                 "sqlite_path": str(ROOT / "data" / "judge_ctl.db"),
                                                 "max_docs_per_user": 0, "max_chunks_per_user": 0, "daily_token_limit_per_user": 0})
    for suffix in ("", "-wal", "-shm"):
        Path(settings.sqlite_path + suffix).unlink(missing_ok=True)
    store = QdrantStore(settings, collection=f"{settings.qdrant_collection}_eval")
    await store.ensure_collection()
    cached = CachedOpenAI(LLMGuard(settings), settings, DiskCache(), "JC")
    container = build_container(settings, store=store, openai_client=cached, db=Database(settings.sqlite_path))
    ingestion = IngestionService(settings, container.db, store, cached, allowed_types={"pdf", "docx", "pptx", "txt", "md"},
                                 chunk_size=1000, chunk_overlap=200)
    await store.delete_owner(OWNER)
    for f in sorted((ROOT / "corpus").iterdir()):
        if f.is_file() and f.suffix.lower() in {".pdf", ".docx", ".pptx", ".txt", ".md"}:
            await ingestion.upload(OWNER, f.name, f.read_bytes(), None)

    rows = []
    for cid, qid, kind, answer, human_correct, human_faithful in CONTROLS:
        q = golden[qid]
        res = await retrieve(OWNER, q["question"], embedder=cached, store=store, db=container.db, settings=settings)
        chunks = chunks_in_prompt(q["question"], res.raw, settings)
        v = await judge_answer(cached, q["question"], [c.model_dump() for c in chunks], answer, q["reference_answer"])
        rows.append({"id": cid, "question_id": qid, "kind": kind, "answer": answer, "human_correct": human_correct,
                     "human_faithful": human_faithful, "judge_correctness": v.correctness, "judge_faithfulness": v.faithfulness,
                     "judge_parse_error": v.parse_error})
        print(f"{cid} {kind:26} human(correct={human_correct!s:5} faithful={human_faithful!s:5}) judge(correct={v.correctness} faithful={v.faithfulness})")
    await store.delete_owner(OWNER)
    out = ROOT / "evals" / "results" / "judge_controls.json"
    out.write_text(json.dumps({"labeller": "Claude (labels fixed by construction)", "judge_model": settings.judge_model, "rows": rows}, indent=1), encoding="utf-8")
    print(f"\nwrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
