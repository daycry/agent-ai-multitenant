"""Los bare repos de los proyectos, de punta a punta (prod-04 task_prod_04_05).

El principio rector 4 de CLAUDE.md dice que el código vive en bare repos en
`{data_root}/projects/{tenant}/{project}/repos/{repo}.git`, y el 5 que cada plan
materializa una rama `plan/{id_short}-{slug}` dentro. O sea: esos repos son EL
PRODUCTO de la plataforma. Si un DR no los devuelve, la plataforma restaurada
está vacía por dentro aunque la base de datos hable de cien planes.

Estado antes de prod-04 — dos averías encadenadas
--------------------------------------------------
1. Los repos solo entraban de rebote en el tar del bind `/data/agent-platform`,
   con los worktrees dentro (transitorios, enormes, y en escritura activa
   mientras corren agentes: `tar` devuelve rc≠0 con «file changed as we read it»).
2. Peor: `restore.py._restore_volumes` filtraba `kind == "volume_tar"`, así que
   el artefacto `bind_tar` se capturaba, se le calculaba el SHA-256, se
   verificaba estructuralmente… **y el restore no lo extraía nunca**. Nadie lo
   había notado porque ningún test restauraba un bundle CON binds y comprobaba el
   disco después.

Este test usa `git` de verdad: crea un bare repo con una rama `plan/…`, lo
respalda con tar real, lo restaura en OTRO directorio y comprueba con
`git rev-parse` que la rama y su commit sobrevivieron. Es la única forma de que
la casilla signifique algo.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from workers.backup import BackupConfig, BackupEngine, SubprocessRunner
from workers.backup_verification import CHECK_TAR_LIST, verify_bundle
from workers.restore import RestoreConfig, RestoreEngine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("tar") is None or shutil.which("git") is None,
        reason="este test necesita tar y git REALES; sin ellos no probaría nada",
    ),
]

_NOW = datetime(2026, 7, 29, 3, 0, 0, tzinfo=UTC)
_PLAN_BRANCH = "plan/prod04-backup-dr"
_DB_URL = "postgresql://migrations_user:x@postgres:5432/agentic_platform"

# TOC sintético para que el verificador no necesite un pg_restore real.
_FAKE_TOC = ";\n; Archive\n;\n215; 1259 16404 TABLE public tenants migrations_user\n"


def _git(*args: str, cwd: Path) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env={
            "GIT_AUTHOR_NAME": "drill",
            "GIT_AUTHOR_EMAIL": "drill@example.invalid",
            "GIT_COMMITTER_NAME": "drill",
            "GIT_COMMITTER_EMAIL": "drill@example.invalid",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "PATH": __import__("os").environ.get("PATH", ""),
        },
    )
    assert done.returncode == 0, f"git {' '.join(args)} falló: {done.stderr or done.stdout}"
    return done.stdout.strip()


def _seed_project_repo(projects_root: Path) -> tuple[Path, str]:
    """Un bare repo con la rama de un plan. Devuelve (bare_path, sha del plan)."""
    project = projects_root / "tenant-uno" / "proyecto-uno"
    bare = project / "repos" / "app.git"
    bare.mkdir(parents=True)
    _git("init", "--bare", "--initial-branch=main", str(bare), cwd=projects_root)

    work = projects_root.parent / "work"
    work.mkdir(parents=True)
    _git("init", "--initial-branch=main", str(work), cwd=projects_root)
    (work / "README.md").write_text("# app\n", encoding="utf-8")
    _git("add", "README.md", cwd=work)
    _git("commit", "-m", "chore: seed", cwd=work)
    _git("checkout", "-b", _PLAN_BRANCH, cwd=work)
    (work / "feature.py").write_text("print('del plan')\n", encoding="utf-8")
    _git("add", "feature.py", cwd=work)
    _git("commit", "-m", "feat: lo que hizo el plan", cwd=work)
    sha = _git("rev-parse", "HEAD", cwd=work)
    _git("remote", "add", "origin", str(bare), cwd=work)
    _git("push", "origin", "main", _PLAN_BRANCH, cwd=work)

    # Un worktree por tarea: transitorio, NO debe entrar en el bundle.
    worktree = project / "worktrees" / "task-abc123"
    worktree.mkdir(parents=True)
    (worktree / "scratch.txt").write_text("basura regenerable\n" * 500, encoding="utf-8")

    # El clon de trabajo se queda donde está (fuera de `projects/`, así que no
    # entra en el tar). En Windows los objetos de git son read-only y borrarlo
    # con shutil.rmtree revienta con WinError 5 — no vale la pena.
    return bare, sha


class Runner(SubprocessRunner):
    """Runner real salvo pg_dump/pg_restore (no hay servidor en el test)."""

    def run(self, args, *, env=None, timeout=None):  # type: ignore[no-untyped-def]
        argv = list(args)
        if argv[0] == "pg_dump":
            out = next(a[len("--file=") :] for a in argv if a.startswith("--file="))
            Path(out).mkdir(parents=True, exist_ok=True)
            (Path(out) / "toc.dat").write_bytes(b"synthetic")
            from workers.backup import CommandResult

            return CommandResult(returncode=0)
        if argv[0] == "pg_restore":
            from workers.backup import CommandResult

            return CommandResult(returncode=0, stdout=_FAKE_TOC)
        if argv[0] in {"psql", "docker"}:
            from workers.backup import CommandResult

            return CommandResult(returncode=0)
        return super().run(argv, env=env, timeout=timeout)


def test_a_plan_branch_survives_backup_and_restore_into_a_clean_directory(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "agent-platform"
    projects_root = data_root / "projects"
    projects_root.mkdir(parents=True)
    _, plan_sha = _seed_project_repo(projects_root)

    backup_root = data_root / "backups"
    backup_root.mkdir()
    volumes_root = tmp_path / "volumes"
    (volumes_root / "minio_data" / "_data").mkdir(parents=True)
    (volumes_root / "minio_data" / "_data" / "obj").write_bytes(b"x" * 64)

    cfg = BackupConfig(
        backup_root=backup_root,
        database_url=_DB_URL,
        volumes=("minio_data",),
        volumes_mount_root=volumes_root,
        retention_days=7,
        projects_root=str(projects_root),
        transient_excludes=("worktrees", "dep-cache"),
    )
    runner = Runner()
    result = BackupEngine(cfg, runner=runner, now=_NOW).run_full_backup()

    # -- el artefacto existe, es su propia clase, y está verificado ------------
    projects_art = [a for a in result.artifacts if a.kind == "projects_tar"]
    assert len(projects_art) == 1, [a.kind for a in result.artifacts]
    assert projects_art[0].source == str(projects_root)
    report = verify_bundle(result.bundle_dir, runner=runner)
    assert report.valid, [c.to_dict() for c in report.failures]
    assert CHECK_TAR_LIST in {c.check for c in report.checks if c.artifact == "projects.tar.gz"}

    # -- los worktrees NO viajan (regenerables + en escritura activa) ----------
    listing = subprocess.run(
        ["tar", "--list", "--gzip", f"--file={result.bundle_dir / 'projects.tar.gz'}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert listing.returncode == 0, listing.stderr
    assert "worktrees" not in listing.stdout, listing.stdout
    assert "app.git" in listing.stdout

    # -- RESTORE en una máquina «limpia»: otro directorio, nada preexistente ---
    clean_projects = tmp_path / "maquina-limpia" / "projects"
    restore_cfg = RestoreConfig(
        backup_root=backup_root,
        database_url=_DB_URL,
        volumes=("minio_data",),
        volumes_mount_root=tmp_path / "restored-volumes",
        compose_project="agentic-platform",
        compose_file=tmp_path / "no-compose.yml",  # preflight no verificable
        app_services=("api-server",),
        volume_services=("minio",),
        projects_root=str(clean_projects),
    )
    restored = RestoreEngine(restore_cfg, runner=Runner()).run_full_restore(
        result.bundle_dir, confirm=result.backup_id
    )
    assert str(clean_projects) in restored.restored_paths

    # -- la prueba de fuego: git sabe leer el repo restaurado ------------------
    bare_restored = clean_projects / "tenant-uno" / "proyecto-uno" / "repos" / "app.git"
    assert bare_restored.is_dir(), sorted(p.name for p in clean_projects.rglob("*"))
    assert _git("rev-parse", f"refs/heads/{_PLAN_BRANCH}", cwd=bare_restored) == plan_sha
    assert "del plan" in _git("show", f"{_PLAN_BRANCH}:feature.py", cwd=bare_restored)
    # Y el repo pasa su propia auditoría de integridad.
    _git("fsck", "--no-progress", cwd=bare_restored)


def test_a_bundle_without_a_projects_root_says_so_instead_of_pretending(
    tmp_path: Path,
) -> None:
    """Sin `projects_root` configurado el backup sigue, pero el artefacto NO
    aparece: es la diferencia entre «no hay repos» y «los repos están dentro»."""
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    volumes_root = tmp_path / "volumes"
    (volumes_root / "minio_data" / "_data").mkdir(parents=True)
    (volumes_root / "minio_data" / "_data" / "obj").write_bytes(b"y" * 32)
    cfg = BackupConfig(
        backup_root=backup_root,
        database_url=_DB_URL,
        volumes=("minio_data",),
        volumes_mount_root=volumes_root,
        retention_days=7,
        projects_root="",
    )
    result = BackupEngine(cfg, runner=Runner(), now=_NOW).run_full_backup()
    assert not [a for a in result.artifacts if a.kind == "projects_tar"]


def test_a_configured_projects_root_that_is_not_on_disk_does_not_kill_the_backup(
    tmp_path: Path,
) -> None:
    """Una instalación recién parida no tiene proyectos todavía. Fallar el backup
    entero por eso convertiría el PRIMER backup en un fallo — y el operador
    aprendería a ignorar la alerta."""
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    volumes_root = tmp_path / "volumes"
    (volumes_root / "minio_data" / "_data").mkdir(parents=True)
    (volumes_root / "minio_data" / "_data" / "obj").write_bytes(b"z" * 32)
    cfg = BackupConfig(
        backup_root=backup_root,
        database_url=_DB_URL,
        volumes=("minio_data",),
        volumes_mount_root=volumes_root,
        retention_days=7,
        projects_root=str(tmp_path / "todavia-no-existe"),
    )
    result = BackupEngine(cfg, runner=Runner(), now=_NOW).run_full_backup()
    assert not [a for a in result.artifacts if a.kind == "projects_tar"]


def test_the_projects_tree_is_not_captured_twice(tmp_path: Path) -> None:
    """Con la config por defecto `projects_root` está DENTRO del bind del
    data-root. Si el bind no lo excluyera, los bare repos —lo más pesado del
    bundle después de MinIO— viajarían dos veces en cada backup, y el restore
    los extraería dos veces, la segunda encima de la primera."""
    data_root = tmp_path / "agent-platform"
    projects_root = data_root / "projects"
    projects_root.mkdir(parents=True)
    _seed_project_repo(projects_root)
    backup_root = data_root / "backups"
    backup_root.mkdir()
    volumes_root = tmp_path / "volumes"
    (volumes_root / "minio_data" / "_data").mkdir(parents=True)
    (volumes_root / "minio_data" / "_data" / "obj").write_bytes(b"o" * 32)

    cfg = BackupConfig(
        backup_root=backup_root,
        database_url=_DB_URL,
        volumes=("minio_data",),
        volumes_mount_root=volumes_root,
        retention_days=7,
        bind_paths=(str(data_root),),
        projects_root=str(projects_root),
        transient_excludes=("worktrees", "dep-cache"),
    )
    result = BackupEngine(cfg, runner=Runner(), now=_NOW).run_full_backup()

    bind_art = next(a for a in result.artifacts if a.kind == "bind_tar")
    listing = subprocess.run(
        ["tar", "--list", "--gzip", f"--file={result.bundle_dir / bind_art.path}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert listing.returncode == 0, listing.stderr
    assert (
        "app.git" not in listing.stdout
    ), f"los bare repos viajan DOS veces (en projects_tar y en el bind): {listing.stdout}"
    assert "backups/" not in listing.stdout, "el bundle se auto-incluyó"
    # No vacuo: el bind SÍ captura lo que no es projects/ (aquí, el clon de
    # trabajo que `_seed_project_repo` deja fuera de projects/).
    assert "./work/" in listing.stdout, listing.stdout
    # Y los repos siguen estando: en su propio artefacto.
    assert [a for a in result.artifacts if a.kind == "projects_tar"]
