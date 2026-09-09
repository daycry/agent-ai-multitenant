"""El instalador le dice al api-server qué proveedor Ollama sembrar — y el api-server lo LEE.

Plan `remediacion-instalador-runs-de-serie-2026-09-09`, `task_inst_03` (G1).

## El hueco

Hasta el 2026-09-09 el `.env` generado llevaba `LLM_OLLAMA_ENABLED=true` y
`LLM_OLLAMA_ENDPOINT=http://ollama:11434`. Ningún proceso las leía: el api-server
sólo lee variables con prefijo `API_SERVER_` (`Settings.model_config.env_prefix`),
shared-llm no lee entorno, e `init_tenant` no creaba proveedores. Una instalación
limpia arrancaba con `llm_providers` VACÍA y todo run moría `model_unresolved` —
que es exactamente el estado en que apareció la BD de esta máquina.

Y el test de contrato del `.env` (`test_compose_env_contract.py`) las eximía con
la frase «read by shared-llm», que era falsa. Una exención escrita sobre una
creencia no verificada es un agujero con nombre bonito.

## El arreglo

Las tres variables que el api-server SÍ lee, con su prefijo, mapeadas a campos
reales de `Settings` (el test de contrato lo comprueba solo). El resto de kinds no
se emiten: sus credenciales no las escribe el instalador y una fila sin credencial
mentiría igual que la variable huérfana.
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
from installer_backend.config_generators import build_env_vars, generate_secrets

pytestmark = pytest.mark.unit


def _config(*, ollama: OllamaProvider) -> InstallerConfig:
    return InstallerConfig(
        system=SystemConfig(domain="agentic.example.com", environment=Environment.PRODUCTION),
        resources=ResourceConfig(
            worker_replicas=1, worker_memory_gib=2, gpu_enabled=False, ollama_mode="cpu"
        ),
        storage=StorageConfig(
            data_root="/data/agent-platform",
            minio_bucket="agentic-platform",
            minio_access_key="throwaway-access",
            minio_secret_key="throwaway-secret-value-123",
        ),
        providers=ProvidersConfig(ollama=ollama),
        tenant=TenantConfig(tenant_name="Acme", admin_email="admin@example.com"),
        ports=PortsConfig(),
    )


def test_the_env_carries_the_ollama_provider_with_the_prefix_the_api_server_reads() -> None:
    env = build_env_vars(
        _config(
            ollama=OllamaProvider(
                enabled=True, endpoint="http://ollama:11434", chat_model="qwen2.5:3b"
            )
        ),
        generate_secrets(),
    )

    assert env["API_SERVER_LLM_OLLAMA_ENABLED"] == "true"
    assert env["API_SERVER_LLM_OLLAMA_ENDPOINT"] == "http://ollama:11434"
    assert env["API_SERVER_LLM_OLLAMA_CHAT_MODEL"] == "qwen2.5:3b"


def test_the_dead_unprefixed_llm_keys_are_gone() -> None:
    """Una variable que nadie lee en el `.env` de una instalación es una promesa
    falsa: quien la vea creerá que el proveedor quedó configurado."""
    env = build_env_vars(
        _config(ollama=OllamaProvider(enabled=True, endpoint="http://ollama:11434")),
        generate_secrets(),
    )
    dead = sorted(k for k in env if k.startswith("LLM_"))
    assert dead == [], f"claves LLM_* sin prefijo que nadie lee: {dead}"


def test_in_stack_ollama_gets_the_internal_endpoint_by_default() -> None:
    """Sin `endpoint` explícito y con Ollama in-stack, el api-server recibe el
    host INTERNO del compose — que es además el que permite el egress-proxy."""
    env = build_env_vars(
        _config(ollama=OllamaProvider(enabled=True)),
        generate_secrets(),
    )
    assert env["API_SERVER_LLM_OLLAMA_ENDPOINT"] == "http://ollama:11434"


def test_an_empty_chat_model_is_rejected_at_config_time() -> None:
    """Mejor un 422 del `install.yaml` que un run que muere en `plan`."""
    with pytest.raises(ValueError):
        OllamaProvider(enabled=True, endpoint="http://ollama:11434", chat_model="  ")
