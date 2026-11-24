"""Vector store: Qdrant (PLAN.md §3.1, §4.1).

One collection holds every owner's chunks; isolation is a payload filter on ``metadata.owner_id`` (every
search and delete includes it). The payload layout (``page_content`` + ``metadata{...}``) is the one
``langchain-qdrant`` uses, so the collection can also be read with ``QdrantVectorStore``. We write and
search with ``AsyncQdrantClient`` directly because we embed ourselves through ``llm_guard`` (retries,
semaphores, deadline) and the LangChain store would embed a second time on its own.

Qdrant point ids must be UUIDs or integers, so a chunk's readable id ``{doc_id}:{chunk_idx}`` is stored as
``metadata.chunk_id`` and the point id is ``uuid5(NAMESPACE, chunk_id)``.
"""
from __future__ import annotations

import asyncio
import logging
import random
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.config import Settings
from app.errors import UpstreamUnavailable
from app.logging_setup import log_event

logger = logging.getLogger("rag.store")

POINT_NAMESPACE = uuid.UUID("6f1c2a9e-5b0d-4c7e-9a31-2d8f4e6b7c10")  # fixed: ids must be stable across runs
OWNER_KEY = "metadata.owner_id"
DOC_KEY = "metadata.doc_id"


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(POINT_NAMESPACE, chunk_id))


@dataclass
class VectorItem:
    id: str                    # readable chunk id: "{doc_id}:{chunk_idx}"
    values: list[float]
    metadata: dict[str, Any]   # includes "text" (the chunk text)


@dataclass
class Hit:
    id: str                    # readable chunk id
    score: float               # cosine similarity
    metadata: dict[str, Any] = field(default_factory=dict)  # includes "text"


class VectorStore(Protocol):
    async def ensure_collection(self) -> None: ...
    async def upsert(self, owner: str, items: list[VectorItem]) -> None: ...
    async def query(self, owner: str, vector: list[float], top_k: int, doc_ids: list[str] | None = None) -> list[Hit]: ...
    async def delete_document(self, owner: str, doc_id: str) -> None: ...
    async def delete_owner(self, owner: str) -> None: ...
    async def count(self, owner: str) -> int: ...
    async def ping(self) -> bool: ...


def _owner_filter(owner: str, doc_ids: list[str] | None = None, doc_id: str | None = None) -> models.Filter:
    must: list[models.Condition] = [models.FieldCondition(key=OWNER_KEY, match=models.MatchValue(value=owner))]
    if doc_id:
        must.append(models.FieldCondition(key=DOC_KEY, match=models.MatchValue(value=doc_id)))
    if doc_ids:
        must.append(models.FieldCondition(key=DOC_KEY, match=models.MatchAny(any=list(doc_ids))))
    return models.Filter(must=must)


class QdrantStore:
    """Qdrant over REST (Cloud/server) or embedded (``QDRANT_PATH``: a folder or ``:memory:``)."""

    def __init__(self, settings: Settings, client: AsyncQdrantClient | None = None, collection: str | None = None,
                 sleep=asyncio.sleep):
        self.s = settings
        self._client = client
        self._sleep = sleep
        self.collection = collection or settings.qdrant_collection

    @property
    def client(self) -> AsyncQdrantClient:
        if self._client is None:
            if self.s.qdrant_path:
                self._client = AsyncQdrantClient(path=None if self.s.qdrant_path == ":memory:" else self.s.qdrant_path,
                                                 location=":memory:" if self.s.qdrant_path == ":memory:" else None)
            else:
                if not self.s.qdrant_url:
                    raise UpstreamUnavailable("Qdrant is not configured (missing QDRANT_URL).")
                self._client = AsyncQdrantClient(url=self.s.qdrant_url, api_key=self.s.qdrant_api_key or None,
                                                 timeout=int(self.s.qdrant_timeout_s), prefer_grpc=False)
        return self._client

    async def _call(self, make_call):
        """Run one Qdrant call with a timeout and bounded jittered retries; any failure becomes a 503."""
        if self.s.demo_fault == "vector_db_down":
            raise UpstreamUnavailable()
        attempt = 0
        while True:
            try:
                return await asyncio.wait_for(make_call(), timeout=self.s.qdrant_timeout_s)
            except UpstreamUnavailable:
                raise
            except Exception as exc:  # network, auth, timeout, server error
                retryable = not (isinstance(exc, UnexpectedResponse) and exc.status_code < 500)
                attempt += 1
                if not retryable or attempt > self.s.max_retries:
                    log_event(logger, "Qdrant call failed", logging.ERROR, error=type(exc).__name__, attempts=attempt)
                    raise UpstreamUnavailable() from exc
                wait = min(self.s.retry_backoff_max_s, self.s.retry_backoff_s * (2 ** (attempt - 1))) * random.uniform(0.5, 1.0)
                log_event(logger, "Qdrant call failed; retrying", logging.WARNING,
                          error=type(exc).__name__, attempt=attempt, wait_s=round(wait, 2))
                await self._sleep(wait)

    async def ensure_collection(self) -> None:
        """Idempotent: create the collection and payload indexes if missing; refuse a size mismatch."""
        name, dim = self.collection, self.s.embedding_dim
        if not await self._call(lambda: self.client.collection_exists(name)):
            await self._call(lambda: self.client.create_collection(
                name, vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE)))
            for key in (OWNER_KEY, DOC_KEY):  # no effect in embedded mode, needed for speed on a server
                await self._call(lambda key=key: self.client.create_payload_index(
                    name, field_name=key, field_schema=models.PayloadSchemaType.KEYWORD))
        info = await self._call(lambda: self.client.get_collection(name))
        size = info.config.params.vectors.size
        if size != dim:
            raise RuntimeError(f"Qdrant collection '{name}' has vector size {size}, expected {dim}")

    async def upsert(self, owner: str, items: list[VectorItem]) -> None:
        batch = self.s.qdrant_upsert_batch
        for i in range(0, len(items), batch):
            points = []
            for it in items[i:i + batch]:
                meta = {k: v for k, v in it.metadata.items() if k != "text"}
                meta["owner_id"] = owner
                meta["chunk_id"] = it.id
                points.append(models.PointStruct(id=point_id(it.id), vector=it.values,
                                                 payload={"page_content": it.metadata.get("text", ""), "metadata": meta}))
            await self._call(lambda points=points: self.client.upsert(self.collection, points=points, wait=True))

    async def query(self, owner, vector, top_k, doc_ids=None) -> list[Hit]:
        res = await self._call(lambda: self.client.query_points(
            self.collection, query=vector, limit=top_k, query_filter=_owner_filter(owner, doc_ids=doc_ids),
            with_payload=True))
        hits = []
        for p in res.points:
            payload = p.payload or {}
            meta = dict(payload.get("metadata") or {})
            meta["text"] = payload.get("page_content", "")
            hits.append(Hit(id=str(meta.get("chunk_id", p.id)), score=float(p.score), metadata=meta))
        return hits

    async def delete_document(self, owner: str, doc_id: str) -> None:
        """One filtered delete (owner AND doc); also removes the points of a half-written upload."""
        flt = _owner_filter(owner, doc_id=doc_id)
        await self._call(lambda: self.client.delete(
            self.collection, points_selector=models.FilterSelector(filter=flt), wait=True))

    async def delete_owner(self, owner: str) -> None:
        flt = _owner_filter(owner)
        await self._call(lambda: self.client.delete(
            self.collection, points_selector=models.FilterSelector(filter=flt), wait=True))

    async def count(self, owner: str) -> int:
        res = await self._call(lambda: self.client.count(
            self.collection, count_filter=_owner_filter(owner), exact=True))
        return res.count

    async def ping(self) -> bool:
        try:
            await self._call(lambda: self.client.get_collections())
            return True
        except UpstreamUnavailable:
            return False
