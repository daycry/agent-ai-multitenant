"""La identidad git de la plataforma es de FUENTE ÚNICA (cadena-pr, causa raíz A).

La auditoría 2026-07-03 llamó «identidad git sin fuente única» a la derivación
triplicada de rama/bare, y T1/T2 la reconciliaron. Pero la OTRA identidad git —el
autor con el que la plataforma firma sus commits— seguía escrita a mano en tres
sitios, y **con dos valores distintos**:

  * `git_repos.seed_initial_commit_if_empty` → ``platform@agentic.local``
  * `plan_git.commit_task` (commits de tarea) → ``noreply@agentic.local``
  * `plan_git.push_review_to_bare` (committer del rebase) → ``noreply@agentic.local``

Consecuencia real: el historial de un mismo proyecto queda firmado por DOS autores
distintos, y los proveedores (GitHub/GitLab) atribuyen por email — cualquier mapeo,
allowlist o filtro por autor solo cubría uno de los dos. Este test pinea el módulo
único y que nadie vuelva a escribir el email a mano.
"""

from __future__ import annotations

import inspect
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKERS_SRC = _REPO_ROOT / "apps" / "workers" / "src" / "workers"


def test_identity_env_carries_author_and_committer() -> None:
    from workers.git_identity import PLATFORM_GIT_EMAIL, PLATFORM_GIT_NAME, git_identity_env

    env = git_identity_env()
    assert env == {
        "GIT_AUTHOR_NAME": PLATFORM_GIT_NAME,
        "GIT_AUTHOR_EMAIL": PLATFORM_GIT_EMAIL,
        "GIT_COMMITTER_NAME": PLATFORM_GIT_NAME,
        "GIT_COMMITTER_EMAIL": PLATFORM_GIT_EMAIL,
    }
    # Un email vacío o sin dominio hace que git falle o firme con la config del
    # host (identidad impredecible dentro del contenedor).
    assert "@" in PLATFORM_GIT_EMAIL and PLATFORM_GIT_NAME.strip()


def test_commit_task_defaults_to_the_single_source_identity() -> None:
    from workers.git_identity import PLATFORM_GIT_EMAIL, PLATFORM_GIT_NAME
    from workers.plan_git import commit_task

    params = inspect.signature(commit_task).parameters
    assert params["author_name"].default == PLATFORM_GIT_NAME
    assert params["author_email"].default == PLATFORM_GIT_EMAIL


def test_no_worker_module_hardcodes_a_git_identity_email() -> None:
    """La guarda: el email solo se escribe en `git_identity.py` (§4 de
    verificar-antes-de-implementar — con aserción de que encontró algo)."""
    from workers.git_identity import PLATFORM_GIT_EMAIL

    domain = PLATFORM_GIT_EMAIL.split("@", 1)[1]
    offenders: list[str] = []
    seen = 0
    for path in _WORKERS_SRC.rglob("*.py"):
        hits = path.read_text(encoding="utf-8").count(f"@{domain}")
        if not hits:
            continue
        if path.name == "git_identity.py":
            seen += hits
            continue
        offenders.append(f"{path.relative_to(_REPO_ROOT)} ({hits})")
    assert seen >= 1, f"la guarda dejó de ver la definición canónica (vio {seen})"
    assert not offenders, (
        "identidad git escrita a mano fuera de git_identity.py — importa "
        f"PLATFORM_GIT_NAME/PLATFORM_GIT_EMAIL/git_identity_env(): {offenders}"
    )
