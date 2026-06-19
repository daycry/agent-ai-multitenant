# Ola B — Built-ins completos (tools + skills por rol) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que todo agente de un equipo built-in salga con tools Y skills sensatas por rol (hoy el equipo CI4 tiene tools pero NO skills), reusando la maquinaria de seeds existente.

**Architecture:** Un mapa `rol → skill_slugs` (DRY) en un módulo de seeds nuevo; el roster CI4 deriva sus skills de ese mapa por el `role` de cada agente; una función `seed_ci4_agent_skills` (espejo de `seed_builtin_agent_skills`) cablea la junction `agent_skills`; un test de guardia asegura "ningún agente de equipo built-in sin tool/skill".

**Tech Stack:** Python 3.12, SQLAlchemy async, Postgres, pytest. Seeds en `apps/api-server/src/api_server/seeds/`.

## Global Constraints

- Tests con `.venv/Scripts/python.exe` (el `python` del PATH es Laragon sin deps).
- Catálogo de skills es agnóstico de lenguaje en este mapa (el catálogo trae sabor Python/Next; CI4 es PHP) → asignar SOLO skills aplicables a cualquier stack por rol.
- Slugs de skill DEBEN existir en `BUILTIN_SKILLS` (`builtin_skills.py`). Slugs válidos por categoría:
  - backend: `python-pytest, sqlalchemy-async, fastapi-routing, database-migrations, background-jobs, api-versioning`
  - frontend: `nextjs-app-router, tailwind-design, shadcn-components, tanstack-query, accessibility-aria, responsive-design`
  - devops: `dockerfile-optimization, docker-compose-orchestration, github-actions-ci, observability-otel, secrets-vault, infrastructure-as-code`
  - qa: `test-pyramid-design, playwright-e2e, property-based-testing, regression-test-strategy, edge-case-identification`
  - research: `technical-comparison, literature-review, cost-benefit-analysis, competitive-analysis, evidence-collection`
  - docs: `structured-writing, mermaid-diagrams, adr-authoring, runbook-authoring, api-documentation`
- Idempotencia: el wiring de skills usa upsert + borrado de stale (mismo patrón que `seed_builtin_agent_skills`).
- No tocar el catálogo cerrado de proveedores (ADR 0021). No inventar enums de rol nuevos.

---

## File Structure

- **Create** `apps/api-server/src/api_server/seeds/builtin_role_capabilities.py` — el mapa `ROLE_DEFAULT_SKILLS: dict[str, tuple[str, ...]]` (rol → slugs de skill agnósticas) + helper `default_skill_slugs(role)`.
- **Modify** `apps/api-server/src/api_server/seeds/ci4_team.py` — `CI4Agent.skill_slugs` (derivado del mapa por rol; override por agente posible) + `seed_ci4_agent_skills(session)`.
- **Modify** `apps/api-server/src/api_server/seeds/__main__.py` — invocar `seed_ci4_agent_skills` tras `seed_ci4_agents` + `seed_builtin_skills`.
- **Create** `tests/unit/test_builtin_role_capabilities.py` — el mapa cubre los roles CI4 + slugs válidos.
- **Create** `tests/integration/test_builtin_teams_capability_complete.py` — guardia: todo agente de equipo built-in tiene ≥1 tool y ≥1 skill.

---

## Task 1: Mapa `rol → skills` por defecto (DRY)

**Files:**

- Create: `apps/api-server/src/api_server/seeds/builtin_role_capabilities.py`
- Test: `tests/unit/test_builtin_role_capabilities.py`

**Interfaces:**

- Produces: `ROLE_DEFAULT_SKILLS: dict[str, tuple[str, ...]]` y `default_skill_slugs(role: str) -> tuple[str, ...]` (devuelve `()` si el rol no está en el mapa).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_builtin_role_capabilities.py
"""El mapa rol→skills por defecto cubre los roles de los equipos built-in y
solo referencia slugs reales del catálogo (Ola B)."""
from __future__ import annotations

import pytest
from api_server.seeds.builtin_role_capabilities import (
    ROLE_DEFAULT_SKILLS,
    default_skill_slugs,
)
from api_server.seeds.builtin_skills import BUILTIN_SKILLS

pytestmark = pytest.mark.unit

_CI4_ROLES = {
    "project_manager", "architect", "backend_dev", "frontend_dev",
    "security", "specialist", "qa", "reviewer", "devops",
}


def test_map_covers_every_ci4_role_with_at_least_one_skill() -> None:
    for role in _CI4_ROLES:
        assert default_skill_slugs(role), f"rol {role} sin skills por defecto"


def test_map_only_references_real_catalog_slugs() -> None:
    catalog = {s.slug for s in BUILTIN_SKILLS}
    for role, slugs in ROLE_DEFAULT_SKILLS.items():
        for slug in slugs:
            assert slug in catalog, f"{role} referencia slug inexistente: {slug}"


def test_unknown_role_returns_empty() -> None:
    assert default_skill_slugs("no-such-role") == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_builtin_role_capabilities.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'api_server.seeds.builtin_role_capabilities'`.

- [ ] **Step 3: Write minimal implementation**

```python
# apps/api-server/src/api_server/seeds/builtin_role_capabilities.py
"""Default capability map per agent ROLE for built-in teams (Ola B / ADR 0055).

Los equipos built-in deben salir "completos": cada agente con tools + skills
sensatas por su rol. Este módulo es la fuente DRY del conjunto de SKILLS por rol;
las tools por equipo siguen en el seed del equipo. Solo skills AGNÓSTICAS de
stack (el catálogo trae sabor Python/Next, pero estas aplican a cualquier
lenguaje, incl. el equipo PHP CodeIgniter 4).
"""
from __future__ import annotations

# rol (AgentRole value) -> slugs de skill del catálogo `builtin_skills.py`.
ROLE_DEFAULT_SKILLS: dict[str, tuple[str, ...]] = {
    "project_manager": ("cost-benefit-analysis", "structured-writing"),
    "architect": ("adr-authoring", "technical-comparison", "mermaid-diagrams"),
    "backend_dev": ("database-migrations", "api-versioning", "background-jobs"),
    "frontend_dev": ("responsive-design", "accessibility-aria"),
    "security": ("secrets-vault", "edge-case-identification"),
    "specialist": ("technical-comparison", "evidence-collection"),
    "qa": ("test-pyramid-design", "regression-test-strategy", "edge-case-identification"),
    "reviewer": ("regression-test-strategy", "edge-case-identification"),
    "devops": ("docker-compose-orchestration", "github-actions-ci", "observability-otel"),
}


def default_skill_slugs(role: str) -> tuple[str, ...]:
    """Skills por defecto de un rol (vacío si el rol no está mapeado)."""
    return ROLE_DEFAULT_SKILLS.get(role, ())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_builtin_role_capabilities.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add apps/api-server/src/api_server/seeds/builtin_role_capabilities.py tests/unit/test_builtin_role_capabilities.py
git commit -m "feat(seeds): mapa rol→skills por defecto para equipos built-in (Ola B)"
```

---

## Task 2: CI4 agents derivan skills del mapa + `seed_ci4_agent_skills`

**Files:**

- Modify: `apps/api-server/src/api_server/seeds/ci4_team.py`
- Modify: `apps/api-server/src/api_server/seeds/__main__.py`

**Interfaces:**

- Consumes: `default_skill_slugs(role)` (Task 1); `seed_builtin_agent_skills`-style upsert pattern.
- Produces: `seed_ci4_agent_skills(session: AsyncSession) -> int` (nº de links tocados).

- [ ] **Step 1: Add `skill_slugs` to the `CI4Agent` dataclass**

En `ci4_team.py`, en la dataclass `CI4Agent` (tras `tool_slugs`):

```python
    # Slugs de skills built-in (junction agent_skills). Si vacío, se derivan
    # del rol vía ROLE_DEFAULT_SKILLS (default_skill_slugs) — Ola B.
    skill_slugs: tuple[str, ...] = field(default_factory=tuple)

    def resolved_skill_slugs(self) -> tuple[str, ...]:
        """Skills explícitas si las hay; si no, las por defecto del rol."""
        from api_server.seeds.builtin_role_capabilities import default_skill_slugs

        return self.skill_slugs or default_skill_slugs(self.role)
```

- [ ] **Step 2: Add the `seed_ci4_agent_skills` function**

En `ci4_team.py`, junto a `seed_ci4_agent_tools` (reusa el id estable `_ci4_agent_id` y el id de skill del catálogo):

```python
from sqlalchemy import text  # (si no está ya importado en el módulo)
from api_server.seeds.builtin_skills import _skill_id as _builtin_skill_id

_UPSERT_CI4_AGENT_SKILL_SQL = text(
    """
    INSERT INTO agent_skills (agent_id, skill_id)
    VALUES (:agent_id, :skill_id)
    ON CONFLICT (agent_id, skill_id) DO UPDATE SET updated_at = now()
    """
)
_DELETE_STALE_CI4_AGENT_SKILLS_SQL = text(
    """
    DELETE FROM agent_skills
     WHERE agent_id = :agent_id
       AND skill_id <> ALL(:keep_ids)
    """
)


async def seed_ci4_agent_skills(session: AsyncSession) -> int:
    """Cablea cada agente CI4 a sus skills (por rol). Idempotente: upsert +
    poda de links fuera del spec. Debe correr DESPUÉS de seed_ci4_agents y
    seed_builtin_skills (FKs de agent_skills)."""
    links = 0
    for agent in CI4_AGENTS:
        keep_ids = [str(_builtin_skill_id(slug)) for slug in agent.resolved_skill_slugs()]
        for skill_id in keep_ids:
            await session.execute(
                _UPSERT_CI4_AGENT_SKILL_SQL,
                {"agent_id": str(agent.id), "skill_id": skill_id},
            )
            links += 1
        await session.execute(
            _DELETE_STALE_CI4_AGENT_SKILLS_SQL,
            {"agent_id": str(agent.id), "keep_ids": keep_ids},
        )
    return links
```

- [ ] **Step 3: Wire it in `__main__.py`**

En `seeds/__main__.py`: importar `seed_ci4_agent_skills` del módulo `ci4_team` y llamarla DESPUÉS de que existan `seed_ci4_agents` + `seed_builtin_skills` (junto a donde se llama `seed_builtin_agent_skills`):

```python
from api_server.seeds.ci4_team import (
    seed_ci4_agent_skills,  # nuevo
    seed_ci4_agent_tools,
    seed_ci4_agents,
    seed_ci4_project_template,
    seed_ci4_team,
)
...
        n_ci4_agent_skills = await seed_ci4_agent_skills(session)
```

(Añadir `ci4_agent_skills=n_ci4_agent_skills` al resumen/print si el seed reporta contadores, igual que `agent_skills`.)

- [ ] **Step 4: Run the seed unit/contract tests + the CI4 seed tests**

Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_builtin_role_capabilities.py -q` (sigue verde)
Run: `.venv/Scripts/python.exe -m pytest -k "ci4" -q`
Expected: PASS (sin regresiones en los tests CI4 existentes).

- [ ] **Step 5: Commit**

```bash
git add apps/api-server/src/api_server/seeds/ci4_team.py apps/api-server/src/api_server/seeds/__main__.py
git commit -m "feat(seeds): el equipo CI4 asigna skills por rol (Ola B)"
```

---

## Task 3: Guardia "ningún agente de equipo built-in sin tool/skill"

**Files:**

- Create: `tests/integration/test_builtin_teams_capability_complete.py`

**Interfaces:**

- Consumes: el seed completo (corre todos los seeders contra una BD migrada) — mismo harness que otros tests de integración de seeds.

- [ ] **Step 1: Write the test (guardia de regresión)**

```python
# tests/integration/test_builtin_teams_capability_complete.py
"""Guardia (Ola B): todo agente de un equipo built-in tiene ≥1 tool y ≥1 skill.
Evita el 'built-in vacío' (el equipo CI4 salía sin skills antes de la Ola B)."""
from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_every_builtin_team_agent_has_a_tool_and_a_skill(
    alembic_config, admin_database_url: str
) -> None:
    command.upgrade(alembic_config, "head")
    from api_server.seeds.__main__ import run_all_seeds  # entrypoint del seed

    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            await run_all_seeds(session)
        async with sm() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT a.id, a.name,"
                        " (SELECT count(*) FROM agent_tools t WHERE t.agent_id = a.id) tools,"
                        " (SELECT count(*) FROM agent_skills s WHERE s.agent_id = a.id) skills"
                        " FROM agents a"
                        " JOIN team_members tm ON tm.agent_id = a.id"
                        " JOIN teams te ON te.id = tm.team_id"
                        " WHERE te.is_builtin = true"
                    )
                )
            ).all()
        assert rows, "no hay agentes de equipos built-in tras el seed"
        empty = [(r.name, r.tools, r.skills) for r in rows if r.tools == 0 or r.skills == 0]
        assert not empty, f"agentes built-in sin tool/skill: {empty}"
    finally:
        await engine.dispose()
```

> NOTA al implementar: confirmar el nombre real del entrypoint del seed en
> `seeds/__main__.py` (p.ej. `run_all_seeds`/`seed_all`/`main`). Si no existe una
> función reusable, llamar a los seeders en el orden de `__main__` (organizations
> → builtin_tools → builtin_skills → builtin_agents → seed_builtin_agent_tools →
> seed_builtin_agent_skills → seed_ci4_team → seed_ci4_agents → seed_ci4_agent_tools
> → seed_ci4_agent_skills). El test es el contrato; ajusta el arranque a la API real.

- [ ] **Step 2: Run to verify it PASSES after Tasks 1-2**

Run: `.venv/Scripts/python.exe -m pytest tests/integration/test_builtin_teams_capability_complete.py -q`
Expected: PASS. (Si se ejecuta con Task 2 revertido, FALLA listando los agentes CI4 sin skill — confirma que la guardia detecta el "vacío".)

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_builtin_teams_capability_complete.py
git commit -m "test(seeds): guardia de 'built-in team agent no vacío' (tool+skill) (Ola B)"
```

---

## Self-Review (hecho)

- **Cobertura del spec (Parte B):** mapa rol→skills (Task 1) ✓; CI4 gana skills (Task 2) ✓; test "no vacío" (Task 3) ✓; override por agente (`skill_slugs` explícito en `CI4Agent`) ✓; sin cambio de datos ✓.
- **Placeholders:** ninguno salvo la NOTA explícita del entrypoint del seed en Task 3 (el implementador confirma el nombre real; el test es el contrato).
- **Consistencia de tipos:** `default_skill_slugs(role) -> tuple[str,...]` usado igual en Task 1 y 2; `seed_ci4_agent_skills(session) -> int` espeja `seed_builtin_agent_skills`.
- **Alcance:** Ola B independiente; produce software testeable por sí sola.
