from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.container import build_container
from app.ingestion.store import QdrantStore
from app.main import create_app
from tests.fakes import DIM, FakeOpenAI


def make_settings(tmp_path, **overrides) -> Settings:
    base = dict(sqlite_path=str(tmp_path / "test.db"), similarity_threshold=0.15, rate_limit_per_min=1000,
                embedding_dim=DIM, qdrant_path=":memory:", max_retries=2, retry_backoff_s=0.0, retry_backoff_max_s=0.0,
                jwt_secret="x" * 40, openai_api_key="test")
    base.update(overrides)
    return Settings(_env_file=None, **base)


class Env:
    def __init__(self, tmp_path, **overrides):
        self.settings = make_settings(tmp_path, **overrides)
        self.store = QdrantStore(self.settings)
        asyncio.run(self.store.ensure_collection())
        self.openai = FakeOpenAI()
        self.container = build_container(self.settings, store=self.store, openai_client=self.openai)
        self.app = create_app(self.settings, self.container)
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def count(self, owner: str) -> int:
        """Number of vectors stored for ``owner`` (embedded Qdrant)."""
        return asyncio.run(self.store.count(owner))

    def vector_db_down(self, down: bool = True) -> None:
        self.settings.demo_fault = "vector_db_down" if down else "none"


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


@pytest.fixture
def client(env):
    return env.client
