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
