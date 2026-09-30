"""`<private>` (CWE-184): apertura con atributos/espacios, cierre laxo, anidado y sin cerrar."""

from __future__ import annotations

import pytest
from api_server.memorizer.sanitize import sanitize_memory_content, strip_private_tags


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a <private>x</private> b", "a  b"),
        ("a <private >x</private> b", "a  b"),
        ("a <private\n>x</private> b", "a  b"),
        ('a <private class="x">x</private> b', "a  b"),
        ("a <PRIVATE>x</PRIVATE > b", "a  b"),
        ("a <private>x <private>y</private> SECRETO</private> b", "a  b"),
        ("a <private>x</private> mid <private>y</private> b", "a  mid  b"),
        ("a <private>sin cerrar y todo lo que sigue", "a "),
        ("a <private>x <private>y</private> sin cerrar el externo", "a "),
        ("texto sin etiquetas, private a secas", "texto sin etiquetas, private a secas"),
        ("huérfano </private> cierre", "huérfano  cierre"),
    ],
)
def test_strip_private_tags(raw: str, expected: str) -> None:
    assert strip_private_tags(raw) == expected


def test_nothing_after_an_unclosed_open_survives_sanitize() -> None:
    text, redactions = sanitize_memory_content("publico <private >token=abc123 y mas")
    assert text == "publico"
    assert redactions >= 1
