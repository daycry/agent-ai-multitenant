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

Reversible: ``downgrade`` drops the SAML CHECK + columns and restores
the OIDC NOT NULL constraints (no ``saml`` rows can exist at downgrade
time because the table only held OIDC rows before this revision; the
backfill is a no-op for any future saml rows, which downgrade removes
implicitly is NOT done — instead we hard-require the operator to drop
saml rows first, matching Alembic's "data loss is explicit" stance).

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
    # Restore the OIDC NOT NULL constraints. Any pre-existing rows are
    # OIDC and always have these set; saml rows (added by this revision)
    # must be removed by the operator before downgrading.
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
