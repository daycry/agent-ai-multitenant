"""El catálogo built-in se re-afirma en cada arranque, no sólo cuando falta.

El defecto que fija este fichero costó un run entero y se midió el 2026-08-30:

* el código repartía `stack-exec` a seis roles **desde julio**;
* `agent_tools` de los agentes de plataforma llevaba sin tocarse desde el
  **2026-06-28**;
* porque el seed completo es un CLI manual (`python -m api_server.seeds`) y la
  red de arranque que existía sólo garantiza filas **cuando faltan**, nunca
  cuando están presentes y desactualizadas.

Consecuencia medida: un agente pidió `stack_exec` a la primera, recibió «tool
not allowed in this mode», y se pasó 24 llamadas buscando `php` dentro de un
sandbox de Python hasta agotar reintentos — 2,22 USD y 62,2k tokens sin instalar
nada. Antes de eso, alguien ya había parcheado a mano las copias de dos tenants
(junio y julio) sin dejar detrás nada que impidiera la repetición. Esto es ese
«algo».

La distinción que sostiene todo lo de abajo: **sembrar cuando falta** y
**mantener al día** son garantías distintas, y la segunda es la que faltaba.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_RAIZ = Path(__file__).resolve().parents[2] / "apps" / "api-server" / "src" / "api_server"
_STARTUP = _RAIZ / "seeds" / "startup.py"
_MAIN = _RAIZ / "main.py"


def _funcion(ruta: Path, nombre: str) -> ast.AsyncFunctionDef:
    for nodo in ast.walk(ast.parse(ruta.read_text(encoding="utf-8"))):
        if isinstance(nodo, ast.AsyncFunctionDef) and nodo.name == nombre:
            return nodo
    raise AssertionError(f"no se encontró {nombre!r} en {ruta.name}")


def test_el_refresco_de_capacidades_existe_y_se_exporta() -> None:
    from api_server.seeds.startup import __all__, refresh_builtin_agent_capabilities

    assert callable(refresh_builtin_agent_capabilities)
    assert "refresh_builtin_agent_capabilities" in __all__, (
        "la función no está en __all__: quien la busque desde fuera no la encontrará"
    )


def _nombres_referenciados(fn: ast.AsyncFunctionDef) -> set[str]:
    """Identificadores que la función usa, se llamen o se pasen por referencia."""
    return {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}


def _pasos_de_capacidades() -> set[str]:
    """Los pasos del seed que cablean CAPACIDADES, derivados del seed mismo.

    La regla, mecánica a propósito: un `SeedStep` cablea capacidades si su
    nombre termina en ``_tools`` o ``_skills`` y no es el catálogo suelto
    (``tools`` / ``skills``, que siembran las tools y skills en sí, no las
    junctions). Derivarlo en vez de escribir una lista aquí es lo que hace que
    un sexto roster —cuando lo haya— entre solo en la comprobación.
    """
    fuente = (_RAIZ / "seeds" / "__main__.py").read_text(encoding="utf-8")
    pasos: set[str] = set()
    for nodo in ast.walk(ast.parse(fuente)):
        if not (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name)):
            continue
        if nodo.func.id != "SeedStep" or not nodo.args:
            continue
        primero = nodo.args[0]
        if not (isinstance(primero, ast.Constant) and isinstance(primero.value, str)):
            continue
        nombre = primero.value
        if nombre in {"tools", "skills"}:
            continue
        if nombre.endswith(("_tools", "_skills")):
            pasos.add(nombre)
    return pasos


def test_la_derivacion_de_pasos_encuentra_algo() -> None:
    """Si la derivación devolviera vacío, el test de cobertura pasaría a ciegas."""
    pasos = _pasos_de_capacidades()
    assert len(pasos) >= 6, (
        f"la derivación encontró {sorted(pasos)}; se esperaban al menos los "
        "seis pasos de cableado (built-in, CI4 y QA E2E, tools y skills)"
    )


def test_el_refresco_cubre_todos_los_rosters() -> None:
    """Ningún roster se queda fuera — el defecto de la primera versión.

    Aquella llamaba sólo a los dos seeds de ``BUILTIN_AGENTS`` y dejaba fuera a
    los diez agentes del equipo CodeIgniter 4, que son EL roster del incidente.
    Habría parecido que arreglaba el problema mientras el equipo que lo sufrió
    seguía exactamente igual: la peor forma de arreglarlo, porque además calla.
    """
    referenciados = _nombres_referenciados(_funcion(_STARTUP, "refresh_builtin_agent_capabilities"))
    esperados = {
        "agent_tools": "seed_builtin_agent_tools",
        "agent_skills": "seed_builtin_agent_skills",
        "ci4_agent_tools": "seed_ci4_agent_tools",
        "ci4_agent_skills": "seed_ci4_agent_skills",
        "qa_e2e_automator_tools": "seed_qa_e2e_automator_tools",
        "qa_e2e_automator_skills": "seed_qa_e2e_automator_skills",
    }

    # Que el mapa de arriba no envejezca respecto al seed real.
    assert set(esperados) == _pasos_de_capacidades(), (
        "el seed cablea capacidades en pasos que este test no conoce (o al "
        f"revés): seed={sorted(_pasos_de_capacidades())} test={sorted(esperados)}"
    )

    faltan = sorted(fn for fn in esperados.values() if fn not in referenciados)
    assert not faltan, (
        f"el refresco de arranque no re-aplica {faltan}: esos agentes built-in "
        "seguirán con lo que la base tenga de hace meses"
    )


def test_el_refresco_no_es_condicional_a_que_falte_algo() -> None:
    """La diferencia con la red de arranque anterior, y la razón de existir.

    Si esta función volviera a llevar un «si ya hay filas, salgo» —como hace
    `ensure_builtin_catalog`, que para su caso es correcto— dejaría de arreglar
    el defecto: el catálogo estaba PRESENTE y rancio.
    """
    fn = _funcion(_STARTUP, "refresh_builtin_agent_capabilities")
    cuerpo = fn.body

    bucles = [i for i, n in enumerate(cuerpo) if isinstance(n, (ast.For, ast.AsyncFor))]
    assert bucles, "el refresco ya no recorre los rosters: ¿sigue sembrando algo?"

    # Sólo lo que ocurre ANTES del bucle. El `return` FINAL es legítimo —
    # devuelve el parte de lo aplicado— y una versión anterior de este test lo
    # marcaba como defecto, que es afirmar sobre la forma en vez de sobre la
    # propiedad.
    antes = cuerpo[: bucles[0]]
    fugas = [n for n in antes for x in ast.walk(n) if isinstance(x, ast.Return)]
    assert not fugas, (
        "hay un `return` antes del bucle de siembra: la función volvió a ser "
        "'sembrar sólo cuando falta', que es justo lo que NO arregla el defecto "
        "(el catálogo estaba presente y rancio)"
    )


def test_el_arranque_lo_llama() -> None:
    """El eslabón que convierte la función en una garantía.

    Sin esta llamada la función es código muerto y el catálogo sigue
    envejeciendo en silencio, que es exactamente el estado del que venimos.
    """
    # Se busca una LLAMADA, no el nombre suelto. Buscar el texto no vale: el
    # `import` mantiene el nombre en el fichero aunque nadie invoque la función,
    # así que una versión anterior de este test seguía en verde con la llamada
    # sustituida por un `pass` — comprobado mutando, no razonado.
    fn = _funcion(_MAIN, "_ensure_builtin_catalog")
    llamadas = {
        n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }

    assert "refresh_builtin_agent_capabilities" in llamadas, (
        "el arranque IMPORTA el refresco pero no lo LLAMA: el catálogo built-in "
        "volverá a envejecer en silencio, que es el estado del que venimos"
    )


def test_el_fallo_del_refresco_no_impide_arrancar() -> None:
    """Un catálogo desactualizado deja agentes peor equipados; no arrancar deja
    la plataforma entera fuera. La degradación tiene que ser en ese orden."""
    fn = _funcion(_STARTUP, "refresh_builtin_agent_capabilities")

    manejadores = [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]
    assert manejadores, "el refresco no captura excepciones: un fallo tumbaría el arranque"

    for handler in manejadores:
        assert not any(isinstance(n, ast.Raise) for n in ast.walk(handler)), (
            "el manejador vuelve a lanzar: un problema de siembra impediría arrancar"
        )
