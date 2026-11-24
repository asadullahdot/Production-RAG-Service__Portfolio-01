"""One-command evaluation: ``python -m evals.run_eval --config all`` (PLAN.md §12).

For each chunking config (A 1000/200, B 500/100) it re-ingests the corpus under an isolated internal owner,
its own SQLite file and its own Qdrant collection (<name>_eval), then measures retrieval (hit@k, recall@k, MRR), answers
every question, runs the LLM judge, tunes the similarity threshold on the dev split and reports on the test split.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

from app.config import Settings, get_settings
from app.container import build_container
from app.db import Database
from app.ingestion.service import IngestionService
from app.ingestion.store import QdrantStore, VectorStore
from app.llm_guard import LLMGuard
from app.retrieval import retrieve
from app.schemas import AskRequest
from evals import metrics
from evals.cache import CachedOpenAI, DiskCache
from evals.judge import judge_answer

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = Path(__file__).parent / "results"
CONFIGS = {"A": (1000, 200), "B": (500, 100)}


def load_golden(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    questions = data["questions"] if isinstance(data, dict) else data
    if not any("split" in q for q in questions):
        metrics.assign_split(questions)
    return questions


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


async def evaluate_config(name: str, questions: list[dict], corpus_files: list[Path], settings: Settings, *,
                          inner_openai, store: VectorStore, cache: DiskCache, work_dir: Path,
                          use_judge: bool = True) -> dict:
    size, overlap = CONFIGS[name]
    owner = f"eval_{name}"
    db_path = work_dir / f"eval_{name}.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    cfg_settings = settings.model_copy(update={"sqlite_path": str(db_path), "chunk_size": size, "chunk_overlap": overlap})
    cached = CachedOpenAI(inner_openai, cfg_settings, cache, config_id=name)
    container = build_container(cfg_settings, store=store, openai_client=cached, db=Database(str(db_path)))
    ingestion = IngestionService(cfg_settings, container.db, store, cached,
                                 allowed_types={"pdf", "docx", "pptx", "txt", "md"}, chunk_size=size, chunk_overlap=overlap)

    await store.delete_owner(owner)  # wipe and re-ingest every run
    for f in corpus_files:
        await ingestion.upload(owner, f.name, f.read_bytes(), None)
    docs = container.db.list_documents(owner)
    num_chunks = sum(d["num_chunks"] for d in docs)

    # ---- retrieval on the raw top-k (before the threshold)
    rows: list[dict] = []
    for q in questions:
        res = await retrieve(owner, q["question"], embedder=cached, store=store, db=container.db, settings=cfg_settings,
                             threshold=0.0)
        chunks = [c.model_dump() for c in res.raw]
        spans = metrics.as_spans(q.get("evidence"))
        row = {"id": q["id"], "split": q["split"], "answerable": bool(q["answerable"]), "type": q["type"],
               "top_score": chunks[0]["score"] if chunks else 0.0, "num_chunks": len(chunks)}
        if row["answerable"]:
            rel = [metrics.chunk_relevant(c, q["source_doc"], spans) for c in chunks]
            recall, all_cov = metrics.span_coverage(chunks, q["source_doc"], spans)
            row.update(hit=metrics.hit_at_k(rel), mrr=metrics.mrr(rel), recall=recall, all_spans=all_cov)
        rows.append(row)

    # ---- threshold tuned on dev
    dev = [(r["top_score"], r["answerable"]) for r in rows if r["split"] == "dev"]
    threshold, curve = metrics.tune_threshold(dev) if dev else (settings.similarity_threshold, [])
    tuned = container.settings.model_copy(update={"similarity_threshold": threshold})
    container.ask.s = tuned

    # ---- answers + judge
    for q, row in zip(questions, rows):
        resp = await container.ask.ask(owner, AskRequest(question=q["question"]))
        row.update(refused=resp.refused, reason=resp.reason, answer=resp.answer,
                   input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
                   latency_ms=cached.last.get("latency_ms", resp.latency_ms) if resp.usage.input_tokens else 0)
        row["citations"] = [c.n for c in resp.citations]
        if use_judge and not resp.refused:
            v = await judge_answer(cached, q["question"], [c.model_dump() for c in resp.retrieved_chunks], resp.answer,
                                   q.get("reference_answer") if q["answerable"] else None)
            row.update(faithfulness=v.faithfulness, correctness=v.correctness, declines=v.declines,
                       judge_parse_error=v.parse_error, n_doc_claims=len(v.document_claims))
        elif q["answerable"]:
            row.update(correctness=0.0)  # over-refusal scores 0
    await store.delete_owner(owner)
    return {"config": name, "chunk_size": size, "chunk_overlap": overlap, "documents": len(docs), "chunks": num_chunks,
            "threshold": threshold, "dev_curve": curve, "rows": rows}


def summarise(result: dict, split: str = "test") -> dict:
    rows = [r for r in result["rows"] if r["split"] == split]
    ans = [r for r in rows if r["answerable"]]
    una = [r for r in rows if not r["answerable"]]
    answered_ans = [r for r in ans if not r.get("refused")]
    una_ok = [r for r in una if r.get("refused") or r.get("declines")]
    faith = [r["faithfulness"] for r in answered_ans if r.get("faithfulness") is not None]
    lat = [r["latency_ms"] for r in rows if r.get("latency_ms")]
    tokens = [r.get("input_tokens", 0) + r.get("output_tokens", 0) for r in rows]
    return {
        "split": split, "n_answerable": len(ans), "n_unanswerable": len(una),
        "hit_at_k": metrics.mean(r["hit"] for r in ans), "mrr": metrics.mean(r["mrr"] for r in ans),
        "recall_at_k": metrics.mean(r["recall"] for r in ans),
        "faithfulness": metrics.mean(faith),
        "correctness": metrics.mean(r.get("correctness", 0.0) for r in ans),
        "unanswerable_refused": (len(una_ok) / len(una)) if una else None,
        "over_refusal_rate": (sum(1 for r in ans if r.get("refused")) / len(ans)) if ans else None,
        "latency_ms_p50": metrics.percentile(lat, 0.5), "latency_ms_p95": metrics.percentile(lat, 0.95),
        "tokens_per_question": metrics.mean(tokens),
    }


def pick_winner(a: dict, b: dict, min_net_questions: int = 2) -> dict:
    """Fixed rule: primary = hit@k and MRR on test; a difference counts only if ≥ ``min_net_questions`` net
    questions change; tie-break = correctness, then faithfulness."""
    rows_a = {r["id"]: r for r in a["rows"] if r["split"] == "test" and r["answerable"]}
    rows_b = {r["id"]: r for r in b["rows"] if r["split"] == "test" and r["answerable"]}
    ids = sorted(rows_a.keys() & rows_b.keys())
    a_wins = sum(1 for i in ids if rows_a[i]["mrr"] > rows_b[i]["mrr"])
    b_wins = sum(1 for i in ids if rows_b[i]["mrr"] > rows_a[i]["mrr"])
    hit_net = sum(rows_a[i]["hit"] for i in ids) - sum(rows_b[i]["hit"] for i in ids)
    sa, sb = summarise(a), summarise(b)
    if abs(hit_net) >= min_net_questions:
        winner, basis = ("A" if hit_net > 0 else "B"), "hit@k (net questions)"
    elif abs(a_wins - b_wins) >= min_net_questions:
        winner, basis = ("A" if a_wins > b_wins else "B"), "MRR per-question wins"
    else:
        winner, basis = "tie", "retrieval difference below the 2-net-question rule"
        for key in ("correctness", "faithfulness"):
            if sa[key] is not None and sb[key] is not None and abs(sa[key] - sb[key]) > 1e-9:
                winner, basis = ("A" if sa[key] > sb[key] else "B"), f"tie-break on {key}"
                break
    return {"winner": winner, "basis": basis, "mrr_wins": {"A": a_wins, "B": b_wins, "ties": len(ids) - a_wins - b_wins},
            "hit_net_questions_A_minus_B": hit_net, "n_questions": len(ids)}


def print_summary(results: dict) -> None:
    def fmt(v):
        return "-" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))
    cols = ["hit_at_k", "mrr", "recall_at_k", "faithfulness", "correctness", "unanswerable_refused", "over_refusal_rate",
            "latency_ms_p50", "latency_ms_p95", "tokens_per_question"]
    print(f"\nTest-split results ({results['date']}, commit {results['git_commit']}, cached calls: {results['cache']['hits']})")
    print(f"{'config':<8}{'thr':>7}  " + "  ".join(f"{c[:14]:>14}" for c in cols))
    for name, r in results["configs"].items():
        s = r["summary_test"]
        print(f"{name:<8}{r['threshold']:>7.3f}  " + "  ".join(f"{fmt(s[c]):>14}" for c in cols))
    if "comparison" in results:
        print("Winner:", results["comparison"])


async def main_async(args) -> int:
    settings = get_settings()
    golden_path = Path(args.golden)
    if not golden_path.exists():
        print(f"Golden set not found: {golden_path}. Create it first (see evals/README_GOLDEN.md).", file=sys.stderr)
        return 2
    corpus_dir = Path(args.corpus)
    corpus_files = sorted(p for p in corpus_dir.iterdir() if p.is_file() and p.suffix.lower() in {".pdf", ".docx", ".pptx", ".txt", ".md"})
    if not corpus_files:
        print(f"No corpus documents found in {corpus_dir}.", file=sys.stderr)
        return 2
    questions = load_golden(golden_path)
    cache = DiskCache()
    inner = LLMGuard(settings)
    store = QdrantStore(settings, collection=f"{settings.qdrant_collection}_eval")  # never the live collection
    await store.ensure_collection()
    work_dir = ROOT / "data"
    work_dir.mkdir(exist_ok=True)

    names = list(CONFIGS) if args.config == "all" else [args.config.upper()]
    configs = {}
    for name in names:
        configs[name] = await evaluate_config(name, questions, corpus_files, settings, inner_openai=inner, store=store,
                                              cache=cache, work_dir=work_dir)
        configs[name]["summary_test"] = summarise(configs[name], "test")
        configs[name]["summary_dev"] = summarise(configs[name], "dev")
    results = {"date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "git_commit": git_commit(),
               "models": {"llm": settings.llm_model, "judge": settings.judge_model, "embedding": settings.embedding_model},
               "seed": 7, "n_questions": len(questions), "cache": {"hits": cache.hits, "misses": cache.misses},
               "configs": configs}
    if len(configs) == 2:
        results["comparison"] = pick_winner(configs["A"], configs["B"])
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"results_{dt.datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    (RESULTS_DIR / "latest.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print_summary(results)
    print(f"\nSaved {out.relative_to(ROOT)}")
    if args.deepeval:
        print("DeepEval cross-check is optional and not installed by default: pip install deepeval, then see PLAN.md §12.")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Evaluate chunking configs A and B on the golden set.")
    p.add_argument("--config", default="all", choices=["all", "A", "B", "a", "b"])
    p.add_argument("--golden", default=str(Path(__file__).parent / "golden_set.json"))
    p.add_argument("--corpus", default=str(ROOT / "corpus"))
    p.add_argument("--deepeval", action="store_true", help="optional DeepEval cross-check (not required)")
    sys.exit(asyncio.run(main_async(p.parse_args())))


if __name__ == "__main__":
    main()
