"""La guarda de tenant del equipo no puede impedir lo que sí es correcto.

Este fichero existe porque la primera versión de esa guarda —escrita para cerrar
el agujero de asignar un equipo de otro tenant por API— introdujo **dos
regresiones peores que el defecto que cerraba**:

1. Validaba `payload.team_id` ANTES de resolver la adopción de la plantilla. El
   asistente manda el id del equipo built-in **esperando que se forkee**, así que
   la creación normal desde plantilla pasó a devolver 422. El defecto original
   dejaba un proyecto que no podía planificar; la «corrección» dejaba un proyecto
   que no se podía crear.

2. Validaba en CADA update que trajera `team_id`, aunque no cambiase. Los
   proyectos que ya arrastran un equipo ajeno —los que el defecto creó, y que hay
   que poder reparar— quedaban sin poder editarse en nada, ni el nombre.

La regla que queda fijada, y vale más allá del caso: **una guarda se aplica al
estado final, no al payload**, y **sólo cuando el estado cambia**. Comprobar la
intención en vez del resultado es cómo una guarda correcta se convierte en un
falso fallo.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_RUTA = (
    Path(__file__).resolve().parents[2]
    / "apps"
    / "api-server"
    / "src"
    / "api_server"
    / "routers"
    / "projects.py"
)


def _funcion(nombre: str) -> ast.AsyncFunctionDef:
    arbol = ast.parse(_RUTA.read_text(encoding="utf-8"))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.AsyncFunctionDef) and nodo.name == nombre:
            return nodo
    raise AssertionError(f"no se encontró la corrutina {nombre!r} en {_RUTA.name}")


def _llamada_a_la_guarda(fn: ast.AsyncFunctionDef) -> ast.Call:
    for nodo in ast.walk(fn):
        if (
            isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Name)
            and nodo.func.id == "_verify_team_visible"
        ):
            return nodo
    raise AssertionError(f"{fn.name} ya no llama a la guarda: ¿se retiró sin querer?")


def test_al_crear_se_valida_el_equipo_final_no_el_del_payload() -> None:
    """El argumento tiene que ser el equipo EFECTIVO, el que queda tras el fork.

    Con `payload.team_id` la creación desde plantilla devuelve 422, porque el
    asistente manda el built-in a sabiendas de que se va a forkear.
    """
    llamada = _llamada_a_la_guarda(_funcion("create_project"))
    argumento = llamada.args[1]

    assert isinstance(argumento, ast.Name), (
        "la guarda de creación recibe una expresión inesperada; se esperaba el "
        "identificador del equipo efectivo"
    )
    assert argumento.id == "effective_team_id", (
        f"la guarda valida {argumento.id!r}: si vuelve a ser el del payload, crear "
        f"un proyecto desde plantilla devolverá 422 antes de llegar al fork"
    )


def test_al_crear_la_guarda_va_despues_de_resolver_la_adopcion() -> None:
    """El orden importa tanto como el argumento: antes de `_resolve_template_adoption`
    todavía no se sabe si habrá fork, así que no hay nada que validar."""
    fn = _funcion("create_project")
    linea_adopcion = next(
        n.lineno
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_resolve_template_adoption"
    )

    assert _llamada_a_la_guarda(fn).lineno > linea_adopcion, (
        "la guarda corre ANTES de resolver la adopción: en ese punto `fork_team` "
        "todavía no está decidido y el rechazo es prematuro"
    )


def test_al_crear_no_se_valida_cuando_va_a_haber_fork() -> None:
    """Si se forkea, el equipo final es una copia del tenant y no hay nada que objetar."""
    fn = _funcion("create_project")
    guarda = _llamada_a_la_guarda(fn)

    condicion = next(
        n for n in ast.walk(fn) if isinstance(n, ast.If) and any(c is guarda for c in ast.walk(n))
    )
    fuente = ast.unparse(condicion.test)

    assert "fork_team" in fuente, (
        f"la condición de la guarda ({fuente!r}) no mira `fork_team`: rechazaría "
        f"creaciones que iban a terminar con una copia perfectamente válida"
    )


def test_al_actualizar_solo_se_valida_si_el_equipo_cambia() -> None:
    """Un formulario que reenvía el valor actual no está pidiendo nada.

    Sin esto, los proyectos que YA arrastran un equipo ajeno —los que el defecto
    original creó— se quedan sin poder editarse en nada.
    """
    fn = _funcion("update_project")
    guarda = _llamada_a_la_guarda(fn)

    condicion = next(
        n for n in ast.walk(fn) if isinstance(n, ast.If) and any(c is guarda for c in ast.walk(n))
    )
    fuente = ast.unparse(condicion.test)

    assert "project.team_id" in fuente, (
        f"la condición del update ({fuente!r}) no compara contra el equipo actual "
        f"del proyecto: un proyecto ya roto no se podrá reparar ni renombrar"
    )
