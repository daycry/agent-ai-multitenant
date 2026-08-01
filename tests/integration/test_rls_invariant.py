"""Invariante de cobertura RLS (plan prod-14, hallazgo tenancy-3).

El resto de la suite comprueba el aislamiento **tabla a tabla**: alguien
escribió un test para `agents`, otro para `projects`, otro para `executions`.
Eso deja abierta una clase entera de regresión: *la migración futura que crea
una tabla con `tenant_id` y se olvida del bloque `ENABLE ROW LEVEL SECURITY`*.
Nadie la detecta, porque nadie escribe el test que no sabe que falta, y CI pasa
en verde. Ese es el agujero que cierra este fichero.

La lógica es al revés que la de un test normal: no enumera lo que debe estar
protegido, **descubre** en el catálogo de PostgreSQL todo lo que huele a
tenant-scoped y exige que lo esté. Lo que no lo esté tiene que aparecer en una
allowlist con su justificación escrita al lado. Añadir una entrada nueva a una
allowlist es una decisión que se ve en el diff del PR; olvidarse de la RLS, no.

Cuatro invariantes:

1. Toda tabla con una columna `*tenant_id` tiene RLS `ENABLE` + `FORCE`, al
   menos una policy, y esa policy referencia `app.tenant_id`.
2. Toda tabla SIN columna de tenant está en :data:`GLOBAL_TABLES_ALLOWLIST`.
3. Ninguna allowlist tiene entradas muertas (tabla que ya no existe) y todas
   llevan justificación de verdad.
4. Las allowlists son **MÍNIMAS**: ninguna exime a una tabla que ya no lo
   necesita, y el conjunto de tablas exentas es EXACTAMENTE el catalogado.

## Sobre el ratchet que había aquí y ya no está

La primera ejecución de este fichero (2026-07-30) destapó cuatro desviaciones
reales, que quedaron anotadas en dos dicts `KNOWN_RLS_GAPS_*` para no hacer
scope creep. La migración **0125** cerró tres de las cuatro (`ENABLE` + `FORCE` +
policy owner-only en `cortex_conversations`; el `FORCE` que faltaba en
`review_sessions`, `task_audit_events` y `tenant_settings`) y la cuarta,
`marketplace_sources`, resultó ser una decisión documentada, no un olvido: está
catalogada abajo. Con los dos ratchets vacíos, el propio test #4 pedía borrarlos
—«un ratchet vacío no vigila nada»—, así que se sustituyeron por la forma
CERRADA: el invariante nº 1 ya no admite exenciones fuera de las allowlists
justificadas, y el nº 4 exige además que esas allowlists sean mínimas y exactas.
El resultado es estrictamente más estricto que el ratchet: antes se podía eximir
una tabla añadiéndola a `KNOWN_RLS_GAPS_*` con la coartada de «es un olvido
conocido»; ahora hay que justificar por qué es correcta.

Las listas manuales que ya existían (`EXPECTED_RLS_TABLES` en
`test_migrations.py`, `NEW_TENANT_SCOPED_TABLES` en `test_migrations_v2.py`) se
conservan: son tests *por migración*. Este es el complemento que no envejece.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from alembic import command

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]


# ---------------------------------------------------------------------------
# Tablas SIN ninguna columna de tenant. Cada entrada lleva su porqué; si una
# tabla nueva aparece aquí sin justificación, el revisor del PR tiene que
# preguntar por qué es global.
# ---------------------------------------------------------------------------
GLOBAL_TABLES_ALLOWLIST: dict[str, str] = {
    "alembic_version": "Contabilidad interna de Alembic (una fila con el revision id).",
    "organizations": (
        "ES el tenant. Su aislamiento no es `tenant_id = app.tenant_id` sino"
        " `id = app.tenant_id` (policy `org_self_only`, migración 0001)."
    ),
    "users": (
        "Directorio GLOBAL de identidades: el login es pre-tenant (no hay"
        " `app.tenant_id` cuando se resuelve el email → hash), y un mismo usuario"
        " puede pertenecer a varios tenants vía `user_org_memberships`. El ADR"
        " 0137 descarta la RLS sobre `users`: una policy que depende de quién"
        " eres no puede gobernar la query que averigua quién eres. El aislamiento"
        " del directorio lo da la RLS de `user_org_memberships`, por la que pasan"
        " las dos únicas lecturas en contexto tenant."
    ),
    "platform_settings": (
        "Ajustes de la PLATAFORMA (System Admin). No hay dato de tenant que aislar."
    ),
    "llm_providers": (
        "Catálogo cerrado de proveedores LLM (ADR 0021), de plataforma. Las"
        " credenciales por tenant NO viven aquí (van a Vault / tenant_settings)."
    ),
    "model_prices": "Tarifas públicas de modelos: mismo dato para todos los tenants.",
    "price_sync_audit": "Auditoría de la sincronización de tarifas: operación de plataforma.",
    "exchange_rates": "Tipos de cambio: dato de mercado, idéntico para todos los tenants.",
    "sso_configurations": (
        "Global por decisión explícita de la migración 0076 (`sso_global`), que"
        " RETIRÓ su `tenant_id` y su RLS: el descubrimiento de proveedor SSO"
        " ocurre ANTES de saber el tenant. Revertirlo exige revertir la 0076."
    ),
    "cortex_identity": "Córtex (ADR 0074): pertenece al usuario dueño, no a un tenant.",
    "cortex_identity_history": "Córtex (ADR 0074): aislado por `owner_user_id`.",
    "cortex_turns": "Córtex (ADR 0074): aislado por `owner_user_id`.",
    "cortex_affect_snapshots": "Córtex (ADR 0074): aislado por `owner_user_id`.",
    "cortex_curiosity_pursuits": "Córtex (ADR 0074): aislado por `owner_user_id`.",
}

# ---------------------------------------------------------------------------
# Tablas CON columna de tenant y sin RLS **por decisión documentada**.
# ---------------------------------------------------------------------------
TENANT_COLUMN_WITHOUT_RLS_ALLOWLIST: dict[str, str] = {
    "marketplace_sources": (
        "Decisión declarada TEXTUALMENTE en el docstring de `MarketplaceSource`"
        " (`db/marketplace.py`): «Tenancy decision: **tenant-agnostic** — a source"
        " is a platform-level registry endpoint, not per-tenant data, so it"
        " carries no ``tenant_id`` and no RLS policy. A *private* tenant catalog"
        " is a row whose nullable ``owner_tenant_id`` is set; the service layer"
        " resolves visibility (public sources + the caller's own private"
        " source).» Lo que dispara este invariante es que ese `owner_tenant_id`"
        " casa con el patrón `%tenant_id` del descubrimiento, no que haya dato de"
        " tenant que aislar: es NULL en las sources públicas, que son la mayoría,"
        " y una policy `= app.tenant_id` las escondería todas. Cambiarlo requiere"
        " ADR."
    ),
}

# Tablas cuya policy NO menciona `app.tenant_id` a propósito.
POLICY_WITHOUT_TENANT_GUC_ALLOWLIST: dict[str, str] = {
    "sessions": (
        "Se aísla por `app.user_id` (`session_owner_only`, migración 0001) y no"
        " por tenant: una sesión pertenece a la PERSONA, que puede tener varios"
        " tenants. Aislarla por tenant rompería el cambio de tenant sin re-login."
    ),
    "cortex_conversations": (
        "Mismo patrón que `sessions`: se aísla por `app.user_id`"
        " (`cortex_conversations_owner_only`, migración 0125). Su `tenant_id` es,"
        " palabra por palabra del modelo, «the physical discriminator the owner's"
        " memory needs — NOT an authorisation axis»: lo resuelve"
        " `resolve_cortex_tenant_id` como la membresía activa MÁS ANTIGUA del"
        " owner (Decisión D1, ADR 0074). Una policy por tenant sería a la vez"
        " demasiado permisiva (el `tenant_admin` de ese tenant leería el hilo"
        " privado del System Owner) y funcionalmente rota (`open_tenant_session`"
        " fija `app.tenant_id` al tenant ELEGIDO en la request, así que el owner"
        " entrando con otro contexto perdería su historial en silencio). El"
        " aislamiento por owner es estrictamente más restrictivo. Cobertura"
        " funcional: `tests/integration/test_cortex_conversations_rls.py`."
    ),
}


# ---------------------------------------------------------------------------
# Descubrimiento
# ---------------------------------------------------------------------------
async def _introspect(dsn: str) -> dict[str, dict[str, object]]:
    """{tabla: {tenant_columns, rls, force, policies, guc_policies}} de `public`."""
    conn = await asyncpg.connect(dsn)
    try:
        tables = [
            r["table_name"]
            for r in await conn.fetch(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
        ]
        tenant_cols: dict[str, list[str]] = {t: [] for t in tables}
        for row in await conn.fetch(
            "SELECT table_name, column_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND column_name LIKE '%tenant_id'"
        ):
            tenant_cols.setdefault(row["table_name"], []).append(row["column_name"])

        # `relkind IN ('r', 'p')`: 'r' son las tablas normales y 'p' las
        # PARTICIONADAS (part-01 / ADR 0151). Con solo 'r', el PADRE de una tabla
        # particionada entraba por `information_schema.tables` —donde sí aparece—
        # y salía de aquí sin fila, o sea con `(False, False)`: el invariante nº 1
        # lo denunciaba por «sin ENABLE ROW LEVEL SECURITY» teniéndola. Un falso
        # positivo, y de los caros: el arreglo obvio ante ese mensaje es añadir la
        # tabla a una allowlist, que es exactamente eximir de la RLS a la tabla
        # que sí la tiene. Las particiones son 'r' y entran por el camino normal,
        # que es lo que se quiere: cada una lleva su propia policy.
        flags = {
            r["relname"]: (r["relrowsecurity"], r["relforcerowsecurity"])
            for r in await conn.fetch(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class"
                " WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r', 'p')"
            )
        }
        policies: dict[str, list[tuple[str, str]]] = {t: [] for t in tables}
        for row in await conn.fetch(
            "SELECT tablename, policyname,"
            " coalesce(qual, '') || ' ' || coalesce(with_check, '') AS expr"
            " FROM pg_policies WHERE schemaname = 'public'"
        ):
            policies.setdefault(row["tablename"], []).append((row["policyname"], row["expr"]))

        out: dict[str, dict[str, object]] = {}
        for t in tables:
            rls, force = flags.get(t, (False, False))
            pols = policies.get(t, [])
            out[t] = {
                "tenant_columns": sorted(tenant_cols.get(t, [])),
                "rls": rls,
                "force": force,
                "policies": [p[0] for p in pols],
                "guc_policies": [p[0] for p in pols if "app.tenant_id" in p[1]],
            }
        return out
    finally:
        await conn.close()


@pytest.fixture()
def schema(alembic_config: object, migrations_pg_dsn: str) -> dict[str, dict[str, object]]:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    return asyncio.run(_introspect(migrations_pg_dsn))


def _tenant_scoped(schema: dict[str, dict[str, object]]) -> list[str]:
    return sorted(t for t, meta in schema.items() if meta["tenant_columns"])


# ===========================================================================
# 0. La guarda de la guarda: si el descubrimiento deja de encontrar tablas,
#    todo lo de abajo pasaría VACÍAMENTE y el invariante moriría en silencio.
# ===========================================================================
def test_discovery_actually_finds_the_schema(schema) -> None:
    assert len(schema) >= 75, (
        f"el descubrimiento solo vio {len(schema)} tablas: o la migración no corrió"
        " o la query de introspección se rompió — los invariantes de abajo estarían"
        " pasando en vacío"
    )
    tenant_scoped = _tenant_scoped(schema)
    assert len(tenant_scoped) >= 60, (
        f"solo {len(tenant_scoped)} tablas tenant-scoped descubiertas (esperaba >= 60):"
        " la detección de la columna tenant_id dejó de funcionar"
    )


# ===========================================================================
# 1. Toda tabla con columna de tenant: RLS ENABLE + FORCE + policy que use el
#    GUC. Es el invariante que impide que la PRÓXIMA tabla nazca desprotegida.
# ===========================================================================
def test_every_tenant_scoped_table_has_complete_rls(schema) -> None:
    # La ÚNICA vía de exención, desde que la 0125 cerró el ratchet: una entrada
    # justificada en la allowlist, visible en el diff del PR.
    exempt = set(TENANT_COLUMN_WITHOUT_RLS_ALLOWLIST)
    checked = 0
    offenders: list[str] = []
    for table in _tenant_scoped(schema):
        if table in exempt:
            continue
        checked += 1
        meta = schema[table]
        if not meta["rls"]:
            offenders.append(f"{table}: sin ENABLE ROW LEVEL SECURITY")
        elif not meta["force"]:
            offenders.append(f"{table}: sin FORCE ROW LEVEL SECURITY")
        elif not meta["policies"]:
            offenders.append(f"{table}: RLS activa pero SIN NINGUNA POLICY")
        elif not meta["guc_policies"] and table not in POLICY_WITHOUT_TENANT_GUC_ALLOWLIST:
            offenders.append(
                f"{table}: tiene policies {meta['policies']} pero ninguna referencia"
                " app.tenant_id (¿USING (true)?)"
            )

    # 65 tablas comprobadas en HEAD 0125 (66 tenant-scoped − 1 exenta). La cota
    # subió de 55 a 62 al cerrarse el ratchet: cuatro tablas que se saltaban este
    # invariante ahora entran por él.
    assert checked >= 62, f"solo se comprobaron {checked} tablas: el filtro se comió el conjunto"
    assert not offenders, (
        "tablas con tenant_id y RLS incompleta. Añade el bloque canónico a su"
        " migración:\n  ALTER TABLE t ENABLE ROW LEVEL SECURITY;\n"
        "  ALTER TABLE t FORCE ROW LEVEL SECURITY;\n"
        "  CREATE POLICY t_tenant_isolation ON t FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (...);\n"
        "…o, si la tabla es global a propósito, documéntala en la allowlist de"
        f" este fichero.\nOfensores: {offenders}"
    )


# ===========================================================================
# 2. Toda tabla SIN columna de tenant está justificada en la allowlist.
# ===========================================================================
def test_every_global_table_is_documented(schema) -> None:
    globals_found = sorted(t for t, meta in schema.items() if not meta["tenant_columns"])
    undocumented = [t for t in globals_found if t not in GLOBAL_TABLES_ALLOWLIST]
    assert not undocumented, (
        "tablas sin columna de tenant y sin justificar. Si son globales a"
        " propósito, añádelas a GLOBAL_TABLES_ALLOWLIST con el porqué; si les"
        f" falta el tenant_id, es un fallo de aislamiento: {undocumented}"
    )
    assert len(globals_found) >= 10, (
        f"solo {len(globals_found)} tablas globales descubiertas: el descubrimiento"
        " se rompió y este test pasaría en vacío"
    )


# ===========================================================================
# 3. Las allowlists no acumulan entradas muertas y CADA UNA lleva justificación.
# ===========================================================================
def test_allowlists_have_no_dead_entries(schema) -> None:
    for name, allowlist in (
        ("GLOBAL_TABLES_ALLOWLIST", GLOBAL_TABLES_ALLOWLIST),
        ("TENANT_COLUMN_WITHOUT_RLS_ALLOWLIST", TENANT_COLUMN_WITHOUT_RLS_ALLOWLIST),
        ("POLICY_WITHOUT_TENANT_GUC_ALLOWLIST", POLICY_WITHOUT_TENANT_GUC_ALLOWLIST),
    ):
        dead = [t for t in allowlist if t not in schema]
        assert not dead, (
            f"{name} menciona tablas que ya no existen: {dead}. Una allowlist con"
            " entradas muertas es una excepción que nadie revisa."
        )
        thin = [t for t, why in allowlist.items() if len(why) < 30]
        assert not thin, f"{name}: entradas sin justificación de verdad: {thin}"


# ===========================================================================
# 4. FORMA CERRADA (sustituye al ratchet, ver docstring del módulo): el conjunto
#    de tablas exentas es EXACTAMENTE el catalogado, y cada allowlist es MÍNIMA
#    — ninguna exime a una tabla que ya no lo necesita.
#
#    Por qué esto es más estricto que el ratchet que había: el ratchet dejaba
#    eximir una tabla escribiéndola en `KNOWN_RLS_GAPS_*` con la coartada de
#    «hueco conocido». Aquí toda exención pasa por una allowlist con
#    justificación, y además caduca sola: en cuanto la tabla deja de necesitarla,
#    el test se pone rojo pidiendo que se borre la entrada. Una allowlist que
#    nadie poda se convierte en el cajón de sastre que este fichero existe para
#    impedir.
# ===========================================================================
def test_the_only_exempt_tables_are_the_catalogued_ones(schema) -> None:
    incomplete = {
        t
        for t in _tenant_scoped(schema)
        if not (schema[t]["rls"] and schema[t]["force"] and schema[t]["policies"])
    }
    assert incomplete == set(TENANT_COLUMN_WITHOUT_RLS_ALLOWLIST), (
        "el conjunto de tablas tenant-scoped con RLS incompleta ya no coincide con"
        f" el catálogo. Catalogadas {sorted(TENANT_COLUMN_WITHOUT_RLS_ALLOWLIST)},"
        f" encontradas {sorted(incomplete)}. Si has arreglado una, bórrala de la"
        " allowlist; si aparece una nueva, es una regresión de aislamiento y NO se"
        " tapa añadiéndola aquí sin justificar por qué es correcta."
    )


def test_allowlists_are_minimal(schema) -> None:
    """Cada exención sigue siendo necesaria. Si no, sobra."""
    stale_global = [
        t for t in GLOBAL_TABLES_ALLOWLIST if t in schema and schema[t]["tenant_columns"]
    ]
    assert not stale_global, (
        "estas tablas están en GLOBAL_TABLES_ALLOWLIST pero YA tienen columna de"
        f" tenant: {stale_global}. Sácalas de la allowlist — tienen que pasar por"
        " el invariante nº 1, no esquivarlo."
    )

    stale_no_rls = [
        t
        for t in TENANT_COLUMN_WITHOUT_RLS_ALLOWLIST
        if t in schema and schema[t]["rls"] and schema[t]["force"] and schema[t]["policies"]
    ]
    assert not stale_no_rls, (
        "estas tablas están exentas de RLS pero YA la tienen completa:"
        f" {stale_no_rls}. Borra la entrada para que el invariante nº 1 las"
        " vigile de verdad."
    )

    stale_guc = [
        t for t in POLICY_WITHOUT_TENANT_GUC_ALLOWLIST if t in schema and schema[t]["guc_policies"]
    ]
    assert not stale_guc, (
        "estas tablas están exentas de «la policy debe citar app.tenant_id» pero"
        f" YA tienen una que lo cita: {stale_guc}. Borra la entrada."
    )

    # La guarda de la guarda: si las tres listas se vaciasen, las tres
    # aserciones de arriba pasarían en vacío y este test moriría en silencio.
    total = (
        len(GLOBAL_TABLES_ALLOWLIST)
        + len(TENANT_COLUMN_WITHOUT_RLS_ALLOWLIST)
        + len(POLICY_WITHOUT_TENANT_GUC_ALLOWLIST)
    )
    assert total >= 15, f"solo {total} exenciones catalogadas: ¿se vaciaron las allowlists?"


# ===========================================================================
# 5. Las 4 junctions de la migración 0124 entran por el camino normal, sin
#    excepciones: es la comprobación de que este invariante y prod-14 encajan.
# ===========================================================================
def test_junction_tables_need_no_exception(schema) -> None:
    for table in ("agent_skills", "agent_tools", "team_members", "task_dependencies"):
        meta = schema[table]
        assert meta["tenant_columns"] == ["tenant_id"], f"{table} perdió su tenant_id"
        assert meta["rls"] and meta["force"], f"{table}: RLS incompleta"
        assert f"{table}_tenant_isolation" in meta["guc_policies"]
        for allowlist in (
            GLOBAL_TABLES_ALLOWLIST,
            TENANT_COLUMN_WITHOUT_RLS_ALLOWLIST,
            POLICY_WITHOUT_TENANT_GUC_ALLOWLIST,
        ):
            assert table not in allowlist, f"{table} no debería necesitar excepción"
