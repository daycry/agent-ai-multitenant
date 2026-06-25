"""Unit test — prod-06 task_prod06_beat_01.

`_parse_cron` must fail LOUDLY on a malformed expression instead of silently
degrading every bad cron to a global daily 04:00:

  - dev/test: log an ERROR naming the offending env var and fall back to THIS
    entry's documented default (e.g. the 10-minute escalation sweep stays
    10-minute, it does not become daily);
  - staging/prod: RAISE to reject beat boot, so a typo cannot quietly turn a
    10-minute sweep into a daily one in production.
"""

from __future__ import annotations

import logging

import pytest
from celery.schedules import crontab
from workers.beat_schedule import _parse_cron, build_beat_schedule
from workers.config import Settings

pytestmark = pytest.mark.unit


def test_valid_cron_parses() -> None:
    result = _parse_cron("0 4 * * *", env_var="WORKERS_PRICE_SYNC_CRON", default="0 4 * * *")
    assert result == crontab(minute="0", hour="4")


def test_malformed_falls_back_to_this_entrys_default_in_dev(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A 4-field typo of the 10-minute escalation sweep.
    with caplog.at_level(logging.ERROR):
        result = _parse_cron(
            "*/10 * * *",
            env_var="WORKERS_HUMAN_ESCALATION_CRON",
            default="*/10 * * * *",
            environment="dev",
        )
    # Fell back to THIS entry's documented default (every 10 min), NOT 04:00.
    assert result == crontab(minute="*/10")
    assert result != crontab(minute="0", hour="4")
    # Logged loudly, naming the offending env var.
    assert any("WORKERS_HUMAN_ESCALATION_CRON" in r.message for r in caplog.records)
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


def test_out_of_range_field_is_treated_as_malformed() -> None:
    # 5 fields but minute 99 is out of range → malformed → per-entry fallback.
    result = _parse_cron(
        "99 4 * * *",
        env_var="WORKERS_PRICE_SYNC_CRON",
        default="0 4 * * *",
        environment="dev",
    )
    assert result == crontab(minute="0", hour="4")


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_malformed_rejects_beat_boot_in_strict_env(env: str) -> None:
    with pytest.raises(ValueError, match="WORKERS_BACKUP_CRON"):
        _parse_cron(
            "garbage",
            env_var="WORKERS_BACKUP_CRON",
            default="0 3 * * *",
            environment=env,
        )


def test_build_beat_schedule_rejects_boot_on_bad_cron_in_prod() -> None:
    cfg = Settings(
        price_sync_cron="not a cron",
        environment="prod",
        database_url="postgresql+asyncpg://app:s3cret@db:5432/agentic",
    )
    with pytest.raises(ValueError):
        build_beat_schedule(cfg)


def test_build_beat_schedule_survives_bad_cron_in_dev(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cfg = Settings(human_escalation_cron="*/10 * * *", environment="dev")
    with caplog.at_level(logging.ERROR):
        sched = build_beat_schedule(cfg)
    # Boot survived; the entry fell back to the 10-minute default, not 04:00.
    from workers.beat_schedule import HUMAN_ESCALATION_BEAT_ENTRY

    assert sched[HUMAN_ESCALATION_BEAT_ENTRY]["schedule"] == crontab(minute="*/10")
