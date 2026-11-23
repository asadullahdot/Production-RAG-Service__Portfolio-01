"""Shared helper for the step-by-step notebooks.

With OPENAI_API_KEY and PINECONE_API_KEY set the notebooks use the REAL services. Without keys they fall back to
the offline test doubles (hashed bag-of-words embedder, in-memory store, scripted LLM) and print a loud banner,
so the plumbing can still be demonstrated. Re-run with keys before submitting to capture real outputs.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings, get_settings  # noqa: E402
from app.container import build_container  # noqa: E402
from app.ingestion.store import InMemoryStore  # noqa: E402


def arun(coro):
    """Run a coroutine to completion from a notebook cell (Jupyter already has a running event loop)."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, coro).result()


def sample_document() -> tuple[str, bytes]:
    """First PDF/DOCX in corpus/ if present, else a small generated PDF."""
    for p in sorted((ROOT / "corpus").glob("*")):
        if p.suffix.lower() in {".pdf", ".docx"}:
            return p.name, p.read_bytes()
    from tests.make_files import make_pdf
    pages = [
        "Refund policy. Returns are accepted within thirty days of purchase with a valid receipt. Items must be unused.",
        "Shipping. Domestic orders ship in five business days. International orders take two weeks and cost extra.",
        "Support. Customer support answers email from Monday to Friday between nine and five local time.",
    ]
    return "sample_policy.pdf", make_pdf(pages)


def services():
    """Return (container, mode) where mode is 'REAL' or 'OFFLINE'."""
    settings = get_settings()
    if settings.openai_api_key and settings.pinecone_api_key:
        return build_container(settings), "REAL"
    from tests.fakes import FakeOpenAI
    offline = Settings(_env_file=None, sqlite_path=str(Path(tempfile.mkdtemp()) / "nb.db"), similarity_threshold=0.15,
                       index_ready_timeout_s=1)
    print("=" * 78)
    print("OFFLINE MODE: no API keys found, so these outputs come from test doubles, NOT OpenAI/Pinecone.")
    print("Add OPENAI_API_KEY and PINECONE_API_KEY to .env and re-run to capture real results.")
    print("=" * 78)
    return build_container(offline, store=InMemoryStore(), openai_client=FakeOpenAI()), "OFFLINE"
