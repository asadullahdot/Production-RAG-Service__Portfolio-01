"""Shared helper for the step-by-step notebooks.

With OPENAI_API_KEY and QDRANT_URL (or QDRANT_PATH) set the notebooks use the REAL services. Without keys they fall back to
the offline test doubles (hashed bag-of-words embedder, embedded Qdrant store, scripted LLM) and print a loud banner,
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
from app.ingestion.store import QdrantStore  # noqa: E402


_LOOP = None


def arun(coro):
    """Run a coroutine to completion from a notebook cell (Jupyter already has a running event loop).

    All cells share ONE background event loop: the OpenAI and Qdrant HTTP clients bind to the loop they are first
    used on, so a fresh ``asyncio.run`` per cell would break them with "Event loop is closed".
    """
    import asyncio
    import threading
    global _LOOP
    if _LOOP is None:
        _LOOP = asyncio.new_event_loop()
        threading.Thread(target=_LOOP.run_forever, daemon=True).start()
    return asyncio.run_coroutine_threadsafe(coro, _LOOP).result()


def reset_owner(ct, owner: str) -> None:
    """Start a notebook from a clean slate: delete the owner's vectors AND registry rows (the two stores must agree)."""
    arun(ct.store.delete_owner(owner))
    for row in ct.db.list_documents(owner):
        ct.db.delete_document(owner, row["doc_id"])


def sample_document() -> tuple[str, bytes]:
    """A small generated policy PDF. The notebook questions are about it, and it carries no personal data."""
    from tests.make_files import make_pdf
    pages = [
        "Refund policy. Returns are accepted within thirty days of purchase with a valid receipt. Items must be unused.",
        "Shipping. Domestic orders ship in five business days. International orders take two weeks and cost extra.",
        "Support. Customer support answers email from Monday to Friday between nine and five local time.",
    ]
    return "sample_policy.pdf", make_pdf(pages)


def services():
    """Return (container, mode) where mode is 'REAL' or 'OFFLINE'."""
    settings = Settings(_env_file=ROOT / ".env")  # the kernel runs in notebooks/, so give the .env path explicitly
    # Notebooks keep their own registry file and Qdrant collection, never the live ones.
    settings = settings.model_copy(update={"sqlite_path": str(ROOT / "data" / "notebooks.db"),
                                           "qdrant_collection": settings.qdrant_collection + "_nb"})
    if settings.openai_api_key and (settings.qdrant_url or settings.qdrant_path):
        ct = build_container(settings)
        arun(ct.store.ensure_collection())
        return ct, "REAL"
    from tests.fakes import FakeOpenAI
    offline = Settings(_env_file=None, sqlite_path=str(Path(tempfile.mkdtemp()) / "nb.db"), similarity_threshold=0.15,
                       embedding_dim=256, qdrant_path=":memory:")
    print("=" * 78)
    print("OFFLINE MODE: no API keys found, so these outputs come from test doubles, NOT OpenAI/Qdrant Cloud.")
    print("Add OPENAI_API_KEY and QDRANT_URL / QDRANT_API_KEY to .env and re-run to capture real results.")
    print("=" * 78)
    store = QdrantStore(offline)
    arun(store.ensure_collection())
    return build_container(offline, store=store, openai_client=FakeOpenAI()), "OFFLINE"
