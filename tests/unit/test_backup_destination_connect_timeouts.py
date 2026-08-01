"""Los adaptadores de destino remoto tienen plazo de CONEXIÓN (task_prod13_02).

La mitad que ya estaba: las llamadas de red salen del bucle de eventos por
`to_thread` y la respuesta al operador va con plazo
(`routers/backup.py::REMOTE_PROBE_TIMEOUT_S`). Lo que faltaba —y lo dice el
docstring de `test_backup_remote_probe_deadline.py`— es que ese plazo acota la
RESPUESTA, no el HILO: Python no puede matar un hilo, así que una sonda contra
una IP que DROPea paquetes seguía quemando un hilo del executor por defecto
(`min(32, cpu+4)`) hasta que se rindiera el SO. Con suficientes destinos
inalcanzables el executor se agota y `to_thread` vuelve a hacer cola — el
bloqueo entra por detrás.

El único sitio donde se puede acotar el HILO es dentro del adaptador, diciéndole
a su cliente cuánto puede esperar a que abra el socket. Eso es lo que fijan
estos tests, uno por backend.

Lo que deliberadamente NO se toca, porque acotarlo rompería el caso bueno: el
plazo de LECTURA. Una subida multiparte de varios GB por un enlace lento hace
lecturas legítimamente largas, y un `read_timeout` corto la mataría a mitad. El
connect es distinto: o abre en segundos o no va a abrir.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

pytestmark = pytest.mark.unit


def test_the_connect_deadline_is_short_enough_to_be_useful() -> None:
    """Un TCP connect que no abre en unos segundos no va a abrir. El valor
    concreto importa: 5 minutos sería tener plazo sobre el papel."""
    from workers.backup_destinations import REMOTE_CONNECT_TIMEOUT_S

    assert 0 < REMOTE_CONNECT_TIMEOUT_S <= 30


# ---------------------------------------------------------------------------
# S3 / B2 (boto3)
# ---------------------------------------------------------------------------
def test_s3_client_is_built_with_a_bounded_connect_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin esto, boto3 va con `connect_timeout=60` Y reintentos: una sonda
    contra un host muerto ocupa el hilo minutos, no segundos."""
    import boto3
    from workers.backup_destinations import REMOTE_CONNECT_TIMEOUT_S, _default_boto3_factory

    captured: dict[str, Any] = {}

    def _fake_client(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "client"

    monkeypatch.setattr(boto3, "client", _fake_client)
    assert _default_boto3_factory(service_name="s3", endpoint_url=None) == "client"

    config = captured.get("config")
    assert config is not None, "el cliente boto3 se construyó sin botocore Config"
    assert config.connect_timeout == REMOTE_CONNECT_TIMEOUT_S
    # Los reintentos multiplican el plazo: 5 intentos × 10 s son 50 s de hilo
    # quemado por sonda, que es justo lo que se venía a acotar.
    assert config.retries["max_attempts"] <= 2
    # El de LECTURA se queda generoso a propósito (ver el docstring del módulo).
    assert config.read_timeout is None or config.read_timeout >= 60


def test_s3_factory_does_not_override_an_explicit_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El default es un default: si un llamante afina el Config, gana él."""
    import boto3
    from botocore.config import Config as BotocoreConfig
    from workers.backup_destinations import _default_boto3_factory

    captured: dict[str, Any] = {}
    monkeypatch.setattr(boto3, "client", lambda **kw: captured.update(kw) or "client")

    mine = BotocoreConfig(connect_timeout=3)
    _default_boto3_factory(service_name="s3", config=mine)
    assert captured["config"] is mine


# ---------------------------------------------------------------------------
# SFTP (paramiko)
# ---------------------------------------------------------------------------
class _FakeSSHClient:
    last_connect_kwargs: ClassVar[dict[str, Any]] = {}

    def load_system_host_keys(self) -> None: ...

    def load_host_keys(self, path: str) -> None: ...

    def set_missing_host_key_policy(self, policy: object) -> None: ...

    def connect(self, **kwargs: Any) -> None:
        type(self).last_connect_kwargs = dict(kwargs)

    def open_sftp(self) -> str:
        return "sftp"


def test_sftp_connect_carries_explicit_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    """paramiko sin `timeout` hereda el del SO — contra un firewall que DROPea,
    minutos. Y los otros dos plazos son igual de necesarios: un host que abre el
    socket y no manda banner, o que acepta y no responde al auth, cuelga el hilo
    igual con el TCP connect ya resuelto."""
    import paramiko
    from workers.backup_destinations import REMOTE_CONNECT_TIMEOUT_S, _default_paramiko_transport

    monkeypatch.setattr(paramiko, "SSHClient", _FakeSSHClient)
    result = _default_paramiko_transport(
        host="h",
        port=22,
        username="u",
        host_key_policy="reject",
        known_hosts_path=None,
        password="p",
    )
    assert result == "sftp"

    kwargs = _FakeSSHClient.last_connect_kwargs
    assert kwargs["timeout"] == REMOTE_CONNECT_TIMEOUT_S
    assert 0 < kwargs["banner_timeout"] <= 60
    assert 0 < kwargs["auth_timeout"] <= 60


# ---------------------------------------------------------------------------
# rclone (subproceso)
# ---------------------------------------------------------------------------
def test_rclone_argv_carries_a_connect_timeout() -> None:
    """rclone trae `--contimeout` a 1 minuto por defecto y lo aplica a cada
    intento. Se acorta en TODAS las invocaciones (no solo en la sonda) porque
    acota únicamente la fase de conexión: una copia de varios GB no se ve
    afectada, y una copia contra un host muerto deja de esperar un minuto."""
    from pathlib import Path

    from workers.backup import CommandResult
    from workers.backup_destinations import (
        REMOTE_CONNECT_TIMEOUT_S,
        RcloneDestination,
        RcloneDestinationConfig,
    )

    seen: list[list[str]] = []

    class _Runner:
        def run(self, argv: list[str], timeout: int | None = None) -> CommandResult:
            seen.append(list(argv))
            return CommandResult(returncode=0, stdout="[]", stderr="")

    dest = RcloneDestination(
        config=RcloneDestinationConfig(name="d", remote="rem", path="backups"),
        secrets=_FakeSecrets(),
        runner=_Runner(),
    )
    dest._rclone(Path("conf.conf"), "lsd", "rem:backups")

    argv = seen[0]
    assert f"--contimeout={REMOTE_CONNECT_TIMEOUT_S}s" in argv
    # La bandera va ANTES del subcomando: rclone exige las globales delante.
    assert argv.index(f"--contimeout={REMOTE_CONNECT_TIMEOUT_S}s") < argv.index("lsd")


class _FakeSecrets:
    def fetch(self, fields: list[str]) -> dict[str, str]:
        return dict.fromkeys(fields, "[rem]\ntype = local\n")
