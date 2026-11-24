"""Upload / delete / list orchestration (PLAN.md §8.1, §4.4).

Order matters: the SQLite registry row is written LAST; any failure after embedding starts
deletes the document's points by filter so no orphans survive.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
import time
import uuid

from fastapi.concurrency import run_in_threadpool

from app import guardrails
from app.config import Settings
from app.db import Database, now_iso
from app.errors import AppError, DuplicateDocument, FileTooLarge, NoText, NotFound, UpstreamUnavailable
from app.ingestion.chunking import chunk_sections
from app.ingestion.embedding import Embedder, embed_texts
from app.ingestion.extraction import extract
from app.ingestion.store import VectorItem, VectorStore
from app.logging_setup import log_event

logger = logging.getLogger("rag.ingestion")


def doc_out(row: dict) -> dict:
    return {k: row[k] for k in ("doc_id", "file_name", "file_type", "num_chunks", "num_pages", "uploaded_at", "file_hash")}


def vector_ids(doc_id: str, n: int) -> list[str]:
    return [f"{doc_id}:{i}" for i in range(n)]


class IngestionService:
    def __init__(self, settings: Settings, db: Database, store: VectorStore, embedder: Embedder,
                 allowed_types: set[str] | None = None, chunk_size: int | None = None,
                 chunk_overlap: int | None = None):
        self.s = settings
        self.db = db
        self.store = store
        self.embedder = embedder
        self.allowed_types = allowed_types or {t.strip() for t in settings.allowed_file_types.split(",") if t.strip()}
        self.chunk_size = chunk_size or settings.chunk_size
        self.chunk_overlap = chunk_overlap if chunk_overlap is not None else settings.chunk_overlap
        self._locks: dict[tuple[str, str], list] = {}  # key -> [lock, users]; removed when idle

    # ------------------------------------------------------------------ upload
    async def upload(self, owner_id: str, filename: str, data: bytes, content_type: str | None = None) -> dict:
        file_type = await run_in_threadpool(guardrails.validate_upload, filename, content_type, data, self.s,
                                            self.allowed_types)
        file_hash = await run_in_threadpool(lambda: hashlib.sha256(data).hexdigest())

        key = (owner_id, file_hash)
        entry = self._locks.setdefault(key, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:  # one in-flight ingest per (owner, hash); the unique index is the final guard
                return await self._ingest(owner_id, filename, data, file_type, file_hash)
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                self._locks.pop(key, None)

    async def _ingest(self, owner_id: str, filename: str, data: bytes, file_type: str, file_hash: str) -> dict:
        existing = await run_in_threadpool(self.db.find_by_hash, owner_id, file_hash)
        if existing:
            raise DuplicateDocument(details={"doc_id": existing["doc_id"]})

        extracted = await run_in_threadpool(extract, data, file_type, self.s.max_pages)
        if extracted.total_chars < self.s.min_extracted_chars:
            raise NoText()
        if file_type in ("pdf", "pptx") and extracted.num_pages > self.s.max_pages:  # DOCX/MD sections are not pages: the chunk cap bounds them
            raise FileTooLarge(f"The document has {extracted.num_pages} pages (max {self.s.max_pages}).")

        for section in extracted.sections:  # B6: flag instruction-like text; it stays data
            hits = guardrails.scan_document_text(section.page_content)
            if hits:
                log_event(logger, "document contains instruction-like text (kept as data)", logging.WARNING,
                          file_name=filename, page=section.metadata["page"], patterns=len(hits))

        drafts = await run_in_threadpool(chunk_sections, extracted.sections, self.chunk_size, self.chunk_overlap)
        if not drafts:
            raise NoText()
        if len(drafts) > self.s.max_chunks_per_doc:
            raise FileTooLarge(f"The document produces {len(drafts)} chunks (max {self.s.max_chunks_per_doc}).")

        doc_id = str(uuid.uuid4())
        ids = vector_ids(doc_id, len(drafts))
        started = time.monotonic()
        try:
            vectors = await embed_texts(self.embedder, [d.text for d in drafts], self.s.embed_batch_size)
            uploaded_at = now_iso()
            items = [
                VectorItem(id=ids[d.chunk_idx], values=vectors[i], metadata={
                    "doc_id": doc_id, "owner_id": owner_id, "file_name": filename, "file_type": file_type,
                    "page": d.page, "chunk_idx": d.chunk_idx, "file_hash": file_hash,
                    "uploaded_at": uploaded_at, "text": d.text})
                for i, d in enumerate(drafts)
            ]
            await self.store.upsert(owner_id, items)  # wait=True: points are searchable when this returns
            try:
                row = await run_in_threadpool(
                    lambda: self.db.add_document(
                        doc_id=doc_id, owner_id=owner_id, file_name=filename, file_type=file_type,
                        num_pages=extracted.num_pages, num_chunks=len(drafts), file_hash=file_hash))
            except sqlite3.IntegrityError as exc:  # lost a race on the unique (owner, hash) index
                other = await run_in_threadpool(self.db.find_by_hash, owner_id, file_hash)
                raise DuplicateDocument(details={"doc_id": other["doc_id"] if other else None}) from exc
        except BaseException as exc:
            await self._cleanup(owner_id, doc_id)
            if isinstance(exc, AppError):
                raise
            if isinstance(exc, (Exception,)):
                log_event(logger, "ingestion failed", logging.ERROR, error=type(exc).__name__)
                raise UpstreamUnavailable() from exc
            raise
        log_event(logger, "document ingested", doc_id=doc_id, chunks=len(drafts),
                  seconds=round(time.monotonic() - started, 2))
        return doc_out(row)

    async def _cleanup(self, owner_id: str, doc_id: str) -> None:
        try:
            await asyncio.shield(self.store.delete_document(owner_id, doc_id))
        except Exception as exc:
            log_event(logger, "cleanup of partial upload failed", logging.ERROR, error=type(exc).__name__)

    # ------------------------------------------------------------------ list / delete
    async def list(self, owner_id: str) -> list[dict]:
        rows = await run_in_threadpool(self.db.list_documents, owner_id)
        return [doc_out(r) for r in rows]

    async def delete(self, owner_id: str, doc_id: str) -> None:
        row = await run_in_threadpool(self.db.get_document, owner_id, doc_id)
        if row is None:
            raise NotFound("Document not found.")
        await self.store.delete_document(owner_id, doc_id)  # one filtered delete; 503 on failure, row kept
        await run_in_threadpool(self.db.delete_document, owner_id, doc_id)
