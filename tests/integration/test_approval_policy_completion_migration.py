"""La migración 0133 (ADR 0153, opción D) contra PostgreSQL real.

El banco tiene proyectos de los CUATRO presets más los tres casos raros que de
verdad viven en la BD: la política que la UI guarda sin `preset`, la que ya está
completa y la que no tiene política ninguna.

Lo que se comprueba, y por qué cada cosa:

1. **No queda ni una política incompleta.** Es la invariante que el ADR viene a
   establecer: cero categorías implícitas, ni una sola delegando en un default
   del código.
2. **Los de `development` y `sandbox` deciden EXACTAMENTE lo mismo antes y
   después.** Es el gemelo del anterior y el que demuestra que completar no es
   endurecer. Si se pone rojo, alguien «arregló» la migración endureciendo
   desarrollo — y una cola de aprobaciones que nadie atiende enseña a aprobar
   sin leer.
3. **El downgrade devuelve las políticas byte a byte**, desde el respaldo, no
   por inferencia.
4. **El pre-check aborta** ante una política incoherente y no deja nada escrito.
5. **El informe previo predice exactamente lo que la migración escribe.** Era la
   condición del operador al firmar; un informe que no predice es peor que
   ninguno.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from shared_domain.approval_categories import APPROVAL_CATEGORIES

pytestmark = [pytest.mark.integration]

#: La revisión ANTERIOR a la 0133: el estado que su `downgrade` debe restaurar.
#: Por NOMBRE, nunca `-1` — con varios carriles añadiendo migraciones, `-1`
#: apunta a lo que haya debajo en ese momento.
_REVISION_BEFORE = "0132_guardrail_configs"

_BACKUP_TABLE = "approval_policy_backfill_0133"

# --- El banco, tal y como las políticas existen HOY en la base -------------
_SKELETON: dict[str, Any] = {
    "preset": "development",
    "categories": {
        "code_changes": "auto",
        "git_push": "human_required",
        "external_http": "human_required",  # no es canónica: no gatea nada
        "secrets_access": "human_required",
    },
}
_PRODUCTION: dict[str, Any] = {
    **_SKELETON,
    "preset": "production",
    "categories": {
        **_SKELETON["categories"],
        "data_migration": "human_required",
        "production_deploy": "human_required",
    },
}
_SANDBOX: dict[str, Any] = {"preset": "sandbox", "categories": {"all": "auto"}}
_CUSTOMER: dict[str, Any] = {
    "preset": "customer-external",
    "categories": {"code_changes": "auto"},
}
_HAND_WRITTEN: dict[str, Any] = {
    "preset": "production",
    "categories": {**_PRODUCTION["categories"], "data_export_pii": "auto"},
}


class _JsonNull:
    """Centinela: `'null'::jsonb`, que NO es el NULL de SQL.

    Existe porque los dos se leen `None` en Python y el `WHERE ... IS NOT NULL`
    de la migración solo filtra el segundo. Sin descartarlo a mano, el pre-check
    lo vería como «la política no es un objeto JSON» y abortaría la migración
    entera por un valor que el gate trata como «sin política».
    """


_JSON_NULL = _JsonNull()

#: (slug del banco, política) — el orden es estable para que los asserts hablen.
_BANK: tuple[tuple[str, Any], ...] = (
    ("sandbox", _SANDBOX),
    ("development", _SKELETON),
    ("production", _PRODUCTION),
    ("customer-external", _CUSTOMER),
    ("hand-written", _HAND_WRITTEN),
    ("ui-saved-no-preset", None),  # se rellena en _seed (necesita preset_decisions)
    ("already-complete", None),  # idem
    ("no-policy", None),
    ("empty-policy", {}),
    ("json-null-policy", _JSON_NULL),
)


def _ui_saved_policy() -> dict[str, Any]:
    """Lo que deja la pantalla de política: las 13 explícitas y NINGÚN preset."""
    from api_server.seeds.builtin_approval_policies import preset_decisions

    return {"categories": preset_decisions("development")}


def _already_complete_policy() -> dict[str, Any]:
    from api_server.cli.approval_policy_audit import complete_policy

    return complete_policy(_PRODUCTION)


#: Los dos del banco que se construyen en tiempo de ejecución.
_LATE_BOUND = {
    "ui-saved-no-preset": _ui_saved_policy,
    "already-complete": _already_complete_policy,
}


def _bank() -> list[tuple[str, Any]]:
    return [
        (slug, _LATE_BOUND[slug]() if slug in _LATE_BOUND else policy) for slug, policy in _BANK
    ]


# ---------------------------------------------------------------------------
# Utilidades de base de datos
# ---------------------------------------------------------------------------
async def _seed(dsn: str, bank: list[tuple[str, Any]]) -> dict[str, UUID]:
    tenant_id = uuid4()
    ids: dict[str, UUID] = {}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE projects RESTART IDENTITY CASCADE")
        for slug, policy in bank:
            project_id = uuid4()
            ids[slug] = project_id
            await conn.execute(
                "INSERT INTO projects (id, tenant_id, name, human_approval_policy)"
                " VALUES ($1, $2, $3, CAST($4 AS jsonb))",
                project_id,
                tenant_id,
                f"banco-{slug}",
                (
                    None
                    if policy is None
                    else ("null" if isinstance(policy, _JsonNull) else json.dumps(policy))
                ),
            )
    finally:
        await conn.close()
    return ids


async def _policies(dsn: str) -> dict[str, Any]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch("SELECT name, human_approval_policy FROM projects ORDER BY name")
        return {
            r["name"].removeprefix("banco-"): (
                None
                if r["human_approval_policy"] is None
                else json.loads(r["human_approval_policy"])
            )
            for r in rows
        }
    finally:
        await conn.close()


async def _backup_rows(dsn: str) -> dict[str, Any]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            f"SELECT p.name, b.previous_policy FROM {_BACKUP_TABLE} b"
            "  JOIN projects p ON p.id = b.project_id"
        )
        return {r["name"].removeprefix("banco-"): json.loads(r["previous_policy"]) for r in rows}
    finally:
        await conn.close()


async def _table_exists(dsn: str, table: str) -> bool:
    conn = await asyncpg.connect(dsn)
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", table))
    finally:
        await conn.close()


def _seeded_before_the_migration(alembic_config: object, dsn: str) -> dict[str, UUID]:
    """Deja la base en 0132 con el banco sembrado, listo para subir a la 0133."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]
    return asyncio.run(_seed(dsn, _bank()))


def _decisions(policy: Any) -> dict[str, bool]:
    """Lo que el gate REAL decide para las 13 + una categoría que no existe."""
    from api_server.db.approval_repo import requires_human_approval

    probes = (*APPROVAL_CATEGORIES, "una_categoria_que_todavia_no_existe")
    return {category: requires_human_approval(policy, category) for category in probes}


# ---------------------------------------------------------------------------
# 1. La invariante: nadie se queda a medias
# ---------------------------------------------------------------------------
def test_no_policy_is_left_incomplete(alembic_config: object, migrations_pg_dsn: str) -> None:
    """Cero categorías implícitas en TODOS los presets, no solo en producción.

    Si esto se pone rojo tras añadir una categoría canónica, el mensaje es: la
    0133 congeló las 13 de 2026-08-02 y hace falta OTRA migración de relleno
    para la nueva, no editar aquélla.
    """
    from api_server.cli.approval_policy_audit import UNLISTED_CATEGORY_KEY, policy_categories

    _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    after = asyncio.run(_policies(migrations_pg_dsn))
    for slug, policy in after.items():
        # «Sin política» se decide por la FORMA, no por una lista de slugs: un
        # `NULL`, un `'null'::jsonb` y un `{}` son los tres el mismo caso, y con
        # una lista a mano el tercero se coló (rojo del 2026-08-02). El ADR 0104
        # ya les da una política completa —heredan el preset VIVO de la
        # plataforma— y escribirles una explícita los congelaría contra ese
        # ajuste, que es justo lo contrario de lo que se busca.
        if not isinstance(policy, dict) or not policy:
            continue
        missing = sorted(set(APPROVAL_CATEGORIES) - set(policy_categories(policy)))
        assert not missing, f"{slug} quedó con categorías implícitas: {missing}"
        assert UNLISTED_CATEGORY_KEY in policy, slug


def test_projects_without_a_policy_are_left_exactly_as_they_were(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """NULL y `{}` no son políticas incompletas: son proyectos SIN política.

    El ADR 0104 ya les da una completa (heredan el preset de plataforma, vivo).
    Escribirles una explícita los congelaría contra ese ajuste.
    """
    _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    after = asyncio.run(_policies(migrations_pg_dsn))

    assert after["no-policy"] is None
    assert after["empty-policy"] == {}
    # Y el `'null'::jsonb` sigue ahí SIN haber abortado la migración: el gate lo
    # trata como «sin política», así que el pre-check no puede tomarlo por un
    # dato incoherente.
    assert after["json-null-policy"] is None


# ---------------------------------------------------------------------------
# 2. El gemelo: completar NO es endurecer
# ---------------------------------------------------------------------------
def test_development_and_sandbox_decide_exactly_the_same_before_and_after(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """El test que demuestra la mitad (b) de la decisión del operador.

    Si se pone rojo, alguien ha endurecido desarrollo — y una cola de
    aprobaciones que nadie atiende es PEOR que no tener gate: enseña a aprobar
    sin leer, y ese hábito se lleva luego al proyecto donde sí importaba.
    """
    _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    before = asyncio.run(_policies(migrations_pg_dsn))

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    after = asyncio.run(_policies(migrations_pg_dsn))

    for slug in ("development", "sandbox"):
        assert _decisions(before[slug]) == _decisions(after[slug]), slug


def test_the_migration_never_loosens_anything_anywhere(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """Ninguna categoría pasa de exigir humano a no exigirlo. En ningún preset."""
    _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    before = asyncio.run(_policies(migrations_pg_dsn))

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    after = asyncio.run(_policies(migrations_pg_dsn))

    for slug, previous in before.items():
        for category, needed_human in _decisions(previous).items():
            if needed_human:
                assert _decisions(after[slug])[category], (slug, category)


def test_production_gets_the_eight_implicit_categories_gated(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """Y la mitad (a): en producción lo implícito se escribe con criterio estricto."""
    _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    categories = asyncio.run(_policies(migrations_pg_dsn))["production"]["categories"]
    newly_gated = sorted(
        category
        for category in APPROVAL_CATEGORIES
        if category not in _PRODUCTION["categories"] and categories[category] == "human_required"
    )

    assert len(newly_gated) == 8, newly_gated
    assert "data_export_pii" in newly_gated
    assert "user_management" in newly_gated


def test_a_hand_written_decision_survives_the_migration(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """`data_export_pii: auto` bajo `production`, puesto a conciencia, se respeta."""
    _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    after = asyncio.run(_policies(migrations_pg_dsn))["hand-written"]

    assert after["categories"]["data_export_pii"] == "auto"


def test_stray_non_canonical_keys_are_not_deleted(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """`external_http` no gatea nada, pero borrarlo NO es «rellenar lo ausente»."""
    _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    after = asyncio.run(_policies(migrations_pg_dsn))

    assert after["development"]["categories"]["external_http"] == "human_required"
    assert after["sandbox"]["categories"]["all"] == "auto"


# ---------------------------------------------------------------------------
# 3. Idempotencia y reversibilidad
# ---------------------------------------------------------------------------
def test_an_already_complete_policy_is_not_touched_nor_backed_up(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """La prueba de que una segunda pasada no haría nada: la primera ya no lo hace."""
    _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    before = asyncio.run(_policies(migrations_pg_dsn))

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    after = asyncio.run(_policies(migrations_pg_dsn))

    assert after["already-complete"] == before["already-complete"]
    # Sin cambio no hay respaldo: el respaldo describe EXACTAMENTE lo tocado.
    assert "already-complete" not in asyncio.run(_backup_rows(migrations_pg_dsn))


def test_the_downgrade_restores_every_policy_byte_for_byte(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """Reversible de verdad: se restaura la FOTO, no se «quita lo que se puso».

    Reconstruir por inferencia devolvería a un valor que la política nunca tuvo
    en cuanto alguien la hubiera editado después de migrar.
    """
    _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    before = asyncio.run(_policies(migrations_pg_dsn))

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    assert asyncio.run(_policies(migrations_pg_dsn)) != before  # algo hizo

    command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]

    assert asyncio.run(_policies(migrations_pg_dsn)) == before
    assert not asyncio.run(_table_exists(migrations_pg_dsn, _BACKUP_TABLE))

    # Y vuelve a subir sobre la base ya usada, no sobre una virgen.
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    assert asyncio.run(_policies(migrations_pg_dsn)) != before


def test_the_backup_table_records_the_previous_state(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    before = asyncio.run(_policies(migrations_pg_dsn))

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    backup = asyncio.run(_backup_rows(migrations_pg_dsn))

    for slug, previous in backup.items():
        assert previous == before[slug], slug
    # Los cuatro presets del banco cambian; los de `no-policy`/`empty-policy` no.
    assert {"sandbox", "development", "production", "customer-external"} <= set(backup)
    assert not ({"no-policy", "empty-policy", "json-null-policy"} & set(backup))


# ---------------------------------------------------------------------------
# 4. El pre-check ruidoso
# ---------------------------------------------------------------------------
def test_the_precheck_aborts_and_writes_nothing(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """Ante datos incoherentes se para, no se «elige» y se consolida el desastre."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]
    broken = [
        ("development", _SKELETON),
        # `human-required` con guion: una intención escrita que hoy NO gatea nada.
        ("typo", {"preset": "production", "categories": {"git_push": "human-required"}}),
        # Y un valor truthy que no es un objeto: hoy revienta el gate con
        # AttributeError, así que tampoco puede pasar de largo.
        ("not-an-object", ["code_changes"]),
    ]
    asyncio.run(_seed(migrations_pg_dsn, broken))
    before = asyncio.run(_policies(migrations_pg_dsn))

    with pytest.raises(RuntimeError, match="0133 abortada"):
        command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    assert asyncio.run(_policies(migrations_pg_dsn)) == before
    assert not asyncio.run(_table_exists(migrations_pg_dsn, _BACKUP_TABLE))

    # Deja la base utilizable para el resto de la sesión.
    asyncio.run(_seed(migrations_pg_dsn, _bank()))
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 5. El informe previo predice lo que pasa (la condición del operador)
# ---------------------------------------------------------------------------
async def _audit(url: str) -> Any:
    from api_server.cli.approval_policy_audit import audit_approval_policies
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine)() as session:
            report = await audit_approval_policies(session)
            await session.rollback()
            return report
    finally:
        await engine.dispose()


def test_the_report_predicts_exactly_what_the_migration_writes(
    alembic_config: object, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Un informe que no predice lo que va a pasar es peor que no tener informe."""
    from api_server.cli.approval_policy_audit import complete_policy

    _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    before = asyncio.run(_policies(migrations_pg_dsn))
    report = asyncio.run(_audit(admin_database_url))

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    after = asyncio.run(_policies(migrations_pg_dsn))

    assert report.without_policy == 3  # `no-policy`, `empty-policy` y `json-null-policy`
    assert report.would_abort is False
    for finding in report.findings:
        slug = finding.name.removeprefix("banco-")
        assert complete_policy(before[slug]) == after[slug], slug
        predicted = set(finding.plan.writes)
        actual = {
            category
            for category in APPROVAL_CATEGORIES
            if category not in (before[slug].get("categories") or before[slug])
        }
        assert predicted == actual, slug


def test_the_report_transaction_is_read_only(
    alembic_config: object, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """«Sin permisos de escritura» es una garantía de la BASE, no una promesa.

    El rol admin (BYPASSRLS) SÍ puede escribir; lo que impide la escritura es el
    `SET TRANSACTION READ ONLY` que abre el informe. Esto lo comprueba.
    """
    from api_server.cli.approval_policy_audit import audit_approval_policies
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    _seeded_before_the_migration(alembic_config, migrations_pg_dsn)

    async def attempt() -> str:
        engine = create_async_engine(admin_database_url)
        try:
            async with async_sessionmaker(engine)() as session:
                await audit_approval_policies(session)
                with pytest.raises(Exception) as excinfo:  # - driver-specific
                    await session.execute(text("UPDATE projects SET name = 'no debería'"))
                return str(excinfo.value)
        finally:
            await engine.dispose()

    assert "read-only" in asyncio.run(attempt()).lower()

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
