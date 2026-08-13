"""Guarda: ninguna puerta de autorización de ``auth/deps.py`` sin llamante.

## Por qué existe

`require_admin_or_owner` vivió desde el ADR 0074 con **cero** `Depends(...)` en
todo `apps/`. No era inofensiva por estar muerta: venía con docstring
convincente y un test verde, así que quien necesitase «admin o owner» la habría
cableado creyendo que estaba en uso y probada en producción. Código muerto en la
superficie de autorización es el peor sitio donde tenerlo, y la única señal de
que lo estaba era un grep que nadie corría.

Esta guarda lo convierte en rojo de CI: cada dependencia ``require_*`` que
`auth/deps.py` define tiene que aparecer en al menos un `Depends(...)` del código
de aplicación, o estar en la allowlist de abajo **con motivo escrito**.

## Cómo NO pasar vacía

El modo de fallo nº4 de `verificar-antes-de-implementar`: el día que alguien
renombre el módulo o cambie la forma de declarar dependencias, el descubrimiento
devolvería cero puertas y cero infractores → verde silencioso. De ahí las
aserciones de que la guarda ENCONTRÓ puertas, ENCONTRÓ ficheros de aplicación y
ENCONTRÓ usos: las tres tienen que ser distintas de cero para que un `not
offenders` signifique algo.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEPS_MODULE = _REPO_ROOT / "apps" / "api-server" / "src" / "api_server" / "auth" / "deps.py"

#: Puertas SIN llamante que se aceptan, cada una con su motivo. Mantener esta
#: lista CORTA: una entrada nueva es una decisión, no un trámite.
_ALLOWED_WITHOUT_CALLERS = {
    # Factoría paramétrica documentada como punto de extensión en
    # docs/04-reference/rbac.md («parametric factory (rarely needed)») y ya
    # anotada como no usada por el ADR 0079. A diferencia de una puerta
    # concreta, no afirma una combinación de privilegios que pueda engañar a
    # quien la lea: construye la que le pidas. Se conserva a propósito.
    "require_tenant_role",
}

#: Las que sabemos que SÍ están cableadas. Sin esto, un cambio que dejara sin
#: llamantes a `require_system_admin` (70 usos hoy) podría colarse metiéndola en
#: la allowlist; aquí queda escrito que esas cuatro deben tener uso real.
_MUST_HAVE_CALLERS = {
    "require_system_admin",
    "require_system_owner",
    "require_tenant_member",
    "require_tenant_admin",
}


def _declared_gates() -> set[str]:
    """Los nombres ``require_*`` que define ``auth/deps.py`` (def o async def)."""
    source = _DEPS_MODULE.read_text(encoding="utf-8")
    return set(re.findall(r"^(?:async )?def (require_\w+)\(", source, flags=re.MULTILINE))


def _application_sources() -> list[Path]:
    """Todo el código de aplicación, excluyendo tests, node_modules y migraciones."""
    files: list[Path] = []
    for app_dir in sorted((_REPO_ROOT / "apps").iterdir()):
        src = app_dir / "src"
        if not src.is_dir():
            continue
        files.extend(p for p in src.rglob("*.py") if "__pycache__" not in p.parts)
    return files


def _depends_usages(sources: list[Path]) -> dict[str, int]:
    """Cuenta, por nombre de puerta, los ``Depends(<gate>`` del código de app."""
    joined = "\n".join(p.read_text(encoding="utf-8") for p in sources)
    counts: dict[str, int] = {}
    for name in re.findall(r"Depends\(\s*(require_\w+)", joined):
        counts[name] = counts.get(name, 0) + 1
    return counts


def test_the_guard_actually_discovers_something() -> None:
    """Sin esto, un renombrado dejaría la guarda pasando en vacío."""
    gates = _declared_gates()
    sources = _application_sources()
    usages = _depends_usages(sources)

    assert len(gates) >= 5, f"la guarda dejó de encontrar las puertas de deps.py (vio {gates})"
    assert len(sources) >= 100, f"la guarda dejó de encontrar el código de app (vio {len(sources)})"
    assert sum(usages.values()) >= 50, (
        f"la guarda dejó de encontrar usos de las puertas (vio {usages})"
    )


def test_no_authorization_gate_is_without_callers() -> None:
    gates = _declared_gates()
    usages = _depends_usages(_application_sources())

    orphans = sorted(
        name for name in gates if name not in usages and name not in _ALLOWED_WITHOUT_CALLERS
    )
    assert not orphans, (
        "puertas de autorización definidas en auth/deps.py sin un solo "
        f"Depends(...) en apps/: {orphans}. Cablearlas o retirarlas — una puerta "
        "con test verde y sin endpoint hace creer que el camino está probado. "
        "Si hay un motivo para conservarla, añádela a _ALLOWED_WITHOUT_CALLERS "
        "con ese motivo escrito."
    )


def test_the_load_bearing_gates_are_really_wired() -> None:
    """Contra-prueba: las puertas que sostienen el RBAC no pueden acabar en la
    allowlist ni quedarse sin uso sin que esto se ponga rojo."""
    usages = _depends_usages(_application_sources())
    missing = sorted(name for name in _MUST_HAVE_CALLERS if usages.get(name, 0) == 0)
    assert not missing, f"puertas que DEBEN estar cableadas y no lo están: {missing}"


def test_the_retired_composite_gate_is_gone() -> None:
    """`require_admin_or_owner` se retiró el 2026-07-30 y no debe volver sin
    endpoint. Si alguien la necesita de verdad, la repone CON su `Depends(...)` —
    y entonces esta aserción es lo único que hay que actualizar."""
    source = _DEPS_MODULE.read_text(encoding="utf-8")
    assert not re.search(r"^(?:async )?def require_admin_or_owner\(", source, flags=re.MULTILINE), (
        "require_admin_or_owner ha vuelto a auth/deps.py; comprueba que esta vez "
        "tiene endpoints que la usen"
    )
