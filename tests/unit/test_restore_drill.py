"""Restore-drill mensual (ADR 0126) — núcleo puro con fakes.

Un backup no probado no existe: el drill toma el último bundle, corre la
verificación estructural EXISTENTE (pg_restore --list / tar -tf / checksums)
y después lo restaura DE VERDAD a una base efímera contando filas clave.
Notifica SIEMPRE el resultado — un drill fallido en silencio sería peor que
no tener drill.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from workers.restore_drill import DrillOutcome, _run_drill

pytestmark = pytest.mark.unit


class _Notifier:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def publish(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def _run(*, verify_ok=True, restore=None):
    notifier = _Notifier()

    async def verifier(bundle: str) -> tuple[bool, str]:
        return (True, "3/3 checks ok") if verify_ok else (False, "checksum mismatch")

    async def default_restore(bundle: str) -> dict[str, int]:
        return {"organizations": 3, "plans": 12, "executions": 40}

    outcome = asyncio.run(
        _run_drill(
            bundle="/backups/2026-07-19",
            verifier=verifier,
            restorer=restore or default_restore,
            notifier=notifier,
        )
    )
    return notifier, outcome


def test_successful_drill_notifies_ok_with_counts() -> None:
    notifier, outcome = _run()
    assert outcome == DrillOutcome.OK
    assert len(notifier.events) == 1
    event = notifier.events[0]
    assert event["event_type"] == "restore_drill_result"
    assert event["tenant_id"] is None
    assert event["context"]["ok"] is True
    assert "plans=12" in event["context"]["detail"]


def test_failed_verification_notifies_fail_and_skips_restore() -> None:
    called = {"restore": False}

    async def restorer(bundle: str) -> dict[str, int]:
        called["restore"] = True
        return {}

    notifier, outcome = _run(verify_ok=False, restore=restorer)
    assert outcome == DrillOutcome.FAILED
    assert called["restore"] is False  # un bundle corrupto no se restaura
    assert notifier.events[0]["context"]["ok"] is False
    assert "checksum" in notifier.events[0]["context"]["detail"]


def test_restore_crash_notifies_fail() -> None:
    async def restorer(bundle: str) -> dict[str, int]:
        raise RuntimeError("pg_restore exploded")

    notifier, outcome = _run(restore=restorer)
    assert outcome == DrillOutcome.FAILED
    assert notifier.events[0]["context"]["ok"] is False
    assert "pg_restore exploded" in notifier.events[0]["context"]["detail"]


def test_empty_restore_counts_are_a_failure() -> None:
    async def restorer(bundle: str) -> dict[str, int]:
        return {"organizations": 0, "plans": 0, "executions": 0}

    notifier, outcome = _run(restore=restorer)
    assert outcome == DrillOutcome.FAILED  # restaurar "nada" no es un éxito
    assert notifier.events[0]["context"]["ok"] is False
