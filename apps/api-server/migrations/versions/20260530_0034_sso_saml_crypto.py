"""SAML SP signing/encryption config on sso_configurations (Plan 08 task_08_05).

task_08_04 shipped the SAML flow with the security-critical *inbound*
direction covered: the SP verifies the IdP's assertion signature against
``idp_x509_cert``. This revision adds the *outbound* / optional crypto a
real enterprise IdP often requires:

  * **Request signing** — sign the SP ``AuthnRequest`` with the SP key so
    the IdP can authenticate the request (``authn_requests_signed``).
  * **Assertion / NameID encryption** — let the IdP encrypt the assertion
    (or just the NameID) to the SP public cert; the SP decrypts with its
    private key (``want_assertions_encrypted`` / ``want_name_id_encrypted``).
  * **Per-config ``want_assertions_signed``** — task_08_04 hard-coded this
    to true in the flow settings; promote it to a column so an operator
    can express the policy (it still defaults to true — turning it off is
    a deliberate, audited choice).

SP key material (CLAUDE.md: no plaintext secrets in the DB). The SP
private key is stored in EXACTLY ONE of two forms, never both, never in
clear text — mirroring the OIDC ``client_secret`` columns:

  * ``sp_private_key_ref``        — a Vault pointer (``vault:...``).
  * ``sp_private_key_encrypted``  — Fernet ciphertext (encrypted at rest
                                    with the SSO encryption key).

The SP public cert (``sp_x509_cert``) is not secret (the IdP needs it),
so it is stored as plaintext PEM/base64 like ``idp_x509_cert``.

Two CHECK constraints guard the invariants:
  * never both SP private-key forms set ("at most one source").
  * if any SP-key-requiring feature is on (request signing OR assertion/
    NameID encryption), the SP cert AND a private-key source must be set
    — so the flow can never be configured into a state where it must sign
    or decrypt but has no key.

Reversible: ``downgrade`` drops the two CHECKs then the five columns. No
data migration is needed — existing rows simply gain the new columns at
their server defaults (request-signing off, encryption off, assertions
signed), i.e. the exact behaviour task_08_04 hard-coded.

Revision ID: 0034_sso_saml_crypto
Revises: 0033_sso_saml_columns
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_sso_saml_crypto"
down_revision: str | Sequence[str] | None = "0033_sso_saml_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SP key material — same two-form, never-plaintext shape as the OIDC
    # client secret. The public cert is not secret (plaintext PEM/base64).
    op.add_column(
        "sso_configurations",
        sa.Column("sp_x509_cert", sa.Text(), nullable=True),
    )
    op.add_column(
        "sso_configurations",
        sa.Column("sp_private_key_ref", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "sso_configurations",
        sa.Column("sp_private_key_encrypted", sa.Text(), nullable=True),
    )

    # Per-config security policy flags (all map onto python3-saml settings).
    op.add_column(
        "sso_configurations",
        sa.Column(
            "authn_requests_signed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "sso_configurations",
        sa.Column(
            "want_assertions_signed",
            sa.Boolean(),
            nullable=False,
            # task_08_04 hard-coded this true; keep that as the default so
            # existing saml rows behave identically after the upgrade.
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "sso_configurations",
        sa.Column(
            "want_assertions_encrypted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "sso_configurations",
        sa.Column(
            "want_name_id_encrypted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # At most one SP private-key source (mirrors the OIDC secret CHECK).
    op.create_check_constraint(
        "ck_sso_config_single_sp_key_source",
        "sso_configurations",
        "NOT (sp_private_key_ref IS NOT NULL AND sp_private_key_encrypted IS NOT NULL)",
    )
    # If any SP-key-requiring feature is enabled, the SP must have a cert
    # AND a private key — so the flow can never need to sign/decrypt with
    # no key. (A row that enables none of these needs no SP key at all.)
    op.create_check_constraint(
        "ck_sso_config_sp_key_when_crypto",
        "sso_configurations",
        "(authn_requests_signed = false"
        " AND want_assertions_encrypted = false"
        " AND want_name_id_encrypted = false)"
        " OR (sp_x509_cert IS NOT NULL"
        " AND (sp_private_key_ref IS NOT NULL OR sp_private_key_encrypted IS NOT NULL))",
    )


def downgrade() -> None:
    op.drop_constraint("ck_sso_config_sp_key_when_crypto", "sso_configurations", type_="check")
    op.drop_constraint("ck_sso_config_single_sp_key_source", "sso_configurations", type_="check")
    op.drop_column("sso_configurations", "want_name_id_encrypted")
    op.drop_column("sso_configurations", "want_assertions_encrypted")
    op.drop_column("sso_configurations", "want_assertions_signed")
    op.drop_column("sso_configurations", "authn_requests_signed")
    op.drop_column("sso_configurations", "sp_private_key_encrypted")
    op.drop_column("sso_configurations", "sp_private_key_ref")
    op.drop_column("sso_configurations", "sp_x509_cert")
