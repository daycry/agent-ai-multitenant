"""Unit tests for the smoke-probe logic, against a mocked httpx transport.

These run EVERYWHERE (no deployed stack needed): every probe is driven by an
``httpx.MockTransport`` that replays canned responses, so the
status-code/JSON interpretation and result shaping are fully covered even
when ``pytest tests/smoke/`` is run with no live target. This is what makes
the suite's assertions meaningful in CI while the live tests in
``test_smoke.py`` skip-guard.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from tests.smoke import probes

BASE = "https://platform.example.test"


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    """An httpx.Client whose every request is served by ``handler``."""
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


# ---------------------------------------------------------------------------
# Health probe
# ---------------------------------------------------------------------------
def test_health_ok_on_200() -> None:
    with _client(lambda r: httpx.Response(200, json={"status": "ok"})) as client:
        result = probes.probe_health(client, BASE)
    assert result.ok and result.reachable
    assert result.status_code == 200


def test_health_fails_on_503_but_is_reachable() -> None:
    with _client(lambda r: httpx.Response(503, text="down")) as client:
        result = probes.probe_health(client, BASE)
    assert result.reachable is True
    assert result.ok is False
    assert result.status_code == 503


def test_health_unreachable_on_transport_error() -> None:
    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with _client(_boom) as client:
        result = probes.probe_health(client, BASE)
    assert result.reachable is False
    assert result.ok is False
    assert result.status_code is None
    assert "unreachable" in result.detail


def test_health_targets_the_right_url() -> None:
    seen: list[str] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"status": "ok"})

    # Trailing slash on base + leading slash on path must not double up.
    with _client(_record) as client:
        probes.probe_health(client, BASE + "/")
    assert seen == [f"{BASE}/healthz"]


# ---------------------------------------------------------------------------
# Login probe + token extraction
# ---------------------------------------------------------------------------
def test_login_ok_with_access_token() -> None:
    with _client(lambda r: httpx.Response(200, json={"access_token": "jwt-123"})) as client:
        result = probes.probe_login(client, BASE, email="a@b.c", password="pw")
    assert result.ok and result.reachable
    assert "access token" in result.detail


def test_login_ok_on_mfa_challenge() -> None:
    """A 200 carrying an MFA challenge still proves the auth path is alive."""
    with _client(lambda r: httpx.Response(200, json={"mfa_required": True})) as client:
        result = probes.probe_login(client, BASE, email="a@b.c", password="pw")
    assert result.ok is True
    assert "mfa" in result.detail.lower()


def test_login_fails_on_401() -> None:
    with _client(lambda r: httpx.Response(401, json={"detail": "bad creds"})) as client:
        result = probes.probe_login(client, BASE, email="a@b.c", password="wrong")
    assert result.reachable is True
    assert result.ok is False
    assert result.status_code == 401


def test_login_fails_on_500() -> None:
    with _client(lambda r: httpx.Response(500, text="boom")) as client:
        result = probes.probe_login(client, BASE, email="a@b.c", password="pw")
    assert result.reachable is True
    assert result.ok is False
    assert result.status_code == 500


def test_login_posts_credentials_as_json() -> None:
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["content"] = request.content
        return httpx.Response(200, json={"access_token": "t"})

    with _client(_capture) as client:
        probes.probe_login(client, BASE, email="me@x.io", password="s3cret")
    assert captured["method"] == "POST"
    assert captured["url"] == f"{BASE}/auth/login"
    assert b"me@x.io" in captured["content"]  # type: ignore[operator]
    assert b"s3cret" in captured["content"]  # type: ignore[operator]


@pytest.mark.parametrize("field", ["access_token", "token", "jwt"])
def test_extract_access_token_tolerates_field_name(field: str) -> None:
    response = httpx.Response(200, json={field: "value-xyz"})
    assert probes.extract_access_token(response) == "value-xyz"


def test_extract_access_token_none_for_mfa_and_non200() -> None:
    assert probes.extract_access_token(httpx.Response(200, json={"mfa_required": True})) is None
    assert probes.extract_access_token(httpx.Response(401, json={"access_token": "x"})) is None
    assert probes.extract_access_token(httpx.Response(200, text="not-json")) is None
    assert probes.extract_access_token(httpx.Response(200, json=["a", "list"])) is None


# ---------------------------------------------------------------------------
# API v1 probe
# ---------------------------------------------------------------------------
def test_api_v1_ok_on_json_list() -> None:
    with _client(lambda r: httpx.Response(200, json=[{"id": "p1"}, {"id": "p2"}])) as client:
        result = probes.probe_api_v1(client, BASE, token="tok")
    assert result.ok and result.reachable
    assert "2 items" in result.detail


def test_api_v1_sends_bearer_token() -> None:
    seen_auth: list[str | None] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("authorization"))
        return httpx.Response(200, json=[])

    with _client(_record) as client:
        probes.probe_api_v1(client, BASE, token="abc123")
    assert seen_auth == ["Bearer abc123"]


def test_api_v1_fails_on_401_403() -> None:
    for status in (401, 403):
        with _client(lambda r, s=status: httpx.Response(s, json={"detail": "no"})) as client:
            result = probes.probe_api_v1(client, BASE, token="bad")
        assert result.reachable is True
        assert result.ok is False
        assert result.status_code == status
        assert "token rejected" in result.detail


def test_api_v1_fails_on_200_non_list() -> None:
    with _client(lambda r: httpx.Response(200, json={"not": "a list"})) as client:
        result = probes.probe_api_v1(client, BASE, token="tok")
    assert result.reachable is True
    assert result.ok is False


def test_api_v1_unreachable_on_timeout() -> None:
    def _timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with _client(_timeout) as client:
        result = probes.probe_api_v1(client, BASE, token="tok")
    assert result.reachable is False
    assert result.status_code is None


# ---------------------------------------------------------------------------
# Generic reachability probe (admin panel / monitoring UIs)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status", [200, 302, 307, 401, 403])
def test_reachable_ok_for_serving_statuses(status: int) -> None:
    with _client(lambda r: httpx.Response(status, text="")) as client:
        result = probes.probe_reachable(client, BASE, "admin_panel")
    assert result.ok is True
    assert result.reachable is True
    assert result.status_code == status


def test_reachable_fails_on_5xx() -> None:
    with _client(lambda r: httpx.Response(502, text="bad gateway")) as client:
        result = probes.probe_reachable(client, BASE, "admin_panel")
    assert result.reachable is True
    assert result.ok is False
    assert result.status_code == 502


def test_reachable_unreachable_on_connect_error() -> None:
    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with _client(_boom) as client:
        result = probes.probe_reachable(client, BASE, "grafana", path="/api/health")
    assert result.reachable is False
    assert result.ok is False
    assert result.name == "grafana"


def test_reachable_uses_given_path() -> None:
    seen: list[str] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200)

    with _client(_record) as client:
        probes.probe_reachable(client, BASE, "prometheus", path="/-/healthy")
    assert seen == [f"{BASE}/-/healthy"]


# ---------------------------------------------------------------------------
# Diagnóstico de base mal apuntada (2026-08-27)
# ---------------------------------------------------------------------------
#
# El defecto que motiva esto: apuntando `SMOKE_BASE_URL` a la RAÍZ del gateway
# en vez de a la base de la api-server, `/healthz` responde 200 —lo contesta el
# propio Caddy, sin proxy— y `/readyz` cae en el SPA y devuelve 404. La suite
# fallaba entonces en `test_readyz` con «readiness check failed», que se lee
# como «Postgres o Redis están caídos» y no como «has apuntado mal».
#
# Es el modo de fallo caro: manda a alguien a diagnosticar una caída que no
# existe, de madrugada y con el despliegue recién hecho. La configuración mala
# tiene que decir que es configuración.


def test_liveness_sin_readiness_se_reconoce_como_base_equivocada() -> None:
    """200 en /healthz + 404 en /readyz = la base apunta al gateway, no a la API."""
    salud = probes.ProbeResult(name="health", ok=True, reachable=True, status_code=200, detail="ok")
    readiness = probes.ProbeResult(
        name="health", ok=False, reachable=True, status_code=404, detail="unexpected status 404"
    )
    assert probes.base_url_no_es_la_de_la_api(salud, readiness) is True


def test_un_stack_sano_no_se_confunde_con_una_base_equivocada() -> None:
    """El caso bueno: readiness responde 200. No hay nada que diagnosticar."""
    salud = probes.ProbeResult(name="health", ok=True, reachable=True, status_code=200, detail="ok")
    readiness = probes.ProbeResult(
        name="health", ok=True, reachable=True, status_code=200, detail="ok"
    )
    assert probes.base_url_no_es_la_de_la_api(salud, readiness) is False


def test_readiness_caida_de_verdad_NO_se_disfraza_de_error_de_configuracion() -> None:
    """La mitad que impide que el arreglo tape el fallo que debe ver.

    Un 503 en /readyz es la api-server diciendo que una dependencia está caída.
    Eso es una incidencia real y tiene que seguir fallando como tal — si el
    diagnóstico se lo tragase, habríamos cambiado un mensaje confuso por uno
    tranquilizador, que es peor.
    """
    salud = probes.ProbeResult(name="health", ok=True, reachable=True, status_code=200, detail="ok")
    readiness = probes.ProbeResult(
        name="health", ok=False, reachable=True, status_code=503, detail="postgresql unreachable"
    )
    assert probes.base_url_no_es_la_de_la_api(salud, readiness) is False
