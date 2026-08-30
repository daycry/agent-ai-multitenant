"""El texto de «Adoptar → Un proyecto» tiene que decir lo que la adopción HACE.

Defecto detectado revisando la tanda de H9 del recorrido E2E del 2026-08-29
(``docs/roadmap/2026-08-29-hallazgos-e2e-hello-world-v2.md``). H9b era que
adoptar con destino «un proyecto» dejaba el trabajo a medias: creaba el equipo
con sus agentes atados al proyecto, pero ``projects.team_id`` seguía apuntando
al anterior, así que había que ir a la ficha y seleccionarlo a mano. Se arregló
en ``POST /teams/{id}/adopt`` (``target_project.team_id = new_team.id``).

Lo que quedó desincronizado es el **texto de ayuda** del diálogo, que sólo
hablaba de la atadura: «El equipo y sus agentes quedan atados a un proyecto
concreto». Quien lo lee sigue creyendo que después tiene que asignarlo, y quien
no lo hace se queda con el equipo anterior — el mismo paso a medias que H9b
denunciaba, ahora en la copia en vez de en el código.

Este guard ata las DOS mitades: si alguien retira la asignación del router, o si
alguien devuelve el texto a la versión que sólo promete la atadura, la pareja
deja de cuadrar y esto se rompe. Un test que sólo mirase el texto sería
decorativo; uno que sólo mirase el router no habría visto nunca este defecto.

Se parsea el TSX en vez de importarlo por el mismo motivo que
``test_approval_policy_ui_categories.py``: el guard vive en la suite de Python,
que es la que puede leer también el router.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_DICTIONARY = _ROOT / "apps/admin-panel/lib/i18n/dictionary.ts"
_TEAMS_ROUTER = _ROOT / "apps/api-server/src/api_server/routers/teams.py"

# La línea que HACE la asignación (H9b). Si se mueve o se reescribe, este guard
# tiene que romperse y obligar a revisar el texto, no seguir en verde.
_ASSIGNMENT_LINE = "target_project.team_id = new_team.id"

# El texto tiene que prometer las dos cosas: la atadura Y que el proyecto pasa a
# usar el equipo. Las alternativas admiten reescrituras razonables de la copia
# sin admitir la versión vieja, que hablaba sólo de la atadura.
_SAYS_IT_IS_TIED = {
    "es": re.compile(r"atad", re.IGNORECASE),
    "en": re.compile(r"\btied\b", re.IGNORECASE),
}
_SAYS_IT_IS_ASSIGNED = {
    "es": re.compile(r"pasa a usarlo|lo usa|se asigna|queda asignad", re.IGNORECASE),
    "en": re.compile(r"starts using it|will use it|is assigned|gets assigned", re.IGNORECASE),
}


def _adopt_target_project_help() -> dict[str, str]:
    """``{"es": …, "en": …}`` leído de la clave ``adoptTargetProjectHelp``."""
    source = _DICTIONARY.read_text(encoding="utf-8")
    match = re.search(
        r"adoptTargetProjectHelp:\s*\{(.*?)\}",
        source,
        re.DOTALL,
    )
    assert match is not None, "no está la clave adoptTargetProjectHelp en el diccionario"
    block = match.group(1)
    entries = dict(re.findall(r'\b(es|en):\s*"((?:[^"\\]|\\.)*)"', block))
    assert set(entries) == {"es", "en"}, f"faltan idiomas en adoptTargetProjectHelp: {entries}"
    return entries


def test_the_two_files_are_where_this_guard_expects_them() -> None:
    """Si cualquiera de los dos se mueve, esto se rompe en vez de pasar en vacío
    (``verificar-antes-de-implementar`` §4): todo lo de abajo depende de leerlos."""
    assert _DICTIONARY.is_file(), f"no está {_DICTIONARY}"
    assert _TEAMS_ROUTER.is_file(), f"no está {_TEAMS_ROUTER}"


def test_adopting_into_a_project_actually_assigns_that_project() -> None:
    """La mitad de la promesa que vive en el código (H9b)."""
    source = _TEAMS_ROUTER.read_text(encoding="utf-8")
    assert _ASSIGNMENT_LINE in source, (
        "POST /teams/{id}/adopt ya no repunta projects.team_id al equipo adoptado; "
        "si es a propósito, el texto de adoptTargetProjectHelp deja de ser cierto"
    )


@pytest.mark.parametrize("lang", ["es", "en"])
def test_the_help_text_promises_the_assignment_too(lang: str) -> None:
    """La otra mitad: el texto que el operador lee ANTES de decidir el destino."""
    help_text = _adopt_target_project_help()[lang]
    assert _SAYS_IT_IS_TIED[lang].search(help_text), (
        f"el texto {lang!r} ya no dice que el equipo queda atado al proyecto: {help_text!r}"
    )
    assert _SAYS_IT_IS_ASSIGNED[lang].search(help_text), (
        f"el texto {lang!r} no dice que el proyecto pasa a USAR el equipo adoptado, "
        f"que es lo que la adopción hace desde H9b: {help_text!r}"
    )
