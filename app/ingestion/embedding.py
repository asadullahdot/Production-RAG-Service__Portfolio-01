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
    tasks = [asyncio.ensure_future(embedder.embed(b)) for b in batches]
    try:
        results = await asyncio.gather(*tasks)
    except BaseException:
        for t in tasks:  # one batch failed: stop the others instead of letting them hold embed slots
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return [vec for batch in results for vec in batch]
