"""Contrato ORM de las dos tablas del ADR 0142 (`task_mkt2_01`).

La migración y la RLS se ejercitan en
`tests/integration/test_marketplace_v2_migration.py`. Aquí, en proceso y sin BD,
se fija lo que el resto de la fase 1 da por cierto:

* los nombres de tabla y de columna que la migración tiene que crear;
* el **UNIQUE PARCIAL** `(installation_id, project_id) WHERE status = 'active'`,
  que es el candado de la idempotencia. Un UNIQUE total prohibiría re-desplegar
  tras una retirada, y un índice no-único no prohibiría nada;
* el `ondelete` de cada FK, que no es decoración: un `CASCADE` donde tocaba un
  `SET NULL` borra auditoría;
* el vocabulario cerrado de `status` **en la BD** (CHECK), no solo en el enum.

Por qué estas aserciones pueden fallar: cada una nombra un valor concreto
(`'active'`, `"CASCADE"`, `"SET NULL"`) que no coincide con el default de
SQLAlchemy, así que si alguien retira el índice parcial o afloja un `ondelete`,
se ponen rojas.
"""

from __future__ import annotations

import pytest
from api_server.db.marketplace import (
    DeploymentStatus,
    MarketplaceAuditAction,
    MarketplaceDeployment,
    MarketplaceInstallation,
    MarketplaceListingVersion,
)
from sqlalchemy import CheckConstraint, UniqueConstraint

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
def test_deployment_status_enum_values() -> None:
    """El vocabulario del §4 del diseño, exacto: la fila se conserva al retirar."""
    assert {s.value for s in DeploymentStatus} == {"active", "disabled", "retired"}


def test_audit_actions_cover_the_deployment_lifecycle() -> None:
    """Desplegar y retirar son acciones AUDITADAS, no efectos colaterales."""
    values = {a.value for a in MarketplaceAuditAction}
    assert {"deploy", "retire"} <= values
    # Y no se ha renombrado ninguna histórica (las filas viejas las referencian).
    assert {"install", "uninstall", "revoke", "consent", "update", "share"} <= values


# ---------------------------------------------------------------------------
# marketplace_deployments — forma
# ---------------------------------------------------------------------------
def test_deployment_table_name() -> None:
    assert MarketplaceDeployment.__tablename__ == "marketplace_deployments"


def test_deployment_has_the_columns_the_service_writes() -> None:
    cols = set(MarketplaceDeployment.__table__.columns.keys())
    expected = {
        "id",
        "tenant_id",
        "installation_id",
        "project_id",
        "config",
        "role_map",
        "deployed_version",
        "status",
        "created_refs",
        "deployed_by",
        "retired_at",
        "retired_by",
        "created_at",
        "updated_at",
    }
    assert expected <= cols, f"faltan columnas: {sorted(expected - cols)}"


def test_deployment_is_tenant_scoped_not_null() -> None:
    """`tenant_id NOT NULL` es la precondición de la policy RLS."""
    assert MarketplaceDeployment.__table__.c.tenant_id.nullable is False


def test_deployment_is_not_soft_deletable() -> None:
    """Retirar NO borra: pasa a `retired` y conserva la auditoría (ADR 0142 §5)."""
    assert "deleted_at" not in MarketplaceDeployment.__table__.columns


def test_deployment_status_default_is_active() -> None:
    default = MarketplaceDeployment.__table__.c.status.server_default
    assert default is not None
    assert "active" in str(default.arg)


def test_deployment_status_is_a_closed_vocabulary_in_the_db() -> None:
    checks = [
        c for c in MarketplaceDeployment.__table__.constraints if isinstance(c, CheckConstraint)
    ]
    names = {c.name for c in checks}
    assert "ck_marketplace_deployments_status" in names, (
        "sin CHECK, un script puede escribir un `status` inventado y el índice"
        " parcial de activos deja de significar lo que dice"
    )
    sqltext = " ".join(str(c.sqltext) for c in checks)
    for value in ("active", "disabled", "retired"):
        assert value in sqltext


def test_active_deployment_uniqueness_is_partial_on_status() -> None:
    """El candado de la idempotencia: UNIQUE **parcial** sobre los activos.

    Total prohibiría re-desplegar tras retirar; no-único no prohibiría el doble
    despliegue concurrente que el servicio promete que es un no-op.
    """
    indexes = {ix.name: ix for ix in MarketplaceDeployment.__table__.indexes}
    ix = indexes.get("uq_marketplace_deployments_active")
    assert ix is not None, f"índice ausente; hay {sorted(indexes)}"
    assert ix.unique is True
    assert [c.name for c in ix.columns] == ["installation_id", "project_id"]
    where = str(ix.dialect_options["postgresql"]["where"])
    assert "status" in where and "active" in where, (
        f"el índice no es parcial por status ({where!r}): un UNIQUE total"
        " impediría re-desplegar sobre un par retirado"
    )
    # Y no hay un UNIQUE de tabla que lo duplique con semántica total.
    uniques = [
        c for c in MarketplaceDeployment.__table__.constraints if isinstance(c, UniqueConstraint)
    ]
    assert uniques == [], f"UNIQUE total inesperado: {[c.name for c in uniques]}"


def test_deployment_project_read_index_is_restricted_to_active() -> None:
    ix = {i.name: i for i in MarketplaceDeployment.__table__.indexes}[
        "ix_marketplace_deployments_project_active"
    ]
    assert "active" in str(ix.dialect_options["postgresql"]["where"])


@pytest.mark.parametrize(
    ("column", "ondelete"),
    [
        # La instalación y el proyecto son los DUEÑOS del despliegue: si
        # cualquiera muere, el despliegue no tiene sentido.
        ("installation_id", "CASCADE"),
        ("project_id", "CASCADE"),
        # El actor es auditoría: el despliegue sobrevive a quien lo hizo.
        ("deployed_by", "SET NULL"),
        ("retired_by", "SET NULL"),
    ],
)
def test_deployment_fk_ondelete(column: str, ondelete: str) -> None:
    fks = list(MarketplaceDeployment.__table__.c[column].foreign_keys)
    assert len(fks) == 1, f"{column} debería tener exactamente una FK"
    assert fks[0].ondelete == ondelete


def test_deployment_tenant_fk_lives_in_the_migration_only() -> None:
    """`TenantScopedMixin` no declara FK en `tenant_id`, y aquí tampoco.

    Es la convención de la casa (todas las tablas tenant-scoped): la FK a
    `organizations` la pone la migración, y el ORM se queda con la columna. Este
    test existe para que quien lea el modelo y no vea la FK no la añada «para
    arreglarlo» — la comprobación de que EXISTE en la BD vive en
    `tests/integration/test_marketplace_v2_migration.py`.
    """
    assert list(MarketplaceDeployment.__table__.c.tenant_id.foreign_keys) == []


# ---------------------------------------------------------------------------
# marketplace_listing_versions — forma
# ---------------------------------------------------------------------------
def test_listing_version_table_name() -> None:
    assert MarketplaceListingVersion.__tablename__ == "marketplace_listing_versions"


def test_listing_version_snapshots_what_publish_declared() -> None:
    cols = set(MarketplaceListingVersion.__table__.columns.keys())
    expected = {
        "id",
        "listing_id",
        "tenant_id",
        "version",
        "manifest",
        "requested_permissions",
        "config_schema",
        "changelog",
        "published_by",
        "reviewed_by",
        "reviewed_at",
    }
    assert expected <= cols, f"faltan columnas: {sorted(expected - cols)}"


def test_listing_version_tenant_is_nullable_hybrid() -> None:
    """Espeja la tenencia híbrida del listing: NULL = catálogo global.

    Si dejara de ser nullable, las versiones del catálogo oficial (Playwright)
    no podrían existir.
    """
    assert MarketplaceListingVersion.__table__.c.tenant_id.nullable is True


def test_listing_version_is_unique_per_listing_and_version() -> None:
    uniques = {
        c.name: [col.name for col in c.columns]
        for c in MarketplaceListingVersion.__table__.constraints
        if isinstance(c, UniqueConstraint)
    }
    assert uniques.get("uq_marketplace_listing_versions_listing_version") == [
        "listing_id",
        "version",
    ]


@pytest.mark.parametrize(
    ("column", "ondelete"),
    [
        ("listing_id", "CASCADE"),
        ("published_by", "SET NULL"),
        ("reviewed_by", "SET NULL"),
    ],
)
def test_listing_version_fk_ondelete(column: str, ondelete: str) -> None:
    fks = list(MarketplaceListingVersion.__table__.c[column].foreign_keys)
    assert len(fks) == 1
    assert fks[0].ondelete == ondelete


# ---------------------------------------------------------------------------
# El pin en la instalación
# ---------------------------------------------------------------------------
def test_installation_pins_a_version_row() -> None:
    col = MarketplaceInstallation.__table__.c.pinned_version_id
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "marketplace_listing_versions"
    # NULLABLE por la desviación documentada en el modelo (el escritor del pin
    # en el flujo de PUBLICACIÓN es la fase 3/4). El backfill deja cero nulos y
    # el test de integración de la migración lo comprueba sobre datos reales.
    assert col.nullable is True
