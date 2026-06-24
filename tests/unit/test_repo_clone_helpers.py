"""Unit: helpers puros de la task de clone (ADR 0072) — nombre de repo desde la
URL del remoto y slug del proyecto."""

from __future__ import annotations

import pytest
from workers.repo_clone import _repo_name_from_url, _slugify

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/owner/api.git", "api"),
        ("git@github.com:owner/api.git", "api"),
        ("https://gitlab.com/group/sub/backend", "backend"),
        ("https://dev.azure.com/org/proj/_git/repo.git", "repo"),
        ("ssh://git@host/path/to/svc.git/", "svc"),
    ],
)
def test_repo_name_from_url(url: str, expected: str) -> None:
    assert _repo_name_from_url(url) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Mi Proyecto", "mi-proyecto"),
        ("API_v2!!", "api-v2"),
        ("  ", "project"),
        ("Pruebas Jordi", "pruebas-jordi"),
    ],
)
def test_slugify(name: str, expected: str) -> None:
    assert _slugify(name) == expected
