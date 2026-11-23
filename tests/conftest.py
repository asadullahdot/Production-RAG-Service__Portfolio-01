from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.container import build_container
from app.ingestion.store import InMemoryStore
from app.main import create_app
from tests.fakes import FakeOpenAI


def make_settings(tmp_path, **overrides) -> Settings:
    base = dict(sqlite_path=str(tmp_path / "test.db"), similarity_threshold=0.15, rate_limit_per_min=1000,
                index_ready_timeout_s=1, max_retries=2, retry_backoff_s=0.0, retry_backoff_max_s=0.0,
                jwt_secret="x" * 40, openai_api_key="test", pinecone_api_key="test")
    base.update(overrides)
    return Settings(_env_file=None, **base)


class Env:
    def __init__(self, tmp_path, **overrides):
        self.settings = make_settings(tmp_path, **overrides)
        self.store = InMemoryStore()
        self.openai = FakeOpenAI()
        self.container = build_container(self.settings, store=self.store, openai_client=self.openai)
        self.app = create_app(self.settings, self.container)
        self.client = TestClient(self.app, raise_server_exceptions=False)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


@pytest.fixture
def client(env):
    return env.client
