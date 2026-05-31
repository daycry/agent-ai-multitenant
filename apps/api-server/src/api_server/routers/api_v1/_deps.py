"""Auth + scope dependencies shared by the public v1 endpoints (task_13_05).

The v1 surface authenticates EXCLUSIVELY through the Fase A
``X-API-Token`` path (NOT the JWT/session auth). Two building blocks live
here:

  * :func:`require_scope` — a dependency FACTORY. ``require_scope("read")``
    gates GET endpoints; ``require_scope("write")`` gates creates. It is
    layered on top of :func:`enforce_api_token_rate_limit`, so a single
    dependency both (a) authenticates the token, (b) applies the token's
    per-token sliding-window rate limit and attaches the ``X-RateLimit-*``
    headers, and (c) asserts the token carries the required scope (403
    otherwise — the credential is valid, the capability is not).

  * :data:`V1Session` — re-export of the Fase A tenant-scoped (RLS-bound)
    session dependency, so the endpoints read one name.

A ``write``-scope token is NOT implicitly granted ``read``: the v1
contract requires the explicit scope for each verb. In practice the admin
mint endpoint defaults to ``["read"]`` and a writer is minted
``["read", "write"]``, but the gate checks the exact scope so the
behaviour is unambiguous when an operator mints a write-only token.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, status

from api_server.auth.api_token_auth import (
    ApiTokenPrincipal,
    enforce_api_token_rate_limit,
    get_api_token_session,
)
from api_server.db.models import ApiTokenScope

# Re-export under a v1-local name so the endpoint signatures read cleanly
# (``session: AsyncSession = Depends(V1Session)``).
V1Session = get_api_token_session


def require_scope(scope: ApiTokenScope) -> Callable[..., Awaitable[ApiTokenPrincipal]]:
    """Build a dependency that authenticates + rate-limits + checks ``scope``.

    The returned coroutine depends on :func:`enforce_api_token_rate_limit`
    (which itself depends on :func:`get_api_token_principal`), so by the
    time the scope check runs the token is already resolved to its tenant
    and its rate-limit budget has been counted + the ``X-RateLimit-*``
    headers attached. If the resolved token's ``scopes`` do not include
    ``scope`` the request is rejected with 403 (a valid token lacking the
    capability), distinct from the 401 a missing/invalid token gets.
    """

    async def _dep(
        principal: ApiTokenPrincipal = Depends(enforce_api_token_rate_limit),
    ) -> ApiTokenPrincipal:
        if scope.value not in principal.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API token is missing the '{scope.value}' scope",
            )
        return principal

    return _dep


__all__ = ["V1Session", "require_scope"]
