"""B3 report: the monitoring numbers of a running server as text and as evals/results/b3_metrics.json.

Run:  python scripts/metrics_report.py [--base-url http://127.0.0.1:8000] [--window all]
It reads /metrics/summary with the demo account (the same one scripts/demo_traffic.py used), so the numbers are exactly
what the dashboard shows. Cost is an estimate from published prices; embedding tokens are estimated from text length.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def ms(v):
    return "-" if v is None else (f"{v / 1000:.1f} s" if v >= 1000 else f"{v:.0f} ms")


def main(args) -> int:
    with httpx.Client(base_url=args.base_url, timeout=60) as c:
        if c.get("/health").json().get("auth_enabled"):
            login = c.post("/auth/login", data={"username": args.email, "password": args.password})
            if login.status_code != 200:
                print("Could not log in:", login.json().get("error", {}).get("message", login.text[:200]))
                return 1
            c.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        s = c.get("/metrics/summary", params={"window": args.window}).json()
    q, r, t, cost = s["questions"], s["requests"], s["tokens"], s["cost"]
    lat = q["latency_ms"]
    print(f"Monitoring report ({s['window']}, generated {s['generated_at']})\n")
    print(f"Requests         {r['total']}  (by route: {r['by_route']})")
    print(f"Error rate       {r['error_rate']:.1%}  ({r['server_errors']} server errors; {r['rate_limited']} rate-limited, {r['client_errors']} bad requests)" if r["total"] else "Error rate       -")
    print(f"Questions        {q['count']}  answered {q['answered']}, refused {q['refused']} {q['refusal_reasons']}")
    print(f"Latency          p50 {ms(lat['p50'])}, p95 {ms(lat['p95'])}, mean {ms(lat['mean'])}, max {ms(lat['max'])}  (over {lat['count']} answered questions)")
    print(f"Tokens           {t['input']:,} in, {t['output']:,} out, about {t['embedding_estimated']:,} embedding (estimated); {t['per_question']} per question")
    print(f"Cost (estimate)  ${cost['total_usd']:.6f} in total, ${cost['per_question_usd']:.6f} per answered question" if cost["per_question_usd"] is not None else "Cost (estimate)  -")
    for route in ("ask", "upload"):
        if s["steps"][route]:
            print(f"\nSteps of {route} requests (p50 / p95 / count):")
            for st in s["steps"][route]:
                print(f"  {st['name']:<18} {ms(st['p50']):>8} / {ms(st['p95']):>8}   x{st['count']}" + (f"   {st['errors']} failed" if st["errors"] else ""))
    out = ROOT / "evals" / "results" / "b3_metrics.json"
    out.write_text(json.dumps({"date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "summary": s}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--email", default="monitoring-demo@example.com")
    ap.add_argument("--password", default="monitoring-demo-pass-1")
    ap.add_argument("--window", default="all", choices=["1h", "24h", "7d", "all"])
    sys.exit(main(ap.parse_args()))
