"""El pin de markdownlint es el mismo en pre-commit y en CI.

El defecto que cierra
---------------------
La puerta de markdown existía **sólo en CI**. Prettier sí corre en local sobre
markdown, pero formatea: no aplica las reglas de estilo de markdownlint. Así que
un ``*`` a principio de línea —que markdownlint lee como viñeta de otro estilo—
costaba un run completo de CI para enterarse. Pasó dos veces en dos días
(2026-08-19 y 2026-08-20).

El hook local lo arregla, pero **introduce su propio modo de fallo, y es peor que
el problema original**: si el pin del hook y el de CI se separan, el commit pasa
en local con una versión y rompe en CI con otra. Es exactamente la trampa de
``docs/03-guides/gotchas/ci-tool-version-drift.md``, y ya se pagó una vez en este
repo con prettier — donde el `rev` no fijaba la versión real y `--all-files`
pasaba en local mientras reescribía 16 ficheros en CI.

De ahí este test: la única forma de que un hook local valga es que corra lo mismo
que la puerta que dice representar.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_RAIZ = Path(__file__).resolve().parents[2]
_PRECOMMIT = _RAIZ / ".pre-commit-config.yaml"
_CI = _RAIZ / ".github" / "workflows" / "ci.yml"

_PIN = re.compile(r"markdownlint-cli@(\d+\.\d+\.\d+)")


def _pines(ruta: Path) -> set[str]:
    assert ruta.is_file(), f"no encuentro {ruta}"
    return set(_PIN.findall(ruta.read_text(encoding="utf-8")))


def test_both_files_pin_markdownlint() -> None:
    """No-vacuidad: sin esto, dos conjuntos vacíos serían «iguales».

    Y dice cuál de los dos lados desapareció, que es la mitad útil del mensaje:
    si se fue el de CI, la puerta ya no existe; si se fue el del hook, volvemos a
    enterarnos de los errores de markdown por un run de CI.
    """
    assert _pines(_PRECOMMIT), (
        ".pre-commit-config.yaml ya no fija `markdownlint-cli@X.Y.Z`. Sin el hook"
        " local, un error de markdown se descubre en CI y no al comitear."
    )
    assert _pines(_CI), (
        "ci.yml ya no fija `markdownlint-cli@X.Y.Z`. Si la puerta de CI se retiró,"
        " este test sobra; si sólo cambió de forma, arréglalo aquí."
    )


def test_the_pin_is_identical_on_both_sides() -> None:
    hook, ci = _pines(_PRECOMMIT), _pines(_CI)
    assert hook == ci, (
        f"el hook de pre-commit fija markdownlint {sorted(hook)} y CI"
        f" {sorted(ci)}. Con versiones distintas el commit pasa en local y rompe"
        " en CI — el hook deja de ser una red y pasa a ser una promesa falsa"
        " (docs/03-guides/gotchas/ci-tool-version-drift.md)."
    )
    assert len(hook) == 1, (
        f"hay más de una versión de markdownlint en juego: {sorted(hook)}."
        " Con dos pines distintos, cuál corre depende de qué fichero se lea."
    )


def test_both_sides_use_the_same_rule_file() -> None:
    """Mismo binario y mismas reglas: un `--config` distinto también divergiría.

    La versión es la mitad del contrato; la otra es el fichero de reglas. Con el
    mismo binario y dos configuraciones, el hook seguiría dando verde a lo que CI
    rechaza.
    """
    for ruta in (_PRECOMMIT, _CI):
        texto = ruta.read_text(encoding="utf-8")
        assert "--config .markdownlint.jsonc" in texto, (
            f"{ruta.name} invoca markdownlint sin `--config .markdownlint.jsonc`."
            " Con reglas distintas a cada lado, el hook da verde a lo que CI"
            " rechaza — que es el mismo fallo que este fichero viene a cerrar,"
            " por la otra mitad."
        )
