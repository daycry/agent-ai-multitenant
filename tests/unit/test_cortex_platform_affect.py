"""C2 (investigación córtex 2026-07-11): el pulso de plataforma mueve el afecto.

El córtex era CIEGO al sistema que su owner opera: su afecto solo se movía por
el texto del chat — un plan que fracasa o una tanda de runs con éxito no le
producían nada. `pulse_appraisal` es el mapeo DETERMINISTA (sin LLM, puro)
pulso-de-plataforma → delta PAD + razón + drive, que el beat
`workers.cortex_platform_pulse` aplica por el mismo motor que el appraisal
conversacional.
"""

from __future__ import annotations

import pytest
from api_server.cortex.platform_affect import PlatformPulse, pulse_appraisal

pytestmark = pytest.mark.unit


def test_quiet_platform_is_a_noop() -> None:
    delta, reason, drive, amount = pulse_appraisal(PlatformPulse(0, 0, 0, 0))
    assert (delta.valence, delta.arousal, delta.dominance, delta.intensity) == (0, 0, 0, 0)
    assert reason is None
    assert drive is None
    assert amount == 0.0


def test_failures_hurt_valence_and_raise_arousal() -> None:
    delta, reason, _, _ = pulse_appraisal(
        PlatformPulse(executions_done=0, executions_failed=3, plans_blocked=1, plans_completed=0)
    )
    assert delta.valence < 0
    assert delta.arousal > 0
    assert reason is not None and "3" in reason


def test_successes_lift_valence_and_feed_competence() -> None:
    delta, reason, drive, amount = pulse_appraisal(
        PlatformPulse(executions_done=4, executions_failed=0, plans_blocked=0, plans_completed=1)
    )
    assert delta.valence > 0
    assert drive == "competence"
    assert amount > 0
    assert reason is not None


def test_deltas_are_bounded_even_on_extreme_days() -> None:
    delta, _, _, amount = pulse_appraisal(
        PlatformPulse(
            executions_done=500, executions_failed=500, plans_blocked=50, plans_completed=50
        )
    )
    assert -0.35 <= delta.valence <= 0.35
    assert 0 <= delta.arousal <= 0.3
    assert 0 <= amount <= 0.3


def test_reason_is_spanish_and_mentions_the_pulse() -> None:
    _, reason, _, _ = pulse_appraisal(
        PlatformPulse(executions_done=2, executions_failed=1, plans_blocked=0, plans_completed=0)
    )
    assert reason is not None
    assert "plataforma" in reason.lower()
