"""Vector store interface with Pinecone and in-memory implementations (PLAN.md §3, §4.1).

Every method takes an ``owner`` that maps to one Pinecone namespace (``<prefix>_<owner>``).
Pinecone serverless is eventually consistent and cannot delete by metadata filter, so callers
poll :meth:`exists` after upserting and delete by computed ids.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config import Settings
from app.errors import UpstreamUnavailable
from app.logging_setup import log_event

logger = logging.getLogger("rag.store")


@dataclass
class VectorItem:
    id: str
    values: list[float]
    metadata: dict[str, Any]


@dataclass
class Hit:
    id: str
    score: float  # cosine similarity
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorStore(Protocol):
    async def ensure_index(self) -> None: ...
    async def upsert(self, owner: str, items: list[VectorItem]) -> None: ...
    async def query(self, owner: str, vector: list[float], top_k: int, doc_ids: list[str] | None = None) -> list[Hit]: ...
    async def exists(self, owner: str, vector_id: str) -> bool: ...
    async def delete_ids(self, owner: str, ids: list[str]) -> None: ...
    async def delete_namespace(self, owner: str) -> None: ...
    async def ping(self) -> bool: ...


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class InMemoryStore:
    """Exact cosine search in a dict; used by tests and for offline development."""

    def __init__(self) -> None:
        self.ns: dict[str, dict[str, VectorItem]] = {}
        self.fail = False  # tests flip this to simulate "Pinecone down"

    def _check(self) -> None:
        if self.fail:
            raise UpstreamUnavailable()

    async def ensure_index(self) -> None:
        return None

    async def upsert(self, owner: str, items: list[VectorItem]) -> None:
        self._check()
        bucket = self.ns.setdefault(owner, {})
        for it in items:
            bucket[it.id] = it

    async def query(self, owner, vector, top_k, doc_ids=None) -> list[Hit]:
        self._check()
        hits = []
        for it in self.ns.get(owner, {}).values():
            if doc_ids and it.metadata.get("doc_id") not in doc_ids:
                continue
            hits.append(Hit(it.id, _cosine(vector, it.values), dict(it.metadata)))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]

    async def exists(self, owner: str, vector_id: str) -> bool:
        self._check()
        return vector_id in self.ns.get(owner, {})

    async def delete_ids(self, owner: str, ids: list[str]) -> None:
        self._check()
        bucket = self.ns.get(owner, {})
        for i in ids:
            bucket.pop(i, None)

    async def delete_namespace(self, owner: str) -> None:
        self._check()
        self.ns.pop(owner, None)

    async def ping(self) -> bool:
        return not self.fail

    def count(self, owner: str) -> int:
        return len(self.ns.get(owner, {}))


class PineconeStore:
    """Pinecone serverless. The sync SDK runs in worker threads with a per-call timeout."""

    def __init__(self, settings: Settings):
        self.s = settings
        self._pc = None
        self._index = None

    def _namespace(self, owner: str) -> str:
        return f"{self.s.pinecone_namespace_prefix}_{owner}" if not owner.startswith("eval_") else owner

    def _client(self):
        if self._pc is None:
            if not self.s.pinecone_api_key:
                raise UpstreamUnavailable("Pinecone is not configured (missing PINECONE_API_KEY).")
            from pinecone import Pinecone
            self._pc = Pinecone(api_key=self.s.pinecone_api_key)
        return self._pc

    def _idx(self):
        if self._index is None:
            self._index = self._client().Index(self.s.pinecone_index)
        return self._index

    async def _call(self, fn, *args, **kwargs):
        if self.s.demo_fault == "pinecone_down":
            raise UpstreamUnavailable()
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn, *args, **kwargs), timeout=self.s.pinecone_timeout_s)
        except UpstreamUnavailable:
            raise
        except Exception as exc:  # network, auth, timeouts, SDK errors
            log_event(logger, "Pinecone call failed", logging.ERROR, error=type(exc).__name__)
            raise UpstreamUnavailable() from exc

    async def ensure_index(self) -> None:
        """Idempotent: create the serverless index if missing; refuse a dimension mismatch."""
        from pinecone import ServerlessSpec

        pc = self._client()
        name = self.s.pinecone_index

        def _ensure():
            if not pc.has_index(name):
                pc.create_index(name=name, dimension=self.s.embedding_dim, metric="cosine",
                                spec=ServerlessSpec(cloud=self.s.pinecone_cloud, region=self.s.pinecone_region))
            desc = pc.describe_index(name)
            if int(desc.dimension) != self.s.embedding_dim:
                raise RuntimeError(
                    f"Pinecone index '{name}' has dimension {desc.dimension}, expected {self.s.embedding_dim}")
            return desc

        await asyncio.wait_for(asyncio.to_thread(_ensure), timeout=max(self.s.pinecone_timeout_s, 120))

    async def upsert(self, owner: str, items: list[VectorItem]) -> None:
        ns = self._namespace(owner)
        batch = self.s.pinecone_upsert_batch
        for i in range(0, len(items), batch):
            chunk = [{"id": it.id, "values": it.values, "metadata": it.metadata} for it in items[i:i + batch]]
            await self._call(self._idx().upsert, vectors=chunk, namespace=ns)

    async def query(self, owner, vector, top_k, doc_ids=None) -> list[Hit]:
        kwargs: dict[str, Any] = dict(vector=vector, top_k=top_k, namespace=self._namespace(owner), include_metadata=True)
        if doc_ids:
            kwargs["filter"] = {"doc_id": {"$in": list(doc_ids)}}
        res = await self._call(self._idx().query, **kwargs)
        return [Hit(m["id"], float(m["score"]), dict(m.get("metadata") or {})) for m in _matches(res)]

    async def exists(self, owner: str, vector_id: str) -> bool:
        res = await self._call(self._idx().fetch, ids=[vector_id], namespace=self._namespace(owner))
        vectors = res["vectors"] if isinstance(res, dict) else getattr(res, "vectors", {})
        return vector_id in (vectors or {})

    async def delete_ids(self, owner: str, ids: list[str]) -> None:
        ns = self._namespace(owner)
        for i in range(0, len(ids), 1000):
            await self._call(self._idx().delete, ids=ids[i:i + 1000], namespace=ns)

    async def delete_namespace(self, owner: str) -> None:
        try:
            await self._call(self._idx().delete, delete_all=True, namespace=self._namespace(owner))
        except UpstreamUnavailable:
            # Deleting a namespace that does not exist yet is not an error.
            logger.info("namespace delete skipped or failed for %s", owner)

    async def ping(self) -> bool:
        try:
            await self._call(self._client().describe_index, self.s.pinecone_index)
            return True
        except UpstreamUnavailable:
            return False


def _matches(res) -> list[dict]:
    raw = res["matches"] if isinstance(res, dict) else getattr(res, "matches", [])
    out = []
    for m in raw or []:
        if isinstance(m, dict):
            out.append(m)
        else:
            out.append({"id": m.id, "score": m.score, "metadata": getattr(m, "metadata", None)})
    return out
