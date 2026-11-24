"""Retry / backoff / Retry-After / quota / deadline behaviour of the single LLM guard (PLAN.md §8.3)."""
from __future__ import annotations

import asyncio
import time

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage

from app.config import Settings
from app.errors import UpstreamUnavailable
from app.llm_guard import LLMGuard, deadline_var


def _resp(status, headers=None):
    return httpx.Response(status, headers=headers or {}, request=httpx.Request("POST", "http://x"))


def rate_limit(retry_after=None, quota=False):
    err = openai.RateLimitError("rate limited", response=_resp(429, {"retry-after": str(retry_after)} if retry_after else {}),
                                body={"code": "insufficient_quota"} if quota else None)
    if quota:
        err.code = "insufficient_quota"
    return err


def server_error():
    return openai.InternalServerError("boom", response=_resp(500), body=None)


class FakeChat:
    """Stands in for ``ChatOpenAI``: ``ainvoke`` is driven by a shared list of outcomes."""

    def __init__(self, owner):
        self.owner = owner

    async def ainvoke(self, messages):
        return await self.owner.next()


class FakeEmbeddings:
    def __init__(self, owner):
        self.owner = owner

    async def aembed_documents(self, texts):
        return await self.owner.next()


class Script:
    """Outcomes (messages or exceptions) handed out one per model call; counts the calls."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.caps: list[int] = []

    async def next(self):
        self.calls += 1
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    def chat_factory(self, model, effort, max_tokens):
        self.caps.append(max_tokens)
        return FakeChat(self)


def ok_response(text="hello", status="completed"):
    return AIMessage(content=[{"type": "text", "text": text}], response_metadata={"status": status},
                     usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5})


def make(outcomes, **kw):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    base = dict(max_retries=3, retry_backoff_s=1.0, retry_backoff_max_s=8.0, openai_api_key="k")
    base.update(kw)
    script = Script(outcomes)
    guard = LLMGuard(Settings(_env_file=None, **base), embeddings=FakeEmbeddings(script),
                     chat_factory=script.chat_factory, sleep=fake_sleep)
    return guard, script, sleeps


def run(coro):
    return asyncio.run(coro)


def test_success_returns_usage():
    client, sdk, _ = make([ok_response("hi")])
    res = run(client.generate("sys", "user"))
    assert res.text == "hi" and res.input_tokens == 3 and res.output_tokens == 2 and sdk.calls == 1


def test_retries_then_succeeds_and_honours_retry_after():
    client, sdk, sleeps = make([rate_limit(retry_after=7), server_error(), ok_response()])
    assert run(client.generate("s", "u")).text == "hello"
    assert sdk.calls == 3 and len(sleeps) == 2
    assert sleeps[0] >= 7  # Retry-After wins over the (smaller) computed backoff


def test_exhausted_retries_raise_503_after_exactly_max_retries_plus_one_calls():
    client, sdk, sleeps = make([server_error()] * 10)
    with pytest.raises(UpstreamUnavailable):
        run(client.generate("s", "u"))
    assert sdk.calls == 4 and len(sleeps) == 3  # 1 try + 3 retries


def test_timeout_is_retried():
    timeout = openai.APITimeoutError(httpx.Request("POST", "http://x"))
    client, sdk, _ = make([timeout, ok_response()])
    assert run(client.generate("s", "u")).text == "hello" and sdk.calls == 2


def test_insufficient_quota_is_not_retried():
    client, sdk, sleeps = make([rate_limit(quota=True), ok_response()])
    with pytest.raises(UpstreamUnavailable):
        run(client.generate("s", "u"))
    assert sdk.calls == 1 and sleeps == []


def test_other_4xx_is_not_retried():
    bad = openai.BadRequestError("nope", response=_resp(400), body=None)
    client, sdk, _ = make([bad, ok_response()])
    with pytest.raises(UpstreamUnavailable):
        run(client.generate("s", "u"))
    assert sdk.calls == 1


def test_incomplete_output_retried_once_then_503():
    client, sdk, _ = make([ok_response("cut", "incomplete"), ok_response("full answer")])
    assert run(client.generate("s", "u")).text == "full answer" and sdk.calls == 2
    client, sdk, _ = make([ok_response("cut", "incomplete")] * 2)
    with pytest.raises(UpstreamUnavailable):
        run(client.generate("s", "u"))


def test_empty_output_is_503():
    client, _, _ = make([ok_response("")])
    with pytest.raises(UpstreamUnavailable):
        run(client.generate("s", "u"))


def test_request_deadline_stops_retrying():
    async def go():
        deadline_var.set(time.monotonic() - 1)  # already expired
        client, sdk, _ = make([ok_response()])
        with pytest.raises(UpstreamUnavailable):
            await client.generate("s", "u")
        assert sdk.calls == 0
    run(go())


def test_embeddings_and_demo_fault():
    client, _, _ = make([[[0.1, 0.2]]])
    assert run(client.embed(["a"])) == [[0.1, 0.2]]
    client, sdk, sleeps = make([ok_response()], demo_fault="llm_429", max_retries=2)
    with pytest.raises(UpstreamUnavailable):
        run(client.generate("s", "u"))
    assert sdk.calls == 0 and len(sleeps) == 2  # fault injected before the SDK call; retried twice, then 503


def test_incomplete_retry_doubles_the_output_cap_and_no_temperature_setting():
    client, script, _ = make([ok_response("cut", "incomplete"), ok_response("full")], llm_max_output_tokens=1000)
    assert run(client.generate("s", "u")).text == "full"
    assert script.caps == [1000, 2000]
    assert not hasattr(client.s, "llm_temperature")  # gpt-6-luna / gpt-6.1-terra reject temperature
