import threading
import time
from collections import deque
from collections.abc import Callable

MAX_TRACKED_KEYS = 10_000


class SlidingWindowRateLimiter:
    """In-process sliding-window limiter.

    Good enough for a single API instance. With several replicas each one keeps its
    own counters, so a shared store (e.g. Redis) would be needed.
    """

    def __init__(self, limit: int, window_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def hit(self, key: str) -> bool:
        """Record a request for ``key``; return False when it exceeds the limit."""
        if not self.enabled:
            return True
        now = self._clock()
        with self._lock:
            if len(self._hits) >= MAX_TRACKED_KEYS:
                self._sweep(now)
            hits = self._hits.setdefault(key, deque())
            self._expire(hits, now)
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    def _expire(self, hits: deque[float], now: float) -> None:
        while hits and now - hits[0] >= self._window:
            hits.popleft()

    def _sweep(self, now: float) -> None:
        for key in list(self._hits):
            self._expire(self._hits[key], now)
            if not self._hits[key]:
                del self._hits[key]
