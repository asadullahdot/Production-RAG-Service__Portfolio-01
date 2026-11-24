"""Monitoring numbers computed from the stored traces (B3, PLAN.md section 16). Pure functions plus one database reader.

Definitions (also shown in the dashboard):
- error rate: requests that ended in HTTP 5xx (including 503 when OpenAI or Qdrant failed) divided by all traced requests.
  Refusals are HTTP 200 and are not errors; 4xx answers and 429 rate limits are counted separately.
- latency p50 / p95: of the questions that returned HTTP 200, the whole request from start to response.
- tokens and cost per question: over all /ask requests in the window (a refusal by the pattern filter costs nothing, a
  refused question may still have cost the screen call). Cost is an ESTIMATE: tokens times the published prices in the
  settings; embedding tokens are estimated from text length (the wrapper does not report them).
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

WINDOWS = {"1h": 3600, "24h": 86400, "7d": 7 * 86400, "all": None}


def percentile(values: list[float], p: float) -> float | None:
    """Linear interpolation between the closest ranks (the usual definition); None for no data."""
    if not values:
        return None
    xs = sorted(values)
    k = (len(xs) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] if lo == hi else xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def latency_stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "p50": None, "p95": None, "mean": None, "max": None}
    r = lambda v: round(v, 1)  # noqa: E731
    return {"count": len(values), "p50": r(percentile(values, 50)), "p95": r(percentile(values, 95)),
            "mean": r(sum(values) / len(values)), "max": r(max(values))}


def _bucket_seconds(window: str, first: datetime | None, now: datetime) -> int:
    if window == "1h":
        return 300
    if window == "24h":
        return 3600
    if window == "7d":
        return 86400
    span = (now - first).total_seconds() if first else 0
    return max(86400, int(span / 100) // 3600 * 3600 + 3600)  # about 100 bars at most


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def summarize(rows: list[dict], step_rows: list[dict], window: str, now: datetime) -> dict:
    total = len(rows)
    server_errors = sum(1 for r in rows if r["status"] >= 500)
    rate_limited = sum(1 for r in rows if r["status"] == 429)
    client_errors = sum(1 for r in rows if 400 <= r["status"] < 500 and r["status"] != 429)
    asks = [r for r in rows if r["route"] == "ask"]
    ok_asks = [r for r in asks if r["status"] == 200]
    tokens_in = sum(r["input_tokens"] for r in asks)
    tokens_out = sum(r["output_tokens"] for r in asks)
    embed_est = sum(r["embed_tokens_est"] for r in asks)
    cost_asks = sum(r["cost_usd"] for r in asks)
    n_asks = len(asks)
    refused = [r for r in ok_asks if r["outcome"] == "refused"]

    steps: dict[str, list[dict]] = {}
    for route in ("ask", "upload"):
        by_name: dict[str, list[dict]] = defaultdict(list)
        for s in step_rows:
            if s["route"] == route:
                by_name[s["name"]].append(s)
        out = []
        for name, items in by_name.items():
            lat = latency_stats([i["duration_ms"] for i in items])
            out.append({"name": name, "count": lat["count"], "p50": lat["p50"], "p95": lat["p95"], "mean": lat["mean"],
                        "errors": sum(1 for i in items if i["status"] == "error"),
                        "_order": percentile([i["offset_ms"] for i in items], 50) or 0.0})
        out.sort(key=lambda x: x["_order"])
        steps[route] = [{k: v for k, v in o.items() if k != "_order"} for o in out]

    parsed = [(datetime.fromisoformat(r["ts"]), r) for r in rows]
    first = min((t for t, _ in parsed), default=None)
    bucket = _bucket_seconds(window, first, now)
    start = now - timedelta(seconds=WINDOWS[window]) if WINDOWS[window] else first
    series: list[dict] = []
    if start is not None:
        t0 = datetime.fromtimestamp(math.floor(start.timestamp() / bucket) * bucket, tz=timezone.utc)
        n_buckets = int((now - t0).total_seconds() // bucket) + 1
        counts = [[0, 0] for _ in range(n_buckets)]
        for t, r in parsed:
            i = int((t - t0).total_seconds() // bucket)
            if 0 <= i < n_buckets:
                counts[i][0] += 1
                counts[i][1] += 1 if r["status"] >= 500 else 0
        series = [{"t": _iso(t0 + timedelta(seconds=i * bucket)), "requests": c[0], "errors": c[1]} for i, c in enumerate(counts)]

    return {
        "window": window, "generated_at": _iso(now), "bucket_seconds": bucket,
        "requests": {
            "total": total, "server_errors": server_errors, "client_errors": client_errors, "rate_limited": rate_limited,
            "error_rate": round(server_errors / total, 4) if total else None,
            "by_route": dict(Counter(r["route"] for r in rows)),
        },
        "questions": {
            "count": n_asks, "answered": sum(1 for r in ok_asks if r["outcome"] == "answered"), "refused": len(refused),
            "refusal_rate": round(len(refused) / len(ok_asks), 4) if ok_asks else None,
            "refusal_reasons": dict(Counter(r["reason"] or "unknown" for r in refused)),
            "latency_ms": latency_stats([r["latency_ms"] for r in ok_asks]),
        },
        "tokens": {
            "input": tokens_in, "output": tokens_out, "embedding_estimated": embed_est,
            "per_question": round((tokens_in + tokens_out) / n_asks, 1) if n_asks else None,
        },
        "cost": {
            "total_usd": round(cost_asks, 6),
            "per_question_usd": round(cost_asks / n_asks, 6) if n_asks else None,
            "estimate": True,
        },
        "steps": steps,
        "series": series,
    }


def build_summary(db, owner_id: str, window: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    seconds = WINDOWS[window]
    since = _iso(now - timedelta(seconds=seconds)) if seconds else None
    return summarize(db.trace_requests(owner_id, since), db.trace_step_rows(owner_id, since), window, now)


def request_item(row: dict) -> dict:
    keys = ("request_id", "ts", "route", "status", "latency_ms", "outcome", "reason", "error_code", "num_retrieved",
            "input_tokens", "output_tokens", "embed_tokens_est", "cost_usd")
    return {k: row.get(k) for k in keys}
