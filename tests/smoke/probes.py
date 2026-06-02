"""Pure, transport-agnostic probe logic for the post-deploy smoke suite.

These helpers take an ``httpx.Client`` (whose transport may be the real
network transport against a deployed stack OR an ``httpx.MockTransport`` in a
unit test) and return a small :class:`ProbeResult` describing what happened.
They contain ZERO network setup and never call ``pytest.skip`` — that lives in
``conftest.py`` and the test modules. Keeping the interpretation logic here is
what lets ``test_probes_unit.py`` cover the assertions against a mocked
transport with no deployed stack.

Design notes:
  * Probes never raise on an *HTTP error status*; they fold transport errors
    (connection refused, timeout, DNS) into ``ProbeResult(ok=False,
    reachable=False, ...)`` so the caller can decide whether that means
    "skip" (target down) or "fail" (target up but the contract broke).
  * ``ok`` answers "did the contract hold?"; ``reachable`` answers "did we get
    *any* HTTP response back?". A live test SKIPS when ``not reachable`` and
    FAILS when ``reachable and not ok``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

# Default timeout (seconds) for every probe request. A deployed stack that
# does not answer within this window is treated as unreachable rather than
# hanging the suite.
DEFAULT_TIMEOUT = 5.0


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a single smoke probe.

    Attributes:
        name: short identifier for the probe (used in skip/fail messages).
        ok: the probe's contract held (status + body as expected).
        reachable: the target returned *some* HTTP response (i.e. it is up).
        status_code: the HTTP status seen, or ``None`` on a transport error.
        detail: a human-readable note (the error, or a short success summary).
    """

    name: str
    ok: bool
    reachable: bool
    status_code: int | None
    detail: str


def _join(base_url: str, path: str) -> str:
    """Join a base URL and a path without doubling or dropping the slash."""
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _request(
    client: httpx.Client,
    name: str,
    method: str,
    url: str,
    **kwargs: Any,
) -> tuple[httpx.Response | None, ProbeResult | None]:
    """Issue a request, folding transport errors into an unreachable result.

    Returns ``(response, None)`` on any HTTP response (even 5xx) or
    ``(None, ProbeResult)`` when the target could not be reached at all.
    """
    try:
        response = client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:  # connection refused, timeout, DNS, ...
        return None, ProbeResult(
            name=name,
            ok=False,
            reachable=False,
            status_code=None,
            detail=f"unreachable: {type(exc).__name__}: {exc}",
        )
    return response, None


def probe_health(client: httpx.Client, base_url: str, path: str = "/healthz") -> ProbeResult:
    """Health/readiness probe.

    The api-server exposes ``GET /healthz`` returning ``{"status": "ok"}``.
    Any 2xx with a truthy body counts as healthy; a non-2xx on a reachable
    target is a real failure.
    """
    response, unreachable = _request(client, "health", "GET", _join(base_url, path))
    if unreachable is not None:
        return unreachable
    assert response is not None
    healthy = response.status_code == 200
    detail = "healthy" if healthy else f"unexpected status {response.status_code}"
    return ProbeResult(
        name="health",
        ok=healthy,
        reachable=True,
        status_code=response.status_code,
        detail=detail,
    )


def probe_login(
    client: httpx.Client,
    base_url: str,
    *,
    email: str,
    password: str,
    path: str = "/auth/login",
) -> ProbeResult:
    """Auth/login probe against ``POST /auth/login``.

    A deployed stack answers one of:
      * 200 with a body that carries an access token (full login), OR
      * 200 with an ``mfa_required`` challenge (MFA enrolled).
    Either proves the auth path is wired. A 401 means the supplied
    credentials are wrong (still proves the endpoint works, but the probe
    cannot continue) — surfaced as ``ok=False`` with a clear detail. A 5xx is
    a real failure.
    """
    response, unreachable = _request(
        client,
        "login",
        "POST",
        _join(base_url, path),
        json={"email": email, "password": password},
    )
    if unreachable is not None:
        return unreachable
    assert response is not None
    token = extract_access_token(response)
    if response.status_code == 200 and token is not None:
        return ProbeResult("login", True, True, 200, "logged in (access token issued)")
    if response.status_code == 200:
        # 200 without a token == MFA challenge; the auth path is alive.
        return ProbeResult("login", True, True, 200, "login reached (mfa challenge)")
    if response.status_code == 401:
        return ProbeResult("login", False, True, 401, "invalid credentials")
    return ProbeResult("login", False, True, response.status_code, "login endpoint error")


def extract_access_token(response: httpx.Response) -> str | None:
    """Pull an access token out of a login response, tolerant of field name.

    Returns ``None`` for non-2xx, non-JSON, or an MFA challenge (a 200 that
    carries ``mfa_required`` / no token field).
    """
    if response.status_code != 200:
        return None
    try:
        body = response.json()
    except (ValueError, httpx.DecodingError):
        return None
    if not isinstance(body, dict):
        return None
    if body.get("mfa_required"):
        return None
    for key in ("access_token", "token", "jwt"):
        value = body.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def probe_api_v1(
    client: httpx.Client,
    base_url: str,
    *,
    token: str,
    path: str = "/api/v1/projects",
) -> ProbeResult:
    """Minimal authenticated API v1 call.

    Hits a read-scoped v1 endpoint with a bearer token. A 200 returning a
    JSON list is the contract. A 401/403 means the token is bad (the endpoint
    still works, but the probe cannot prove the authed path) — ``ok=False``.
    """
    response, unreachable = _request(
        client,
        "api_v1",
        "GET",
        _join(base_url, path),
        headers={"Authorization": f"Bearer {token}"},
    )
    if unreachable is not None:
        return unreachable
    assert response is not None
    if response.status_code == 200:
        try:
            body = response.json()
        except (ValueError, httpx.DecodingError):
            return ProbeResult("api_v1", False, True, 200, "200 but body was not JSON")
        if isinstance(body, list):
            return ProbeResult("api_v1", True, True, 200, f"v1 ok ({len(body)} items)")
        return ProbeResult("api_v1", False, True, 200, "200 but body was not a list")
    if response.status_code in (401, 403):
        return ProbeResult("api_v1", False, True, response.status_code, "token rejected")
    return ProbeResult("api_v1", False, True, response.status_code, "v1 endpoint error")


def probe_reachable(
    client: httpx.Client,
    base_url: str,
    name: str,
    path: str = "/",
    *,
    ok_statuses: tuple[int, ...] = (200, 301, 302, 307, 308, 401, 403),
) -> ProbeResult:
    """Generic reachability probe (admin panel, web app, monitoring UIs).

    Many surfaces answer a bare ``GET /`` with a redirect (to ``/login``) or a
    401/403 (auth wall) rather than a 200 — all of which still prove the
    service is *up and serving*, which is all a smoke check needs. Only a 5xx
    or a transport error is a problem.
    """
    response, unreachable = _request(client, name, "GET", _join(base_url, path))
    if unreachable is not None:
        return unreachable
    assert response is not None
    serving = response.status_code in ok_statuses
    detail = "serving" if serving else f"unexpected status {response.status_code}"
    return ProbeResult(
        name=name,
        ok=serving,
        reachable=True,
        status_code=response.status_code,
        detail=detail,
    )
