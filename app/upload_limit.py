"""Stop an oversized or unauthenticated request while it is still arriving (PLAN.md §8.1 step 1).

FastAPI parses a request body before the route (and its dependencies) run, so a check inside the route would come
after the whole body had been received and spooled. This pure-ASGI middleware wraps ``receive`` for every POST:

* ``POST /documents`` may carry up to ``MAX_UPLOAD_MB`` (plus multipart overhead); every other POST (``/ask``,
  ``/auth/*``) is JSON and may carry at most ``JSON_LIMIT_BYTES``;
* with auth enabled, ``POST /documents`` without a valid token is rejected with 401 before a single body byte is read.

A violation raises ``HTTPException`` from ``receive``: FastAPI re-raises HTTP exceptions from body parsing (any other
exception would become a 400), so the normal error handler produces the 413 / 401 JSON.
"""
from __future__ import annotations

from fastapi import HTTPException

from app import auth
from app.config import Settings

OVERHEAD_BYTES = 1024 * 1024   # multipart boundaries and headers on top of the file itself
JSON_LIMIT_BYTES = 64 * 1024   # a question, a login form or a list of document ids is tiny


class UploadSizeLimit:
    def __init__(self, app, settings: Settings):
        self.app = app
        self.s = settings
        self.upload_limit = settings.max_upload_bytes + OVERHEAD_BYTES
        self.upload_message = f"File exceeds the {settings.max_upload_mb} MB limit."

    def _unauthenticated(self, headers: dict) -> bool:
        token = headers.get(b"authorization", b"").decode("latin-1")
        return not (token.lower().startswith("bearer ") and auth.decode_token(token[7:], self.s))

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)

        headers = dict(scope["headers"])
        is_upload = scope["path"] == "/documents"
        limit = self.upload_limit if is_upload else JSON_LIMIT_BYTES
        message = self.upload_message if is_upload else "The request body is too large."
        declared = headers.get(b"content-length", b"")
        too_big_declared = declared.isdigit() and int(declared) > limit
        reject_unauthenticated = is_upload and self.s.auth_enabled and self._unauthenticated(headers)
        seen = 0

        async def limited_receive():
            nonlocal seen
            if reject_unauthenticated:
                raise HTTPException(401, "Authentication required.")
            if too_big_declared:
                raise HTTPException(413, message)
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:
                    raise HTTPException(413, message)
            return msg

        await self.app(scope, limited_receive, send)
