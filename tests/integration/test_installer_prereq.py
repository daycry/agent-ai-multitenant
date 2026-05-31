"""Prerequisite validation — step 1 of the installer wizard (Plan 15 task_15_02).

Exercises the check LOGIC and the install gate with the host probe MOCKED: a
fake :class:`installer_backend.prereqs.HostProbe` returns scripted
:class:`HostReadings`, so no real Docker / RAM / disk / GPU probing happens.
The real probing is :class:`SystemHostProbe`, run only on a real machine in the
plan's Tests Humanos.

Coverage (per the task contract):
  * all prerequisites pass -> the gate is open, install can proceed;
  * Docker missing -> FAIL with the right remediation, gate closed;
  * RAM below the minimum -> FAIL, gate closed;
  * free disk below the minimum -> FAIL, gate closed;
  * GPU absent -> WARN (not FAIL), and the gate stays open;
  * thresholds are configurable: lowering the minimum flips a FAIL to OK;
  * the `/api/prereqs` route wires the RealPrereqChecker behind the seam and
    reports `can_proceed` / per-item status + remediation, no secrets logged.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest
from fastapi.testclient import TestClient
from installer_backend.main import create_app, get_prereq_checker
from installer_backend.prereqs import (
    BYTES_PER_GIB,
    DEFAULT_MIN_DISK_GIB,
    DEFAULT_MIN_RAM_GIB,
    MIN_COMPOSE_VERSION,
    MIN_DOCKER_VERSION,
    HostReadings,
    PrereqThresholds,
    RealPrereqChecker,
    check_disk,
    check_docker,
    check_gpu,
    check_ram,
)
from installer_backend.seams import PrereqStatus

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fake host probe — the seam tests inject instead of touching the real host.
# ---------------------------------------------------------------------------
@dataclass
class FakeHostProbe:
    """Returns a fixed :class:`HostReadings`. No I/O, no real host access."""

    readings: HostReadings

    def read(self) -> HostReadings:
        return self.readings


def _healthy_readings(**overrides: object) -> HostReadings:
    """A host that satisfies every required prerequisite, with a GPU."""

    base = HostReadings(
        docker_version=(27, 1),
        compose_version=(2, 29),
        total_ram_bytes=16 * BYTES_PER_GIB,
        free_disk_bytes=200 * BYTES_PER_GIB,
        gpu_present=True,
        gpu_name="NVIDIA RTX 4090",
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def _by_key(checker: RealPrereqChecker) -> dict[str, object]:
    return {r.key: r for r in checker.check_all()}


# ---------------------------------------------------------------------------
# All prerequisites pass -> proceed.
# ---------------------------------------------------------------------------
def test_all_pass_allows_proceeding() -> None:
    checker = RealPrereqChecker(probe=FakeHostProbe(_healthy_readings()))
    results = checker.check_all()

    # one result per check, all required ones OK.
    assert {r.key for r in results} == {"docker", "compose", "ram", "disk", "gpu"}
    assert all(r.status is PrereqStatus.OK for r in results if r.required)
    assert checker.can_proceed is True
    # No blocking result, and no remediation noise on the passing required ones.
    assert not any(r.blocking for r in results)
    assert all(r.remediation == "" for r in results if r.status is PrereqStatus.OK)


# ---------------------------------------------------------------------------
# Docker missing -> FAIL + remediation, blocks.
# ---------------------------------------------------------------------------
def test_docker_missing_fails_and_blocks() -> None:
    readings = _healthy_readings(docker_version=None)
    result = check_docker(readings, PrereqThresholds())
    assert result.status is PrereqStatus.FAIL
    assert result.blocking is True
    assert "Docker" in result.remediation and "install" in result.remediation

    checker = RealPrereqChecker(probe=FakeHostProbe(readings))
    assert checker.can_proceed is False


def test_docker_too_old_fails() -> None:
    readings = _healthy_readings(docker_version=(20, 10))
    result = check_docker(readings, PrereqThresholds())
    assert result.status is PrereqStatus.FAIL
    assert "Actualiza" in result.remediation


def test_compose_missing_fails_and_blocks() -> None:
    readings = _healthy_readings(compose_version=None)
    checker = RealPrereqChecker(probe=FakeHostProbe(readings))
    items = _by_key(checker)
    assert items["compose"].status is PrereqStatus.FAIL  # type: ignore[attr-defined]
    assert "docker compose" in items["compose"].remediation.lower()  # type: ignore[attr-defined]
    assert checker.can_proceed is False


# ---------------------------------------------------------------------------
# RAM below the minimum -> FAIL + remediation, blocks.
# ---------------------------------------------------------------------------
def test_low_ram_fails_and_blocks() -> None:
    readings = _healthy_readings(total_ram_bytes=4 * BYTES_PER_GIB)
    result = check_ram(readings, PrereqThresholds())
    assert result.status is PrereqStatus.FAIL
    assert result.blocking is True
    assert str(DEFAULT_MIN_RAM_GIB) in result.remediation

    checker = RealPrereqChecker(probe=FakeHostProbe(readings))
    assert checker.can_proceed is False


# ---------------------------------------------------------------------------
# Free disk below the minimum -> FAIL + remediation, blocks.
# ---------------------------------------------------------------------------
def test_low_disk_fails_and_blocks() -> None:
    readings = _healthy_readings(free_disk_bytes=10 * BYTES_PER_GIB)
    result = check_disk(readings, PrereqThresholds())
    assert result.status is PrereqStatus.FAIL
    assert result.blocking is True
    assert str(DEFAULT_MIN_DISK_GIB) in result.remediation

    checker = RealPrereqChecker(probe=FakeHostProbe(readings))
    assert checker.can_proceed is False


# ---------------------------------------------------------------------------
# GPU absent -> WARN, not FAIL, and does not block.
# ---------------------------------------------------------------------------
def test_gpu_absent_is_warn_not_fail() -> None:
    readings = _healthy_readings(gpu_present=False, gpu_name=None)
    result = check_gpu(readings, PrereqThresholds())
    assert result.status is PrereqStatus.WARN
    assert result.required is False
    assert result.blocking is False
    assert result.ok is True  # a WARN does not close the gate
    assert result.remediation  # actionable guidance present

    # The whole gate stays open with only the GPU missing.
    checker = RealPrereqChecker(probe=FakeHostProbe(readings))
    assert checker.can_proceed is True


def test_gpu_present_is_ok_with_name_detail() -> None:
    readings = _healthy_readings(gpu_present=True, gpu_name="NVIDIA L4")
    result = check_gpu(readings, PrereqThresholds())
    assert result.status is PrereqStatus.OK
    assert result.detail == "NVIDIA L4"


# ---------------------------------------------------------------------------
# Thresholds are configurable.
# ---------------------------------------------------------------------------
def test_thresholds_are_configurable_ram() -> None:
    readings = _healthy_readings(total_ram_bytes=4 * BYTES_PER_GIB)
    # Default minimum (8) -> FAIL.
    assert check_ram(readings, PrereqThresholds()).status is PrereqStatus.FAIL
    # Lower the minimum to 2 GiB -> OK. Same readings, different config.
    relaxed = PrereqThresholds(min_ram_gib=2)
    assert check_ram(readings, relaxed).status is PrereqStatus.OK


def test_thresholds_are_configurable_disk() -> None:
    readings = _healthy_readings(free_disk_bytes=10 * BYTES_PER_GIB)
    assert check_disk(readings, PrereqThresholds()).status is PrereqStatus.FAIL
    relaxed = PrereqThresholds(min_disk_gib=5)
    assert check_disk(readings, relaxed).status is PrereqStatus.OK


def test_threshold_defaults_come_from_named_constants() -> None:
    t = PrereqThresholds()
    assert t.min_ram_gib == DEFAULT_MIN_RAM_GIB
    assert t.min_disk_gib == DEFAULT_MIN_DISK_GIB
    assert t.min_docker_version == MIN_DOCKER_VERSION
    assert t.min_compose_version == MIN_COMPOSE_VERSION


def test_relaxed_thresholds_open_the_gate_for_a_small_host() -> None:
    # A small host: 4 GiB RAM, 10 GiB free, no GPU. Fails the defaults.
    readings = _healthy_readings(
        total_ram_bytes=4 * BYTES_PER_GIB,
        free_disk_bytes=10 * BYTES_PER_GIB,
        gpu_present=False,
        gpu_name=None,
    )
    strict = RealPrereqChecker(probe=FakeHostProbe(readings))
    assert strict.can_proceed is False

    relaxed = RealPrereqChecker(
        probe=FakeHostProbe(readings),
        thresholds=PrereqThresholds(min_ram_gib=2, min_disk_gib=5),
    )
    assert relaxed.can_proceed is True


# ---------------------------------------------------------------------------
# The /api/prereqs route, wired to the RealPrereqChecker behind the seam.
# ---------------------------------------------------------------------------
def _client_with(readings: HostReadings, thresholds: PrereqThresholds | None = None) -> TestClient:
    app = create_app()

    def checker() -> RealPrereqChecker:
        return RealPrereqChecker(
            probe=FakeHostProbe(readings),
            thresholds=thresholds or PrereqThresholds(),
        )

    app.dependency_overrides[get_prereq_checker] = checker
    return TestClient(app)


def test_route_reports_can_proceed_when_all_pass() -> None:
    client = _client_with(_healthy_readings())
    body = client.get("/api/prereqs").json()
    assert body["can_proceed"] is True
    assert body["all_required_ok"] is True
    statuses = {r["key"]: r["status"] for r in body["results"]}
    assert statuses == {
        "docker": "ok",
        "compose": "ok",
        "ram": "ok",
        "disk": "ok",
        "gpu": "ok",
    }


def test_route_blocks_and_surfaces_remediation_on_missing_docker() -> None:
    client = _client_with(_healthy_readings(docker_version=None))
    resp = client.get("/api/prereqs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["can_proceed"] is False
    docker = next(r for r in body["results"] if r["key"] == "docker")
    assert docker["status"] == "fail"
    assert docker["ok"] is False
    assert docker["remediation"]


def test_route_gpu_absent_warns_but_does_not_block() -> None:
    client = _client_with(_healthy_readings(gpu_present=False, gpu_name=None))
    body = client.get("/api/prereqs").json()
    assert body["can_proceed"] is True
    gpu = next(r for r in body["results"] if r["key"] == "gpu")
    assert gpu["status"] == "warn"
    assert gpu["required"] is False
    assert gpu["ok"] is True
