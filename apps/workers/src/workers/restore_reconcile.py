"""Reconciliación post-restore de los cuatro almacenes (prod-04 task_prod_04_13).

Por qué hace falta
------------------
Un bundle NO es una foto: es un conjunto de fotos tomadas en instantes
ligeramente distintos (el `pg_dump` empieza antes de que acabe el tar de MinIO;
el snapshot de Vault es otro momento; los bare repos, otro). El motor de restore
sabe poner cada pieza en su sitio, pero **nadie comprobaba que las piezas
encajan**: un restore podía terminar con éxito y dejar documentos cuya fuente no
existe, proveedores LLM apuntando a secretos que no están en el Vault
restaurado, o planes activos cuya rama de trabajo no está en ningún repo.

Eso es exactamente lo que un operador NO puede detectar mirando
`docker compose ps`: el stack arranca sano y el agujero aparece semanas después,
cuando alguien intenta reindexar un documento o continuar un plan.

Los cuatro almacenes y sus criterios
------------------------------------
* **BD ↔ MinIO** — cada `documents.source_storage_key` vivo tiene su blob
  (CRÍTICO: sin la fuente el documento es irreparable) y cada blob `kb/**` tiene
  su fila (AVISO: es basura que el GC barrerá, no pérdida de datos).
* **BD ↔ Vault** — cada `llm_providers.secret_vault_path` resuelve en el Vault
  restaurado (CRÍTICO: sin credencial el proveedor no funciona y el fallo
  aparece a mitad de un run).
* **BD ↔ git** — cada plan activo tiene su bare repo y su rama
  `plan/{id_short}-{slug}` (CRÍTICO: es el trabajo de los agentes; el principio
  rector 5 de CLAUDE.md).

Uso
---
Como paso final del restore, o suelto::

    python -m workers.restore_reconcile

Sale con **código ≠ 0** si hay divergencias críticas, para que un script de DR
pueda encadenar y no dar el restore por bueno.

Diseño — costuras inyectables
-----------------------------
Cada almacén se consulta a través de una costura (`ObjectProbe`, `VaultProbe`,
`GitProbe`) con implementación de producción y doble de test. Las de MinIO y
Vault son inevitablemente dobles en test (no hay servidor); la de git NO lo es
— ahí se ejecuta `git` de verdad, porque comprobar ramas contra un doble sería
comprobar el doble.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from workers.config import Settings, get_settings
from workers.plan_git import make_plan_branch_name

_log = structlog.get_logger("workers.restore_reconcile")

#: Severidades. Solo `critical` hace que el informe falle: un blob huérfano es
#: basura recuperable (el GC lo barre), un documento sin fuente no lo es.
SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"

#: Cota del barrido de blobs huérfanos: es O(objetos del bucket) y en un DR no
#: puede quedarse una hora contando basura. El resto lo ve el GC.
_ORPHAN_SCAN_CAP = 5000

#: Estados de plan que se consideran «activos»: son los que tienen (o deberían
#: tener) una rama de trabajo viva en el bare repo del proyecto.
_ACTIVE_PLAN_STATUSES = ("approved", "in_progress", "pending_human_validation", "blocked")


class ObjectProbe(Protocol):
    """Lo que la reconciliación necesita del object storage."""

    async def object_exists(self, *, key: str) -> bool: ...

    async def list_objects(self, *, prefix: str) -> Iterable[str]: ...


class VaultProbe(Protocol):
    """¿Existe este secreto en el Vault restaurado?"""

    async def secret_exists(self, path: str) -> bool: ...


class GitProbe(Protocol):
    """¿Existe este bare repo, y esta rama dentro de él?"""

    def repo_exists(self, repo_path: Path) -> bool: ...

    def branch_exists(self, repo_path: Path, branch: str) -> bool: ...


@dataclass(frozen=True)
class Divergence:
    """Una discrepancia concreta entre dos almacenes."""

    check: str
    severity: str
    subject: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class ReconcileReport:
    """El veredicto. `ok` es False en cuanto hay UNA divergencia crítica."""

    checks_run: tuple[str, ...] = ()
    checks_skipped: tuple[str, ...] = ()
    divergences: tuple[Divergence, ...] = field(default_factory=tuple)

    @property
    def critical(self) -> tuple[Divergence, ...]:
        return tuple(d for d in self.divergences if d.severity == SEVERITY_CRITICAL)

    @property
    def warnings(self) -> tuple[Divergence, ...]:
        return tuple(d for d in self.divergences if d.severity != SEVERITY_CRITICAL)

    @property
    def ok(self) -> bool:
        return not self.critical

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks_run": list(self.checks_run),
            "checks_skipped": list(self.checks_skipped),
            "critical": [d.to_dict() for d in self.critical],
            "warnings": [d.to_dict() for d in self.warnings],
        }

    def render(self) -> str:
        """Informe legible para el operador a las 4 de la mañana."""
        lines = [
            "RECONCILIACIÓN POST-RESTORE",
            "=" * 27,
            f"comprobaciones ejecutadas: {', '.join(self.checks_run) or '(ninguna)'}",
        ]
        if self.checks_skipped:
            lines.append(f"omitidas (sin sonda): {', '.join(self.checks_skipped)}")
        if not self.divergences:
            lines.append("")
            lines.append("Sin divergencias. Los cuatro almacenes cuadran.")
            return "\n".join(lines)
        for label, items in (("CRÍTICAS", self.critical), ("AVISOS", self.warnings)):
            if not items:
                continue
            lines.append("")
            lines.append(f"{label} ({len(items)}):")
            lines.extend(f"  [{d.check}] {d.subject}: {d.detail}" for d in items)
        lines.append("")
        lines.append(
            "El restore NO se puede dar por bueno con divergencias críticas."
            if self.critical
            else "Solo avisos: el restore es utilizable; revísalos igualmente."
        )
        return "\n".join(lines)


class RestoreReconciler:
    """Compara la base de datos restaurada contra MinIO, Vault y los repos git.

    Cada sonda es opcional: si no se inyecta (y no se puede construir la de
    producción), la comprobación se marca como OMITIDA en vez de darse por
    buena. «No comprobado» y «correcto» no son lo mismo, y confundirlos es
    justo el tipo de verde vacío que este módulo existe para evitar.
    """

    def __init__(
        self,
        *,
        objects: ObjectProbe | None = None,
        vault: VaultProbe | None = None,
        git: GitProbe | None = None,
        data_root: Path | None = None,
    ) -> None:
        self._objects = objects
        self._vault = vault
        self._git = git or SubprocessGitProbe()
        self._data_root = data_root or Path(get_settings().data_root)

    async def reconcile(self, session: AsyncSession) -> ReconcileReport:
        divergences: list[Divergence] = []
        run: list[str] = []
        skipped: list[str] = []

        if self._objects is not None:
            divergences.extend(await self._check_db_vs_objects(session))
            run.append("db<->minio")
        else:
            skipped.append("db<->minio")

        if self._vault is not None:
            divergences.extend(await self._check_db_vs_vault(session))
            run.append("db<->vault")
        else:
            skipped.append("db<->vault")

        divergences.extend(await self._check_db_vs_git(session))
        run.append("db<->git")

        report = ReconcileReport(
            checks_run=tuple(run),
            checks_skipped=tuple(skipped),
            divergences=tuple(divergences),
        )
        _log.info(
            "restore.reconcile.done",
            ok=report.ok,
            critical=len(report.critical),
            warnings=len(report.warnings),
            skipped=skipped,
        )
        return report

    # -- BD <-> MinIO --------------------------------------------------------

    async def _check_db_vs_objects(self, session: AsyncSession) -> list[Divergence]:
        assert self._objects is not None
        out: list[Divergence] = []
        rows = (
            await session.execute(
                text(
                    "SELECT id, source_storage_key FROM documents"
                    " WHERE deleted_at IS NULL AND source_storage_key IS NOT NULL"
                )
            )
        ).all()
        live_keys: set[str] = set()
        for doc_id, key in rows:
            live_keys.add(str(key))
            if not await self._objects.object_exists(key=str(key)):
                out.append(
                    Divergence(
                        check="db<->minio",
                        severity=SEVERITY_CRITICAL,
                        subject=f"document {doc_id}",
                        detail=(
                            f"la fila está viva pero su fuente {key!r} no está en el "
                            f"object storage: el documento no se puede reindexar ni descargar"
                        ),
                    )
                )

        try:
            blobs = list(await self._objects.list_objects(prefix="kb/"))[:_ORPHAN_SCAN_CAP]
        except Exception as exc:  # el listado es lo caro: no tumbar el informe
            _log.warning("restore.reconcile.list_objects_failed", error=str(exc))
            return out
        orphans = [b for b in blobs if b not in live_keys]
        if orphans:
            out.append(
                Divergence(
                    check="db<->minio",
                    severity=SEVERITY_WARNING,
                    subject=f"{len(orphans)} blobs huérfanos",
                    detail=(
                        "hay objetos kb/** sin fila viva en `documents` "
                        f"(p. ej. {orphans[0]!r}); el GC de conocimiento los barrerá"
                    ),
                )
            )
        return out

    # -- BD <-> Vault --------------------------------------------------------

    async def _check_db_vs_vault(self, session: AsyncSession) -> list[Divergence]:
        assert self._vault is not None
        out: list[Divergence] = []
        rows = (
            await session.execute(
                text(
                    "SELECT id, display_name, secret_vault_path FROM llm_providers"
                    " WHERE secret_vault_path IS NOT NULL AND secret_vault_path <> ''"
                )
            )
        ).all()
        for provider_id, display_name, path in rows:
            if not await self._vault.secret_exists(str(path)):
                out.append(
                    Divergence(
                        check="db<->vault",
                        severity=SEVERITY_CRITICAL,
                        subject=f"llm_provider {display_name} ({provider_id})",
                        detail=(
                            f"apunta a {path!r} y ese secreto no está en el Vault "
                            f"restaurado: el proveedor fallará a mitad del primer run"
                        ),
                    )
                )
        return out

    # -- BD <-> git ----------------------------------------------------------

    async def _check_db_vs_git(self, session: AsyncSession) -> list[Divergence]:
        out: list[Divergence] = []
        statuses = ", ".join(f"'{s}'" for s in _ACTIVE_PLAN_STATUSES)
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT p.id, p.title, p.slug, pr.slug AS project_slug,"
                        "       o.slug AS tenant_slug"
                        "  FROM plans p"
                        "  JOIN projects pr ON pr.id = p.project_id"
                        "  JOIN organizations o ON o.id = p.tenant_id"
                        f" WHERE p.deleted_at IS NULL AND p.status IN ({statuses})"
                    )
                )
            )
            .mappings()
            .all()
        )

        for row in rows:
            project_root = (
                self._data_root / "projects" / str(row["tenant_slug"]) / str(row["project_slug"])
            )
            repos_root = project_root / "repos"
            branch = make_plan_branch_name(str(row["id"]), str(row["slug"] or ""))
            bares = sorted(repos_root.glob("*.git")) if repos_root.is_dir() else []
            if not bares:
                out.append(
                    Divergence(
                        check="db<->git",
                        severity=SEVERITY_CRITICAL,
                        subject=f"plan {row['title']} ({row['id']})",
                        detail=(
                            f"el proyecto no tiene ningún bare repo en {repos_root}: "
                            f"el código del plan no volvió del backup"
                        ),
                    )
                )
                continue
            if not any(self._git.branch_exists(bare, branch) for bare in bares):
                out.append(
                    Divergence(
                        check="db<->git",
                        severity=SEVERITY_CRITICAL,
                        subject=f"plan {row['title']} ({row['id']})",
                        detail=(
                            f"la rama {branch!r} no está en ninguno de los repos "
                            f"{[b.name for b in bares]}: el trabajo de los agentes "
                            f"sobre ese plan no está restaurado"
                        ),
                    )
                )
        return out


@dataclass
class SubprocessGitProbe:
    """Sonda de git REAL — `git rev-parse --verify` contra el bare repo.

    Es la única de las tres que no se dobla en los tests: comprobar la
    existencia de una rama contra un doble sería comprobar el doble.
    """

    timeout_s: int = 30

    def repo_exists(self, repo_path: Path) -> bool:
        return (repo_path / "HEAD").is_file() or (repo_path / ".git").exists()

    def branch_exists(self, repo_path: Path, branch: str) -> bool:
        if not self.repo_exists(repo_path):
            return False
        done = subprocess.run(  # — argv explícito, nunca shell
            [
                "git",
                f"--git-dir={repo_path}",
                "rev-parse",
                "--verify",
                "--quiet",
                f"refs/heads/{branch}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=self.timeout_s,
        )
        return done.returncode == 0


async def reconcile_after_restore(
    *,
    settings: Settings | None = None,
    session: AsyncSession | None = None,
    objects: ObjectProbe | None = None,
    vault: VaultProbe | None = None,
    git: GitProbe | None = None,
) -> ReconcileReport:
    """Punto de entrada: construye las sondas de producción y reconcilia.

    `session` se inyecta en los tests; en producción se abre contra la base
    restaurada con la sesión admin del worker (la reconciliación es cross-tenant
    por definición — mira TODOS los tenants, es una operación de plataforma).
    """
    cfg = settings or get_settings()
    if objects is None:
        objects = _default_object_probe()
    reconciler = RestoreReconciler(
        objects=objects, vault=vault, git=git, data_root=Path(cfg.data_root)
    )
    if session is not None:
        return await reconciler.reconcile(session)

    from workers.db import worker_session

    async with worker_session(cfg) as db:
        return await reconciler.reconcile(db)


def _default_object_probe() -> ObjectProbe | None:
    """El `ObjectStorage` del api-server, si está disponible en este proceso.

    Devuelve ``None`` —y la comprobación se marca OMITIDA— en vez de fingir que
    cuadra: un informe que dice «correcto» sin haber mirado es peor que uno que
    dice «no lo he podido comprobar»."""
    try:
        from api_server.storage import get_object_storage

        probe: ObjectProbe = get_object_storage()
        return probe
    except Exception as exc:  # pragma: no cover — depende del despliegue
        _log.warning("restore.reconcile.no_object_probe", error=str(exc))
        return None


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: imprime el informe y devuelve 0 / 1 según haya divergencias críticas."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m workers.restore_reconcile",
        description=(
            "Reconcilia la base de datos restaurada contra MinIO, Vault y los "
            "repos git. Código de salida != 0 si hay divergencias críticas."
        ),
    )
    parser.add_argument("--json", action="store_true", help="salida en JSON en vez de texto")
    args = parser.parse_args(argv)

    report = asyncio.run(reconcile_after_restore())
    print(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) if args.json else report.render()
    )
    return report.exit_code


if __name__ == "__main__":  # pragma: no cover — entrypoint
    raise SystemExit(main())


__all__ = [
    "SEVERITY_CRITICAL",
    "SEVERITY_WARNING",
    "Divergence",
    "GitProbe",
    "ObjectProbe",
    "ReconcileReport",
    "RestoreReconciler",
    "SubprocessGitProbe",
    "VaultProbe",
    "main",
    "reconcile_after_restore",
]
