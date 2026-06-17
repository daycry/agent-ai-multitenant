"""E2E: install.sh → stack vivo → smoke → uninstall (Plan prod-01 task_20 / deploy-1/2/3).

The test the audit asked for explicitly: a real install on a clean machine, the
proxy serving HTTPS, the published-surface policy (8000/3000 NOT directly
reachable, ADR 0061), a real login with the revealed credential, and a verified
uninstall purge.

HEAVY + host-only: gated by ``E2E_INSTALL=1`` AND a Docker daemon (see
conftest.py). On CI / Windows the whole module SKIPS — green — and that skip does
NOT acredit deploy-1/2/3. Run for real on a Linux runner (nightly, prod-02).
"""

from __future__ import annotations

import socket

import httpx
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(1800)]

#: The domain the minimal profile serves on (Host header for the proxy).
_DOMAIN = "agentic.example.com"
_BASE = "https://127.0.0.1"
_HEADERS = {"Host": _DOMAIN}


def test_install_completes_and_is_not_a_simulation(installed_stack: dict[str, str]) -> None:
    # The fixture asserted rc==0 and no simulation markers; here we confirm the
    # reveal carried a real-looking admin credential.
    assert installed_stack.get("admin_username"), "no se reveló el usuario admin"


def test_proxy_serves_https_healthz(installed_stack: dict[str, str]) -> None:
    # The api-server /healthz is reachable through the proxy under /api (TLS
    # internal/self-signed → verify=False).
    resp = httpx.get(f"{_BASE}/api/healthz", headers=_HEADERS, verify=False, timeout=30)
    assert resp.status_code == 200


def test_direct_app_ports_are_not_published(installed_stack: dict[str, str]) -> None:
    # ADR 0061: only the proxy (80/443) is published; api-server:8000 and
    # admin-panel:3000 must NOT answer directly on the host.
    for port in (8000, 3000):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(3)
            connected = sock.connect_ex(("127.0.0.1", port)) == 0
        assert not connected, f"el puerto {port} responde directo (debería ir tras el proxy)"


def test_admin_login_with_the_revealed_credential(installed_stack: dict[str, str]) -> None:
    username = installed_stack.get("admin_username")
    password = installed_stack.get("admin_password")
    if not username or not password:
        pytest.skip("no se pudieron parsear las credenciales reveladas del stdout del install")
    resp = httpx.post(
        f"{_BASE}/api/auth/login",
        headers=_HEADERS,
        json={"email": username, "password": password},
        verify=False,
        timeout=30,
    )
    # The revealed credential MUST authenticate: a real seeded admin → 200 with a
    # token (or an MFA challenge). A 401 means the seed credential is wrong — that
    # is a FAILURE, not an accepted outcome.
    assert resp.status_code == 200, f"login con la credencial revelada falló: {resp.status_code}"
    body = resp.json()
    assert body.get("access_token") or body.get("mfa_required"), "login 200 sin token ni MFA"
