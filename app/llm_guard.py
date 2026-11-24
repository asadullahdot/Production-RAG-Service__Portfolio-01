"""The ONLY module that calls OpenAI models (PLAN.md §8.3).

The models are LangChain's ``ChatOpenAI`` (Responses API) and ``OpenAIEmbeddings``, built with
``max_retries=0`` so the SDK never retries on its own. Every call goes through :meth:`_with_policy`:
per-service concurrency semaphores, timeouts, bounded jittered retries that honour ``Retry-After``,
no retry on ``insufficient_quota``, the overall request deadline, and mapping of every upstream failure
to ``UpstreamUnavailable`` (HTTP 503). LangChain re-raises the OpenAI SDK's exceptions unchanged, so the
error handling below works on ``openai.*`` errors.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import random
import time
from dataclasses import dataclass

import openai
from langchain_core.messages import HumanMessage, SystemMessage

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


class LLMGuard:
    """``embed`` / ``generate`` / ``judge`` with the failure policy applied.

    Tests inject ``embeddings`` (a LangChain ``Embeddings``) and ``chat_factory`` (a function
    ``(model, effort, max_tokens) -> chat model``); production builds the real OpenAI models lazily.
    """

    def __init__(self, settings: Settings, *, embeddings=None, chat_factory=None, sleep=asyncio.sleep):
        self.s = settings
        self._embeddings = embeddings
        self._chat_factory = chat_factory or self._openai_chat
        self._chats: dict[tuple[str, str, int], object] = {}
        self._sleep = sleep
        self._sems = {
            "llm": asyncio.Semaphore(settings.llm_max_concurrency),
            "embed": asyncio.Semaphore(settings.embed_max_concurrency),
            "judge": asyncio.Semaphore(settings.judge_max_concurrency),
        }

    # ------------------------------------------------------------------ model construction
    def _require_key(self) -> None:
        if not self.s.openai_api_key:
            raise UpstreamUnavailable("OpenAI is not configured (missing OPENAI_API_KEY).")

    def _openai_chat(self, model: str, effort: str, max_tokens: int):
        from langchain_openai import ChatOpenAI
        self._require_key()
        # No temperature: gpt-6-luna and gpt-6.1-terra reject it (HTTP 400, verified 24 Nov 2026).
        return ChatOpenAI(model=model, api_key=self.s.openai_api_key, use_responses_api=True,
                          reasoning={"effort": effort}, max_tokens=max_tokens,
                          max_retries=0, timeout=self.s.llm_timeout_s)

    def _chat(self, model: str, effort: str, max_tokens: int):
        key = (model, effort, max_tokens)
        if key not in self._chats:
            self._chats[key] = self._chat_factory(model, effort, max_tokens)
        return self._chats[key]

    @property
    def embeddings(self):
        if self._embeddings is None:
            from langchain_openai import OpenAIEmbeddings
            self._require_key()
            self._embeddings = OpenAIEmbeddings(
                model=self.s.embedding_model, api_key=self.s.openai_api_key, dimensions=self.s.embedding_dim, max_retries=0,
                timeout=self.s.embed_timeout_s, check_embedding_ctx_length=False)
        return self._embeddings

    # ------------------------------------------------------------------ public API
    async def embed(self, texts: list[str]) -> list[list[float]]:
        async def call():
            return await self.embeddings.aembed_documents(texts)
        vectors = await self._with_policy("embed", call)
        if vectors and len(vectors[0]) != self.s.embedding_dim:  # Qdrant would reject these later with no clear cause
            log_event(logger, "embedding length differs from EMBEDDING_DIM", logging.ERROR,
                      got=len(vectors[0]), expected=self.s.embedding_dim)
            raise UpstreamUnavailable()
        return vectors

    async def generate(self, system: str, user: str, effort: str | None = None) -> LLMResult:
        return await self._respond("llm", self.s.llm_model, effort or self.s.llm_reasoning_effort, system, user,
                                   self.s.llm_max_output_tokens)

    async def judge(self, system: str, user: str) -> LLMResult:
        return await self._respond("judge", self.s.judge_model, self.s.judge_reasoning_effort, system, user,
                                   self.s.llm_max_output_tokens)

    # ------------------------------------------------------------------ internals
    async def _respond(self, service: str, model: str, effort: str, system: str, user: str,
                       max_tokens: int) -> LLMResult:
        for attempt in range(2):  # second pass only when the first was cut off by the token cap
            async def call(cap=max_tokens):
                chat = self._chat(model, effort, cap)
                msg = await chat.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
                usage = getattr(msg, "usage_metadata", None) or {}
                meta = getattr(msg, "response_metadata", None) or {}
                return LLMResult(
                    text=(msg.text or "").strip(),  # the reply content is a list of blocks; .text joins them
                    input_tokens=usage.get("input_tokens", 0) or 0,
                    output_tokens=usage.get("output_tokens", 0) or 0,
                    status=meta.get("status", "completed") or "completed",
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
        if service == "embed" and fault == "embed_timeout":
            raise _Transient("demo timeout")
        if service == "embed" and fault == "embed_429":
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
                    # +1 s lets the SDK's own timeout fire first, but never past the request deadline
                    return await asyncio.wait_for(call(), timeout=timeout if deadline is not None else timeout + 1)
            except UpstreamUnavailable:
                raise
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
                if exc.status_code >= 500 or exc.status_code in (408, 409):  # transient: worth a retry
                    reason, retry_after = f"server error {exc.status_code}", _retry_after(exc)
                else:
                    log_event(logger, "OpenAI rejected the request", logging.ERROR,
                              service=service, status=exc.status_code)
                    raise UpstreamUnavailable() from exc
            except Exception as exc:  # unexpected payload / parse / SDK error: still a 503, never a 500
                log_event(logger, "unexpected model-call failure", logging.ERROR,
                          service=service, error=type(exc).__name__)
                raise UpstreamUnavailable() from exc

            attempt += 1
            if attempt > self.s.max_retries:
                log_event(logger, "OpenAI retries exhausted", logging.ERROR, service=service, attempts=attempt, reason=reason)
                raise UpstreamUnavailable()
            backoff = min(self.s.retry_backoff_max_s, self.s.retry_backoff_s * (2 ** (attempt - 1)))
            wait = max(min(retry_after or 0.0, self.s.retry_backoff_max_s), backoff * random.uniform(0.5, 1.0))
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
