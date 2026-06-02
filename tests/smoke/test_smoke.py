"""Post-deploy smoke tests against a LIVE stack (task_15_26).

Every test here depends on a fixture that SKIPS when its prerequisite env is
absent or the deployed target is unreachable (see ``conftest.py``), so
``pytest tests/smoke/`` exits 0 in CI / this dev env (all skipped) and runs
for real once ``SMOKE_BASE_URL`` (+ optional credentials / monitoring URLs)
point at a deployed stack.

Mapping to the post-deploy checklist:
  * health/readiness of the api-server         -> test_health
  * auth/login probe                           -> test_login
  * minimal authenticated API v1 call          -> test_api_v1_with_token
  * admin-panel reachability                   -> test_admin_panel_reachable
  * monitoring endpoints (Grafana/Prometheus)  -> test_grafana_*/test_prometheus_*
"""

from __future__ import annotations

import httpx

from tests.smoke import probes


def test_health(smoke_client: httpx.Client, smoke_base_url: str) -> None:
    """The api-server answers ``GET /healthz`` with a 2xx (service is up)."""
    result = probes.probe_health(smoke_client, smoke_base_url)
    assert result.reachable, result.detail
    assert result.ok, f"health check failed: {result.detail}"


def test_login(
    smoke_client: httpx.Client,
    smoke_base_url: str,
    smoke_login_credentials: tuple[str, str],
) -> None:
    """``POST /auth/login`` is wired and accepts the supplied credentials."""
    email, password = smoke_login_credentials
    result = probes.probe_login(smoke_client, smoke_base_url, email=email, password=password)
    assert result.reachable, result.detail
    assert result.ok, f"login probe failed: {result.detail}"


def test_api_v1_with_token(
    smoke_client: httpx.Client,
    smoke_base_url: str,
    smoke_api_token: str,
) -> None:
    """A read-scoped token can call a minimal API v1 endpoint and get JSON."""
    result = probes.probe_api_v1(smoke_client, smoke_base_url, token=smoke_api_token)
    assert result.reachable, result.detail
    assert result.ok, f"API v1 probe failed: {result.detail}"


def test_admin_panel_reachable(
    smoke_client: httpx.Client,
    smoke_admin_panel_url: str,
) -> None:
    """The admin panel is serving (200 / redirect-to-login / auth wall)."""
    result = probes.probe_reachable(smoke_client, smoke_admin_panel_url, "admin_panel")
    assert result.reachable, result.detail
    assert result.ok, f"admin panel not serving: {result.detail}"


def test_grafana_healthy(
    smoke_client: httpx.Client,
    smoke_grafana_url: str,
) -> None:
    """Grafana answers its health endpoint (``/api/health``)."""
    result = probes.probe_reachable(smoke_client, smoke_grafana_url, "grafana", path="/api/health")
    assert result.reachable, result.detail
    assert result.ok, f"Grafana not healthy: {result.detail}"


def test_prometheus_healthy(
    smoke_client: httpx.Client,
    smoke_prometheus_url: str,
) -> None:
    """Prometheus answers its readiness endpoint (``/-/healthy``)."""
    result = probes.probe_reachable(
        smoke_client, smoke_prometheus_url, "prometheus", path="/-/healthy"
    )
    assert result.reachable, result.detail
    assert result.ok, f"Prometheus not healthy: {result.detail}"
