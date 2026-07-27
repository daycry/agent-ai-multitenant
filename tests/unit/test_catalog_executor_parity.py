"""Paridad catálogo↔executor: ningún builtin sin ejecutor es ASIGNABLE.

T4 del plan `tools-y-cierre-plan-fixes` (hallazgo **g4** de la auditoría de
plataforma 2026-07-03), cerrando el invariante que ADR 0049 abrió cuando retiró
la familia ``git_*`` del catálogo por no tener ejecutor.

El defecto original no era que el catálogo listara nombres sin ejecutor —eso lo
señaliza honestamente ``is_runtime_wired``— sino que **se podían asignar**: los
seeds de rol y del equipo CI4 los escribían directos en ``agent_tools``
esquivando el 422 del PUT, y el agente los recibía en su prompt, los invocaba y
morían como ``unknown tool`` (run 019f27ff, ``search_code``, idx 36).

T5 limpió los seeds. Este fichero es el **candado**: cruza TODAS las vías que
pueden acabar en una fila de ``agent_tools`` y falla si alguna vuelve a colar un
builtin de plataforma sin ejecutor. Las vías son cuatro y aquí se cubren las
tres declarativas:

  1. ``ROLE_DEFAULT_TOOLS`` — los defaults por rol.
  2. ``BUILTIN_AGENTS[*].resolved_tool_slugs()`` — lo que **de verdad** escribe
     ``seed_builtin_agent_tools``. No es lo mismo que (1): un agente puede
     declarar ``tool_slugs=`` y saltarse el default del rol, así que pinear solo
     el diccionario deja la puerta abierta al override.
  3. ``CI4_AGENTS[*].tool_slugs`` — el equipo built-in CI4.

La cuarta —el ``PUT /agents/{id}/tools``— es de runtime y se cubre por
comportamiento en ``tests/integration/test_agent_tools_assignment.py``
(``test_cannot_assign_unwired_builtin``). Aquí solo se afirma que el predicado
que consulta esa guarda (``tool_is_runtime_wired``) sigue diciendo la verdad
sobre los builtins sin ejecutor.

Nota sobre el fork (``_clone_agent_tools``): copia las filas del agente origen,
así que no puede *introducir* un nombre que las vías de arriba no hayan dejado
entrar antes. No añade superficie nueva.
"""

from __future__ import annotations

import pytest
from api_server.schemas.catalog import tool_is_runtime_wired
from api_server.seeds.builtin_agents import BUILTIN_AGENTS
from api_server.seeds.builtin_role_capabilities import ROLE_DEFAULT_TOOLS
from api_server.seeds.builtin_tools import BUILTIN_TOOLS
from api_server.seeds.ci4_team import CI4_AGENTS
from shared_domain.tool_names import (
    CANONICAL_TOOL_NAMES,
    RUNTIME_WIRED_TOOL_NAMES,
    is_runtime_wired,
)

pytestmark = pytest.mark.unit


def _canonical(slug: str) -> str:
    """Los seeds usan slugs kebab (``read-file``); el catálogo canónico snake."""
    return slug.replace("-", "_")


def _unwired_catalog_builtins() -> frozenset[str]:
    """Builtins del catálogo asignable cuyo nombre no tiene ejecutor.

    Se deriva de la semilla real (``BUILTIN_TOOLS``) y no de una lista escrita a
    mano, para que añadir una fila nueva sin ejecutor entre por aquí sola en vez
    de quedarse fuera del candado.
    """
    return frozenset(
        tool.name
        for tool in BUILTIN_TOOLS
        if tool.implementation_type == "builtin" and not is_runtime_wired(tool.name)
    )


# ---------------------------------------------------------------------------
# (1) Los defaults por rol.
# ---------------------------------------------------------------------------
def test_role_defaults_assign_no_unwired_builtin() -> None:
    offenders = {
        role: sorted(t for t in tools if not is_runtime_wired(_canonical(t)))
        for role, tools in ROLE_DEFAULT_TOOLS.items()
    }
    offenders = {role: dead for role, dead in offenders.items() if dead}
    assert not offenders, f"ROLE_DEFAULT_TOOLS asigna tools sin ejecutor: {offenders}"


# ---------------------------------------------------------------------------
# (2) Lo que escribe el seed de verdad — incluidos los overrides `tool_slugs=`.
# ---------------------------------------------------------------------------
def test_builtin_agents_resolved_tools_are_all_wired() -> None:
    """``seed_builtin_agent_tools`` itera ``resolved_tool_slugs()``, no el dict.

    Un agente con ``tool_slugs=("search-code",)`` pasaría el test del diccionario
    y aun así sembraría una tool muerta. Este es el punto que se cruza con la
    escritura real.
    """
    offenders = {
        agent.slug: sorted(
            t for t in agent.resolved_tool_slugs() if not is_runtime_wired(_canonical(t))
        )
        for agent in BUILTIN_AGENTS
    }
    offenders = {slug: dead for slug, dead in offenders.items() if dead}
    assert not offenders, f"agentes built-in con tools sin ejecutor: {offenders}"


# ---------------------------------------------------------------------------
# (3) El equipo CI4.
# ---------------------------------------------------------------------------
def test_ci4_team_assigns_no_unwired_builtin() -> None:
    offenders = {
        agent.slug: sorted(t for t in agent.tool_slugs if not is_runtime_wired(_canonical(t)))
        for agent in CI4_AGENTS
    }
    offenders = {slug: dead for slug, dead in offenders.items() if dead}
    assert not offenders, f"agentes CI4 con tools sin ejecutor: {offenders}"


# ---------------------------------------------------------------------------
# (4) El predicado que consulta la guarda del PUT no miente.
# ---------------------------------------------------------------------------
def test_unwired_catalog_builtins_are_reported_unwired_by_the_put_guard() -> None:
    """``tool_is_runtime_wired`` es lo que el PUT usa para devolver el 422.

    Si alguna vez devolviera ``True`` para un builtin sin ejecutor —p.ej. por el
    atajo del ``implementation_type``, que es exactamente cómo ``send_notification``
    se coló— la guarda dejaría de rechazar y el candado de arriba no se enteraría,
    porque los seeds seguirían limpios.
    """
    for name in sorted(_unwired_catalog_builtins()):
        assert tool_is_runtime_wired(name, "builtin") is False, name


def test_the_unwired_set_is_not_silently_empty() -> None:
    """Guarda del propio candado.

    Si un refactor vaciara ``_unwired_catalog_builtins()`` (p.ej. cambiando el
    nombre de ``implementation_type``), los cuatro tests de arriba pasarían sin
    ejercer nada. Hoy el catálogo sí ofrece builtins sin ejecutor —los ofrece
    marcados como no ejecutables, que es lo honesto— así que el conjunto debe ser
    no vacío y quedar registrado por nombre.
    """
    unwired = _unwired_catalog_builtins()
    assert unwired, (
        "ningún builtin del catálogo aparece sin ejecutor: o se cablearon todos "
        "(actualiza este test) o la derivación se rompió y el candado no ejerce nada"
    )
    # Los tres que g4 nombró siguen siendo el caso conocido; si se cablean o se
    # retiran del catálogo, este assert avisa de que el invariante cambió.
    assert unwired >= {"apply_patch", "search_code", "summarize_text"}, sorted(unwired)


def test_unwired_builtins_are_still_canonical_names() -> None:
    """No ejecutable ≠ desconocido: siguen siendo nombres del catálogo canónico.

    Es la diferencia entre «el operador lo ve marcado como no ejecutable» y «el
    nombre no existe»; el diagnóstico de tools depende de la primera.
    """
    for name in sorted(_unwired_catalog_builtins()):
        assert name in CANONICAL_TOOL_NAMES, name
        assert name not in RUNTIME_WIRED_TOOL_NAMES, name
