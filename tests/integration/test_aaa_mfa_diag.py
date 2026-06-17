"""TEMP diagnostic (Plan prod-01 CI forensics) — REMOVE once the CI-only
`user_mfa_totp does not exist` bug is fixed.

A clean `alembic upgrade head` in CI does not create `user_mfa_totp` (migration
0037), yet it does locally and the chain is linear with no later drop. This runs
FIRST (aaa) on a fresh session DB and prints the post-upgrade state — alembic
version + which migration-0036/0037/0038 tables physically exist — so we can see
whether alembic is at head with the table missing (version ahead of schema) or
stopped earlier.
"""

from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration


def test_diag_mfa_after_upgrade_head(alembic_config: object, admin_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    async def _introspect() -> None:
        conn = await asyncpg.connect(admin_pg_dsn)
        try:
            ver = await conn.fetchval("SELECT version_num FROM alembic_version")
            ntables = await conn.fetchval(
                "SELECT count(*) FROM information_schema.tables " "WHERE table_schema = 'public'"
            )
            print(f"\n[DIAG] alembic_version={ver!r}  public_tables={ntables}")
            for t in ("users", "scim_tokens", "user_mfa_totp", "webauthn_credentials"):
                reg = await conn.fetchval(f"SELECT to_regclass('public.{t}')::text")
                print(f"[DIAG] table {t} => {reg!r}")
        finally:
            await conn.close()

    asyncio.run(_introspect())


def test_diag_reversibility_roundtrip(alembic_config: object, admin_pg_dsn: str) -> None:
    """Mirror test_approval_timeout's reversibility tests against the shared
    session DB and introspect AFTER each step: upgrade head -> downgrade 0011 ->
    upgrade head. In CI the full suite ends up with user_mfa_totp missing while
    alembic is at head; this reproduces the exact round-trip in isolation and
    prints the resulting state so we can see whether the re-upgrade recreates
    the table.
    """

    pg_host = os.environ.get("TEST_PG_HOST", "localhost")
    pg_port = os.environ.get("TEST_PG_PORT", "15432")
    pg_db = os.environ.get("TEST_PG_DB_NAME", "agentic_platform_test")
    app_user = os.environ.get("TEST_PG_APP_USER", "app_user")
    app_pw = os.environ.get("TEST_PG_APP_PASSWORD", "changeme-app-dev-only")
    app_dsn = f"postgresql://{app_user}:{app_pw}@{pg_host}:{pg_port}/{pg_db}"

    async def _state(label: str) -> None:
        conn = await asyncpg.connect(admin_pg_dsn)
        try:
            ver = await conn.fetchval("SELECT version_num FROM alembic_version")
            mfa = await conn.fetchval("SELECT to_regclass('public.user_mfa_totp')::text")
            wac = await conn.fetchval("SELECT to_regclass('public.webauthn_credentials')::text")
            n = await conn.fetchval(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
            )
            print(
                f"[RT] {label}: alembic={ver!r} user_mfa_totp={mfa!r} webauthn={wac!r} tables={n}"
            )
        finally:
            await conn.close()
        # Probe as app_user (NOBYPASSRLS) — the role the api-server uses. The
        # failing query is `SELECT FROM user_mfa_totp` from app_user; check both
        # catalog visibility and an actual SELECT (does-not-exist vs perm-denied).
        appc = await asyncpg.connect(app_dsn)
        try:
            sp = await appc.fetchval("SHOW search_path")
            mfa_app = await appc.fetchval("SELECT to_regclass('public.user_mfa_totp')::text")
            try:
                await appc.execute("SELECT 1 FROM user_mfa_totp WHERE false")
                sel = "OK"
            except Exception as exc:  # pragma: no cover - diagnostic
                sel = f"{type(exc).__name__}: {exc}"
            print(f"[RT] {label} [app_user]: search_path={sp!r} regclass={mfa_app!r} select={sel}")
        finally:
            await appc.close()

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    asyncio.run(_state("after upgrade head #1"))
    command.downgrade(alembic_config, "0011_platform_settings")  # type: ignore[arg-type]
    asyncio.run(_state("after downgrade 0011"))
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    asyncio.run(_state("after upgrade head #2"))
