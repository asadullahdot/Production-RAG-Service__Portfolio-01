"""Wires settings, database, vector store, OpenAI wrapper and services together (one per app)."""
from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.db import Database
from app.generation import AskService
from app.ingestion.service import IngestionService
from app.ingestion.store import PineconeStore, VectorStore
from app.openai_client import OpenAIClient
from app.ratelimit import RateLimiter


@dataclass
class Container:
    settings: Settings
    db: Database
    store: VectorStore
    openai: OpenAIClient
    ingestion: IngestionService
    ask: AskService
    limiter: RateLimiter


def build_container(settings: Settings, *, store: VectorStore | None = None, openai_client=None,
                    db: Database | None = None) -> Container:
    """Tests inject an in-memory store and a fake OpenAI client; production uses Pinecone and OpenAI."""
    db = db or Database(settings.sqlite_path, settings.db_timeout_s)
    store = store or PineconeStore(settings)
    openai_client = openai_client or OpenAIClient(settings)
    return Container(
        settings=settings, db=db, store=store, openai=openai_client,
        ingestion=IngestionService(settings, db, store, openai_client),
        ask=AskService(settings, db, store, openai_client),
        limiter=RateLimiter(settings.rate_limit_per_min),
    )
