"""Vigía de credenciales LLM (ADR 0122) — núcleo puro con fakes.

Dolor real que resuelve: la credencial de claude_sdk caducó dos veces y los
runs murieron en silencio (blocked) hasta la inspección manual. El vigía
sondea cada proveedor ACTIVO con el probe existente (liveness) y notifica en
la TRANSICIÓN sana→caída (y recovery), con re-aviso si la caída persiste —
nunca spam en cada pasada. Reusa el event_type `provider_credential_invalid`
del registry del dispatcher para el fallo y añade `provider_recovered`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from workers.provider_watchdog import (
    REMIND_AFTER_S,
    ProviderRow,
    _watch_providers,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


class _Notifier:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def publish(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class _State:
    """Fake del estado persistido (Redis en producción)."""

    def __init__(self, initial: dict[str, dict[str, Any]] | None = None) -> None:
        self.data = initial or {}

    async def get(self, provider_id: str) -> dict[str, Any] | None:
        return self.data.get(provider_id)

    async def set(self, provider_id: str, value: dict[str, Any]) -> None:
        self.data[provider_id] = value


def _provider(**over: Any) -> ProviderRow:
    base: dict[str, Any] = {
        "provider_id": str(uuid4()),
        "name": "Claude principal",
        "kind": "claude_sdk",
    }
    base.update(over)
    return ProviderRow(**base)


def _run(providers, prober, state, now=_NOW):
    notifier = _Notifier()
    result = asyncio.run(
        _watch_providers(
            providers=providers, prober=prober, notifier=notifier, state=state, now=now
        )
    )
    return notifier, result


async def _ok_prober(_row: ProviderRow) -> tuple[bool, str]:
    return True, "ok"


async def _fail_prober(_row: ProviderRow) -> tuple[bool, str]:
    return False, "OAuth token expired"


def test_transition_to_fail_notifies_once() -> None:
    row = _provider()
    state = _State()
    notifier, result = _run([row], _fail_prober, state)
    assert result == {"checked": 1, "unhealthy": 1, "notified": 1}
    assert len(notifier.events) == 1
    event = notifier.events[0]
    assert event["event_type"] == "provider_credential_invalid"
    assert event["tenant_id"] is None  # señal de plataforma, como la rotación
    assert event["context"]["provider_name"] == "Claude principal"
    assert "expired" in event["context"]["detail"]


def test_persistent_fail_renotifies_only_after_remind_window() -> None:
    row = _provider()
    recently = (_NOW - timedelta(seconds=60)).isoformat()
    state = _State({row.provider_id: {"status": "fail", "last_notified_at": recently}})
    notifier, _ = _run([row], _fail_prober, state)
    assert notifier.events == []  # dentro de la ventana: silencio

    long_ago = (_NOW - timedelta(seconds=REMIND_AFTER_S + 1)).isoformat()
    state = _State({row.provider_id: {"status": "fail", "last_notified_at": long_ago}})
    notifier, _ = _run([row], _fail_prober, state)
    assert len(notifier.events) == 1  # recordatorio pasado el umbral


def test_recovery_notifies_and_healthy_stays_silent() -> None:
    row = _provider()
    state = _State({row.provider_id: {"status": "fail", "last_notified_at": _NOW.isoformat()}})
    notifier, _ = _run([row], _ok_prober, state)
    assert [e["event_type"] for e in notifier.events] == ["provider_recovered"]

    # Sano → sano: ni un evento.
    notifier, _ = _run([row], _ok_prober, state)
    assert notifier.events == []


def test_prober_crash_counts_as_unhealthy_without_breaking_the_loop() -> None:
    bad = _provider(name="Roto")
    good = _provider(name="Sano")

    async def prober(row: ProviderRow) -> tuple[bool, str]:
        if row.name == "Roto":
            raise RuntimeError("vault unreachable")
        return True, "ok"

    notifier, result = _run([bad, good], prober, _State())
    assert result["checked"] == 2
    assert result["unhealthy"] == 1
    assert len(notifier.events) == 1
    assert "vault unreachable" in notifier.events[0]["context"]["detail"]
