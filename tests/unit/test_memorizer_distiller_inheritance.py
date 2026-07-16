"""AUD16-14 (auditoría 2026-07-16): el destilador usa el modelo REAL del agente.

El camino primario de F2.1 leía ``agent.model_config`` crudo, pero un agente
con modelo HEREDADO (plataforma→proyecto→equipo→agente, ADR 0065/0082) tiene
``model_config`` sin provider_id/model — la herencia se resuelve en el
dispatch, no se materializa en la fila. ``_build_agent_llm`` devolvía ``None``
en silencio y el 100% de las memorias vivas acabaron destiladas por el
fallback ``llama3.2:1b`` (~21% ruido). Ahora resuelve la misma cadena que el
dispatch antes de rendirse, y el fallback se loguea con motivo.
"""

from __future__ import annotations

import contextlib
from typing import Any
from uuid import uuid4

import pytest
from workers import memorizer

pytestmark = pytest.mark.unit

_PID = uuid4()


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


def _sessionmaker() -> Any:
    return lambda: _FakeSession()


@pytest.fixture()
def _fake_factory(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Intercepta build_llm_provider + el vault store (imports internos)."""
    calls: dict[str, Any] = {}
    built = object()
    calls["built"] = built

    async def fake_build(session: Any, *, provider_id: Any, model: str, vault: Any) -> Any:
        calls["provider_id"] = provider_id
        calls["model"] = model
        return built

    import workers.execution as wex
    from api_server.llm_providers import factory

    monkeypatch.setattr(factory, "build_llm_provider", fake_build)
    with contextlib.suppress(AttributeError):
        monkeypatch.setattr(wex, "_default_vault_store", lambda: None)
    return calls


@pytest.mark.asyncio
async def test_inherited_model_config_is_resolved_via_chain(
    monkeypatch: pytest.MonkeyPatch, _fake_factory: dict[str, Any]
) -> None:
    agent = {"id": uuid4(), "model_config": {}}  # modelo heredado: fila vacía
    project = {"id": uuid4(), "team_id": None}

    async def fake_chain(session: Any, agent_cfg: Any, project_ctx: Any) -> dict[str, Any]:
        assert project_ctx == project
        return {"provider": "ollama", "provider_id": str(_PID), "model": "gpt-oss:120b"}

    monkeypatch.setattr(memorizer, "_resolve_inherited_model_config", fake_chain)

    got = await memorizer._build_agent_llm(_sessionmaker(), agent, project=project)

    assert got is not None
    provider, model = got
    assert provider is _fake_factory["built"]
    assert model == "gpt-oss:120b"
    assert str(_fake_factory["provider_id"]) == str(_PID)


@pytest.mark.asyncio
async def test_unresolvable_chain_falls_back_to_none_with_reason(
    monkeypatch: pytest.MonkeyPatch, _fake_factory: dict[str, Any]
) -> None:
    agent = {"id": uuid4(), "model_config": {}}

    async def fake_chain(session: Any, agent_cfg: Any, project_ctx: Any) -> None:
        return None

    monkeypatch.setattr(memorizer, "_resolve_inherited_model_config", fake_chain)
    logged: list[dict[str, Any]] = []
    monkeypatch.setattr(
        memorizer,
        "_log",
        type(
            "L",
            (),
            {
                "info": lambda self, e, **kw: logged.append({"event": e, **kw}),
                "warning": lambda self, e, **kw: logged.append({"event": e, **kw}),
            },
        )(),
    )

    got = await memorizer._build_agent_llm(_sessionmaker(), agent, project=None)

    assert got is None
    assert any(e["event"] == "memorizer.distill_fallback" for e in logged)


@pytest.mark.asyncio
async def test_pinned_provider_id_keeps_working_without_chain(
    monkeypatch: pytest.MonkeyPatch, _fake_factory: dict[str, Any]
) -> None:
    agent = {"id": uuid4(), "model_config": {"provider_id": str(_PID), "model": "opus"}}

    async def boom(session: Any, agent_cfg: Any, project_ctx: Any) -> None:
        raise AssertionError("la cadena no debe consultarse con un spec ya pineado")

    monkeypatch.setattr(memorizer, "_resolve_inherited_model_config", boom)

    got = await memorizer._build_agent_llm(_sessionmaker(), agent, project=None)

    assert got is not None
    assert got[1] == "opus"
