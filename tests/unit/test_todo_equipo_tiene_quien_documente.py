"""Ningún equipo built-in se queda sin nadie que pueda documentar.

## El defecto, y cómo se vio

`CLAUDE.md` (principio 8) exige documentación en `/docs/` con estructura
canónica de 7 carpetas. Y es una regla del PRODUCTO, no una convención de este
repo: `api_server/docs_structure/constants.py` abre citándola, y la estructura
se aplica a los proyectos GESTIONADOS (`docs_viewer/`, `tech_writer/`).

Qué hace el agente y qué hace el código, porque confundirlo lleva a justificar
mal: el changelog y los ADR los genera código DETERMINISTA
(`tech_writer/generation.py`, `changelog.py`) — «the file structure is generated
by code». Lo que aporta el agente es la **redacción** («the wording may come from
the agent») y `docs/04-reference/`, que ese mismo módulo deja como no-op
explícito porque no puede derivarlo sin una fuente estructurada de API/esquema.

Más lo que ningún generador cubre y los proyectos necesitan igual: el README y
la referencia de endpoints.

Pero estaba en **UNO de los seis** equipos built-in (`research-spec`). Y eso no
es una carencia estética, porque el pool de candidatos del dispatch es cerrado:

```python
if team_id is not None:
    member_ids = select(TeamMember.agent_id).where(TeamMember.team_id == team_id)
    pool_filter = or_(Agent.id.in_(member_ids), project_local)
```

(`orchestrator/dispatch.py`, `_candidates`). Un built-in **global** que no esté
en el equipo NO entra nunca. Así que en cinco de seis equipos, una tarea de rol
`technical_writer` resolvía a `NULL` y la documentación no tenía dueño.

## Cómo se descubrió, que es la parte que conviene recordar

No mirando el código: mirando **lo que los agentes hicieron**. En el histórico de
ejecuciones, el PM del equipo CodeIgniter había llamado a `write_file` nueve
veces — siete sobre código y configuración que su prompt le prohíbe tocar
(`app/Config/Paths.php`, `vendor/autoload.php`, `tests/Feature/HelloTest.php`,
`composer.json`, `.env`) y **dos sobre documentación** (`README.md`,
`docs/api/hello_endpoint.md`).

Esas dos son la huella del hueco: el PM documentaba porque no había nadie más.
Retirarle la escritura sin poner un writer habría dejado los proyectos sin
documentación en vez de con documentación escrita por quien no debía.
"""

from __future__ import annotations

import pytest
from api_server.seeds.builtin_agents import BUILTIN_AGENTS
from api_server.seeds.builtin_teams import BUILTIN_TEAMS
from api_server.seeds.ci4_team import CI4_AGENTS, CI4_TEAM

pytestmark = pytest.mark.unit

_TODOS_LOS_EQUIPOS = (*BUILTIN_TEAMS, CI4_TEAM)
_ROL_POR_SLUG = {a.slug: a.role for a in (*BUILTIN_AGENTS, *CI4_AGENTS)}


def _roles_del_equipo(equipo: object) -> set[str]:
    miembros = getattr(equipo, "members", ())
    return {_ROL_POR_SLUG[m.agent_slug] for m in miembros if m.agent_slug in _ROL_POR_SLUG}


def test_los_equipos_conocidos_se_resuelven() -> None:
    """Si los slugs dejaran de casar, todo lo de abajo pasaría en vacío."""
    for equipo in _TODOS_LOS_EQUIPOS:
        roles = _roles_del_equipo(equipo)
        assert roles, (
            f"el equipo {getattr(equipo, 'slug', '?')!r} no resuelve NINGUNO de sus "
            "miembros a un rol: cambió la forma de declararlos y esta guarda dejó de mirar"
        )


@pytest.mark.parametrize("equipo", _TODOS_LOS_EQUIPOS, ids=lambda t: str(getattr(t, "slug", "?")))
def test_cada_equipo_tiene_technical_writer(equipo: object) -> None:
    assert "technical_writer" in _roles_del_equipo(equipo), (
        f"el equipo {getattr(equipo, 'slug', '?')!r} no tiene ningún agente de rol "
        "`technical_writer`. El pool de candidatos del dispatch son los MIEMBROS del "
        "equipo más los agentes `project_local`, así que el Technical Writer global no "
        "le llega: una tarea de documentación resolvería a NULL, y en la práctica la "
        "acaba escribiendo el PM, que tiene prohibido escribir."
    )


@pytest.mark.parametrize("equipo", _TODOS_LOS_EQUIPOS, ids=lambda t: str(getattr(t, "slug", "?")))
def test_quien_documenta_puede_escribir(equipo: object) -> None:
    """Tener el rol no basta: hace falta que pueda producir el fichero.

    La otra mitad del mismo hueco. Un `technical_writer` en el equipo sin
    `write-file` es la misma trampa por la puerta de al lado — el rol resuelve,
    la tarea se asigna, y el agente no puede entregar.
    """
    por_slug = {a.slug: a for a in (*BUILTIN_AGENTS, *CI4_AGENTS)}
    escritores = [
        por_slug[m.agent_slug]
        for m in getattr(equipo, "members", ())
        if por_slug.get(m.agent_slug) is not None
        and _ROL_POR_SLUG.get(m.agent_slug) == "technical_writer"
    ]
    assert escritores, "cubierto por el test de arriba"
    for agente in escritores:
        tools = set(agente.resolved_tool_slugs())
        assert "write-file" in tools, (
            f"{agente.slug!r} documenta para el equipo "
            f"{getattr(equipo, 'slug', '?')!r} y no tiene `write-file`: la tarea se le "
            "asignaría y no podría entregar nada"
        )


def test_el_writer_lleva_la_skill_del_changelog() -> None:
    """La skill que su ROL concede no puede perderse por declarar `skill_slugs`.

    Ojo con el motivo, porque el primero que se escribió aquí estaba MAL y lo
    corrigió el operador: no es que el agente redacte el changelog. No lo
    redacta. `api_server/tech_writer/changelog.py` lo genera con un renderer
    DETERMINISTA y su docstring es explícito — «calls `render_changelog` rather
    than asking an LLM to free-form the file, so the structure is guaranteed and
    the output is reproducible».

    Lo que este test fija es otra cosa, y se sostiene sola: `changelog-authoring`
    está en `ROLE_DEFAULT_SKILLS["technical_writer"]` porque alguien lo decidió,
    y declarar `skill_slugs` en un agente SUSTITUYE la herencia del rol en vez de
    extenderla. Así fue como el Technical Writer se quedó sin ella sin que nada
    lo dijera. El defecto es la mecánica del override; la conveniencia de la
    skill es una decisión previa que este fichero no revisa.

    Dónde SÍ aporta el agente, para no volver a exagerarlo: la REDACCIÓN (el
    propio `generation.py` dice «the wording may come from the agent, but the
    file structure is generated by code») y `docs/04-reference/`, que ese módulo
    deja explícitamente como no-op porque no puede derivarlo sin una fuente
    estructurada de API/esquema.
    """
    por_slug = {a.slug: a for a in (*BUILTIN_AGENTS, *CI4_AGENTS)}
    sin_skill = sorted(
        slug
        for slug, agente in por_slug.items()
        if _ROL_POR_SLUG.get(slug) == "technical_writer"
        and "changelog-authoring" not in agente.resolved_skill_slugs()
    )
    assert not sin_skill, (
        f"agentes de rol `technical_writer` sin `changelog-authoring`: {sin_skill}. "
        "Es el entregable que CLAUDE.md exige para cerrar un plan."
    )
