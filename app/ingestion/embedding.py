"""Batched embedding over the single OpenAI wrapper (PLAN.md §8.1 step 8)."""
from __future__ import annotations

import asyncio
from typing import Protocol


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


async def embed_texts(embedder: Embedder, texts: list[str], batch_size: int) -> list[list[float]]:
    """Embed ``texts`` in batches; batches run concurrently (the wrapper's semaphore caps them)."""
    if not texts:
        return []
    batches = [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]
    results = await asyncio.gather(*(embedder.embed(b) for b in batches))
    return [vec for batch in results for vec in batch]
