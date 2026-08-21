"""Session cookie + double-submit CSRF primitives (ADR 0133, task_prod09_07).

The panel used to keep its JWT in ``localStorage`` under ``agentic.token``, so
any script running on the page could read the credential — and for a System
Admin that credential is cross-tenant (``sys`` claim + ``X-Tenant-Id``), i.e.
the most valuable secret on the platform. ADR 0133 (option A, accepted
2026-07-31) moves it to a cookie the browser will not hand to JavaScript.

Two cookies, deliberately:

``agentic_session`` (``HttpOnly``)
    Carries the JWT. Unreadable from JS, so an XSS can no longer *exfiltrate*
    the session (it can still *use* it from the page — the browser attaches the
    cookie — which is why this is a mitigation, not a cure; see the honest
    trade-off table in the ADR).

``agentic_csrf`` (readable)
    The double-submit half. Cookies travel automatically, so moving off Bearer
    CREATES a CSRF surface that did not exist before: a third-party page can
    make the browser POST to the API with the session attached. It cannot,
    however, READ our cookie (same-origin policy) nor set a custom header
    cross-origin without a CORS preflight we do not grant. So every mutation
    authenticated BY COOKIE must echo this value in ``X-CSRF-Token`` and the
    server compares the two.

Both are session cookies (no ``Expires``) with an explicit ``Max-Age`` matching
the JWT TTL, so closing the browser also ends the session — the old
``localStorage`` token survived a tab close for its full 24 h.

``Secure`` is unconditional. Browsers accept ``Secure`` cookies over
``http://localhost`` (treated as a trusted origin: Chrome ≥ 89, Firefox ≥ 75),
so dev keeps working, and no environment branch can accidentally ship a session
cookie that travels in clear text.

Requests authenticated with an ``Authorization: Bearer`` header are NOT subject
to CSRF: an attacker's page cannot add that header, which is what made the old
scheme immune. The public API, ``curl`` and the SDKs therefore keep working
unchanged.
"""

from __future__ import annotations

import hmac
import secrets
from typing import Literal

from fastapi import Response

# Cookie / header names. Underscored (not dotted like the old `agentic.token`)
# because a cookie name is a token per RFC 6265 and dots, while legal, confuse
# some proxies' cookie-scrubbing rules.
SESSION_COOKIE_NAME = "agentic_session"
CSRF_COOKIE_NAME = "agentic_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"

# HTTP methods that do not change state and therefore need no CSRF proof.
# Anything NOT in here requires it — a new verb defaults to PROTECTED.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

_COOKIE_PATH = "/"
_SAME_SITE: Literal["lax", "strict", "none"] = "lax"


def new_csrf_token() -> str:
    """A fresh, unguessable double-submit token (32 bytes of urandom)."""
    return secrets.token_urlsafe(32)


def issue_session_cookies(
    response: Response,
    *,
    token: str,
    max_age_seconds: int,
    csrf_token: str | None = None,
) -> str:
    """Attach the session + CSRF cookies to ``response``; return the CSRF token.

    Callers that need to hand the CSRF value back to the client in the body (the
    SSO redirect cannot) may pass an explicit ``csrf_token``; otherwise a fresh
    one is minted. The JWT NEVER lands in the readable cookie.
    """
    csrf = csrf_token or new_csrf_token()
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=max_age_seconds,
        path=_COOKIE_PATH,
        httponly=True,
        secure=True,
        samesite=_SAME_SITE,
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf,
        max_age=max_age_seconds,
        path=_COOKIE_PATH,
        # NOT httponly on purpose: the panel has to read it to echo it back.
        httponly=False,
        secure=True,
        samesite=_SAME_SITE,
    )
    return csrf


def clear_session_cookies(response: Response) -> None:
    """Expire both cookies (logout). ``Max-Age=0`` + empty value so a browser
    that ignores one form of deletion still drops the credential."""
    for name, http_only in ((SESSION_COOKIE_NAME, True), (CSRF_COOKIE_NAME, False)):
        response.set_cookie(
            name,
            "",
            max_age=0,
            path=_COOKIE_PATH,
            httponly=http_only,
            secure=True,
            samesite=_SAME_SITE,
        )


def csrf_token_matches(cookie_value: str | None, header_value: str | None) -> bool:
    """Constant-time double-submit comparison.

    An EMPTY value never validates: without that guard, the day a bug clears
    both halves the comparison would succeed and CSRF protection would silently
    evaporate — the classic way this scheme fails open.
    """
    if not cookie_value or not header_value:
        return False
    return hmac.compare_digest(cookie_value, header_value)


def csrf_required_for_method(method: str) -> bool:
    """True for state-changing verbs. Unknown verbs are treated as unsafe."""
    return method.upper() not in _SAFE_METHODS


__all__ = [
    "CSRF_COOKIE_NAME",
    "CSRF_HEADER_NAME",
    "SESSION_COOKIE_NAME",
    "clear_session_cookies",
    "csrf_required_for_method",
    "csrf_token_matches",
    "issue_session_cookies",
    "new_csrf_token",
]
