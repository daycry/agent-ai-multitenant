"""Unit tests for the event → notification registry + quiet-hours logic.

These exercise the *pure* parts of ``notification_dispatcher.event_mapping``
(no DB / no Celery): the data-driven registry is internally consistent, and
the quiet-hours window math (including wrap-around + the defer clamp) is
correct. The DB-backed fan-out / opt-out / tenant-isolation behaviour is
covered by ``tests/integration/test_event_mapping.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from notification_dispatcher.event_mapping import (
    EVENT_REGISTRY,
    NotificationLane,
    _Preference,
    lane_queue,
    lookup_event,
    quiet_hours_defer_until,
    registry_event_types,
)
from notification_dispatcher.templates import BUILTIN_TEMPLATES

pytestmark = pytest.mark.unit


def test_every_registry_event_has_builtin_templates_in_both_locales() -> None:
    """Single source of truth: every event the registry dispatches must
    have a builtin template fallback in BOTH es + en, so a render can never
    fail for a catalogued event with no tenant override."""
    for event_type, spec in EVENT_REGISTRY.items():
        assert spec.notification_event_type == event_type
        for locale in ("es", "en"):
            assert (
                event_type,
                locale,
            ) in BUILTIN_TEMPLATES, f"registry event {event_type!r} missing {locale} builtin"


def test_lookup_unknown_event_returns_none() -> None:
    assert lookup_event("definitely_not_an_event") is None
    assert "task_blocked" in registry_event_types()


def test_priority_events_use_priority_lane() -> None:
    """Time-sensitive events ride the priority lane (so a backlog of
    ordinary sends never delays an escalation / budget alert)."""
    assert EVENT_REGISTRY["budget_alert"].lane is NotificationLane.PRIORITY
    assert EVENT_REGISTRY["human_validation_needed"].lane is NotificationLane.PRIORITY
    assert EVENT_REGISTRY["plan_approved"].lane is NotificationLane.DEFAULT


class _Settings:
    """Minimal stand-in carrying just the two queue names lane_queue reads."""

    default_queue = "notifications.default"
    priority_queue = "notifications.priority"


def test_lane_queue_resolves_to_tunable_queue_names() -> None:
    settings = _Settings()
    assert lane_queue(NotificationLane.DEFAULT, settings) == "notifications.default"  # type: ignore[arg-type]
    assert lane_queue(NotificationLane.PRIORITY, settings) == "notifications.priority"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Quiet-hours window math.
# ---------------------------------------------------------------------------
_MAX_DEFER = 24 * 3600


def _pref(start: int | None, end: int | None, tz: str | None = "UTC") -> _Preference:
    return _Preference(
        scope="user",
        enabled=True,
        quiet_hours_start=start,
        quiet_hours_end=end,
        quiet_hours_tz=tz,
    )


def test_no_window_never_defers() -> None:
    now = datetime(2026, 5, 30, 23, 0, tzinfo=UTC)
    assert quiet_hours_defer_until(_pref(None, None), now=now, max_defer_s=_MAX_DEFER) is None
    # Half-configured window is treated as "no quiet hours".
    assert quiet_hours_defer_until(_pref(1320, None), now=now, max_defer_s=_MAX_DEFER) is None
    # Zero-width window never defers.
    assert quiet_hours_defer_until(_pref(600, 600), now=now, max_defer_s=_MAX_DEFER) is None


def test_inside_wraparound_window_defers_to_end() -> None:
    # 22:00 → 07:00; now 23:30 → defer to 07:00 next day.
    now = datetime(2026, 5, 30, 23, 30, tzinfo=UTC)
    eta = quiet_hours_defer_until(_pref(1320, 420), now=now, max_defer_s=_MAX_DEFER)
    assert eta is not None
    assert eta.hour == 7 and eta.minute == 0
    assert eta.date() == datetime(2026, 5, 31, tzinfo=UTC).date()


def test_inside_window_after_midnight_defers_to_end_same_day() -> None:
    # 22:00 → 07:00; now 02:00 → still in the window → defer to 07:00 today.
    now = datetime(2026, 5, 31, 2, 0, tzinfo=UTC)
    eta = quiet_hours_defer_until(_pref(1320, 420), now=now, max_defer_s=_MAX_DEFER)
    assert eta is not None
    assert eta.hour == 7
    assert eta.date() == now.date()


def test_outside_window_does_not_defer() -> None:
    now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
    assert quiet_hours_defer_until(_pref(1320, 420), now=now, max_defer_s=_MAX_DEFER) is None


def test_same_day_window_defers_inside() -> None:
    # 09:00 → 17:00 (no wrap); now 10:00 → defer to 17:00.
    now = datetime(2026, 5, 30, 10, 0, tzinfo=UTC)
    eta = quiet_hours_defer_until(_pref(540, 1020), now=now, max_defer_s=_MAX_DEFER)
    assert eta is not None
    assert eta.hour == 17 and eta.date() == now.date()


def test_defer_is_clamped_to_max() -> None:
    # A tiny clamp forces the ETA to now+clamp even though the window end is
    # further out — a misconfigured window can never defer beyond the bound.
    from datetime import timedelta

    now = datetime(2026, 5, 30, 23, 0, tzinfo=UTC)
    eta = quiet_hours_defer_until(_pref(1320, 420), now=now, max_defer_s=60)
    assert eta is not None
    assert eta == now + timedelta(seconds=60)


def test_unknown_tz_falls_back_to_utc() -> None:
    now = datetime(2026, 5, 30, 23, 30, tzinfo=UTC)
    eta = quiet_hours_defer_until(
        _pref(1320, 420, tz="Not/AReal_Zone"), now=now, max_defer_s=_MAX_DEFER
    )
    # Falls back to UTC → behaves like the UTC window test above.
    assert eta is not None
    assert eta.hour == 7
