"""La precedencia normativa, comprobable en vez de recordada (`task_gov_01`).

Plan [`gov-01`](../../docs/roadmap/gov-01-precedencia-prompts-y-rigor.md), fase 0.
El problema que resuelve pasó tres veces durante agosto de 2026: un plan pedía
algo que un ADR posterior había **rechazado**, y se resolvió a ojo cada vez. La
relación «este ADR invalida esta casilla» existía —el 0150 retiró dos mitades de
`task_prod07_09`, el 0141 descartó las premisas de dos tareas de prod-08, el 0133
dejó sin objeto `task_prod09_12`, el 0151 descartó `task_prod13_15`— pero vivía
en **prosa**, así que solo la encontraba quien ya sabía que estaba.

Este fichero cubre las dos mitades de la regla que `CLAUDE.md` fija ahora:

* **el orden**: que la cadena de precedencia esté escrita, completa y en orden, y
  que lleve consigo la obligación fina (un ADR que contradiga `CLAUDE.md` lo
  actualiza en el MISMO commit);
* **el campo**: que todo `rejects:` del frontmatter de un ADR apunte a algo que
  existe, que la casilla nombrada esté **cerrada** y que el documento rechazado
  **cite de vuelta** al ADR que lo rechaza. Sin la referencia de vuelta, el campo
  sería una anotación de un solo lado y el implementador que abre la casilla
  seguiría sin enterarse — que es exactamente el fallo original.

Dos decisiones de diseño que conviene no deshacer sin leer:

1. **El parseo del `rejects:` no usa PyYAML.** El frontmatter del corpus es
   heterogéneo (de 3 a 9 claves) y hay **dos** ADR cuyo YAML no carga —
   `0107` y `0108` llevan `related: [hallazgo #11 (…), ADR 0072]`, y el `#` abre
   un comentario dentro de una secuencia de flujo. Si la guarda dependiera de
   `yaml.safe_load`, un `rejects:` en cualquiera de esos dos sería invisible: la
   guarda pasaría en verde ignorando justo el fichero roto.
2. **Cada descubrimiento lleva su aserción de no-vacuidad**
   (`docs/03-guides/verificar-antes-de-implementar.md` §4): una guarda estática
   que deja de encontrar sujetos pasa vacía y envejece sin avisar. Aquí eso sería
   grave por partida doble, porque el corpus de `rejects:` es pequeño a propósito.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_ADR_DIR = _ROOT / "docs" / "05-architecture-decisions"
_ROADMAP = _ROOT / "docs" / "roadmap"
_CLAUDE_MD = _ROOT / "CLAUDE.md"

#: El bloque `---` … `---` de cabecera. Se ancla al principio del fichero: un
#: `---` suelto a mitad de documento (separador de secciones, y hay muchos) no
#: es frontmatter.
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)

#: `rejects:` con el resto de la línea (vacío si la lista viene en bloque).
_REJECTS_RE = re.compile(r"^rejects:\s*(.*?)\s*$")

#: Un ítem de lista YAML en bloque (`  - task_prod07_09`).
_YAML_ITEM_RE = re.compile(r"^\s*-\s+(.+?)\s*$")

#: Encabezado de tarea: `#### \`task_prod07_09\` — …`.
_TASK_HEADING_RE = re.compile(r"^#{2,5}\s+`?(task_[\w.]+)`?")

#: Tarea declarada DENTRO de la propia casilla: `- [x] **\`task_part01_07\` — …`.
#: part-01 usa esta forma y no la anterior; un parser que solo mire encabezados
#: se deja fuera un plan entero.
_TASK_INLINE_RE = re.compile(r"^\s*-\s+\[([ xX])\]\s+\*\*`?(task_[\w.]+)`?")

_CHECKBOX_RE = re.compile(r"^\s*-\s+\[([ xX])\]")
_ANY_HEADING_RE = re.compile(r"^#{1,5}\s")

#: `plan_id:` del frontmatter de un fichero de roadmap.
_PLAN_ID_RE = re.compile(r"^plan_id:\s*(.+?)\s*$", re.M)

#: El número de un ADR a partir de su nombre de fichero (`0150-…md` → `0150`).
_ADR_NUMBER_RE = re.compile(r"^(\d{4,})-")


# ---------------------------------------------------------------------------
# Descubrimiento: los ADR y su `rejects:`
# ---------------------------------------------------------------------------
def _adr_files() -> list[Path]:
    """Los ADR canónicos (`NNNN-slug.md`); el README no lo es."""
    return sorted(p for p in _ADR_DIR.glob("*.md") if _ADR_NUMBER_RE.match(p.name))


def _frontmatter_block(path: Path) -> str | None:
    """El texto crudo del frontmatter, o ``None`` si el fichero no lleva."""
    match = _FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def _parse_rejects(block: str) -> tuple[str, ...]:
    """Los valores de `rejects:` de un frontmatter, sin pasar por PyYAML.

    Tolera las tres formas que admite YAML y que el corpus mezcla:
    lista de flujo (``rejects: [a, b]``), lista en bloque (``rejects:`` y luego
    ``  - a``) y escalar suelto (``rejects: a``). Devuelve ``()`` cuando la clave
    no está.
    """
    lines = block.split("\n")
    for index, line in enumerate(lines):
        match = _REJECTS_RE.match(line)
        if match is None:
            continue
        rest = match.group(1)
        if rest.startswith("["):
            inner = rest.strip().lstrip("[").rstrip("]")
            return tuple(_clean(v) for v in inner.split(",") if _clean(v))
        if rest:
            return (_clean(rest),)
        items: list[str] = []
        for following in lines[index + 1 :]:
            item = _YAML_ITEM_RE.match(following)
            if item is None:
                break
            items.append(_clean(item.group(1)))
        return tuple(i for i in items if i)
    return ()


def _clean(value: str) -> str:
    """Quita comillas y espacios de un valor YAML escrito a mano."""
    return value.strip().strip("\"'").strip()


def _adr_rejections() -> list[tuple[str, str]]:
    """``(número de ADR, id rechazado)`` para todo `rejects:` del corpus."""
    out: list[tuple[str, str]] = []
    for path in _adr_files():
        block = _frontmatter_block(path)
        numbered = _ADR_NUMBER_RE.match(path.name)
        if block is None or numbered is None:
            continue
        for target in _parse_rejects(block):
            out.append((numbered.group(1), target))
    return out


# ---------------------------------------------------------------------------
# Descubrimiento: las casillas del roadmap
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _TaskBlock:
    """Una casilla del roadmap: dónde vive, si está marcada y su texto."""

    plan_file: str
    marked: bool | None
    text: str


def _blocks_of_one_plan(md: Path) -> dict[str, _TaskBlock]:
    """Las tareas de UN fichero de roadmap, indexadas por ``task_id``.

    El bloque de una tarea va desde su encabezado (o desde su casilla, en los
    planes que declaran el id dentro del ``- [x]``) hasta el siguiente
    encabezado. ``marked`` es el estado de la PRIMERA casilla del bloque — la
    del título; las de más abajo son sub-listas de la descripción.
    """
    out: dict[str, _TaskBlock] = {}
    current: str | None = None
    marked: bool | None = None
    buffer: list[str] = []

    for line in md.read_text(encoding="utf-8").split("\n"):
        heading = _TASK_HEADING_RE.match(line)
        inline = _TASK_INLINE_RE.match(line)
        starts_task = heading is not None or inline is not None
        if starts_task or (current is not None and _ANY_HEADING_RE.match(line)):
            if current is not None:
                out.setdefault(current, _TaskBlock(md.name, marked, "\n".join(buffer)))
            if heading is not None:
                current, marked, buffer = heading.group(1), None, [line]
            elif inline is not None:
                current = inline.group(2)
                marked = inline.group(1).lower() == "x"
                buffer = [line]
            else:
                current, marked, buffer = None, None, []
            continue
        if current is not None:
            box = _CHECKBOX_RE.match(line)
            if box is not None and marked is None:
                marked = box.group(1).lower() == "x"
            buffer.append(line)

    if current is not None:
        out.setdefault(current, _TaskBlock(md.name, marked, "\n".join(buffer)))
    return out


def _task_blocks() -> dict[str, _TaskBlock]:
    """Todas las tareas de todo el roadmap. Gana la primera aparición del id."""
    out: dict[str, _TaskBlock] = {}
    for md in sorted(_ROADMAP.glob("*.md")):
        for task_id, block in _blocks_of_one_plan(md).items():
            out.setdefault(task_id, block)
    return out


def _plan_files() -> dict[str, Path]:
    """``plan_id`` → fichero, para los ficheros de roadmap que lo declaran."""
    out: dict[str, Path] = {}
    for md in sorted(_ROADMAP.glob("*.md")):
        block = _frontmatter_block(md)
        if block is None:
            continue
        match = _PLAN_ID_RE.search(block)
        if match:
            out[_clean(match.group(1))] = md
    return out


def _cites_adr(text: str, number: str) -> bool:
    """¿El texto nombra ese ADR? Acepta `ADR 0150`, `0150-slug.md` y `[0150]`."""
    return re.search(rf"\b0*{int(number)}\b", text) is not None or number in text


# ---------------------------------------------------------------------------
# No-vacuidad de los tres descubrimientos
# ---------------------------------------------------------------------------
def test_the_discovery_finds_the_adr_corpus() -> None:
    """Si el localizador de ADR deja de encontrarlos, todo lo demás pasa solo."""
    with_frontmatter = [p for p in _adr_files() if _frontmatter_block(p) is not None]
    assert len(with_frontmatter) >= 100, (
        "esperaba el corpus entero de ADR con frontmatter; encontre "
        f"{len(with_frontmatter)} de {len(_adr_files())} ficheros"
    )


def test_the_discovery_finds_the_roadmap_tasks() -> None:
    """Idem para las casillas: sin universo, el chequeo de existencia miente."""
    blocks = _task_blocks()
    assert len(blocks) >= 500, f"esperaba cientos de casillas de roadmap, encontre {len(blocks)}"
    assert len(_plan_files()) >= 30, (
        f"esperaba decenas de plan_id declarados, encontre {len(_plan_files())}"
    )


def test_at_least_one_adr_declares_what_it_rejects() -> None:
    """El campo `rejects:` tiene que estar USADO, no solo documentado.

    Es la guarda contra el modo de fallo dominante de esta base (§5 de
    `verificar-antes-de-implementar`): mecanismo entregado, cero llamantes. Un
    `rejects:` que nadie escribe deja los tres chequeos de abajo pasando en
    vacío, y la regla vuelve a resolverse a ojo.
    """
    rejections = _adr_rejections()
    assert len(rejections) >= 3, (
        "ningun ADR (o casi) declara `rejects:`. Los casos reales estan medidos: "
        "0133->task_prod09_12, 0141->task_prod08_shared_logging_08 y "
        "task_prod08_metrics_workers_05, 0150->task_prod07_09, 0151->task_prod13_15. "
        f"Encontre {len(rejections)}"
    )


# ---------------------------------------------------------------------------
# Las tres reglas del campo
# ---------------------------------------------------------------------------
def test_rejects_points_at_something_that_exists() -> None:
    """Un `rejects:` a un id inexistente es una referencia muerta.

    Peor que no tenerla: quien la lea creera que la comprobo.
    """
    tasks, plans = _task_blocks(), _plan_files()
    dead = [
        (adr, target)
        for adr, target in _adr_rejections()
        if target not in tasks and target not in plans
    ]
    assert not dead, (
        "estos `rejects:` apuntan a un plan_id / task_id que NO existe en "
        "docs/roadmap/:\n" + "\n".join(f"  ADR {adr} -> {target}" for adr, target in sorted(dead))
    )


def test_a_rejected_task_is_closed_not_open() -> None:
    """Una casilla rechazada por un ADR `accepted` no puede seguir abierta.

    Es la contradiccion literal que motivo el plan: el ADR dice «esto no se
    hace» y el roadmap sigue pidiendolo. Cerrarla en negativo —marcarla `[x]`
    con la nota de por que NO se hizo— es lo unico que impide que el siguiente
    implementador la coja de buena fe.
    """
    tasks = _task_blocks()
    open_ones = [
        (adr, target)
        for adr, target in _adr_rejections()
        if target in tasks and tasks[target].marked is not True
    ]
    assert not open_ones, (
        "un ADR rechaza estas casillas y siguen SIN marcar. O el ADR sobra, o "
        "la casilla se cierra en negativo con su nota de por que no se hizo:\n"
        + "\n".join(
            f"  ADR {adr} -> {target} ({tasks[target].plan_file})"
            for adr, target in sorted(open_ones)
        )
    )


def test_the_rejected_target_cites_the_adr_back() -> None:
    """La relacion consta en los DOS lados, no solo en el ADR.

    Un `rejects:` de un solo sentido no arregla el problema original: el
    implementador abre el plan, no el corpus de ADR. La nota de cierre de la
    casilla (o el plan, cuando lo rechazado es un plan entero) tiene que nombrar
    al ADR que la anulo.
    """
    tasks, plans = _task_blocks(), _plan_files()
    orphan: list[tuple[str, str]] = []
    for adr, target in _adr_rejections():
        if target in tasks:
            text = tasks[target].text
        elif target in plans:
            text = plans[target].read_text(encoding="utf-8")
        else:
            continue  # cubierto por el test de referencias muertas
        if not _cites_adr(text, adr):
            orphan.append((adr, target))
    assert not orphan, (
        "estos rechazos solo constan en el ADR; el documento rechazado no lo "
        "nombra, asi que quien lea el roadmap no se entera:\n"
        + "\n".join(f"  ADR {adr} -> {target}" for adr, target in sorted(orphan))
    )


# ---------------------------------------------------------------------------
# La cadena de precedencia, en `CLAUDE.md`
# ---------------------------------------------------------------------------
#: El orden que firmo el operador el 2026-08-12, de mas fuerte a mas debil. Se
#: comprueba la SECUENCIA, no solo la presencia: una cadena con los mismos
#: eslabones en otro orden es una regla distinta.
PRECEDENCE_CHAIN: tuple[str, ...] = (
    ".docx",
    "CLAUDE.md",
    "decisión escrita del operador",
    "ADR `accepted` posterior",
    "plan",
    "código",
    "intuición",
)


def _precedence_section() -> str:
    """La seccion de precedencia de `CLAUDE.md`, hasta el siguiente `##`."""
    text = _CLAUDE_MD.read_text(encoding="utf-8")
    marker = "## Qué manda cuando dos documentos se contradicen"
    assert marker in text, (
        f"CLAUDE.md ya no tiene la seccion «{marker}». Es el sitio donde se "
        "busca la regla de precedencia; si se renombra, hay que renombrarla aqui."
    )
    after = text.split(marker, 1)[1]
    return after.split("\n## ", 1)[0]


def _precedence_chain_declaration() -> str:
    """La linea de la seccion que DECLARA la cadena (la cita con los `>`).

    Se busca la declaracion, no la seccion entera: palabras como «plan» o
    «codigo» salen tambien en la prosa que la explica, y comprobar el orden
    sobre todo el texto daria por buena una cadena desordenada solo porque la
    prosa nombra los eslabones en otro orden. Lo aprendi rompiendolo: la primera
    version de esta guarda fallaba por el «(plan» del parrafo introductorio.
    """
    runs: list[str] = []
    current: list[str] = []
    for line in _precedence_section().split("\n"):
        if line.lstrip().startswith(">"):
            current.append(line.lstrip().lstrip(">").strip())
            continue
        if current:
            runs.append(" ".join(current))
            current = []
    if current:
        runs.append(" ".join(current))
    candidates = [run for run in runs if "**>**" in run]
    assert len(candidates) == 1, (
        "esperaba UNA cita que declare la cadena de precedencia con sus "
        f"eslabones separados por `**>**`; encontre {len(candidates)}"
    )
    return candidates[0]


def test_claude_md_declares_the_precedence_order() -> None:
    """La cadena esta escrita entera y en orden, en una sola declaracion."""
    declaration = _precedence_chain_declaration()
    positions: list[int] = []
    for link in PRECEDENCE_CHAIN:
        index = declaration.find(link)
        assert index >= 0, f"la cadena de precedencia de CLAUDE.md no nombra «{link}»"
        positions.append(index)
    assert positions == sorted(positions), (
        "los eslabones de la cadena aparecen DESORDENADOS en CLAUDE.md; el orden "
        f"esperado es {' > '.join(PRECEDENCE_CHAIN)}"
    )
    assert declaration.count("**>**") == len(PRECEDENCE_CHAIN) - 1, (
        "la cadena declara un numero de saltos distinto del de eslabones: o "
        "sobra un eslabon sin comprobar, o falta uno"
    )


def test_claude_md_binds_an_adr_to_update_it_in_the_same_commit() -> None:
    """La regla fina, que es la que evita que `CLAUDE.md` envejezca.

    Un ADR posterior no gana por ser posterior: gana porque al aceptarse deja
    `CLAUDE.md` diciendo la verdad. Sin esta obligacion, la cadena de arriba se
    convierte en su contraria a los seis meses — `CLAUDE.md` manda sobre el ADR,
    pero dice algo que un ADR aceptado ya derogo.
    """
    section = _precedence_section()
    assert "mismo commit" in section, (
        "falta la obligacion de actualizar CLAUDE.md en el MISMO commit que "
        "acepta el ADR que lo contradice"
    )
    assert "rejects:" in section, (
        "la seccion de precedencia debe remitir al campo `rejects:` del "
        "frontmatter del ADR: es la mitad mecanizable de la regla"
    )
