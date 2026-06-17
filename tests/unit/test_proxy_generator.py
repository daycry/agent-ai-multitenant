"""Unit tests for the reverse-proxy (Caddy) generator + TLS config (ADR 0061).

Plan prod-01 task_15 / deploy-7. Two pure surfaces under test:

  * ``installer_backend.config.SystemConfig`` TLS fields + validator
    (``internal`` default, ``provided`` needs cert+key, ``acme`` needs an email
    and rejects an IP domain);
  * ``installer_backend.proxy_generator.generate_caddyfile`` — the Caddyfile the
    installer materialises: single origin (SPA at ``/``, backend under ``/api``),
    the ``/api/v1`` no-strip rule BEFORE the generic ``/api`` strip, HSTS, a
    plain-HTTP health endpoint, and the TLS block per mode.

Pure functions: no host access, no Caddy parse (the e2e in task_20 runs Caddy
for real).
"""

from __future__ import annotations

import pytest
from installer_backend.config import (
    InstallerConfig,
    OllamaProvider,
    PortsConfig,
    ProvidersConfig,
    ResourceConfig,
    StorageConfig,
    SystemConfig,
    TenantConfig,
)
from installer_backend.proxy_generator import generate_caddyfile
from pydantic import ValidationError

pytestmark = pytest.mark.unit


def _config(*, system: SystemConfig | None = None) -> InstallerConfig:
    return InstallerConfig(
        system=system or SystemConfig(domain="agentic.example.com"),
        resources=ResourceConfig(worker_replicas=2, worker_memory_gib=4),
        storage=StorageConfig(
            data_root="/data/agent-platform",
            minio_bucket="agentic-platform",
            minio_access_key="throwaway-access",
            minio_secret_key="throwaway-secret-value-123",
        ),
        providers=ProvidersConfig(ollama=OllamaProvider(enabled=True, endpoint="http://o:11434")),
        tenant=TenantConfig(tenant_name="Acme", admin_email="admin@example.com"),
        ports=PortsConfig(),
    )


# ---------------------------------------------------------------------------
# SystemConfig TLS fields + validator.
# ---------------------------------------------------------------------------
def test_tls_mode_defaults_to_internal() -> None:
    # Back-compat: a config with only domain still loads, self-signed by default.
    sys_cfg = SystemConfig(domain="agentic.example.com")
    assert sys_cfg.tls_mode == "internal"


def test_tls_provided_requires_cert_and_key() -> None:
    with pytest.raises(ValidationError):
        SystemConfig(domain="agentic.example.com", tls_mode="provided")
    with pytest.raises(ValidationError):
        SystemConfig(domain="agentic.example.com", tls_mode="provided", tls_cert_path="/c.crt")


def test_tls_provided_with_cert_and_key_ok() -> None:
    sys_cfg = SystemConfig(
        domain="agentic.example.com",
        tls_mode="provided",
        tls_cert_path="/etc/ssl/server.crt",
        tls_key_path="/etc/ssl/server.key",
    )
    assert sys_cfg.tls_mode == "provided"


def test_tls_acme_requires_email() -> None:
    with pytest.raises(ValidationError):
        SystemConfig(domain="agentic.example.com", tls_mode="acme")


def test_tls_acme_with_email_ok() -> None:
    sys_cfg = SystemConfig(
        domain="agentic.example.com", tls_mode="acme", tls_acme_email="ops@example.com"
    )
    assert sys_cfg.tls_acme_email == "ops@example.com"


def test_tls_acme_rejects_ip_domain() -> None:
    # ACME CAs do not issue for bare IPs.
    with pytest.raises(ValidationError):
        SystemConfig(domain="10.0.0.5", tls_mode="acme", tls_acme_email="ops@example.com")


def test_tls_acme_ca_must_be_an_http_url() -> None:
    with pytest.raises(ValidationError):
        SystemConfig(
            domain="agentic.example.com",
            tls_mode="acme",
            tls_acme_email="ops@example.com",
            tls_acme_ca="not-a-url",
        )


# ---------------------------------------------------------------------------
# Caddyfile routing.
# ---------------------------------------------------------------------------
def test_caddyfile_contains_the_configured_domain() -> None:
    out = generate_caddyfile(_config())
    assert "agentic.example.com" in out


def test_caddyfile_emits_hsts_and_compression() -> None:
    out = generate_caddyfile(_config())
    assert "Strict-Transport-Security" in out
    assert "encode" in out


def test_caddyfile_routes_api_v1_intact_before_stripping_api() -> None:
    # The lone backend route that already starts with /api must be matched and
    # forwarded INTACT before the generic /api strip, or the public API breaks.
    out = generate_caddyfile(_config())
    # The bare /api/v1 and /api/v1/* both match the no-strip rule.
    assert "handle /api/v1 /api/v1/*" in out
    idx_v1 = out.index("handle /api/v1")
    idx_api = out.index("handle_path /api/*")
    assert idx_v1 < idx_api, "the /api/v1 no-strip rule must precede the /api strip"
    assert "reverse_proxy api-server:8000" in out


def test_caddyfile_falls_back_to_the_admin_panel_spa() -> None:
    out = generate_caddyfile(_config())
    idx_api = out.index("handle_path /api/*")
    idx_spa = out.index("reverse_proxy admin-panel:3000")
    assert idx_spa > idx_api, "the SPA catch-all must come after the /api routes"


def test_caddyfile_has_a_plain_http_health_endpoint() -> None:
    # The container healthcheck hits :80/healthz; it must NOT redirect to https
    # (a 308 + self-signed cert would mark the proxy unhealthy).
    out = generate_caddyfile(_config())
    assert ":80" in out
    assert "/healthz" in out
    assert "respond" in out
    assert "redir https://{host}{uri}" in out


def test_caddyfile_disables_the_admin_api() -> None:
    out = generate_caddyfile(_config())
    assert "admin off" in out


def test_caddyfile_has_no_env_secret_markers() -> None:
    # The Caddyfile carries no secrets and no ${ENV} references.
    out = generate_caddyfile(_config())
    assert "${" not in out


# ---------------------------------------------------------------------------
# Caddyfile TLS block per mode.
# ---------------------------------------------------------------------------
def test_caddyfile_tls_internal_is_the_default_self_signed() -> None:
    out = generate_caddyfile(_config())
    assert "tls internal" in out


def test_caddyfile_tls_provided_uses_the_mounted_cert() -> None:
    sys_cfg = SystemConfig(
        domain="agentic.example.com",
        tls_mode="provided",
        tls_cert_path="/etc/ssl/server.crt",
        tls_key_path="/etc/ssl/server.key",
    )
    out = generate_caddyfile(_config(system=sys_cfg))
    assert "tls /etc/caddy/tls/server.crt /etc/caddy/tls/server.key" in out
    assert "tls internal" not in out


def test_caddyfile_tls_acme_emits_email_and_no_static_tls_directive() -> None:
    sys_cfg = SystemConfig(
        domain="agentic.example.com", tls_mode="acme", tls_acme_email="ops@example.com"
    )
    out = generate_caddyfile(_config(system=sys_cfg))
    assert "email ops@example.com" in out
    assert "tls internal" not in out
    assert "/etc/caddy/tls/server.crt" not in out


def test_caddyfile_tls_acme_emits_acme_ca_when_given() -> None:
    sys_cfg = SystemConfig(
        domain="agentic.example.com",
        tls_mode="acme",
        tls_acme_email="ops@example.com",
        tls_acme_ca="https://acme.corp.internal/directory",
    )
    out = generate_caddyfile(_config(system=sys_cfg))
    assert "acme_ca https://acme.corp.internal/directory" in out
