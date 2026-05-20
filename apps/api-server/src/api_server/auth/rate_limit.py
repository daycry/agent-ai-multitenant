"""Sliding-window rate limiter backed by a Redis sorted set.

For each (key, window) pair, store one Z-member per hit with the
timestamp as score. To check, drop expired members and count what's
left.

Pros: precise (no bucket boundary artifacts) and cheap (one pipeline
round-trip per check). Cons: O(window_hits) memory per key.
"""

from __future__ import annotations

import time
from uuid import uuid4

from redis.asyncio import Redis


class RateLimitExceededError(Exception):
    """Raised by RateLimiter.check_or_raise when the caller is over budget."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"rate limit exceeded; retry after {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


class RateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def check(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        """Record a hit and report whether it stays under `limit`.

        Returns (allowed, count_in_window). The hit is recorded even
        when over budget — that mirrors anti-abuse semantics where
        consistent traffic should not reset the clock.
        """
        now = time.time()
        window_start = now - window_seconds
        member = str(uuid4())

        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zadd(key, {member: now})
        pipe.zcard(key)
        pipe.expire(key, window_seconds + 1)
        _, _, count, _ = await pipe.execute()

        return (count <= limit, int(count))

    async def check_or_raise(self, key: str, *, limit: int, window_seconds: int) -> None:
        allowed, _ = await self.check(key, limit=limit, window_seconds=window_seconds)
        if not allowed:
            raise RateLimitExceededError(retry_after_seconds=window_seconds)
