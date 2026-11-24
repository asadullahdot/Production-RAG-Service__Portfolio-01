"""In-memory sliding-window limiter for the client-facing 429 (PLAN.md §6, §7.3).

Single-process only: state resets on restart and is not shared between workers.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from starlette.requests import Request

from app import auth
from app.config import Settings


class RateLimiter:
    def __init__(self, limit_per_min: int, window_s: float = 60.0, clock=time.monotonic):
        self.limit = limit_per_min
        self.window = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._next_sweep = clock() + window_s

    def check(self, key: str) -> tuple[bool, int]:
        """Record a hit. Returns (allowed, retry_after_seconds)."""
        now = self._clock()
        self._sweep(now)
        q = self._hits[key]
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False, max(1, int(self.window - (now - q[0])) + 1)
        q.append(now)
        return True, 0

    def _sweep(self, now: float) -> None:
        """Once per window, drop keys whose hits have all expired, so spoofed or one-off keys cannot pile up."""
        if now < self._next_sweep:
            return
        self._next_sweep = now + self.window
        for key in [k for k, q in self._hits.items() if not q or now - q[-1] >= self.window]:
            del self._hits[key]

    def reset(self) -> None:
        self._hits.clear()


def rate_key(request: Request, settings: Settings) -> str:
    """User id when a valid token is present (no DB hit), otherwise the client IP (always IP for /auth/*)."""
    if settings.auth_enabled and not request.url.path.startswith("/auth/"):
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            user_id = auth.decode_token(header[7:], settings)
            if user_id:
                return f"user:{user_id}"
    ip = request.client.host if request.client else "unknown"
    if settings.behind_proxy:
        # Proxies append the real peer on the right; the left side is whatever the client sent (spoofable).
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(parts) >= settings.trusted_proxies:
            ip = parts[-settings.trusted_proxies]
    return f"ip:{ip}"
