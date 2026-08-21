"""SSO end-to-end: from the IdP to the PANEL with a session (task_prod09_09).

frontend-1, verbatim from the audit: the OIDC callback answered a raw
``LoginResponse`` JSON, so a user who logged in through their IdP ended up
looking at ``{"access_token": "...", "token_type": "bearer", ...}`` in the
browser — no session in the panel, no way forward except copy-pasting a JWT.
The SSO flow was complete on the server and had no last mile.

With the session in a cookie (ADR 0133) the last mile is a redirect: the
callback sets the cookie and bounces to the panel, which resolves the tenant.

The fake IdP harness is REUSED from :mod:`tests.integration.test_sso_global_login`
rather than copied — a second copy of a 300-line mock OpenID Provider is a second
thing to drift.

Pre-condition: postgres (15432) + redis from docker-compose are healthy.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.test_sso_global_login import (  # noqa: F401 — fixtures
    _capture_login_state,
    _FakeIdP,
    _seed_global_oidc,
    _truncate_all,
    configured_app,
    idp,
)

pytestmark = pytest.mark.integration


def _client(app: object) -> AsyncClient:
    # https so httpx keeps the `Secure` session cookie in its jar.
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver")


@pytest.mark.asyncio
async def test_oidc_callback_redirects_to_the_panel_with_the_session_cookie(
    configured_app,  # noqa: F811 — fixture importada, no una redefinición
    migrations_pg_dsn: str,
    idp: _FakeIdP,  # noqa: F811
) -> None:
    from api_server.auth.cookies import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME

    await _truncate_all(migrations_pg_dsn)
    provider_id = await _seed_global_oidc(migrations_pg_dsn)

    async with _client(configured_app) as client:
        state = await _capture_login_state(client, provider_id, idp)
        resp = await client.get(
            "/auth/sso/oidc/callback",
            params={"code": "fake-auth-code", "state": state},
            follow_redirects=False,
        )

        # A redirect, NOT a JSON body with the token in it.
        assert resp.status_code == 303, resp.text
        assert resp.headers["location"] == "http://testserver/auth/callback"
        assert "access_token" not in resp.text

        session_cookie = client.cookies.get(SESSION_COOKIE_NAME)
        assert session_cookie
        assert client.cookies.get(CSRF_COOKIE_NAME)

        raw = next(
            h for h in resp.headers.get_list("set-cookie") if h.startswith(SESSION_COOKIE_NAME)
        )
        assert "HttpOnly" in raw and "Secure" in raw, raw

        # And the cookie really is a working session: the JIT-provisioned user
        # is reachable with NO Authorization header at all.
        me = await client.get("/me")
        assert me.status_code == 200, me.text
        assert me.json()["email"] == "worker@acme.test"

        # ---------------------------------------------------------------
        # Second leg, in the SAME test on purpose: the landing URL must not
        # be steerable by anything the IdP or the browser sends, or the SSO
        # callback becomes an open redirect that hands out a fresh session
        # cookie on arrival.
        #
        # It shares this test function because a SECOND `configured_app` in
        # the same process trips a PRE-EXISTING `DuplicateTimeseries` in the
        # Prometheus registry (reproducible on untouched
        # `test_sso_global_login.py`, whose own second test fails the same
        # way). Splitting this in two would be reporting someone else's bug
        # as mine.
        # ---------------------------------------------------------------
        state = await _capture_login_state(client, provider_id, idp)
        steered = await client.get(
            "/auth/sso/oidc/callback",
            params={
                "code": "fake-auth-code",
                "state": state,
                # Every parameter name an "open redirect by accident" arrives under.
                "next": "https://evil.example/",
                "redirect_uri": "https://evil.example/",
                "RelayState": "https://evil.example/",
            },
            follow_redirects=False,
        )
        assert steered.status_code == 303, steered.text
        assert "evil.example" not in steered.headers["location"]
        assert steered.headers["location"] == "http://testserver/auth/callback"
