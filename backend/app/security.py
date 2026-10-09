"""API key check and a small in-memory rate limiter."""

import hmac
import threading
import time
from collections.abc import Callable

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from app.config import Settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(request: Request, api_key: str | None = Security(api_key_header)) -> None:
    settings: Settings = request.app.state.settings
    expected = settings.api_key.get_secret_value().encode()
    # compare_digest on bytes keeps the comparison constant-time.
    if api_key is None or not hmac.compare_digest(api_key.encode(), expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


class RateLimiter:
    """Token bucket: allows bursts of `rate` requests, refilled at `rate` per `per` seconds.

    Single process only (the app runs with one worker). Use as a route dependency:
    `dependencies=[Depends(RateLimiter(10, 60))]`.
    """

    def __init__(self, rate: int, per: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.rate = rate
        self.per = per
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self.rate), now))
            tokens = min(float(self.rate), tokens + (now - last) * self.rate / self.per)
            if tokens < 1:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1, now)
            return True

    def __call__(self, request: Request) -> None:
        key = request.client.host if request.client else "unknown"
        if not self.allow(key):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
            )
