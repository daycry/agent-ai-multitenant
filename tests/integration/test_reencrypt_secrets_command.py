"""`reencrypt-secrets` against the real tables (prod-05 task_prod05_02).

The command is the MIDDLE step of an at-rest key rotation, and the reason it must
be tested against real rows is that its output is what an operator uses to decide
whether step 3 — deleting the previous key — is safe. A command that reported
"done" while leaving rows behind would not merely be wrong; it would be the
instrument of the data loss.

What is asserted here, in order of how badly each would hurt:

1. **After the run, the OLD key is genuinely unnecessary.** Not "the roundtrip
   still works with both keys" (that is true before the run too) — the ciphertext
   is decrypted with the NEW KEY ALONE.
2. **A dry run writes nothing** and still counts correctly. This is the readiness
   signal; if it lied in the safe direction the operator would retire a live key.
3. **Idempotence**: the second run migrates 0 and says the retirement is safe.
4. **An unreadable row does not abort the run** — the other rows still migrate,
   and the lost one is reported BY ID.
5. **The inventory is complete**: every Fernet-ciphertext column in the ORM
   metadata is in ``TARGETS``. A column missing from that tuple is a column every
   future rotation silently skips.

Pre-condition: the docker-compose postgres is healthy. The session fixtures
create a throwaway DB (``TEST_PG_DB_NAME``) and drop it afterwards.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.auth.crypto_keys import build_multifernet, derive_fernet_key
from api_server.cli.reencrypt_secrets import (
    TARGETS,
    reencrypt_secrets,
    rings_from_settings,
    select_targets,
)
from api_server.config import Settings
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

# Material de clave FALSO y sin forma PEM a propósito: el código bajo prueba
# solo ve bytes opacos (cifra y descifra), así que la forma `-----BEGIN ...-----`
# no aportaba nada y disparaba el hook `detect-private-key` — que hace bien en
# ser estricto. No le devuelvas el envoltorio PEM "por realismo".
_FAKE_SP_KEY_MATERIAL = "sp-key-material::sp"

_OLD = "the-key-being-retired-0123456789abcdefgh"
_NEW = "the-key-taking-over-zyxwvu9876543210abcd"
_LOST = "a-key-that-was-dropped-without-reencrypti"


# ---------------------------------------------------------------------------
# Fixtures: a migrated throwaway DB + a session on the BYPASSRLS engine
# ---------------------------------------------------------------------------
@pytest.fixture()
def migrated_db(alembic_config: object, migrations_pg_dsn: str) -> Iterator[str]:
    """Upgrade the throwaway DB to head and yield a plain asyncpg DSN."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    yield migrations_pg_dsn


@pytest.fixture()
def session_factory(
    migrated_db: str, admin_database_url: str
) -> Iterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(admin_database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        asyncio.run(engine.dispose())


def _settings(*, sso: str, mfa: str | None = None) -> Settings:
    """Dev settings whose SSO/MFA/notification/webhook rings are all ``sso``.

    One ring for every family keeps the seeding simple; the family SPLIT is
    asserted separately (``test_only_the_selected_family_is_touched``).
    """
    return Settings(
        environment="dev",
        sso_encryption_keys=sso,
        notification_encryption_keys=sso,
        incoming_webhook_encryption_keys=sso,
        mfa_encryption_keys=mfa or "",
    )


def _encrypt(raw_key: str, plaintext: str) -> str:
    return Fernet(derive_fernet_key(raw_key)).encrypt(plaintext.encode()).decode("ascii")


# ---------------------------------------------------------------------------
# Seeding — raw SQL as the BYPASSRLS role, one row per target column
# ---------------------------------------------------------------------------
#: The tables this module seeds. Truncated before each seed because the
#: throwaway DB is SESSION-scoped and shared by the whole integration suite: the
#: first version of this file leaked rows between its own tests and every count
#: assertion drifted (total=4 where 1 was seeded). Same pattern as
#: ``test_saml_crypto._truncate_all``.
_SEEDED_TABLES = (
    "incoming_webhook_configs",
    "notification_channels",
    "user_mfa_totp",
    "sso_configurations",
    "projects",
    "users",
    "organizations",
)


async def _truncate(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f"TRUNCATE {', '.join(_SEEDED_TABLES)} RESTART IDENTITY CASCADE")
    finally:
        await conn.close()


async def _seed(dsn: str, *, key: str) -> dict[str, UUID]:
    """Insert one row per re-encryptable column, all encrypted with ``key``."""
    await _truncate(dsn)
    ids = {
        "sso": uuid4(),
        "saml": uuid4(),
        "user": uuid4(),
        "mfa": uuid4(),
        "channel": uuid4(),
        "project": uuid4(),
        "webhook": uuid4(),
    }
    tenant_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        # A real tenant row: `user_mfa_totp.tenant_id` and `projects.tenant_id`
        # carry FKs to `organizations` (migration 0124).
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            f"Rotation Tenant {tenant_id}",
            f"rot-{str(tenant_id)[:8]}",
        )
        # `ck_sso_config_provider_shape` forces an oidc row to carry
        # issuer+client_id and a saml row to carry the three idp_* columns, so the
        # two ciphertext columns of this table live on two different rows.
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, provider, display_name, enabled, issuer, client_id,
                 client_secret_encrypted)
            VALUES ($1, 'oidc', 'Acme OIDC', true, 'https://idp.test', 'acme-client',
                    $2)
            """,
            ids["sso"],
            _encrypt(key, "oidc-client-secret"),
        )
        await conn.execute(
            """
            INSERT INTO sso_configurations
                (id, provider, display_name, enabled, idp_entity_id, idp_sso_url,
                 idp_x509_cert, sp_private_key_encrypted)
            VALUES ($1, 'saml', 'Acme SAML', true, 'urn:acme:idp',
                    'https://idp.test/sso', 'MIIB-fake-cert', $2)
            """,
            ids["saml"],
            _encrypt(key, _FAKE_SP_KEY_MATERIAL),
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'x')",
            ids["user"],
            f"rot-{ids['user']}@example.test",
        )
        await conn.execute(
            """
            INSERT INTO user_mfa_totp (id, tenant_id, user_id, secret_encrypted)
            VALUES ($1, $2, $3, $4)
            """,
            ids["mfa"],
            tenant_id,
            ids["user"],
            _encrypt(key, "JBSWY3DPEHPK3PXP"),
        )
        await conn.execute(
            """
            INSERT INTO notification_channels
                (id, scope, channel_type, name, tenant_id, secret_encrypted)
            VALUES ($1, 'tenant', 'slack', 'Ops Slack', $2, $3)
            """,
            ids["channel"],
            tenant_id,
            _encrypt(key, "xoxb-bot-token"),
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, 'Rotation Project')",
            ids["project"],
            tenant_id,
        )
        await conn.execute(
            """
            INSERT INTO incoming_webhook_configs
                (id, tenant_id, project_id, origin, name, signing_secret_encrypted)
            VALUES ($1, $2, $3, 'github', 'CI hook', $4)
            """,
            ids["webhook"],
            tenant_id,
            ids["project"],
            _encrypt(key, "github-hmac-signing-secret"),
        )
    finally:
        await conn.close()
    return ids


async def _read(dsn: str, table: str, column: str, row_id: UUID) -> str:
    conn = await asyncpg.connect(dsn)
    try:
        value = await conn.fetchval(
            f"SELECT {column} FROM {table} WHERE id = $1",
            row_id,  # - test-local
        )
    finally:
        await conn.close()
    return str(value)


# ---------------------------------------------------------------------------
# 1. The point of the command
# ---------------------------------------------------------------------------
def test_after_the_run_the_retired_key_is_no_longer_needed(
    migrated_db: str, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """THE assertion. Decrypts with the NEW KEY ALONE — with both keys in hand the
    test would pass before the command ran and prove nothing."""
    ids = asyncio.run(_seed(migrated_db, key=_OLD))

    async def run() -> None:
        async with session_factory() as session:
            report = await reencrypt_secrets(
                session, settings=_settings(sso=f"{_NEW},{_OLD}"), dry_run=False
            )
        assert report.migrated == 5, report.render()
        assert report.unreadable == 0
        assert report.safe_to_retire_previous_key is True

    asyncio.run(run())

    new_key_only = Fernet(derive_fernet_key(_NEW))
    checks = [
        ("sso_configurations", "client_secret_encrypted", ids["sso"], b"oidc-client-secret"),
        ("user_mfa_totp", "secret_encrypted", ids["mfa"], b"JBSWY3DPEHPK3PXP"),
        ("notification_channels", "secret_encrypted", ids["channel"], b"xoxb-bot-token"),
        (
            "incoming_webhook_configs",
            "signing_secret_encrypted",
            ids["webhook"],
            b"github-hmac-signing-secret",
        ),
    ]
    for table, column, row_id, expected in checks:
        stored = asyncio.run(_read(migrated_db, table, column, row_id))
        assert new_key_only.decrypt(stored.encode()) == expected, f"{table}.{column}"

    # ...and the OLD key alone can no longer read them, which is what makes
    # deleting it from the list a safe operation rather than a gamble.
    old_key_only = Fernet(derive_fernet_key(_OLD))
    stored = asyncio.run(_read(migrated_db, "user_mfa_totp", "secret_encrypted", ids["mfa"]))
    with pytest.raises(InvalidToken):
        old_key_only.decrypt(stored.encode())


def test_a_dry_run_counts_the_work_and_writes_nothing(
    migrated_db: str, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The readiness signal. If it under-counted, an operator would retire a key
    the platform still needs; if it wrote, ``--dry-run`` would be a lie."""
    ids = asyncio.run(_seed(migrated_db, key=_OLD))
    before = asyncio.run(_read(migrated_db, "user_mfa_totp", "secret_encrypted", ids["mfa"]))

    async def run() -> None:
        async with session_factory() as session:
            report = await reencrypt_secrets(
                session, settings=_settings(sso=f"{_NEW},{_OLD}"), dry_run=True
            )
        assert report.dry_run is True
        assert report.migrated == 5, report.render()
        assert report.already_on_head == 0
        # ...and the dry run must NOT claim the retirement is safe.
        assert report.work_remaining == 5
        assert report.safe_to_retire_previous_key is False
        assert "DRY RUN" in report.render()

    asyncio.run(run())

    after = asyncio.run(_read(migrated_db, "user_mfa_totp", "secret_encrypted", ids["mfa"]))
    assert after == before, "--dry-run wrote to the database"


def test_the_second_run_is_a_no_op_and_clears_the_retirement(
    migrated_db: str, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Idempotence, and the signal the runbook branches on: ``migrated == 0``
    is what tells the operator step 3 is safe."""
    asyncio.run(_seed(migrated_db, key=_OLD))

    async def run() -> None:
        settings = _settings(sso=f"{_NEW},{_OLD}")
        async with session_factory() as session:
            first = await reencrypt_secrets(session, settings=settings, dry_run=False)
            assert first.migrated == 5
        async with session_factory() as session:
            second = await reencrypt_secrets(session, settings=settings, dry_run=False)
        assert second.migrated == 0, second.render()
        assert second.already_on_head == 5
        assert second.safe_to_retire_previous_key is True
        assert "now safe" in second.render()

    asyncio.run(run())


def test_an_unreadable_row_is_reported_by_id_and_does_not_abort_the_run(
    migrated_db: str, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """One lost secret must not cost the operator the whole rotation."""
    ids = asyncio.run(_seed(migrated_db, key=_OLD))

    # A second channel encrypted with a key that is NOT in the ring — the shape
    # of a row left behind by a previous rotation that skipped step 2.
    orphan = uuid4()

    async def seed_orphan() -> None:
        conn = await asyncpg.connect(migrated_db)
        try:
            await conn.execute(
                """
                INSERT INTO notification_channels
                    (id, scope, channel_type, name, secret_encrypted)
                VALUES ($1, 'platform', 'slack', 'Orphaned', $2)
                """,
                orphan,
                _encrypt(_LOST, "unrecoverable"),
            )
        finally:
            await conn.close()

    asyncio.run(seed_orphan())

    async def run() -> None:
        async with session_factory() as session:
            report = await reencrypt_secrets(
                session, settings=_settings(sso=f"{_NEW},{_OLD}"), dry_run=False
            )
        assert report.unreadable == 1, report.render()
        # Every other row still migrated.
        assert report.migrated == 5
        assert str(orphan) in report.render()
        # An unreadable row does NOT block step 3: the key that wrote it is
        # already gone, so keeping the previous key would not save it.
        assert report.safe_to_retire_previous_key is True

    asyncio.run(run())

    # The readable rows moved even though a sibling row in the SAME table failed.
    stored = asyncio.run(
        _read(migrated_db, "notification_channels", "secret_encrypted", ids["channel"])
    )
    assert Fernet(derive_fernet_key(_NEW)).decrypt(stored.encode()) == b"xoxb-bot-token"


def test_only_the_selected_family_is_touched(
    migrated_db: str, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """ADR 0143 in practice: rotating the MFA key must leave the SSO rows alone.

    The rings differ here, so touching the wrong table would produce ciphertext
    the running api-server cannot read.
    """
    ids = asyncio.run(_seed(migrated_db, key=_OLD))
    sso_before = asyncio.run(
        _read(migrated_db, "sso_configurations", "client_secret_encrypted", ids["sso"])
    )

    async def run() -> None:
        async with session_factory() as session:
            report = await reencrypt_secrets(
                session,
                settings=_settings(sso=_OLD, mfa=f"{_NEW},{_OLD}"),
                dry_run=False,
                families=["mfa"],
            )
        assert report.migrated == 1, report.render()
        assert [c.table for c in report.columns] == ["user_mfa_totp"]

    asyncio.run(run())

    assert (
        asyncio.run(_read(migrated_db, "sso_configurations", "client_secret_encrypted", ids["sso"]))
        == sso_before
    ), "an SSO row was rewritten by an MFA-only rotation"
    mfa_stored = asyncio.run(_read(migrated_db, "user_mfa_totp", "secret_encrypted", ids["mfa"]))
    assert Fernet(derive_fernet_key(_NEW)).decrypt(mfa_stored.encode()) == b"JBSWY3DPEHPK3PXP"


def test_a_typo_in_the_table_filter_is_an_error_not_a_silent_no_op() -> None:
    """ "Re-encrypted 0 rows, exit 0" is how an operator concludes the rotation is
    finished and drops a key that was still in use."""
    with pytest.raises(ValueError, match="unknown table"):
        select_targets(tables=["user_mfa_toto"])
    with pytest.raises(ValueError, match="unknown family"):
        select_targets(families=["sso-typo"])


# ---------------------------------------------------------------------------
# 5. The inventory cannot silently fall behind the schema
# ---------------------------------------------------------------------------
def test_every_fernet_column_in_the_schema_is_a_reencryption_target() -> None:
    """A Fernet column absent from ``TARGETS`` is skipped by EVERY rotation, and
    nothing would ever say so — the row simply becomes unreadable the day the key
    is retired.

    Discovers columns from the ORM metadata (named ``*_encrypted``, of a text
    type) and asserts it found some, so the guard cannot pass on an empty scan.
    """
    import api_server.db.models
    import api_server.db.notification  # noqa: F401
    from api_server.db.base import Base
    from sqlalchemy import Text

    discovered: set[tuple[str, str]] = set()
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if column.name.endswith("_encrypted") and isinstance(column.type, Text):
                discovered.add((table.name, column.name))

    assert len(discovered) >= 5, f"the discovery stopped finding columns: {discovered}"
    declared = {(target.table, target.column) for target in TARGETS}
    missing = sorted(discovered - declared)
    assert not missing, (
        "these Fernet-ciphertext columns are not in TARGETS, so every key "
        f"rotation silently skips them: {missing}"
    )


def test_every_target_family_has_a_ring() -> None:
    """A target whose family has no ring would explode with a KeyError mid-run,
    after having already committed earlier batches."""
    rings = rings_from_settings(_settings(sso=_OLD))
    for target in TARGETS:
        assert target.family in rings, target
        assert rings[target.family], target


def test_an_unsafe_identifier_cannot_become_a_target() -> None:
    """The table/column names reach a SQL string, so the type refuses anything
    that is not a plain identifier."""
    from api_server.cli.reencrypt_secrets import SecretColumn

    with pytest.raises(ValueError, match="unsafe SQL identifier"):
        SecretColumn("users; DROP TABLE users", "secret_encrypted", "sso")
    with pytest.raises(ValueError, match="unsafe SQL identifier"):
        SecretColumn("users", 'secret_encrypted" , "x', "sso")


def test_the_ring_reads_what_the_previous_key_wrote() -> None:
    """Sanity check on the fixture helpers themselves: if ``_encrypt`` did not
    produce ciphertext the ring can read, every test above would pass vacuously
    on the "unreadable" path."""
    token = _encrypt(_OLD, "payload")
    assert build_multifernet((_NEW, _OLD)).decrypt(token.encode()) == b"payload"
