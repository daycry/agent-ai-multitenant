"""QA E2E Automator — the Playwright-driven agent template (Plan 09 task_09_14).

Plan 09 Fase D names the *QA E2E Automator* as the flagship agent that drives
the featured Playwright tool (task_09_13) to author and run end-to-end browser
tests. This module seeds it as a GLOBAL platform agent template, reusing the
EXACT representation every other built-in agent already uses
(:class:`api_server.seeds.builtin_agents.BuiltinAgent` + the ``agents`` table:
``scope='global_builtin'``, ``is_template=true``, ``tenant_id`` = the platform
tenant, bilingual prompts in ``model_config.system_prompts``). It is NOT a new
template system — it is one more curated row under the same model.

Why a dedicated loader instead of an extra entry in ``BUILTIN_AGENTS``:

  * The QA E2E Automator is the *bridge* between the Plan 02/03 agent model and
    the Plan 09 marketplace: it must declare a reference to the Playwright
    **marketplace listing** (task_09_13), which is a ``marketplace_listings``
    row — NOT a ``tools`` table row — so it cannot be wired through the
    ``agent_tools`` M:N junction (that FK points at ``tools.id``). The
    reference therefore rides in ``model_config.marketplace_tools`` as the
    listing's stable identity (name + version + kind), exactly the trio the
    install flow keys on.
  * Keeping it out of ``BUILTIN_AGENTS`` preserves the day-one count the Plan
    01 seed test pins (``test_seed_agents`` asserts exactly eleven built-ins)
    while still landing the template under the same scope, schema and upsert.

Multi-tenancy note: the template is a GLOBAL platform-curated row
(``tenant_id`` = ``PLATFORM_TENANT_ID``, ``scope='global_builtin'``) — visible
to every tenant via the ``agents_global_builtin_read`` SELECT RLS policy, never
mutable by a tenant session. The Playwright listing it references is itself a
GLOBAL catalog listing (``tenant_id NULL``, Phase A hybrid model). A tenant
forks the template into its own ``global_tenant_template`` / ``project_local``
copy when it wants to customise it; the fork is then tenant-scoped by RLS.

Idempotent: re-running upserts the single slug-derived row (stable
``uuid5`` id) without duplicating it, mirroring ``seed_builtin_agents``.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api_server.marketplace.playwright import (
    PLAYWRIGHT_TOOL_NAME,
    PLAYWRIGHT_TOOL_VERSION,
)
from api_server.seeds import PLATFORM_TENANT_ID
from api_server.seeds.builtin_agents import _UPSERT_SQL, BuiltinAgent

# Stable identity of the QA E2E Automator template (same uuid5 namespace + slug
# convention as every other built-in agent, via ``BuiltinAgent.id``).
QA_E2E_AUTOMATOR_SLUG = "qa-e2e-automator"
QA_E2E_AUTOMATOR_NAME = "QA E2E Automator"
QA_E2E_AUTOMATOR_ROLE = "qa"

# The marketplace listing this template drives — the featured Playwright tool
# (task_09_13). It is a marketplace_listings row, so we reference it by its
# stable (name, version, kind) identity, NOT a tools.id FK.
PLAYWRIGHT_TOOL_REF: dict[str, str] = {
    "name": PLAYWRIGHT_TOOL_NAME,
    "version": PLAYWRIGHT_TOOL_VERSION,
    "kind": "tool",
    "source": "marketplace",
}


_SYSTEM_PROMPT_ES = (
    "Eres el QA E2E Automator, un agente especializado en automatización de "
    "pruebas end-to-end de navegador con Playwright. Tu herramienta principal "
    f"es la tool '{PLAYWRIGHT_TOOL_NAME}' del marketplace (verificada, global): "
    "la usas para escribir y EJECUTAR specs de Playwright (.spec.ts) contra la "
    "aplicación bajo prueba.\n\n"
    "Tu flujo para cada feature:\n"
    "  1. Identifica los flujos críticos de usuario (login, signup, checkout, "
    "navegación clave) y sus aserciones observables.\n"
    "  2. Escribe specs Playwright deterministas: selectores por rol/label "
    "accesible antes que CSS frágil, esperas por estado (no sleeps fijos), "
    "una aserción específica por intención.\n"
    "  3. Configura la tool de forma guiada — browsers (chromium/firefox/"
    "webkit), headless, screenshots y traces — eligiendo "
    "'only-on-failure'/'retain-on-failure' por defecto para triaje barato.\n"
    "  4. Ejecuta la suite vía la tool; cuando un test falla, reproduces "
    "minimalmente y reportas con pasos exactos y los artefactos (screenshot + "
    "trace) como evidencia.\n\n"
    "Tu sesgo es romper, no validar: una feature 'verde' que no has intentado "
    "romper no está terminada. NO modificas código de producción para que un "
    "test pase; si el test revela un bug, lo reportas. Respetas el aislamiento "
    "multi-tenant: solo pruebas los dominios consentidos del proyecto "
    "(allowed_domains) bajo la network_policy declarada por la tool."
)

_SYSTEM_PROMPT_EN = (
    "You are the QA E2E Automator, an agent specialised in end-to-end browser "
    "test automation with Playwright. Your primary tool is the marketplace "
    f"'{PLAYWRIGHT_TOOL_NAME}' tool (verified, global): you use it to author "
    "and RUN Playwright specs (.spec.ts) against the application under test.\n\n"
    "Your flow for each feature:\n"
    "  1. Identify the critical user flows (login, signup, checkout, key "
    "navigation) and their observable assertions.\n"
    "  2. Write deterministic Playwright specs: accessible role/label "
    "selectors over brittle CSS, state-based waits (never fixed sleeps), one "
    "specific assertion per intent.\n"
    "  3. Configure the tool through its guided config — browsers (chromium/"
    "firefox/webkit), headless, screenshots and traces — defaulting to "
    "'only-on-failure'/'retain-on-failure' for cheap triage.\n"
    "  4. Run the suite via the tool; when a test fails, reproduce it minimally "
    "and report with exact steps plus the artifacts (screenshot + trace) as "
    "evidence.\n\n"
    "Your bias is to break, not to validate: a 'green' feature you have not "
    "tried to break isn't done. You do NOT edit production code to make a test "
    "pass; if a test reveals a bug, you report it. You respect multi-tenant "
    "isolation: you only test the project's consented domains (allowed_domains) "
    "under the network_policy the tool declares."
)


# The QA E2E Automator built-in, expressed through the SAME ``BuiltinAgent``
# dataclass every other curated agent uses (so it serialises identically and
# goes through the shared agents upsert).
QA_E2E_AUTOMATOR = BuiltinAgent(
    slug=QA_E2E_AUTOMATOR_SLUG,
    name=QA_E2E_AUTOMATOR_NAME,
    description=(
        "Agente QA especializado que usa la tool Playwright del marketplace para "
        "escribir y ejecutar tests end-to-end de navegador con screenshots y traces."
    ),
    role=QA_E2E_AUTOMATOR_ROLE,
    memory_scope="team_shared",
    review_capability=True,
    max_concurrent_tasks=3,
    system_prompt_es=_SYSTEM_PROMPT_ES,
    system_prompt_en=_SYSTEM_PROMPT_EN,
    # Explícitas en vez de heredadas del rol `qa`, por una sola razón:
    # `playwright-e2e` no la reparte NADIE en todo el catálogo, y este es el
    # agente que se llama así. Un «QA E2E Automator» sin la skill de Playwright
    # es la versión en pequeño del mismo defecto que persigue todo este trabajo
    # — la pieza existe, está escrita, y no llega a quien la necesita.
    # Las otras cuatro son las del rol; se repiten aquí porque declarar
    # `skill_slugs` sustituye a la herencia, no la extiende.
    skill_slugs=(
        "playwright-e2e",
        "test-pyramid-design",
        "regression-test-strategy",
        "edge-case-identification",
        "contract-testing",
    ),
)


def qa_e2e_automator_model_config() -> dict[str, Any]:
    """The ``model_config`` payload — base config + the Playwright tool ref.

    Extends the standard built-in agent ``model_config`` (provider / model /
    temperature / bilingual ``system_prompts``) with a ``marketplace_tools``
    list declaring the Playwright listing this template drives. The reference
    is the listing's stable identity, so it survives re-seeds and points at the
    verified GLOBAL catalog row rather than any tenant copy.
    """
    config = QA_E2E_AUTOMATOR.to_model_config()
    config["marketplace_tools"] = [dict(PLAYWRIGHT_TOOL_REF)]
    return config


async def seed_qa_e2e_automator(session: AsyncSession) -> int:
    """Upsert the QA E2E Automator template. Returns the number of rows touched.

    Idempotent: the stable ``uuid5`` id makes a re-run a true upsert (refreshing
    the prompt / config in place) rather than a duplicate insert. Must run under
    a BYPASSRLS / platform session (writing a ``global_builtin`` platform row is
    reserved for the seed runner), exactly like :func:`seed_builtin_agents`.
    """
    await session.execute(
        _UPSERT_SQL,
        {
            "id": str(QA_E2E_AUTOMATOR.id),
            "tenant_id": str(PLATFORM_TENANT_ID),
            "name": QA_E2E_AUTOMATOR.name,
            "description": QA_E2E_AUTOMATOR.description,
            "role": QA_E2E_AUTOMATOR.role,
            # El EFECTIVO (persona + guía de ejecución generada): la columna
            # plana y `model_config.system_prompts` deben decir lo mismo.
            "system_prompt": QA_E2E_AUTOMATOR.effective_prompt_es,
            "model_config": json.dumps(qa_e2e_automator_model_config()),
            "memory_scope": QA_E2E_AUTOMATOR.memory_scope,
            "review_capability": QA_E2E_AUTOMATOR.review_capability,
            "max_concurrent_tasks": QA_E2E_AUTOMATOR.max_concurrent_tasks,
        },
    )
    return 1


async def seed_qa_e2e_automator_tools(session: AsyncSession) -> int:
    """Cablea las tools del QA E2E Automator (`agent_tools`). Devuelve enlaces.

    Existe como paso propio porque este agente vive FUERA de ``BUILTIN_AGENTS``
    —para no mover el conteo de once que fija ``test_seed_agents``— y por eso se
    quedó con CERO tools desde el día que se sembró, mientras su prompt le ordena
    «escribe specs Playwright deterministas» y «reproduces minimalmente». Un
    agente que no puede escribir un fichero no produce entregable reconocible.

    Debe correr DESPUÉS de :func:`seed_qa_e2e_automator` (FK
    ``agent_tools.agent_id``) y de ``seed_builtin_tools`` (FK
    ``agent_tools.tool_id``). Reusa el upsert + la poda de
    :mod:`api_server.seeds.builtin_agents`: mismo contrato idempotente que el
    resto del catálogo, no una segunda forma de escribir la misma junction.
    """
    from api_server.seeds.builtin_agents import (
        _DELETE_STALE_AGENT_TOOLS_SQL,
        _UPSERT_AGENT_TOOL_SQL,
    )
    from api_server.seeds.builtin_tools import _tool_id

    keep_ids = [str(_tool_id(slug)) for slug in QA_E2E_AUTOMATOR.resolved_tool_slugs()]
    for tool_id in keep_ids:
        await session.execute(
            _UPSERT_AGENT_TOOL_SQL,
            {"agent_id": str(QA_E2E_AUTOMATOR.id), "tool_id": tool_id},
        )
    await session.execute(
        _DELETE_STALE_AGENT_TOOLS_SQL,
        {"agent_id": str(QA_E2E_AUTOMATOR.id), "keep_ids": keep_ids},
    )
    return len(keep_ids)


async def seed_qa_e2e_automator_skills(session: AsyncSession) -> int:
    """Cablea las skills del QA E2E Automator (`agent_skills`). Devuelve enlaces.

    La mitad gemela de :func:`seed_qa_e2e_automator_tools`, y llega después por
    el mismo motivo por el que aquélla llegó tarde: este agente vive FUERA de
    ``BUILTIN_AGENTS`` —para no mover el conteo de once que fija
    ``test_seed_agents``— así que ``seed_builtin_agent_skills`` no lo alcanza y
    nadie más lo hacía. Resultado medido el 2026-08-30: ``resolved_skill_slugs``
    devolvía cuatro skills desde siempre y en la base había CERO, porque no
    existía el paso que las escribiera.

    Es el modo de fallo más silencioso de este catálogo: la definición es
    correcta, se puede leer, se puede testear en unidad… y no llega nunca a la
    tabla. Cerrar un roster a medias —tools sí, skills no— deja exactamente este
    hueco, y por eso el refresco de arranque deriva su lista de pasos del propio
    seed en vez de llevarla escrita a mano.

    Debe correr DESPUÉS de :func:`seed_qa_e2e_automator` (FK
    ``agent_skills.agent_id``) y de ``seed_builtin_skills`` (FK
    ``agent_skills.skill_id``). Reusa el upsert + la poda de
    :mod:`api_server.seeds.builtin_agents`: mismo contrato idempotente que el
    resto del catálogo.
    """
    from api_server.seeds.builtin_agents import (
        _DELETE_STALE_AGENT_SKILLS_SQL,
        _UPSERT_AGENT_SKILL_SQL,
    )
    from api_server.seeds.builtin_skills import _skill_id

    keep_ids = [str(_skill_id(slug)) for slug in QA_E2E_AUTOMATOR.resolved_skill_slugs()]
    for skill_id in keep_ids:
        await session.execute(
            _UPSERT_AGENT_SKILL_SQL,
            {"agent_id": str(QA_E2E_AUTOMATOR.id), "skill_id": skill_id},
        )
    await session.execute(
        _DELETE_STALE_AGENT_SKILLS_SQL,
        {"agent_id": str(QA_E2E_AUTOMATOR.id), "keep_ids": keep_ids},
    )
    return len(keep_ids)


__all__ = [
    "PLAYWRIGHT_TOOL_REF",
    "QA_E2E_AUTOMATOR",
    "QA_E2E_AUTOMATOR_NAME",
    "QA_E2E_AUTOMATOR_ROLE",
    "QA_E2E_AUTOMATOR_SLUG",
    "qa_e2e_automator_model_config",
    "seed_qa_e2e_automator",
    "seed_qa_e2e_automator_tools",
]
