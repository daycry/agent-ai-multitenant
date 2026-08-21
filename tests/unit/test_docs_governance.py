"""Doc-lint: la documentación de gobernanza no puede divergir del repo real.

Plan prod-15 (`task_gov_claude_md_01`, `task_gov_arch_overview_02`,
`task_gov_indices_06`). Estos tests son **quirúrgicos a propósito** (listas de
servicios, nombres de carpeta, recuentos, nombre de rama) — no semánticos —
porque el riesgo que cubren es el drift silencioso, no la prosa.

Cada guarda lleva su aserción de "encontré algo": un parser que deja de
encontrar entradas pasaría vacío y envejecería sin avisar
(`docs/03-guides/verificar-antes-de-implementar.md` §4).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_CLAUDE_MD = _ROOT / "CLAUDE.md"
_ARCH_OVERVIEW = _ROOT / "docs" / "context" / "architecture-overview.md"
_ROADMAP = _ROOT / "docs" / "roadmap"
_CONVENTIONS = _ROOT / "docs" / "context" / "conventions.md"

#: La rama por defecto real del repo (ver `git symbolic-ref
#: refs/remotes/origin/HEAD` → `origin/master`). Renombrarla a `main` es una
#: decisión aparte declarada fuera de alcance en prod-15.
DEFAULT_BRANCH = "master"


# ---------------------------------------------------------------------------
# Helpers: parseo del árbol ASCII de CLAUDE.md
# ---------------------------------------------------------------------------
_ENTRY_RE = re.compile(r"^(?P<prefix>[│ ]*)(?:├──|└──) (?P<name>[^\s#]+)(?P<rest>.*)$")


def _repo_tree_block() -> list[str]:
    """Las líneas del bloque ``` bajo "Estructura del Repositorio"."""
    text = _CLAUDE_MD.read_text(encoding="utf-8")
    marker = "## Estructura del Repositorio"
    assert marker in text, "CLAUDE.md ya no tiene la sección de estructura"
    after = text.split(marker, 1)[1]
    fence = after.split("```", 2)
    assert len(fence) >= 3, "el árbol de CLAUDE.md no está en un bloque ```"
    return fence[1].splitlines()


def _tree_children(section: str) -> dict[str, str]:
    """Hijos directos de ``section`` (p.ej. ``apps/``) en el árbol.

    Devuelve ``{nombre_sin_slash: resto_de_la_línea}``; el resto contiene los
    comentarios de anotación (`# RESERVADA`, `# previsto`, …).
    """
    out: dict[str, str] = {}
    current: str | None = None
    for line in _repo_tree_block():
        m = _ENTRY_RE.match(line)
        if m is None:
            continue
        depth = len(m.group("prefix"))
        name = m.group("name")
        if depth == 0:
            current = name
            continue
        if depth == 4 and current == section:
            out[name.rstrip("/")] = m.group("rest")
    return out


def _has_source(directory: Path) -> bool:
    """¿Contiene la carpeta algo más que `.gitkeep`/README?"""
    for pattern in ("*.py", "*.ts", "*.tsx", "*.toml", "*.json"):
        for found in directory.rglob(pattern):
            if "node_modules" in found.parts or "dist" in found.parts:
                continue
            return True
    return False


#: Palabras que marcan una carpeta como reservada / no implementada todavía.
_RESERVED_MARKERS = ("RESERVADA", "previsto", "vacía")


# ---------------------------------------------------------------------------
# task_gov_claude_md_01
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("section", ["apps/", "packages/"])
def test_claude_md_tree_matches_repo(section: str) -> None:
    """El árbol de CLAUDE.md lista EXACTAMENTE las carpetas reales.

    Es el contrato del contexto que cargan los agentes IA: si sobra una
    carpeta, trabajan sobre una que no existe; si falta, no saben que existe
    (docsroadmap-1: faltaban `apps/watchdog` y `packages/sdk-*`).
    """
    documented = _tree_children(section)
    assert len(documented) >= 5, (
        f"el parser del árbol dejó de encontrar hijos de {section} "
        f"(vio {len(documented)}): el formato del árbol cambió"
    )
    real = {p.name for p in (_ROOT / section.rstrip("/")).iterdir() if p.is_dir()}

    missing = sorted(real - set(documented))
    extra = sorted(set(documented) - real)
    assert not missing, f"{section} existe en el repo y NO está en CLAUDE.md: {missing}"
    assert not extra, f"CLAUDE.md lista {section} que no existen: {extra}"


@pytest.mark.parametrize("section", ["apps/", "packages/"])
def test_claude_md_marks_empty_components_as_reserved(section: str) -> None:
    """Una carpeta sin código debe estar anotada como reservada/prevista.

    Al revés también: una carpeta con código no puede estar anotada como
    vacía (era el caso de `packages/shared-domain`, que sí tiene código).
    """
    documented = _tree_children(section)
    assert documented, f"el parser no encontró hijos de {section}"

    lying_empty: list[str] = []
    lying_full: list[str] = []
    seen_reserved = 0
    for name, rest in documented.items():
        path = _ROOT / section.rstrip("/") / name
        annotated = any(marker in rest for marker in _RESERVED_MARKERS)
        if annotated:
            seen_reserved += 1
        has_code = _has_source(path)
        if not has_code and not annotated:
            lying_empty.append(name)
        if has_code and annotated:
            lying_full.append(name)

    assert seen_reserved >= 2, (
        f"{section}: el detector de anotaciones no vio ninguna carpeta marcada "
        f"como reservada (vio {seen_reserved}); cambiaron las palabras de "
        f"{_RESERVED_MARKERS} y este test estaría pasando vacío"
    )
    assert not lying_empty, (
        f"{section}: carpetas SIN código y sin anotar como reservadas "
        f"(el agente creerá que hay código donde solo hay .gitkeep): {lying_empty}"
    )
    assert not lying_full, (
        f"{section}: carpetas CON código anotadas como vacías/reservadas: {lying_full}"
    )


def test_claude_md_no_main_branch() -> None:
    """El protocolo de CLAUDE.md no puede mandar a mergear/pushear a `main`.

    La rama por defecto real es `master`; decirlo mal manda a los agentes a
    una rama que no existe (docsroadmap-1).
    """
    text = _CLAUDE_MD.read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in text.splitlines()
        if re.search(r"`main`", line) and DEFAULT_BRANCH not in line
    ]
    assert not offenders, (
        "CLAUDE.md menciona `main` como rama sin decir que la real es "
        f"`{DEFAULT_BRANCH}`: {offenders}"
    )


def test_conventions_default_branch_is_real() -> None:
    """`conventions.md` §Ramas documenta la rama por defecto real."""
    text = _CONVENTIONS.read_text(encoding="utf-8")
    assert "## Git" in text, "conventions.md ya no tiene la sección Git"
    offenders = [line.strip() for line in text.splitlines() if re.match(r"^- `main`", line.strip())]
    assert not offenders, (
        f"conventions.md declara `main` como rama default; la real es "
        f"`{DEFAULT_BRANCH}`: {offenders}"
    )


# ---------------------------------------------------------------------------
# task_gov_arch_overview_02
# ---------------------------------------------------------------------------
def _core_services() -> tuple[str, ...]:
    src = _ROOT / "apps" / "installer" / "backend" / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from installer_backend.compose_generator import CORE_SERVICES

    return CORE_SERVICES


def test_arch_overview_control_plane_matches_compose_generator() -> None:
    """El "plano de control" documentado solo lista servicios que el installer genera.

    docsroadmap-4: el documento listaba `personal-assistant`,
    `webhook-dispatcher` y `memorizer`, que NO son servicios — son módulos
    dentro de api-server (ADR 0033). Un operador que lea eso busca contenedores
    que no arrancan nunca.
    """
    core = _core_services()
    assert len(core) >= 10, f"CORE_SERVICES quedó sospechosamente corto: {core}"

    text = _ARCH_OVERVIEW.read_text(encoding="utf-8")
    marker = "**Plano de control**"
    assert marker in text, "architecture-overview.md ya no describe el plano de control"
    paragraph = text.split(marker, 1)[1].split("\n\n", 1)[0]

    # Nombres de servicio: minúsculas y guiones, SIN puntos ni barras (así los
    # `fichero.py` y las rutas de test que cita el párrafo no cuentan como
    # servicios). Ninguno de CORE_SERVICES lleva punto.
    documented = set(re.findall(r"`([a-z][a-z0-9-]*)`", paragraph))
    assert len(documented) >= 4, (
        f"el parser no encontró servicios en el párrafo del plano de control "
        f"(vio {sorted(documented)})"
    )
    unknown = sorted(documented - set(core))
    assert not unknown, (
        "el plano de control documenta servicios que el installer NO genera "
        f"(CORE_SERVICES): {unknown}"
    )


#: Los nombres que, si aparecen como nodo del plano de control, son mentira:
#: módulos internos de api-server presentados como contenedores (ADR 0033).
_PHANTOM_SERVICES = frozenset({"personal-assistant", "webhook-dispatcher", "memorizer", "web-app"})


def test_arch_overview_mermaid_has_no_phantom_services() -> None:
    """El diagrama Mermaid del plano de control tampoco inventa contenedores."""
    core = set(_core_services())
    assert not (_PHANTOM_SERVICES & core), (
        "el installer empezó a generar uno de los servicios que este test "
        "considera fantasma: revisar la lista _PHANTOM_SERVICES"
    )

    text = _ARCH_OVERVIEW.read_text(encoding="utf-8")
    marker = 'subgraph app["Plano de control'
    assert marker in text, "el subgrafo del plano de control cambió de nombre"
    block = text.split(marker, 1)[1].split("\n    end", 1)[0]

    labels = [label.strip() for label in re.findall(r'\w+\["([^"<]+)', block)]
    assert len(labels) >= 3, f"el parser no encontró nodos en el subgrafo: {labels}"
    phantoms = sorted(set(labels) & _PHANTOM_SERVICES)
    assert not phantoms, (
        f"el subgrafo del plano de control dibuja como contenedor lo que es un "
        f"módulo interno de api-server: {phantoms}"
    )


# ---------------------------------------------------------------------------
# task_gov_indices_06
# ---------------------------------------------------------------------------
def _numbered_plans() -> list[Path]:
    """Los planes de CONSTRUCCIÓN numerados. Un informe fechado no es un plan.

    Contaba «todo fichero que empieza por dígito», y eso incluye a los informes
    con nombre fechado (`2026-08-12-analisis-…`). Al añadir dos análisis el
    2026-08-12 el recuento subió de 35 a 37 y el test pidió declarar «37 planes
    de construcción» — o sea, pidió que el README mintiera para que él pasara.

    El arreglo no es subir el número: es que la medida mida lo que dice medir. Un
    plan de construcción tiene `plan_id` en su frontmatter; un informe lleva
    `status: informe` y no lo tiene. Se filtra por eso, que es la propiedad real
    y no una coincidencia del nombre del fichero.
    """
    plans = []
    for p in sorted(_ROADMAP.glob("*.md")):
        if not re.match(r"^\d", p.name):
            continue
        cabecera = p.read_text(encoding="utf-8")[:600]
        if re.search(r"^status:\s*informe\s*$", cabecera, re.M):
            continue
        plans.append(p)
    return plans


def test_roadmap_readme_count_matches_files() -> None:
    """El README declara el recuento real de planes numerados.

    docsroadmap-3: decía "17 planes de construcción" con 35 ficheros numerados
    en disco, y no mencionaba la serie prod-01…prod-18.
    """
    readme = (_ROADMAP / "README.md").read_text(encoding="utf-8")
    real = len(_numbered_plans())
    assert real >= 20, f"el descubrimiento de planes numerados falló (vio {real})"

    declared = re.search(r"\*\*(\d+) planes de construcción\*\*", readme)
    assert declared is not None, (
        "el README ya no declara el recuento de planes de construcción "
        "(o cambió el formato `**N planes de construcción**`)"
    )
    assert int(declared.group(1)) == real, (
        f"README dice {declared.group(1)} planes de construcción; en disco hay {real}"
    )


def test_roadmap_readme_links_every_plan() -> None:
    """Ningún fichero de `docs/roadmap/` queda huérfano del índice.

    Un plan que el índice no menciona es un plan que nadie encuentra — la
    causa de que la serie prod-* llevara semanas invisible.
    """
    readme = (_ROADMAP / "README.md").read_text(encoding="utf-8")
    files = [p for p in sorted(_ROADMAP.glob("*.md")) if p.name != "README.md"]
    assert len(files) >= 50, f"el descubrimiento de ficheros falló (vio {len(files)})"

    orphans = [p.name for p in files if p.name not in readme]
    assert not orphans, f"ficheros de docs/roadmap/ no enlazados desde README.md: {orphans}"


def test_execution_sequence_is_archived_not_authoritative() -> None:
    """EXECUTION-SEQUENCE.md es histórico y no promete actualizarse (D3).

    Duplicar estado es la causa raíz de docsroadmap-3 y -6: el documento
    prometía "se actualiza al cerrar cada ola" y nunca lo hizo.
    """
    path = _ROADMAP / "EXECUTION-SEQUENCE.md"
    text = path.read_text(encoding="utf-8")
    front = text.split("---", 2)[1]
    assert re.search(r"^status:\s*(obsolete|archived)\s*$", front, re.M), (
        "EXECUTION-SEQUENCE.md debe declararse obsolete/archived en su frontmatter"
    )
    assert "se actualiza al cerrar cada ola" not in text, (
        "EXECUTION-SEQUENCE.md sigue prometiendo actualizarse por ola: esa "
        "promesa es la que lo dejó mintiendo 8 fases"
    )
