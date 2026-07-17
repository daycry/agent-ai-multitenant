"""G-02 (auditoría proyecto 2026-07-17): el catálogo builtin de KBs se
garantiza al arrancar, no solo por un `python -m api_server.seeds` manual.

Tras el reset del tenant demo, `knowledge_bases` quedó a 0 filas: los
`default_kb_grants` de 6 plantillas apuntaban a KBs inexistentes y el auto-RAG
quedó estéril (0 rag_search en 128 runs). La red de seguridad
`ensure_builtin_catalog` re-siembra las FILAS estructurales (categorías + KBs
builtin — rápido, sin embeddings ni docling) de forma idempotente y bajo
advisory lock, y avisa si estaban ausentes. La ingesta del corpus (pesada,
Ollama) sigue en el CLI.
"""

from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _kb_count(sm: async_sessionmaker) -> int:
    async with sm() as s:
        return int(
            (
                await s.execute(
                    text("SELECT count(*) FROM knowledge_bases WHERE is_builtin = true")
                )
            ).scalar_one()
        )


@pytest.mark.asyncio
async def test_ensure_builtin_catalog_seeds_when_empty(
    _migrated: None, admin_database_url: str
) -> None:
    from api_server.seeds.startup import ensure_builtin_catalog

    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s, s.begin():
            await s.execute(text("TRUNCATE knowledge_bases CASCADE"))

        result = await ensure_builtin_catalog(sm)

        assert result["seeded"] is True
        assert result["builtin_kbs"] > 0
        assert await _kb_count(sm) == result["builtin_kbs"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_builtin_catalog_is_idempotent(
    _migrated: None, admin_database_url: str
) -> None:
    from api_server.seeds.startup import ensure_builtin_catalog

    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        first = await ensure_builtin_catalog(sm)
        before = await _kb_count(sm)
        second = await ensure_builtin_catalog(sm)
        after = await _kb_count(sm)

        assert first["builtin_kbs"] > 0
        # Segunda pasada: no re-siembra (el catálogo ya estaba) y no duplica.
        assert second["seeded"] is False
        assert before == after
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_slugs_referenced_by_templates_exist_after_seed(
    _migrated: None, admin_database_url: str
) -> None:
    """Los slugs de KB que las plantillas builtin conceden EXISTEN tras la
    siembra (el síntoma raíz de G-02: punteros muertos). Comprueba un conjunto
    representativo — las 8 CI4 + las convenciones por stack."""
    from api_server.seeds.builtin_kbs import kb_id_for_slug
    from api_server.seeds.startup import ensure_builtin_catalog

    referenced = [
        "codeigniter-4-conventions",
        "codeigniter-4-architecture",
        "codeigniter-4-testing",
        "python-fastapi-conventions",
        "react-nextjs-conventions",
        "postgresql-best-practices",
    ]
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        await ensure_builtin_catalog(sm)
        # El grant resuelve slug→id determinista (kb_id_for_slug, uuid5); la KB
        # existe si esa fila está viva (mismo check que apply_template_kb_grants).
        ids = [kb_id_for_slug(slug) for slug in referenced]
        async with sm() as s:
            present = {
                row[0]
                for row in (
                    await s.execute(
                        text(
                            "SELECT id FROM knowledge_bases"
                            " WHERE id = ANY(:ids) AND deleted_at IS NULL"
                        ),
                        {"ids": ids},
                    )
                ).all()
            }
        missing = [slug for slug, kid in zip(referenced, ids, strict=True) if kid not in present]
        assert not missing, f"KBs referenciadas por plantillas ausentes: {missing}"
    finally:
        await engine.dispose()
