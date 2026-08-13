"""Tests de contrato de la documentación del modelo de capacitación (Plan 06.17).

Plan 06.17 — Fase B (`task_06_17_07`). Fija el **modelo mental único** que
consume toda la UI del plan y corrige las divergencias término↔código. Son
tests puros (solo leen Markdown del repo; sin DB, sin red, sin reloj) que
verifican:

  * ``docs/04-reference/training-model.md`` **existe** con frontmatter YAML
    válido y enuncia las **cuatro categorías** SABER / RECORDAR / SER / HACER,
    el **verbo único** "Asignar/Quitar" y la **tabla de NIVELES**
    (Rol/Stack/Equipo/Plataforma);
  * el glosario gana los **headwords** operador-céntricos Capacidad / Persona /
    Contexto y la distinción **Documento vs Documentación**;
  * ``docs/04-reference/domain-model.md`` usa ``forked_from_agent_id`` (la
    columna real de ``agents``) y **no** el nombre fantasma ``parent_agent_id``;
  * el índice ``docs/03-guides/README.md`` es **completo**: lista todas las
    guías ``.md`` reales de la carpeta (salvo el propio README).

Cada bloque se aísla en su test para que un fallo señale la divergencia exacta.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

# --- localización de los documentos ----------------------------------------

# tests/docs/test_docs_training_model.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCS = _REPO_ROOT / "docs"

_TRAINING_MODEL = _DOCS / "04-reference" / "training-model.md"
_GLOSSARY = _DOCS / "context" / "glossary.md"
_DOMAIN_MODEL = _DOCS / "04-reference" / "domain-model.md"
_GUIDES_DIR = _DOCS / "03-guides"
_GUIDES_README = _GUIDES_DIR / "README.md"

#: Las cuatro categorías canónicas del modelo de capacitación.
_CATEGORIES = ("SABER", "RECORDAR", "SER", "HACER")

#: Los cuatro niveles donde se capacita (etiquetas de la tabla de NIVELES).
_LEVELS = ("Rol", "Stack", "Equipo", "Plataforma")


# --- helpers ---------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Separa el bloque de frontmatter ``--- ... ---`` del cuerpo Markdown."""
    if not text.startswith("---"):
        return "", text
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        return "", text
    frontmatter = parts[0][len("---") :]
    body = parts[1].lstrip("\n").lstrip()
    return frontmatter, body


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- training-model.md: existencia + frontmatter ---------------------------


def test_training_model_file_exists() -> None:
    assert _TRAINING_MODEL.is_file(), "falta docs/04-reference/training-model.md"


def test_training_model_frontmatter_is_valid_yaml() -> None:
    frontmatter, _ = _split_frontmatter(_read(_TRAINING_MODEL))
    assert frontmatter.strip(), "training-model.md: frontmatter ausente o no delimitado"
    data = yaml.safe_load(frontmatter)
    assert isinstance(data, dict), "training-model.md: el frontmatter no es un mapeo YAML"
    title = data.get("title")
    assert isinstance(title, str) and title.strip(), "training-model.md: falta 'title'"


# --- training-model.md: las cuatro categorías ------------------------------


@pytest.mark.parametrize("category", _CATEGORIES)
def test_training_model_lists_the_four_categories(category: str) -> None:
    _, body = _split_frontmatter(_read(_TRAINING_MODEL))
    assert category in body, (
        f"training-model.md: debe enunciar la categoría {category!r} (SABER/RECORDAR/SER/HACER)"
    )


# --- training-model.md: verbo único ----------------------------------------


def test_training_model_uses_single_verb_asignar() -> None:
    """El modelo fija un verbo único 'Asignar/Quitar' en toda la UI."""
    _, body = _split_frontmatter(_read(_TRAINING_MODEL))
    assert "Asignar" in body, "training-model.md: debe fijar el verbo único 'Asignar'"
    assert "Quitar" in body, "training-model.md: el verbo único incluye 'Quitar'"


# --- training-model.md: tabla de NIVELES -----------------------------------


def test_training_model_has_niveles_table() -> None:
    """Debe existir una sección/tabla de NIVELES (dónde se capacita qué)."""
    _, body = _split_frontmatter(_read(_TRAINING_MODEL))
    assert re.search(r"NIVELES|[Nn]iveles", body), "training-model.md: falta la tabla de NIVELES"


@pytest.mark.parametrize("level", _LEVELS)
def test_training_model_lists_the_four_levels(level: str) -> None:
    _, body = _split_frontmatter(_read(_TRAINING_MODEL))
    assert level in body, (
        f"training-model.md: la tabla de NIVELES debe incluir el nivel {level!r} "
        "(Rol/Stack/Equipo/Plataforma)"
    )


# --- glosario: headwords operador-céntricos --------------------------------


@pytest.mark.parametrize("headword", ("Capacidad", "Persona", "Contexto"))
def test_glossary_has_operator_headwords(headword: str) -> None:
    """El glosario gana los headwords Capacidad / Persona / Contexto en negrita."""
    text = _read(_GLOSSARY)
    assert re.search(rf"\*\*{headword}", text), (
        f"glossary.md: falta el headword en negrita **{headword}**"
    )


def test_glossary_distinguishes_documento_vs_documentacion() -> None:
    """El glosario distingue 'Documento' (de KB) de 'Documentación' (de /docs)."""
    text = _read(_GLOSSARY)
    assert re.search(r"\*\*Documento\b", text), (
        "glossary.md: falta el headword **Documento** (unidad ingerida en una KB)"
    )
    assert re.search(r"\*\*Documentación\b", text), (
        "glossary.md: falta el headword **Documentación** (las 7 carpetas de /docs)"
    )


# --- domain-model.md: forked_from_agent_id (no parent_agent_id) ------------


def test_domain_model_uses_forked_from_agent_id() -> None:
    text = _read(_DOMAIN_MODEL)
    assert "forked_from_agent_id" in text, (
        "domain-model.md: debe usar la columna real 'forked_from_agent_id' "
        "(ver agents en db/domain.py)"
    )


def test_domain_model_does_not_use_phantom_parent_agent_id() -> None:
    text = _read(_DOMAIN_MODEL)
    assert "parent_agent_id" not in text, (
        "domain-model.md: no debe usar el nombre fantasma 'parent_agent_id'; "
        "la columna real es 'forked_from_agent_id'"
    )


# --- 03-guides/README.md: índice completo ----------------------------------


def _guide_stems() -> set[str]:
    """Nombres de fichero de las guías ``.md`` reales (sin el propio README)."""
    return {p.name for p in _GUIDES_DIR.glob("*.md") if p.name.lower() != "readme.md"}


def test_guides_readme_index_is_complete() -> None:
    """El índice 03-guides/README.md debe listar TODAS las guías de la carpeta."""
    readme = _read(_GUIDES_README)
    missing = sorted(name for name in _guide_stems() if name not in readme)
    assert not missing, (
        f"03-guides/README.md: el índice no lista estas guías presentes en la carpeta: {missing}"
    )
