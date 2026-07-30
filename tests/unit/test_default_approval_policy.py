"""Unit — default approval policy for a project without an explicit one (A8b).

Un proyecto creado SIN ``human_approval_policy`` corría TODAS las categorías
sensibles en auto (el gate ni se instanciaba). Ahora hereda un preset por defecto
(``development``, configurable vía platform setting): las tools del bucle de coding
siguen en auto, pero comunicación / http_post / secrets / deploy / infra / PII /
user_mgmt quedan gateadas. Nunca fail-open a todo-auto.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


def test_preset_decisions_development_gates_the_dangerous_categories() -> None:
    from api_server.seeds.builtin_approval_policies import preset_decisions

    dev = preset_decisions("development")
    # El bucle de coding sigue en auto (no rompe la convergencia).
    assert dev["code_changes"] == "auto"
    assert dev["external_http_get"] == "auto"
    # Lo peligroso queda gateado.
    assert dev["external_communication"] == "human_required"
    assert dev["external_http_post"] == "human_required"


def test_preset_decisions_unknown_slug_falls_back_not_fail_open() -> None:
    """Un slug desconocido cae al preset seguro por defecto, NUNCA a todo-auto."""
    from api_server.seeds.builtin_approval_policies import (
        DEFAULT_APPROVAL_POLICY_PRESET,
        preset_decisions,
    )

    assert preset_decisions("does-not-exist") == preset_decisions(DEFAULT_APPROVAL_POLICY_PRESET)
    # Y ese default NO es todo-auto.
    assert "human_required" in preset_decisions("does-not-exist").values()


@pytest.mark.asyncio
async def test_resolver_uses_project_policy_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers.execution import _resolve_effective_approval_policy

    explicit = {"external_communication": "auto"}
    project = SimpleNamespace(human_approval_policy=explicit)

    result = await _resolve_effective_approval_policy(object(), project)

    assert result == explicit  # la política explícita del proyecto gana


@pytest.mark.asyncio
async def test_resolver_applies_default_preset_when_policy_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin política explícita → preset por defecto (no None → no fail-open)."""
    import api_server.db.platform_settings as ps
    from workers.execution import _resolve_effective_approval_policy

    async def _fake_get(_session, _key, *, default=None):
        return "development"

    monkeypatch.setattr(ps, "get_platform_setting", _fake_get)

    project = SimpleNamespace(human_approval_policy=None)
    result = await _resolve_effective_approval_policy(object(), project)

    assert result is not None
    assert result["code_changes"] == "auto"
    assert result["external_communication"] == "human_required"


@pytest.mark.asyncio
async def test_resolver_defaults_when_project_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_server.db.platform_settings as ps
    from workers.execution import _resolve_effective_approval_policy

    async def _fake_get(_session, _key, *, default=None):
        return default  # simula setting ausente → usa el default pasado

    monkeypatch.setattr(ps, "get_platform_setting", _fake_get)

    result = await _resolve_effective_approval_policy(object(), None)

    assert result is not None
    assert "human_required" in result.values()
