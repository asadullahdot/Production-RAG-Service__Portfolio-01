"""Disk cache for embeddings, generations and judge calls (PLAN.md §12 Reproducibility).

Key = hash of (kind, model id, reasoning effort, prompt version, config id, exact input). A changed
prompt, model or chunking can never reuse a stale answer. Entries keep the ORIGINAL latency and token
usage so reported latency/cost stay real on cached reruns.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from app.config import Settings
from app.llm_guard import LLMResult

PROMPT_VERSION = "v1"
CACHE_DIR = Path(__file__).parent / "results" / "cache"


def _key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


class DiskCache:
    def __init__(self, directory: Path = CACHE_DIR):
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    def get(self, key: str):
        path = self.dir / f"{key}.json"
        if path.exists():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):  # a run was killed while writing: treat it as a miss
                path.unlink(missing_ok=True)
            else:
                self.hits += 1
                return value
        self.misses += 1
        return None

    def put(self, key: str, value) -> None:
        path = self.dir / f"{key}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value), encoding="utf-8")
        os.replace(tmp, path)  # atomic: a reader never sees half a file


class CachedOpenAI:
    """Same surface as ``LLMGuard`` (embed / generate / judge) with a disk cache in front.

    ``config_id`` separates Config A from Config B. Per-call stats (latency, tokens, cached flag) are collected
    in ``self.calls`` so the harness can attribute them to questions.
    """

    def __init__(self, inner, settings: Settings, cache: DiskCache, config_id: str):
        self.inner, self.s, self.cache, self.config_id = inner, settings, cache, config_id
        self.last: dict = {}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float] | None] = []
        missing: list[int] = []
        for i, t in enumerate(texts):
            hit = self.cache.get(_key("embed", self.s.embedding_model, str(self.s.embedding_dim), t))
            out.append(hit)
            if hit is None:
                missing.append(i)
        if missing:
            fresh = await self.inner.embed([texts[i] for i in missing])
            for i, vec in zip(missing, fresh):
                out[i] = vec
                self.cache.put(_key("embed", self.s.embedding_model, str(self.s.embedding_dim), texts[i]), vec)
        return out  # type: ignore[return-value]

    async def _llm(self, kind: str, model: str, effort: str, system: str, user: str, override: str | None = None) -> LLMResult:
        key = _key(kind, model, effort, PROMPT_VERSION, self.config_id, system, user)
        hit = self.cache.get(key)
        if hit:
            self.last = {"latency_ms": hit["latency_ms"], "cached": True}
            return LLMResult(hit["text"], hit["input_tokens"], hit["output_tokens"], hit.get("status", "completed"))
        started = time.monotonic()
        res = await (self.inner.generate(system, user, override) if kind == "generate" else self.inner.judge(system, user))
        latency_ms = int((time.monotonic() - started) * 1000)
        self.cache.put(key, {"text": res.text, "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
                             "status": res.status, "latency_ms": latency_ms})
        self.last = {"latency_ms": latency_ms, "cached": False}
        return res

    async def generate(self, system: str, user: str, effort: str | None = None) -> LLMResult:
        return await self._llm("generate", self.s.llm_model, effort or self.s.llm_reasoning_effort, system, user, effort)

    async def judge(self, system: str, user: str) -> LLMResult:
        return await self._llm("judge", self.s.judge_model, self.s.judge_reasoning_effort, system, user)
