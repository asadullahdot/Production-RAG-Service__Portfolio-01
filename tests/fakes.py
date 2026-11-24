"""Offline test doubles: hashed bag-of-words embedder and a scriptable OpenAI wrapper (PLAN.md §13).

The embedder hashes each word into a fixed-size vector, so texts sharing words are similar. That is enough
to test plumbing, ranking order and thresholds; it cannot judge real semantic quality (the eval does).
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import deque

from app.llm_guard import LLMResult

DIM = 256
_STOP = {"the", "a", "an", "is", "are", "of", "to", "and", "in", "on", "for", "what", "how", "does", "do", "it", "this", "that"}


def embed_text(text: str) -> list[float]:
    vec = [0.0] * DIM
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if word in _STOP:
            continue
        h = int(hashlib.md5(word.encode()).hexdigest(), 16)
        vec[h % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class FakeOpenAI:
    """Same surface as ``LLMGuard`` (embed / generate / judge)."""

    def __init__(self):
        self.script: deque[str] = deque()
        self.prompts: list[tuple[str, str]] = []
        self.embed_calls = 0
        self.llm_calls = 0
        self.fail_embed = False

    async def embed(self, texts):
        from app.errors import UpstreamUnavailable
        self.embed_calls += 1
        if self.fail_embed:
            raise UpstreamUnavailable()
        return [embed_text(t) for t in texts]

    async def generate(self, system, user, effort=None):
        self.llm_calls += 1
        self.prompts.append((system, user))
        if self.script:
            return LLMResult(text=self.script.popleft(), input_tokens=10, output_tokens=5)
        m = re.search(r"\[(\d+)\]", user)
        n = m.group(1) if m else "1"
        return LLMResult(text=f"According to the documents, this is covered. [{n}]", input_tokens=10, output_tokens=5)

    judge = generate
