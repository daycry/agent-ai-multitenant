"""Versioning + updates: semver compare/ordering + the update path (task_09_12).

Two layers, both honest about the capability gaps the rest of Plan 09
already documented:

  * **Pure semver logic** (no DB): version compare/ordering, outdated
    detection, the major-bump compatibility rule, and
    :func:`select_update_target` (no auto-major-jump without the explicit
    opt-in). These run anywhere.

  * **The update path against REAL Postgres + RLS**: drives
    :meth:`api_server.marketplace.install.InstallOrchestrator.update`
    end-to-end the same way ``test_install_flow.py`` drives ``install`` —
    the artifact fetch is a :class:`LocalArtifactFetcher` over an on-disk
    fixture (no network), the sandbox's Docker client is MOCKED, and a
    verified update's signature is verified with REAL :mod:`cryptography`.
    It pins:
      - an outdated install updates to the newer compatible version, the
        install row is re-pointed, and an ``update`` audit row is written
        carrying the version diff + the re-run gate trail;
      - the update RE-RUNS the gates: a tampered new-version artifact is
        REJECTED (``SignatureVerificationError``), the install stays on its
        old version, and an abort audit row is committed;
      - a MAJOR-version bump is not auto-selected — the compatibility rule
        only proposes it with the explicit opt-in;
      - cross-tenant (``@pytest.mark.cross_tenant``): tenant B's RLS-scoped
        session never sees tenant A's update / audit rows.

The orchestrator manages its own transaction (it COMMITS abort audit rows),
so — exactly like ``test_install_flow.py`` — these tests open a tenant-scoped
session WITHOUT an outer ``session.begin()`` and set the RLS GUCs as
session-level config so they survive the orchestrator's intermediate commits.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

# Register the ORM tables the marketplace FKs reference before we flush.
from api_server.db import domain as _domain  # noqa: F401
from api_server.db import marketplace as _marketplace  # noqa: F401
from api_server.db import models as _models  # noqa: F401
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

_PG_HOST = os.environ.get("TEST_PG_HOST", "localhost")
_PG_PORT = int(os.environ.get("TEST_PG_PORT", "15432"))


# ===========================================================================
# Layer 1 — pure semver logic (no DB)
# ===========================================================================
from api_server.marketplace.versioning import (  # noqa: E402
    VersioningError,
    compare_versions,
    is_major_bump,
    is_outdated,
    latest_version,
    parse_version,
    select_update_target,
)


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("1.0.0", "1.0.0", 0),
        ("1.0.0", "1.0.1", -1),
        ("1.2.0", "1.10.0", -1),  # numeric, not lexicographic
        ("2.0.0", "1.9.9", 1),
        ("1.0.0-alpha", "1.0.0", -1),  # prerelease < release
        ("1.0.0-alpha.1", "1.0.0-alpha.2", -1),
    ],
)
def test_compare_versions_orders_semver(left: str, right: str, expected: int) -> None:
    assert compare_versions(left, right) == expected


def test_compare_versions_rejects_non_semver() -> None:
    # "1.2" is leniently accepted by packaging but is NOT strict semver.
    with pytest.raises(VersioningError):
        compare_versions("1.2", "1.2.0")
    with pytest.raises(VersioningError):
        parse_version("not-a-version")


def test_is_outdated() -> None:
    assert is_outdated("1.0.0", "1.0.1") is True
    assert is_outdated("1.0.1", "1.0.0") is False
    assert is_outdated("1.0.0", "1.0.0") is False


def test_is_major_bump() -> None:
    assert is_major_bump("1.4.0", "2.0.0") is True
    assert is_major_bump("1.4.0", "1.9.0") is False
    # A downgrade across a major is not an (upward) major bump.
    assert is_major_bump("2.0.0", "1.0.0") is False


def test_latest_version() -> None:
    assert latest_version(["1.0.0", "2.3.1", "0.9.0", "2.3.0"]) == "2.3.1"
    assert latest_version([]) is None


def test_select_update_target_minor_bump_no_optin() -> None:
    """Without opt-in, target is the highest SAME-MAJOR newer version."""
    a = select_update_target("1.2.0", ["1.0.0", "1.2.0", "1.5.0", "1.9.0", "2.0.0"])
    assert a.target_version == "1.9.0"
    assert a.latest_version == "2.0.0"
    assert a.outdated is True
    assert a.update_available is True
    assert a.latest_is_major_bump is True


def test_select_update_target_major_requires_optin() -> None:
    """When the only newer version is a major bump, no target without opt-in."""
    no_optin = select_update_target("1.2.0", ["1.0.0", "1.2.0", "2.0.0"])
    assert no_optin.target_version is None  # gated
    assert no_optin.outdated is True  # a newer version DOES exist
    assert no_optin.update_available is False
    assert no_optin.latest_is_major_bump is True

    with_optin = select_update_target("1.2.0", ["1.0.0", "1.2.0", "2.0.0"], allow_major=True)
    assert with_optin.target_version == "2.0.0"
    assert with_optin.update_available is True


def test_select_update_target_up_to_date() -> None:
    a = select_update_target("2.0.0", ["1.0.0", "1.5.0", "2.0.0"])
    assert a.target_version is None
    assert a.outdated is False
    assert a.update_available is False
    assert a.latest_version == "2.0.0"


# ===========================================================================
# Layer 2 — the update path against real Postgres + RLS
# ===========================================================================
def _generate_keypair() -> tuple[bytes, object]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return public_pem, private


def _sign(private_key: object, manifest_text: str) -> str:
    sig = private_key.sign(manifest_text.encode("utf-8"))  # type: ignore[attr-defined]
    return sig.hex()


def _skill_md(name: str, version: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        "description: A clean, signed skill.\n"
        f"version: {version}\n"
        "---\n"
        "\n"
        "# Skill\n"
    )


def _write_artifact(
    root: Path, listing_id: UUID, manifest_name: str, manifest_text: str, *, signature: str | None
) -> None:
    listing_dir = root / str(listing_id)
    listing_dir.mkdir(parents=True, exist_ok=True)
    (listing_dir / manifest_name).write_text(manifest_text, encoding="utf-8")
    if signature is not None:
        (listing_dir / f"{manifest_name}.sig").write_text(signature, encoding="utf-8")


def _fake_docker_client(*, exit_code: int = 0):
    from unittest.mock import MagicMock

    def _run(image: str, **kwargs):
        c = MagicMock()
        c.id = "sandbox-0"
        c.exec_run = MagicMock(return_value=MagicMock(exit_code=exit_code, output=(b"ok\n", b"")))
        return c

    client = MagicMock()
    client.containers.run.side_effect = _run
    network = MagicMock()
    network.name = "marketplace-sandbox-test"
    client.networks.create.return_value = network
    return client


def _sandbox(*, exit_code: int = 0):
    from api_server.marketplace.sandbox import MarketplaceSandbox

    return MarketplaceSandbox(client=_fake_docker_client(exit_code=exit_code))


def _orchestrator(root: Path, public_key_pem: bytes, *, sandbox=None):
    from api_server.marketplace.install import InstallOrchestrator, LocalArtifactFetcher

    return InstallOrchestrator(
        fetcher=LocalArtifactFetcher(root_dir=str(root)),
        public_key_pem=public_key_pem,
        sandbox=sandbox,
    )


def _actor(user_id: UUID) -> str:
    return f"user:{user_id}"


async def _seed(dsn: str) -> dict[str, UUID]:
    """Two verified skill versions (1.0.0 + 1.1.0 + 2.0.0) of one logical
    listing, in the global catalog, plus two tenants/admins."""
    ids = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "admin_a": uuid4(),
        "admin_b": uuid4(),
        "source": uuid4(),
        "v1_0_0": uuid4(),
        "v1_1_0": uuid4(),
        "v2_0_0": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE marketplace_audit_entries, marketplace_installations,"
            " marketplace_listings, marketplace_sources,"
            " projects, agents, teams, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-ver",
            ids["tenant_b"],
            "Tenant B",
            "tenant-b-ver",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["admin_a"],
            "admin-a@ver.test",
            "h",
            ids["admin_b"],
            "admin-b@ver.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'), ($4, $5, $6, 'tenant_admin')",
            uuid4(),
            ids["tenant_a"],
            ids["admin_a"],
            uuid4(),
            ids["tenant_b"],
            ids["admin_b"],
        )
        await conn.execute(
            "INSERT INTO marketplace_sources (id, name, source_type)"
            " VALUES ($1, 'official-catalog', 'official')",
            ids["source"],
        )
        # Three versions of the SAME logical listing (same source+name+kind,
        # global tenant). trust_level verified so the signature gate runs.
        await conn.execute(
            # Manifest materializable (remediación 2026-07-17): un skill
            # listing sin prompt_fragment ya no instala (422).
            "INSERT INTO marketplace_listings"
            " (id, source_id, tenant_id, kind, name, version, trust_level, signature, manifest)"
            " VALUES"
            " ($1, $4, NULL, 'skill', 'evolving-skill', '1.0.0', 'verified', 'sig-placeholder',"
            '  \'{"prompt_fragment": "usa el estilo v1"}\'::jsonb),'
            " ($2, $4, NULL, 'skill', 'evolving-skill', '1.1.0', 'verified', 'sig-placeholder',"
            '  \'{"prompt_fragment": "usa el estilo v1.1"}\'::jsonb),'
            " ($3, $4, NULL, 'skill', 'evolving-skill', '2.0.0', 'verified', 'sig-placeholder',"
            '  \'{"prompt_fragment": "usa el estilo v2"}\'::jsonb)',
            ids["v1_0_0"],
            ids["v1_1_0"],
            ids["v2_0_0"],
            ids["source"],
        )
    finally:
        await conn.close()
    return ids


@asynccontextmanager
async def _tenant_session(
    app_database_url: str, *, user_id: UUID, tenant_id: UUID
) -> AsyncIterator[AsyncSession]:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(app_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()
    try:
        await session.execute(
            text("SELECT set_config('app.user_id', :uid, false)"), {"uid": str(user_id)}
        )
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, false)"), {"tid": str(tenant_id)}
        )
        yield session
    finally:
        await session.close()
        await engine.dispose()


async def _load_listing(session: AsyncSession, listing_id: UUID):
    from api_server.db.marketplace import MarketplaceListing

    result = await session.execute(
        select(MarketplaceListing).where(MarketplaceListing.id == listing_id)
    )
    return result.scalar_one()


async def _install_version(
    session: AsyncSession,
    orch,
    *,
    tenant_id: UUID,
    actor: str,
    installed_by: UUID,
    listing,
):
    """Install a starting version so we have something to update from."""
    result = await orch.install(
        session=session,
        tenant_id=tenant_id,
        actor=actor,
        listing=listing,
        installed_by=installed_by,
    )
    await session.commit()
    return result.installation


async def _count_audit(dsn: str, tenant_id: UUID, *, action: str | None = None) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        if action is None:
            row = await conn.fetchrow(
                "SELECT count(*) AS n FROM marketplace_audit_entries WHERE tenant_id = $1",
                tenant_id,
            )
        else:
            row = await conn.fetchrow(
                "SELECT count(*) AS n FROM marketplace_audit_entries"
                " WHERE tenant_id = $1 AND action = $2",
                tenant_id,
                action,
            )
        return int(row["n"])
    finally:
        await conn.close()


async def _count_update_audit(dsn: str, tenant_id: UUID, *, aborted: bool) -> int:
    """Count ``update``-action audit rows, split by whether they aborted.

    A successful update row has no ``aborted`` key (treated as false); an
    aborted one carries ``"aborted": true``."""
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT count(*) AS n FROM marketplace_audit_entries"
            " WHERE tenant_id = $1 AND action = 'update'"
            " AND COALESCE((detail->>'aborted')::bool, false) = $2",
            tenant_id,
            aborted,
        )
        return int(row["n"])
    finally:
        await conn.close()


async def _installed_version(dsn: str, installation_id: UUID) -> str:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT version FROM marketplace_installations WHERE id = $1", installation_id
        )
        return str(row["version"])
    finally:
        await conn.close()


@pytest.fixture()
def migrated_db(alembic_config, test_redis_url: str):
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    yield


# ---------------------------------------------------------------------------
# Outdated install updates to the newer compatible version + audits
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_to_newer_compatible_version_repoints_and_audits(
    migrated_db, app_database_url: str, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    public_pem, private = _generate_keypair()
    md_1_0_0 = _skill_md("evolving-skill", "1.0.0")
    md_1_1_0 = _skill_md("evolving-skill", "1.1.0")
    _write_artifact(
        tmp_path, seeded["v1_0_0"], "SKILL.md", md_1_0_0, signature=_sign(private, md_1_0_0)
    )
    _write_artifact(
        tmp_path, seeded["v1_1_0"], "SKILL.md", md_1_1_0, signature=_sign(private, md_1_1_0)
    )

    async with _tenant_session(
        app_database_url, user_id=seeded["admin_a"], tenant_id=seeded["tenant_a"]
    ) as session:
        orch = _orchestrator(tmp_path, public_pem)
        v1 = await _load_listing(session, seeded["v1_0_0"])
        installation = await _install_version(
            session,
            orch,
            tenant_id=seeded["tenant_a"],
            actor=_actor(seeded["admin_a"]),
            installed_by=seeded["admin_a"],
            listing=v1,
        )
        assert installation.version == "1.0.0"

        # Now update to 1.1.0 (a compatible minor bump).
        target = await _load_listing(session, seeded["v1_1_0"])
        result = await orch.update(
            session=session,
            tenant_id=seeded["tenant_a"],
            actor=_actor(seeded["admin_a"]),
            installation=installation,
            target_listing=target,
        )
        assert result.installation.version == "1.1.0"
        assert result.installation.listing_id == seeded["v1_1_0"]
        assert result.gate_report["update"] == {"from_version": "1.0.0", "to_version": "1.1.0"}
        assert result.gate_report["signature_verified"] is True
        await session.commit()

    assert await _installed_version(migrations_pg_dsn, installation.id) == "1.1.0"
    # install + update audit rows (both non-aborted).
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], action="install") == 1
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], action="update") == 1


# ---------------------------------------------------------------------------
# The update RE-RUNS the gates: a tampered new version is rejected
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_reruns_gates_and_rejects_tampered_new_version(
    migrated_db, app_database_url: str, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    from api_server.marketplace.install import SignatureVerificationError

    seeded = await _seed(migrations_pg_dsn)
    public_pem, private = _generate_keypair()
    md_1_0_0 = _skill_md("evolving-skill", "1.0.0")
    _write_artifact(
        tmp_path, seeded["v1_0_0"], "SKILL.md", md_1_0_0, signature=_sign(private, md_1_0_0)
    )
    # The 1.1.0 artifact is signed over the pristine text but written TAMPERED.
    md_1_1_0 = _skill_md("evolving-skill", "1.1.0")
    good_sig = _sign(private, md_1_1_0)
    tampered = md_1_1_0.replace("clean, signed skill", "tampered skill")
    _write_artifact(tmp_path, seeded["v1_1_0"], "SKILL.md", tampered, signature=good_sig)

    async with _tenant_session(
        app_database_url, user_id=seeded["admin_a"], tenant_id=seeded["tenant_a"]
    ) as session:
        orch = _orchestrator(tmp_path, public_pem)
        v1 = await _load_listing(session, seeded["v1_0_0"])
        installation = await _install_version(
            session,
            orch,
            tenant_id=seeded["tenant_a"],
            actor=_actor(seeded["admin_a"]),
            installed_by=seeded["admin_a"],
            listing=v1,
        )
        target = await _load_listing(session, seeded["v1_1_0"])
        with pytest.raises(SignatureVerificationError):
            await orch.update(
                session=session,
                tenant_id=seeded["tenant_a"],
                actor=_actor(seeded["admin_a"]),
                installation=installation,
                target_listing=target,
            )

    # The install stays on its OLD version; the failed update wrote an
    # ABORT audit row under the ``update`` action (so the trail records the
    # blocked update), but NO successful (non-aborted) update row exists.
    assert await _installed_version(migrations_pg_dsn, installation.id) == "1.0.0"
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], action="install") == 1
    assert await _count_update_audit(migrations_pg_dsn, seeded["tenant_a"], aborted=True) == 1
    assert await _count_update_audit(migrations_pg_dsn, seeded["tenant_a"], aborted=False) == 0


# ---------------------------------------------------------------------------
# The compatibility rule gates the major bump (logic, against seeded versions)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_major_bump_requires_opt_in_against_seeded_versions(
    migrated_db, app_database_url: str, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    # The available versions for the logical listing are 1.0.0, 1.1.0, 2.0.0.
    async with _tenant_session(
        app_database_url, user_id=seeded["admin_a"], tenant_id=seeded["tenant_a"]
    ) as session:
        from api_server.db.marketplace import MarketplaceListing

        rows = await session.execute(
            select(MarketplaceListing.version).where(MarketplaceListing.name == "evolving-skill")
        )
        versions = [r[0] for r in rows.all()]

    # Installed at 1.0.0 → without opt-in the target is 1.1.0 (the major 2.0.0
    # is gated); with opt-in the target is 2.0.0.
    no_optin = select_update_target("1.0.0", versions)
    assert no_optin.target_version == "1.1.0"
    assert no_optin.latest_is_major_bump is True

    with_optin = select_update_target("1.0.0", versions, allow_major=True)
    assert with_optin.target_version == "2.0.0"


# ---------------------------------------------------------------------------
# Cross-tenant isolation of the update + its audit rows
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_update_and_audit_are_tenant_isolated(
    migrated_db, app_database_url: str, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    from api_server.db.marketplace import MarketplaceAuditEntry, MarketplaceInstallation
    from sqlalchemy import func

    seeded = await _seed(migrations_pg_dsn)
    public_pem, private = _generate_keypair()
    md_1_0_0 = _skill_md("evolving-skill", "1.0.0")
    md_1_1_0 = _skill_md("evolving-skill", "1.1.0")
    _write_artifact(
        tmp_path, seeded["v1_0_0"], "SKILL.md", md_1_0_0, signature=_sign(private, md_1_0_0)
    )
    _write_artifact(
        tmp_path, seeded["v1_1_0"], "SKILL.md", md_1_1_0, signature=_sign(private, md_1_1_0)
    )

    async with _tenant_session(
        app_database_url, user_id=seeded["admin_a"], tenant_id=seeded["tenant_a"]
    ) as session_a:
        orch = _orchestrator(tmp_path, public_pem)
        v1 = await _load_listing(session_a, seeded["v1_0_0"])
        installation = await _install_version(
            session_a,
            orch,
            tenant_id=seeded["tenant_a"],
            actor=_actor(seeded["admin_a"]),
            installed_by=seeded["admin_a"],
            listing=v1,
        )
        target = await _load_listing(session_a, seeded["v1_1_0"])
        await orch.update(
            session=session_a,
            tenant_id=seeded["tenant_a"],
            actor=_actor(seeded["admin_a"]),
            installation=installation,
            target_listing=target,
        )
        await session_a.commit()

    # Tenant B sees NONE of tenant A's install / audit rows.
    async with _tenant_session(
        app_database_url, user_id=seeded["admin_b"], tenant_id=seeded["tenant_b"]
    ) as session_b:
        installs_b = await session_b.execute(
            select(func.count()).select_from(MarketplaceInstallation)
        )
        audits_b = await session_b.execute(select(func.count()).select_from(MarketplaceAuditEntry))
        assert installs_b.scalar_one() == 0
        assert audits_b.scalar_one() == 0

    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], action="update") == 1
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_b"]) == 0


# ===========================================================================
# Layer 3 — the REST surface (update-check GET + update POST)
# ===========================================================================
from httpx import ASGITransport, AsyncClient  # noqa: E402
from uuid6 import uuid7  # noqa: E402


@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()

    from api_server.main import create_app

    app = create_app()
    try:
        yield app
    finally:
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_http(dsn: str) -> dict[str, UUID]:
    """Like ``_seed`` but adds a plain member in tenant A (for the RBAC test)."""
    ids = await _seed(dsn)
    ids["member_a"] = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            ids["member_a"],
            "member-a@ver.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_user')",
            uuid4(),
            ids["tenant_a"],
            ids["member_a"],
        )
    finally:
        await conn.close()
    return ids


async def _install_via_api(client: AsyncClient, listing_id: UUID, headers: dict[str, str]) -> dict:
    """Install a starting version through the Phase A endpoint (no orchestrator)."""
    resp = await client.post(
        "/marketplace/installations",
        json={"listing_id": str(listing_id)},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _override_orchestrator(app, root: Path, public_key_pem: bytes) -> None:
    """Point the route's orchestrator at the on-disk fixture + test key.

    The live fetcher reads ``default_artifact_root()`` and the signing key
    from the environment; in the test we inject a :class:`LocalArtifactFetcher`
    over ``tmp_path`` (no network) so the update POST exercises the real
    pipeline against fixture artifacts."""
    from api_server.marketplace.install import InstallOrchestrator, LocalArtifactFetcher
    from api_server.routers.marketplace import get_install_orchestrator

    def _factory() -> InstallOrchestrator:
        return InstallOrchestrator(
            fetcher=LocalArtifactFetcher(root_dir=str(root)),
            public_key_pem=public_key_pem,
        )

    app.dependency_overrides[get_install_orchestrator] = _factory


@pytest.mark.asyncio
async def test_update_check_reports_outdated_and_gates_major(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed_http(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        installed = await _install_via_api(client, seeded["v1_0_0"], headers)
        # Default check: outdated, target is the same-major 1.1.0, the 2.0.0
        # major bump is flagged but NOT proposed.
        resp = await client.get(
            f"/marketplace/installations/{installed['id']}/update-check", headers=headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["installed_version"] == "1.0.0"
        assert body["latest_version"] == "2.0.0"
        assert body["target_version"] == "1.1.0"
        assert body["outdated"] is True
        assert body["update_available"] is True
        assert body["latest_is_major_bump"] is True

        # With the explicit opt-in, the target becomes the major 2.0.0.
        resp_major = await client.get(
            f"/marketplace/installations/{installed['id']}/update-check?allow_major=true",
            headers=headers,
        )
        assert resp_major.json()["target_version"] == "2.0.0"


@pytest.mark.asyncio
async def test_update_post_repoints_to_compatible_version(
    configured_app, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    seeded = await _seed_http(migrations_pg_dsn)
    public_pem, private = _generate_keypair()
    md_1_1_0 = _skill_md("evolving-skill", "1.1.0")
    _write_artifact(
        tmp_path, seeded["v1_1_0"], "SKILL.md", md_1_1_0, signature=_sign(private, md_1_1_0)
    )
    _override_orchestrator(configured_app, tmp_path, public_pem)

    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        installed = await _install_via_api(client, seeded["v1_0_0"], headers)
        resp = await client.post(
            f"/marketplace/installations/{installed['id']}/update",
            json={},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["from_version"] == "1.0.0"
        assert body["to_version"] == "1.1.0"
        assert body["installation"]["version"] == "1.1.0"
        assert body["installation"]["listing_id"] == str(seeded["v1_1_0"])

    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], action="update") == 1


@pytest.mark.asyncio
async def test_update_post_major_bump_without_optin_is_409(
    configured_app, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    seeded = await _seed_http(migrations_pg_dsn)
    public_pem, _ = _generate_keypair()
    _override_orchestrator(configured_app, tmp_path, public_pem)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        installed = await _install_via_api(client, seeded["v1_0_0"], headers)
        # Pin the major 2.0.0 WITHOUT opt-in -> 409 (no orchestrator run).
        resp = await client.post(
            f"/marketplace/installations/{installed['id']}/update",
            json={"target_version": "2.0.0"},
            headers=headers,
        )
        assert resp.status_code == 409, resp.text
        assert "major" in resp.json()["detail"].lower()

    # No update audit row at all (the gate did not even run).
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], action="update") == 0


@pytest.mark.asyncio
async def test_update_post_requires_tenant_admin(
    configured_app, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    seeded = await _seed_http(migrations_pg_dsn)
    public_pem, _ = _generate_keypair()
    _override_orchestrator(configured_app, tmp_path, public_pem)

    admin_token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    member_token = await _mint_token(seeded["member_a"], seeded["tenant_a"])

    async with _client(configured_app) as client:
        installed = await _install_via_api(
            client, seeded["v1_0_0"], {"Authorization": f"Bearer {admin_token}"}
        )
        # A plain tenant_user cannot perform an update.
        resp = await client.post(
            f"/marketplace/installations/{installed['id']}/update",
            json={},
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert resp.status_code == 403, resp.text


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_update_check_cross_tenant_is_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed_http(migrations_pg_dsn)
    admin_token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    admin_b_token = await _mint_token(seeded["admin_b"], seeded["tenant_b"])

    async with _client(configured_app) as client:
        installed = await _install_via_api(
            client, seeded["v1_0_0"], {"Authorization": f"Bearer {admin_token}"}
        )
        # Tenant B cannot see tenant A's install -> 404.
        resp = await client.get(
            f"/marketplace/installations/{installed['id']}/update-check",
            headers={"Authorization": f"Bearer {admin_b_token}"},
        )
        assert resp.status_code == 404, resp.text
