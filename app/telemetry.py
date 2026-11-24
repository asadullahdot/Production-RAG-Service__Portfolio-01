"""Request tracing and monitoring data (B3, PLAN.md section 16).

Every API request that matters (ask, upload, list, delete) is one OpenTelemetry trace: a root span for the request and a
child span for each step. A small span processor collects the spans of a trace and, when the root span ends, writes one row
to ``requests`` and its spans to ``spans`` in SQLite, with the token counts and an estimated cost. ``app/metrics.py`` and
the ``/metrics/*`` endpoints read those tables.

Only timings, token counts, status codes and error type names are recorded. Questions, answers and document text never are.
Telemetry must never break a request: every failure on this path is logged and swallowed.
"""
from __future__ import annotations

import contextlib
import json
import logging
import threading
from contextvars import ContextVar
from datetime import datetime, timezone

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.trace import Status, StatusCode

from app.config import Settings

logger = logging.getLogger("rag.telemetry")

_active: "Telemetry | None" = None
_root: ContextVar = ContextVar("rag_root_span", default=None)  # the open request span of the current request


def configure(telemetry: "Telemetry | None") -> None:
    global _active
    _active = telemetry


def route_label(method: str, path: str) -> str | None:
    """Which requests are traced. Health checks, static files, docs, login and the metrics calls themselves are not."""
    if (method, path) == ("POST", "/ask"):
        return "ask"
    if path == "/documents":
        return {"POST": "upload", "GET": "list"}.get(method)
    if method == "DELETE" and path.startswith("/documents/"):
        return "delete"
    return None


def _clean(attrs: dict) -> dict:
    return {k: v for k, v in attrs.items() if isinstance(v, (str, bool, int, float))}


@contextlib.contextmanager
def span(name: str, **attrs):
    """A step inside the current request. Does nothing outside a request (unit tests, evaluation, notebooks)."""
    tel = _active
    if tel is None or _root.get() is None:
        yield None
        return
    with tel._tracer.start_as_current_span(name, attributes=_clean(attrs), record_exception=False,
                                           set_status_on_exception=False) as sp:
        try:
            yield sp
        except BaseException as exc:
            sp.set_attribute("error.type", type(exc).__name__)  # the type name only: messages can carry user text
            sp.set_status(Status(StatusCode.ERROR))
            raise


def annotate(**attrs) -> None:
    """Add attributes to the current step."""
    sp = trace.get_current_span()
    if _active is not None and _root.get() is not None and sp.is_recording():
        for key, value in _clean(attrs).items():
            sp.set_attribute(key, value)


def add(key: str, amount: int = 1) -> None:
    """Add to a numeric attribute of the current step (retries, for example)."""
    sp = trace.get_current_span()
    if _active is not None and _root.get() is not None and sp.is_recording():
        sp.set_attribute(key, int((getattr(sp, "attributes", None) or {}).get(key, 0)) + amount)


def annotate_root(**attrs) -> None:
    """Add attributes to the request itself (outcome, refusal reason, ...)."""
    root = _root.get()
    if root is not None and root.is_recording():
        for key, value in _clean(attrs).items():
            root.set_attribute(key, value)


class _NoTrace:
    def set(self, **attrs) -> None:
        pass


NO_TRACE = contextlib.nullcontext(_NoTrace())


class _RequestTrace:
    def __init__(self, tel: "Telemetry", route: str, request_id: str, method: str):
        self._tel = tel
        self._attrs = {"route": route, "request_id": request_id, "method": method}

    def __enter__(self):
        # An empty context makes this span a root even if something leaked a current span.
        self._span = self._tel._tracer.start_span("request", context=otel_context.Context(), attributes=self._attrs)
        self._token = otel_context.attach(trace.set_span_in_context(self._span))
        self._root_token = _root.set(self._span)
        return self

    def set(self, **attrs) -> None:
        for key, value in _clean(attrs).items():
            self._span.set_attribute(key, value)

    def __exit__(self, *exc):
        _root.reset(self._root_token)
        otel_context.detach(self._token)
        self._span.end()  # the collector writes the finished trace
        return False


class _Collector(SpanProcessor):
    """Keeps the spans of each open request and hands the whole trace over when its root span ends."""

    def __init__(self, flush):
        self._flush = flush
        self._open: dict[int, list[ReadableSpan]] = {}
        self._lock = threading.Lock()

    def on_start(self, span, parent_context=None):
        if span.parent is None:
            with self._lock:
                self._open[span.context.trace_id] = []

    def on_end(self, span: ReadableSpan):
        trace_id = span.context.trace_id
        with self._lock:
            spans = self._open.get(trace_id)
            if spans is None:  # a straggler that outlived its request: drop it
                return
            spans.append(span)
            if span.parent is not None:
                return
            self._open.pop(trace_id)
        try:
            self._flush(spans)
        except Exception as exc:  # monitoring must never break the request
            logger.warning("could not store a trace: %s", type(exc).__name__)


class Telemetry:
    def __init__(self, settings: Settings, db):
        self.s = settings
        self.db = db
        provider = TracerProvider(resource=Resource.create({"service.name": "knowlex"}))  # private, not the global provider
        provider.add_span_processor(_Collector(self._store))
        self._tracer = provider.get_tracer("knowlex")
        self._writes = 0

    def request(self, route: str, request_id: str, method: str) -> _RequestTrace:
        return _RequestTrace(self, route, request_id, method)

    # ------------------------------------------------------------------ storing a finished trace
    def _store(self, spans: list[ReadableSpan]) -> None:
        spans = sorted(spans, key=lambda s: s.start_time)
        root = next(s for s in spans if s.parent is None)
        parent_of = {s.context.span_id: (s.parent.span_id if s.parent else None) for s in spans}

        def depth(span_id: int) -> int:
            d, parent = 0, parent_of.get(span_id)
            while parent is not None and parent in parent_of:
                d, parent = d + 1, parent_of[parent]
            return d

        started = root.start_time
        tokens_in = tokens_out = embed_est = 0
        rows = []
        for i, s in enumerate(spans):
            a = dict(s.attributes or {})
            tokens_in += int(a.get("tokens.input", 0))
            tokens_out += int(a.get("tokens.output", 0))
            embed_est += int(a.get("embed.tokens_est", 0))
            rows.append({
                "seq": i, "name": s.name, "depth": depth(s.context.span_id),
                "offset_ms": round((s.start_time - started) / 1e6, 1),
                "duration_ms": round((s.end_time - s.start_time) / 1e6, 1),
                "status": "error" if s.status.status_code == StatusCode.ERROR else "ok",
                "attrs": json.dumps({k: v for k, v in a.items() if k not in ("owner_id",)}, ensure_ascii=False),
            })
        ra = dict(root.attributes or {})
        status = int(ra.get("status", 0))
        cost = (tokens_in * self.s.llm_price_in_per_m + tokens_out * self.s.llm_price_out_per_m
                + embed_est * self.s.embed_price_per_m) / 1_000_000
        self.db.add_trace({
            "request_id": str(ra.get("request_id", "")),
            "ts": datetime.fromtimestamp(started / 1e9, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "owner_id": ra.get("owner_id"), "route": str(ra.get("route", "")), "method": str(ra.get("method", "")),
            "status": status, "latency_ms": round((root.end_time - started) / 1e6, 1),
            "outcome": str(ra.get("outcome") or ("ok" if status < 400 else "error")),
            "reason": ra.get("reason"), "error_code": ra.get("error_code"), "num_retrieved": ra.get("num_retrieved"),
            "input_tokens": tokens_in, "output_tokens": tokens_out, "embed_tokens_est": embed_est,
            "cost_usd": round(cost, 8),
        }, rows)
        self._writes += 1
        if self._writes % 200 == 0:
            self.db.prune_traces(self.s.trace_keep)
