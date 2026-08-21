"""Tests de contrato de los ADR del Plan 06.17 (capacitación de agentes).

Plan 06.17 — Fase 0 (`task_06_17_01`). Valida que los tres ADR de decisiones de
producto del plan (0053-0055) cumplen el contrato exigido por el roadmap **antes**
de cerrar las tareas dependientes:

  * frontmatter YAML parseable con ``status: accepted``, ``title``, ``date`` y un
    campo de plan (``plan_referenced`` / ``plan_id``) que apunta a
    ``06.17-capacitacion-agentes``;
  * las cuatro secciones canónicas presentes — Contexto, Opciones (con **≥2**
    opciones enumeradas), Decisión y Consecuencias;
  * cada ADR **enlaza el plan 06.17** en el cuerpo (trazabilidad);
  * el ADR 0053 **referencia el ADR 0026** (agent-scoped KBs: la decisión es
    mantenerlo, no crear un subsistema TeamKnowledgeBase nuevo);
  * el ADR 0055 **enumera los cuatro proveedores del catálogo cerrado** del ADR
    0021 (``claude_sdk``, ``copilot``, ``azure_foundry``, ``ollama``);
  * además, el ADR 0026 gana una **nota que enlaza el ADR 0053**.

Son tests puros: solo leen los ficheros Markdown del repo (sin DB, sin red, sin
reloj). Cada caso se parametriza con un id ``adr_0053``..``adr_0055`` para que el
roadmap pueda filtrar con ``-k``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

# --- localización de los ADR ----------------------------------------------

# tests/docs/test_adrs_capacitacion.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADR_DIR = _REPO_ROOT / "docs" / "05-architecture-decisions"

#: ADR del Plan 06.17 -> nombre de fichero (stem). El id de parametrización es la
#: clave (``adr_0053``..``adr_0055``) para que ``-k`` del roadmap funcione.
ADR_FILES: dict[str, str] = {
    "adr_0053": "0053-capacidad-de-equipo.md",
    "adr_0054": "0054-acoplamiento-contexto-proyecto-task.md",
    "adr_0055": "0055-validacion-model-config.md",
}

PLAN_ID = "06.17-capacitacion-agentes"

#: Campos del frontmatter que pueden contener la referencia al plan. El roadmap
#: admite ``plan_referenced`` *o* ``plan_id``; los ADR usan ``plan_referenced``.
_PLAN_FIELDS = ("plan_referenced", "plan_id")

#: Encabezados canónicos (se hace match por prefijo, p. ej. "Opciones
#: consideradas" satisface "Opciones"; "Decisión" en español acentuado).
_REQUIRED_SECTION_PREFIXES = ("Contexto", "Opciones", "Decisión", "Consecuencias")

#: Los cuatro proveedores del catálogo cerrado del ADR 0021 que el ADR 0055
#: debe enumerar (los valores enum exactos que la validación de model_config
#: acepta).
_ADR_0021_PROVIDERS = ("claude_sdk", "copilot", "azure_foundry", "ollama")


# --- helpers ---------------------------------------------------------------


def _adr_path(stem: str) -> Path:
    return _ADR_DIR / stem


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Separa el bloque de frontmatter ``--- ... ---`` del cuerpo Markdown."""
    if not text.startswith("---"):
        return "", text
    # El primer "---" abre; el siguiente "\n---" en línea propia cierra.
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        return "", text
    frontmatter = parts[0][len("---") :]
    body = parts[1].lstrip("\n")
    body = body.lstrip()
    return frontmatter, body


def _heading_titles(body: str) -> list[str]:
    """Devuelve los títulos de los encabezados ``##`` (nivel 2) del cuerpo."""
    titles: list[str] = []
    for line in body.splitlines():
        m = re.match(r"^##\s+(.*\S)\s*$", line)
        if m:
            titles.append(m.group(1).strip())
    return titles


def _section_body(body: str, prefix: str) -> str:
    """Texto entre el encabezado ``## <prefix>...`` y el siguiente ``## ``."""
    lines = body.splitlines()
    start: int | None = None
    for i, line in enumerate(lines):
        m = re.match(r"^##\s+(.*\S)\s*$", line)
        if m and m.group(1).strip().startswith(prefix):
            start = i + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start, len(lines)):
        if re.match(r"^##\s+", lines[j]):
            end = j
            break
    return "\n".join(lines[start:end])


def _count_options(options_body: str) -> int:
    """Cuenta las opciones enumeradas en la sección 'Opciones consideradas'.

    Las opciones se redactan como ítems en negrita con etiqueta — p. ej.
    ``- **A. ...**``, ``- **T-A. ...**``, ``- **P-A. ...**`` o ``- **M-A. ...**``.
    Contamos los bullets cuya negrita arranca con una etiqueta de opción
    (una o dos letras opcionalmente con sufijo ``-X``, seguida de punto).
    """
    pattern = re.compile(r"^\s*[-*]\s+\*\*[A-Z](?:-[A-Z])?\.")
    return sum(1 for line in options_body.splitlines() if pattern.match(line))


# --- fixtures de parametrización -------------------------------------------

_ALL_ADRS = list(ADR_FILES.items())


@pytest.fixture(params=_ALL_ADRS, ids=[k for k, _ in _ALL_ADRS])
def adr(request: pytest.FixtureRequest) -> tuple[str, str, str]:
    """(adr_key, frontmatter, body) para cada ADR del plan."""
    adr_key, stem = request.param
    path = _adr_path(stem)
    text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    return adr_key, frontmatter, body


# --- existencia ------------------------------------------------------------


@pytest.mark.parametrize(("adr_key", "stem"), _ALL_ADRS, ids=[k for k, _ in _ALL_ADRS])
def test_adr_file_exists(adr_key: str, stem: str) -> None:
    assert _adr_path(stem).is_file(), f"{adr_key}: falta {stem}"


# --- frontmatter -----------------------------------------------------------


def test_adr_frontmatter_is_valid_yaml(adr: tuple[str, str, str]) -> None:
    adr_key, frontmatter, _ = adr
    assert frontmatter.strip(), f"{adr_key}: frontmatter ausente o no delimitado por ---"
    data = yaml.safe_load(frontmatter)
    assert isinstance(data, dict), f"{adr_key}: el frontmatter no es un mapeo YAML"


def test_adr_status_is_accepted(adr: tuple[str, str, str]) -> None:
    adr_key, frontmatter, _ = adr
    data = yaml.safe_load(frontmatter)
    assert data.get("status") == "accepted", f"{adr_key}: status debe ser 'accepted'"


def test_adr_has_title(adr: tuple[str, str, str]) -> None:
    adr_key, frontmatter, _ = adr
    data = yaml.safe_load(frontmatter)
    title = data.get("title")
    assert isinstance(title, str) and title.strip(), f"{adr_key}: falta 'title'"


def test_adr_has_date(adr: tuple[str, str, str]) -> None:
    adr_key, frontmatter, _ = adr
    data = yaml.safe_load(frontmatter)
    assert "date" in data, f"{adr_key}: falta 'date'"
    assert data["date"] is not None, f"{adr_key}: 'date' no puede ser null"


def test_adr_frontmatter_references_plan_06_17(adr: tuple[str, str, str]) -> None:
    adr_key, frontmatter, _ = adr
    data = yaml.safe_load(frontmatter)
    found = [data[f] for f in _PLAN_FIELDS if f in data and data[f] is not None]
    assert found, f"{adr_key}: falta un campo de plan ({' / '.join(_PLAN_FIELDS)})"
    assert any(str(v) == PLAN_ID for v in found), (
        f"{adr_key}: el campo de plan debe apuntar a {PLAN_ID!r}, no a {found!r}"
    )


# --- secciones canónicas ---------------------------------------------------


@pytest.mark.parametrize("prefix", _REQUIRED_SECTION_PREFIXES)
def test_adr_has_required_sections(adr: tuple[str, str, str], prefix: str) -> None:
    adr_key, _, body = adr
    titles = _heading_titles(body)
    assert any(t.startswith(prefix) for t in titles), (
        f"{adr_key}: falta la sección '## {prefix}...' (encabezados: {titles})"
    )


def test_adr_options_has_at_least_two_options(adr: tuple[str, str, str]) -> None:
    adr_key, _, body = adr
    options_body = _section_body(body, "Opciones")
    assert options_body.strip(), f"{adr_key}: sección de Opciones vacía"
    n = _count_options(options_body)
    assert n >= 2, f"{adr_key}: la sección de Opciones debe enumerar ≥2 opciones (se hallaron {n})"


def test_adr_links_plan_06_17_in_body(adr: tuple[str, str, str]) -> None:
    adr_key, _, body = adr
    assert PLAN_ID in body, f"{adr_key}: el cuerpo del ADR debe enlazar el plan {PLAN_ID}"


# --- requisitos específicos de ADR 0053 ------------------------------------


def _adr_body(adr_key: str) -> str:
    text = _adr_path(ADR_FILES[adr_key]).read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)
    return body


def test_adr_0053_references_adr_0026() -> None:
    """ADR 0053 debe referenciar el ADR 0026 (agent-scoped KBs).

    La decisión es MANTENER el ADR 0026 (sin grants de KB a agentes
    ``global_builtin``, sin subsistema TeamKnowledgeBase nuevo); por eso el
    cuerpo debe enlazarlo explícitamente.
    """
    body = _adr_body("adr_0053")
    assert re.search(r"\bADR\s*0026\b", body) or re.search(r"\b0026\b", body), (
        "adr_0053: debe referenciar el ADR 0026"
    )


# --- requisitos específicos de ADR 0055 ------------------------------------


def test_adr_0055_enumerates_the_four_providers() -> None:
    """ADR 0055 debe enumerar los cuatro proveedores del catálogo cerrado (ADR 0021)."""
    body = _adr_body("adr_0055")
    for provider in _ADR_0021_PROVIDERS:
        assert provider in body, (
            f"adr_0055: debe enumerar el proveedor {provider!r} del catálogo cerrado del ADR 0021"
        )
    assert re.search(r"\bADR\s*0021\b", body) or re.search(r"\b0021\b", body), (
        "adr_0055: debe referenciar el ADR 0021 (catálogo cerrado de proveedores)"
    )


# --- el ADR 0026 gana una nota que enlaza el ADR 0053 ----------------------


def test_adr_0026_links_back_to_adr_0053() -> None:
    """El ADR 0026 debe ganar una nota que enlace el ADR 0053."""
    text = _adr_path("0026-agent-scoped-kbs.md").read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)
    assert re.search(r"\bADR\s*0053\b", body) or re.search(r"\b0053\b", body), (
        "0026: debe ganar una nota que enlace el ADR 0053"
    )
