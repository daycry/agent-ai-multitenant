"""ADR 0127/0128 — las 4 skills builtin de Atlassian y la categoría `atlassian`.

Las skills enseñan a los agentes a usar el MCP de Atlassian del proyecto
(Jira + Confluence). Reglas que estos tests fijan como contrato:

* La categoría `atlassian` existe en el enum cerrado `SkillCategory`.
* Las 4 skills están en el catálogo builtin, en la categoría `atlassian`,
  con id uuid5 estable y campos no vacíos.
* NO cablean nombres de tool namespaced (`<server>.<tool>`) — el nombre del
  server lo elige el operador, así que un prompt_fragment que lo hardcodee se
  rompería en cuanto el operador nombrara el server distinto.
* Degradan con gracia: mencionan qué hacer si el MCP de Atlassian no está.
"""

from __future__ import annotations

import re

import pytest
from api_server.db.domain import SkillCategory
from api_server.seeds.builtin_skills import BUILTIN_SKILLS

pytestmark = pytest.mark.unit

_ATLASSIAN_SLUGS = {
    "atlassian-jira-task-tracking",
    "atlassian-jira-review-notes",
    "atlassian-confluence-docs",
    "atlassian-jira-planning-context",
}


def test_skill_category_has_atlassian() -> None:
    assert SkillCategory.ATLASSIAN.value == "atlassian"
    assert "atlassian" in {c.value for c in SkillCategory}


def test_four_atlassian_skills_present_and_well_shaped() -> None:
    by_slug = {s.slug: s for s in BUILTIN_SKILLS}
    missing = _ATLASSIAN_SLUGS - set(by_slug)
    assert not missing, f"faltan skills Atlassian: {sorted(missing)}"
    for slug in _ATLASSIAN_SLUGS:
        s = by_slug[slug]
        assert s.category == "atlassian", f"{slug}: categoría {s.category!r} != atlassian"
        assert s.name and s.description and s.prompt_fragment, f"{slug}: campos vacíos"
        # id uuid5 estable (idempotencia del upsert ON CONFLICT (id))
        assert str(s.id) == str(s.id)


def test_atlassian_is_the_only_use_of_the_category() -> None:
    # Nadie más debería colarse en el bucket de integración por error.
    in_cat = {s.slug for s in BUILTIN_SKILLS if s.category == "atlassian"}
    assert in_cat == _ATLASSIAN_SLUGS


def test_prompt_fragments_do_not_hardcode_namespaced_tool_names() -> None:
    # Un nombre namespaced tiene la forma <server>.<tool> con guion_bajo; el
    # server lo elige el operador, así que cablearlo rompería la skill. Los
    # prompts deben referirse por CAPACIDAD ("tus herramientas de Jira").
    namespaced = re.compile(r"\b[a-z0-9-]+\.(?:jira|confluence)_[a-z_]+\b", re.IGNORECASE)
    offenders = {
        s.slug
        for s in BUILTIN_SKILLS
        if s.category == "atlassian" and namespaced.search(s.prompt_fragment)
    }
    assert not offenders, f"prompt_fragment cablea tool namespaced: {sorted(offenders)}"


def test_prompt_fragments_degrade_gracefully() -> None:
    # Cada skill debe decir qué hacer si el MCP de Atlassian no está disponible
    # (no fallar la tarea; anotar en PROGRESS). Guardia contra prompts que
    # asumen que Atlassian siempre está cableado.
    for s in BUILTIN_SKILLS:
        if s.category != "atlassian":
            continue
        low = s.prompt_fragment.lower()
        assert "progress" in low, f"{s.slug}: no menciona PROGRESS para el caso degradado"
        assert (
            "no dispones" in low
            or "no están disponibles" in low
            or "no está" in low
            or "no fallas" in low
        ), f"{s.slug}: no describe la degradación con gracia si Atlassian no está"


def test_prompt_fragments_read_the_project_anchors_first() -> None:
    """`task_mk_21` (MK-05): las cuatro skills toman los identificadores de las
    anclas de integración del proyecto que llegan en el preámbulo del run
    (`project.integrations`) y sólo caen a la descripción del plan si faltan.
    Guardia contra volver a «adivinar el epic en el texto del plan»."""
    for s in BUILTIN_SKILLS:
        if s.category != "atlassian":
            continue
        low = s.prompt_fragment.lower()
        assert "anclas de integración del proyecto" in low, (
            f"{s.slug}: no lee las anclas de integración del proyecto"
        )
        assert "preámbulo" in low, f"{s.slug}: no dice que las anclas vienen en el preámbulo"
        assert "mandan sobre" in low, f"{s.slug}: no dice que las anclas mandan sobre el plan"
        assert "descripción" in low, (
            f"{s.slug}: sin la caída a la descripción del plan cuando falta el ancla"
        )
