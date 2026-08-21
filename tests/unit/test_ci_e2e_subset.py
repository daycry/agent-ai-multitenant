"""El subset e2e de CI se elige por exclusión, y la lista de exclusión no miente.

## El defecto que cierra

Hasta el 2026-08-21 el job «Frontend e2e (Playwright, mocked subset)» elegía sus
specs con::

    grep -rlE "page.route|\\.route\\(" e2e

Era un proxy TEXTUAL de «este spec es autocontenido», y envejeció en tres
direcciones a la vez:

* El ADR 0133 movió los mocks al helper ``seedSession``, así que **tres specs que
  pasan sin backend ninguno** —``dev-portal``, ``playwright-templates``,
  ``ingestion-progress``: 16 casos en 41,7 s medidos— dejaron de casar el patrón y
  CI se los saltaba.
* El ``grep -rl`` recorre el árbol entero, así que le pasaba a Playwright los
  ficheros de ``e2e/helpers/`` **como si fueran specs**.
* Seleccionaba 100 de 112 mientras el comentario del propio job decía 88.

Un selector que se equivoca en las dos direcciones a la vez —deja fuera lo que
cabe y mete lo que no es un spec— no informa de su cobertura: la simula.

## Por qué exclusión y no inclusión

La dirección es lo importante. Con una lista de EXCLUSIÓN, un spec nuevo entra en
CI por defecto y sólo sale si alguien declara que necesita backend vivo. Con una
de inclusión sería al contrario: el spec nuevo no correría y nadie se enteraría —
que es exactamente el modo de fallo que este fichero viene a cerrar, una capa más
arriba.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_RAIZ = Path(__file__).resolve().parents[2]
_E2E = _RAIZ / "apps" / "admin-panel" / "e2e"
_WORKFLOW = _RAIZ / ".github" / "workflows" / "ci.yml"

#: Los specs que hablan con un api-server DE VERDAD y por eso no caben en el
#: subset. Su arnés: `docs/03-guides/e2e-con-backend-vivo.md`.
#:
#: Son 9 y no 12: de los doce que no mockean con `page.route`, tres
#: (`dev-portal`, `playwright-templates`, `ingestion-progress`) se midieron el
#: 2026-08-21 pasando **sin backend ninguno**, así que entran al subset.
_NECESITAN_BACKEND = (
    "admin-login",
    "agents-catalog",
    "lang-switcher",
    "mfa-enrollment",
    "notification-config",
    "notification-inbox",
    "personal-assistant",
    "project-wizard",
    "team-detail",
)

#: Los tres que el grep dejaba fuera sin motivo. Se nombran para que quien vuelva
#: a tocar el selector sepa que su inclusión fue deliberada y medida.
_PASAN_SIN_BACKEND = ("dev-portal", "playwright-templates", "ingestion-progress")


def _todos_los_specs() -> list[str]:
    """`find e2e -maxdepth 1 -type f -name "*.spec.ts" | sort`, en Python.

    `maxdepth 1` importa: sin él entran los ficheros de `e2e/helpers/`, que es
    parte del defecto que se está cerrando.
    """
    return sorted(p.stem.removesuffix(".spec") for p in _E2E.glob("*.spec.ts"))


def _subset() -> list[str]:
    return [s for s in _todos_los_specs() if s not in _NECESITAN_BACKEND]


def test_the_discovery_finds_the_suite() -> None:
    """No-vacuidad: con cero specs, todo lo de abajo pasa sin comprobar nada."""
    todos = _todos_los_specs()
    assert len(todos) >= 100, (
        f"esperaba un centenar de specs en {_E2E}, encontré {len(todos)}."
        " O el descubrimiento se rompió, o la suite adelgazó de una forma que hay"
        " que mirar."
    )


def test_every_excluded_spec_exists() -> None:
    """Una exclusión que nombra un fichero inexistente no excluye nada.

    Es la mitad que impide que esta lista se vuelva prosa: un spec renombrado
    dejaría su nombre aquí, la exclusión no casaría con nada, y el spec entraría
    al subset sin backend — rojo en CI y nadie sabría por qué.
    """
    todos = set(_todos_los_specs())
    fantasmas = sorted(set(_NECESITAN_BACKEND) - todos)
    assert not fantasmas, (
        "estos nombres de `_NECESITAN_BACKEND` no existen como spec:"
        f" {fantasmas}. Si se renombraron, actualiza la lista Y la del workflow."
    )


def test_the_three_measured_specs_are_in_the_subset() -> None:
    """Los tres que el grep se saltaba siguen dentro.

    Se comprueba explícitamente porque su inclusión es el motivo del cambio: si
    alguien los saca «por si acaso», estos 16 casos vuelven a no correr en ningún
    sitio y la cobertura baja sin que nada lo diga.
    """
    subset = set(_subset())
    fuera = sorted(s for s in _PASAN_SIN_BACKEND if s not in subset)
    assert not fuera, (
        f"{fuera} deberían estar en el subset: se midió el 2026-08-21 que pasan"
        " sin backend (16 casos, 41,7 s). Si de verdad ya lo necesitan, muévelos"
        " a `_NECESITAN_BACKEND` y a la lista del workflow, y dilo en el commit."
    )


def test_no_helper_is_passed_as_a_spec() -> None:
    """`e2e/helpers/` no son specs, y el selector viejo se los pasaba a Playwright."""
    assert (_E2E / "helpers").is_dir(), "no encuentro e2e/helpers/: ¿se movió?"
    intrusos = [s for s in _todos_los_specs() if "helper" in s]
    assert not intrusos, f"el descubrimiento está recogiendo helpers: {intrusos}"


def test_the_workflow_still_uses_this_shape() -> None:
    """Si CI cambia el selector, este fichero deja de describir lo que CI hace.

    Es la mitad que impide que este test fije su propia idea del mundo: comprueba
    que en `ci.yml` siguen estando las tres piezas de las que dependen los tests
    de arriba, y que su lista de exclusión es LA MISMA.
    """
    assert _WORKFLOW.is_file(), f"no encuentro {_WORKFLOW}"
    texto = _WORKFLOW.read_text(encoding="utf-8")

    assert 'find e2e -maxdepth 1 -type f -name "*.spec.ts"' in texto, (
        "el workflow ya no descubre los specs con `find … -maxdepth 1`. Sin"
        " `maxdepth` vuelven a entrar los helpers, que es medio defecto original."
    )
    assert "grep -rlE" not in texto or "page.route" not in texto, (
        "el workflow ha vuelto al selector por `grep page.route`. Ese patrón es un"
        " proxy textual que ya falló en las dos direcciones: ver el docstring."
    )

    bloque = re.search(r"vivos=\(\s*(.*?)\s*\)", texto, re.S)
    assert bloque, "no encuentro la lista `vivos=(…)` en el workflow"
    en_workflow = set(bloque.group(1).split())
    assert en_workflow == set(_NECESITAN_BACKEND), (
        "la lista de exclusión del workflow y la de este test han divergido.\n"
        f"  sólo en el workflow: {sorted(en_workflow - set(_NECESITAN_BACKEND))}\n"
        f"  sólo en el test:     {sorted(set(_NECESITAN_BACKEND) - en_workflow)}\n"
        "Con dos listas distintas, este test certifica una cobertura que CI no"
        " tiene."
    )
