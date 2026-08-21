"""Skip-guards + fixtures for the heavy install e2e (Plan prod-01 task_20).

These tests provision a REAL stack with Docker, so they run ONLY when explicitly
enabled. They are gated TWICE (``E2E_INSTALL=1`` AND a responding Docker daemon)
so a normal CI / Windows run SKIPS them — green — with an HONEST reason.

IMPORTANT: a skip here does NOT acredit deploy-1/2/3. task_prod01_20 is only
marked done after a real GREEN run on a Linux runner with ``E2E_INSTALL=1``
(nightly, coordination prod-02) — never by the CI skip.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

#: Repo root (this file is tests/e2e/conftest.py).
REPO_ROOT = Path(__file__).resolve().parents[2]
#: The install profile the e2e drives. Minimal = 1 worker, Ollama only.
E2E_PROFILE = REPO_ROOT / "scripts" / "install-profiles" / "minimal.yaml"


@pytest.fixture(scope="session")
def e2e_enabled() -> None:
    if os.environ.get("E2E_INSTALL") != "1":
        pytest.skip(
            "E2E_INSTALL!=1: e2e de instalación NO ejecutado. Este skip NO "
            "acredita deploy-1/2/3 — requiere un runner Linux con Docker y "
            "E2E_INSTALL=1 (nightly)."
        )


@pytest.fixture(scope="session")
def docker_available(e2e_enabled: None) -> None:  # - gate dependency
    if shutil.which("docker") is None:
        pytest.skip("docker no está en PATH: e2e de instalación no ejecutable.")
    try:
        proc = subprocess.run(["docker", "info"], capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("el daemon Docker no responde: e2e de instalación no ejecutable.")
    if proc.returncode != 0:
        pytest.skip("el daemon Docker no responde (docker info != 0).")


@pytest.fixture(scope="session")
def installed_stack(docker_available: None) -> Iterator[dict[str, str]]:
    """Install the stack via scripts/install.sh; yield the parsed reveal; uninstall.

    Real, host-only: runs the REAL installer (no --dry-run) against the published
    images, captures the one-time credential reveal from stdout, and at teardown
    runs scripts/uninstall.sh with --purge-data and asserts the data root is gone.
    """

    install_sh = REPO_ROOT / "scripts" / "install.sh"
    proc = subprocess.run(
        ["bash", str(install_sh), "--config", str(E2E_PROFILE)],
        capture_output=True,
        text=True,
        timeout=1500,
        check=False,
    )
    assert proc.returncode == 0, (
        f"install.sh falló (rc={proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
    )
    stdout = proc.stdout
    # The install must be REAL — never a simulation.
    for forbidden in ("SIMULACIÓN", "stub-", "FALSA"):
        assert forbidden not in stdout, f"el install parece una simulación: contiene {forbidden!r}"

    creds = _parse_reveal(stdout)
    try:
        yield creds
    finally:
        uninstall_sh = REPO_ROOT / "scripts" / "uninstall.sh"
        un = subprocess.run(
            [
                "bash",
                str(uninstall_sh),
                "--confirm-name",
                "agentic-platform",
                "--yes",
                "--purge-data",
            ],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        # Verify the purge actually happened (deploy-3): uninstall succeeded AND
        # the data root no longer exists.
        assert un.returncode == 0, (
            f"uninstall.sh falló (rc={un.returncode}):\n{un.stdout}\n{un.stderr}"
        )
        data_root = _profile_data_root()
        assert not Path(data_root).exists(), f"la purga no eliminó la raíz de datos {data_root}"


def _profile_data_root() -> str:
    """The ``storage.data_root`` declared in the e2e install profile."""

    import yaml

    doc = yaml.safe_load(E2E_PROFILE.read_text(encoding="utf-8"))
    return str(doc["storage"]["data_root"])


def _parse_reveal(stdout: str) -> dict[str, str]:
    """Extract admin username/password from the one-time reveal block (best-effort)."""

    creds: dict[str, str] = {}
    for line in stdout.splitlines():
        low = line.lower()
        if "admin" in low and "@" in line and "password" not in low:
            creds.setdefault("admin_username", line.split()[-1].strip())
        if "password" in low:
            creds.setdefault("admin_password", line.split()[-1].strip())
    return creds
