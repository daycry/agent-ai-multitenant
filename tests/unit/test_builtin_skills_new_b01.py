"""Ola B0.1: skills nuevas en el catálogo built-in (PHP/CI4, security, data,
LLM, web-research). Llenan huecos reales — el mayor: equipo CI4 (PHP) sin skills
de su stack. Respeta la taxonomía CERRADA de categorías (ADR 0050)."""

from __future__ import annotations

import pytest
from api_server.seeds.builtin_skills import BUILTIN_SKILLS

pytestmark = pytest.mark.unit

_NEW_SLUGS = {
    # backend
    "php-phpunit",
    "codeigniter4-hmvc",
    "doctrine-orm",
    "secure-coding-owasp",
    "sql-optimization",
    "rag-pgvector",
    # frontend
    "twig-templating",
    "state-management",
    "web-performance",
    # devops
    "dependency-audit-sca",
    "backup-recovery",
    # qa
    "contract-testing",
    "load-testing",
    # research
    "prompt-engineering",
    "eval-design",
    "web-research",
    # docs
    "changelog-authoring",
    "openapi-authoring",
}

_CATEGORIES = {"backend", "frontend", "devops", "qa", "research", "docs"}


def test_new_b01_skills_present_with_valid_shape() -> None:
    by_slug = {s.slug: s for s in BUILTIN_SKILLS}
    missing = _NEW_SLUGS - set(by_slug)
    assert not missing, f"faltan skills B0.1: {sorted(missing)}"
    for slug in _NEW_SLUGS:
        s = by_slug[slug]
        assert s.category in _CATEGORIES, f"{slug}: categoría inválida {s.category!r}"
        assert s.name and s.description and s.prompt_fragment, f"{slug}: campos vacíos"


def test_no_duplicate_slugs_in_catalog() -> None:
    slugs = [s.slug for s in BUILTIN_SKILLS]
    assert len(slugs) == len(set(slugs)), "slugs duplicados en el catálogo de skills"
