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


__all__ = ["ensure_builtin_catalog"]
