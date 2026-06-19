"""El mapa rol→skills por defecto cubre los roles de los equipos built-in y
solo referencia slugs reales del catálogo (Ola B)."""

from __future__ import annotations

import pytest
from api_server.seeds.builtin_role_capabilities import (
    ROLE_DEFAULT_SKILLS,
    default_skill_slugs,
)
from api_server.seeds.builtin_skills import BUILTIN_SKILLS

pytestmark = pytest.mark.unit

_CI4_ROLES = {
    "project_manager",
    "architect",
    "backend_dev",
    "frontend_dev",
    "security",
    "specialist",
    "qa",
    "reviewer",
    "devops",
}


def test_map_covers_every_ci4_role_with_at_least_one_skill() -> None:
    for role in _CI4_ROLES:
        assert default_skill_slugs(role), f"rol {role} sin skills por defecto"


def test_map_only_references_real_catalog_slugs() -> None:
    catalog = {s.slug for s in BUILTIN_SKILLS}
    for role, slugs in ROLE_DEFAULT_SKILLS.items():
        for slug in slugs:
            assert slug in catalog, f"{role} referencia slug inexistente: {slug}"


def test_unknown_role_returns_empty() -> None:
    assert default_skill_slugs("no-such-role") == ()
