"""Admin-panel hardening for the System-Admin surface (Plan 15 task_15_18).

The ``/admin/*`` endpoints are the highest-value target on the platform: a
System Admin acts cross-tenant on the BYPASSRLS engine. In production we
harden that surface beyond a normal authenticated session with three
independent controls, ALL of which are enforced ONLY when ``environment`` is
``staging`` / ``prod`` so local development stays usable:

  1. **Mandatory MFA** — an admin without an enrolled+confirmed second factor
     (TOTP or WebAuthn, Plan 08) is locked out (403, a forced-enrollment
     gate). The check reuses :func:`api_server.auth.mfa.store.user_mfa_methods`
     (a narrow, tenant-agnostic existence probe), so a regular tenant user is
     never subject to it — only requests that already passed
     ``require_system_admin`` reach here.
  2. **IP allowlist** — admin access is restricted to a configurable CIDR
     allowlist. The CIDR matching reuses the api-token allowlist semantics
     (``strict=False`` networks, a bare host is a /32). An EMPTY allowlist
     means "no network restriction"; a non-empty list rejects every source IP
     outside it with 403.
  3. **Short sessions** — an admin request on a session older than
     ``admin_session_ttl_minutes`` (default 15) is rejected (401), forcing
     re-authentication. This is independent of the JWT/session TTL: a regular
     user's session can outlive 15 minutes, the admin surface clamps to the
     short window using the ``created_at`` the :class:`SessionStore` now
     stamps on every session.

This composes ON TOP of :func:`api_server.auth.deps.require_system_admin`
(which already 403s a non-admin) and never affects the local/SSO/MFA login of
non-admins.

WHERE IT IS WIRED (corrected by prod-09 task_prod09_01, authz-1). The original
docstring claimed that "wiring this single dependency at the ``/admin`` router
level hardens every admin route" — true of ``routers/admin.py`` and FALSE of the
platform, because there is not one ``/admin`` router but ten: ``/admin/backup``
(with a DESTRUCTIVE restore), ``/admin/llm-providers`` (LLM credentials),
``/admin/platform-settings``, ``/admin/cross-tenant-stats``,
``/admin/marketplace``, ``/admin/model-prices``, ``/admin/ollama``,
``/admin/embeddings`` and ``/admin/llm/copilot/device-flow`` were mounted
WITHOUT it. The gate is now attached AT MOUNT TIME by
:func:`api_server.main._is_admin_surface`: any router whose whole surface lives
under ``/admin`` gets this dependency by the mere fact of being mounted, so a
new admin router cannot regress by omission.
``tests/integration/test_admin_hardening_surface.py`` is the contract test that
iterates ``app.routes`` and fails if ANY ``/admin`` path lacks the gate.

The IP/MFA/short-session predicates are written as PURE functions so the
security suite can assert each invariant deterministically without a live
Redis or DB (the MFA lookup is the one I/O seam, injected via the principal
flow and mocked in tests).
"""

from __future__ import annotations

import time
from ipaddress import ip_address, ip_network

from fastapi import Depends, HTTPException, Request, status

from api_server.auth.deps import (
    AuthPrincipal,
    get_client_ip,
    get_session_store,
    require_system_admin,
)
from api_server.auth.mfa.store import user_mfa_methods
from api_server.auth.sessions import SessionStore
from api_server.config import Settings, get_settings

# Environments in which the admin hardening is enforced. ``dev`` is
# intentionally NOT here so local development is not over-enforced (no MFA, no
# allowlist, no 15-minute clock).
#
# This set used to be the fail-open half of authz-2: an UNKNOWN ``environment``
# tag (``production``, an empty var, ``prod `` with a trailing space) also fell
# outside it, so a typo silently turned the entire admin hardening OFF. It is
# safe as an allow-list now only because ``Settings`` validates ``environment``
# against a closed enum and refuses to start otherwise
# (prod-09 task_prod09_02) — the guarantee lives there, not here.
_ENFORCED_ENVIRONMENTS = frozenset({"staging", "prod"})


def admin_hardening_enforced(settings: Settings) -> bool:
    """True iff the admin hardening must be enforced for this environment.

    Only ``staging`` / ``prod`` enforce; ``dev`` stays usable. Centralised so
    every control answers the "should I enforce?" question the same way.
    """
    return settings.environment in _ENFORCED_ENVIRONMENTS


def admin_ip_allowed(client_ip: str, allowlist: list[str]) -> bool:
    """True iff ``client_ip`` falls in any CIDR of the allowlist.

    Mirrors the api-token allowlist semantics
    (:func:`api_server.auth.api_token_auth._ip_allowed`): an EMPTY allowlist
    means "any source IP" (the operator opted out), a malformed client IP is
    rejected, and each entry is parsed as a network with ``strict=False`` so a
    bare host like ``10.0.0.5`` is accepted as a /32. A malformed allowlist
    entry is skipped so it never silently widens access.
    """
    if not allowlist:
        return True
    try:
        candidate = ip_address(client_ip)
    except ValueError:
        return False
    for entry in allowlist:
        try:
            if candidate in ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def admin_session_expired(created_at: int | None, *, ttl_minutes: int, now: float) -> bool:
    """True iff a session created at ``created_at`` is older than the admin TTL.

    ``created_at`` is the epoch-seconds stamp :class:`SessionStore` records on
    every session. A session minted before the stamp existed (``None``) is
    treated as EXPIRED for the admin surface — fail closed: an admin request we
    cannot age out must re-authenticate rather than be trusted indefinitely.
    A non-positive ``ttl_minutes`` disables the clamp (returns ``False``).
    """
    if ttl_minutes <= 0:
        return False
    if created_at is None:
        return True
    return (now - float(created_at)) > (ttl_minutes * 60)


async def require_hardened_system_admin(
    request: Request,
    principal: AuthPrincipal = Depends(require_system_admin),
    sessions: SessionStore = Depends(get_session_store),
) -> AuthPrincipal:
    """Gate the admin surface on the three hardening controls (task_15_18).

    Runs AFTER :func:`require_system_admin` (a non-admin is already 403'd), and
    in ``staging`` / ``prod`` only. Raises:

      * 403 when the source IP is outside a non-empty allowlist.
      * 403 when the admin has no enrolled+confirmed MFA factor.
      * 401 when the admin's session is older than the short admin TTL.

    In ``dev`` it is a pass-through, so local admin work is unaffected.
    """
    settings = get_settings()
    if not admin_hardening_enforced(settings):
        return principal

    # (2) IP allowlist — cheapest, network-level check first.
    if not admin_ip_allowed(get_client_ip(request), list(settings.admin_ip_allowlist)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="source IP not in the admin allowlist",
        )

    # (3) Short session — reject a session older than the admin TTL.
    payload = await sessions.get(principal.session_id)
    created_at = payload.get("created_at") if payload else None
    created_at_int = int(created_at) if isinstance(created_at, int | float) else None
    if admin_session_expired(
        created_at_int,
        ttl_minutes=settings.admin_session_ttl_minutes,
        now=time.time(),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin session expired; re-authenticate",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # (1) Mandatory MFA — forced-enrollment gate. Last because it is the one
    # DB round-trip; the cheaper gates have already filtered the request.
    if settings.admin_require_mfa and not await user_mfa_methods(principal.user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin access requires an enrolled MFA factor",
        )

    return principal


__all__ = [
    "admin_hardening_enforced",
    "admin_ip_allowed",
    "admin_session_expired",
    "require_hardened_system_admin",
]
