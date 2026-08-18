"""SAML columns on sso_configurations (Plan 08 task_08_04).

Phase B adds SAML 2.0 alongside the OIDC flow shipped in Phase A. Both
providers live on the SAME ``sso_configurations`` table (one row per
``(tenant_id, provider)``), so a tenant can have an ``oidc`` row AND a
``saml`` row simultaneously without either touching the other.

SAML needs a different identity-provider shape than OIDC (there is no
discovery doc, no client_id/secret token exchange). This migration adds
the SAML-specific columns and relaxes the two OIDC-only NOT NULL columns
(``issuer``, ``client_id``) so a ``saml`` row can omit them:

  * ``idp_entity_id``      — the IdP's SAML EntityID (the ``Issuer`` it
                             stamps on assertions; validated on the ACS).
  * ``idp_sso_url``        — the IdP Single-Sign-On endpoint the SP
                             AuthnRequest redirects the browser to.
  * ``idp_x509_cert``      — the IdP's signing certificate (PEM/base64),
                             used to verify the assertion signature.
  * ``name_id_format``     — the requested NameID format (defaults to
                             emailAddress).
  * ``attribute_mappings`` — SAML attribute name -> local user field
                             (e.g. ``{"email": "...", "full_name": "..."}``).

A CHECK constraint enforces that a ``saml`` row carries the three SAML
essentials (entity id, SSO URL, cert), mirroring how the OIDC invariant
is guarded — without forcing them onto ``oidc`` rows.

Reversible (corregido el 2026-08-18)
------------------------------------
``downgrade`` **borra las filas ``saml``** antes de restaurar los NOT NULL de
``issuer`` / ``client_id``, y luego tira el CHECK y las columnas SAML.

El texto original decía lo contrario: «no ``saml`` rows can exist at downgrade
time because the table only held OIDC rows before this revision … we hard-
require the operator to drop saml rows first». Ese razonamiento era cierto el
día que se escribió —esta revisión ES la que introduce SAML— y falso desde el
día siguiente. Con una sola configuración SAML viva, el ``downgrade`` moría con

    ERROR: column "client_id" of relation "sso_configurations" contains null values

un mensaje que no nombra SAML, no dice qué borrar y deja la bajada a medias:
exactamente lo contrario de «data loss is explicit». Y choca de frente con la
regla dura de ``CLAUDE.md`` (no desplegar sin comprobar que las migraciones son
reversibles), porque la base de datos de producción SÍ tiene datos.

Se sigue el patrón que ya usa el resto de la cadena cuando una bajada no puede
representar un dato: **borrarlo y decirlo**, no abortar a mitad —
``0113_notification_log_content`` (``DELETE FROM notification_log_reads WHERE
tenant_id IS NULL``), ``0115_sso_multi_provider`` (borra soft-borradas y
duplicadas de ESTA MISMA tabla, y se ejecuta ANTES que esta bajada) y
``0137_partition_executions`` (borra hijas huérfanas). La pérdida es real y
está anotada arriba: al bajar de 0033 el esquema destino **no tiene columnas
donde guardar una configuración SAML**, así que conservarla no es una opción;
la alternativa honesta a borrarla es no poder bajar.

Revision ID: 0033_sso_saml_columns
Revises: 0032_sso_configurations
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033_sso_saml_columns"
down_revision: str | Sequence[str] | None = "0032_sso_configurations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SAML-specific identity-provider columns.
    op.add_column(
        "sso_configurations",
        sa.Column("idp_entity_id", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "sso_configurations",
        sa.Column("idp_sso_url", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "sso_configurations",
        sa.Column("idp_x509_cert", sa.Text(), nullable=True),
    )
    op.add_column(
        "sso_configurations",
        sa.Column(
            "name_id_format",
            sa.String(length=128),
            nullable=False,
            server_default=sa.text("'urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress'"),
        ),
    )
    op.add_column(
        "sso_configurations",
        sa.Column(
            "attribute_mappings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    # OIDC-only columns become nullable so a `saml` row can omit them.
    op.alter_column(
        "sso_configurations",
        "issuer",
        existing_type=sa.String(length=512),
        nullable=True,
    )
    op.alter_column(
        "sso_configurations",
        "client_id",
        existing_type=sa.String(length=255),
        nullable=True,
    )

    # Per-provider shape invariants (one CHECK, branching on provider):
    #   * oidc -> issuer + client_id required (the code exchange needs them).
    #   * saml -> idp_entity_id + idp_sso_url + idp_x509_cert required.
    op.create_check_constraint(
        "ck_sso_config_provider_shape",
        "sso_configurations",
        "(provider <> 'oidc' OR (issuer IS NOT NULL AND client_id IS NOT NULL))"
        " AND (provider <> 'saml' OR (idp_entity_id IS NOT NULL"
        " AND idp_sso_url IS NOT NULL AND idp_x509_cert IS NOT NULL))",
    )


def downgrade() -> None:
    op.drop_constraint("ck_sso_config_provider_shape", "sso_configurations", type_="check")

    # Las filas que NO CABEN en el esquema de 0032 — las SAML, que por
    # definición no llevan `issuer` ni `client_id`. Se filtran por el dato y no
    # por `provider = 'saml'` a propósito: lo que impide restaurar el NOT NULL
    # es el NULL, venga del provider que venga (0115 añadió más providers
    # simultáneos por tenant). Así la condición dice exactamente lo que la
    # siguiente línea necesita.
    op.execute("DELETE FROM sso_configurations WHERE client_id IS NULL OR issuer IS NULL")

    # Restore the OIDC NOT NULL constraints.
    op.alter_column(
        "sso_configurations",
        "client_id",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.alter_column(
        "sso_configurations",
        "issuer",
        existing_type=sa.String(length=512),
        nullable=False,
    )
    op.drop_column("sso_configurations", "attribute_mappings")
    op.drop_column("sso_configurations", "name_id_format")
    op.drop_column("sso_configurations", "idp_x509_cert")
    op.drop_column("sso_configurations", "idp_sso_url")
    op.drop_column("sso_configurations", "idp_entity_id")
