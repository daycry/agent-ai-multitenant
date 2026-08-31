"""Red de seguridad de arranque para el catálogo builtin (G-02).

El seed completo (`python -m api_server.seeds`) es CLI-manual; tras un reset/
wipe del tenant demo, `knowledge_bases` quedó a 0 y los `default_kb_grants` de
las plantillas apuntaban a KBs inexistentes → auto-RAG estéril. Esta función
GARANTIZA al arranque del api-server las FILAS estructurales del catálogo de
conocimiento (tenant plataforma + categorías + KBs builtin) — rápido, sin
embeddings ni docling, idempotente y bajo un advisory lock para que varias
réplicas no compitan.

La ingesta del CORPUS (embeddings vía Ollama, `seed_catalog_ingestion`) sigue
siendo responsabilidad del CLI/instalador: es lenta y depende de servicios
externos, no cabe en el hot-path de un arranque. Si el catálogo estaba
ausente, se registra un WARNING para que el operador corra el seed completo.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_log = structlog.get_logger(__name__)

# Clave arbitraria pero estable del advisory lock (solo un runner siembra a la
# vez; el resto ve el catálogo ya presente y sale idempotente).
_CATALOG_LOCK_KEY = 0x4B42_5345_4544  # "KBSEED"


async def _builtin_kb_count(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                text("SELECT count(*) FROM knowledge_bases WHERE is_builtin = true")
            )
        ).scalar_one()
    )


async def ensure_builtin_catalog(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    """Garantiza el catálogo builtin de conocimiento; devuelve
    ``{"seeded": bool, "builtin_kbs": int}``.

    ``seeded`` es ``True`` solo si estaba ausente y esta llamada lo sembró
    (dispara el WARNING para re-ingestar el corpus). Best-effort: cualquier
    fallo se loguea y devuelve el conteo observado — un problema de siembra no
    debe impedir arrancar la app.
    """
    from api_server.seeds.builtin_kb_categories import seed_builtin_kb_categories
    from api_server.seeds.builtin_kbs import seed_builtin_kbs
    from api_server.seeds.platform import ensure_platform_tenant

    try:
        async with sessionmaker() as session, session.begin():
            # Solo un runner siembra; los demás esperan y ven el catálogo listo.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CATALOG_LOCK_KEY}
            )
            existing = await _builtin_kb_count(session)
            if existing > 0:
                return {"seeded": False, "builtin_kbs": existing}
            await ensure_platform_tenant(session)
            await seed_builtin_kb_categories(session)
            seeded_kbs = await seed_builtin_kbs(session)
        _log.warning(
            "startup.builtin_catalog_seeded",
            builtin_kbs=seeded_kbs,
            note="KB rows re-created; run `python -m api_server.seeds` to ingest the corpus",
        )
        return {"seeded": True, "builtin_kbs": seeded_kbs}
    except Exception:
        _log.warning("startup.builtin_catalog_failed", exc_info=True)
        try:
            async with sessionmaker() as session:
                return {"seeded": False, "builtin_kbs": await _builtin_kb_count(session)}
        except Exception:
            return {"seeded": False, "builtin_kbs": 0}


async def refresh_builtin_agent_capabilities(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    """Re-aplica en cada arranque las tools y skills de los agentes BUILT-IN.

    Por qué esto existe, y por qué no basta la red de arriba: aquélla siembra
    **cuando falta**, y el defecto que cierra ésta es otro — el catálogo estaba
    presente y RANCIO. Medido el 2026-08-30: el código repartía `stack-exec` a
    seis roles desde julio y la base de datos llevaba desde el **2026-06-28** sin
    tocarse, porque el seed completo es un CLI manual que nada dispara.

    Lo que costó: un run real pidió `stack_exec` a la primera, recibió «tool not
    allowed in this mode», y se pasó 24 llamadas buscando `php` dentro de un
    sandbox de Python hasta agotar reintentos — 2,22 USD y 62,2k tokens sin
    instalar nada. Y antes de eso, alguien ya había parcheado a mano las copias
    de dos tenants (junio y julio) sin dejar nada que lo impidiera repetirse.

    Se re-aplica SIEMPRE, no sólo cuando falta, y eso es deliberado: las tools de
    un agente built-in son de la PLATAFORMA. Quien quiera personalizarlas adopta
    una copia —para eso existe la adopción— y esa copia no se toca aquí. Volver a
    afirmar la intención del catálogo en cada arranque es exactamente lo que
    «built-in» significa. Las cinco funciones son upserts idempotentes que además
    PODAN los links fuera del spec, así que retirar una tool en el código también
    la retira de la base — que es lo que hace que un arreglo aterrice y no sólo
    quede escrito.

    **Los once pasos, y por qué son once.** Hay tres rosters —`BUILTIN_AGENTS`,
    el equipo CodeIgniter 4 y el QA E2E Automator, que vive fuera de ambas
    tuplas— y de cada uno hay que re-afirmar TRES cosas: que el agente existe,
    que tiene sus tools y skills, y que pertenece a su equipo.

    Las dos primeras versiones de esta función se quedaron cortas por el mismo
    sitio, y conviene que conste porque el error se repite:

    * la primera llamaba sólo a los dos pasos de `BUILTIN_AGENTS` y dejaba fuera
      a los diez agentes CI4, que son el roster del incidente — habría parecido
      que arreglaba el problema mientras el equipo que lo sufrió seguía igual;
    * la segunda re-aplicaba CAPACIDADES sobre agentes que daba por presentes.
      Al añadir `ci4-tech-writer` al roster, el arranque falló contra la FK de
      `agent_tools.agent_id` y el equipo se quedó a diez miembros. «Las tools de
      un agente built-in son de la plataforma» no se sostiene si el agente en sí
      sólo llega corriendo el CLI a mano.

    **Una transacción por paso**, y no una para los once: si un roster no está
    sembrado todavía, su upsert revienta contra la FK de `agent_tools`, y con una
    transacción común ese fallo arrastraría también a los pasos que sí habían
    ido bien. Con el reparto, lo que se puede afirmar se afirma.

    Best-effort en su conjunto: un fallo se loguea y NO impide arrancar — un
    catálogo desactualizado deja agentes peor equipados; no arrancar deja la
    plataforma entera fuera.
    """
    from api_server.seeds.builtin_agents import (
        seed_builtin_agent_skills,
        seed_builtin_agent_tools,
        seed_builtin_agents,
    )
    from api_server.seeds.builtin_teams import seed_builtin_teams
    from api_server.seeds.ci4_team import (
        seed_ci4_agent_skills,
        seed_ci4_agent_tools,
        seed_ci4_agents,
        seed_ci4_team,
    )
    from api_server.seeds.qa_e2e_automator import (
        seed_qa_e2e_automator,
        seed_qa_e2e_automator_skills,
        seed_qa_e2e_automator_tools,
    )

    # EL ORDEN ES LA FK, no una preferencia. Los agentes primero (upsert por
    # uuid5 estable), luego sus capacidades (`agent_tools.agent_id`,
    # `agent_skills.agent_id`), y al final la pertenencia al equipo
    # (`team_members.agent_id`). Invertirlo revienta contra la clave ajena, que
    # es EXACTAMENTE lo que pasó el 2026-08-30 al añadir `ci4-tech-writer`: sin
    # los pasos de agentes, dos de seis fallaron y el equipo se quedó a diez.
    pasos: tuple[tuple[str, Any], ...] = (
        ("agents", seed_builtin_agents),
        ("qa_e2e_automator", seed_qa_e2e_automator),
        ("ci4_agents", seed_ci4_agents),
        ("agent_tools", seed_builtin_agent_tools),
        ("agent_skills", seed_builtin_agent_skills),
        ("ci4_agent_tools", seed_ci4_agent_tools),
        ("ci4_agent_skills", seed_ci4_agent_skills),
        ("qa_e2e_automator_tools", seed_qa_e2e_automator_tools),
        ("qa_e2e_automator_skills", seed_qa_e2e_automator_skills),
        ("teams", seed_builtin_teams),
        ("ci4_team", seed_ci4_team),
    )

    aplicados: dict[str, int] = {}
    fallidos: list[str] = []
    for nombre, seed in pasos:
        try:
            async with sessionmaker() as session, session.begin():
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CATALOG_LOCK_KEY}
                )
                aplicados[nombre] = int(await seed(session))
        except Exception:
            fallidos.append(nombre)
            _log.warning("startup.builtin_agent_capabilities_step_failed", step=nombre)

    if fallidos:
        _log.warning(
            "startup.builtin_agent_capabilities_partial",
            applied=aplicados,
            failed=fallidos,
        )
    else:
        _log.info("startup.builtin_agent_capabilities_refreshed", applied=aplicados)
    return {"refreshed": not fallidos, "applied": aplicados, "failed": fallidos}


__all__ = ["ensure_builtin_catalog", "refresh_builtin_agent_capabilities"]
