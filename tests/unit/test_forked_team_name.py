"""El nombre del equipo que nace al crear un proyecto con fork de equipo.

## Por qué esto existe ahora y no antes

`fork_team_into` bautiza la copia con el nombre del proyecto, y `teams.name` es
`String(120)` con un único por tenant sobre los vivos
(`uq_teams_tenant_name_live`, migración 0126). Mientras «personalizar el equipo»
fue una casilla desmarcada, los dos bordes —nombre repetido y nombre larguísimo—
casi no se alcanzaban.

El arreglo de H6 (recorrido E2E del 2026-08-29) los pone en el camino por
defecto: crear desde una plantilla con equipo built-in FORKEA siempre, porque
referenciar el built-in deja el proyecto sin agentes utilizables. Y la
plataforma SÍ permite dos proyectos homónimos en un tenant — el `slug` se
desempata con `-{id8}` justamente por eso. Sin este desempate, el segundo
proyecto homónimo respondía 409 `duplicate_team_name`, medido en
`tests/integration/test_projects_endpoints.py`.

El desempate imita al del slug a propósito: el nombre bonito para el caso
normal, y el id corto sólo cuando hace falta.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from api_server.routers.projects import FORKED_TEAM_NAME_SUFFIX, forked_team_name_candidates

pytestmark = pytest.mark.unit

_MAX = 120  # teams.name -> String(120)
_PROJECT_ID = UUID("1b43db92-62f7-4428-9982-861339e13949")


def test_the_preferred_name_is_the_readable_one() -> None:
    preferred, _ = forked_team_name_candidates("Hello World", _PROJECT_ID)
    assert preferred == f"Hello World{FORKED_TEAM_NAME_SUFFIX}"


def test_the_tiebreaker_differs_and_carries_the_short_id() -> None:
    preferred, tiebreak = forked_team_name_candidates("Hello World", _PROJECT_ID)
    assert tiebreak != preferred
    assert _PROJECT_ID.hex[:8] in tiebreak
    assert tiebreak.endswith(FORKED_TEAM_NAME_SUFFIX)


def test_two_projects_with_the_same_name_get_different_tiebreakers() -> None:
    """El desempate tiene que depender del PROYECTO: si dependiera sólo del
    nombre, el tercer proyecto homónimo volvería a chocar."""
    other = UUID("00000000-0000-4000-8000-0000000000ff")
    _, mine = forked_team_name_candidates("Hello World", _PROJECT_ID)
    _, theirs = forked_team_name_candidates("Hello World", other)
    assert mine != theirs


def test_a_project_name_longer_than_the_column_still_fits() -> None:
    """`name` del proyecto llega a 255 (`ProjectCreateRequest`) y la columna del
    equipo son 120: sin recorte esto era un `StringDataRightTruncation` a la
    cara del usuario en el camino por defecto."""
    long_name = "M" * 255
    preferred, tiebreak = forked_team_name_candidates(long_name, _PROJECT_ID)

    assert len(preferred) <= _MAX
    assert len(tiebreak) <= _MAX
    # El recorte no puede comerse lo que hace el nombre reconocible ni útil: el
    # sufijo se conserva entero, y el desempate sigue llevando el id.
    assert preferred.endswith(FORKED_TEAM_NAME_SUFFIX)
    assert tiebreak.endswith(FORKED_TEAM_NAME_SUFFIX)
    assert _PROJECT_ID.hex[:8] in tiebreak
    assert preferred.startswith("MMM")


def test_a_name_that_fits_exactly_is_not_trimmed() -> None:
    """Guarda contra un recorte demasiado goloso: lo que cabe, entra entero."""
    exact = "N" * (_MAX - len(FORKED_TEAM_NAME_SUFFIX))
    preferred, _ = forked_team_name_candidates(exact, _PROJECT_ID)
    assert preferred == f"{exact}{FORKED_TEAM_NAME_SUFFIX}"
    assert len(preferred) == _MAX


def test_surrounding_whitespace_never_survives_the_trim() -> None:
    """Recortar por el medio de un nombre puede dejar un espacio colgando justo
    antes del sufijo, y « — equipo» duplicado se lee como un error."""
    preferred, tiebreak = forked_team_name_candidates("Hola   ", _PROJECT_ID)
    assert preferred == f"Hola{FORKED_TEAM_NAME_SUFFIX}"
    assert "  " not in tiebreak
