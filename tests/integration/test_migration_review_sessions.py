"""La tabla `review_sessions` en HEAD: forma, índices, CHECKs y RLS (Plan 06.5
`task_06_5_01`).

La casilla declaraba este fichero desde el 2026-05-28 y **no existía**. Lo que
sí había cubría dos de las tres cosas que promete, y ninguna con este sujeto:

  * `tests/integration/test_rls_invariant.py` exige RLS `ENABLE` + `FORCE` +
    policy a TODA tabla con columna de tenant — `review_sessions` entra por
    descubrimiento, no por nombre;
  * `tests/integration/test_migrations.py` afirma sobre dos de los cinco índices
    (los que añadió la 0031) y da la vuelta head→base→head;
  * de las **cuatro CHECK constraints** y de los predicados parciales de los
    índices de la 0024 no había ni una línea.

Lo que este fichero fija, y por qué cada cosa:

1. **Las columnas que el código escribe existen.** `_compose_review_runtime`
   inserta `spec`/`container_ids` como JSONB y sella `expires_at`; una migración
   que renombre cualquiera de las dos rompe el worker en producción, no en CI.
2. **NO hay secreto por fila, y es a propósito.** El enunciado de la casilla
   pedía `runtime_container_id` y `secret_hmac_key`; la migración 0024 no los
   creó nunca y su docstring lo dice: la URL firmada se deriva de una clave HMAC
   **de tenant** (`review_runtime.sign_review_url`), así que no hay material
   criptográfico en reposo en esta tabla. Se afirma en NEGATIVO porque «no
   guardamos secretos aquí» es una propiedad de seguridad que un `ALTER TABLE`
   bienintencionado puede tirar sin que nada más se queje.
3. **Los cinco índices son PARCIALES sobre filas vivas.** Un índice sin su
   `WHERE deleted_at IS NULL` sigue existiendo y sigue sirviendo la consulta:
   la diferencia es tamaño y coste de escritura, o sea que se degrada en
   silencio. Por eso se comprueba la definición, no el nombre.
4. **Los CHECK son la única defensa del estado.** `status` y `kind` son
   `String`, no enums de PostgreSQL: sin los CHECK, un `status` mal escrito
   entra y la sesión queda invisible para los barridos de expiración y de
   suspensión (que filtran `status = 'running'`). La invariante de ADR 0130
   —`plan_id NULL ⇒ kind='preview'`— vive en el mismo sitio.
5. **RLS con `FORCE`**, que la 0024 no puso y la 0125 arregló.

**Se afirma sobre HEAD, no sobre la 0024**, porque HEAD es lo que corre: la 0031
añadió el índice compuesto `(plan_id, status)`, la 0118 hizo `plan_id` NULLABLE y
añadió `kind` (ADR 0130, app-preview on-demand) y la 0125 añadió el `FORCE`. Un
test anclado a la forma original de la 0024 estaría hoy en rojo por tres cambios
deliberados, que es la forma más rápida de que un test acabe borrado.

La reversibilidad NO se re-comprueba aquí: `test_migrations.py` hace la vuelta
completa head→base→head, que incluye el `downgrade` de la 0024.
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
async def _fetch(dsn: str, sql: str, *args: Any) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(dsn)
    try:
        return [dict(r) for r in await conn.fetch(sql, *args)]
    finally:
        await conn.close()


def _rows(dsn: str, sql: str, *args: Any) -> list[dict[str, Any]]:
    return asyncio.run(_fetch(dsn, sql, *args))


@pytest.fixture()
def _at_head(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]


def _columns(dsn: str) -> dict[str, dict[str, Any]]:
    rows = _rows(
        dsn,
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
        " WHERE table_name = 'review_sessions'",
    )
    return {r["column_name"]: r for r in rows}


def _indexes(dsn: str) -> dict[str, str]:
    rows = _rows(
        dsn,
        "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'review_sessions'",
    )
    return {r["indexname"]: r["indexdef"] for r in rows}


def _checks(dsn: str) -> dict[str, str]:
    rows = _rows(
        dsn,
        "SELECT con.conname, pg_get_constraintdef(con.oid) AS def"
        " FROM pg_constraint con JOIN pg_class rel ON rel.oid = con.conrelid"
        " WHERE rel.relname = 'review_sessions' AND con.contype = 'c'",
    )
    return {r["conname"]: r["def"] for r in rows}


def _foreign_keys(dsn: str) -> dict[str, str]:
    rows = _rows(
        dsn,
        "SELECT con.conname, pg_get_constraintdef(con.oid) AS def"
        " FROM pg_constraint con JOIN pg_class rel ON rel.oid = con.conrelid"
        " WHERE rel.relname = 'review_sessions' AND con.contype = 'f'",
    )
    return {r["conname"]: r["def"] for r in rows}


# ---------------------------------------------------------------------------
# 1. La tabla y sus columnas
# ---------------------------------------------------------------------------
def test_the_table_exists_with_the_columns_the_code_writes(
    _at_head: None, migrations_pg_dsn: str
) -> None:
    cols = _columns(migrations_pg_dsn)
    assert cols, "no existe `review_sessions` en head"

    # Identidad + aislamiento.
    assert cols["id"]["data_type"] == "uuid"
    assert cols["tenant_id"]["data_type"] == "uuid"
    assert cols["tenant_id"]["is_nullable"] == "NO"

    # ADR 0130: un preview de PROYECTO no cuelga de un plan.
    assert cols["plan_id"]["data_type"] == "uuid"
    assert cols["plan_id"]["is_nullable"] == "YES"
    assert cols["kind"]["is_nullable"] == "NO"

    # Lo que `_compose_review_runtime` escribe: el spec y los ids de contenedor
    # viajan como JSONB (el worker rehidrata la sesión tras un reinicio).
    assert cols["spec"]["data_type"] == "jsonb"
    assert cols["container_ids"]["data_type"] == "jsonb"

    # El ciclo de vida que barren `expire_overdue` / `suspend_idle`.
    for column in ("created_at", "last_activity_at", "expires_at"):
        assert cols[column]["data_type"] == "timestamp with time zone"
        assert cols[column]["is_nullable"] == "NO", f"{column} debe estar siempre sellada"
    for column in ("suspended_at", "deleted_at"):
        assert cols[column]["is_nullable"] == "YES"

    # El veredicto humano.
    assert cols["verdict"]["is_nullable"] == "YES"
    assert "rejection_reason" in cols
    assert cols["rerun_requested"]["data_type"] == "boolean"


def test_no_per_session_secret_is_stored_at_rest(_at_head: None, migrations_pg_dsn: str) -> None:
    """El enunciado de la casilla pedía `secret_hmac_key` (y un
    `runtime_container_id` singular). Ninguno se creó, y el de la clave **no debe
    crearse**: la URL firmada se deriva de una clave HMAC de TENANT
    (`review_runtime.sign_review_url`), así que esta tabla no guarda material
    criptográfico. Guardarlo por fila multiplicaría por N las copias del secreto
    en el `pg_dump`.
    """
    cols = set(_columns(migrations_pg_dsn))
    assert cols, "descubrimiento vacío: el test pasaría por vacuidad"

    assert "secret_hmac_key" not in cols
    # Ninguna otra columna con pinta de secreto se ha colado por otro nombre.
    sospechosas = {c for c in cols if any(t in c for t in ("secret", "hmac", "token", "key"))}
    assert not sospechosas, f"columnas con pinta de secreto en review_sessions: {sospechosas}"

    # Y los ids de contenedor son la lista JSONB, no un singular.
    assert "runtime_container_id" not in cols
    assert "container_ids" in cols


# ---------------------------------------------------------------------------
# 2. Los índices, con su predicado parcial
# ---------------------------------------------------------------------------
def test_the_five_indexes_are_partial_over_live_rows(
    _at_head: None, migrations_pg_dsn: str
) -> None:
    idx = _indexes(migrations_pg_dsn)

    esperados = {
        # (nombre, trozos que su definición tiene que contener)
        "ix_review_sessions_tenant_id": ("(tenant_id)", "deleted_at IS NULL"),
        "ix_review_sessions_plan_id": ("(plan_id)", "deleted_at IS NULL"),
        # 0031: complementa al simple, no lo reemplaza.
        "ix_review_sessions_plan_status": ("plan_id, status", "deleted_at IS NULL"),
        # Los dos barridos: sólo miran filas `running` y vivas.
        "ix_review_sessions_running_by_expiry": (
            "(expires_at)",
            "'running'::text",
            "deleted_at IS NULL",
        ),
        "ix_review_sessions_running_by_activity": (
            "(last_activity_at)",
            "'running'::text",
            "deleted_at IS NULL",
        ),
    }
    faltan = set(esperados) - set(idx)
    assert not faltan, f"índices ausentes en head: {faltan}"

    for name, trozos in esperados.items():
        definicion = idx[name]
        for trozo in trozos:
            assert trozo in definicion, (
                f"{name} perdió `{trozo}`; un índice total sirve la misma consulta"
                f" y cuesta más en cada escritura. Definición: {definicion}"
            )


# ---------------------------------------------------------------------------
# 3. Los CHECK: la única defensa del estado (status/kind son String, no enum)
# ---------------------------------------------------------------------------
def test_the_status_check_admits_exactly_the_six_states(
    _at_head: None, migrations_pg_dsn: str
) -> None:
    checks = _checks(migrations_pg_dsn)
    assert "ck_review_sessions_status" in checks, checks
    definicion = checks["ck_review_sessions_status"]
    for estado in ("running", "suspended", "approved", "rejected", "expired", "cancelled"):
        assert f"'{estado}'" in definicion, f"{estado} salió del CHECK: {definicion}"


def test_the_verdict_and_kind_checks_are_in_place(_at_head: None, migrations_pg_dsn: str) -> None:
    checks = _checks(migrations_pg_dsn)
    assert "'approved'" in checks["ck_review_sessions_verdict"]
    assert "'rejected'" in checks["ck_review_sessions_verdict"]
    # ADR 0130.
    assert "'preview'" in checks["ck_review_sessions_kind"]
    assert "'plan'" in checks["ck_review_sessions_kind"]


def test_a_row_without_a_plan_must_declare_itself_a_preview(
    _at_head: None, migrations_pg_dsn: str
) -> None:
    """La invariante de ADR 0130 (`plan_id NULL ⇒ kind='preview'`) se comprueba
    INSERTANDO, no leyendo el catálogo: es la única forma de saber que el CHECK
    muerde y no sólo que está escrito.

    **Lleva su control dentro**, y no es adorno: la primera versión de este test
    insertaba con un `tenant_id` inventado y habría pasado igual por la
    **violación de la FK del tenant** — verde por el motivo equivocado, y encima
    seguiría verde el día que el CHECK desapareciera. Así que se siembra un
    tenant de verdad, se afirma sobre el NOMBRE de la constraint que salta, y la
    misma fila con `kind='preview'` **entra**: eso prueba que lo único que
    rechazó la primera era la invariante.
    """
    checks = _checks(migrations_pg_dsn)
    assert "ck_review_sessions_plan_or_preview" in checks, checks

    async def _exercise() -> None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        # `plan_id` es NULL en las dos, así que la FK a `plans` no participa;
        # la del tenant se satisface sembrando la organización.
        insert = (
            "INSERT INTO review_sessions"
            " (id, tenant_id, plan_id, spec, status, kind, expires_at)"
            " VALUES (gen_random_uuid(), $1, NULL, '{}'::jsonb, 'running', $2, now())"
        )
        tenant = None
        try:
            tenant = await conn.fetchval(
                "INSERT INTO organizations (id, name, slug)"
                " VALUES (gen_random_uuid(), 'RS Check', 'rs-check-migration') RETURNING id"
            )
            with pytest.raises(asyncpg.exceptions.CheckViolationError) as exc:
                await conn.execute(insert, tenant, "plan")
            assert exc.value.constraint_name == "ck_review_sessions_plan_or_preview", (
                f"saltó otra constraint ({exc.value.constraint_name}): este test estaría"
                f" pasando por un motivo que no es la invariante de ADR 0130"
            )
            # CONTROL: la misma fila declarada `preview` entra sin más.
            await conn.execute(insert, tenant, "preview")
        finally:
            if tenant is not None:
                # CASCADE se lleva la sesión sembrada.
                await conn.execute("DELETE FROM organizations WHERE id = $1", tenant)
            await conn.close()

    asyncio.run(_exercise())


def test_both_parents_cascade_on_delete(_at_head: None, migrations_pg_dsn: str) -> None:
    """Borrar el tenant o el plan se lleva sus sesiones: una sesión huérfana
    seguiría contando contra el cap del tenant y firmando URLs válidas."""
    fks = _foreign_keys(migrations_pg_dsn)
    tenant_fk = fks["fk_review_sessions_tenant"]
    plan_fk = fks["fk_review_sessions_plan"]
    assert "organizations" in tenant_fk and "ON DELETE CASCADE" in tenant_fk, tenant_fk
    assert "plans" in plan_fk and "ON DELETE CASCADE" in plan_fk, plan_fk


# ---------------------------------------------------------------------------
# 4. RLS: `ENABLE` (0024) + `FORCE` (0125) + policy por tenant
# ---------------------------------------------------------------------------
def test_rls_is_enabled_forced_and_scoped_by_tenant(_at_head: None, migrations_pg_dsn: str) -> None:
    estado = _rows(
        migrations_pg_dsn,
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
        " WHERE relname = 'review_sessions'",
    )
    assert estado, "no encuentro la tabla en pg_class"
    assert estado[0]["relrowsecurity"] is True, "RLS sin activar"
    # La 0024 se dejó el FORCE; sin él, el PROPIETARIO de la tabla se salta sus
    # propias policies. Lo puso la 0125.
    assert estado[0]["relforcerowsecurity"] is True, "falta FORCE (migración 0125)"

    policies = _rows(
        migrations_pg_dsn,
        "SELECT policyname, qual, with_check FROM pg_policies WHERE tablename = 'review_sessions'",
    )
    assert policies, "RLS activo y NINGUNA policy: la tabla queda ilegible, no protegida"
    predicados = " ".join(f"{p['qual']} {p['with_check']}" for p in policies)
    assert "app.tenant_id" in predicados, (
        f"ninguna policy de review_sessions se apoya en app.tenant_id: {policies}"
    )
