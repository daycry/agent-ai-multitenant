"""Córtex F3 (bloque 1) — forma de los modelos ORM de identidad (en proceso, sin DB).

Este fichero existe porque la tarea F3.1 de `docs/roadmap/cortex-f3-identidad.md`
pedía por nombre un test unitario de :class:`CortexIdentity` /
:class:`CortexIdentityHistory` que nunca se escribió: la auditoría del 2026-07-27
(`docs/roadmap/gaps-cortex-2026-07-27.md`) comprobó que NINGÚN test del repo
nombraba estas clases. Lo que se presentaba como cobertura equivalente vive en
integración y afirma `relrowsecurity = false` — eso es RLS apagada en la TABLA, que
NO es la defensa que pedía el plan («comprobar que NO hay `tenant_id`» en el
MODELO): una tabla puede llevar perfectamente una columna `tenant_id` con RLS off.

Los defectos que atrapa, todos sin Postgres:

  * que alguien "arregle" el aviso de multi-tenancy añadiendo `TenantScopedMixin` a
    estas dos tablas **tenant-less** (excepción consciente al Principio 1, ADR 0074:
    el eje de aislamiento es `owner_user_id` y el filtro es explícito en todo SQL);
  * que se pierda el invariante singleton (`uq_cortex_identity_owner`) o el UNIQUE
    `(owner, version)` del versionado — la única defensa contra dos identidades del
    mismo owner o dos filas de la misma versión;
  * que el histórico deje de ser append-only inmutable: un `updated_at`/`deleted_at`
    ahí significaría que la auditoría del cambio se puede reescribir o borrar, y la
    identidad NUNCA se borra (ADR 0077), solo se versiona;
  * que el índice del timeline pierda el `version DESC` que el histórico necesita
    para servir «las últimas N versiones» sin ordenar la tabla entera;
  * que los modelos se queden fuera de `api_server.db.models`, que es donde Alembic
    los ve: un modelo no importado es invisible al autogenerate y el drift pasa.

La migración `0094_cortex_identity` y la (no-)RLS se ejercen aparte en
`tests/integration/test_cortex_identity.py`.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from api_server.db.base import Base, SoftDeleteMixin, TenantScopedMixin
from api_server.db.cortex_identity import CortexIdentity, CortexIdentityHistory

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Nombres de tabla y forma de columnas (espejo de la migración 0094)
# ---------------------------------------------------------------------------
def test_tablenames_son_los_canonicos() -> None:
    assert CortexIdentity.__tablename__ == "cortex_identity"
    assert CortexIdentityHistory.__tablename__ == "cortex_identity_history"


def test_identity_tiene_exactamente_las_columnas_disenadas() -> None:
    """Conjunto EXACTO (no `<=`): así una columna añadida al modelo sin migración
    —o al revés— rompe aquí en lugar de en el primer INSERT de producción."""
    cols = {c.name for c in CortexIdentity.__table__.columns}
    assert cols == {
        "id",
        "owner_user_id",
        "identity_state",
        "version",
        "updated_by",
        "onboarded_at",
        "created_at",
        "updated_at",
    }


def test_history_tiene_exactamente_las_columnas_disenadas() -> None:
    cols = {c.name for c in CortexIdentityHistory.__table__.columns}
    assert cols == {
        "id",
        "owner_user_id",
        "version",
        "identity_state",
        "diff",
        "updated_by",
        "reason",
        "created_at",
    }


# ---------------------------------------------------------------------------
# Tenant-less por diseño (ADR 0074) — el criterio literal del plan
# ---------------------------------------------------------------------------
def test_ninguna_tabla_de_identidad_lleva_tenant_id() -> None:
    """El córtex es del System Owner, no de un tenant: el aislamiento es
    `owner_user_id` filtrado explícitamente en todo SQL. Un `tenant_id` NOT NULL
    aquí no tendría quién lo rellene (no hay middleware de tenant en el córtex) y
    daría la falsa sensación de que hay RLS protegiendo estas filas."""
    for model in (CortexIdentity, CortexIdentityHistory):
        cols = {c.name for c in model.__table__.columns}
        assert "tenant_id" not in cols, model.__tablename__


def test_los_modelos_no_heredan_tenant_ni_soft_delete() -> None:
    """La otra mitad de la misma defensa, por herencia: el mixin es la vía por la
    que `tenant_id`/`deleted_at` se colarían sin que nadie lo note en el diff."""
    for model in (CortexIdentity, CortexIdentityHistory):
        assert not issubclass(model, TenantScopedMixin), model.__tablename__
        assert not issubclass(model, SoftDeleteMixin), model.__tablename__


def test_la_identidad_no_es_borrable_ni_por_soft_delete() -> None:
    """ADR 0077: la identidad nunca se auto-olvida; se versiona."""
    assert "deleted_at" not in {c.name for c in CortexIdentity.__table__.columns}


# ---------------------------------------------------------------------------
# Invariante singleton + versionado sin duplicados
# ---------------------------------------------------------------------------
def test_unique_por_owner_impone_el_singleton() -> None:
    idx = {i.name: i for i in CortexIdentity.__table__.indexes}
    assert "uq_cortex_identity_owner" in idx
    singleton = idx["uq_cortex_identity_owner"]
    assert singleton.unique is True
    assert {c.name for c in singleton.columns} == {"owner_user_id"}


def test_unique_owner_version_impide_dos_filas_de_la_misma_version() -> None:
    idx = {i.name: i for i in CortexIdentityHistory.__table__.indexes}
    assert "uq_cortex_identity_history_owner_version" in idx
    versioned = idx["uq_cortex_identity_history_owner_version"]
    assert versioned.unique is True
    assert {c.name for c in versioned.columns} == {"owner_user_id", "version"}


def test_indice_del_timeline_ordena_por_version_descendente() -> None:
    """El timeline se lee «las últimas N versiones»: sin el `DESC` en el índice el
    endpoint de histórico ordena la tabla entera en cada llamada."""
    idx = {i.name: i for i in CortexIdentityHistory.__table__.indexes}
    assert "ix_cortex_identity_history_owner_version" in idx
    timeline = idx["ix_cortex_identity_history_owner_version"]
    assert timeline.unique is False
    assert any("version DESC" in str(expr) for expr in timeline.expressions)


# ---------------------------------------------------------------------------
# Nullabilidad y defaults de servidor
# ---------------------------------------------------------------------------
def test_columnas_no_nulas_de_identity() -> None:
    cols = CortexIdentity.__table__.columns
    for name in ("owner_user_id", "identity_state", "version", "updated_by"):
        assert cols[name].nullable is False, name
    # NULL ⇒ onboarding pendiente: es el flag de "aún no me he presentado".
    assert cols["onboarded_at"].nullable is True


def test_columnas_no_nulas_de_history() -> None:
    cols = CortexIdentityHistory.__table__.columns
    for name in ("owner_user_id", "version", "identity_state", "diff", "updated_by"):
        assert cols[name].nullable is False, name
    # El `reason` es un resumen 1-línea opcional del ciclo que produjo el cambio.
    assert cols["reason"].nullable is True


def test_defaults_de_servidor_evitan_jsonb_nulos() -> None:
    """Un blob JSONB a NULL rompería a todo consumidor (`state.get(...)` sobre
    None); el server_default `'{}'::jsonb` lo hace imposible desde SQL crudo."""
    identity_cols = CortexIdentity.__table__.columns
    assert "'{}'::jsonb" in str(identity_cols["identity_state"].server_default.arg)
    history_cols = CortexIdentityHistory.__table__.columns
    assert "'{}'::jsonb" in str(history_cols["identity_state"].server_default.arg)
    assert "'{}'::jsonb" in str(history_cols["diff"].server_default.arg)
    assert "0" in str(identity_cols["version"].server_default.arg)
    assert "onboarding" in str(identity_cols["updated_by"].server_default.arg)


def test_history_es_append_only_inmutable() -> None:
    """Solo `created_at`: sin `updated_at` no hay forma idiomática de reescribir
    una fila de auditoría ya emitida."""
    cols = {c.name for c in CortexIdentityHistory.__table__.columns}
    assert "created_at" in cols
    assert "updated_at" not in cols
    assert "deleted_at" not in cols
    assert CortexIdentityHistory.__table__.columns["created_at"].nullable is False


# ---------------------------------------------------------------------------
# FK al owner + PK uuid7 generada en cliente
# ---------------------------------------------------------------------------
def test_fk_al_owner_cascadea_al_borrar_el_usuario() -> None:
    for model in (CortexIdentity, CortexIdentityHistory):
        fks = list(model.__table__.columns["owner_user_id"].foreign_keys)
        assert len(fks) == 1, model.__tablename__
        assert fks[0].target_fullname == "users.id"
        assert fks[0].ondelete == "CASCADE"


def test_pk_uuid_se_genera_en_cliente() -> None:
    """`UUIDPrimaryKeyMixin` trae un default Python (uuid7): sin él un INSERT del
    ORM iría sin `id` y PostgreSQL no tiene generador para esta columna."""
    for model in (CortexIdentity, CortexIdentityHistory):
        default = model.__table__.columns["id"].default
        assert default is not None, model.__tablename__
        assert default.is_callable is True


# ---------------------------------------------------------------------------
# Instanciación y registro en la metadata
# ---------------------------------------------------------------------------
def test_instanciar_una_identidad_asigna_los_campos() -> None:
    owner = uuid4()
    identity = CortexIdentity(
        owner_user_id=owner,
        identity_state={"name": "Córtex", "traits": {"openness": 0.5}},
        version=0,
        updated_by="onboarding",
        onboarded_at=None,
    )
    assert identity.owner_user_id == owner
    assert identity.identity_state["name"] == "Córtex"
    assert identity.version == 0
    assert identity.updated_by == "onboarding"
    # NULL ⇒ onboarding pendiente (nadie ha confirmado la identidad todavía).
    assert identity.onboarded_at is None


def test_instanciar_una_fila_de_historico_asigna_el_diff() -> None:
    owner = uuid4()
    row = CortexIdentityHistory(
        owner_user_id=owner,
        version=3,
        identity_state={"name": "Atlas"},
        diff={"name": {"before": "Córtex", "after": "Atlas"}},
        updated_by="owner_override",
        reason="el owner renombró el córtex",
    )
    assert row.owner_user_id == owner
    assert row.version == 3
    assert row.diff["name"] == {"before": "Córtex", "after": "Atlas"}
    assert row.updated_by == "owner_override"
    assert row.reason == "el owner renombró el córtex"


def test_ambas_tablas_estan_en_la_metadata_y_en_db_models() -> None:
    """`db/models.py` es el módulo que importa Alembic: un modelo que no esté ahí
    no aparece en el autogenerate y su drift de esquema pasa desapercibido."""
    assert "cortex_identity" in Base.metadata.tables
    assert "cortex_identity_history" in Base.metadata.tables

    from api_server.db import models

    assert models.CortexIdentity is CortexIdentity
    assert models.CortexIdentityHistory is CortexIdentityHistory
    assert "CortexIdentity" in models.__all__
    assert "CortexIdentityHistory" in models.__all__
