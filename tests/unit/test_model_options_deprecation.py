"""`GET /agents/model-options` queda RETIRADO (`plan-unificacion-provider-id`).

Agregaba por KIND y de cada kind exponía solo el proveedor activo más nuevo — la
semántica que el ADR 0082 vino a sustituir. Con dos filas del mismo kind (Ollama
local y Ollama cloud) escondía una de las dos y no había forma de fijar la que
se quiere; `provider-options` lista cada fila activa con su `provider_id`.

Se BORRA en vez de deprecarse. Primero se marcó `deprecated=True` por prudencia
—«no rompamos un SDK de ahí fuera»— y al comprobarlo resultó falso: los SDK se
generan solo del OpenAPI **v1** (`build_v1_openapi()`) y esta ruta vive en la
superficie de administración, fuera de `/api/v1`. Sin contrato que proteger y
con cero llamantes, dejar en pie un endpoint cuya semántica contradice al ADR
0082 solo mantiene una forma de elegir el proveedor equivocado.

Estos tests impiden que vuelva por descuido — y que reaparezca el patrón
«por kind» con otro nombre.
"""

from __future__ import annotations

import re
from pathlib import Path

_AGENTS = (
    Path(__file__).resolve().parents[2]
    / "apps"
    / "api-server"
    / "src"
    / "api_server"
    / "routers"
    / "agents.py"
)
_PANEL = Path(__file__).resolve().parents[2] / "apps" / "admin-panel"


def test_the_by_kind_endpoint_is_gone() -> None:
    source = _AGENTS.read_text(encoding="utf-8")
    assert not re.search(r'@router\.get\("/model-options"', source), (
        "volvió `/agents/model-options`: agrega por kind y esconde una de dos "
        "filas del mismo proveedor (ADR 0082)"
    )


def test_the_replacement_is_still_there() -> None:
    # No vacuo: sin esto, borrar las DOS rutas dejaría el test en verde.
    source = _AGENTS.read_text(encoding="utf-8")
    assert re.search(
        r'@router\.get\("/provider-options"', source
    ), "desapareció `/agents/provider-options`, que es el sustituto"


def test_the_dead_schema_went_with_it() -> None:
    schemas = (_AGENTS.parent.parent / "schemas" / "agents.py").read_text(encoding="utf-8")
    assert "AgentModelOptionsResponse" not in schemas


def test_no_frontend_surface_calls_the_removed_endpoint() -> None:
    """Borrarlo solo era seguro porque nadie de casa lo llamaba.

    Se buscan llamadas (`"/agents/model-options"` o `"/model-options"` dentro de
    un fetch), no menciones: los comentarios que explican por qué se dejó de
    usar son legítimos y no deben hacer fallar esto.
    """
    offenders: list[str] = []
    for path in (
        list(_PANEL.glob("app/**/*.tsx"))
        + list(_PANEL.glob("lib/**/*.ts"))
        + list(_PANEL.glob("components/**/*.tsx"))
    ):
        if "node_modules" in str(path):
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("*") or stripped.startswith("//"):
                continue
            if re.search(r'["\'`]/agents/model-options["\'`]', line):
                offenders.append(f"{path.relative_to(_PANEL)}:{line_no}")
    assert not offenders, f"siguen llamando al endpoint deprecado: {offenders}"
