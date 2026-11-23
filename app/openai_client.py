"""The ONLY module that talks to OpenAI (PLAN.md §8.3).

Applies per-service concurrency semaphores, timeouts, bounded jittered retries that honour
``Retry-After``, never retries ``insufficient_quota``, enforces the overall request deadline,
and maps every upstream failure to ``UpstreamUnavailable`` (HTTP 503).
SDK-level retries are disabled (``max_retries=0``) so only this policy applies.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import random
import time
from dataclasses import dataclass

import openai

from app.config import Settings
from app.errors import UpstreamUnavailable
from app.logging_setup import log_event

logger = logging.getLogger("rag.openai")

# Absolute monotonic deadline for the current request (set by the API layer / services).
deadline_var: contextvars.ContextVar[float | None] = contextvars.ContextVar("deadline", default=None)


@dataclass
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    status: str = "completed"


class _Transient(Exception):
    def __init__(self, reason: str, retry_after: float | None = None):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


class OpenAIClient:
    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None, sleep=asyncio.sleep):
        self.s = settings
        self._client = client
        self._sleep = sleep
        self._sems = {
            "llm": asyncio.Semaphore(settings.llm_max_concurrency),
            "embed": asyncio.Semaphore(settings.embed_max_concurrency),
            "judge": asyncio.Semaphore(settings.judge_max_concurrency),
        }

    @property
    def client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            if not self.s.openai_api_key:
                raise UpstreamUnavailable("OpenAI is not configured (missing OPENAI_API_KEY).")
            self._client = openai.AsyncOpenAI(api_key=self.s.openai_api_key, max_retries=0)
        return self._client

    # ------------------------------------------------------------------ public API
    async def embed(self, texts: list[str]) -> list[list[float]]:
        async def call():
            resp = await self.client.embeddings.create(
                model=self.s.embedding_model, input=texts, timeout=self.s.embed_timeout_s)
            return [d.embedding for d in resp.data]
        return await self._with_policy("embed", call)

    async def generate(self, system: str, user: str) -> LLMResult:
        return await self._respond("llm", self.s.llm_model, self.s.llm_reasoning_effort, system, user,
                                   self.s.llm_max_output_tokens, self.s.llm_temperature)

    async def judge(self, system: str, user: str) -> LLMResult:
        return await self._respond("judge", self.s.judge_model, self.s.judge_reasoning_effort, system, user,
                                   self.s.llm_max_output_tokens, None)

    # ------------------------------------------------------------------ internals
    async def _respond(self, service: str, model: str, effort: str, system: str, user: str,
                       max_tokens: int, temperature: float | None) -> LLMResult:
        for attempt in range(2):  # second pass only when the first was cut off by the token cap
            async def call(cap=max_tokens):
                kwargs = dict(model=model, instructions=system, input=user, max_output_tokens=cap,
                              reasoning={"effort": effort}, timeout=self.s.llm_timeout_s)
                if temperature is not None:
                    kwargs["temperature"] = temperature
                resp = await self.client.responses.create(**kwargs)
                usage = getattr(resp, "usage", None)
                return LLMResult(
                    text=(getattr(resp, "output_text", "") or "").strip(),
                    input_tokens=getattr(usage, "input_tokens", 0) or 0,
                    output_tokens=getattr(usage, "output_tokens", 0) or 0,
                    status=getattr(resp, "status", "completed") or "completed",
                )
            result = await self._with_policy(service, call)
            if result.status == "incomplete" and attempt == 0:
                max_tokens *= 2
                log_event(logger, "output cap hit; retrying once with a larger cap", logging.WARNING, service=service)
                continue
            break
        if result.status == "incomplete" or not result.text:
            raise UpstreamUnavailable()
        return result

    def _fault(self, service: str) -> None:
        fault = self.s.demo_fault
        if service in ("llm", "judge") and fault == "llm_timeout":
            raise _Transient("demo timeout")
        if service in ("llm", "judge") and fault == "llm_429":
            raise _Transient("demo rate limit", retry_after=0.5)

    async def _with_policy(self, service: str, call):
        sem = self._sems[service]
        attempt = 0
        while True:
            deadline = deadline_var.get()
            if deadline is not None and time.monotonic() >= deadline:
                raise UpstreamUnavailable()
            try:
                async with sem:
                    self._fault(service)
                    timeout = self.s.llm_timeout_s if service != "embed" else self.s.embed_timeout_s
                    if deadline is not None:
                        timeout = max(0.1, min(timeout, deadline - time.monotonic()))
                    return await asyncio.wait_for(call(), timeout=timeout + 1)
            except _Transient as exc:
                reason, retry_after = exc.reason, exc.retry_after
            except (asyncio.TimeoutError, openai.APITimeoutError):
                reason, retry_after = "timeout", None
            except openai.APIConnectionError:
                reason, retry_after = "connection error", None
            except openai.RateLimitError as exc:
                if getattr(exc, "code", None) == "insufficient_quota" or "insufficient_quota" in str(exc):
                    log_event(logger, "OpenAI quota exhausted; not retrying", logging.ERROR, service=service)
                    raise UpstreamUnavailable() from exc
                reason, retry_after = "rate limited (429)", _retry_after(exc)
            except openai.APIStatusError as exc:
                if exc.status_code >= 500:
                    reason, retry_after = f"server error {exc.status_code}", _retry_after(exc)
                else:
                    log_event(logger, "OpenAI rejected the request", logging.ERROR,
                              service=service, status=exc.status_code)
                    raise UpstreamUnavailable() from exc

            attempt += 1
            if attempt > self.s.max_retries:
                log_event(logger, "OpenAI retries exhausted", logging.ERROR, service=service, attempts=attempt, reason=reason)
                raise UpstreamUnavailable()
            backoff = min(self.s.retry_backoff_max_s, self.s.retry_backoff_s * (2 ** (attempt - 1)))
            wait = max(retry_after or 0.0, backoff * random.uniform(0.5, 1.0))
            if deadline is not None and time.monotonic() + wait >= deadline:
                raise UpstreamUnavailable()
            log_event(logger, "OpenAI call failed; retrying", logging.WARNING,
                      service=service, attempt=attempt, reason=reason, wait_s=round(wait, 2))
            await self._sleep(wait)


def _retry_after(exc: openai.APIStatusError) -> float | None:
    try:
        value = exc.response.headers.get("retry-after")
        return float(value) if value is not None else None
    except (AttributeError, ValueError, TypeError):
        return None
