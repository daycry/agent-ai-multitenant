"""C1 (investigación córtex 2026-07-11): iniciativa proactiva — decisión pura.

Todo el córtex era estrictamente reactivo: jamás escribía él primero (el
surfacing solo disparaba DENTRO de un turno del owner). `should_reach_out` +
`compose_initiative_message` son la parte pura del beat
``workers.cortex_initiative``: decide cuándo tiene sentido escribir primero
(hay algo que contar + silencio largo + sin iniciativa previa sin responder +
cap diario) y compone el mensaje determinista (sin LLM).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from api_server.cortex.initiative import (
    INITIATIVE_MIN_GAP_S,
    compose_initiative_message,
    should_reach_out,
)
from api_server.cortex.self_context import PendingLearning

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 12, 10, 0, tzinfo=UTC)


def _learning(topic: str = "pgvector 0.8", digest: str = "novedades de HNSW") -> PendingLearning:
    return PendingLearning(pursuit_id=uuid4(), topic=topic, digest=digest)


def test_reaches_out_after_long_silence_with_something_to_tell() -> None:
    last = _NOW - timedelta(seconds=INITIATIVE_MIN_GAP_S + 3600)
    assert should_reach_out(
        now=_NOW, last_turn_at=last, has_pending_learnings=True, unanswered_initiative=False
    )


def test_never_interrupts_an_active_conversation() -> None:
    assert not should_reach_out(
        now=_NOW,
        last_turn_at=_NOW - timedelta(minutes=30),
        has_pending_learnings=True,
        unanswered_initiative=False,
    )


def test_never_stacks_unanswered_initiatives() -> None:
    last = _NOW - timedelta(days=3)
    assert not should_reach_out(
        now=_NOW, last_turn_at=last, has_pending_learnings=True, unanswered_initiative=True
    )


def test_stays_silent_with_nothing_to_tell() -> None:
    last = _NOW - timedelta(days=3)
    assert not should_reach_out(
        now=_NOW, last_turn_at=last, has_pending_learnings=False, unanswered_initiative=False
    )


def test_first_conversation_is_not_initiated_by_the_cortex() -> None:
    # Sin historia previa no hay relación que retomar: el primer contacto es
    # del owner (onboarding), no del córtex.
    assert not should_reach_out(
        now=_NOW, last_turn_at=None, has_pending_learnings=True, unanswered_initiative=False
    )


def test_message_mentions_gap_and_learning() -> None:
    msg = compose_initiative_message(
        (_learning(),), now=_NOW, last_turn_at=_NOW - timedelta(days=2), language="es"
    )
    assert msg is not None
    assert "pgvector 0.8" in msg
    assert "2 día" in msg
    assert "novedades de HNSW" in msg


def test_message_without_learnings_is_none() -> None:
    assert (
        compose_initiative_message(
            (), now=_NOW, last_turn_at=_NOW - timedelta(days=2), language="es"
        )
        is None
    )
