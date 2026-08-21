"""Guarda estática de la identidad git de fuente única (cadena-pr, criterio 3).

La causa raíz A de la auditoría 2026-07-03 fue una identidad git derivada en TRES
sitios con reglas distintas: la rama del plan y el nombre del bare se recalculaban
en `execution.py`, en `plan_pr.py` (`_slugify("Plan: " + title)`) y en
`repo_clone.py` (`basename(remote_url)`), así que el auto-PR apuntaba a una rama y
un bare que NO eran los de la ejecución. T1/T2 lo reconciliaron en
`plan_git.plan_git_identity` y borraron los helpers viejos, pero **el guard-test que
el criterio de cierre 3 del plan exige nunca se escribió** — nada impedía que la
divergencia volviera a colarse en el próximo módulo que necesite un bare o una rama.

Esta guarda es ese test. Tres invariantes sobre el código de producción:

  1. el nombre de la rama del plan se construye SOLO en `plan_git.py`;
  2. `make_plan_branch_name` se llama solo desde la lista blanca (el módulo canónico
     y la poda de worktrees, que no tiene proyecto a mano);
  3. el nombre del bare repo NUNCA se deriva de un nombre/título/URL — sale de
     `projects.slug` persistido (`project_slug` / `repo_name` / `identity.project_slug`).

Cada invariante lleva una aserción de que **encontró algo** (`docs/03-guides/
verificar-antes-de-implementar.md` §4): si un renombrado deja el descubrimiento a
cero, la guarda falla en vez de pasar vacíamente.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Raíces de producción escaneadas (los tests SÍ pueden reconstruir la derivación
#: antigua: `test_plan_git_identity` la documenta inline a propósito).
_SOURCE_ROOTS = (
    _REPO_ROOT / "apps" / "workers" / "src" / "workers",
    _REPO_ROOT / "apps" / "api-server" / "src" / "api_server",
)

#: El módulo canónico: la ÚNICA fuente de la identidad (rama + bare).
_CANONICAL = "plan_git.py"

#: Excepción documentada de la lista blanca: la poda de worktrees calcula el
#: conjunto de ramas vivas desde filas (plan_id, slug) de BD y no tiene el
#: `project_slug` que `plan_git_identity` pide. Usa el mismo generador de nombres,
#: así que no puede divergir.
#: Quién puede llamar al generador de la rama. La lista existe para que la
#: identidad de la rama tenga UNA fuente, no para prohibir el uso de la función
#: canónica: un llamante que la usa (en vez de formatear `plan/...` a mano) está
#: del lado correcto de la invariante y solo necesita quedar declarado aquí.
#: `restore_reconcile.py` la usa en SOLO LECTURA — deriva el nombre esperado para
#: comprobar si la rama existe en el bare tras una restauración, que es
#: exactamente la comparación DB↔git que ese reconciliador hace.
_BRANCH_NAME_ALLOWLIST = frozenset({_CANONICAL, "cleanup.py", "restore_reconcile.py"})

#: Señales de que un nombre de bare se está derivando de algo que NO es el slug
#: persistido — la forma exacta del defecto P2 (`_slugify(project.name)`,
#: `basename(remote_url)`).
_DERIVED_FROM_NAME = ("slugify", "remote_url", "basename", "rsplit", ".name", "title")

#: Llamadas cuyo argumento de nombre de bare se inspecciona, y el índice (0-based)
#: de ese argumento en cada una.
_BARE_NAME_CALLS = {
    "ensure_repo": 0,
    "bare_repo_path": 0,
    "fetch_remote": 0,
    "WorktreeManager": 1,  # (layout, repo_name)
}


def _python_sources() -> list[Path]:
    files = [p for root in _SOURCE_ROOTS for p in root.rglob("*.py")]
    assert len(files) >= 50, f"el descubrimiento de fuentes se rompió (vio {len(files)})"
    return files


def _balanced_args(text: str, open_paren: int) -> str | None:
    """El texto entre el paréntesis de apertura en ``open_paren`` y su pareja."""
    depth = 0
    for idx in range(open_paren, len(text)):
        char = text[idx]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1 : idx]
    return None


def _split_top_level(args: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in args:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def test_plan_branch_name_is_built_only_in_the_canonical_module() -> None:
    """Nadie más formatea `plan/...` a mano (invariante 1)."""
    literal = re.compile(r"""["']plan/""")
    offenders: list[str] = []
    seen = 0
    for path in _python_sources():
        hits = literal.findall(path.read_text(encoding="utf-8"))
        if not hits:
            continue
        if path.name == _CANONICAL:
            seen += len(hits)
            continue
        offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert seen >= 2, f"la guarda dejó de ver la construcción canónica de la rama (vio {seen})"
    assert not offenders, (
        "la rama del plan se construye fuera de plan_git.py — usa "
        f"plan_git_identity()/worktree_coordinates(): {offenders}"
    )


def test_branch_name_generator_callers_are_whitelisted() -> None:
    """`make_plan_branch_name` solo se llama desde la lista blanca (invariante 2)."""
    call = re.compile(r"\bmake_plan_branch_name\s*\(")
    offenders: list[str] = []
    seen = 0
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        hits = len(call.findall(text))
        if not hits:
            continue
        seen += hits
        if path.name not in _BRANCH_NAME_ALLOWLIST:
            offenders.append(f"{path.relative_to(_REPO_ROOT)} ({hits})")
    assert seen >= 3, f"la guarda dejó de encontrar los llamantes conocidos (vio {seen})"
    assert not offenders, (
        "nuevo llamante de make_plan_branch_name: deriva la identidad por "
        f"plan_git_identity()/worktree_coordinates() en su lugar: {offenders}"
    )


def test_bare_repo_name_never_derives_from_a_name_title_or_url() -> None:
    """El bare sale del slug persistido, no de un nombre/título/URL (invariante 3)."""
    offenders: list[str] = []
    seen = 0
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        for name, arg_index in _BARE_NAME_CALLS.items():
            for match in re.finditer(rf"\b{name}\s*\(", text):
                args = _balanced_args(text, match.end() - 1)
                if args is None:
                    continue
                parts = _split_top_level(args)
                if len(parts) <= arg_index:
                    continue  # se llama con el default (repo_name posicional ausente)
                expr = parts[arg_index]
                seen += 1
                bad = [token for token in _DERIVED_FROM_NAME if token in expr]
                if bad:
                    offenders.append(f"{path.relative_to(_REPO_ROOT)}: {name}({expr}) → {bad}")
    assert seen >= 6, f"la guarda dejó de encontrar las llamadas de bare repo (vio {seen})"
    assert not offenders, (
        "el nombre del bare repo se deriva de un nombre/título/URL (defecto P2 de la "
        f"auditoría 2026-07-03): usa projects.slug persistido: {offenders}"
    )
