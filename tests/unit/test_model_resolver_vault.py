"""Vault caído en el dispatch: fallo EXPLÍCITO, no 401 dentro del sandbox.

prod-07 task_prod07_07 (llm-9 + workers-8).

El modo de fallo
----------------
``resolve_provider_config`` se escribió para el ASISTENTE, donde degradar a
«sin credencial» es correcto: si Vault no responde, el factory se queda con la
credencial del env/instalador y el chat sigue. En el DISPATCH esa premisa es
falsa por diseño — el sandbox del agent-runtime no tiene env de credenciales
(principio #2), así que «secret = {}» no degrada a nada: lanza el contenedor con
un spec sin credencial, el proveedor devuelve 401 y el run muere atribuyendo la
causa al proveedor cuando el culpable era Vault.

Los dos caminos del resolver tenían el mismo `except ... : secret = {}`: el de
``provider_id`` (línea propia) y el de ``kind`` (heredado de
``factory_resolver``). Este fichero fija el comportamiento correcto en AMBOS.

Lo que NO se toca: una fila SIN ``secret_vault_path`` (ollama local) sigue
resolviéndose sin credencial. Ahí no hay nada que leer, así que no hay nada que
pueda fallar — y abortar rompería el caso legítimo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from api_server.llm_providers import factory_resolver
from api_server.llm_providers.vault import LLMProviderVaultError
from workers import model_resolver
from workers.model_resolver import ModelResolutionError, resolve_model_spec

pytestmark = pytest.mark.unit

_PROVIDER_ID = str(uuid4())


class _Row:
    """Fila mínima de ``llm_providers`` (lo que leen los dos caminos)."""

    def __init__(self, *, kind: str, vault_path: str | None) -> None:
        self.id = _PROVIDER_ID
        self.kind = kind
        self.base_url = "https://apim.example"
        self.secret_vault_path = vault_path
        self.is_active = True


class _BrokenVault:
    """Vault que no responde. Cuenta los intentos: el plan pide reintentar UNA vez."""

    def __init__(self) -> None:
        self.reads = 0

    def read_secret(self, _path: str) -> dict[str, str]:
        self.reads += 1
        raise LLMProviderVaultError("connection refused")


class _GoodVault:
    def __init__(self, secret: dict[str, str]) -> None:
        self._secret = secret
        self.reads = 0

    def read_secret(self, _path: str) -> dict[str, str]:
        self.reads += 1
        return dict(self._secret)


@pytest.fixture()
def by_kind(monkeypatch: pytest.MonkeyPatch) -> list[_Row]:
    """El camino `kind`: la fila ACTIVA más nueva del kind."""
    rows: list[_Row] = []

    async def _list(_session: Any, kind: str) -> list[_Row]:
        return [row for row in rows if row.kind == kind]

    monkeypatch.setattr(factory_resolver, "list_active_llm_providers_by_kind", _list)
    return rows


@pytest.fixture()
def by_provider_id(monkeypatch: pytest.MonkeyPatch) -> dict[str, _Row]:
    """El camino `provider_id`, que resuelve la fila EXACTA."""
    rows: dict[str, _Row] = {}
    module = __import__("api_server.db.llm_providers", fromlist=["get_llm_provider"])

    async def _get(_session: Any, pid: Any) -> _Row | None:
        return rows.get(str(pid))

    monkeypatch.setattr(module, "get_llm_provider", _get)
    return rows


# ---------------------------------------------------------------------------
# Camino `kind`
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_vault_down_on_the_kind_path_aborts(by_kind: list[_Row]) -> None:
    by_kind.append(_Row(kind="azure_foundry", vault_path="llm/azure-1"))
    vault = _BrokenVault()

    with pytest.raises(ModelResolutionError) as info:
        await resolve_model_spec(
            None, {"provider": "azure_foundry", "model": "azure/gpt-4o"}, vault=vault
        )

    assert info.value.abort_code == "vault_unavailable"
    assert "vault" in str(info.value).lower()


@pytest.mark.asyncio
async def test_the_vault_read_is_retried_once_before_aborting(by_kind: list[_Row]) -> None:
    """Un blip de red no debe tumbar el run al primer intento."""
    by_kind.append(_Row(kind="azure_foundry", vault_path="llm/azure-1"))
    vault = _BrokenVault()

    with pytest.raises(ModelResolutionError):
        await resolve_model_spec(
            None, {"provider": "azure_foundry", "model": "azure/gpt-4o"}, vault=vault
        )

    assert vault.reads == 2, f"se esperaba 1 reintento, hubo {vault.reads} lecturas"


@pytest.mark.asyncio
async def test_a_transient_vault_failure_that_recovers_resolves(by_kind: list[_Row]) -> None:
    """Y el reintento sirve para algo: si la segunda lectura funciona, el run sigue."""
    by_kind.append(_Row(kind="azure_foundry", vault_path="llm/azure-1"))

    class _FlakyVault:
        def __init__(self) -> None:
            self.reads = 0

        def read_secret(self, _path: str) -> dict[str, str]:
            self.reads += 1
            if self.reads == 1:
                raise LLMProviderVaultError("blip")
            return {"api_key": "sub-key"}

    vault = _FlakyVault()
    spec = await resolve_model_spec(
        None, {"provider": "azure_foundry", "model": "azure/gpt-4o"}, vault=vault
    )
    assert spec["subscription_key"] == "sub-key"


@pytest.mark.asyncio
async def test_a_row_without_a_vault_path_still_resolves(by_kind: list[_Row]) -> None:
    """Ollama local: no hay secreto que leer, así que no hay nada que abortar."""
    by_kind.append(_Row(kind="ollama", vault_path=None))
    vault = _BrokenVault()

    spec = await resolve_model_spec(
        None, {"provider": "ollama", "model": "ollama/llama3.1"}, vault=vault
    )
    assert spec["kind"] == "ollama"
    assert vault.reads == 0


@pytest.mark.asyncio
async def test_no_active_row_keeps_the_model_unresolved_code(by_kind: list[_Row]) -> None:
    """No confundir los dos fallos: sin fila activa el abort_code sigue siendo
    ``model_unresolved``. Un operador que vea ``vault_unavailable`` tiene que
    poder ir a mirar Vault y no el catálogo."""
    with pytest.raises(ModelResolutionError) as info:
        await resolve_model_spec(
            None, {"provider": "copilot", "model": "gpt-4o"}, vault=_GoodVault({})
        )
    assert info.value.abort_code == "model_unresolved"


# ---------------------------------------------------------------------------
# Camino `provider_id`
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_vault_down_on_the_provider_id_path_aborts(
    by_provider_id: dict[str, _Row], by_kind: list[_Row]
) -> None:
    """El camino de `provider_id` tenía su PROPIO `except: secret = {}` —
    arreglar solo el de `kind` habría dejado el agujero abierto en el camino que
    usa el dispatch por defecto desde la Feature B."""
    by_provider_id[_PROVIDER_ID] = _Row(kind="copilot", vault_path="llm/copilot-1")
    vault = _BrokenVault()

    with pytest.raises(ModelResolutionError) as info:
        await resolve_model_spec(
            None,
            {"provider_id": _PROVIDER_ID, "provider": "copilot", "model": "gpt-4o"},
            vault=vault,
        )
    assert info.value.abort_code == "vault_unavailable"


@pytest.mark.asyncio
async def test_provider_id_path_resolves_with_a_healthy_vault(
    by_provider_id: dict[str, _Row],
) -> None:
    by_provider_id[_PROVIDER_ID] = _Row(kind="copilot", vault_path="llm/copilot-1")
    spec = await resolve_model_spec(
        None,
        {"provider_id": _PROVIDER_ID, "provider": "copilot", "model": "gpt-4o"},
        vault=_GoodVault({"oauth_token": "gho_x"}),
    )
    assert spec["github_token"] == "gho_x"


# ---------------------------------------------------------------------------
# El código de aborto llega hasta la ejecución (§5: seguir el dato de punta a
# punta — un abort_code que nadie propaga no existe para el operador)
# ---------------------------------------------------------------------------
def test_the_abort_code_is_not_hardcoded_in_the_dispatch() -> None:
    source = (Path(model_resolver.__file__).resolve().parent / "execution.py").read_text(
        encoding="utf-8"
    )
    assert "resolution_abort_code" in source, (
        "execution.py tiene que propagar el abort_code del error, no fijar "
        "'model_unresolved' a mano — si no, `vault_unavailable` no sale nunca"
    )
    hardcoded = '("model_unresolved", prepared.resolution_error)'
    assert hardcoded not in source


def test_model_resolution_error_defaults_to_model_unresolved() -> None:
    assert ModelResolutionError("x").abort_code == "model_unresolved"
