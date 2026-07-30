"""La UI de política de aprobación enseña LAS 13, y con pistas ciertas.

``shared_domain.approval_categories`` dice de sí mismo: «The admin-panel UI
mirrors this list with labels (``approval-policy/page.tsx``); keep them in sync».
Eso era un control de DISCIPLINA — nada lo comprobaba — sobre la pantalla que
decide qué acciones de un agente paran a pedir permiso. Una categoría que el
backend gatea y la UI no lista es una puerta que el operador no puede cerrar
porque no sabe que existe.

Y el segundo asunto, que motivó este fichero: el `hint` de ``data_migration``
decía solo «DDL / alembic». Desde prod-03 task_prod03_02 esa categoría gatea
además ``promote_to_kb`` — copiar un documento y todos sus chunks a otra KB del
tenant, de donde lo lee por RAG cualquier proyecto con grant. Un operador que
leyera «DDL» descartaba la categoría por irrelevante y dejaba en `auto` la única
puerta que hay sobre la escritura persistente en la base de conocimiento. Las
pistas de esta pantalla son interfaz de seguridad, no adorno.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from shared_domain.approval_categories import APPROVAL_CATEGORIES

pytestmark = pytest.mark.unit

_PAGE = Path(__file__).resolve().parents[2] / "apps/admin-panel/app/admin/approval-policy/page.tsx"


def _entries() -> dict[str, str]:
    """``{category_id: hint}`` leído de ``CATEGORY_LABELS`` del TSX.

    Se parsea el fichero en vez de importarlo porque el guard tiene que correr en
    la suite de Python, que es la que conoce la lista canónica.
    """
    source = _PAGE.read_text(encoding="utf-8")
    block = source.split("const CATEGORY_LABELS", 1)[1].split("\n];", 1)[0]
    ids = re.findall(r'id:\s*"([a-z_]+)"', block)
    hints = re.findall(r'hint:\s*"([^"]+)"', block)
    assert len(ids) == len(hints), f"ids ({len(ids)}) y hints ({len(hints)}) descuadran"
    return dict(zip(ids, hints, strict=True))


def test_the_page_exists_where_the_canonical_list_says_it_does() -> None:
    """Si la pantalla se mueve, este guard tiene que romperse en vez de pasar
    vacíamente (§4): todo lo de abajo se apoya en encontrar el fichero."""
    assert _PAGE.is_file(), f"no está {_PAGE}"


def test_the_ui_lists_exactly_the_canonical_categories() -> None:
    entries = _entries()
    assert len(entries) >= 13, f"el parseo dejó de encontrar categorías (vio {len(entries)})"
    assert set(entries) == set(APPROVAL_CATEGORIES), {
        "solo_en_la_ui": sorted(set(entries) - set(APPROVAL_CATEGORIES)),
        "solo_en_el_dominio": sorted(set(APPROVAL_CATEGORIES) - set(entries)),
    }


def test_every_category_carries_a_usable_hint() -> None:
    for category, hint in _entries().items():
        assert len(hint) >= 10, f"{category} tiene una pista inservible: {hint!r}"


def test_the_data_migration_hint_names_promote_to_kb() -> None:
    """La categoría con la que la pista engañaba: gatea `promote_to_kb`, no solo
    DDL. Si alguien vuelve a acortarla, esto avisa."""
    hint = _entries()["data_migration"]
    assert "promote_to_kb" in hint or "KB" in hint, hint


def test_the_hint_matches_what_the_runtime_actually_gates() -> None:
    """No basta con que la pista mencione la tool: la tool tiene que seguir en esa
    categoría. El día que `promote_to_kb` se mueva (p.ej. a la 14ª categoría que
    los comentarios del runtime dejan anotada), la pista pasa a mentir y este
    test lo dice."""
    from agent_runtime.approval import DEFAULT_TOOL_CATEGORIES

    assert DEFAULT_TOOL_CATEGORIES["promote_to_kb"] == "data_migration"
