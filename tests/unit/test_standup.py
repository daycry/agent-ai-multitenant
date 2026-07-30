"""Standup diario del PM (ADR 0120) — núcleo puro con dependencias inyectadas.

El beat corre cada hora; por cada tenant con el standup habilitado cuya hora
configurada coincide con la hora actual, compone el resumen (SQL vía
`collector` inyectado), lo redacta con el LLM del tenant (fail-open: si el
LLM falla se envía la versión estructurada — el parte NUNCA se pierde por un
proveedor caído) y lo publica como evento `daily_standup` al pipeline de
notificaciones (inbox + canales del tenant). Estos tests cubren el núcleo con
fakes; el SQL real del collector se cubre en integración.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from workers.standup import (
    StandupSummary,
    TenantStandupConfig,
    _format_structured,
    _run_standup,
)

pytestmark = pytest.mark.unit


def _summary(**over: Any) -> StandupSummary:
    base: dict[str, Any] = {
        "tasks_done_yesterday": 3,
        "plans_closed_yesterday": 1,
        "tasks_in_progress": 2,
        "tasks_blocked": 1,
        "runs_waiting_human": 2,
        "cost_usd_yesterday": 1.2345,
    }
    base.update(over)
    return StandupSummary(**base)


class _Notifier:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def publish(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class _OkLLM:
    async def complete(self, messages: Any, **_: Any) -> Any:
        class R:
            content = "Buenos días: ayer 3 tareas hechas; 2 esperando validación."

        return R()

    async def aclose(self) -> None:
        pass


class _BrokenLLM:
    async def complete(self, messages: Any, **_: Any) -> Any:
        raise RuntimeError("provider down")

    async def aclose(self) -> None:
        pass


def _run(
    *,
    tenants: list[TenantStandupConfig],
    now: datetime,
    llm_factory: Any,
    collector: Any = None,
) -> _Notifier:
    notifier = _Notifier()

    async def default_collector(tenant_id: Any) -> StandupSummary:
        return _summary()

    asyncio.run(
        _run_standup(
            tenants=tenants,
            now=now,
            collector=collector or default_collector,
            llm_factory=llm_factory,
            notifier=notifier,
        )
    )
    return notifier


def test_format_structured_is_deterministic_and_complete() -> None:
    text = _format_structured(_summary(), date_label="2026-07-19")
    for needle in ("2026-07-19", "3", "1", "2", "$1.23"):
        assert needle in text
    assert "validación" in text.lower() or "humano" in text.lower()


def test_sends_only_to_tenants_whose_hour_matches() -> None:
    t_match = TenantStandupConfig(tenant_id=uuid4(), enabled=True, hour=8)
    t_other = TenantStandupConfig(tenant_id=uuid4(), enabled=True, hour=9)
    t_off = TenantStandupConfig(tenant_id=uuid4(), enabled=False, hour=8)
    notifier = _run(
        tenants=[t_match, t_other, t_off],
        now=datetime(2026, 7, 19, 8, 5, tzinfo=UTC),
        llm_factory=lambda _t: _OkLLM(),
    )
    assert len(notifier.events) == 1
    event = notifier.events[0]
    assert event["event_type"] == "daily_standup"
    assert event["tenant_id"] == str(t_match.tenant_id)
    assert "Buenos días" in event["context"]["standup_body"]


def test_llm_failure_falls_back_to_structured_body() -> None:
    tenant = TenantStandupConfig(tenant_id=uuid4(), enabled=True, hour=8)
    notifier = _run(
        tenants=[tenant],
        now=datetime(2026, 7, 19, 8, 0, tzinfo=UTC),
        llm_factory=lambda _t: _BrokenLLM(),
    )
    assert len(notifier.events) == 1
    body = notifier.events[0]["context"]["standup_body"]
    # Fail-open: versión estructurada, nunca un parte perdido.
    assert "$1.23" in body


def test_collector_failure_skips_that_tenant_but_not_the_rest() -> None:
    bad = TenantStandupConfig(tenant_id=uuid4(), enabled=True, hour=8)
    good = TenantStandupConfig(tenant_id=uuid4(), enabled=True, hour=8)

    async def collector(tenant_id: Any) -> StandupSummary:
        if tenant_id == bad.tenant_id:
            raise RuntimeError("db hiccup")
        return _summary()

    notifier = _run(
        tenants=[bad, good],
        now=datetime(2026, 7, 19, 8, 0, tzinfo=UTC),
        llm_factory=lambda _t: _OkLLM(),
        collector=collector,
    )
    assert [e["tenant_id"] for e in notifier.events] == [str(good.tenant_id)]
