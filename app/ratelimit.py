"""In-memory sliding-window limiter for the client-facing 429 (PLAN.md §6, §7.3).

Single-process only: state resets on restart and is not shared between workers.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, limit_per_min: int, window_s: float = 60.0, clock=time.monotonic):
        self.limit = limit_per_min
        self.window = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> tuple[bool, int]:
        """Record a hit. Returns (allowed, retry_after_seconds)."""
        now = self._clock()
        q = self._hits[key]
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False, max(1, int(self.window - (now - q[0])) + 1)
        q.append(now)
        return True, 0

    def reset(self) -> None:
        self._hits.clear()
