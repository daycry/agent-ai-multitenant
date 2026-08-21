"""El índice de `docs/03-guides/gotchas/` no puede quedarse atrás de sus ficheros.

CLAUDE.md ordena buscar en esa carpeta ANTES de inventar una solución para un
error de infraestructura, y añadir la trampa si no estaba. Las dos mitades de esa
orden dependen del `README.md`: un gotcha que no está en el índice existe pero no
se encuentra, y quien no lo encuentra vuelve a pagar el día que costó escribirlo.

Cuando se escribió esta guarda (2026-08-12) el índice llevaba **21 ficheros de
retraso** sobre 86. No se ponen rojos aquí porque un test que nace rojo se
desactiva en vez de arreglarse; se inventarían en :data:`_SIN_INDEXAR_2026_08_12`
para que la deuda sea **visible y acotada**, y la guarda impide que crezca. Es el
mismo mecanismo que `test_gate_debt_inventory_has_not_grown` aplica a los
`gate_override` del roadmap, y por la misma razón: lo que no se cuenta, crece.

Al indexar uno de los pendientes, **bórralo de la lista**. El propio test lo exige
(`test_the_debt_inventory_has_no_ghosts`): una entrada que ya está indexada dice
que hay deuda donde ya no la hay, que es la manera de que un inventario deje de
creerse.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_GOTCHAS = Path(__file__).resolve().parents[2] / "docs" / "03-guides" / "gotchas"
_README = _GOTCHAS / "README.md"

#: Los que ya estaban sin indexar el día que se escribió la guarda. NO añadir
#: nada aquí: un gotcha nuevo se indexa, que cuesta una línea. Esta lista sólo
#: puede encoger.
_SIN_INDEXAR_2026_08_12 = frozenset(
    {
        "admin-panel-build-context-is-app-dir.md",
        "agent-runtime-egress-blocks-in-stack-llm.md",
        "api-server-manuals-needs-with-claude.md",
        "app-image-missing-runtime-deps.md",
        "cadvisor-containerd-snapshotter.md",
        "ci-tool-version-drift.md",
        "compose-healthcheck-tooling-missing.md",
        "docker-cap-drop-all-breaks-official-images.md",
        "engine-restart-mata-runs-en-vuelo.md",
        "entrypoint-root-home-asyncpg-eacces.md",
        "httpx-drops-secure-cookies-over-http.md",
        "nextjs-stale-next-cache-after-branch-switch.md",
        "orchestrator-workers-base-image-arg.md",
        "php-runtime-missing-intl-empty-logs.md",
        "plan-branch-commit-race-non-fast-forward.md",
        "postgres-force-rls-vs-bypassrls.md",
        "powershell-utf8-em-dash-and-native-stderr.md",
        "pytest-needs-the-repo-venv.md",
        "registry-proxy-composer-dist-403.md",
        "test-runtime-exec-blocked.md",
        "venv-local-por-detras-del-lock.md",
        "worktree-bind-dood-empty-vs-named-volume.md",
    }
)


def _gotcha_files() -> set[str]:
    return {p.name for p in _GOTCHAS.glob("*.md") if p.name != "README.md"}


def _indexed() -> set[str]:
    """Los ficheros enlazados desde el README, con o sin `./` delante."""
    texto = _README.read_text(encoding="utf-8")
    return {m.group(1) for m in re.finditer(r"\]\(\.?/?([a-z0-9][a-z0-9.-]*\.md)\)", texto)}


def test_every_gotcha_is_reachable_from_the_index() -> None:
    """Un gotcha fuera del índice es un gotcha que nadie encontrará."""
    huerfanos = _gotcha_files() - _indexed() - _SIN_INDEXAR_2026_08_12
    assert not huerfanos, (
        "Estos gotchas no están enlazados desde docs/03-guides/gotchas/README.md: "
        f"{sorted(huerfanos)}. Añádelos a la sección por área que les corresponda, "
        "con una línea que diga el SÍNTOMA — que es por lo que alguien buscará."
    )


def test_the_debt_inventory_has_no_ghosts() -> None:
    """Un pendiente ya indexado tiene que salir de la lista de pendientes."""
    ya_hechos = _SIN_INDEXAR_2026_08_12 & _indexed()
    assert not ya_hechos, (
        f"Estos ya están en el índice: {sorted(ya_hechos)}. Bórralos de "
        "_SIN_INDEXAR_2026_08_12 en este fichero: un inventario que cuenta deuda "
        "saldada deja de servir para medir la que queda."
    )


def test_the_inventory_does_not_name_files_that_no_longer_exist() -> None:
    """Y un pendiente borrado tampoco puede seguir contando como deuda."""
    fantasmas = _SIN_INDEXAR_2026_08_12 - _gotcha_files()
    assert not fantasmas, (
        f"_SIN_INDEXAR_2026_08_12 nombra ficheros que ya no existen: {sorted(fantasmas)}."
    )


def test_the_index_does_not_link_files_that_do_not_exist() -> None:
    """El fallo simétrico: un enlace roto en el índice."""
    rotos = {
        nombre
        for nombre in _indexed()
        if nombre != "README.md" and not (_GOTCHAS / nombre).exists()
    }
    assert not rotos, f"El README enlaza gotchas inexistentes: {sorted(rotos)}."
