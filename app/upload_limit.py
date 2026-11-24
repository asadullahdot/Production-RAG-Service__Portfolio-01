"""Stop an oversized upload while it is still arriving (PLAN.md §8.1 step 1).

FastAPI parses the multipart body before the route runs, so a size check inside the route would come after the
whole file had been received and spooled. This pure-ASGI middleware wraps ``receive`` for ``POST /documents``:
a declared ``Content-Length`` above the limit, or a body that streams past it, raises ``HTTPException(413)``
(FastAPI re-raises HTTP exceptions from body parsing; any other exception would become a 400) after at most
``limit`` bytes have been read.
"""
from __future__ import annotations

from fastapi import HTTPException

OVERHEAD_BYTES = 1024 * 1024  # multipart boundaries and headers on top of the file itself


class UploadSizeLimit:
    def __init__(self, app, max_bytes: int, max_mb: int):
        self.app = app
        self.limit = max_bytes + OVERHEAD_BYTES
        self.message = f"File exceeds the {max_mb} MB limit."

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/documents":
            return await self.app(scope, receive, send)

        declared = dict(scope["headers"]).get(b"content-length", b"")
        too_big_declared = declared.isdigit() and int(declared) > self.limit
        seen = 0

        async def limited_receive():
            nonlocal seen
            if too_big_declared:
                raise HTTPException(413, self.message)
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.limit:
                    raise HTTPException(413, self.message)
            return message

        await self.app(scope, limited_receive, send)
