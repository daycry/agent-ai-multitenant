"""Fixtures + skip-guard for the post-deploy smoke suite (task_15_26).

The whole point of this suite is that it runs against a LIVE deployed stack
when one is configured, and SKIPS cleanly everywhere else so CI / this dev env
stay green. The skip decision lives here, in one place:

  * ``smoke_base_url`` — read from ``SMOKE_BASE_URL``; skips the live tests
    when unset.
  * ``smoke_client`` — a real ``httpx.Client`` pointed at the stack; the
    fixture *first* pings ``/healthz`` and skips the live tests if the target
    is unreachable (deployment down / wrong URL), so a missing stack never
    fails — it skips with a clear reason.

Optional env knobs (only consumed when ``SMOKE_BASE_URL`` is set):
  SMOKE_BASE_URL          base URL of the deployed api-server (required to run)
  SMOKE_TIMEOUT           per-request timeout in seconds (default 5)
  SMOKE_LOGIN_EMAIL       credentials for the auth/login probe
  SMOKE_LOGIN_PASSWORD
  SMOKE_API_TOKEN         a read-scoped API v1 token for the authed probe
  SMOKE_ADMIN_PANEL_URL   admin-panel base URL (default = SMOKE_BASE_URL)
  SMOKE_GRAFANA_URL       Grafana base URL (e.g. http://localhost:3000)
  SMOKE_PROMETHEUS_URL    Prometheus base URL (e.g. http://localhost:9090)
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import httpx
import pytest

from tests.smoke.probes import DEFAULT_TIMEOUT, base_url_no_es_la_de_la_api, probe_health


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


@pytest.fixture(scope="session")
def smoke_base_url() -> str:
    """Base URL of the deployed api-server. Skips the live suite if unset."""
    base = _env("SMOKE_BASE_URL")
    if base is None:
        pytest.skip(
            "SMOKE_BASE_URL not set — no deployed stack to smoke-test "
            "(set it to a live api-server URL to run, e.g. "
            "SMOKE_BASE_URL=https://platform.example.com)"
        )
    return base


@pytest.fixture(scope="session")
def smoke_timeout() -> float:
    raw = _env("SMOKE_TIMEOUT")
    if raw is None:
        return DEFAULT_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT


@pytest.fixture(scope="session")
def smoke_client(smoke_base_url: str, smoke_timeout: float) -> Iterator[httpx.Client]:
    """A real httpx.Client against the stack.

    Before yielding, it pings ``/healthz``; if the target is unreachable the
    whole live suite SKIPS (deployment down / wrong URL) rather than failing —
    that is the skip-guard that keeps CI green when no stack is deployed.
    """
    client = httpx.Client(timeout=smoke_timeout, follow_redirects=False)
    health = probe_health(client, smoke_base_url)
    if not health.reachable:
        client.close()
        pytest.skip(
            f"deployed stack at {smoke_base_url} is unreachable ({health.detail}); "
            "skipping post-deploy smoke tests"
        )
    # Una base mal apuntada tiene que decir que es configuración (2026-08-27).
    # Con la RAÍZ del gateway como base, `/healthz` da 200 —lo contesta el propio
    # reverse proxy, sin proxificar— y `/readyz` cae en el SPA y da 404. Sin este
    # aviso la suite fallaba en `test_readyz` con «readiness check failed», que se
    # lee como «Postgres o Redis están caídos»: manda a diagnosticar una caída que
    # no existe, justo después de un despliegue. Falla, no se salta: un skip aquí
    # sería un verde silencioso, y el objetivo es lo contrario.
    readiness = probe_health(client, smoke_base_url, path="/readyz")
    if base_url_no_es_la_de_la_api(health, readiness):
        client.close()
        pytest.fail(
            f"SMOKE_BASE_URL={smoke_base_url} responde /healthz pero da 404 en /readyz: "
            "apunta al gateway, no a la api-server. Detrás de un reverse proxy la base "
            "suele llevar el prefijo (p. ej. http://host:8080/api). Esto es un error de "
            "configuración de la prueba, NO una dependencia caída.",
            pytrace=False,
        )
    try:
        yield client
    finally:
        client.close()


@pytest.fixture(scope="session")
def smoke_login_credentials() -> tuple[str, str]:
    """(email, password) for the login probe. Skips that test if absent."""
    email = _env("SMOKE_LOGIN_EMAIL")
    password = _env("SMOKE_LOGIN_PASSWORD")
    if not email or not password:
        pytest.skip("SMOKE_LOGIN_EMAIL / SMOKE_LOGIN_PASSWORD not set — login probe skipped")
    return email, password


@pytest.fixture(scope="session")
def smoke_api_token() -> str:
    """A read-scoped API v1 token. Skips the authed v1 probe if absent."""
    token = _env("SMOKE_API_TOKEN")
    if not token:
        pytest.skip("SMOKE_API_TOKEN not set — authenticated API v1 probe skipped")
    return token


@pytest.fixture(scope="session")
def smoke_admin_panel_url(smoke_base_url: str) -> str:
    """Admin-panel base URL (defaults to the api-server base URL)."""
    return _env("SMOKE_ADMIN_PANEL_URL") or smoke_base_url


@pytest.fixture(scope="session")
def smoke_grafana_url() -> str:
    url = _env("SMOKE_GRAFANA_URL")
    if not url:
        pytest.skip("SMOKE_GRAFANA_URL not set — Grafana probe skipped")
    return url


@pytest.fixture(scope="session")
def smoke_prometheus_url() -> str:
    url = _env("SMOKE_PROMETHEUS_URL")
    if not url:
        pytest.skip("SMOKE_PROMETHEUS_URL not set — Prometheus probe skipped")
    return url
