"""`Origin` validation on the WebSocket handshake (ADR 0133, condición 2).

This is the test the ADR says «nadie escribe y el que convierte la migración en
una regresión si falta».

The WebSocket handshake does NOT honour CORS. While the credential travelled as
``?token=`` that did not matter: a page on ``evil.com`` cannot read our token, so
it cannot open an authenticated socket. The moment the session lives in a
cookie, the browser attaches it to a handshake initiated from ANY origin — so
without this gate, moving to cookies makes the WebSocket surface strictly WORSE
than it was. Hence: same delivery, or neither.

Two rules, and the second one is the subtle half:

  * an ``Origin`` that is present and unknown is always rejected;
  * an ABSENT ``Origin`` is fine for a ``?token=`` socket (that is what a
    non-browser client looks like, and it carries no ambient credential) but NOT
    for a cookie-authenticated one — browsers always send ``Origin`` on a
    handshake, so a cookie socket without it is not a browser doing the normal
    thing.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

ALLOWLIST = ["http://localhost:3000", "https://panel.example.com"]


def _allowed(origin: str | None, *, self_origin: str | None = None, require: bool = False) -> bool:
    from api_server.routers.ws import origin_is_allowed

    return origin_is_allowed(
        origin, allowlist=ALLOWLIST, self_origin=self_origin, require_origin=require
    )


def test_configured_origin_is_allowed() -> None:
    assert _allowed("http://localhost:3000") is True
    assert _allowed("https://panel.example.com") is True


def test_foreign_origin_is_rejected() -> None:
    """The CSWSH case: a page on another site opening our socket."""
    assert _allowed("https://evil.example") is False
    # A prefix/suffix lookalike must not sneak through a substring check.
    assert _allowed("https://panel.example.com.evil.example") is False
    assert _allowed("https://evil.example/?https://panel.example.com") is False


def test_trailing_slash_and_case_are_normalised() -> None:
    """A configured origin written with a trailing slash (a very common typo in
    an env var) must not silently lock the panel out."""
    assert _allowed("http://LOCALHOST:3000") is True
    assert _allowed("http://localhost:3000/") is True


def test_same_origin_is_allowed_without_being_configured() -> None:
    """Production is single-origin behind Caddy (panel on `/`, API on `/api/*`),
    so the panel's origin IS the api-server's own origin. Deriving it from the
    request means a correct deployment needs no extra env var — and the derived
    value cannot be forged: the browser sets both `Host` and `Origin`, and a page
    on evil.com sends `Origin: https://evil.com` with OUR host.
    """
    assert _allowed("https://app.example.com", self_origin="https://app.example.com") is True
    assert _allowed("https://evil.example", self_origin="https://app.example.com") is False


def test_absent_origin_allowed_for_token_sockets_but_not_cookie_sockets() -> None:
    # Non-browser client with an explicit `?token=`: no ambient credential, no
    # CSWSH risk. Rejecting it would break every headless consumer.
    assert _allowed(None, require=False) is True
    # Cookie-authenticated: a browser ALWAYS sends Origin on a WS handshake, so
    # its absence means we cannot prove where the request came from — and the
    # credential is ambient. Reject.
    assert _allowed(None, require=True) is False


def test_empty_allowlist_still_accepts_same_origin_only() -> None:
    """A guard that passes vacuously is not a guard: an empty allowlist must not
    mean "allow everything"."""
    from api_server.routers.ws import origin_is_allowed

    assert (
        origin_is_allowed(
            "https://evil.example", allowlist=[], self_origin="https://app.example.com"
        )
        is False
    )
    assert (
        origin_is_allowed(
            "https://app.example.com", allowlist=[], self_origin="https://app.example.com"
        )
        is True
    )


class _FakeUrl:
    def __init__(self, scheme: str) -> None:
        self.scheme = scheme


class _FakeWs:
    def __init__(self, headers: dict[str, str], scheme: str = "ws") -> None:
        self.headers = headers
        self.url = _FakeUrl(scheme)


@pytest.mark.parametrize(
    ("headers", "scheme", "expected"),
    [
        ({"host": "app.example.com"}, "wss", "https://app.example.com"),
        ({"host": "localhost:8001"}, "ws", "http://localhost:8001"),
        # Behind Caddy the upstream hop is plain http; the public scheme comes
        # from the forwarded header. Getting this wrong makes production derive
        # `http://` for an `https://` page and reject the real panel.
        (
            {"host": "app.example.com", "x-forwarded-proto": "https"},
            "ws",
            "https://app.example.com",
        ),
        (
            {"host": "app.example.com", "x-forwarded-proto": "https, http"},
            "ws",
            "https://app.example.com",
        ),
        ({}, "ws", None),
    ],
)
def test_self_origin_derivation(headers: dict[str, str], scheme: str, expected: str | None) -> None:
    from api_server.routers.ws import derive_self_origin

    assert derive_self_origin(_FakeWs(headers, scheme)) == expected
