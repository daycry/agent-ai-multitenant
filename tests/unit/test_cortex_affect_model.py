"""Córtex F2 (fase A) — forma del modelo ORM `CortexAffectSnapshot` y AUSENCIA de
drift contra su migración (en proceso, sin Postgres).

Este fichero existe porque la tarea «Modelo ORM `CortexAffectSnapshot`» de
`docs/roadmap/cortex-f2-afectivo.md` pedía verificar `__tablename__`, columnas,
pertenencia a `Base.metadata` y que «`alembic check` no detecta drift» — y la
auditoría del 2026-07-27 (`docs/roadmap/gaps-cortex-2026-07-27.md`) comprobó que
NADA de eso estaba probado: ni un assert directo sobre la forma del modelo, ni
`alembic check` en ningún test ni en `.github/workflows/ci.yml`. El mapeo 1:1
modelo↔tabla se daba por bueno de forma indirecta, por el hecho de que los tests
de integración insertaban filas sin quejarse.

El drift que eso deja pasar es del tipo peor: silencioso hasta producción. Un
INSERT del ORM sólo toca las columnas que el modelo conoce, así que **añadir una
columna NOT NULL a la migración y olvidarla en el modelo** (o al revés) pasa toda
la suite de integración en verde y explota en el primer despliegue. Aquí se
comparan las dos declaraciones cara a cara, sin base de datos: se ejecuta el
`upgrade()` de la migración 0093 contra un `op` de mentira que graba el DDL y se
confronta con `CortexAffectSnapshot.__table__`.

Los defectos que atrapa, todos sin Postgres:

  * drift real modelo↔migración: una columna, un tipo, una nulabilidad, un
    `server_default`, una FK o un índice que existan en una declaración y no en la
    otra (lo que `alembic check` haría con una DB delante);
  * que alguien "arregle" el aviso de multi-tenancy metiendo `TenantScopedMixin`
    en esta tabla **tenant-less** (excepción consciente al Principio 1, ADR 0074:
    el eje de aislamiento es `owner_user_id`, filtrado explícito en todo SQL —
    sin RLS de respaldo);
  * que la serie temporal deje de ser append-only: un `updated_at`/`deleted_at`
    aquí significaría que una muestra afectiva ya emitida se puede reescribir o
    borrar, y el gráfico del panel dejaría de ser auditable;
  * que se pierda el UNIQUE PARCIAL por turno, que es la ÚNICA defensa de
    idempotencia del distilador (Celery re-entrega: sin él, una re-entrega
    duplicaría el snapshot del mismo turno) — y que se pierda su condición
    `WHERE source_turn_id IS NOT NULL`, sin la cual los snapshots de decay
    (todos con `source_turn_id` NULL) chocarían entre sí en PostgreSQL... no, peor:
    NULL no colisiona en un UNIQUE, pero el índice dejaría de ser parcial y la
    intención documentada se perdería;
  * que el modelo se quede fuera de `api_server.db.models`, que es el módulo que
    Alembic importa: un modelo no importado es invisible al autogenerate.

La migración viva (upgrade/downgrade reales, índices en `pg_class`, RLS apagada)
se ejerce aparte en `tests/integration/test_cortex_affect_store.py`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from api_server.db.base import Base, SoftDeleteMixin, TenantScopedMixin
from api_server.db.cortex_affect import CortexAffectSnapshot

pytestmark = pytest.mark.unit

_TABLE = "cortex_affect_snapshots"


# ---------------------------------------------------------------------------
# Nombre de tabla y forma de columnas
# ---------------------------------------------------------------------------
def test_tablename_es_el_canonico() -> None:
    assert CortexAffectSnapshot.__tablename__ == _TABLE


def test_tiene_exactamente_las_columnas_disenadas() -> None:
    """Conjunto EXACTO (no `<=`): una columna añadida al modelo sin migración
    —o al revés— rompe aquí y no en el primer INSERT de producción."""
    cols = {c.name for c in CortexAffectSnapshot.__table__.columns}
    assert cols == {
        "id",
        "owner_user_id",
        # Emoción viva (capa rápida).
        "valence",
        "arousal",
        "dominance",
        "intensity",
        # Mood (capa lenta, EWMA) + etiqueta derivada SOLO-UI.
        "mood_valence",
        "mood_arousal",
        "mood_dominance",
        "mood_label",
        # Drives homeostáticos.
        "drives",
        "appraisal_reason",
        "source_turn_id",
        "created_at",
    }


def test_columnas_no_nulas_del_estado_afectivo() -> None:
    """El estado PAD es la fuente de verdad del gráfico: un eje a NULL haría que
    el panel dibujase huecos que nadie sabría interpretar."""
    cols = CortexAffectSnapshot.__table__.columns
    for name in (
        "owner_user_id",
        "valence",
        "arousal",
        "dominance",
        "intensity",
        "mood_valence",
        "mood_arousal",
        "mood_dominance",
        "mood_label",
        "drives",
        "created_at",
    ):
        assert cols[name].nullable is False, name


def test_appraisal_y_turno_son_opcionales() -> None:
    """Los dos caminos que producen snapshot SIN LLM y SIN turno: el fail-open del
    distilador (Ollama caído ⇒ delta=0, `appraisal_reason` NULL) y el decay de
    mantenimiento (sin turno de origen). Si estas dos se volviesen NOT NULL, el
    fail-open dejaría de poder escribir y el afecto se congelaría en silencio."""
    cols = CortexAffectSnapshot.__table__.columns
    assert cols["appraisal_reason"].nullable is True
    assert cols["source_turn_id"].nullable is True


# ---------------------------------------------------------------------------
# Tenant-less + append-only por diseño (ADR 0074 / 0075)
# ---------------------------------------------------------------------------
def test_la_tabla_no_lleva_tenant_id() -> None:
    """El córtex es del System Owner, no de un tenant. Un `tenant_id` aquí no
    tendría quién lo rellene (el córtex no pasa por el middleware de tenant) y daría
    la falsa sensación de que hay RLS protegiendo estas filas — no la hay."""
    assert "tenant_id" not in {c.name for c in CortexAffectSnapshot.__table__.columns}


def test_el_modelo_no_hereda_tenant_ni_soft_delete() -> None:
    """La otra mitad de la misma defensa, por herencia: el mixin es la vía por la
    que `tenant_id`/`deleted_at` se colarían sin que nadie lo note en el diff."""
    assert not issubclass(CortexAffectSnapshot, TenantScopedMixin)
    assert not issubclass(CortexAffectSnapshot, SoftDeleteMixin)


def test_la_serie_es_append_only_inmutable() -> None:
    """Sólo `created_at`: sin `updated_at` no hay forma idiomática de reescribir una
    muestra ya emitida, y sin `deleted_at` la serie no se puede agujerear."""
    cols = {c.name for c in CortexAffectSnapshot.__table__.columns}
    assert "created_at" in cols
    assert "updated_at" not in cols
    assert "deleted_at" not in cols


# ---------------------------------------------------------------------------
# Índices: lectura del dial/timeseries, filtro por emoción e idempotencia
# ---------------------------------------------------------------------------
def _indexes() -> dict[str, sa.Index]:
    return {i.name: i for i in CortexAffectSnapshot.__table__.indexes if i.name}


def test_indice_del_timeseries_ordena_por_created_descendente() -> None:
    """`/affect/timeseries` y "el último snapshot" leen por owner en orden inverso;
    sin el `DESC` en el índice PostgreSQL ordena la serie entera en cada llamada."""
    idx = _indexes()["ix_cortex_affect_snapshots_owner_created"]
    assert idx.unique is False
    assert any("created_at DESC" in str(expr) for expr in idx.expressions)


def test_indice_por_mood_label_sirve_el_filtro_de_episodios() -> None:
    idx = _indexes()["ix_cortex_affect_snapshots_owner_mood_label"]
    assert idx.unique is False
    assert [c.name for c in idx.columns] == ["owner_user_id", "mood_label"]


def test_unique_parcial_por_turno_es_la_idempotencia_del_distilador() -> None:
    """Celery re-entrega por diseño (visibility timeout ~7 h): sin este UNIQUE, una
    re-entrega del mismo `turn_id` escribiría un segundo snapshot y el mismo evento
    contaría dos veces en el mood. La condición PARCIAL es igual de intencional: los
    snapshots de decay/mantenimiento van SIN turno de origen."""
    idx = _indexes()["uq_cortex_affect_snapshot_per_turn"]
    assert idx.unique is True
    assert [c.name for c in idx.columns] == ["source_turn_id"]
    where = idx.dialect_options["postgresql"].get("where")
    assert where is not None, "el UNIQUE por turno debe ser PARCIAL"
    assert "source_turn_id IS NOT NULL" in str(where)


# ---------------------------------------------------------------------------
# FK al owner + PK uuid7 generada en cliente
# ---------------------------------------------------------------------------
def test_fk_al_owner_cascadea_al_borrar_el_usuario() -> None:
    fks = list(CortexAffectSnapshot.__table__.columns["owner_user_id"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].target_fullname == "users.id"
    assert fks[0].ondelete == "CASCADE"


def test_fk_al_turno_no_arrastra_el_snapshot() -> None:
    """Borrar un hilo de conversación NO debe borrar la historia afectiva: el mood
    ya integró ese evento, así que la muestra sobrevive con `source_turn_id` NULL."""
    fks = list(CortexAffectSnapshot.__table__.columns["source_turn_id"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].target_fullname == "cortex_turns.id"
    assert fks[0].ondelete == "SET NULL"


def test_pk_uuid_se_genera_en_cliente() -> None:
    """`UUIDPrimaryKeyMixin` trae un default Python (uuid7): sin él un INSERT del
    ORM iría sin `id` y PostgreSQL no tiene generador para esta columna."""
    default = CortexAffectSnapshot.__table__.columns["id"].default
    assert default is not None
    assert default.is_callable is True


# ---------------------------------------------------------------------------
# Instanciación y registro en la metadata
# ---------------------------------------------------------------------------
def test_instanciar_un_snapshot_asigna_los_campos() -> None:
    owner = uuid4()
    snap = CortexAffectSnapshot(
        owner_user_id=owner,
        valence=0.4,
        arousal=0.7,
        dominance=0.1,
        intensity=0.6,
        mood_valence=0.02,
        mood_arousal=0.31,
        mood_dominance=0.01,
        mood_label="neutral",
        drives={"curiosity": 0.5, "bonding": 0.5, "coherence": 0.5, "competence": 0.5},
        appraisal_reason="el owner elogió el diseño",
        source_turn_id=None,
    )
    assert snap.owner_user_id == owner
    assert snap.mood_label == "neutral"
    assert snap.drives["curiosity"] == 0.5
    # Snapshot de decay/mantenimiento: sin turno de origen.
    assert snap.source_turn_id is None


def test_la_tabla_esta_en_la_metadata_y_en_db_models() -> None:
    """`db/models.py` es el módulo que importa `migrations/env.py`: un modelo que no
    esté ahí no aparece en el autogenerate y su drift pasa desapercibido."""
    assert _TABLE in Base.metadata.tables

    from api_server.db import models

    assert models.CortexAffectSnapshot is CortexAffectSnapshot
    assert "CortexAffectSnapshot" in models.__all__


# ===========================================================================
# Drift modelo ↔ migración 0093 (el `alembic check` que el plan pedía, sin DB)
# ===========================================================================
_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "apps"
    / "api-server"
    / "migrations"
    / "versions"
    / "20260623_0093_cortex_affect.py"
)


class _RecordingOp:
    """Un `alembic.op` de mentira que GRABA el DDL en vez de emitirlo.

    Permite leer la migración como dato (columnas, índices, FKs) sin Postgres y
    sin ejecutar Alembic: `upgrade()` es código Python normal en cuanto `op` deja
    de ser el proxy que exige un contexto de migración."""

    def __init__(self) -> None:
        self.tables: dict[str, tuple[Any, ...]] = {}
        self.indexes: list[tuple[str, str, list[Any], dict[str, Any]]] = []

    def create_table(self, name: str, *args: Any, **_kwargs: Any) -> None:
        self.tables[name] = args

    def create_index(self, name: str, table: str, columns: list[Any], **kwargs: Any) -> None:
        self.indexes.append((name, table, columns, kwargs))


def _migration_ddl() -> _RecordingOp:
    """Ejecuta el `upgrade()` de 0093 contra el `op` grabador."""
    spec = importlib.util.spec_from_file_location("_cortex_affect_migration_0093", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registrado en sys.modules durante la ejecución para que `from __future__` y
    # las anotaciones del módulo resuelvan igual que en un import normal.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        recorder = _RecordingOp()
        module.op = recorder  # type: ignore[attr-defined]
        module.upgrade()
        return recorder
    finally:
        sys.modules.pop(spec.name, None)


def _server_default_sql(column: sa.Column[Any]) -> str | None:
    """El `server_default` como SQL comparable.

    El modelo usa `func.now()` y la migración `sa.text("now()")`: objetos
    distintos que compilan al MISMO SQL. Comparar la forma renderizada es lo que
    hace `alembic check`; comparar los objetos daría un falso positivo."""
    default = column.server_default
    if default is None:
        return None
    return str(default.arg).strip()  # type: ignore[union-attr]


def test_la_migracion_declara_la_revision_encadenada() -> None:
    """Pin de contexto: si esta migración se renumerase, el fichero que este test
    lee ya no sería el que crea la tabla y el chequeo de drift se volvería vacío."""
    ddl = _migration_ddl()
    assert _TABLE in ddl.tables


def test_sin_drift_de_columnas_entre_modelo_y_migracion() -> None:
    """El corazón del `alembic check` que el plan pedía: nombre, tipo, nulabilidad
    y `server_default` de CADA columna, en las dos declaraciones."""
    ddl = _migration_ddl()
    # La lista de la migración es heterogénea (columnas + constraints), de ahí el
    # `isinstance`. Se extrae a una variable en vez de dejar el `type: ignore` dentro
    # de la comprensión: ahí el comentario hacía que black y ruff-format
    # discreparan sobre si la línea cabe, y cada uno deshacía al otro en el
    # pre-commit — el commit no llegaba a cerrar nunca.
    ddl_items: list[Any] = list(ddl.tables[_TABLE])
    migration_cols = {c.name: c for c in ddl_items if isinstance(c, sa.Column)}
    model_cols = {c.name: c for c in CortexAffectSnapshot.__table__.columns}

    assert set(migration_cols) == set(model_cols), (
        "drift de columnas modelo↔migración: sólo en la migración "
        f"{sorted(set(migration_cols) - set(model_cols))}, sólo en el modelo "
        f"{sorted(set(model_cols) - set(migration_cols))}"
    )
    for name, model_col in model_cols.items():
        migration_col = migration_cols[name]
        assert str(model_col.type) == str(migration_col.type), name
        assert model_col.nullable == migration_col.nullable, name
        assert _server_default_sql(model_col) == _server_default_sql(migration_col), name


def _index_part(expr: Any) -> str:
    """Normaliza un componente de índice a texto comparable.

    Las dos declaraciones nombran lo mismo de formas distintas: el modelo resuelve
    sus columnas a objetos `Column` (que renderizan cualificados,
    `cortex_affect_snapshots.owner_user_id`) y la migración las pasa como cadenas
    sueltas. Las expresiones (`created_at DESC`) son `TextClause` en ambos lados."""
    name = getattr(expr, "name", None)
    return name if isinstance(name, str) else str(expr)


def test_sin_drift_de_indices_entre_modelo_y_migracion() -> None:
    """Los índices son la mitad que el `INSERT` de un test de integración NO
    ejercita: un índice que exista sólo en el modelo no se crea en producción, y
    uno que exista sólo en la migración desaparece al regenerar el esquema."""
    ddl = _migration_ddl()
    migration_idx = {name: (cols, kwargs) for name, table, cols, kwargs in ddl.indexes}
    model_idx = _indexes()

    assert set(migration_idx) == set(model_idx), (
        "drift de índices modelo↔migración: sólo en la migración "
        f"{sorted(set(migration_idx) - set(model_idx))}, sólo en el modelo "
        f"{sorted(set(model_idx) - set(migration_idx))}"
    )
    for name, index in model_idx.items():
        columns, kwargs = migration_idx[name]
        # Expresiones (p.ej. `created_at DESC`) y nombres de columna, en orden.
        assert [_index_part(expr) for expr in index.expressions] == [
            _index_part(col) for col in columns
        ], name
        assert bool(index.unique) == bool(kwargs.get("unique", False)), name
        model_where = index.dialect_options["postgresql"].get("where")
        migration_where = kwargs.get("postgresql_where")
        assert (model_where is None) == (migration_where is None), name
        if model_where is not None:
            assert str(model_where) == str(migration_where), name


def test_sin_drift_de_claves_ajenas_entre_modelo_y_migracion() -> None:
    ddl = _migration_ddl()
    migration_fks = {
        tuple(c.column_keys): c
        for c in ddl.tables[_TABLE]
        if isinstance(c, sa.ForeignKeyConstraint)
    }
    model_fks = {
        (col.name,): next(iter(col.foreign_keys))
        for col in CortexAffectSnapshot.__table__.columns
        if col.foreign_keys
    }

    assert set(migration_fks) == set(model_fks)
    for cols, fk in model_fks.items():
        constraint = migration_fks[cols]
        assert [str(e.target_fullname) for e in constraint.elements] == [fk.target_fullname]
        assert constraint.ondelete == fk.ondelete, cols
