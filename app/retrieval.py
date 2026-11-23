"""Embed query → Pinecone search (owner namespace) → registry filter → similarity threshold (PLAN.md §8.2)."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi.concurrency import run_in_threadpool

from app.config import Settings
from app.db import Database
from app.ingestion.embedding import Embedder
from app.ingestion.store import VectorStore
from app.schemas import RetrievedChunk


@dataclass
class RetrievalResult:
    raw: list[RetrievedChunk]    # raw top-k after the registry filter, before the threshold (numbered by rank)
    kept: list[RetrievedChunk]   # raw chunks with score >= threshold (what the LLM may see)


def _to_chunk(n: int, hit) -> RetrievedChunk:
    m = hit.metadata
    return RetrievedChunk(
        n=n, chunk_id=hit.id, doc_id=str(m.get("doc_id", "")), file_name=str(m.get("file_name", "")),
        page=int(m.get("page", 1)), chunk_idx=int(m.get("chunk_idx", 0)), text=str(m.get("text", "")),
        score=round(min(1.0, max(0.0, float(hit.score))), 4))  # cosine similarity, clamped — no 1 - distance


async def retrieve(owner_id: str, question: str, *, embedder: Embedder, store: VectorStore, db: Database,
                   settings: Settings, top_k: int | None = None, doc_ids: list[str] | None = None,
                   threshold: float | None = None) -> RetrievalResult:
    k = min(top_k or settings.top_k, settings.top_k_max)
    vector = (await embedder.embed([question]))[0]
    hits = await store.query(owner_id, vector, k, doc_ids)
    registered = await run_in_threadpool(db.doc_ids, owner_id)
    # The registry is the source of truth: leftover vectors from failed cleanups never surface.
    hits = [h for h in hits if h.metadata.get("doc_id") in registered]
    raw = [_to_chunk(i, h) for i, h in enumerate(hits, start=1)]
    limit = settings.similarity_threshold if threshold is None else threshold
    return RetrievalResult(raw=raw, kept=[c for c in raw if c.score >= limit])
