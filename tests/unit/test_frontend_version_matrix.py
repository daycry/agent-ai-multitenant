"""La matriz Next/ESLint/TypeScript está FIJADA, y el lint es un gate.

`task_audit14_09` de `remediacion-auditoria-integral-2026-07-14` pedía tres cosas:
corregir los ocho warnings de `react-hooks/exhaustive-deps` (hecho en las cinco
páginas que los producían), **elegir una combinación soportada y fijada** de
Next/ESLint/TypeScript, y **convertir los warnings de lint en gate**. Las dos
últimas son las que vigila este fichero, porque si no las vigila nadie vuelven
solas: un `^` en `typescript` basta para que dos máquinas linten con reglas
distintas, y un `next lint` sin `--max-warnings=0` deja pasar en verde
exactamente lo que la casilla acababa de limpiar.

Tres invariantes, y ninguno pasa en vacío (§4 de verificar-antes-de-implementar:
cada uno afirma primero que ENCONTRÓ lo que iba a comprobar):

1. **`eslint-config-next` va clavado a la versión de `next`.** No es cosmético:
   la config viaja con el framework y una desalineación cambia el conjunto de
   reglas sin avisar. Es la única de las cuatro cuya versión no se elige.
2. **Las cuatro son pines exactos**, y el `package-lock.json` resuelve
   exactamente a ellos — un pin que el lockfile contradice no es un pin, es una
   declaración de intenciones.
3. **`npm run lint` lleva `--max-warnings=0`** en las dos apps de Next. CI sólo
   ejecuta el del `admin-panel`, pero el del `installer` se pone igual: dejarlo
   fuera sería reintroducir la asimetría por la que tres casillas de `prod-08`
   estaban «hechas» en el stack de desarrollo y ausentes del generado.

Lo que este test NO afirma: que `next lint` sea la herramienta correcta a largo
plazo. Está deprecado y desaparece en Next 16; migrar al CLI de ESLint es trabajo
de la casilla que suba de mayor, y meterlo aquí habría sido ampliar el alcance.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

#: Las dos apps Next del repo. Si aparece una tercera y no se añade aquí, el
#: test no se entera — por eso `test_the_matrix_covers_every_next_app` la busca.
NEXT_APPS: tuple[str, ...] = ("apps/admin-panel", "apps/installer")

#: Las cuatro piezas de la matriz. `eslint-config-next` está aparte porque su
#: versión la dicta `next`, no nosotros.
PINNED: tuple[str, ...] = ("next", "eslint-config-next", "eslint", "typescript")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _package_json(app: str) -> dict:
    path = _repo_root() / app / "package.json"
    assert path.is_file(), f"no encuentro {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _declared(pkg: dict, name: str) -> str | None:
    for section in ("dependencies", "devDependencies"):
        value = pkg.get(section, {}).get(name)
        if value is not None:
            return str(value)
    return None


@pytest.mark.parametrize("app", NEXT_APPS)
def test_eslint_config_next_tracks_next_exactly(app: str) -> None:
    pkg = _package_json(app)
    next_version = _declared(pkg, "next")
    config_version = _declared(pkg, "eslint-config-next")
    assert next_version, f"{app}: no declara `next`; ¿sigue siendo una app Next?"
    assert config_version, f"{app}: no declara `eslint-config-next`"
    assert next_version == config_version, (
        f"{app}: `next` va por {next_version} y `eslint-config-next` por"
        f" {config_version}. La config de lint viaja con el framework: si se"
        " desalinean, el conjunto de reglas cambia sin que nadie lo decida."
    )


@pytest.mark.parametrize("app", NEXT_APPS)
def test_the_matrix_is_pinned_and_the_lockfile_agrees(app: str) -> None:
    pkg = _package_json(app)
    lock_path = _repo_root() / app / "package-lock.json"
    assert lock_path.is_file(), f"no encuentro {lock_path}"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    packages = lock.get("packages", {})
    assert packages, f"{app}: el package-lock.json no trae `packages`"

    for name in PINNED:
        declared = _declared(pkg, name)
        assert declared, f"{app}: no declara `{name}`, que es parte de la matriz"
        assert declared[0].isdigit(), (
            f"{app}: `{name}` está declarado como {declared!r}. La matriz se fija"
            " con versión exacta: un rango deja que dos máquinas resuelvan"
            " compiladores o reglas de lint distintos y el rojo aparezca sólo en"
            " una de ellas."
        )
        resolved = (packages.get(f"node_modules/{name}") or {}).get("version")
        assert resolved, f"{app}: el lockfile no resuelve `{name}`"
        assert resolved == declared, (
            f"{app}: `{name}` está pineado a {declared} pero el lockfile trae"
            f" {resolved}. Un pin que el lockfile contradice no es un pin."
        )


@pytest.mark.parametrize("app", NEXT_APPS)
def test_lint_is_a_gate_not_a_report(app: str) -> None:
    pkg = _package_json(app)
    lint = pkg.get("scripts", {}).get("lint")
    assert lint, f"{app}: no tiene script `lint`"
    assert "--max-warnings=0" in lint, (
        f"{app}: `npm run lint` es {lint!r}. Sin `--max-warnings=0`, `next lint`"
        " sale con código 0 aunque emita warnings: los ocho de"
        " `react-hooks/exhaustive-deps` que cerró `task_audit14_09` vivieron"
        " meses en verde precisamente así."
    )


def test_the_matrix_covers_every_next_app() -> None:
    """Si alguien añade una tercera app Next, este test la encuentra.

    Sin esto, los tres de arriba seguirían en verde sobre las dos de siempre
    mientras la nueva lintea sin gate y con versiones sueltas.
    """
    root = _repo_root() / "apps"
    encontradas = {
        str(p.parent.relative_to(_repo_root())).replace("\\", "/")
        for p in root.glob("*/package.json")
        if "next" in json.loads(p.read_text(encoding="utf-8")).get("dependencies", {})
    }
    assert encontradas, "no encontré NINGUNA app Next: el descubrimiento está roto"
    assert encontradas == set(NEXT_APPS), (
        f"las apps Next del repo son {sorted(encontradas)} y NEXT_APPS dice"
        f" {sorted(NEXT_APPS)}. Añade la que falte a la lista: si no, queda sin"
        " gate de lint y sin matriz fijada."
    )
