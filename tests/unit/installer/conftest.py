"""Shared fixtures for the installer unit tests (Plan prod-01 Fase F).

A throwaway :class:`InstallerConfig` + :class:`GeneratedSecrets` used to drive
the real seams (RealStepExecutor / RealCredentialBuilder / teardown) against
in-memory fakes. No host access, no real secrets.
"""

from __future__ import annotations

import pytest
from installer_backend.config import (
    Environment,
    InstallerConfig,
    OllamaProvider,
    PortsConfig,
    ProvidersConfig,
    ResourceConfig,
    StorageConfig,
    SystemConfig,
    TenantConfig,
)
from installer_backend.config_generators import GeneratedSecrets, generate_secrets


def make_installer_config(*, environment: Environment = Environment.PRODUCTION) -> InstallerConfig:
    return InstallerConfig(
        system=SystemConfig(domain="agentic.example.com", environment=environment),
        resources=ResourceConfig(
            worker_replicas=2,
            worker_memory_gib=4,
            gpu_enabled=False,
            ollama_mode=None,
            embedding_model="nomic-embed-text",
        ),
        storage=StorageConfig(
            data_root="/data/agent-platform",
            minio_bucket="agentic-platform",
            minio_access_key="throwaway-access",
            minio_secret_key="throwaway-secret-value-123",
        ),
        providers=ProvidersConfig(ollama=OllamaProvider(enabled=True, endpoint="http://o:11434")),
        tenant=TenantConfig(tenant_name="Acme Corp", admin_email="admin@acme.com"),
        ports=PortsConfig(),
    )


@pytest.fixture
def installer_config() -> InstallerConfig:
    return make_installer_config()


@pytest.fixture
def gen_secrets() -> GeneratedSecrets:
    return generate_secrets()
