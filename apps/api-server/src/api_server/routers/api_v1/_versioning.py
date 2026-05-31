"""API version negotiation + per-version usage tracking (task_13_07).

Plan 13 Decisiones Clave: the API is versioned in the PATH (``/api/v1``),
which stays the single source of truth. The ``X-API-Version`` request
header is an OPTIONAL pin/observe signal layered on top of that path — it
lets a caller assert "I expect to be talking to v1" so a mismatch surfaces
as a clean 400 instead of silently succeeding against the wrong contract.

Three behaviours, wired as a single router-level dependency on the v1
surface (composed WITH, never replacing, the Fase A ``X-API-Token`` auth +
per-token rate limit):

  * **Negotiate.** Read the optional ``X-API-Version`` header. Absent ->
    default to the served version (:data:`SERVED_VERSION`). A value in the
    supported set (:data:`SUPPORTED_VERSIONS`) is accepted; anything else
    is rejected with a clean 400 that names the served path version and the
    supported set (no header value is echoed back into the body verbatim).
  * **Advertise.** Set ``X-API-Version: v1`` on the response so a caller
    always learns which contract served the request, header or not.
  * **Track.** Best-effort increment of a per-version Redis day counter
    (:func:`version_usage_key`). Observability only — a tracking-backend
    hiccup is swallowed so it can NEVER fail an otherwise-good request.

Why Redis (and no migration / table)? Per-version usage is a coarse
observability metric, not durable transactional state: an occasional lost
increment is acceptable, and the value is naturally a monotonically
incrementing per-day counter. A Redis ``INCR`` on a daily key with a TTL is
exactly that, and mirrors the existing ``apitoken:rl:`` rate-limit keys in
:mod:`api_server.auth.api_token_auth` — adding an Alembic table + RLS
policy for a metric counter would be heavier than the signal warrants.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, Response, status
from redis.asyncio import Redis

from api_server.auth.deps import get_redis

# The version this build of the path-versioned surface serves. The PATH
# (``/api/v1``) is the source of truth; this constant names it for the
# header echo + the default when the optional header is absent.
SERVED_VERSION = "v1"

# The set of versions a request may pin via ``X-API-Version``. Today only
# the served version is in flight; a future ``/api/v2`` surface would add
# its constant here (and serve from its own path). Kept as a named tunable
# so the supported set is declared once, never scattered as magic strings.
SUPPORTED_VERSIONS: frozenset[str] = frozenset({SERVED_VERSION})

# Optional request header a caller uses to pin/observe the version, and the
# response header the served version is always advertised back on.
API_VERSION_HEADER = "X-API-Version"

# Redis key namespace for the per-version daily usage counter, e.g.
# ``apiusage:v1:20260530``. Day-bucketed so the series is queryable per day
# without unbounded growth; each bucket gets a TTL so old days age out.
_USAGE_PREFIX = "apiusage:"

# How long a day's usage bucket lives before Redis evicts it (kept a little
# over a week so a daily scrape never races the expiry).
USAGE_RETENTION_SECONDS = 10 * 24 * 60 * 60


def version_usage_key(version: str, *, day: str) -> str:
    """Redis key for ``version``'s usage counter on ``day`` (``yyyymmdd``)."""
    return f"{_USAGE_PREFIX}{version}:{day}"


def _today_utc() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%d")


async def _track_version_usage(redis: Redis, version: str) -> None:
    """Best-effort: bump ``version``'s day counter, swallow any failure.

    A tracking failure (Redis down, network blip) must NEVER fail the
    request — usage is observability, not part of the contract. The TTL is
    (re)applied on every increment so the bucket always carries a bounded
    lifetime even if the very first ``INCR`` of the day raced an eviction.
    """
    key = version_usage_key(version, day=_today_utc())
    try:
        await redis.incr(key)
        await redis.expire(key, USAGE_RETENTION_SECONDS)
    except Exception:  # — tracking is best-effort by design.
        # Intentionally swallowed: a metrics hiccup cannot break the API.
        return


async def enforce_api_version(
    response: Response,
    x_api_version: str | None = Header(default=None, alias=API_VERSION_HEADER),
    redis: Redis = Depends(get_redis),
) -> str:
    """Negotiate + advertise + track the API version for a v1 request.

    Router-level dependency on the public v1 surface. Composes WITH (does
    not replace) the per-endpoint :func:`require_scope` auth — FastAPI runs
    both, this one handles version negotiation only.

    Returns the negotiated (served) version so it can be asserted in tests
    and reused by callers; the meaningful side effects are the 400 on an
    unsupported pin, the ``X-API-Version`` response header, and the
    best-effort usage increment.
    """
    requested = x_api_version if x_api_version is not None else SERVED_VERSION
    if requested not in SUPPORTED_VERSIONS:
        # Clean 400: a caller pinned a version this surface cannot serve.
        # The PATH stays the source of truth, so we name the served version
        # and the supported set rather than trying to honour the header.
        supported = ", ".join(sorted(SUPPORTED_VERSIONS))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported API version requested via the "
                f"'{API_VERSION_HEADER}' header. This endpoint serves "
                f"'{SERVED_VERSION}' (version is in the path, e.g. /api/v1). "
                f"Supported versions: {supported}."
            ),
        )
    # Always advertise the served version back, header present or not.
    response.headers[API_VERSION_HEADER] = SERVED_VERSION
    await _track_version_usage(redis, SERVED_VERSION)
    return SERVED_VERSION


__all__ = [
    "API_VERSION_HEADER",
    "SERVED_VERSION",
    "SUPPORTED_VERSIONS",
    "USAGE_RETENTION_SECONDS",
    "enforce_api_version",
    "version_usage_key",
]
