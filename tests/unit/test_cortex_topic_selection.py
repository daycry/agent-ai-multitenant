"""Córtex F4 — selección de tema de curiosidad (pura, sin DB).

``pick_topic`` favorece frecuencia, EXCLUYE temas ya investigados recientemente,
prioriza el solape con los ``learning_goals`` de la identidad, y devuelve ``None``
si no queda candidato.
"""

from __future__ import annotations

import pytest
from api_server.cortex.curiosity import pick_topic

pytestmark = pytest.mark.unit


def test_picks_most_frequent_when_no_goals() -> None:
    freqs = [("rust", 5), ("python", 3), ("docker", 1)]
    assert pick_topic(freqs, recently_pursued=set()) == "rust"


def test_excludes_recently_pursued() -> None:
    freqs = [("rust", 5), ("python", 3)]
    # rust ya investigado (case-insensitive) → cae a python.
    assert pick_topic(freqs, recently_pursued={"RUST"}) == "python"


def test_prioritises_learning_goal_overlap_over_raw_frequency() -> None:
    freqs = [("rust", 5), ("hexagonal architecture", 2)]
    # Aunque rust es más frecuente, el córtex se propuso aprender arquitectura
    # hexagonal → gana el solape con el learning_goal.
    topic = pick_topic(
        freqs,
        recently_pursued=set(),
        learning_goals=["Hexagonal Architecture"],
    )
    assert topic == "hexagonal architecture"


def test_falls_back_to_frequency_when_no_goal_overlap() -> None:
    freqs = [("rust", 5), ("python", 3)]
    topic = pick_topic(freqs, recently_pursued=set(), learning_goals=["kubernetes"])
    assert topic == "rust"


def test_none_when_no_candidates() -> None:
    assert pick_topic([], recently_pursued=set()) is None
    # Todas excluidas → None.
    assert pick_topic([("rust", 1)], recently_pursued={"rust"}) is None
