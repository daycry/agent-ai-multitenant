"""Config-capture validation — wizard steps 2-6 (Plan 15 task_15_03).

Exercises the server-side validation of the config captured by wizard steps
2-6 (system / resources / storage / providers / tenant) and the
``/api/config/validate`` route. No host access: this is pure validation, no
Docker, no /data writes, no Vault.

Coverage (per the task contract):
  * a valid config is accepted and the normalised non-secret echo comes back;
  * invalid configs are rejected — bad domain, bad data root, bad bucket,
    bad email, no provider enabled, an enabled provider missing its creds;
  * secrets are NEVER echoed back — the MinIO secret key and the provider
    tokens/keys never appear in the response body (only ``*_set`` booleans),
    and SecretStr keeps them out of ``repr``/JSON dumps.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from installer_backend.config import (
    AzureFoundryProvider,
    ClaudeSdkProvider,
    CopilotProvider,
    InstallerConfig,
    OllamaProvider,
    ProvidersConfig,
    ResourceConfig,
    StorageConfig,
    SystemConfig,
    TenantConfig,
    validate_config,
)
from installer_backend.main import create_app

pytestmark = pytest.mark.integration

# A secret value we assert never appears in any response/repr/dump.
_SECRET_SENTINEL = "s3cr3t-never-echoed-xyz"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _valid_config_payload(**overrides: object) -> dict[str, object]:
    """A complete, valid steps 2-6 payload (Ollama is the enabled provider)."""

    payload: dict[str, object] = {
        "system": {"domain": "agentic.example.com", "environment": "production"},
        "resources": {
            "worker_replicas": 3,
            "worker_memory_gib": 4,
            "gpu_enabled": False,
        },
        "storage": {
            "data_root": "/data/agent-platform",
            "minio_bucket": "agentic-platform",
            "minio_access_key": "minioadmin",
            "minio_secret_key": _SECRET_SENTINEL,
        },
        "providers": {
            "ollama": {"enabled": True, "endpoint": "http://localhost:11434"},
        },
        "tenant": {"tenant_name": "Acme Corp", "admin_email": "admin@acme.com"},
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Pure validation logic (no route).
# ---------------------------------------------------------------------------
def _build_config(**overrides: object) -> InstallerConfig:
    base: dict[str, object] = {
        "system": SystemConfig(domain="agentic.example.com", environment="production"),
        "resources": ResourceConfig(),
        "storage": StorageConfig(
            minio_access_key="minioadmin",
            minio_secret_key=_SECRET_SENTINEL,
        ),
        "providers": ProvidersConfig(
            ollama=OllamaProvider(enabled=True, endpoint="http://localhost:11434"),
        ),
        "tenant": TenantConfig(tenant_name="Acme Corp", admin_email="admin@acme.com"),
    }
    base.update(overrides)
    return InstallerConfig(**base)  # type: ignore[arg-type]


def test_domain_is_normalised_lowercase() -> None:
    cfg = _build_config(system=SystemConfig(domain="Agentic.Example.COM", environment="production"))
    assert cfg.system.domain == "agentic.example.com"


def test_valid_config_passes_cross_field_validation() -> None:
    res = validate_config(_build_config())
    assert res.valid is True
    assert res.errors == []
    assert res.normalized["system"] == {  # type: ignore[index]
        "domain": "agentic.example.com",
        "environment": "production",
    }


def test_no_provider_enabled_is_rejected() -> None:
    cfg = _build_config(providers=ProvidersConfig())
    res = validate_config(cfg)
    assert res.valid is False
    assert any(e.field == "providers" for e in res.errors)


def test_enabled_provider_without_creds_is_rejected() -> None:
    # Azure Foundry enabled but no endpoint and no key.
    cfg = _build_config(providers=ProvidersConfig(azure_foundry=AzureFoundryProvider(enabled=True)))
    res = validate_config(cfg)
    assert res.valid is False
    fields = {e.field for e in res.errors}
    assert "providers.azure_foundry.apim_endpoint" in fields
    assert "providers.azure_foundry.api_key" in fields


def test_claude_and_copilot_require_tokens() -> None:
    cfg = _build_config(
        providers=ProvidersConfig(
            claude_sdk=ClaudeSdkProvider(enabled=True),
            copilot=CopilotProvider(enabled=True),
        )
    )
    res = validate_config(cfg)
    assert res.valid is False
    fields = {e.field for e in res.errors}
    assert "providers.claude_sdk.oauth_token" in fields
    assert "providers.copilot.oauth_token" in fields


def test_secretstr_keeps_secret_out_of_repr_and_dump() -> None:
    cfg = _build_config()
    assert _SECRET_SENTINEL not in repr(cfg)
    assert _SECRET_SENTINEL not in cfg.model_dump_json()


# ---------------------------------------------------------------------------
# /api/config/validate route.
# ---------------------------------------------------------------------------
def test_route_accepts_valid_config(client: TestClient) -> None:
    resp = client.post("/api/config/validate", json=_valid_config_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["errors"] == []
    assert body["normalized"]["storage"]["minio_access_key"] == "minioadmin"
    assert body["providers"]["ollama_enabled"] is True


def test_route_never_echoes_secrets(client: TestClient) -> None:
    """The MinIO secret key and provider tokens must NEVER come back."""

    payload = _valid_config_payload(
        providers={
            "claude_sdk": {"enabled": True, "oauth_token": _SECRET_SENTINEL},
            "azure_foundry": {
                "enabled": True,
                "apim_endpoint": "https://apim.example.com/openai",
                "api_key": _SECRET_SENTINEL,
            },
        },
    )
    resp = client.post("/api/config/validate", json=payload)
    assert resp.status_code == 200
    # The raw response text must not contain the secret anywhere.
    assert _SECRET_SENTINEL not in resp.text
    body = resp.json()
    # Only presence booleans, never the value.
    assert body["providers"]["claude_sdk_token_set"] is True
    assert body["providers"]["azure_foundry_key_set"] is True
    # The normalised echo carries no secret subtree.
    assert "minio_secret_key" not in body["normalized"]["storage"]


def test_route_rejects_bad_domain_with_422(client: TestClient) -> None:
    resp = client.post(
        "/api/config/validate",
        json=_valid_config_payload(
            system={"domain": "http://not a host/path", "environment": "production"}
        ),
    )
    # Per-field Pydantic validation fails before the body runs -> 422.
    assert resp.status_code == 422


def test_route_rejects_relative_data_root_with_422(client: TestClient) -> None:
    resp = client.post(
        "/api/config/validate",
        json=_valid_config_payload(
            storage={
                "data_root": "relative/path",
                "minio_bucket": "agentic-platform",
                "minio_access_key": "minioadmin",
                "minio_secret_key": _SECRET_SENTINEL,
            }
        ),
    )
    assert resp.status_code == 422


def test_route_rejects_bad_bucket_with_422(client: TestClient) -> None:
    resp = client.post(
        "/api/config/validate",
        json=_valid_config_payload(
            storage={
                "data_root": "/data/agent-platform",
                "minio_bucket": "Invalid_Bucket",
                "minio_access_key": "minioadmin",
                "minio_secret_key": _SECRET_SENTINEL,
            }
        ),
    )
    assert resp.status_code == 422


def test_route_rejects_bad_email_with_422(client: TestClient) -> None:
    resp = client.post(
        "/api/config/validate",
        json=_valid_config_payload(tenant={"tenant_name": "Acme", "admin_email": "not-an-email"}),
    )
    assert resp.status_code == 422


def test_route_rejects_provider_cross_field_with_200_invalid(client: TestClient) -> None:
    """Cross-field failures (enabled w/o creds) return 200 with valid=false."""

    resp = client.post(
        "/api/config/validate",
        json=_valid_config_payload(providers={"ollama": {"enabled": True}}),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert any(e["field"] == "providers.ollama.endpoint" for e in body["errors"])
