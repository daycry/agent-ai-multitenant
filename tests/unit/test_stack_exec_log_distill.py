"""P1-4 (investigación 2026-07-11): los logs de stack_exec se destilan.

El worker devolvía el tail crudo de 8000 chars — la traza útil (el assert que
falló, el fatal error) podía quedar CORTADA por el ruido posterior (stack
traces, resumen). `distill_stack_logs` antepone las líneas-señal de fallo
(error/exception/fail/fatal/assert, acotadas) al tail, de modo que el agente
siempre ve QUÉ falló aunque el tail sea puro ruido.
"""

from __future__ import annotations

import pytest
from workers.tasks.stack_exec_task import distill_stack_logs

pytestmark = pytest.mark.unit


def test_short_logs_pass_verbatim() -> None:
    logs = "PHPUnit 10.5\nOK (12 tests, 30 assertions)\n"
    assert distill_stack_logs(logs) == logs


def test_failure_signals_survive_a_noisy_tail() -> None:
    # El fallo real está al PRINCIPIO; luego 20k de ruido que se comería el tail.
    failure = "1) InvoiceTest::test_totals\nFailed asserting that 41 matches expected 42."
    logs = failure + "\n" + ("linea de ruido irrelevante\n" * 2000)
    out = distill_stack_logs(logs)
    assert "Failed asserting that 41 matches expected 42." in out
    assert len(out) < 12_000  # señales + tail acotados


def test_signal_block_is_labelled_and_bounded() -> None:
    logs = ("Fatal error: Uncaught TypeError en App.php:10\n" * 300) + ("x\n" * 5000)
    out = distill_stack_logs(logs)
    assert "señales de fallo" in out
    # El bloque de señales no crece sin límite aunque haya cientos de matches.
    assert len(out) < 12_000


def test_tail_only_when_no_signals() -> None:
    logs = "linea normal\n" * 3000
    out = distill_stack_logs(logs)
    assert "señales de fallo" not in out
    assert out == logs[-8000:]
