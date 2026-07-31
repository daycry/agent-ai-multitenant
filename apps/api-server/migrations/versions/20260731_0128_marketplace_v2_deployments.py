"""marketplace v2 (ADR 0142) — el despliegue como entidad + el histórico de versiones.

Tres cambios de esquema y un backfill que no es cosmético:

1. **`marketplace_listing_versions`** — una fila por versión publicada
   (snapshot del manifest, permisos y `config_schema`). Es lo que permite
   comparar «lo que el tenant consintió» con «lo que hay publicado ahora» sin
   confiar en que el manifest del listing no se haya movido.

2. **`marketplace_installations.pinned_version_id`** — el pin de la versión
   consentida. NULLABLE, y la razón está escrita en el modelo: el productor que
   lo mantiene al día en el flujo de PUBLICACIÓN llega en la fase 3/4 de este
   mismo plan. Una columna `NOT NULL` cuyo único escritor aterriza dos fases más
   tarde convierte cada install de un listing privado nuevo en un 500. El
   backfill de aquí abajo deja CERO nulos, y
   `marketplace.deploy.ensure_listing_version` lo rellena al primer despliegue
   de un listing publicado después.

3. **`marketplace_deployments`** — la entidad del ADR 0142. Con dos candados
   que hacen el trabajo que si no hay que hacer a mano en cada endpoint:
   - `uq_marketplace_deployments_active`: UNIQUE PARCIAL
     `(installation_id, project_id) WHERE status = 'active'`. Es lo que hace
     idempotente el re-despliegue **en la base de datos**, no en un `if` del
     servicio. Las filas `retired` quedan fuera del índice, así que el histórico
     acumula sin bloquear un re-despliegue posterior.
   - `created_refs` JSONB: las filas concretas que el despliegue creó. Sin ella
     la retirada es indecidible (¿esta fila `agent_tools` la puso el despliegue
     o el operador a mano?) y el modo de fallo es silencioso.

RLS: `marketplace_deployments` es tenant-owned → `ENABLE` + `FORCE` + policy
`tenant_isolation`, el patrón calcado de `user_invitations` (migración 0127).
`marketplace_listing_versions` es HÍBRIDA como `marketplace_listings`: su
`tenant_id` es NULLABLE y espeja el del listing, así que lleva las MISMAS tres
policies (aislamiento propio, lectura global, lectura de lo compartido vía
`marketplace_shares`) — una versión es exactamente tan visible como su listing y
nunca más. Sin la tercera, un tenant con un listing privado compartido podría
instalarlo y luego no ver su propia versión pinada.

Orden del upgrade a propósito: tablas → columna → **backfill** → RLS. El
backfill corre antes de activar `FORCE ROW LEVEL SECURITY` porque con FORCE ni el
propietario de la tabla escapa a las policies, y una sesión de migración no fija
`app.tenant_id`.

Revision ID: 0128_marketplace_v2_deploy
Revises: 0127_user_invitations
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0128_marketplace_v2_deploy"
down_revision: str | Sequence[str] | None = "0127_user_invitations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Backfill. Tres pasos, todos idempotentes (ON CONFLICT DO NOTHING / IS NULL),
# porque una migración que solo funciona sobre una base virgen no es reversible
# de verdad: el round-trip head→antes→head la vuelve a ejecutar.
# ---------------------------------------------------------------------------
_BACKFILL: tuple[str, ...] = (
    # (a) Cada listing existente pare su fila de versión con v = listing.version.
    #     `manifest -> 'config_schema'` da NULL cuando la clave no existe, que es
    #     exactamente la semántica que queremos («este listing no pide config»).
    "INSERT INTO marketplace_listing_versions"
    " (id, listing_id, tenant_id, version, manifest, requested_permissions,"
    "  config_schema, changelog, created_at, updated_at)"
    " SELECT gen_random_uuid(), l.id, l.tenant_id, l.version, l.manifest,"
    "        l.requested_permissions, l.manifest -> 'config_schema',"
    "        'Backfill ADR 0142: versión sintetizada del listing ya publicado.',"
    "        now(), now()"
    "   FROM marketplace_listings l"
    " ON CONFLICT (listing_id, version) DO NOTHING",
    # (b) Una instalación puede pinar una versión DISTINTA de la vigente del
    #     listing (``select_update_target`` re-apunta la instalación sin tocar el
    #     listing). Sin esta fila, el pin de (c) caería al fallback y mentiría
    #     sobre qué se consintió.
    "INSERT INTO marketplace_listing_versions"
    " (id, listing_id, tenant_id, version, manifest, requested_permissions,"
    "  config_schema, changelog, created_at, updated_at)"
    " SELECT gen_random_uuid(), d.listing_id, d.tenant_id, d.version, d.manifest,"
    "        d.requested_permissions, d.manifest -> 'config_schema',"
    "        'Backfill ADR 0142: versión sintetizada de una instalación existente.',"
    "        now(), now()"
    "   FROM (SELECT DISTINCT l.id AS listing_id, l.tenant_id, i.version, l.manifest,"
    "                l.requested_permissions"
    "           FROM marketplace_installations i"
    "           JOIN marketplace_listings l ON l.id = i.listing_id"
    "          WHERE i.version <> l.version) d"
    " ON CONFLICT (listing_id, version) DO NOTHING",
    # (c) El pin: la fila de SU propia versión.
    "UPDATE marketplace_installations i"
    "   SET pinned_version_id = v.id"
    "  FROM marketplace_listing_versions v"
    " WHERE v.listing_id = i.listing_id"
    "   AND v.version = i.version"
    "   AND i.pinned_version_id IS NULL",
    # (d) Cinturón: si algo quedó sin pin (una instalación cuya versión no casa
    #     con ninguna fila, p. ej. un semver reescrito a mano), cae a la versión
    #     vigente del listing en vez de quedarse a NULL en silencio.
    "UPDATE marketplace_installations i"
    "   SET pinned_version_id = v.id"
    "  FROM marketplace_listings l"
    "  JOIN marketplace_listing_versions v"
    "    ON v.listing_id = l.id AND v.version = l.version"
    " WHERE l.id = i.listing_id"
    "   AND i.pinned_version_id IS NULL",
)


_RLS_UP: tuple[str, ...] = (
    # marketplace_listing_versions — híbrida, espejo de marketplace_listings.
    "ALTER TABLE marketplace_listing_versions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE marketplace_listing_versions FORCE ROW LEVEL SECURITY",
    "CREATE POLICY marketplace_listing_versions_tenant_isolation"
    " ON marketplace_listing_versions FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
    "CREATE POLICY marketplace_listing_versions_global_read"
    " ON marketplace_listing_versions FOR SELECT"
    " USING (tenant_id IS NULL)",
    "CREATE POLICY marketplace_listing_versions_shared_read"
    " ON marketplace_listing_versions FOR SELECT"
    " USING (EXISTS ("
    "   SELECT 1 FROM marketplace_shares s"
    "    WHERE s.listing_id = marketplace_listing_versions.listing_id"
    "      AND s.target_tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    "      AND s.deleted_at IS NULL"
    "      AND s.revoked_at IS NULL))",
    # marketplace_deployments — tenant-owned, sin excepciones.
    "ALTER TABLE marketplace_deployments ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE marketplace_deployments FORCE ROW LEVEL SECURITY",
    "CREATE POLICY marketplace_deployments_tenant_isolation ON marketplace_deployments FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
)

_RLS_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS marketplace_deployments_tenant_isolation ON marketplace_deployments",
    "ALTER TABLE marketplace_deployments DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS marketplace_listing_versions_shared_read"
    " ON marketplace_listing_versions",
    "DROP POLICY IF EXISTS marketplace_listing_versions_global_read"
    " ON marketplace_listing_versions",
    "DROP POLICY IF EXISTS marketplace_listing_versions_tenant_isolation"
    " ON marketplace_listing_versions",
    "ALTER TABLE marketplace_listing_versions DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # 1. marketplace_listing_versions
    # -----------------------------------------------------------------------
    op.create_table(
        "marketplace_listing_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), nullable=False),
        # NULLABLE a propósito: espeja la tenencia híbrida del listing.
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column(
            "manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "requested_permissions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("config_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("published_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "listing_id",
            "version",
            name="uq_marketplace_listing_versions_listing_version",
        ),
        sa.ForeignKeyConstraint(["listing_id"], ["marketplace_listings.id"], ondelete="CASCADE"),
        # SET NULL: la versión sobrevive a quien la publicó y a quien la revisó
        # — es un rastro de auditoría, no una relación de dominio.
        sa.ForeignKeyConstraint(["published_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_marketplace_listing_versions_listing",
        "marketplace_listing_versions",
        ["listing_id"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_listing_versions_tenant",
        "marketplace_listing_versions",
        ["tenant_id"],
        unique=False,
        postgresql_where=sa.text("tenant_id IS NOT NULL"),
    )

    # -----------------------------------------------------------------------
    # 2. El pin en la instalación
    # -----------------------------------------------------------------------
    op.add_column(
        "marketplace_installations",
        sa.Column("pinned_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_marketplace_installations_pinned_version",
        "marketplace_installations",
        "marketplace_listing_versions",
        ["pinned_version_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # -----------------------------------------------------------------------
    # 3. marketplace_deployments
    # -----------------------------------------------------------------------
    op.create_table(
        "marketplace_deployments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "role_map",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("deployed_version", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "created_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("deployed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("retired_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("retired_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('active', 'disabled', 'retired')",
            name="ck_marketplace_deployments_status",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["installation_id"], ["marketplace_installations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["deployed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["retired_by"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_marketplace_deployments_tenant_id",
        "marketplace_deployments",
        ["tenant_id"],
        unique=False,
    )
    # El candado de la idempotencia: un solo despliegue ACTIVO por par.
    op.create_index(
        "uq_marketplace_deployments_active",
        "marketplace_deployments",
        ["installation_id", "project_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_marketplace_deployments_project_active",
        "marketplace_deployments",
        ["project_id"],
        unique=False,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_marketplace_deployments_installation",
        "marketplace_deployments",
        ["installation_id"],
        unique=False,
    )

    # -----------------------------------------------------------------------
    # 4. Backfill ANTES de la RLS (ver docstring).
    # -----------------------------------------------------------------------
    for stmt in _BACKFILL:
        op.execute(stmt)

    # -----------------------------------------------------------------------
    # 5. RLS
    # -----------------------------------------------------------------------
    for stmt in _RLS_UP:
        op.execute(stmt)


def downgrade() -> None:
    """Reversible de verdad: policies, RLS, la columna del pin y las dos tablas.

    Se pierden el histórico de versiones y los despliegues, que es exactamente
    el dato que esta migración introdujo. Las filas materializadas por un
    despliegue (`agent_tools`, `mcp_servers`) NO se tocan: bajar de versión el
    esquema no es retirar despliegues, y hacerlo aquí borraría capacidades que
    el operador ve como suyas.
    """
    for stmt in _RLS_DOWN:
        op.execute(stmt)

    op.drop_index("ix_marketplace_deployments_installation", table_name="marketplace_deployments")
    op.drop_index("ix_marketplace_deployments_project_active", table_name="marketplace_deployments")
    op.drop_index("uq_marketplace_deployments_active", table_name="marketplace_deployments")
    op.drop_index("ix_marketplace_deployments_tenant_id", table_name="marketplace_deployments")
    op.drop_table("marketplace_deployments")

    op.drop_constraint(
        "fk_marketplace_installations_pinned_version",
        "marketplace_installations",
        type_="foreignkey",
    )
    op.drop_column("marketplace_installations", "pinned_version_id")

    op.drop_index(
        "ix_marketplace_listing_versions_tenant", table_name="marketplace_listing_versions"
    )
    op.drop_index(
        "ix_marketplace_listing_versions_listing", table_name="marketplace_listing_versions"
    )
    op.drop_table("marketplace_listing_versions")
