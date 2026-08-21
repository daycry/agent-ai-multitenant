"""Ningún pin de `constraints.txt` puede violar el rango que declara un pyproject.

Los `pyproject.toml` declaran RANGOS de compatibilidad (`pytest>=8.2,<9`) y
`constraints.txt` fija la VERSIÓN reproducible (`pytest==9.1.1`). Son dos capas
del mismo contrato, y si se contradicen la instalación no falla «un poco»: pip
aborta con `ResolutionImpossible` y **no se instala nada**.

Por qué hace falta una guarda y no basta con acordarse
-----------------------------------------------------
El 2026-08-13, actualizando dependencias, se subió `pytest` a 9.1.1 y
`pytest-asyncio` a 1.4.0 en `constraints.txt` sin tocar los `<9` y `<1` que
declaraban **catorce** ficheros. En local no se notó, porque un
``pip install --upgrade pytest`` se salta los rangos declarados: instala lo que
le pides. La CI, que resuelve desde los `pyproject`, murió en el paso de
instalación de CUATRO jobs a la vez — y el mensaje («Cannot install
api-server[dev]... conflicting dependencies») no nombra `constraints.txt` por
ningún lado.

O sea: el entorno donde se valida el cambio es MÁS PERMISIVO que el que lo
instala. Esa asimetría es la misma de
``docs/03-guides/gotchas/ci-no-tiene-docker-env-y-el-compose-lo-exige.md``, y se
cierra igual: comprobándolo aquí, donde se ve antes de empujar.

Al subir un pin que rompa su rango, la respuesta correcta es **subir el rango**
en los pyproject que lo declaran, no bajar el pin: el rango dice «con qué
versiones sé funcionar» y actualizarlo es parte de adoptar la versión nueva.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]
_CONSTRAINTS = _REPO / "constraints.txt"
_PIN_RE = re.compile(r"^([A-Za-z0-9._-]+)==([^\s;]+)")


def _norm(name: str) -> str:
    return name.lower().replace("_", "-")


def _pins() -> dict[str, str]:
    out: dict[str, str] = {}
    for linea in _CONSTRAINTS.read_text(encoding="utf-8").splitlines():
        m = _PIN_RE.match(linea.strip())
        if m:
            out[_norm(m.group(1))] = m.group(2)
    return out


def _pyprojects() -> list[Path]:
    return [
        p
        for p in _REPO.rglob("pyproject.toml")
        if ".venv" not in p.parts and "node_modules" not in p.parts and "build" not in p.parts
    ]


def _declared(path: Path) -> list[str]:
    """Todo requisito declarado: dependencias, extras y grupos (PEP 735)."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    proyecto = data.get("project", {}) or {}
    reqs: list[str] = list(proyecto.get("dependencies") or [])
    for lista in (proyecto.get("optional-dependencies") or {}).values():
        reqs += list(lista)
    for lista in (data.get("dependency-groups") or {}).values():
        reqs += [r for r in lista if isinstance(r, str)]
    return reqs


def test_constraints_file_is_readable_and_not_empty() -> None:
    """Si esta guarda leyera un fichero vacío pasaría en vacío, que es peor que fallar."""
    pins = _pins()
    assert len(pins) > 50, (
        f"constraints.txt sólo aporta {len(pins)} pines: o está truncado o el "
        "formato cambió, y esta guarda estaría comprobando la nada."
    )


def test_pyprojects_are_found() -> None:
    """Idem: sin pyproject que leer, el test de abajo no verificaría nada."""
    encontrados = _pyprojects()
    assert len(encontrados) >= 10, (
        f"sólo se encontraron {len(encontrados)} pyproject.toml; se esperaban los "
        "13 miembros del workspace más la raíz."
    )


def test_no_pin_violates_a_declared_range() -> None:
    violaciones: list[str] = []
    pins = _pins()
    for path in _pyprojects():
        rel = path.relative_to(_REPO).as_posix()
        for crudo in _declared(path):
            try:
                req = Requirement(crudo)
            except Exception:
                continue
            nombre = _norm(req.name)
            fijado = pins.get(nombre)
            if not fijado or not req.specifier:
                continue
            try:
                version = Version(fijado)
            except InvalidVersion:
                continue
            if not req.specifier.contains(version, prereleases=True):
                violaciones.append(
                    f"{rel}: declara «{req.name}{req.specifier}» pero constraints.txt "
                    f"fija {req.name}=={fijado}"
                )

    assert not violaciones, (
        "Un pin contradice el rango que declara un pyproject. `pip install -e … "
        "-c constraints.txt` abortará con ResolutionImpossible y NO instalará nada "
        "—es lo que tumbó cuatro jobs de CI el 2026-08-13—.\n\n"
        + "\n".join(f"  · {v}" for v in sorted(violaciones))
        + "\n\nSube el RANGO en esos ficheros (adoptar una versión nueva incluye "
        "declarar que sabes funcionar con ella), no bajes el pin."
    )
