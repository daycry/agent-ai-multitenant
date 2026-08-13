"""Sliding-window rate limiter backed by a Redis sorted set.

For each (key, window) pair, store one Z-member per hit with the
timestamp as score. To check, drop expired members and count what's
left.

Pros: precise (no bucket boundary artifacts) and cheap (one pipeline
round-trip per check). Cons: O(window_hits) memory per key.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from uuid import uuid4

from redis.asyncio import Redis


class RateLimitExceededError(Exception):
    """Raised by RateLimiter.check_or_raise when the caller is over budget."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"rate limit exceeded; retry after {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    """Outcome of a sliding-window check, with everything the standard
    ``X-RateLimit-*`` response headers need.

    - ``allowed``    — False once the recorded hit pushes the window over
      ``limit`` (this hit is the (limit+1)th or later).
    - ``limit``      — the budget that was applied (the token's own).
    - ``remaining``  — requests left in the window, floored at 0.
    - ``reset_at``   — epoch seconds at which the window frees up enough to
      admit another request (when the oldest in-window hit ages out).
    - ``retry_after`` — seconds the caller should wait before retrying;
      only meaningful when ``allowed`` is False.
    """

    allowed: bool
    limit: int
    remaining: int
    reset_at: int
    retry_after: int


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

    async def check_with_headers(
        self, key: str, *, limit: int, window_seconds: int
    ) -> RateLimitResult:
        """Record a hit and return a :class:`RateLimitResult` for headers.

        Same sliding-window mechanics as :meth:`check` (the hit is always
        recorded, even when over budget), but it additionally reads the
        oldest surviving member's score so the caller can emit a precise
        ``Reset`` / ``Retry-After``. One pipeline round-trip.
        """
        now = time.time()
        window_start = now - window_seconds
        member = str(uuid4())

        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zadd(key, {member: now})
        pipe.zcard(key)
        pipe.expire(key, window_seconds + 1)
        # Oldest surviving member + its score: when it ages out the window
        # frees a slot. WITHSCORES => [(member, score)].
        pipe.zrange(key, 0, 0, withscores=True)
        _, _, count, _, oldest = await pipe.execute()

        count = int(count)
        allowed = count <= limit
        remaining = max(0, limit - count)

        # The window frees a slot when the oldest in-window hit ages out.
        # Fall back to `now` if the set is somehow empty (defensive).
        oldest_score = oldest[0][1] if oldest else now
        reset_at = math.ceil(oldest_score + window_seconds)
        retry_after = max(1, math.ceil(oldest_score + window_seconds - now))

        return RateLimitResult(
            allowed=allowed,
            limit=limit,
            remaining=remaining,
            reset_at=reset_at,
            retry_after=retry_after,
        )

    async def check_or_raise(self, key: str, *, limit: int, window_seconds: int) -> None:
        allowed, _ = await self.check(key, limit=limit, window_seconds=window_seconds)
        if not allowed:
            raise RateLimitExceededError(retry_after_seconds=window_seconds)
