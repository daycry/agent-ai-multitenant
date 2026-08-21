"""Reconciliación post-restore de los cuatro almacenes (prod-04 task_prod_04_13).

Un restore que «termina bien» no es un restore bueno. El bundle son fotos de
instantes distintos, así que la base de datos restaurada puede hablar de
documentos cuya fuente no está en MinIO, de proveedores LLM cuyo secreto no está
en el Vault restaurado, o de planes activos cuya rama de trabajo no está en
ningún repo. Ninguna de esas tres cosas se ve en `docker compose ps`: el stack
arranca sano y el agujero aparece semanas después.

Qué es real aquí
----------------
La comprobación **BD ↔ git** corre `git` de verdad contra bare repos de verdad:
comprobar la existencia de una rama contra un doble sería comprobar el doble, y
es precisamente la comprobación que protege el producto de la plataforma. MinIO
y Vault sí se doblan (no hay servidor en el test), pero sus dobles solo
responden «existe / no existe», que es todo el contrato.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.plan_git import make_plan_branch_name
from workers.restore_reconcile import (
    SEVERITY_CRITICAL,
    SEVERITY_WARNING,
    ReconcileReport,
    RestoreReconciler,
    SubprocessGitProbe,
)

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# Dobles mínimos: solo «existe / no existe».
# --------------------------------------------------------------------------- #


class FakeObjects:
    def __init__(self, keys: Iterable[str]) -> None:
        self.keys = set(keys)

    async def object_exists(self, *, key: str) -> bool:
        return key in self.keys

    async def list_objects(self, *, prefix: str) -> Iterable[str]:
        return [k for k in sorted(self.keys) if k.startswith(prefix)]


class FakeVault:
    def __init__(self, paths: Iterable[str]) -> None:
        self.paths = set(paths)

    async def secret_exists(self, path: str) -> bool:
        return path in self.paths


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


def _git(*args: str, cwd: Path) -> None:
    import os

    done = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "PATH": os.environ.get("PATH", ""),
        },
    )
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr or done.stdout}"


def _make_bare_with_branch(repos_root: Path, branch: str | None) -> Path:
    """Un bare repo REAL; con `branch` creada si se pide."""
    repos_root.mkdir(parents=True, exist_ok=True)
    bare = repos_root / "app.git"
    _git("init", "--bare", "--initial-branch=main", str(bare), cwd=repos_root)
    work = repos_root.parent / f"work-{bare.name}"
    work.mkdir(parents=True, exist_ok=True)
    _git("init", "--initial-branch=main", str(work), cwd=repos_root)
    (work / "f.txt").write_text("x\n", encoding="utf-8")
    _git("add", "f.txt", cwd=work)
    _git("commit", "-m", "seed", cwd=work)
    _git("remote", "add", "origin", str(bare), cwd=work)
    refs = ["main"]
    if branch:
        _git("checkout", "-b", branch, cwd=work)
        refs.append(branch)
    _git("push", "origin", *refs, cwd=work)
    return bare


async def _seed(sm: async_sessionmaker, *, plan_status: str, plan_slug: str) -> dict[str, object]:
    # Ids frescos por test: la base de datos de integración es COMPARTIDA por
    # todo el fichero, así que un UUID fijo choca con `plans_pkey` en el segundo.
    plan_id = uuid4()
    tenant, project, kb, doc, provider = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    key = f"kb/{tenant}/{kb}/{doc}/manual.pdf"
    async with sm() as s, s.begin():
        # La base de integración es COMPARTIDA por todo el fichero y el
        # reconciliador mira TODAS las filas: sin limpiar, el test N ve los
        # restos de los N-1 anteriores y falla por divergencias que no son suyas.
        await s.execute(
            text(
                "TRUNCATE llm_providers, chunks, documents, knowledge_bases,"
                " plans, projects, organizations RESTART IDENTITY CASCADE"
            )
        )
        await s.execute(
            text("INSERT INTO organizations (id, name, slug) VALUES (:t, 'T', :sl)"),
            {"t": tenant, "sl": f"tenant-{tenant.hex[:8]}"},
        )
        await s.execute(
            text(
                "INSERT INTO projects (id, tenant_id, name, slug) VALUES (:p, :t, 'Proyecto', :sl)"
            ),
            {"p": project, "t": tenant, "sl": f"proyecto-{project.hex[:8]}"},
        )
        await s.execute(
            text(
                "INSERT INTO knowledge_bases (id, tenant_id, name, embedding_model_id, is_builtin)"
                " VALUES (:k, :t, 'KB', 'nomic-embed-text-v1.5', false)"
            ),
            {"k": kb, "t": tenant},
        )
        await s.execute(
            text(
                "INSERT INTO documents (id, tenant_id, kb_id, title, source_filename,"
                " source_mime_type, source_storage_key, status)"
                " VALUES (:d, :t, :k, 'M', 'm.pdf', 'application/pdf', :key, 'indexed')"
            ),
            {"d": doc, "t": tenant, "k": kb, "key": key},
        )
        await s.execute(
            text(
                "INSERT INTO plans (id, tenant_id, project_id, title, slug, status)"
                " VALUES (:pl, :t, :p, 'Plan de prueba', :sl, :st)"
            ),
            {"pl": plan_id, "t": tenant, "p": project, "sl": plan_slug, "st": plan_status},
        )
        await s.execute(
            text(
                "INSERT INTO llm_providers (id, display_name, slug, kind, secret_vault_path)"
                " VALUES (:i, 'Proveedor', :sl, 'ollama', 'kv/agentic/providers/uno')"
            ),
            {"i": provider, "sl": f"prov-{provider.hex[:8]}"},
        )
    return {
        "plan_id": plan_id,
        "tenant_slug": f"tenant-{tenant.hex[:8]}",
        "project_slug": f"proyecto-{project.hex[:8]}",
        "storage_key": key,
    }


async def _run(
    admin_database_url: str,
    tmp_path: Path,
    *,
    plan_status: str = "in_progress",
    plan_slug: str = "mi-plan",
    branch_present: bool = True,
    repo_present: bool = True,
    blob_present: bool = True,
    vault_present: bool = True,
    extra_blobs: tuple[str, ...] = (),
) -> tuple[ReconcileReport, str]:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        seeded = await _seed(sm, plan_status=plan_status, plan_slug=plan_slug)
        data_root = tmp_path / "agent-platform"
        repos_root = (
            data_root
            / "projects"
            / str(seeded["tenant_slug"])
            / str(seeded["project_slug"])
            / "repos"
        )
        branch = make_plan_branch_name(str(seeded["plan_id"]), plan_slug)
        if repo_present:
            _make_bare_with_branch(repos_root, branch if branch_present else None)

        keys = list(extra_blobs)
        if blob_present:
            keys.append(str(seeded["storage_key"]))
        reconciler = RestoreReconciler(
            objects=FakeObjects(keys),
            vault=FakeVault(["kv/agentic/providers/uno"] if vault_present else []),
            git=SubprocessGitProbe(),
            data_root=data_root,
        )
        async with sm() as session:
            return await reconciler.reconcile(session), branch
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# Todo cuadra
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("git") is None, reason="la sonda de git es REAL")
async def test_a_consistent_restore_reports_no_divergences(
    _migrated: None, admin_database_url: str, tmp_path: Path
) -> None:
    report, _branch = await _run(admin_database_url, tmp_path)
    assert report.ok, report.render()
    assert report.divergences == ()
    assert report.exit_code == 0
    # No vacuo: las TRES comprobaciones se ejecutaron de verdad.
    assert set(report.checks_run) == {"db<->minio", "db<->vault", "db<->git"}
    assert report.checks_skipped == ()


# --------------------------------------------------------------------------- #
# Cada almacén, roto por su lado
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("git") is None, reason="la sonda de git es REAL")
async def test_a_document_without_its_blob_is_critical(
    _migrated: None, admin_database_url: str, tmp_path: Path
) -> None:
    report, _branch = await _run(admin_database_url, tmp_path, blob_present=False)
    assert not report.ok
    assert report.exit_code == 1
    assert [d.check for d in report.critical] == ["db<->minio"]
    assert "no se puede reindexar" in report.critical[0].detail


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("git") is None, reason="la sonda de git es REAL")
async def test_an_orphan_blob_is_only_a_warning(
    _migrated: None, admin_database_url: str, tmp_path: Path
) -> None:
    """Basura, no pérdida: el GC de conocimiento la barre. Marcarlo como crítico
    haría que un DR perfectamente válido pareciese fallido."""
    report, _branch = await _run(
        admin_database_url,
        tmp_path,
        extra_blobs=(f"kb/{uuid4()}/{uuid4()}/{uuid4()}/fantasma.pdf",),
    )
    assert report.ok, report.render()
    assert [d.severity for d in report.warnings] == [SEVERITY_WARNING]
    assert "huérfanos" in report.warnings[0].subject


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("git") is None, reason="la sonda de git es REAL")
async def test_a_provider_pointing_at_a_missing_vault_secret_is_critical(
    _migrated: None, admin_database_url: str, tmp_path: Path
) -> None:
    report, _branch = await _run(admin_database_url, tmp_path, vault_present=False)
    assert not report.ok
    assert [d.check for d in report.critical] == ["db<->vault"]
    assert "primer run" in report.critical[0].detail


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("git") is None, reason="la sonda de git es REAL")
async def test_an_active_plan_without_its_branch_is_critical(
    _migrated: None, admin_database_url: str, tmp_path: Path
) -> None:
    """El caso que motiva todo esto: el restore extrajo el repo pero la rama del
    plan no está. Es el trabajo de los agentes."""
    report, branch = await _run(admin_database_url, tmp_path, branch_present=False)
    assert not report.ok
    assert [d.check for d in report.critical] == ["db<->git"]
    assert branch in report.critical[0].detail


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("git") is None, reason="la sonda de git es REAL")
async def test_a_project_without_any_repo_is_critical(
    _migrated: None, admin_database_url: str, tmp_path: Path
) -> None:
    """El fallo que tenía el restore hasta prod-04: `bind_tar` y `projects_tar`
    se respaldaban y NO se extraían, así que el árbol de repos venía vacío."""
    report, _branch = await _run(admin_database_url, tmp_path, repo_present=False)
    assert not report.ok
    assert "ningún bare repo" in report.critical[0].detail


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("git") is None, reason="la sonda de git es REAL")
async def test_a_finished_plan_without_a_branch_is_not_a_divergence(
    _migrated: None, admin_database_url: str, tmp_path: Path
) -> None:
    """Solo se exige rama a los planes ACTIVOS. Un plan completado hace meses
    puede tener su rama mergeada y borrada, y eso es lo normal — reportarlo
    llenaría el informe de ruido y enseñaría a ignorarlo."""
    report, _branch = await _run(
        admin_database_url, tmp_path, plan_status="completed", branch_present=False
    )
    assert report.ok, report.render()


# --------------------------------------------------------------------------- #
# «No comprobado» nunca es «correcto»
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("git") is None, reason="la sonda de git es REAL")
async def test_a_missing_probe_is_reported_as_skipped_not_as_ok(
    _migrated: None, admin_database_url: str, tmp_path: Path
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        seeded = await _seed(sm, plan_status="in_progress", plan_slug="mi-plan")
        data_root = tmp_path / "agent-platform"
        _make_bare_with_branch(
            data_root
            / "projects"
            / str(seeded["tenant_slug"])
            / str(seeded["project_slug"])
            / "repos",
            make_plan_branch_name(str(seeded["plan_id"]), "mi-plan"),
        )
        # Sin sonda de MinIO ni de Vault.
        reconciler = RestoreReconciler(git=SubprocessGitProbe(), data_root=data_root)
        async with sm() as session:
            report = await reconciler.reconcile(session)
    finally:
        await engine.dispose()

    assert set(report.checks_skipped) == {"db<->minio", "db<->vault"}
    assert report.checks_run == ("db<->git",)
    assert "omitidas" in report.render(), (
        "un informe que no dice qué NO comprobó se lee como si lo hubiera comprobado todo"
    )


# --------------------------------------------------------------------------- #
# La sonda de git, aislada
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(shutil.which("git") is None, reason="la sonda de git es REAL")
def test_the_git_probe_answers_against_a_real_bare_repo(tmp_path: Path) -> None:
    bare = _make_bare_with_branch(tmp_path / "repos", "plan/abc-lo-que-sea")
    probe = SubprocessGitProbe()
    assert probe.repo_exists(bare)
    assert probe.branch_exists(bare, "plan/abc-lo-que-sea")
    assert probe.branch_exists(bare, "main")
    assert not probe.branch_exists(bare, "plan/no-existe")
    assert not probe.repo_exists(tmp_path / "repos" / "otro.git")
    assert not probe.branch_exists(tmp_path / "repos" / "otro.git", "main")


def test_the_report_renders_something_an_operator_can_act_on() -> None:
    from workers.restore_reconcile import Divergence

    report = ReconcileReport(
        checks_run=("db<->git",),
        checks_skipped=("db<->vault",),
        divergences=(
            Divergence(
                check="db<->git",
                severity=SEVERITY_CRITICAL,
                subject="plan X",
                detail="la rama no está",
            ),
        ),
    )
    rendered = report.render()
    assert "CRÍTICAS (1)" in rendered
    assert "plan X" in rendered
    assert "NO se puede dar por bueno" in rendered
    assert report.exit_code == 1
