"""El estado REAL del tracing OTEL (prod-08 Fase D, hallazgo observability-4).

``telemetry/setup.py`` prometía en su docstring «Auto-instrumentation for
FastAPI, SQLAlchemy, asyncpg, Redis, httpx» y el ``pyproject`` declaraba
``opentelemetry-instrumentation-sqlalchemy``. Pero ``SQLAlchemyInstrumentor``
**no se invoca en ninguna parte**: la instrumentación de SQLAlchemy nunca
existió. Una dependencia que se instala en la imagen y un docstring que miente
al siguiente que lo lea.

Peor que inútil: un operador leyendo ese docstring podía concluir que las
queries lentas ya estaban trazadas y buscar el problema en otro sitio.

El ADR 0140 resuelve el recorte explícito (opción A del plan): el único
exporter es Console opt-in, el tracing distribuido queda fuera de alcance v1 y
la correlación la cubre `request_id` (Fase C). Estos tests fijan ese contrato
para que la dependencia muerta no vuelva a colarse.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_SETUP_PY = _ROOT / "apps" / "api-server" / "src" / "api_server" / "telemetry" / "setup.py"
_PYPROJECT = _ROOT / "apps" / "api-server" / "pyproject.toml"


def _declared_dependencies() -> list[str]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return list(data["project"]["dependencies"])


def test_sqlalchemy_instrumentation_dependency_is_not_declared() -> None:
    """Nadie la invoca: declararla solo engorda la imagen y miente."""
    offenders = [
        dep for dep in _declared_dependencies() if "instrumentation-sqlalchemy" in dep.lower()
    ]

    assert not offenders, (
        "`opentelemetry-instrumentation-sqlalchemy` sigue declarada pero "
        f"SQLAlchemyInstrumentor no se invoca en ningún sitio: {offenders}"
    )


def test_the_instrumentors_declared_as_dependencies_are_the_ones_invoked() -> None:
    """Guarda general: cada `opentelemetry-instrumentation-X` del pyproject
    debe tener un `X...Instrumentor` invocado en el código.

    Sin esto, la próxima dependencia muerta entra sin que nadie lo note — que
    es exactamente cómo entró la de SQLAlchemy.
    """
    source = _SETUP_PY.read_text(encoding="utf-8")
    declared = [
        dep.split(">=")[0].split("==")[0].strip('"').strip()
        for dep in _declared_dependencies()
        if dep.startswith("opentelemetry-instrumentation-")
    ]

    # Guarda contra el paso en vacío (§4 de verificar-antes-de-implementar).
    assert declared, "la guarda dejó de encontrar instrumentaciones OTEL declaradas"

    # Los nombres de clase `*Instrumentor` que el módulo invoca de verdad. Se
    # comparan por PREFIJO y no por igualdad porque la clase no siempre se
    # llama como el paquete: `opentelemetry-instrumentation-httpx` expone
    # `HTTPXClientInstrumentor`, no `HTTPXInstrumentor`.
    invoked = {name.lower() for name in re.findall(r"\b(\w+Instrumentor)\b", source)}
    assert invoked, "la guarda dejó de encontrar instrumentors invocados"

    dead = []
    for dep in declared:
        target = dep.removeprefix("opentelemetry-instrumentation-").replace("-", "")
        if not any(name.startswith(target) for name in invoked):
            dead.append(dep)

    assert not dead, f"instrumentaciones declaradas que nadie invoca: {dead}"


def test_the_docstring_does_not_promise_tracing_that_does_not_happen() -> None:
    """El docstring debe describir lo que el módulo hace HOY."""
    docstring = _SETUP_PY.read_text(encoding="utf-8").split('"""')[1]

    # Este test afirma sobre PROSA, así que se apoya en contratos positivos.
    # La guarda con dientes contra que la mentira vuelva es la de las
    # dependencias (los dos tests de arriba), que es ejecutable.
    #
    # No se puede exigir que la cadena «for FastAPI, SQLAlchemy» desaparezca:
    # el docstring cita a propósito la promesa antigua para explicar por qué
    # se retiró. Lo que sí se exige es la advertencia explícita.
    assert "SQLAlchemy NO" in docstring, (
        "el docstring debe decir EXPLÍCITAMENTE que SQLAlchemy no se instrumenta; "
        "omitirlo deja al lector suponiendo lo contrario"
    )
    lowered = docstring.lower()
    assert "console" in lowered, (
        "el docstring debe decir que el ÚNICO exporter es Console opt-in "
        "(API_SERVER_OTEL_CONSOLE=1), que es la verdad operativa"
    )


def test_console_exporter_stays_opt_in() -> None:
    """El exporter de consola spamea stdout y en Windows tumba uvicorn.

    Debe seguir detrás de la env var; si alguien lo activa por defecto, este
    test lo caza.
    """
    main = (_ROOT / "apps" / "api-server" / "src" / "api_server" / "main.py").read_text(
        encoding="utf-8"
    )

    assert 'os.environ.get("API_SERVER_OTEL_CONSOLE") == "1"' in main
