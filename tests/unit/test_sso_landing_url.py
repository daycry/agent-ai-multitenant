"""The SSO landing URL the callback redirects to (task_prod09_09, frontend-1).

Today the OIDC callback / SAML ACS answer with a raw ``LoginResponse`` JSON, so
a user who logs in through their IdP ends up staring at
``{"access_token": "...", ...}`` in the browser with no session in the panel.
The fix is a ``Set-Cookie`` + redirect — and a redirect built from a
System-Admin-writable platform setting (``app.public_base_url``) is exactly the
shape an open redirect takes, so the builder validates before it builds.

The guard is NOT vacuous: every rejected case below is a value a real operator
can type into that setting (or an attacker can plant with one admin write).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_builds_the_panel_callback_path() -> None:
    from api_server.routers.sso import sso_landing_url

    assert sso_landing_url("https://app.example.com") == "https://app.example.com/auth/callback"
    # A trailing slash in the setting must not produce a double slash.
    assert sso_landing_url("https://app.example.com/") == "https://app.example.com/auth/callback"


@pytest.mark.parametrize(
    "origin",
    [
        "",
        "   ",
        # Protocol-relative: `//evil.example/auth/callback` sends the browser to
        # evil.example, which is the classic open redirect.
        "//evil.example",
        "javascript:alert(1)",
        "data:text/html,<script>",
        "ftp://app.example.com",
        # A CR/LF smuggled into a Location header is response splitting.
        "https://app.example.com\r\nSet-Cookie: x=1",
        "https://app.example.com\nX-Evil: 1",
        # Credentials in the authority are a phishing primitive
        # (`https://app.example.com@evil.example`).
        "https://app.example.com@evil.example",
        "not-a-url",
    ],
)
def test_rejects_anything_that_is_not_a_plain_http_origin(origin: str) -> None:
    from api_server.routers.sso import InvalidLandingOriginError, sso_landing_url

    with pytest.raises(InvalidLandingOriginError):
        sso_landing_url(origin)


def test_accepts_http_for_dev() -> None:
    from api_server.routers.sso import sso_landing_url

    assert sso_landing_url("http://localhost:3000") == "http://localhost:3000/auth/callback"
