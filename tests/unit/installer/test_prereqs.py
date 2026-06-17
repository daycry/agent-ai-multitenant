"""Unit tests for the new ports prerequisite (Plan prod-01 task_17 / deploy-1).

After ADR 0061 the Caddy reverse proxy is the only service that publishes host
ports (80/443), so the installer must verify they are free before it starts the
stack. Driven with a fake :class:`HostProbe` — no real sockets.
"""

from __future__ import annotations

import pytest
from installer_backend.prereqs import (
    PREREQ_CHECKS,
    REQUIRED_FREE_PORTS,
    HostReadings,
    PrereqThresholds,
    check_ports,
)
from installer_backend.seams import PrereqStatus

pytestmark = pytest.mark.unit


def _readings(**overrides: object) -> HostReadings:
    base = {
        "docker_version": (24, 0),
        "compose_version": (2, 0),
        "total_ram_bytes": 16 * 1024**3,
        "free_disk_bytes": 100 * 1024**3,
        "gpu_present": False,
    }
    base.update(overrides)
    return HostReadings(**base)  # type: ignore[arg-type]


def test_required_free_ports_are_80_and_443() -> None:
    assert REQUIRED_FREE_PORTS == (80, 443)


def test_check_ports_ok_when_free() -> None:
    result = check_ports(_readings(ports_in_use=()), PrereqThresholds())
    assert result.status is PrereqStatus.OK
    assert result.blocking is False


def test_check_ports_blocks_when_443_in_use() -> None:
    result = check_ports(_readings(ports_in_use=(443,)), PrereqThresholds())
    assert result.status is PrereqStatus.FAIL
    assert result.blocking is True
    assert result.remediation  # actionable guidance is present
    assert "443" in result.detail


def test_check_ports_is_a_required_check_in_the_pipeline() -> None:
    assert check_ports in PREREQ_CHECKS
