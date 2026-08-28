"""Finalize step 9 — credentials ONCE + self-destruct (Plan 15 task_15_06).

Exercises the finalize state machine and its API routes with the host-touching
self-destruct MOCKED behind the :class:`installer_backend.seams.
InstallerLifecycle` seam (a recording fake) — no real container is stopped, no
``docker compose down`` runs. The real self-destruct binding (Phase B) is
exercised only by the plan's Tests Humanos.

Coverage (per the task contract):
  * the credentials + Vault unseal keys are returned EXACTLY ONCE — a second
    fetch is denied (410 Gone) / empty;
  * they are NOT written to disk nor logged in plaintext;
  * the self-destruct seam IS invoked after the one-time reveal;
  * an INCOMPLETE install does NOT reveal credentials nor self-destruct.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from installer_backend.finalize import (
    CredentialsAlreadyRevealedError,
    FinalizeService,
    InstallCredentials,
    InstallNotCompleteError,
    build_reveal,
)
from installer_backend.install import FakeStepExecutor, InstallStep
from installer_backend.main import (
    create_app,
    get_finalize_service,
    get_step_executor,
)
from installer_backend.seams import StubInstallerLifecycle

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _autoriza_la_simulacion(monkeypatch: pytest.MonkeyPatch) -> None:
    """El paso 9 que se ejercita aquí es el SIMULADO, y se declara como tal.

    Desde la auditoría del 2026-08-28 `/api/finalize/reveal` responde `501` con
    seams de simulación y sin `INSTALLER_ALLOW_SIMULATION`: era la pantalla que
    servía cinco unseal keys de Vault recién inventadas con el mismo contrato que
    el camino real, bajo el aviso de que no había forma de recuperarlas. La
    ceremonia de «una sola vez» que se comprueba aquí no cambia; lo que cambia es
    que correrla exige pedirla. Sin esta línea, los tests de ruta pasarían a
    verde **vacíos** contra un 501.
    """

    monkeypatch.setenv("INSTALLER_ALLOW_SIMULATION", "1")


# A sentinel set of secret values we assert never appear in disk/logs.
_ADMIN_PW = "s3cr3t-admin-pw-never-leaks"
_ROOT_TOKEN = "s.r00t-token-never-leaks"
_UNSEAL_KEYS = (
    "unseal-key-share-1-never-leaks",
    "unseal-key-share-2-never-leaks",
    "unseal-key-share-3-never-leaks",
)
_ALL_SECRETS = (_ADMIN_PW, _ROOT_TOKEN, *_UNSEAL_KEYS)


def _credentials() -> InstallCredentials:
    return InstallCredentials(
        admin_username="admin@acme.com",
        admin_password=_ADMIN_PW,
        vault_root_token=_ROOT_TOKEN,
        vault_unseal_keys=_UNSEAL_KEYS,
    )


# ---------------------------------------------------------------------------
# Pure FinalizeService state machine (no route).
# ---------------------------------------------------------------------------
def test_reveal_returns_credentials_and_unseal_keys_once() -> None:
    lifecycle = StubInstallerLifecycle()
    service = FinalizeService(lifecycle=lifecycle)
    service.arm(_credentials())

    payload = service.reveal()

    # All three credential lines + every unseal-key share are present.
    secrets_revealed = {f.secret for f in payload.credentials}
    assert _ADMIN_PW in secrets_revealed
    assert _ROOT_TOKEN in secrets_revealed
    assert tuple(payload.unseal_keys) == _UNSEAL_KEYS
    # The "save these now, shown once" warning is carried bilingually.
    assert payload.warning_es and payload.warning_en


def test_second_reveal_is_denied_and_payload_is_gone() -> None:
    service = FinalizeService(lifecycle=StubInstallerLifecycle())
    service.arm(_credentials())

    service.reveal()  # first (only) reveal

    assert service.revealed is True
    assert service.can_reveal is False
    # A second fetch is refused — the one-time payload is gone (no recovery).
    with pytest.raises(CredentialsAlreadyRevealedError):
        service.reveal()


def test_self_destruct_invoked_after_the_reveal() -> None:
    lifecycle = StubInstallerLifecycle()
    service = FinalizeService(lifecycle=lifecycle)
    service.arm(_credentials())

    # Not self-destructed until the reveal happens.
    assert lifecycle.destroyed is False
    service.reveal()
    # Self-destruct fired exactly after the one-time reveal.
    assert lifecycle.destroyed is True


def test_incomplete_install_does_not_reveal_or_self_destruct() -> None:
    lifecycle = StubInstallerLifecycle()
    service = FinalizeService(lifecycle=lifecycle)  # never armed

    assert service.installed is False
    assert service.can_reveal is False
    with pytest.raises(InstallNotCompleteError):
        service.reveal()
    # No reveal happened, so the installer did NOT self-destruct.
    assert lifecycle.destroyed is False
    assert service.revealed is False


def test_in_memory_copy_is_dropped_after_reveal() -> None:
    """After the reveal the service holds no credentials in memory."""

    service = FinalizeService(lifecycle=StubInstallerLifecycle())
    service.arm(_credentials())
    service.reveal()
    # Private store is cleared (defense-in-depth for the no-recovery guarantee).
    assert service._credentials is None  # — asserting the drop


def test_credentials_repr_is_redacted() -> None:
    """A stray log/traceback of the credentials must not leak the values."""

    creds = _credentials()
    assert _ADMIN_PW not in repr(creds)
    assert _ROOT_TOKEN not in repr(creds)
    assert _ADMIN_PW not in str(creds)
    assert "redacted" in repr(creds)


def test_build_reveal_carries_every_secret_field() -> None:
    payload = build_reveal(_credentials())
    keys = {f.key for f in payload.credentials}
    assert {"admin_username", "admin_password", "vault_root_token"} <= keys


# ---------------------------------------------------------------------------
# /api/finalize/* routes — with an INJECTED (mocked) lifecycle + finalize svc.
# ---------------------------------------------------------------------------
def _client_with_finalize(service: FinalizeService) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_finalize_service] = lambda: service
    return TestClient(app)


def _client_full(service: FinalizeService, executor: FakeStepExecutor) -> TestClient:
    """A client sharing one finalize service across the install + finalize routes."""

    app = create_app()
    app.dependency_overrides[get_finalize_service] = lambda: service
    app.dependency_overrides[get_step_executor] = lambda: executor
    return TestClient(app)


def test_reveal_route_returns_payload_once_then_410() -> None:
    lifecycle = StubInstallerLifecycle()
    service = FinalizeService(lifecycle=lifecycle)
    service.arm(_credentials())
    client = _client_with_finalize(service)

    # First reveal: 200 with the full secret payload.
    first = client.post("/api/finalize/reveal")
    assert first.status_code == 200
    body = first.json()
    revealed = {c["secret"] for c in body["credentials"]}
    assert _ADMIN_PW in revealed
    assert _ROOT_TOKEN in revealed
    assert body["unseal_keys"] == list(_UNSEAL_KEYS)

    # Self-destruct fired after the reveal.
    assert lifecycle.destroyed is True

    # Second reveal: 410 Gone and NO secret in the body.
    second = client.post("/api/finalize/reveal")
    assert second.status_code == 410
    for secret in _ALL_SECRETS:
        assert secret not in second.text


def test_reveal_route_on_incomplete_install_is_409_and_no_self_destruct() -> None:
    lifecycle = StubInstallerLifecycle()
    service = FinalizeService(lifecycle=lifecycle)  # not armed
    client = _client_with_finalize(service)

    resp = client.post("/api/finalize/reveal")
    assert resp.status_code == 409
    # No secret leaked and the installer did NOT self-destruct.
    for secret in _ALL_SECRETS:
        assert secret not in resp.text
    assert lifecycle.destroyed is False


def test_status_route_reflects_the_finalize_gate() -> None:
    service = FinalizeService(lifecycle=StubInstallerLifecycle())
    client = _client_with_finalize(service)

    # `simulated` entra en el contrato el 2026-08-28: `installed: True` sobre un
    # FakeStepExecutor significa «la simulación terminó», no «la plataforma está
    # instalada», y la UI no podía distinguir las dos cosas. Se comprueba con
    # igualdad exacta a propósito: un campo nuevo en esta respuesta tiene que
    # romper aquí y obligar a decidir qué pinta la pantalla con él.
    # Before install: not installed, cannot reveal.
    pre = client.get("/api/finalize/status").json()
    assert pre == {
        "installed": False,
        "can_reveal": False,
        "revealed": False,
        "simulated": True,
    }

    service.arm(_credentials())
    armed = client.get("/api/finalize/status").json()
    assert armed == {
        "installed": True,
        "can_reveal": True,
        "revealed": False,
        "simulated": True,
    }

    client.post("/api/finalize/reveal")
    after = client.get("/api/finalize/status").json()
    assert after == {
        "installed": True,
        "can_reveal": False,
        "revealed": True,
        "simulated": True,
    }


def test_successful_install_arms_finalize_then_reveals_once() -> None:
    """End-to-end over the routes: a completed install stream arms the reveal."""

    lifecycle = StubInstallerLifecycle()
    service = FinalizeService(lifecycle=lifecycle)
    client = _client_full(service, FakeStepExecutor())

    # Not armed until the install completes.
    assert client.get("/api/finalize/status").json()["can_reveal"] is False

    # Run the (mocked) install pipeline to completion.
    stream = client.post(
        "/api/install/stream",
        json={"config": {"tenant": {"admin_email": "admin@acme.com"}}},
    )
    assert stream.status_code == 200
    # Now the reveal is armed.
    assert client.get("/api/finalize/status").json()["can_reveal"] is True

    reveal = client.post("/api/finalize/reveal")
    assert reveal.status_code == 200
    # The admin username was derived from the install config.
    usernames = {c["secret"] for c in reveal.json()["credentials"] if c["key"] == "admin_username"}
    assert usernames == {"admin@acme.com"}
    assert lifecycle.destroyed is True


def test_failed_install_does_not_arm_finalize() -> None:
    """A halted install never arms the reveal — incomplete installs reveal nothing."""

    lifecycle = StubInstallerLifecycle()
    service = FinalizeService(lifecycle=lifecycle)
    executor = FakeStepExecutor(fail_at=InstallStep.START_STACK)
    client = _client_full(service, executor)

    stream = client.post("/api/install/stream", json={"config": {}})
    assert stream.status_code == 200
    # The pipeline halted, so the finalize service was NOT armed.
    assert client.get("/api/finalize/status").json() == {
        "installed": False,
        "can_reveal": False,
        "revealed": False,
        "simulated": True,
    }
    reveal = client.post("/api/finalize/reveal")
    assert reveal.status_code == 409
    assert lifecycle.destroyed is False


# ---------------------------------------------------------------------------
# Secrets never persisted in plaintext / never logged.
# ---------------------------------------------------------------------------
def test_secrets_not_written_to_disk(tmp_path, monkeypatch) -> None:
    """No file under the (sandboxed) cwd carries a secret after a reveal."""

    monkeypatch.chdir(tmp_path)
    service = FinalizeService(lifecycle=StubInstallerLifecycle())
    service.arm(_credentials())
    service.reveal()

    # The finalize flow writes nothing; assert the install dir stayed empty of
    # any plaintext secret.
    leaked = []
    for path in tmp_path.rglob("*"):
        if path.is_file():
            content = path.read_text(encoding="utf-8", errors="ignore")
            for secret in _ALL_SECRETS:
                if secret in content:
                    leaked.append((path, secret))
    assert leaked == []


def test_secrets_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    """A reveal must not emit any secret to the logs."""

    caplog.set_level(logging.DEBUG)
    lifecycle = StubInstallerLifecycle()
    service = FinalizeService(lifecycle=lifecycle)
    service.arm(_credentials())
    client = _client_with_finalize(service)

    client.post("/api/finalize/reveal")

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    for secret in _ALL_SECRETS:
        assert secret not in log_text
