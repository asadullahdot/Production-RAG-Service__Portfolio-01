"""B7 demo: several uploads and questions arriving at the same time, against a running server.

1. The SAME file is sent N times at once: exactly one copy is stored (one 201, the rest 409), never two.
2. N DIFFERENT small documents are sent at once: every one is stored.
3. N questions are sent at once: every one is answered with its own request id.
It uses a demo account when the server requires login, and deletes the documents it created.

Run (server already started):  python scripts/concurrent_upload_demo.py [--base-url http://127.0.0.1:8000] [--n 8]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.make_edge_case_fixtures import SMALL  # noqa: E402

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def main(args) -> int:
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    async with httpx.AsyncClient(base_url=args.base_url, timeout=240) as c:
        health = (await c.get("/health")).json()
        if health.get("auth_enabled") and not args.token:
            await c.post("/auth/signup", json={"email": args.email, "password": args.password})
            token = (await c.post("/auth/login", data={"username": args.email, "password": args.password})).json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
        c.headers.update(headers)
        before = {d["doc_id"] for d in (await c.get("/documents")).json()["documents"]}
        created: list[str] = []

        data = SMALL["table_prices.docx"]()
        rs = await asyncio.gather(*[c.post("/documents", files={"file": ("same_file.docx", data, DOCX)}) for _ in range(args.n)])
        codes = Counter(r.status_code for r in rs)
        created += [r.json()["doc_id"] for r in rs if r.status_code == 201]
        print(f"1) the same file sent {args.n} times at once  -> {dict(codes)}   (expected one 201 and {args.n - 1} x 409)")

        for doc_id in created:
            await c.delete(f"/documents/{doc_id}")
        created.clear()
        files = [(f"memo_{i}.txt", f"Memo {i}: the meeting room on floor {i} is booked for workshop number {i} this year.".encode()) for i in range(1, args.n + 1)]
        rs = await asyncio.gather(*[c.post("/documents", files={"file": (n, b, "text/plain")}) for n, b in files])
        created += [r.json()["doc_id"] for r in rs if r.status_code == 201]
        print(f"2) {args.n} different documents at once        -> {dict(Counter(r.status_code for r in rs))}   (expected all 201)")

        rs = await asyncio.gather(*[c.post("/ask", json={"question": f"What is the meeting room on floor {i} booked for?"}) for i in range(1, args.n + 1)])
        ids = {r.headers.get("X-Request-ID") for r in rs}
        answered = sum(1 for r in rs if r.status_code == 200 and not r.json()["refused"])
        print(f"3) {args.n} questions at once                  -> {dict(Counter(r.status_code for r in rs))}, answered {answered}/{args.n}, distinct request ids {len(ids)}")

        for doc_id in created:
            await c.delete(f"/documents/{doc_id}")
        after = {d["doc_id"] for d in (await c.get("/documents")).json()["documents"]}
        print(f"cleanup: {'your own documents are untouched' if after == before else 'WARNING: the document list changed'}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--token", default="")
    ap.add_argument("--email", default="concurrency-demo@example.com")
    ap.add_argument("--password", default="concurrency-demo-pass-1")
    ap.add_argument("--n", type=int, default=8)
    raise SystemExit(asyncio.run(main(ap.parse_args())))
