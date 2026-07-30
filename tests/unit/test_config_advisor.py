"""Asesor de configuración (ADR 0125) — núcleo puro con fakes.

Del leaderboard (ADR 0121) a la acción CON gate humano: si un agente rinde
mal con su modelo actual (n suficiente, éxito bajo) y sus propios datos
históricos muestran otra combinación claramente mejor, se emite una
PROPUESTA como notificación accionable — jamás se auto-aplica nada.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from workers.config_advisor import ComboStat, _advise, _proposals_for

pytestmark = pytest.mark.unit


def _stat(**over: Any) -> ComboStat:
    base: dict[str, Any] = {
        "tenant_id": "t1",
        "agent_id": "ag-1",
        "agent_name": "Backend Dev",
        "model": "modelo-flojo",
        "runs": 12,
        "success_rate": 0.3,
        "avg_cost_usd": 0.5,
    }
    base.update(over)
    return ComboStat(**base)


class _Notifier:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def publish(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def test_a_clearly_better_history_produces_a_proposal() -> None:
    current = _stat()  # 30% de éxito con modelo-flojo
    better = _stat(model="modelo-bueno", success_rate=0.9, avg_cost_usd=0.4)
    proposals = _proposals_for([current, better])
    assert len(proposals) == 1
    p = proposals[0]
    assert p["agent_id"] == "ag-1"
    assert p["from_model"] == "modelo-flojo"
    assert p["to_model"] == "modelo-bueno"


def test_no_proposal_without_enough_samples_or_without_clear_winner() -> None:
    # n insuficiente en el candidato mejor.
    current = _stat()
    thin = _stat(model="modelo-bueno", success_rate=0.95, runs=2)
    assert _proposals_for([current, thin]) == []
    # El "mejor" no supera el margen mínimo de mejora.
    similar = _stat(model="modelo-otro", success_rate=0.35)
    assert _proposals_for([_stat(), similar]) == []
    # El actual ya rinde bien: nada que proponer.
    fine = _stat(success_rate=0.85)
    great = _stat(model="modelo-x", success_rate=0.95)
    assert _proposals_for([fine, great]) == []


def test_advise_emits_one_notification_per_proposal_and_never_applies() -> None:
    notifier = _Notifier()
    stats = [
        _stat(),
        _stat(model="modelo-bueno", success_rate=0.9, avg_cost_usd=0.4),
    ]
    result = asyncio.run(_advise(stats=stats, notifier=notifier))
    assert result == {"proposals": 1}
    event = notifier.events[0]
    assert event["event_type"] == "config_proposal"
    assert event["tenant_id"] == "t1"
    ctx = event["context"]
    assert ctx["agent_name"] == "Backend Dev"
    assert ctx["from_model"] == "modelo-flojo"
    assert ctx["to_model"] == "modelo-bueno"
    # La propuesta lleva los DATOS para decidir — no hay ningún "apply".
    assert "success_rate" in ctx["evidence"]
