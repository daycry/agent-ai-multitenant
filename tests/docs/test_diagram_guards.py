"""Guardas de los diagramas Mermaid de `docs/01-overview/03-diagrams[.es].md`.

Un diagrama es una afirmación sobre el sistema, y envejece igual que un badge de
descargas de un paquete que no existe: sin ruido, hasta que alguien lo cree. Este
fichero es la contrapartida — cada dibujo se compara contra la fuente de verdad
que decide su contenido:

  * las dos máquinas de estados, **arista por arista y en las dos direcciones**
    (`plan_state_machine._TRANSITIONS`, `task_state_machine._AI_TRANSITIONS`
    + `_HUMAN_OVERLAY`);
  * los nombres de servicio, contra `compose_generator.CORE_SERVICES` (+ los
    overlays opcionales), en las dos direcciones también: ni servicios
    inventados ni servicios silenciosamente omitidos;
  * los tres roles de PostgreSQL, contra el `.sh` y el `.sql` que los crean;
  * las banderas del sandbox, contra `workers.isolation`;
  * los ocho nodos del agent loop, contra los `add_node` del grafo real.

Y la guarda que impide que la traducción se quede a medias: los dos ficheros
tienen que dibujar **los mismos identificadores de nodo** en cada bloque. Las
etiquetas cambian de idioma; los ids, no. Un bloque añadido en inglés y no en
castellano rompe la suite en vez de irse separando en silencio.

## Por qué hay aserciones de «encontró algo»

`docs/03-guides/verificar-antes-de-implementar.md` §4: una guarda estática que
busca infractores pasa **vacíamente** el día que el descubrimiento deja de
encontrar nada. Todos los extractores de aquí afirman primero un suelo (bloques
Mermaid vistos, aristas parseadas, nodos declarados); si un regex se rompe o el
fichero se mueve, el test falla por ahí y no con un verde silencioso.

## Lo que este test NO puede afirmar, y cómo se comprobó a mano

Que un bloque **renderiza** pide un navegador; un test estático sólo puede
afirmar que la valla existe, no está vacía y abre con un tipo de diagrama
conocido — que es donde estaba el riesgo real (un bloque ```mermaid con prosa
dentro no dibuja nada y nadie se enteraba). La sintaxis se validó una vez con el
parser de verdad, fuera de la suite y fuera de CI para no meter Node ni una
descarga de red en `pytest`:

    npm install --no-save mermaid@11.4.1 jsdom
    node -e "..."   # JSDOM como DOM global + await mermaid.parse(bloque)

El 2026-08-21 pasaron los **25** bloques Mermaid del repositorio —los seis de
cada `03-diagrams[.es].md`, los dos README, los dos `docs/index[.es].md`,
`01-overview/02-architecture.md`, `context/architecture-overview.md` y cuatro
más de guías, runbooks y ADR—, 0 errores de sintaxis. Si se toca un diagrama, se
repite: el coste es un minuto y el fallo alternativo es un dibujo roto publicado
en el sitio.

Dos avisos para quien lo repita, porque los dos cuestan media hora:

1. JSDOM hay que volcarlo en globals **uno a uno** (`Element`, `SVGElement`,
   `getComputedStyle`…). Sin ellos los flowcharts fallan con `Element is not
   defined`, que NO es un error de sintaxis y se confunde con uno; los
   stateDiagram sí pasan, así que el falso negativo es selectivo.
2. `global.navigator = …` revienta en Node 23 (sólo tiene getter): va con
   `Object.defineProperty`.

## Un caso real que estas guardas ya han cerrado

`docs/01-overview/02-architecture.md` dibujaba `memorizer`,
`personal-assistant` y `webhook-dispatcher` como contenedores del plano de
control. No lo son: son módulos dentro de `api-server` y de los workers (ADR
0033), y quien leía ese diagrama buscaba contenedores que nunca arrancan. Había
una guarda para el mismo error en `docs/context/architecture-overview.md`
(`tests/unit/test_docs_governance.py::test_arch_overview_mermaid_has_no_phantom_services`)
que sólo miraba ese fichero. `test_no_mermaid_diagram_draws_a_phantom_service`
barre **todos** los bloques Mermaid de `docs/`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from api_server.chat.plan_state_machine import allowed_transitions as plan_allowed
from api_server.db.domain import PlanStatus, TaskStatus
from api_server.db.platform_settings import DEFAULT_MAX_REVIEW_RETRIES
from api_server.task_state_machine import allowed_transitions as task_allowed
from api_server.task_state_machine import is_terminal as task_is_terminal

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCS = _REPO_ROOT / "docs"
#: El canónico inglés vive en el nombre DESNUDO y el castellano en `.es.md`:
#: es la convención de `docs/03-guides/bilingual-docs.md`, que la vigila
#: `tests/docs/test_bilingual_docs.py`. Este par nació con `.en.md` el mismo
#: día y se realineó en el mismo commit.
_EN = _DOCS / "01-overview" / "03-diagrams.md"
_ES = _DOCS / "01-overview" / "03-diagrams.es.md"

#: Los dos ficheros del par bilingüe, como parámetro de test.
_PAIR = pytest.mark.parametrize("path", [_EN, _ES], ids=["en", "es"])

#: Cuántos bloques Mermaid tiene el documento. Es un número declarado a
#: propósito: si alguien añade un séptimo diagrama, tiene que decidir a
#: conciencia que ese diagrama hace falta (el encargo era «donde APORTEN») y
#: tocar este número y el índice del documento.
_EXPECTED_BLOCKS = 6

#: Tipos de diagrama Mermaid que la convención del repo usa
#: (`docs/context/conventions.md`). Un bloque que abre con otra cosa —o vacío,
#: o con prosa— es exactamente lo que no renderiza.
_MERMAID_TYPES = (
    "flowchart",
    "graph",
    "statediagram-v2",
    "statediagram",
    "sequencediagram",
    "erdiagram",
    "classdiagram",
    "gantt",
    "journey",
    "pie",
)

# ---------------------------------------------------------------------------
# Extractores
# ---------------------------------------------------------------------------
_FENCE_RE = re.compile(r"^```mermaid[ \t]*\n(.*?)^```[ \t]*$", re.S | re.M)

#: Un id de nodo es una palabra pegada a un `[` o un `{` — la posición en la que
#: Mermaid declara un nodo (`id["label"]`, `id[("label")]`, `id{"label"}`) y
#: también la de `subgraph id["label"]`. Anclar en la posición y no en el
#: vocabulario es lo que hace el extractor independiente del idioma: el texto de
#: la etiqueta nunca cae dentro de la captura.
_FLOW_NODE_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*[\[{]")

#: `id: etiqueta` al principio de línea en un stateDiagram.
_STATE_DECL_RE = re.compile(r"^[ \t]*([a-z_][a-z0-9_]*)[ \t]*:", re.M)

#: Una transición de stateDiagram, con su ETIQUETA capturada. La etiqueta va tras
#: el `:` y llega a fin de línea, así que los acentos del castellano caen dentro
#: del último grupo y nunca contaminan los identificadores.
#:
#: La etiqueta hace falta porque es donde vive la marca «esta arista sólo es legal
#: con un asignado humano». No puede ir en el estilo de la flecha: el diagrama de
#: la Tarea llegó a declarar en prosa «las discontinuas son el overlay humano» y a
#: dibujar CERO discontinuas, y el arreglo obvio —pasar esas cuatro a `-.->`— no
#: es válido. Comprobado con el parser real de Mermaid 11.4.1: la gramática de
#: `stateDiagram-v2` sólo acepta `-->` y un `-.->` (sintaxis de flowchart) tira el
#: bloque entero con `Parse error … got 'INVALID'`. Un dibujo que no renderiza es
#: peor que uno sin leyenda, así que la marca va en la etiqueta y
#: `test_no_state_diagram_uses_a_dotted_arrow` impide que alguien lo reintente.
_STATE_EDGE_RE = re.compile(
    r"^[ \t]*(\[\*\]|[a-z_][a-z0-9_]*)[ \t]*(-->|-\.->)[ \t]*"
    r"(\[\*\]|[a-z_][a-z0-9_]*)[ \t]*(?::[ \t]*(.*))?$",
    re.M,
)

#: Cómo se marca en la etiqueta una arista del overlay humano, por idioma. Es
#: texto visible a propósito: el lector la ve en el dibujo y la guarda la
#: comprueba contra `_HUMAN_OVERLAY`; una marca invisible no informaría a nadie.
_HUMAN_ONLY_MARK: dict[str, str] = {"en": "(human only)", "es": "(solo humano)"}

#: `id["etiqueta"]` / `id[("etiqueta")]` con la etiqueta entrecomillada.
_LABELLED_NODE_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\[\(?\"([^\"]+)\"\)?\]")

#: Cabecera de subgrafo. Se BORRA antes de extraer etiquetas: un subgrafo es una
#: caja de agrupación, no un nodo, y su título no nombra ningún servicio. (Un
#: lookbehind negativo no sirve: el motor reintenta un carácter más adelante y
#: `subgraph edge[` acaba casando como el nodo `dge`.)
_SUBGRAPH_HEADER_RE = re.compile(r"^[ \t]*subgraph[ \t]+[A-Za-z_][A-Za-z0-9_]*\[.*$", re.M)


def _blocks(path: Path) -> list[str]:
    """Los cuerpos de los bloques ```mermaid de `path`, en orden."""
    text = path.read_text(encoding="utf-8")
    blocks = [m.group(1) for m in _FENCE_RE.finditer(text)]
    assert len(blocks) == _EXPECTED_BLOCKS, (
        f"{path.relative_to(_REPO_ROOT)}: se esperaban {_EXPECTED_BLOCKS} bloques "
        f"Mermaid y hay {len(blocks)}. O el documento cambió de contenido o el "
        f"regex de las vallas dejó de encontrarlos; un verde aquí no significaría nada"
    )
    return blocks


def _block_with(path: Path, token: str) -> str:
    """El ÚNICO bloque de `path` que contiene `token`."""
    hits = [b for b in _blocks(path) if token in b]
    assert len(hits) == 1, (
        f"{path.relative_to(_REPO_ROOT)}: {len(hits)} bloques contienen "
        f"{token!r} (se esperaba exactamente 1): el marcador con el que este "
        f"test localiza el diagrama dejó de ser único"
    )
    return hits[0]


def _node_ids(block: str) -> frozenset[str]:
    """Los identificadores de nodo del bloque, sea flowchart o stateDiagram."""
    ids = set(_FLOW_NODE_RE.findall(block))
    ids |= set(_STATE_DECL_RE.findall(block))
    for src, _style, dst, _label in _STATE_EDGE_RE.findall(block):
        for node in (src, dst):
            if node != "[*]":
                ids.add(node)
    return frozenset(ids)


def _labelled_state_edges(block: str) -> frozenset[tuple[str, str, str, str]]:
    """`(origen, destino, estilo, etiqueta)` del bloque, sin las pseudo-`[*]`."""
    edges = {
        (src, dst, style, label)
        for src, style, dst, label in _STATE_EDGE_RE.findall(block)
        if src != "[*]" and dst != "[*]"
    }
    assert len(edges) >= 10, (
        f"el parser de transiciones sólo vio {len(edges)} aristas: se rompió, "
        f"y comparar contra la máquina de estados con eso sería un verde vacío"
    )
    return frozenset(edges)


def _state_edges(block: str) -> frozenset[tuple[str, str]]:
    """Las transiciones del bloque, sin estilo ni etiqueta."""
    return frozenset((src, dst) for src, dst, _style, _label in _labelled_state_edges(block))


def _labels(block: str) -> dict[str, str]:
    """`{id: etiqueta}` de los nodos con etiqueta entrecomillada."""
    labels = dict(_LABELLED_NODE_RE.findall(_SUBGRAPH_HEADER_RE.sub("", block)))
    assert len(labels) >= 5, (
        f"el parser de etiquetas sólo vio {len(labels)} nodos etiquetados: se rompió"
    )
    return labels


def _generated_services() -> frozenset[str]:
    """Todo servicio que el instalador puede generar: núcleo + overlays opcionales."""
    src = _REPO_ROOT / "apps" / "installer" / "backend" / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from installer_backend.compose_generator import (
        CORE_SERVICES,
        MONITORING_SERVICES,
        OLLAMA_BOOTSTRAP_SERVICE,
        OLLAMA_SERVICE,
        VOICE_SERVICES,
    )

    services = (
        set(CORE_SERVICES)
        | set(MONITORING_SERVICES)
        | set(VOICE_SERVICES)
        | {OLLAMA_SERVICE, OLLAMA_BOOTSTRAP_SERVICE}
    )
    assert len(services) >= 25, f"la lista de servicios generables quedó corta: {services}"
    return frozenset(services)


def _core_services() -> frozenset[str]:
    src = _REPO_ROOT / "apps" / "installer" / "backend" / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from installer_backend.compose_generator import CORE_SERVICES

    assert len(CORE_SERVICES) >= 15, f"CORE_SERVICES quedó sospechosamente corto: {CORE_SERVICES}"
    return frozenset(CORE_SERVICES)


# ---------------------------------------------------------------------------
# El par bilingüe
# ---------------------------------------------------------------------------
def test_both_files_of_the_pair_exist() -> None:
    for path in (_EN, _ES):
        assert path.is_file(), f"falta la mitad {path.name} del par bilingüe"


@_PAIR
def test_frontmatter_declares_the_language_and_its_twin(path: Path) -> None:
    """Cada mitad declara su `docs_language` y apunta a la otra.

    El campo `docs_language` ya existía en el corpus (la plantilla de ADR lo
    honra); lo que añade el par es que la relación entre las dos mitades sea
    explícita y comprobable, en vez de vivir en el nombre del fichero.
    """
    text = path.read_text(encoding="utf-8")
    expected_lang = "en" if path is _EN else "es"
    twin = _ES if path is _EN else _EN

    assert re.search(rf"^docs_language:\s*{expected_lang}\s*$", text, re.M), (
        f"{path.name} no declara `docs_language: {expected_lang}` en su frontmatter"
    )
    assert re.search(rf"^translation_pair:\s*\./{re.escape(twin.name)}\s*$", text, re.M), (
        f"{path.name} no apunta a su gemelo {twin.name} en `translation_pair`"
    )
    assert f"./{twin.name}" in text, (
        f"{path.name} no enlaza a {twin.name} en el cuerpo: el lector que abre "
        f"una mitad tiene que poder saltar a la otra desde la cabecera"
    )


@_PAIR
def test_every_mermaid_block_renders_something(path: Path) -> None:
    """Ningún bloque está vacío ni abre con prosa.

    Que el dibujo se lea es del humano; que el bloque sea un diagrama y no un
    párrafo dentro de una valla ```mermaid es comprobable, y es donde estaba el
    riesgo real.
    """
    for index, block in enumerate(_blocks(path)):
        body = block.strip()
        assert body, f"{path.name}: el bloque Mermaid #{index} está vacío"
        first = body.splitlines()[0].strip().lower()
        assert first.startswith(_MERMAID_TYPES), (
            f"{path.name}: el bloque Mermaid #{index} abre con {first!r}, que no "
            f"es un tipo de diagrama conocido"
        )


def test_the_two_languages_draw_the_same_node_ids() -> None:
    """El detector de traducción a medias.

    Las etiquetas se traducen; los identificadores de nodo son la estructura y
    NO se traducen. Comparados bloque a bloque, un diagrama añadido, borrado o
    reestructurado en un solo idioma rompe aquí.
    """
    en_blocks = _blocks(_EN)
    es_blocks = _blocks(_ES)
    for index, (en_block, es_block) in enumerate(zip(en_blocks, es_blocks, strict=True)):
        en_ids = _node_ids(en_block)
        es_ids = _node_ids(es_block)
        assert len(en_ids) >= 4, (
            f"el extractor de ids sólo vio {len(en_ids)} nodos en el bloque #{index}: se rompió"
        )
        assert en_ids == es_ids, (
            f"el bloque #{index} no dibuja los mismos nodos en los dos idiomas.\n"
            f"  sólo en inglés:    {sorted(en_ids - es_ids)}\n"
            f"  sólo en castellano: {sorted(es_ids - en_ids)}"
        )


# ---------------------------------------------------------------------------
# Diagrama 2 — ciclo de vida del Plan
# ---------------------------------------------------------------------------
@_PAIR
def test_plan_diagram_matches_the_state_machine(path: Path) -> None:
    """El diagrama del Plan ES la tabla de transiciones, en las dos direcciones."""
    drawn = _state_edges(_block_with(path, "pending_second_approval"))
    legal = {
        (status.value, target) for status in PlanStatus for target in plan_allowed(status.value)
    }
    assert legal, "la tabla de transiciones del plan vino vacía: el import falló"

    invented = sorted(drawn - legal)
    missing = sorted(legal - drawn)
    assert not invented, (
        f"{path.name}: el diagrama del Plan dibuja transiciones que la máquina de "
        f"estados NO permite: {invented}"
    )
    assert not missing, (
        f"{path.name}: el diagrama del Plan omite transiciones legales: {missing}. "
        f"Un diagrama de una máquina de estados que deja aristas fuera sin decirlo "
        f"es peor que no tenerlo"
    )


@_PAIR
def test_plan_diagram_draws_every_state(path: Path) -> None:
    drawn = _node_ids(_block_with(path, "pending_second_approval"))
    missing = sorted({status.value for status in PlanStatus} - drawn)
    assert not missing, f"{path.name}: el diagrama del Plan no dibuja los estados {missing}"


# ---------------------------------------------------------------------------
# Diagrama 3 — ciclo de vida de la Tarea
# ---------------------------------------------------------------------------
def _task_legal_edges() -> set[tuple[str, str]]:
    """Todas las transiciones legales de tarea: tabla de IA + overlay humano."""
    edges: set[tuple[str, str]] = set()
    for status in TaskStatus:
        for agent_type in (None, "human"):
            for target in task_allowed(status.value, assignee_agent_type=agent_type):
                edges.add((status.value, target))
    assert edges, "la tabla de transiciones de tarea vino vacía: el import falló"
    return edges


@_PAIR
def test_task_diagram_matches_the_state_machine(path: Path) -> None:
    """Igual que el del Plan, salvo la omisión DECLARADA de las aristas a `cancelled`.

    Se dejan fuera porque son una por estado y dicen una sola frase, que el
    documento escribe en prosa y el test de abajo comprueba. Cualquier OTRA
    arista que falte, o cualquiera dibujada que no sea legal, rompe aquí.
    """
    drawn = _state_edges(_block_with(path, "awaiting_human_approval"))
    legal = _task_legal_edges()
    cancel_edges = {edge for edge in legal if edge[1] == TaskStatus.CANCELLED.value}
    assert len(cancel_edges) >= 6, (
        f"sólo {len(cancel_edges)} aristas a `cancelled`: la omisión que el "
        f"documento declara ya no describe la tabla"
    )

    invented = sorted(drawn - legal)
    missing = sorted((legal - cancel_edges) - drawn)
    assert not invented, (
        f"{path.name}: el diagrama de la Tarea dibuja transiciones ilegales: {invented}"
    )
    assert not missing, (
        f"{path.name}: el diagrama de la Tarea omite transiciones legales que NO "
        f"son las de `cancelled`: {missing}"
    )
    assert not (drawn & cancel_edges), (
        f"{path.name}: el diagrama dibuja aristas a `cancelled` que el documento "
        f"declara omitidas: {sorted(drawn & cancel_edges)}"
    )


def _human_only_task_edges() -> set[tuple[str, str]]:
    """Aristas legales SÓLO con un asignado humano: overlay menos tabla de IA.

    `in_progress -> in_review` está en las dos, así que NO sale aquí: el humano
    reutiliza el camino de la IA y dibujarlo discontinuo diría que una tarea de
    IA no puede entrar a review, que es falso.
    """
    human_only: set[tuple[str, str]] = set()
    for status in TaskStatus:
        ai = task_allowed(status.value)
        for target in task_allowed(status.value, assignee_agent_type="human") - ai:
            human_only.add((status.value, target))
    assert human_only, "el overlay humano vino vacío: el import o la tabla cambiaron"
    return human_only


@_PAIR
def test_no_state_diagram_uses_a_dotted_arrow(path: Path) -> None:
    """Un `-.->` en un stateDiagram no dibuja nada: el bloque entero falla.

    La gramática de `stateDiagram-v2` sólo conoce `-->`; la flecha punteada es
    sintaxis de flowchart. Comprobado con el parser real (Mermaid 11.4.1): el
    bloque muere con `Parse error … Expecting … '-->' … got 'INVALID'`, y en la
    página no queda un diagrama peor, queda un hueco. Es una trampa que invita a
    caer en ella —el documento pedía «discontinuas para el overlay humano» y
    ponerlas parece el arreglo natural— así que la valla va aquí.
    """
    offenders = [
        block[:60].strip()
        for block in _blocks(path)
        if block.lstrip().lower().startswith("statediagram") and "-.->" in block
    ]
    assert not offenders, (
        f"{path.name}: {offenders} usa `-.->` en un stateDiagram. Mermaid no lo "
        f"parsea y el bloque no renderiza. Si hace falta distinguir un tipo de "
        f"arista, va en la ETIQUETA (ver _HUMAN_ONLY_MARK), no en el estilo"
    )


@_PAIR
def test_the_human_overlay_is_marked_in_the_label_and_nothing_else(path: Path) -> None:
    """La leyenda «las etiquetadas `(human only)` son el overlay humano», comprobada.

    Nació de un defecto real: el documento declaraba en prosa que las aristas
    discontinuas eran el overlay humano y dibujaba **cero** discontinuas, con las
    cuatro del overlay pintadas como transiciones normales y sin marca. Un lector
    veía `ready --> assigned_to_human` como un movimiento cualquiera cuando en una
    tarea asignada a IA levanta `TaskTransitionError`. La guarda de al lado no
    podía cazarlo: compara contra la UNIÓN de las dos tablas, donde las cuatro son
    legales.

    Se exige en las dos direcciones. Una arista marcada que sí es legal para la IA
    miente igual que una sin marcar que no lo es.
    """
    mark = _HUMAN_ONLY_MARK["en" if path is _EN else "es"]
    labelled = _labelled_state_edges(_block_with(path, "awaiting_human_approval"))
    marked = {(src, dst) for src, dst, _style, label in labelled if mark in label}

    human_only = _human_only_task_edges()
    # Las aristas a `cancelled` se omiten del dibujo por decisión declarada, así
    # que la del overlay (`assigned_to_human -> cancelled`) tampoco se exige.
    expected = {edge for edge in human_only if edge[1] != TaskStatus.CANCELLED.value}
    assert len(expected) >= 4, (
        f"sólo {len(expected)} aristas exclusivas del overlay humano: la tabla "
        f"cambió y la leyenda del diagrama hay que rehacerla"
    )

    assert marked == expected, (
        f"{path.name}: las aristas marcadas «{mark}» tienen que ser exactamente "
        f"las del overlay humano. Sobran {sorted(marked - expected)} y faltan "
        f"{sorted(expected - marked)}. La leyenda del documento dice que esa marca "
        f"significa «legal sólo con asignado humano»: o se marca así, o miente"
    )
    # Y la leyenda: una marca que el dibujo usa y el texto no explica es ruido.
    # Los bloques se borran con `_FENCE_RE` y NO partiendo por párrafos: un
    # bloque Mermaid lleva líneas en blanco dentro, así que trocearlo por
    # párrafos deja pedazos del dibujo contando como prosa — y con eso la
    # aserción pasaba aunque se borrase la leyenda (comprobado rompiéndola).
    prose = _FENCE_RE.sub("", path.read_text(encoding="utf-8"))
    assert mark in prose, (
        f"{path.name}: el diagrama marca aristas con «{mark}» y ningún párrafo lo "
        f"explica. La marca sin leyenda es un paréntesis que el lector no descifra"
    )


#: Cómo se escribe cada cantidad en la prosa de los dos idiomas. El documento
#: dice el número con letra («All seven of those edges…»), y decía **ocho** con
#: siete estados no terminales de verdad — un contador escrito a mano que nadie
#: comprobaba, en el documento cuya tesis es que la guarda comprueba la frase.
_NUMBER_WORDS: dict[int, tuple[str, str]] = {
    6: ("six", "seis"),
    7: ("seven", "siete"),
    8: ("eight", "ocho"),
    9: ("nine", "nueve"),
    10: ("ten", "diez"),
}


@_PAIR
def test_the_prose_says_how_many_cancelled_edges_it_omits(path: Path) -> None:
    """El número escrito con letra en la prosa es el número real de aristas.

    La guarda de al lado sólo exigía `>= 6`, así que la frase pudo decir «ocho»
    durante todo el tiempo que hubo siete sin que nada ladrase. Aquí el número
    tiene que coincidir exactamente, y el mensaje dice qué palabra poner.
    """
    real = len({edge for edge in _task_legal_edges() if edge[1] == TaskStatus.CANCELLED.value})
    non_terminal = {
        status.value
        for status in TaskStatus
        if status is not TaskStatus.CANCELLED and not task_is_terminal(status.value)
    }
    assert real == len(non_terminal), (
        f"{real} aristas a `cancelled` para {len(non_terminal)} estados no "
        f"terminales: la frase «una por estado» dejó de ser cierta"
    )
    assert real in _NUMBER_WORDS, (
        f"{real} aristas a `cancelled` y _NUMBER_WORDS no sabe escribir ese "
        f"número: añádelo antes de creerte el verde de este test"
    )

    english, spanish = _NUMBER_WORDS[real]
    expected = english if path is _EN else spanish
    index = 0 if path is _EN else 1
    words = {value[index]: count for count, value in _NUMBER_WORDS.items()}

    # Se busca SÓLO en el párrafo que declara la omisión. El documento usa otros
    # números con letra en otros sitios («los seis servicios de monitorización»),
    # y barrer el fichero entero daría un rojo por una frase que no habla de esto.
    paragraphs = [
        para
        for para in re.split(r"\n[ \t]*\n", path.read_text(encoding="utf-8"))
        if "`cancelled`" in para and any(re.search(rf"\b{word}\b", para) for word in words)
    ]
    assert len(paragraphs) == 1, (
        f"{path.name}: {len(paragraphs)} párrafos declaran cuántas aristas a "
        f"`cancelled` se omiten (se esperaba 1). El marcador con el que este test "
        f"localiza la frase dejó de ser único; un verde aquí no significaría nada"
    )
    claim = paragraphs[0]

    assert re.search(rf"\b{expected}\b", claim), (
        f"{path.name}: hay {real} aristas a `cancelled` y la frase de la omisión "
        f"no dice «{expected}». Escribe el número que es, en los DOS idiomas"
    )
    wrong = sorted(
        word for word, count in words.items() if count != real and re.search(rf"\b{word}\b", claim)
    )
    assert not wrong, (
        f"{path.name}: la frase de la omisión dice {wrong} donde el número real "
        f"es {real} («{expected}»). Un contador escrito con letra envejece igual "
        f"que uno con cifras"
    )


def test_every_non_terminal_task_state_can_be_cancelled() -> None:
    """La frase que justifica la omisión, comprobada.

    El documento dice «todo estado no terminal puede pasar además a
    `cancelled`». Si eso deja de ser cierto —una excepción nueva, un estado
    terminal nuevo— la prosa se vuelve mentira y este test lo dice antes de que
    lo lea nadie.
    """
    offenders = []
    for status in TaskStatus:
        if status is TaskStatus.CANCELLED or task_is_terminal(status.value):
            continue
        reachable = task_allowed(status.value) | task_allowed(
            status.value, assignee_agent_type="human"
        )
        if TaskStatus.CANCELLED.value not in reachable:
            offenders.append(status.value)
    assert not offenders, (
        f"estos estados no terminales YA NO pueden ir a `cancelled`: {offenders}. "
        f"La omisión declarada en 03-diagrams.*.md deja de estar justificada"
    )


@_PAIR
def test_task_diagram_marks_the_terminal_states(path: Path) -> None:
    """`done` y `cancelled` son los terminales; el diagrama dibuja `done` y sólo `done`."""
    terminal = {status.value for status in TaskStatus if task_is_terminal(status.value)}
    assert terminal == {TaskStatus.DONE.value, TaskStatus.CANCELLED.value}, (
        f"los estados terminales de tarea cambiaron ({sorted(terminal)}): el "
        f"diagrama y su prosa hay que rehacerlos"
    )
    drawn = _node_ids(_block_with(path, "awaiting_human_approval"))
    assert TaskStatus.DONE.value in drawn
    assert TaskStatus.CANCELLED.value not in drawn, (
        f"{path.name}: el diagrama declara omitir `cancelled` y lo dibuja"
    )


# ---------------------------------------------------------------------------
# Diagrama 1 — topología
# ---------------------------------------------------------------------------
#: Nodos del diagrama de topología que NO son servicios del compose, con el
#: motivo. Declararlos aquí es lo que permite que el test exija que TODO lo
#: demás sea un servicio real.
_TOPOLOGY_NON_SERVICES: dict[str, str] = {
    "client": "el navegador / cliente de API, fuera del stack",
    "agent_runtime": "contenedor efímero que lanza el worker, ningún compose lo declara",
    "test_runtime": "idem",
    "review_runtime": "idem",
}


@_PAIR
def test_topology_diagram_only_draws_real_services(path: Path) -> None:
    """Toda caja del diagrama de topología es un servicio que el instalador genera."""
    block = _block_with(path, "docling_serve")
    generated = _generated_services()
    labels = _labels(block)

    invented = {}
    for node_id, label in labels.items():
        if node_id in _TOPOLOGY_NON_SERVICES:
            continue
        service = label.split("<br/>")[0].strip()
        if service not in generated:
            invented[node_id] = service
    assert not invented, (
        f"{path.name}: el diagrama de topología dibuja como servicio algo que el "
        f"instalador NO genera: {invented}"
    )


@_PAIR
def test_topology_diagram_omits_no_core_service(path: Path) -> None:
    """La otra dirección: ningún servicio del NÚCLEO se cae del dibujo en silencio.

    Los overlays opcionales sí se omiten, y el documento lo dice; el núcleo no.
    Un servicio nuevo en `CORE_SERVICES` que nadie dibuja deja el diagrama
    describiendo un stack que ya no existe.
    """
    block = _block_with(path, "docling_serve")
    drawn = {label.split("<br/>")[0].strip() for label in _labels(block).values()}
    missing = sorted(_core_services() - drawn)
    assert not missing, (
        f"{path.name}: el diagrama de topología no dibuja estos servicios del núcleo: {missing}"
    )


#: Módulos internos que, dibujados como nodo de un diagrama, son una mentira:
#: viven dentro de `api-server` o de los workers (ADR 0033), no en un
#: contenedor propio. Espejo de `_PHANTOM_SERVICES` de
#: `tests/unit/test_docs_governance.py`, aplicado a TODO `docs/`.
_PHANTOM_SERVICES = frozenset({"personal-assistant", "webhook-dispatcher", "memorizer", "web-app"})


def test_no_mermaid_diagram_draws_a_phantom_service() -> None:
    """Ningún bloque Mermaid de `docs/` dibuja un módulo interno como contenedor."""
    generated = _generated_services()
    assert not (_PHANTOM_SERVICES & generated), (
        "el instalador empezó a generar uno de los servicios que este test tiene "
        "por fantasma: revisar _PHANTOM_SERVICES"
    )

    node_re = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\s*[\[{(]{1,2}\"?([^\"\]\)}<]+)")
    scanned = 0
    offenders: list[str] = []
    for md in sorted(p for p in _DOCS.rglob("*.md") if "node_modules" not in p.parts):
        text = md.read_text(encoding="utf-8", errors="replace")
        for block in _FENCE_RE.findall(text):
            scanned += 1
            for label in node_re.findall(block):
                name = label.strip()
                if name in _PHANTOM_SERVICES:
                    offenders.append(f"{md.relative_to(_REPO_ROOT)} -> {name}")

    assert scanned >= 9, (
        f"la guarda sólo barrió {scanned} bloques Mermaid en docs/ (esperaba >= 9): "
        f"el descubrimiento se rompió y un verde aquí no significaría nada"
    )
    assert not offenders, (
        "diagramas que dibujan como contenedor un módulo interno de api-server o "
        "de los workers (ADR 0033):\n  " + "\n  ".join(sorted(set(offenders)))
    )


# ---------------------------------------------------------------------------
# Diagrama 4 — las dos cosas que se llaman «review»
# ---------------------------------------------------------------------------
_AGENT_GRAPH = (
    _REPO_ROOT / "docker" / "agent-runtimes" / "agent-runtime" / "agent_runtime" / "graph.py"
)


def _agent_loop_nodes() -> frozenset[str]:
    """Los nodos que el grafo real registra con `add_node`.

    Se lee el fuente en vez de importarlo: el módulo vive en la imagen del
    agent-runtime y arrastra LangGraph, que no está en el entorno de la suite.
    """
    text = _AGENT_GRAPH.read_text(encoding="utf-8")
    nodes = frozenset(re.findall(r'graph\.add_node\(\s*"([a-z_]+)"', text))
    assert len(nodes) >= 6, (
        f"sólo se encontraron {len(nodes)} `add_node` en {_AGENT_GRAPH.name}: el "
        f"regex se rompió o el grafo se reescribió"
    )
    return nodes


@_PAIR
def test_review_diagram_draws_the_real_agent_loop(path: Path) -> None:
    """La caja del implementador dibuja exactamente los nodos del grafo."""
    block = _block_with(path, "self_review")
    drawn = _node_ids(block)
    nodes = _agent_loop_nodes()
    missing = sorted(nodes - drawn)
    assert not missing, (
        f"{path.name}: el diagrama de review no dibuja estos nodos del agent loop: {missing}"
    )


@_PAIR
def test_review_diagram_separates_self_review_from_the_reviewer(path: Path) -> None:
    """La trampa del ADR 0159, hecha mecánica.

    `self_review` es un NODO dentro de la ejecución del implementador; el
    reviewer es una EJECUCIÓN aparte. Si el diagrama los mete en la misma caja,
    deja de distinguir lo único que vino a distinguir.
    """
    block = _block_with(path, "self_review")

    def _subgraph_body(name: str) -> str:
        match = re.search(rf"subgraph {name}\[.*?\n(.*?)^    end", block, re.S | re.M)
        assert match, f"{path.name}: el diagrama de review ya no tiene el subgrafo {name}"
        return match.group(1)

    implementer = _subgraph_body("exec_impl")
    reviewer = _subgraph_body("exec_review")

    assert "self_review" in implementer, (
        f"{path.name}: `self_review` tiene que estar DENTRO de la ejecución del "
        f"implementador; es un nodo del grafo, no una ejecución"
    )
    assert "self_review" not in reviewer, (
        f"{path.name}: `self_review` aparece en la caja del reviewer: es justo la "
        f"confusión que el ADR 0159 avisa que cuesta una regresión de seguridad"
    )
    assert "reviewer" in reviewer and "reviewer" not in implementer, (
        f"{path.name}: el reviewer tiene que ser una ejecución APARTE (ADR 0087)"
    )


@_PAIR
def test_review_diagram_quotes_the_real_retry_limit(path: Path) -> None:
    """El número que el diagrama pone como techo del bucle es el default real."""
    block = _block_with(path, "self_review")
    assert DEFAULT_MAX_REVIEW_RETRIES == 3, (
        f"el default de plataforma cambió a {DEFAULT_MAX_REVIEW_RETRIES}: el "
        f"diagrama y su prosa tienen que decirlo"
    )
    assert f"default {DEFAULT_MAX_REVIEW_RETRIES}" in block, (
        f"{path.name}: el diagrama no cita el default real de "
        f"`max_review_retries` ({DEFAULT_MAX_REVIEW_RETRIES})"
    )


# ---------------------------------------------------------------------------
# Diagrama 5 — aislamiento multi-tenant
# ---------------------------------------------------------------------------
_ROLES_SH = _REPO_ROOT / "docker" / "postgres" / "init" / "02-roles.sh"
_SERVICE_ROLE_SQL = _REPO_ROOT / "docker" / "postgres" / "init" / "04-service-role.sql"

#: `{id del nodo: (subcadena obligatoria, subcadena prohibida)}` en su etiqueta.
#: Tokens iguales en los dos idiomas a propósito: los atributos de un rol de
#: PostgreSQL no se traducen.
_ROLE_LABEL_RULES: dict[str, tuple[str, str | None]] = {
    "app_user": ("NOBYPASSRLS", None),
    "service_user": ("BYPASSRLS", "NOBYPASSRLS"),
    "migrations_user": ("BYPASSRLS", "NOBYPASSRLS"),
}


@_PAIR
def test_roles_diagram_matches_the_sql_that_creates_them(path: Path) -> None:
    """Los tres roles y sus atributos salen del `.sh` y del `.sql`, no de la memoria."""
    roles_sh = _ROLES_SH.read_text(encoding="utf-8")
    service_sql = _SERVICE_ROLE_SQL.read_text(encoding="utf-8")

    assert re.search(r"CREATE ROLE migrations_user WITH LOGIN BYPASSRLS", roles_sh)
    assert re.search(r"CREATE ROLE app_user WITH LOGIN NOBYPASSRLS", roles_sh)
    assert "CREATE ROLE service_user" in service_sql
    assert "BYPASSRLS" in service_sql
    assert "REVOKE CREATE ON SCHEMA public FROM service_user" in service_sql, (
        "`service_user` dejó de tener el CREATE revocado: el diagrama afirma "
        "«sin DDL, sin CREATE» y dejaría de ser cierto"
    )

    labels = _labels(_block_with(path, "NOBYPASSRLS"))
    for node_id, (required, forbidden) in _ROLE_LABEL_RULES.items():
        assert node_id in labels, f"{path.name}: el diagrama de roles no dibuja {node_id}"
        label = labels[node_id]
        assert required in label, (
            f"{path.name}: la etiqueta de {node_id} no dice {required!r}: {label!r}"
        )
        if forbidden is not None:
            assert forbidden not in label, (
                f"{path.name}: la etiqueta de {node_id} dice {forbidden!r}, y el "
                f"SQL dice lo contrario: {label!r}"
            )


def test_service_user_is_still_created_but_not_wired() -> None:
    """La arista discontinua del diagrama 5, comprobada.

    El documento dice que `service_user` está creado y **no cableado**: ningún
    compose del repositorio ni el generador del instalador conecta un servicio
    con él. Es la forma exacta del patrón «mecanismo entregado, cero llamantes»
    (`docs/03-guides/verificar-antes-de-implementar.md` §5), y por eso el
    diagrama lo dibuja discontinuo. El día que se cablee, este test se pone
    rojo y hay que pasar la arista a continua y borrar el párrafo.
    """
    assert _SERVICE_ROLE_SQL.is_file(), "desapareció 04-service-role.sql"

    wired: list[str] = []
    generator = (
        _REPO_ROOT
        / "apps"
        / "installer"
        / "backend"
        / "src"
        / "installer_backend"
        / "compose_generator.py"
    )
    candidates = [*sorted((_REPO_ROOT / "docker").glob("docker-compose*.yml")), generator]
    assert len(candidates) >= 5, f"la guarda dejó de encontrar composes: {candidates}"

    for candidate in candidates:
        for line in candidate.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("--"):
                continue
            if "service_user:" in stripped or "service_user@" in stripped:
                wired.append(f"{candidate.relative_to(_REPO_ROOT)}: {stripped[:90]}")

    assert not wired, (
        "algún servicio ya conecta como `service_user`:\n  "
        + "\n  ".join(wired)
        + "\nLa arista discontinua de 03-diagrams.*.md pasa a continua y el "
        "párrafo que dice «no está cableada» hay que retirarlo"
    )


# ---------------------------------------------------------------------------
# Diagrama 6 — aislamiento por contenedor
# ---------------------------------------------------------------------------
_ISOLATION = _REPO_ROOT / "apps" / "workers" / "src" / "workers" / "isolation.py"

#: Cada afirmación del diagrama del sandbox, con lo que la sostiene en el
#: código. Si el módulo deja de fijar una, el diagrama deja de ser cierto.
_SANDBOX_CLAIMS: tuple[tuple[str, str], ...] = (
    ("cap_drop ALL", '"cap_drop": ["ALL"]'),
    ("no-new-privileges", '"no-new-privileges:true"'),
    ("read-only root", '"read_only": True'),
    ("seccomp", "seccomp="),
    ("uid:gid 1000:1000", 'AGENT_UID_GID = "1000:1000"'),
    ("pids limit", '"pids_limit"'),
    ("mem limit", '"mem_limit"'),
    ("tripwire", "def assert_no_docker_socket"),
    ("DockerSocketLeakError", "class DockerSocketLeakError"),
)


def test_sandbox_claims_are_backed_by_the_isolation_module() -> None:
    text = _ISOLATION.read_text(encoding="utf-8")
    missing = [name for name, needle in _SANDBOX_CLAIMS if needle not in text]
    assert not missing, (
        f"el diagrama del sandbox afirma cosas que `workers/isolation.py` ya no fija: {missing}"
    )


@_PAIR
def test_sandbox_diagram_names_the_flags_and_the_networks(path: Path) -> None:
    """Los tokens que no se traducen tienen que estar en las dos versiones."""
    block = _block_with(path, "assert_no_docker_socket")
    for token in (
        "cap_drop ALL",
        "no-new-privileges",
        "seccomp",
        "1000:1000",
        "DockerSocketLeakError",
        "agentic-agents",
        "agentic-docker",
        "agentic-net",
        "/var/run/docker.sock",
    ):
        assert token in block, f"{path.name}: el diagrama del sandbox no menciona {token!r}"


def test_the_agent_network_is_the_one_the_diagram_draws() -> None:
    """`agentic-agents` no es una etiqueta bonita: es el default del worker."""
    config = (_REPO_ROOT / "apps" / "workers" / "src" / "workers" / "config.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r"agent_network:\s*str\s*=\s*Field\(\s*\n\s*default=\"([^\"]+)\"", config)
    assert match, "no se pudo leer el default de `agent_network`: el regex se rompió"
    assert match.group(1) == "agentic-agents", (
        f"la red del sandbox pasó a {match.group(1)!r}: el diagrama 6 hay que "
        f"corregirlo en los dos idiomas"
    )
