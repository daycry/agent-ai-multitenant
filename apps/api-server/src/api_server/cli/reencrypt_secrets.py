"""Bulk re-encryption of the at-rest secrets onto the head key (task_prod05_02).

This is step 2 of the three-step rotation that :mod:`api_server.auth.crypto_keys`
makes possible:

  1. add the new key at the HEAD of ``*_ENCRYPTION_KEYS`` → deploy. Every stored
     ciphertext still decrypts (the old key is in the tail); new writes are on the
     new key.
  2. **run this command.** Every ciphertext still on an older key is decrypted and
     re-encrypted onto the head key.
  3. remove the old key from the tail → deploy. Now the old key is genuinely
     retired, and nothing depends on it.

Skipping step 2 is the interesting failure: the deployment looks healthy for
months (both keys decrypt) and then step 3 silently destroys every row nobody
touched in between. So the command's most important output is not the writing —
it is the ``--dry-run`` count that tells the operator whether step 3 is safe yet.

Design notes that are load-bearing:

* **"Already on the head key" is measured, not assumed.** A Fernet token carries
  no key id, so the only way to know which key produced one is to try that key
  ALONE (:func:`api_server.auth.crypto_keys.primary_fernet`). Without that we
  could only report "rotated N rows", which is both false on a second run and
  useless as a readiness signal. With it, a converged deployment reports
  ``migrated=0`` and the operator knows step 3 is safe.

* **Unreadable rows do NOT abort the run.** A row encrypted with a key that is no
  longer in the ring is already lost; refusing to migrate the other 9 999 rows
  because of it would turn one lost secret into a failed rotation. They are
  counted and listed BY ID for manual treatment (re-enter the OIDC secret,
  re-enrol the TOTP seed, re-issue the webhook secret).

* **BYPASSRLS on purpose.** The command runs on the admin engine
  (``service_user``: BYPASSRLS, no DDL) because it must see every tenant's rows;
  a tenant-bound session would silently migrate a subset and report success. This
  is the same engine the ``/admin/*`` surface uses, and the reason the operation
  is a container-shell command rather than an endpoint.

* **Identifiers are never interpolated from input.** Table/column names come from
  the frozen :data:`TARGETS` tuple and are validated against a strict identifier
  pattern before they reach any SQL string, so the ``--tables`` filter can only
  ever SELECT from that tuple, never extend it.

No secret is ever logged. The report carries table names, counts and row ids.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field

import structlog
from cryptography.fernet import InvalidToken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.crypto_keys import build_multifernet, primary_fernet
from api_server.config import Settings

_log = structlog.get_logger("api_server.cli.reencrypt_secrets")

#: Postgres identifiers we are willing to put in a SQL string. Deliberately
#: narrower than Postgres allows: everything in :data:`TARGETS` is a plain
#: lower-snake name, so anything else is a bug worth failing on.
_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")


@dataclass(frozen=True)
class SecretColumn:
    """One (table, column) pair holding Fernet ciphertext, and its key family.

    ``family`` selects the KEY RING: the four at-rest families have independent
    rings (``sso``, ``mfa``, ``notification``, ``incoming_webhook``), so a
    rotation of one must not attempt to re-encrypt another's rows onto the wrong
    key.
    """

    table: str
    column: str
    family: str

    def __post_init__(self) -> None:
        for identifier in (self.table, self.column):
            if not _IDENTIFIER.fullmatch(identifier):
                raise ValueError(f"unsafe SQL identifier in a re-encryption target: {identifier!r}")


#: Every column in the platform that holds Fernet ciphertext, with the ring that
#: owns it. Adding a Fernet column anywhere without adding it here means that
#: column is silently left behind by every rotation —
#: ``tests/integration/test_reencrypt_secrets_command.py`` pins the inventory
#: against the ORM metadata so a new ``*_encrypted`` Text column fails the suite.
TARGETS: tuple[SecretColumn, ...] = (
    # OIDC client secret + SAML SP private key. The table is platform-GLOBAL
    # (ADR 0047): no tenant_id, one row per provider.
    SecretColumn("sso_configurations", "client_secret_encrypted", "sso"),
    SecretColumn("sso_configurations", "sp_private_key_encrypted", "sso"),
    # TOTP seeds. Its own family since ADR 0143 — see auth/mfa/secrets.py for why
    # coupling it to the SSO key was a System-Admin lockout waiting to happen.
    SecretColumn("user_mfa_totp", "secret_encrypted", "mfa"),
    # Notification channel secrets. The api-server WRITES these and the
    # notification-dispatcher READS them, so this column is the one whose
    # rotation needs BOTH services deployed in the same window.
    SecretColumn("notification_channels", "secret_encrypted", "notification"),
    # Incoming-webhook HMAC signing secrets. Unrecoverable if lost: the clear
    # value was shown to the operator once and pasted into GitHub/Jira.
    SecretColumn("incoming_webhook_configs", "signing_secret_encrypted", "incoming_webhook"),
)


@dataclass
class ColumnReport:
    """Per-column outcome. Counts and row ids only — never a secret."""

    table: str
    column: str
    family: str
    #: Rows with a non-NULL ciphertext (the only ones this command looks at).
    total: int = 0
    #: Already encrypted with the HEAD key — nothing to do, and the signal that
    #: tells the operator the rotation has converged.
    already_on_head: int = 0
    #: Decrypted with an older key in the ring and re-encrypted onto the head
    #: (or, under ``--dry-run``, the rows that WOULD be).
    migrated: int = 0
    #: Ids of rows no key in the ring can decrypt. Already lost; listed so an
    #: operator can re-enter them by hand.
    unreadable: list[str] = field(default_factory=list)

    def as_log_fields(self) -> dict[str, object]:
        return {
            "table": self.table,
            "column": self.column,
            "family": self.family,
            "total": self.total,
            "already_on_head": self.already_on_head,
            "migrated": self.migrated,
            "unreadable": len(self.unreadable),
        }


@dataclass
class ReencryptReport:
    """Whole-run outcome: one :class:`ColumnReport` per column touched."""

    dry_run: bool
    columns: list[ColumnReport] = field(default_factory=list)

    @property
    def migrated(self) -> int:
        return sum(c.migrated for c in self.columns)

    @property
    def already_on_head(self) -> int:
        return sum(c.already_on_head for c in self.columns)

    @property
    def unreadable(self) -> int:
        return sum(len(c.unreadable) for c in self.columns)

    @property
    def work_remaining(self) -> int:
        """Rows still on a NON-head key AFTER this invocation.

        Zero for an applied run (they were rewritten); equal to ``migrated`` for a
        dry run (nothing was written). Spelled out as its own property because
        "migrated" answers a different question and conflating the two is how a
        dry run gets read as "nothing to do".
        """
        return self.migrated if self.dry_run else 0

    @property
    def safe_to_retire_previous_key(self) -> bool:
        """The operator's actual question: can step 3 of the rotation proceed?

        True when no READABLE ciphertext depends on a non-head key any more.
        Unreadable rows are excluded on purpose — they depend on a key that is
        already gone, so keeping the previous key in the ring would not save them;
        they need the secret re-entered by hand.
        """
        return self.work_remaining == 0

    def render(self) -> str:
        """Human-readable table for the operator running the command."""
        mode = "DRY RUN (nothing written)" if self.dry_run else "APPLIED"
        lines = [f"reencrypt-secrets — {mode}", ""]
        header = f"{'table.column':<52}{'total':>7}{'head':>7}{'moved':>7}{'unread':>8}"
        lines.append(header)
        lines.append("-" * len(header))
        for column in self.columns:
            lines.append(
                f"{column.table + '.' + column.column:<52}"
                f"{column.total:>7}{column.already_on_head:>7}"
                f"{column.migrated:>7}{len(column.unreadable):>8}"
            )
        lines.append("-" * len(header))
        lines.append(
            f"{'TOTAL':<52}"
            f"{sum(c.total for c in self.columns):>7}"
            f"{self.already_on_head:>7}{self.migrated:>7}{self.unreadable:>8}"
        )
        if self.unreadable:
            lines.append("")
            lines.append(
                "UNREADABLE rows — no key in the ring decrypts them. They are "
                "already lost; the secret must be re-entered by hand:"
            )
            for column in self.columns:
                for row_id in column.unreadable:
                    lines.append(f"  {column.table}.{column.column} id={row_id}")
        lines.append("")
        if self.dry_run and self.migrated:
            lines.append(
                "Nothing was written. Re-run without --dry-run to migrate the "
                f"{self.migrated} row(s) above."
            )
        elif self.safe_to_retire_previous_key:
            lines.append(
                "Every readable ciphertext is on the head key. Retiring the "
                "previous key from the *_KEYS list is now safe for these columns."
            )
        return "\n".join(lines)


def rings_from_settings(settings: Settings) -> Mapping[str, tuple[str, ...]]:
    """Map each at-rest family to its ordered key ring.

    Reading the rings from ``Settings`` (rather than taking keys as CLI
    arguments) is deliberate: the command must re-encrypt onto the SAME head key
    the running api-server encrypts with. A key passed on the command line could
    disagree with the deployed configuration, and the operator would only find
    out at the next read.
    """
    return {
        "sso": settings.sso_encryption_key_ring,
        "mfa": settings.mfa_encryption_key_ring,
        "notification": settings.notification_encryption_key_ring,
        "incoming_webhook": settings.incoming_webhook_encryption_key_ring,
    }


def select_targets(
    *,
    tables: Collection[str] | None = None,
    families: Collection[str] | None = None,
) -> tuple[SecretColumn, ...]:
    """Filter :data:`TARGETS`. An unknown name is an error, never a silent no-op.

    A typo in ``--tables user_mfa_toto`` that quietly re-encrypted nothing and
    exited 0 is exactly how an operator concludes "the rotation is done" and then
    drops the old key.
    """
    selected = TARGETS
    if tables is not None:
        known = {target.table for target in TARGETS}
        unknown = sorted(set(tables) - known)
        if unknown:
            raise ValueError(
                f"unknown table(s): {', '.join(unknown)}. Known: {', '.join(sorted(known))}"
            )
        selected = tuple(t for t in selected if t.table in tables)
    if families is not None:
        known_families = {target.family for target in TARGETS}
        unknown = sorted(set(families) - known_families)
        if unknown:
            raise ValueError(
                f"unknown family(ies): {', '.join(unknown)}. "
                f"Known: {', '.join(sorted(known_families))}"
            )
        selected = tuple(t for t in selected if t.family in families)
    if not selected:
        raise ValueError("the table/family filters selected no column")
    return selected


async def reencrypt_column(
    session: AsyncSession,
    target: SecretColumn,
    ring: Sequence[str],
    *,
    dry_run: bool,
    batch_size: int = 200,
) -> ColumnReport:
    """Re-encrypt one column onto the head key of ``ring``.

    Idempotent: a second run finds every row already on the head key and writes
    nothing. Batched with a commit per batch so an interruption leaves a
    consistent, *readable* mixed state — which is safe precisely because every key
    in the ring decrypts (the whole premise of the ring).
    """
    report = ColumnReport(table=target.table, column=target.column, family=target.family)
    cipher = build_multifernet(ring)
    head = primary_fernet(ring)

    rows = (
        await session.execute(
            text(
                f"SELECT id, {target.column} AS ciphertext "  # - identifiers validated
                f"FROM {target.table} WHERE {target.column} IS NOT NULL ORDER BY id"
            )
        )
    ).all()
    report.total = len(rows)

    pending: list[tuple[str, str]] = []
    for row in rows:
        row_id = str(row.id)
        raw = str(row.ciphertext).encode("ascii", errors="ignore")
        try:
            head.decrypt(raw)
        except (InvalidToken, ValueError):
            pass
        else:
            report.already_on_head += 1
            continue
        try:
            rotated = cipher.rotate(raw).decode("ascii")
        except (InvalidToken, ValueError):
            # No key in the ring reads it. Do NOT abort: the other rows are
            # migratable and this one is already unrecoverable.
            report.unreadable.append(row_id)
            continue
        report.migrated += 1
        pending.append((row_id, rotated))

        if not dry_run and len(pending) >= batch_size:
            await _flush(session, target, pending)
            pending.clear()

    if not dry_run and pending:
        await _flush(session, target, pending)

    _log.info("reencrypt_secrets.column", dry_run=dry_run, **report.as_log_fields())
    return report


async def _flush(
    session: AsyncSession,
    target: SecretColumn,
    pending: Sequence[tuple[str, str]],
) -> None:
    """Write one batch and commit it. Values are BOUND, never interpolated."""
    for row_id, ciphertext in pending:
        await session.execute(
            text(f"UPDATE {target.table} SET {target.column} = :ciphertext WHERE id = :row_id"),
            {"ciphertext": ciphertext, "row_id": row_id},
        )
    await session.commit()


async def reencrypt_secrets(
    session: AsyncSession,
    *,
    settings: Settings,
    dry_run: bool = True,
    tables: Collection[str] | None = None,
    families: Collection[str] | None = None,
    batch_size: int = 200,
) -> ReencryptReport:
    """Re-encrypt every selected column onto its family's head key.

    ``dry_run`` defaults to True on purpose: the safe call is the one you make by
    accident.
    """
    rings = rings_from_settings(settings)
    report = ReencryptReport(dry_run=dry_run)
    for target in select_targets(tables=tables, families=families):
        report.columns.append(
            await reencrypt_column(
                session,
                target,
                rings[target.family],
                dry_run=dry_run,
                batch_size=batch_size,
            )
        )
    _log.info(
        "reencrypt_secrets.completed",
        dry_run=dry_run,
        migrated=report.migrated,
        already_on_head=report.already_on_head,
        unreadable=report.unreadable,
        work_remaining=report.work_remaining,
        safe_to_retire_previous_key=report.safe_to_retire_previous_key,
    )
    return report


__all__ = [
    "TARGETS",
    "ColumnReport",
    "ReencryptReport",
    "SecretColumn",
    "reencrypt_column",
    "reencrypt_secrets",
    "rings_from_settings",
    "select_targets",
]
