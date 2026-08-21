"""cadena-pr T4 (P6): cuando NO hay PR, el plan dice POR QUÉ — no solo los logs.

T4 pedía dos casos y solo se escribió el feliz (`test_plan_close_e2e`):

  * **opener que erroriza** → `plan.pr_error` poblado y `pr_url` NULL. La rama del
    excepción de `_open_plan_pr_async` no tenía ningún test.
  * **cierre sin remoto** → hasta ahora el auto-PR volvía `skipped:no_remote` sin
    escribir NADA en el plan, así que la ficha decía «Todavía sin PR» para siempre y
    el único rastro del motivo eran los logs del worker: exactamente la ceguera que
    P6 denunciaba.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

from tests.integration._git_helpers import seed_bare_repo

pytestmark = pytest.mark.integration

_ORG_SLUG = "org-prfail"


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(dsn: str, remote_url: str | None, *, pr_url: str | None = None) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "plan": uuid4()}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE plans, projects, organizations RESTART IDENTITY CASCADE")
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Org', $2)",
            ids["tenant"],
            _ORG_SLUG,
        )
        cfg: dict[str, str] = {"provider": "generic", "auth_mode": "pat", "default_branch": "main"}
        if remote_url is not None:
            cfg["remote_url"] = remote_url
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, slug, status, is_template, git_config)"
            " VALUES ($1, $2, 'Backend', 'backend', 'active', false, $3::jsonb)",
            ids["project"],
            ids["tenant"],
            json.dumps(cfg),
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, slug, status, pr_url)"
            " VALUES ($1, $2, $3, 'My plan', 'my-plan', 'in_progress', $4)",
            ids["plan"],
            ids["tenant"],
            ids["project"],
            pr_url,
        )
    finally:
        await conn.close()
    return ids


async def _pr_columns(dsn: str, plan_id: UUID) -> asyncpg.Record:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT pr_url, pr_branch, pr_error FROM plans WHERE id = $1", plan_id
        )
    finally:
        await conn.close()
    assert row is not None
    return row


@pytest.mark.asyncio
async def test_failing_opener_persists_the_reason(
    _migrated: None, migrations_pg_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El opener revienta (401 del proveedor, rate limit…) → el plan guarda el motivo."""
    from workers import plan_pr
    from workers.git_repos import BareRepoLayout, BareRepoManager, _run_git

    remote_bare = tmp_path / "remote" / "backend.git"
    seed_bare_repo(remote_bare)
    ids = await _seed(migrations_pg_dsn, str(remote_bare))
    plan_id = str(ids["plan"])

    data_root = tmp_path / "local"
    layout = BareRepoLayout(data_root=data_root, tenant_slug=_ORG_SLUG, project_slug="backend")
    bare = BareRepoManager(layout).ensure_repo("backend", remote_url=str(remote_bare))
    _run_git("fetch", "origin", cwd=bare)
    _run_git("update-ref", "refs/heads/main", "refs/remotes/origin/main", cwd=bare)
    _run_git("symbolic-ref", "HEAD", "refs/heads/main", cwd=bare)

    monkeypatch.setattr(plan_pr, "_resolve_git_secret", lambda *_a, **_k: ("user", "tok", None))

    def _boom(_title: str, _body: str) -> str:
        raise RuntimeError("401 Bad credentials")

    monkeypatch.setattr(
        plan_pr, "build_pr_opener", lambda **_k: _boom
    )  # el opener revienta al invocarse

    settings = SimpleNamespace(
        database_url=migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1),
        data_root=str(data_root),
    )
    result = await plan_pr._open_plan_pr_async(
        ids["project"], plan_id, title="My plan", body="body", settings=settings
    )

    assert result["url"] is None
    row = await _pr_columns(migrations_pg_dsn, ids["plan"])
    assert row["pr_url"] is None
    assert row["pr_error"] and "401" in row["pr_error"], row["pr_error"]
    assert row["pr_branch"], "la rama del intento se guarda para poder reintentar"


@pytest.mark.asyncio
async def test_close_without_remote_persists_why_there_is_no_pr(
    _migrated: None, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    """Proyecto local (git_config sin `remote_url`): el plan explica que no hay PR."""
    from workers import plan_pr

    ids = await _seed(migrations_pg_dsn, None)
    plan_id = str(ids["plan"])
    settings = SimpleNamespace(
        database_url=migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1),
        data_root=str(tmp_path / "local"),
    )

    result = await plan_pr._open_plan_pr_async(
        ids["project"], plan_id, title="My plan", body="body", settings=settings
    )

    assert result["status"] == "skipped:no_remote"
    row = await _pr_columns(migrations_pg_dsn, ids["plan"])
    assert row["pr_url"] is None
    assert row["pr_error"] and "remote" in row["pr_error"].lower(), row["pr_error"]


@pytest.mark.asyncio
async def test_a_skip_never_erases_an_already_open_pr(
    _migrated: None, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    """El cierre corre más de una vez (re-veredicto, reintento). Un skip posterior
    NO puede borrar la URL de un PR que existe en el proveedor."""
    from workers import plan_pr

    open_pr = "https://fake.test/owner/backend/pull/7"
    ids = await _seed(migrations_pg_dsn, None, pr_url=open_pr)
    settings = SimpleNamespace(
        database_url=migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1),
        data_root=str(tmp_path / "local"),
    )

    await plan_pr._open_plan_pr_async(
        ids["project"], str(ids["plan"]), title="My plan", body="body", settings=settings
    )

    row = await _pr_columns(migrations_pg_dsn, ids["plan"])
    assert row["pr_url"] == open_pr, "el skip pisó la URL del PR ya abierto"
