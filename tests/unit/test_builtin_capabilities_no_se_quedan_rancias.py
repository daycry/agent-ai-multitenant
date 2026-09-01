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


# ---------------------------------------------------------------------------
# El agente TIENE QUE EXISTIR antes de que se le cablee nada
# ---------------------------------------------------------------------------
#: Los tres rosters, con el seed que CREA sus filas en `agents`. La distinción
#: entre crear el agente y cablearle capacidades no es teórica: el 2026-08-30 el
#: refresco re-aplicaba capacidades sobre agentes que daba por presentes, y al
#: añadir `ci4-tech-writer` el arranque falló contra la FK de
#: `agent_tools.agent_id`. El equipo se quedó a diez miembros en la base
#: mientras el código declaraba once, y sólo se supo mirando los logs.
_SEEDS_QUE_CREAN_AGENTES: dict[str, str] = {
    "agents": "seed_builtin_agents",
    "ci4_agents": "seed_ci4_agents",
    "qa_e2e_automator": "seed_qa_e2e_automator",
}

#: Y el seed que los mete en su equipo (`team_members.agent_id`, otra FK).
_SEEDS_DE_PERTENENCIA: dict[str, str] = {
    "teams": "seed_builtin_teams",
    "ci4_team": "seed_ci4_team",
}


def test_el_refresco_crea_los_agentes_antes_de_cablearlos() -> None:
    """Las tres mitades: que el agente exista, tenga capacidades y esté en su equipo.

    Un refresco que sólo cablea capacidades es media garantía. «Las tools de un
    agente built-in son de la PLATAFORMA» no se sostiene si el agente en sí sólo
    llega corriendo el CLI a mano — que es el defecto una vuelta más abajo.
    """
    referenciados = _nombres_referenciados(_funcion(_STARTUP, "refresh_builtin_agent_capabilities"))

    faltan = sorted(
        fn
        for fn in (*_SEEDS_QUE_CREAN_AGENTES.values(), *_SEEDS_DE_PERTENENCIA.values())
        if fn not in referenciados
    )
    assert not faltan, (
        f"el refresco de arranque no llama a {faltan}: un agente built-in nuevo "
        "no llegará a la base, y el cableado de sus tools fallará contra la FK"
    )


def test_los_agentes_van_antes_que_sus_capacidades() -> None:
    """El orden es la clave ajena, no una preferencia de estilo.

    `agent_tools.agent_id` y `agent_skills.agent_id` apuntan a `agents`, y
    `team_members.agent_id` también. Sembrar el cableado antes que el agente
    revienta; sembrar la pertenencia antes que el agente, igual.
    """
    # Se lee la TUPLA de pasos, no la función entera: el bloque de `import` lista
    # los seeds en orden alfabético, así que buscar por texto daba un orden que
    # no es el de ejecución — y el primer intento de este test falló por eso,
    # con `assert 8 < 0`.
    fn = _funcion(_STARTUP, "refresh_builtin_agent_capabilities")
    tupla = next(
        n.value
        for n in ast.walk(fn)
        if isinstance(n, ast.AnnAssign)
        and isinstance(n.target, ast.Name)
        and n.target.id == "pasos"
        and n.value is not None
    )
    orden = [x.id for x in ast.walk(tupla) if isinstance(x, ast.Name)]
    assert len(orden) >= 8, f"la tupla de pasos sólo trae {orden}"
    pos = {nombre: orden.index(nombre) for nombre in set(orden)}

    ultimo_agente = max(pos[fn] for fn in _SEEDS_QUE_CREAN_AGENTES.values())
    primera_capacidad = min(
        pos[fn]
        for fn in (
            "seed_builtin_agent_tools",
            "seed_builtin_agent_skills",
            "seed_ci4_agent_tools",
            "seed_ci4_agent_skills",
            "seed_qa_e2e_automator_tools",
            "seed_qa_e2e_automator_skills",
        )
    )
    assert ultimo_agente < primera_capacidad, (
        "hay un paso de capacidades ANTES del último paso que crea agentes: "
        "el cableado fallará contra `agent_tools.agent_id` para cualquier "
        "agente built-in que aún no exista en la base"
    )

    primera_pertenencia = min(pos[fn] for fn in _SEEDS_DE_PERTENENCIA.values())
    assert ultimo_agente < primera_pertenencia, (
        "la pertenencia al equipo se siembra antes de crear los agentes: "
        "fallará contra `team_members.agent_id`"
    )


# ---------------------------------------------------------------------------
# El CATÁLOGO en sí, no sólo el cableado
# ---------------------------------------------------------------------------
#: Los dos seeds que crean las filas de `tools` y `skills`. Son la mitad que
#: faltaba: el refresco re-aplicaba las junctions (`agent_tools`,
#: `agent_skills`) sobre un catálogo que nadie volvía a tocar.
#:
#: Medido el 2026-08-31. Se añadió el parámetro `recursive` al esquema de
#: `delete-file` y, tras desplegar, la base seguía sirviendo el esquema viejo:
#: el refresco había corrido entero y en verde. Hubo que sembrar a mano — que es
#: EXACTAMENTE el parche que este fichero existe para no volver a necesitar.
#:
#: Y el modo de fallo grave no es el esquema rancio sino el siguiente: una tool
#: NUEVA se reparte a un rol en el código, el catálogo no la tiene, y el paso de
#: cableado revienta contra la FK `agent_tools.tool_id`. El roster entero se
#: queda sin re-aplicar por una tool que nadie sembró.
_SEEDS_DEL_CATALOGO: dict[str, str] = {
    "tools": "seed_builtin_tools",
    "skills": "seed_builtin_skills",
}


def _pasos_del_refresco() -> list[str]:
    """Los nombres de paso del refresco, EN ORDEN.

    Se leen de la tupla `pasos` porque el orden es la FK, no una preferencia, y
    el test de abajo afirma sobre él.
    """
    fn = _funcion(_STARTUP, "refresh_builtin_agent_capabilities")
    for nodo in ast.walk(fn):
        # `pasos` lleva anotación de tipo, así que es un `AnnAssign`. Se aceptan
        # las dos formas para que quitar la anotación no deje el test ciego.
        if isinstance(nodo, ast.AnnAssign):
            destinos = {nodo.target.id} if isinstance(nodo.target, ast.Name) else set()
        elif isinstance(nodo, ast.Assign):
            destinos = {t.id for t in nodo.targets if isinstance(t, ast.Name)}
        else:
            continue
        if "pasos" not in destinos or not isinstance(nodo.value, ast.Tuple):
            continue
        nombres: list[str] = []
        for elt in nodo.value.elts:
            if isinstance(elt, ast.Tuple) and elt.elts:
                primero = elt.elts[0]
                if isinstance(primero, ast.Constant) and isinstance(primero.value, str):
                    nombres.append(primero.value)
        return nombres
    raise AssertionError("no se encontró la tupla `pasos` en el refresco")


def test_el_refresco_siembra_tambien_el_catalogo() -> None:
    """Las tools y skills EN SÍ, no sólo a quién se le reparten.

    Sin esto, cambiar el esquema de una tool built-in no llega nunca a la base:
    el refresco corre entero, en verde, y sirve el esquema de hace meses.
    """
    referenciados = _nombres_referenciados(_funcion(_STARTUP, "refresh_builtin_agent_capabilities"))

    faltan = sorted(fn for fn in _SEEDS_DEL_CATALOGO.values() if fn not in referenciados)
    assert not faltan, (
        f"el refresco no re-siembra {faltan}: el cableado se re-aplica sobre un "
        "catálogo que nadie actualiza, así que un esquema de tool cambiado —o "
        "una tool nueva— no llega a la base aunque el refresco salga en verde"
    )


def test_el_catalogo_se_siembra_antes_de_repartirlo() -> None:
    """El orden es la FK: `agent_tools.tool_id` referencia a `tools.id`.

    Cablear antes de sembrar revienta contra la clave ajena en cuanto se reparte
    una tool que el catálogo todavía no tiene — y con una transacción por paso,
    lo que se pierde es el roster entero de ese paso, no sólo la tool nueva.
    """
    pasos = _pasos_del_refresco()

    for catalogo in _SEEDS_DEL_CATALOGO:
        assert catalogo in pasos, f"el paso {catalogo!r} no está en la tupla `pasos` del refresco"

    cableado = [p for p in pasos if p.endswith(("_tools", "_skills"))]
    assert cableado, "no se encontró ningún paso de cableado: ¿cambió la convención?"

    primero_cableado = min(pasos.index(p) for p in cableado)
    for catalogo in _SEEDS_DEL_CATALOGO:
        assert pasos.index(catalogo) < primero_cableado, (
            f"el paso {catalogo!r} va DESPUÉS del primer paso de cableado "
            f"({pasos[primero_cableado]!r}): repartir una tool que el catálogo "
            "todavía no tiene revienta contra la FK `agent_tools.tool_id`"
        )
