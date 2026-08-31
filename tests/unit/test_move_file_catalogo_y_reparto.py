"""`move_file` está en el catálogo y llega a quien tiene que poder mover.

## Por qué existe este fichero

Medido en vivo el 2026-08-31, proyecto «Hello World CI4 v3» del tenant mediapro
(modelo `gpt-oss:120b` vía ollama). La tarea era instalar el esqueleto de
CodeIgniter 4, y `composer create-project . ` exige un directorio COMPLETAMENTE
vacío. En el paso 31 del segundo run el agente llegó **solo** a la solución
correcta::

    31 | composer create-project codeigniter4/framework tmpci   -> ok
    35 | delete_file {"path":"app","recursive":true}            -> ok, 85 FICHEROS
    39 | mkdir ci4tmp                                           -> BLOQUEADO
    51 | composer create-project codeigniter4/framework .       -> sigue fallando

Instalar en un temporal y mover el resultado son tres pasos; la familia `file`
del catálogo era exactamente `read_file` / `write_file` / `delete_file` /
`list_files`, así que de los tres el único que podía ejecutar era el
destructivo. Esos 85 ficheros eran el deliverable YA commiteado de la tarea
anterior, y `app/` no tenía nada de especial: era la entrada más grande de la
lista.

La guarda que rechaza borrar un árbol versionado (commit del mismo día) impide
el destrozo pero **no desatasca**: sin una forma de mover, el agente se queda sin
poder andamiar y quema el presupuesto. Este fichero fija la otra mitad — que el
camino legítimo exista y llegue.

## Qué fija, y en qué orden de fuerza

1. **El catálogo la ofrece** con el slug que el reparto nombra, con `source` y
   `destination`, y con las mismas convenciones que su hermana `delete-file`.
2. **El reparto es DERIVADO**: quien puede escribir y borrar puede mover, y no
   porque alguien se acordara de añadirla a nueve listas sino porque las tres
   puertas de escritura de ficheros viajan juntas. La comprobación es de
   TODO-O-NADA, que es la forma exacta que tendría el defecto si alguien
   volviera a repartir a mano.
3. **El reviewer no la recibe** — su worktree se monta en sólo lectura (ADR
   0095), y una puerta que rebota con EROFS no es una capacidad: es un turno
   quemado que el agente no puede distinguir de una ruta mal puesta.
4. **La FK del cableado**: todo slug que un roster reparte existe en el
   catálogo. Repartir una tool que `tools` no tiene revienta
   `agent_tools.tool_id` y, con una transacción por paso, se pierde el roster
   entero (por eso el refresco de arranque siembra los catálogos primero).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from api_server.seeds.builtin_agents import BUILTIN_AGENTS
from api_server.seeds.builtin_role_capabilities import (
    FILE_WRITING_TOOLS,
    ROLE_DEFAULT_TOOLS,
    ROLES_WITH_READ_ONLY_WORKSPACE,
    WORKSPACE_MUTATING_TOOLS,
)
from api_server.seeds.builtin_tools import BUILTIN_TOOLS
from api_server.seeds.ci4_team import CI4_AGENTS
from api_server.seeds.qa_e2e_automator import QA_E2E_AUTOMATOR
from shared_domain.tool_names import CANONICAL_TOOL_NAMES, to_canonical

pytestmark = pytest.mark.unit

_MOVER = "move-file"
_CATALOGO = {t.slug: t for t in BUILTIN_TOOLS}


def _rosters() -> tuple[tuple[str, str, frozenset[str]], ...]:
    """(slug, rol, tools EFECTIVAS) de los tres rosters de agentes built-in.

    Se lee `resolved_tool_slugs()` y no `tool_slugs`: lo que importa es lo que el
    agente RECIBE tras la derivación por rol (el equipo CI4 le quita al reviewer
    las tools que mutan el workspace y le añade `stack-exec` a quien ejecuta el
    toolchain). Afirmar sobre la lista declarada dejaría fuera justo el paso que
    protege al reviewer.

    El QA E2E Automator entra explícitamente porque vive fuera de
    `BUILTIN_AGENTS` — y esa es exactamente la razón por la que en su día se
    quedó sin tools sin que nada fallara.
    """
    return tuple(
        (a.slug, a.role, frozenset(a.resolved_tool_slugs()))
        for a in (*BUILTIN_AGENTS, QA_E2E_AUTOMATOR, *CI4_AGENTS)
    )


# ---------------------------------------------------------------------------
# 1. El catálogo la ofrece
# ---------------------------------------------------------------------------
def test_el_catalogo_ofrece_move_file() -> None:
    """Sin fila en el catálogo la tool es código muerto por el otro lado: el
    runtime la registraría y ningún agente la tendría asignada."""
    assert _MOVER in _CATALOGO, (
        f"el catálogo built-in no ofrece {_MOVER!r}; tiene {sorted(_CATALOGO)}"
    )
    assert _CATALOGO[_MOVER].name == "move_file", (
        "el nombre ejecutable no es el que el runtime registra: el cruce "
        "agente ∩ modo se calcula sobre nombres canónicos y saldría vacío"
    )


def test_el_esquema_pide_origen_y_destino() -> None:
    """Los dos argumentos sin los cuales «mover» no significa nada.

    Si el esquema no los declara, el modelo los inventa: el proveedor valida
    contra el esquema anunciado y la llamada se rechaza antes de llegar al
    ejecutor, que es un turno quemado con un error que el agente no puede
    resolver leyendo el resultado.
    """
    schema = _CATALOGO[_MOVER].input_schema
    props = schema["properties"]
    assert {"source", "destination"} <= set(props), sorted(props)
    assert set(schema["required"]) == {"source", "destination"}, schema["required"]
    for campo in ("source", "destination"):
        assert props[campo]["type"] == "string", campo
        assert props[campo].get("description", "").strip(), (
            f"{campo} sin descripción: el modelo tiene que saber que la ruta es "
            "relativa al worktree, no absoluta"
        )
    assert schema["additionalProperties"] is False


def test_la_sobrescritura_es_una_bandera_explicita_y_opcional() -> None:
    """Mismo trato que `recursive` en `delete-file`, y por el mismo motivo.

    Reemplazar un destino existente es la variante destructiva de mover: se pide
    a propósito, no se hereda del caso normal. Y tiene que ser OPCIONAL, porque
    el caso que motivó la tool —traerse a su sitio lo que un scaffolder generó en
    un temporal— mueve sobre rutas que no existen todavía.
    """
    schema = _CATALOGO[_MOVER].input_schema
    assert schema["properties"]["overwrite"]["type"] == "boolean"
    assert "overwrite" not in schema["required"]


def test_move_file_sigue_las_convenciones_de_delete_file() -> None:
    """Las convenciones se comprueban contra la fila de al lado, no contra
    literales: si mañana la familia `file` cambia de categoría o de nivel de
    seguridad, `move-file` no se queda sola con la convención vieja."""
    mover, borrar = _CATALOGO[_MOVER], _CATALOGO["delete-file"]
    for campo in ("category", "implementation_type", "security_level", "implementation_ref"):
        assert getattr(mover, campo) == getattr(borrar, campo), (
            f"{campo}: move-file dice {getattr(mover, campo)!r} y delete-file "
            f"{getattr(borrar, campo)!r}"
        )


def test_su_nombre_es_canonico_de_plataforma() -> None:
    """Un nombre no canónico rompe dos cosas a la vez: el gate de aprobación no
    puede darle categoría (su contrato exige claves canónicas) y `routers/tools`
    deja que un tenant registre una tool con ese nombre, que el registro por
    ToolSpec sustituiría en silencio."""
    assert "move_file" in CANONICAL_TOOL_NAMES
    assert to_canonical("move_file") == frozenset({"move_file"}), (
        "move_file resuelve a otro nombre: sería un alias, no un nombre de catálogo"
    )


def test_el_esquema_declarado_es_el_que_el_ejecutor_honra(tmp_path: Path) -> None:
    """El catálogo y el ejecutor son dos ficheros que se editan por separado.

    Un esquema que declara un argumento que el ejecutor ignora —o que se calla
    uno que sí honra— no falla de forma visible: el proveedor valida contra el
    esquema ANUNCIADO, la llamada llega al runtime y se comporta de otra manera.
    El agente ve un resultado que no cuadra con lo que le prometieron y
    reintenta. Por eso esto se conduce contra el ejecutor real en vez de
    compararlo con literales.

    Cruza el límite api-server ↔ agent-runtime, igual que
    `tests/unit/test_approval_gate_categories.py`, que también es unitario y
    también importa `agent_runtime` para atar el catálogo al gate: son imports
    puros, sin BD ni Docker.
    """
    from agent_runtime.file_tools import WorkspaceFiles

    raiz = tmp_path
    (raiz / "ci4tmp" / "app").mkdir(parents=True)
    (raiz / "ci4tmp" / "app" / "Config.php").write_text("<?php", encoding="utf-8")
    (raiz / "ci4tmp2" / "app").mkdir(parents=True)
    # `tracked_paths` vacío: aquí se comprueba la FIRMA, no las guardas de git
    # (ésas son del carril del runtime y tienen sus propios tests).
    files = WorkspaceFiles(root=str(raiz))

    entrada = _CATALOGO[_MOVER].input_schema["properties"]

    # 1. Los dos argumentos REQUERIDOS bastan para mover de verdad.
    ok = files.file_move({"source": "ci4tmp/app", "destination": "app"})
    assert ok.ok, ok.error
    assert (raiz / "app" / "Config.php").is_file()

    # 2. La bandera declarada es la que el ejecutor honra, no un nombre
    #    inventado: sin ella el destino existente se rechaza; con ella, entra.
    choca = files.file_move({"source": "ci4tmp2/app", "destination": "app"})
    assert not choca.ok and "overwrite" in (choca.error or ""), choca

    flag = "overwrite"
    assert flag in entrada, sorted(entrada)
    pisa = files.file_move({"source": "ci4tmp2/app", "destination": "app", flag: True})
    assert pisa.ok, pisa.error

    # 3. El ejecutor no devuelve nada que el catálogo no declare.
    declarada = set(_CATALOGO[_MOVER].output_schema["properties"])
    devuelta = set(pisa.output or {})
    assert devuelta <= declarada, (
        f"el ejecutor devuelve claves que el catálogo no declara: {sorted(devuelta - declarada)}"
    )
    # Y la declaración no es vacua: el caso que acaba de correr —reemplazar un
    # directorio— es el que más campos produce.
    assert {"moved", "source", "destination", "replaced"} <= devuelta, sorted(devuelta)


# ---------------------------------------------------------------------------
# 2. El reparto, derivado y no a mano
# ---------------------------------------------------------------------------
def test_mover_es_una_puerta_de_escritura_de_ficheros() -> None:
    """El criterio del módulo, en una afirmación: mover deja el workspace
    distinto de como lo encontró, así que va con `write-file` y `delete-file`."""
    assert _MOVER in FILE_WRITING_TOOLS
    assert FILE_WRITING_TOOLS <= WORKSPACE_MUTATING_TOOLS, (
        "hay una puerta que escribe ficheros y no cuenta como que muta el "
        "workspace: el filtro del reviewer la dejaría pasar"
    )


@pytest.mark.parametrize("rol", sorted(ROLE_DEFAULT_TOOLS))
def test_las_puertas_de_escritura_viajan_juntas_por_rol(rol: str) -> None:
    """TODO O NADA, que es la forma exacta que tendría el defecto.

    Añadir `move-file` a mano a unos roles y no a otros pasaría cualquier
    comprobación de «¿la tiene el backend?». Lo que no pasa es esto: un rol
    tiene las TRES puertas de escritura de ficheros o ninguna. Es el mismo
    invariante que ya se cobró tres veces el mismo defecto (`stack-exec` a
    brocha gorda, `write-file` en el reviewer, `_FILE_TOOLS` en el PM del equipo
    CI4): dos criterios en competencia y ninguno escrito.
    """
    tiene = frozenset(ROLE_DEFAULT_TOOLS[rol]) & FILE_WRITING_TOOLS
    assert tiene in (frozenset(), FILE_WRITING_TOOLS), (
        f"el rol {rol!r} tiene {sorted(tiene)} de las puertas de escritura "
        f"{sorted(FILE_WRITING_TOOLS)}: o escribe ficheros o no, no a medias"
    )


def test_quien_escribe_y_borra_puede_mover() -> None:
    """El criterio literal del encargo, comprobado rol a rol."""
    for rol, tools in ROLE_DEFAULT_TOOLS.items():
        escribe_y_borra = {"write-file", "delete-file"} <= set(tools)
        assert (_MOVER in tools) is escribe_y_borra, (
            f"rol {rol!r}: escribe_y_borra={escribe_y_borra} pero move-file={_MOVER in tools}"
        )


def test_los_rosters_no_reparten_las_puertas_a_medias() -> None:
    """La misma regla, ya derivada, sobre los agentes de verdad.

    El rol es la autoridad, pero quien acaba con las tools es el agente: los
    diez del equipo CodeIgniter 4 —el roster del incidente— declaran su lista y
    la derivan. Un `_FILE_TOOLS` escrito a mano que se olvidara de `move-file`
    dejaría a los agentes que ANDAMIAN sin la tool, que es el caso medido.
    """
    a_medias = [
        (slug, sorted(tools & FILE_WRITING_TOOLS))
        for slug, _rol, tools in _rosters()
        if tools & FILE_WRITING_TOOLS not in (frozenset(), FILE_WRITING_TOOLS)
    ]
    assert not a_medias, (
        f"agentes con parte de las puertas de escritura {sorted(FILE_WRITING_TOOLS)}: {a_medias}"
    )


def test_los_agentes_que_andamian_reciben_move_file() -> None:
    """La mitad positiva: la tool LLEGA.

    Sin esta afirmación, un reparto que no conceda `move-file` a nadie pasaría
    todos los tests de simetría de arriba en verde — vacuamente.
    """
    con_mover = sorted(slug for slug, _r, tools in _rosters() if _MOVER in tools)
    assert len(con_mover) >= 10, f"casi nadie puede mover: {con_mover}"
    # El roster del incidente, por su nombre: son los que instalan CodeIgniter.
    ci4_con_mover = {slug for slug in con_mover if slug.startswith("ci4-")}
    assert {"ci4-architect", "ci4-backend", "ci4-devops"} <= ci4_con_mover, (
        f"el equipo del run que se atascó sigue sin poder mover: {sorted(ci4_con_mover)}"
    )


# ---------------------------------------------------------------------------
# 3. El reviewer, no
# ---------------------------------------------------------------------------
def test_el_reviewer_no_puede_mover() -> None:
    """ADR 0095: en una ejecución de review el worker monta el worktree del
    implementador en SÓLO LECTURA. Desde ahí un move rebota con EROFS, y un
    error del sistema de ficheros es indistinguible de una ruta mal puesta, así
    que el agente reintenta."""
    for rol in ROLES_WITH_READ_ONLY_WORKSPACE:
        assert _MOVER not in ROLE_DEFAULT_TOOLS.get(rol, ()), rol


def test_ningun_agente_con_workspace_de_solo_lectura_recibe_move_file() -> None:
    """Y la derivación de cada roster lo cumple de verdad, no sólo el mapa.

    El equipo CI4 declara `_FILE_TOOLS` para los diez y quita después lo que
    muta el workspace. Si `move-file` no entrara en `WORKSPACE_MUTATING_TOOLS`,
    ese filtro la dejaría pasar y el `ci4-reviewer` acabaría con una puerta que
    rebota — exactamente lo que pasó con `write-file`/`delete-file` hasta el
    2026-08-30.
    """
    armados = sorted(
        slug
        for slug, rol, tools in _rosters()
        if rol in ROLES_WITH_READ_ONLY_WORKSPACE and _MOVER in tools
    )
    assert not armados, f"agentes con workspace de sólo lectura y move_file: {armados}"


# ---------------------------------------------------------------------------
# 4. La FK del cableado
# ---------------------------------------------------------------------------
def test_todo_slug_repartido_existe_en_el_catalogo() -> None:
    """`agent_tools.tool_id` referencia a `tools.id`.

    Repartir una tool que el catálogo todavía no tiene no falla «un poco»: el
    paso de cableado revienta contra la clave ajena y, como el refresco de
    arranque usa una transacción POR PASO, lo que se pierde es el roster entero.
    Por eso el refresco siembra los catálogos antes de repartirlos — y por eso
    esta comprobación mira los tres rosters y el mapa por rol a la vez.
    """
    huerfanas: list[tuple[str, list[str]]] = []
    for rol, tools in ROLE_DEFAULT_TOOLS.items():
        faltan = sorted(set(tools) - set(_CATALOGO))
        if faltan:
            huerfanas.append((f"rol:{rol}", faltan))
    for slug, _rol, tools in _rosters():
        faltan = sorted(tools - set(_CATALOGO))
        if faltan:
            huerfanas.append((slug, faltan))
    assert not huerfanas, (
        f"se reparten slugs que el catálogo no tiene: {huerfanas}. El cableado "
        "fallará contra la FK `agent_tools.tool_id`"
    )
