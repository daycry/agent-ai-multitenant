"""Unit tests for the panel session cookie + double-submit CSRF (ADR 0133).

The ADR moves the panel's session out of ``localStorage`` and into a cookie
``httpOnly + Secure + SameSite=Lax``. That closes the XSS exfiltration of a
System-Admin JWT and OPENS a CSRF surface that the Bearer scheme was immune to
by construction, so the two halves are tested together: a cookie whose flags are
wrong and a CSRF comparison that accepts a mismatch are the same bug.

These are the flag/compare primitives; the request-level enforcement lives in
``tests/integration/test_csrf_double_submit.py``.
"""

from __future__ import annotations

import pytest
from fastapi import Response

pytestmark = pytest.mark.unit


def _set_cookie_headers(response: Response) -> list[str]:
    return [value.decode() for key, value in response.raw_headers if key == b"set-cookie"]


def test_session_cookie_is_httponly_secure_and_lax() -> None:
    from api_server.auth.cookies import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME, issue_session_cookies

    response = Response()
    issue_session_cookies(response, token="jwt-value", max_age_seconds=3600)

    headers = _set_cookie_headers(response)
    session = next(h for h in headers if h.startswith(f"{SESSION_COOKIE_NAME}="))

    assert "HttpOnly" in session, session
    assert "Secure" in session, session
    assert "samesite=lax" in session.lower(), session
    assert "Path=/" in session, session
    # The token itself must be in the cookie the browser cannot read from JS.
    assert "jwt-value" in session
    # ...and NEVER in the readable CSRF cookie.
    csrf = next(h for h in headers if h.startswith(f"{CSRF_COOKIE_NAME}="))
    assert "jwt-value" not in csrf


def test_csrf_cookie_is_readable_by_javascript() -> None:
    """The double-submit half MUST be readable — the panel echoes it back in
    the ``X-CSRF-Token`` header. A ``HttpOnly`` CSRF cookie would make the whole
    scheme unimplementable on the client."""
    from api_server.auth.cookies import CSRF_COOKIE_NAME, issue_session_cookies

    response = Response()
    issue_session_cookies(response, token="jwt-value", max_age_seconds=3600)

    csrf = next(h for h in _set_cookie_headers(response) if h.startswith(f"{CSRF_COOKIE_NAME}="))
    assert "HttpOnly" not in csrf, csrf
    assert "Secure" in csrf, csrf
    assert "samesite=lax" in csrf.lower(), csrf


def test_issue_returns_the_csrf_token_it_set() -> None:
    from api_server.auth.cookies import CSRF_COOKIE_NAME, issue_session_cookies

    response = Response()
    csrf_token = issue_session_cookies(response, token="jwt-value", max_age_seconds=3600)

    assert csrf_token
    header = next(h for h in _set_cookie_headers(response) if h.startswith(f"{CSRF_COOKIE_NAME}="))
    assert f"{CSRF_COOKIE_NAME}={csrf_token}" in header


def test_csrf_tokens_are_not_predictable() -> None:
    from api_server.auth.cookies import new_csrf_token

    tokens = {new_csrf_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(t) >= 32 for t in tokens)


def test_clear_expires_both_cookies() -> None:
    from api_server.auth.cookies import (
        CSRF_COOKIE_NAME,
        SESSION_COOKIE_NAME,
        clear_session_cookies,
    )

    response = Response()
    clear_session_cookies(response)

    headers = _set_cookie_headers(response)
    names = {h.split("=", 1)[0] for h in headers}
    assert names == {SESSION_COOKIE_NAME, CSRF_COOKIE_NAME}
    for header in headers:
        assert "Max-Age=0" in header, header


@pytest.mark.parametrize(
    ("cookie", "header", "expected"),
    [
        ("abc123", "abc123", True),
        ("abc123", "abc124", False),
        ("abc123", None, False),
        (None, "abc123", False),
        (None, None, False),
        # An EMPTY pair must not validate: the day a bug clears both, an
        # `==` comparison would happily let every mutation through.
        ("", "", False),
    ],
)
def test_csrf_match(cookie: str | None, header: str | None, expected: bool) -> None:
    from api_server.auth.cookies import csrf_token_matches

    assert csrf_token_matches(cookie, header) is expected


def test_safe_methods_do_not_require_csrf() -> None:
    from api_server.auth.cookies import csrf_required_for_method

    for method in ("GET", "HEAD", "OPTIONS", "TRACE", "get"):
        assert csrf_required_for_method(method) is False
    for method in ("POST", "PUT", "PATCH", "DELETE", "post"):
        assert csrf_required_for_method(method) is True
