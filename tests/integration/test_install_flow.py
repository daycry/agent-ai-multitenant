"""Integration tests for the full install pipeline (Plan 09 task_09_11).

Drives :class:`api_server.marketplace.install.InstallOrchestrator`
end-to-end against the REAL Postgres + RLS, with the two capability-gapped
collaborators handled honestly:

  * the artifact fetch is a :class:`LocalArtifactFetcher` over an on-disk
    fixture tree — no real network (the xmlsec / semgrep / Docker
    precedent);
  * the sandbox's Docker client is MOCKED (a real container run is pending
    the sandbox runtime image);
  * the SIGNATURE is verified with REAL :mod:`cryptography` — the test
    generates an Ed25519 keypair, signs a fixture manifest, and the
    orchestrator verifies it; a TAMPERED artifact is rejected.

What it pins (the binding task requirements):

  * a verified, signed, clean artifact INSTALLS enabled + writes an
    ``install`` audit row carrying the gate trail;
  * a verified artifact whose manifest was TAMPERED after signing is
    REJECTED (``SignatureVerificationError``) with an abort audit row and NO
    install;
  * an unsigned verified artifact is rejected the same way;
  * a HIGH-severity artifact is BLOCKED by static analysis (abort audit, no
    install);
  * a failing sandbox smoke check blocks the install (abort audit, no
    install);
  * a community artifact installs DISABLED, awaiting per-permission consent
    (NOT an abort — the install row exists);
  * EACH abort writes a tenant-scoped audit entry;
  * cross-tenant (``@pytest.mark.cross_tenant``): an install + its audit
    rows are visible only to the installing tenant; tenant B's RLS-scoped
    session sees none of tenant A's install/audit rows.

The orchestrator manages its own transaction (it COMMITS abort audit rows so
they survive the raised error), so the tests open a tenant-scoped session
that sets the same RLS GUCs ``open_tenant_session`` sets, WITHOUT an outer
``session.begin()`` — mirroring how the install flow owns its commits.
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

# Register the ORM tables the marketplace FKs reference (users live in
# ``db.models``; projects in ``db.domain``) before we flush an installation
# row — otherwise SQLAlchemy raises NoReferencedTableError resolving
# ``marketplace_installations.project_id -> projects.id`` /
# ``installed_by -> users.id``.
from api_server.db import domain as _domain  # noqa: F401
from api_server.db import marketplace as _marketplace  # noqa: F401
from api_server.db import models as _models  # noqa: F401
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

# App role (NOBYPASSRLS) — matches tests/integration/conftest.py defaults.
_PG_HOST = os.environ.get("TEST_PG_HOST", "localhost")
_PG_PORT = int(os.environ.get("TEST_PG_PORT", "15432"))


# ---------------------------------------------------------------------------
# Signing helpers — real Ed25519 keypair (cryptography is a project dep)
# ---------------------------------------------------------------------------
def _generate_keypair() -> tuple[bytes, bytes]:
    """Return ``(public_pem, private_key)`` — a fresh Ed25519 keypair."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return public_pem, private  # type: ignore[return-value]


def _sign(private_key: object, manifest_text: str) -> str:
    """Sign ``manifest_text`` and return the detached signature as hex."""
    sig = private_key.sign(manifest_text.encode("utf-8"))  # type: ignore[attr-defined]
    return sig.hex()


# ---------------------------------------------------------------------------
# Fixture manifests
# ---------------------------------------------------------------------------
_VERIFIED_SKILL_MD = (
    "---\n"
    "name: clean-skill\n"
    "description: A clean, signed skill.\n"
    "version: 1.0.0\n"
    "---\n"
    "\n"
    "# Clean Skill\n"
    "\n"
    "Does nothing dangerous.\n"
)

_CLEAN_TOOL_PY = (
    "import json\n" "\n" "def parse(raw: str) -> dict:\n" "    return json.loads(raw)\n"
)

_INSECURE_TOOL_PY = (
    "import subprocess\n"
    "\n"
    "def run(user_input):\n"
    "    eval(user_input)\n"
    "    subprocess.call(user_input, shell=True)\n"
)

_COMMUNITY_SKILL_MD = (
    "---\n"
    "name: community-skill\n"
    "description: A community skill requesting a network permission.\n"
    "version: 2.1.0\n"
    "permissions:\n"
    "  allowed_domains: [api.x.com]\n"
    "  network_policy: restricted\n"
    "---\n"
    "\n"
    "# Community Skill\n"
)


def _write_artifact(
    root: Path, listing_id: UUID, manifest_name: str, manifest_text: str, *, signature: str | None
) -> None:
    """Lay an artifact onto disk the way LocalArtifactFetcher expects."""
    listing_dir = root / str(listing_id)
    listing_dir.mkdir(parents=True, exist_ok=True)
    (listing_dir / manifest_name).write_text(manifest_text, encoding="utf-8")
    if signature is not None:
        (listing_dir / f"{manifest_name}.sig").write_text(signature, encoding="utf-8")


# ---------------------------------------------------------------------------
# Mock Docker client for the sandbox (a real daemon is pending the image)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# DB seeding (BYPASSRLS migrations role) + tenant-scoped app session
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "admin_a": uuid4(),
        "admin_b": uuid4(),
        "source": uuid4(),
        "verified_clean": uuid4(),
        "verified_tampered": uuid4(),
        "high_severity": uuid4(),
        "sandbox_fail": uuid4(),
        "community": uuid4(),
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
            "tenant-a-install",
            ids["tenant_b"],
            "Tenant B",
            "tenant-b-install",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["admin_a"],
            "admin-a@install.test",
            "h",
            ids["admin_b"],
            "admin-b@install.test",
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
        # Four verified listings + one community. trust_level drives the gates.
        await conn.execute(
            "INSERT INTO marketplace_listings"
            " (id, source_id, tenant_id, kind, name, version, trust_level, signature)"
            " VALUES"
            " ($1, $6, NULL, 'skill', 'clean-skill', '1.0.0', 'verified', 'sig-placeholder'),"
            " ($2, $6, NULL, 'skill', 'tampered-skill', '1.0.0', 'verified', 'sig-placeholder'),"
            " ($3, $6, NULL, 'tool', 'evil-tool', '1.0.0', 'verified', 'sig-placeholder'),"
            " ($4, $6, NULL, 'tool', 'sandbox-fail-tool', '1.0.0', 'community', NULL),"
            " ($5, $6, NULL, 'skill', 'community-skill', '2.1.0', 'community', NULL)",
            ids["verified_clean"],
            ids["verified_tampered"],
            ids["high_severity"],
            ids["sandbox_fail"],
            ids["community"],
            ids["source"],
        )
        # The community listing requests one network permission so a
        # consent-gated install lands disabled with it pending.
        await conn.execute(
            "UPDATE marketplace_listings SET requested_permissions ="
            ' \'[{"type": "allowed_domains", "value": ["api.x.com"]}]\'::jsonb'
            " WHERE id = $1",
            ids["community"],
        )
    finally:
        await conn.close()
    return ids


@asynccontextmanager
async def _tenant_session(
    app_database_url: str, *, user_id: UUID, tenant_id: UUID
) -> AsyncIterator[AsyncSession]:
    """A NOBYPASSRLS app-role session with the RLS GUCs set, like the app.

    No outer ``session.begin()`` — the install orchestrator owns its
    transaction (it commits abort audit rows). We set ``app.user_id`` /
    ``app.tenant_id`` as session-level config (not ``is_local``) so they
    persist across the orchestrator's intermediate commits.
    """
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
    from sqlalchemy import select

    result = await session.execute(
        select(MarketplaceListing).where(MarketplaceListing.id == listing_id)
    )
    return result.scalar_one()


async def _count_audit(dsn: str, tenant_id: UUID, *, aborted: bool | None = None) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        if aborted is None:
            row = await conn.fetchrow(
                "SELECT count(*) AS n FROM marketplace_audit_entries WHERE tenant_id = $1",
                tenant_id,
            )
        else:
            # A successful install has no ``aborted`` key, so treat its
            # absence as not-aborted (COALESCE to false) before comparing.
            row = await conn.fetchrow(
                "SELECT count(*) AS n FROM marketplace_audit_entries"
                " WHERE tenant_id = $1 AND COALESCE((detail->>'aborted')::bool, false) = $2",
                tenant_id,
                aborted,
            )
        return int(row["n"])
    finally:
        await conn.close()


async def _count_installs(dsn: str, tenant_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT count(*) AS n FROM marketplace_installations WHERE tenant_id = $1",
            tenant_id,
        )
        return int(row["n"])
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Fixture: migrate the DB + grant app_user (mirrors the other suites)
# ---------------------------------------------------------------------------
@pytest.fixture()
def migrated_db(alembic_config, test_redis_url: str):
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    yield


def _orchestrator(root: Path, public_key_pem: bytes, *, sandbox=None):
    from api_server.marketplace.install import InstallOrchestrator, LocalArtifactFetcher

    return InstallOrchestrator(
        fetcher=LocalArtifactFetcher(root_dir=str(root)),
        public_key_pem=public_key_pem,
        sandbox=sandbox,
    )


def _actor(user_id: UUID) -> str:
    return f"user:{user_id}"


# ===========================================================================
# Happy path: verified signed clean artifact installs + audits
# ===========================================================================
@pytest.mark.asyncio
async def test_verified_clean_signed_artifact_installs_and_audits(
    migrated_db, app_database_url: str, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    public_pem, private = _generate_keypair()
    _write_artifact(
        tmp_path,
        seeded["verified_clean"],
        "SKILL.md",
        _VERIFIED_SKILL_MD,
        signature=_sign(private, _VERIFIED_SKILL_MD),
    )

    async with _tenant_session(
        app_database_url, user_id=seeded["admin_a"], tenant_id=seeded["tenant_a"]
    ) as session:
        listing = await _load_listing(session, seeded["verified_clean"])
        result = await _orchestrator(tmp_path, public_pem).install(
            session=session,
            tenant_id=seeded["tenant_a"],
            actor=_actor(seeded["admin_a"]),
            listing=listing,
            installed_by=seeded["admin_a"],
        )
        assert result.enabled is True
        assert result.installation.status == "enabled"
        # The gate trail recorded signature verification + a clean scan.
        assert result.gate_report["signature_verified"] is True
        assert result.gate_report["static_analysis"]["blocked"] is False
        await session.commit()

    assert await _count_installs(migrations_pg_dsn, seeded["tenant_a"]) == 1
    # Exactly one (non-aborted) install audit row.
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], aborted=False) == 1
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], aborted=True) == 0


# ===========================================================================
# Tampered signature is rejected
# ===========================================================================
@pytest.mark.asyncio
async def test_tampered_signature_is_rejected(
    migrated_db, app_database_url: str, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    from api_server.marketplace.install import SignatureVerificationError

    seeded = await _seed(migrations_pg_dsn)
    public_pem, private = _generate_keypair()
    # Sign the PRISTINE manifest, then write a TAMPERED one to disk.
    good_sig = _sign(private, _VERIFIED_SKILL_MD)
    tampered = _VERIFIED_SKILL_MD.replace("Does nothing dangerous.", "Does something dangerous.")
    _write_artifact(tmp_path, seeded["verified_tampered"], "SKILL.md", tampered, signature=good_sig)

    async with _tenant_session(
        app_database_url, user_id=seeded["admin_a"], tenant_id=seeded["tenant_a"]
    ) as session:
        listing = await _load_listing(session, seeded["verified_tampered"])
        with pytest.raises(SignatureVerificationError):
            await _orchestrator(tmp_path, public_pem).install(
                session=session,
                tenant_id=seeded["tenant_a"],
                actor=_actor(seeded["admin_a"]),
                listing=listing,
                installed_by=seeded["admin_a"],
            )

    # No install; exactly one abort audit row (committed despite the raise).
    assert await _count_installs(migrations_pg_dsn, seeded["tenant_a"]) == 0
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], aborted=True) == 1


@pytest.mark.asyncio
async def test_unsigned_verified_artifact_is_rejected(
    migrated_db, app_database_url: str, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    from api_server.marketplace.install import SignatureVerificationError

    seeded = await _seed(migrations_pg_dsn)
    public_pem, _ = _generate_keypair()
    # Verified listing but NO .sig on disk.
    _write_artifact(
        tmp_path, seeded["verified_clean"], "SKILL.md", _VERIFIED_SKILL_MD, signature=None
    )

    async with _tenant_session(
        app_database_url, user_id=seeded["admin_a"], tenant_id=seeded["tenant_a"]
    ) as session:
        listing = await _load_listing(session, seeded["verified_clean"])
        with pytest.raises(SignatureVerificationError):
            await _orchestrator(tmp_path, public_pem).install(
                session=session,
                tenant_id=seeded["tenant_a"],
                actor=_actor(seeded["admin_a"]),
                listing=listing,
                installed_by=seeded["admin_a"],
            )

    assert await _count_installs(migrations_pg_dsn, seeded["tenant_a"]) == 0
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], aborted=True) == 1


# ===========================================================================
# High-severity artifact is blocked by static analysis
# ===========================================================================
@pytest.mark.asyncio
async def test_high_severity_artifact_is_blocked(
    migrated_db, app_database_url: str, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    from api_server.marketplace.install import StaticAnalysisBlockedError
    from api_server.marketplace.static_analysis import _bandit_executable

    if _bandit_executable() is None:
        pytest.skip("bandit not installed in this environment")

    seeded = await _seed(migrations_pg_dsn)
    public_pem, private = _generate_keypair()
    # A verified tool whose source is insecure: eval + shell=True. The
    # manifest itself is clean + correctly signed, so the analysis gate (not
    # the signature gate) is what blocks.
    tool_yaml = (
        "name: evil-tool\n"
        "version: 1.0.0\n"
        "description: Looks fine, scans dirty.\n"
        "kind: tool\n"
        "entrypoint: tool:run\n"
        "implementation:\n"
        "  runtime: python\n"
    )
    listing_dir = tmp_path / str(seeded["high_severity"])
    listing_dir.mkdir(parents=True)
    (listing_dir / "tool.yaml").write_text(tool_yaml, encoding="utf-8")
    (listing_dir / "tool.yaml.sig").write_text(_sign(private, tool_yaml), encoding="utf-8")
    (listing_dir / "impl.py").write_text(_INSECURE_TOOL_PY, encoding="utf-8")

    async with _tenant_session(
        app_database_url, user_id=seeded["admin_a"], tenant_id=seeded["tenant_a"]
    ) as session:
        listing = await _load_listing(session, seeded["high_severity"])
        with pytest.raises(StaticAnalysisBlockedError):
            await _orchestrator(tmp_path, public_pem).install(
                session=session,
                tenant_id=seeded["tenant_a"],
                actor=_actor(seeded["admin_a"]),
                listing=listing,
                installed_by=seeded["admin_a"],
            )

    assert await _count_installs(migrations_pg_dsn, seeded["tenant_a"]) == 0
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], aborted=True) == 1


# ===========================================================================
# Failing sandbox smoke check blocks the install
# ===========================================================================
@pytest.mark.asyncio
async def test_failing_sandbox_blocks_install(
    migrated_db, app_database_url: str, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    from api_server.marketplace.install import SandboxCheckFailedError
    from api_server.marketplace.static_analysis import _bandit_executable

    if _bandit_executable() is None:
        pytest.skip("bandit not installed in this environment")

    seeded = await _seed(migrations_pg_dsn)
    public_pem, _ = _generate_keypair()
    # A community tool (sandbox_required) with CLEAN source so it passes the
    # analysis gate and reaches the sandbox gate, where the mocked probe
    # exits non-zero.
    tool_yaml = (
        "name: sandbox-fail-tool\n"
        "version: 1.0.0\n"
        "description: Clean source, failing smoke check.\n"
        "kind: tool\n"
        "entrypoint: tool:run\n"
        "implementation:\n"
        "  runtime: python\n"
    )
    listing_dir = tmp_path / str(seeded["sandbox_fail"])
    listing_dir.mkdir(parents=True)
    (listing_dir / "tool.yaml").write_text(tool_yaml, encoding="utf-8")
    (listing_dir / "impl.py").write_text(_CLEAN_TOOL_PY, encoding="utf-8")

    async with _tenant_session(
        app_database_url, user_id=seeded["admin_a"], tenant_id=seeded["tenant_a"]
    ) as session:
        listing = await _load_listing(session, seeded["sandbox_fail"])
        orch = _orchestrator(tmp_path, public_pem, sandbox=_sandbox(exit_code=1))
        with pytest.raises(SandboxCheckFailedError):
            await orch.install(
                session=session,
                tenant_id=seeded["tenant_a"],
                actor=_actor(seeded["admin_a"]),
                listing=listing,
                installed_by=seeded["admin_a"],
            )

    assert await _count_installs(migrations_pg_dsn, seeded["tenant_a"]) == 0
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], aborted=True) == 1


# ===========================================================================
# Community artifact installs DISABLED until consent (NOT an abort)
# ===========================================================================
@pytest.mark.asyncio
async def test_community_artifact_stays_disabled_until_consent(
    migrated_db, app_database_url: str, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    from api_server.marketplace.static_analysis import _bandit_executable

    if _bandit_executable() is None:
        pytest.skip("bandit not installed in this environment")

    seeded = await _seed(migrations_pg_dsn)
    public_pem, _ = _generate_keypair()
    # Community skill with clean body → passes analysis + sandbox, lands
    # disabled awaiting per-permission consent.
    listing_dir = tmp_path / str(seeded["community"])
    listing_dir.mkdir(parents=True)
    (listing_dir / "SKILL.md").write_text(_COMMUNITY_SKILL_MD, encoding="utf-8")

    async with _tenant_session(
        app_database_url, user_id=seeded["admin_a"], tenant_id=seeded["tenant_a"]
    ) as session:
        listing = await _load_listing(session, seeded["community"])
        orch = _orchestrator(tmp_path, public_pem, sandbox=_sandbox(exit_code=0))
        result = await orch.install(
            session=session,
            tenant_id=seeded["tenant_a"],
            actor=_actor(seeded["admin_a"]),
            listing=listing,
            # Even if a caller passes grants, a consent-gated install ignores
            # them and starts with none granted.
            granted_permissions=[{"type": "allowed_domains", "value": ["api.x.com"]}],
        )
        assert result.enabled is False
        assert result.installation.status == "disabled"
        assert result.installation.granted_permissions == []
        assert result.gate_report["consent_required"] is True
        await session.commit()

    # The install row exists (disabled) — this is NOT an abort.
    assert await _count_installs(migrations_pg_dsn, seeded["tenant_a"]) == 1
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], aborted=False) == 1
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"], aborted=True) == 0


# ===========================================================================
# Cross-tenant isolation
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_install_and_audit_are_tenant_isolated(
    migrated_db, app_database_url: str, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    """Tenant A installs a verified listing. Tenant B's RLS-scoped session
    must see NONE of tenant A's install or audit rows."""
    from api_server.db.marketplace import MarketplaceAuditEntry, MarketplaceInstallation
    from sqlalchemy import func, select

    seeded = await _seed(migrations_pg_dsn)
    public_pem, private = _generate_keypair()
    _write_artifact(
        tmp_path,
        seeded["verified_clean"],
        "SKILL.md",
        _VERIFIED_SKILL_MD,
        signature=_sign(private, _VERIFIED_SKILL_MD),
    )

    async with _tenant_session(
        app_database_url, user_id=seeded["admin_a"], tenant_id=seeded["tenant_a"]
    ) as session_a:
        listing = await _load_listing(session_a, seeded["verified_clean"])
        await _orchestrator(tmp_path, public_pem).install(
            session=session_a,
            tenant_id=seeded["tenant_a"],
            actor=_actor(seeded["admin_a"]),
            listing=listing,
            installed_by=seeded["admin_a"],
        )
        await session_a.commit()

    # Tenant B sees its own (empty) install + audit sets, never tenant A's.
    async with _tenant_session(
        app_database_url, user_id=seeded["admin_b"], tenant_id=seeded["tenant_b"]
    ) as session_b:
        installs_b = await session_b.execute(
            select(func.count()).select_from(MarketplaceInstallation)
        )
        audits_b = await session_b.execute(select(func.count()).select_from(MarketplaceAuditEntry))
        assert installs_b.scalar_one() == 0
        assert audits_b.scalar_one() == 0

    # Tenant A still has exactly its one install + one audit row.
    assert await _count_installs(migrations_pg_dsn, seeded["tenant_a"]) == 1
    assert await _count_installs(migrations_pg_dsn, seeded["tenant_b"]) == 0
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_a"]) == 1
    assert await _count_audit(migrations_pg_dsn, seeded["tenant_b"]) == 0
