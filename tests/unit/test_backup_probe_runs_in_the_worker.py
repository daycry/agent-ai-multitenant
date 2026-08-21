"""Las sondas de destino de backup corren EN EL WORKER (prod-15 `task_gov_app_boundary_11`).

Cierra el hallazgo api-9 y la decisión D5 del plan prod-15. `routers/backup.py`
construía los adaptadores de destino (boto3 / paramiko / rclone) dentro del
proceso del api-server con dos ``from workers…`` diferidos. La frontera de apps
que ``celery_client.py`` declara era la parte visible; la parte cara es que **el
endpoint no podía funcionar donde estaba**:

    los adaptadores resuelven sus credenciales por el seam de secretos, y el que
    se les pasaba —``EnvSecretsProvider``— lee ``os.environ`` DEL PROCESO QUE LOS
    EJECUTA (``backup_s3_access_key_id`` → ``WORKERS_BACKUP_S3_ACCESS_KEY_ID``).
    El proceso era el api-server, que no declara ninguna ``WORKERS_*``: las
    credenciales de destino viven en la lane ``privileged`` (servicio
    ``workers-backup``), que es donde la referencia de backup manda ponerlas.

O sea que el botón «probar conectividad» del panel habría dicho FAIL —con un
«faltan credenciales» correcto e inútil— en cuanto alguien configurase un destino
con credencial siguiendo la documentación, y el listado remoto habría vuelto
vacío en silencio. No se había visto porque en este stack no hay ningún destino
configurado.

## Qué fija este fichero, y qué NO

Fija el CONTRATO de la delegación, que es lo que puede volver a romperse:

  * el router encola y espera, no construye adaptadores;
  * lo que viaja al broker es la config NO SECRETA, nunca una credencial;
  * la sonda va a la lane que tiene las credenciales, y con ``expires`` para que
    una sonda caducada no se ejecute tarde;
  * un worker que no contesta es un FALLO ACOTADO con motivo, no un 500 ni un
    spinner eterno;
  * el listado remoto sigue siendo best-effort POR DESTINO.

NO fija que el adaptador funcione: eso ya lo cubren los tests del propio paquete
``workers.backup_destinations``. Aquí no se toca la red.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ROUTER = _REPO_ROOT / "apps" / "api-server" / "src" / "api_server" / "routers" / "backup.py"


class _FakePrincipal:
    """Lo único que el endpoint le pide al principal es el id para la auditoría."""

    def __init__(self) -> None:
        self.user_id = uuid4()


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Captura las escrituras de auditoría en vez de tocar la BD."""
    from api_server.routers import backup as router

    entries: list[dict[str, Any]] = []

    async def _write(_session: Any, **kwargs: Any) -> None:
        entries.append(kwargs)

    monkeypatch.setattr(router, "write_audit_log", _write)
    return entries


def _seed_destinations(monkeypatch: pytest.MonkeyPatch, items: list[dict[str, Any]]) -> None:
    from api_server.routers import backup as router

    async def _get(_session: Any) -> list[dict[str, Any]]:
        return items

    monkeypatch.setattr(router, "get_backup_destinations", _get)


_S3 = {
    "type": "s3",
    "name": "offsite-s3",
    "enabled": True,
    "config": {"bucket": "backups", "prefix": "nightly/"},
}


# ===========================================================================
# La frontera: el router ya no importa `workers`
# ===========================================================================
def test_the_router_no_longer_imports_the_workers_package() -> None:
    """El enunciado literal de la casilla: «`routers/backup.py` deja de hacer
    `from workers…`». Por AST y no por grep, porque este fichero está lleno de
    prosa que explica por qué NO se importa el paquete y un grep la contaría."""
    tree = ast.parse(_ROUTER.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "workers":
            offenders.append(f"línea {node.lineno}: from {node.module} import …")
        elif isinstance(node, ast.Import):
            offenders.extend(
                f"línea {node.lineno}: import {alias.name}"
                for alias in node.names
                if alias.name.split(".")[0] == "workers"
            )

    assert not offenders, (
        "`routers/backup.py` volvió a importar el paquete `workers`: "
        f"{offenders}. La red de los destinos corre en el worker, que es donde "
        "están las `WORKERS_BACKUP_*` — encola por nombre "
        "(`celery_client.probe_backup_destination_and_wait`)."
    )


# ===========================================================================
# La sonda de conectividad
# ===========================================================================
@pytest.mark.asyncio
async def test_the_probe_is_delegated_and_its_verdict_is_relayed(
    monkeypatch: pytest.MonkeyPatch, audited: list[dict[str, Any]]
) -> None:
    """El endpoint devuelve lo que dijo el worker, sin reinterpretarlo."""
    from api_server import celery_client
    from api_server.routers.backup import test_backup_destination as probe_endpoint

    _seed_destinations(monkeypatch, [_S3])
    sent: list[dict[str, Any]] = []

    async def _fake_probe(config: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        sent.append(config)
        return {"ok": True, "detail": "bucket 'backups' reachable"}

    monkeypatch.setattr(celery_client, "probe_backup_destination_and_wait", _fake_probe)

    result = await probe_endpoint("offsite-s3", principal=_FakePrincipal(), session=None)

    assert result.ok is True
    assert result.detail == "bucket 'backups' reachable"
    # La fábrica del worker espera `{type, name, **config}`.
    assert sent == [{"type": "s3", "name": "offsite-s3", "bucket": "backups", "prefix": "nightly/"}]
    assert audited and audited[0]["changes"] == {"name": "offsite-s3", "type": "s3", "ok": True}


def test_no_credential_can_travel_to_the_broker() -> None:
    """La razón de que la sonda se mude es que las credenciales están en el
    worker; sería absurdo mandárselas por el broker para que las use.

    El payload NO filtra nada, y hace bien: filtrar en silencio taparía un
    problema de configuración en vez de denunciarlo. Quien garantiza que no hay
    secretos que arrastrar es ``validate_backup_destinations``, con su allow-list
    por tipo — y por eso este test los ata a los dos: el validador rechaza el
    campo secreto Y lo que sale de él, pasado por el payload, no tiene ni un
    nombre con pinta de credencial. Si alguien relajara la allow-list, esto se
    pone rojo antes de que un secreto llegue a Redis.
    """
    from api_server.db.platform_settings import (
        BACKUP_DESTINATION_TYPES,
        InvalidBackupDestinationError,
        validate_backup_destinations,
    )
    from api_server.routers.backup import destination_probe_payload

    legit = validate_backup_destinations(
        [
            {"type": "s3", "name": "s3", "config": {"bucket": "b", "prefix": "p/"}},
            {"type": "b2", "name": "b2", "config": {"bucket": "b", "region": "us-west-002"}},
            {"type": "sftp", "name": "nas", "config": {"host": "h", "username": "u", "port": 22}},
            {"type": "rclone", "name": "gd", "config": {"remote": "gdrive", "path": "x"}},
        ]
    )
    assert len(legit) == len(BACKUP_DESTINATION_TYPES), (
        "la guarda dejó de cubrir un tipo de destino: cúbrelos todos o el día "
        "que se añada uno nadie mirará su allow-list"
    )
    for item in legit:
        payload = destination_probe_payload(item)
        leaked = [
            key
            for key in payload
            if any(word in key.lower() for word in ("password", "secret", "key", "token"))
        ]
        assert not leaked, f"{item['name']}: el payload al worker lleva {leaked}"

    # Y el otro lado del contrato: el campo secreto no llega a almacenarse.
    for secret_field in ("backup_sftp_password", "backup_s3_secret_access_key"):
        with pytest.raises(InvalidBackupDestinationError):
            validate_backup_destinations(
                [
                    {
                        "type": "sftp",
                        "name": "nas",
                        "config": {"host": "h", "username": "u", secret_field: "hunter2"},
                    }
                ]
            )


@pytest.mark.asyncio
async def test_a_silent_worker_is_a_bounded_failure_with_a_reason(
    monkeypatch: pytest.MonkeyPatch, audited: list[dict[str, Any]]
) -> None:
    """La lane `privileged` corre con `--concurrency=1` y drena el backup
    nocturno: que la sonda no conteste es un caso REAL, no defensivo. Tiene que
    volver como FAIL con motivo —no como 500, no como spinner— y quedar auditado
    como fallo."""
    from api_server import celery_client
    from api_server.routers.backup import test_backup_destination as probe_endpoint

    _seed_destinations(monkeypatch, [_S3])

    async def _no_answer(config: dict[str, Any], *, timeout_s: float) -> dict[str, Any] | None:
        return None

    monkeypatch.setattr(celery_client, "probe_backup_destination_and_wait", _no_answer)

    result = await probe_endpoint("offsite-s3", principal=_FakePrincipal(), session=None)

    assert result.ok is False
    assert "did not answer" in result.detail
    assert audited[0]["changes"]["ok"] is False


@pytest.mark.asyncio
async def test_an_unknown_destination_never_reaches_the_broker(
    monkeypatch: pytest.MonkeyPatch, audited: list[dict[str, Any]]
) -> None:
    """El 404 sigue resolviéndose aquí: encolar una sonda de un destino que no
    existe gasta un turno de la lane privilegiada para nada."""
    from api_server import celery_client
    from api_server.routers.backup import test_backup_destination as probe_endpoint
    from fastapi import HTTPException

    _seed_destinations(monkeypatch, [_S3])
    calls: list[dict[str, Any]] = []

    async def _fake_probe(config: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        calls.append(config)
        return {"ok": True, "detail": "ok"}

    monkeypatch.setattr(celery_client, "probe_backup_destination_and_wait", _fake_probe)

    with pytest.raises(HTTPException) as excinfo:
        await probe_endpoint("nope", principal=_FakePrincipal(), session=None)

    assert excinfo.value.status_code == 404
    assert calls == []


# ===========================================================================
# El listado remoto
# ===========================================================================
@pytest.mark.asyncio
async def test_the_remote_listing_stays_best_effort_per_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un destino mudo no puede vaciar la lista de los demás.

    Es la razón de que sea UNA tarea POR DESTINO y no una por lista: con una
    sola tarea para todas, el plazo sería del lote y un destino colgado se
    llevaría por delante a los sanos."""
    from api_server import celery_client
    from api_server.routers.backup import _list_remote_backups

    _seed_destinations(
        monkeypatch,
        [
            {"type": "s3", "name": "muerto", "config": {"bucket": "b"}},
            {"type": "s3", "name": "vivo", "config": {"bucket": "c"}},
            {"type": "s3", "name": "apagado", "enabled": False, "config": {"bucket": "d"}},
        ],
    )

    async def _fake_list(config: dict[str, Any], *, timeout_s: float) -> list[str] | None:
        if config["name"] == "muerto":
            return None  # el worker no contestó
        return ["20260818-030000.tar"]

    monkeypatch.setattr(celery_client, "list_remote_backup_entries_and_wait", _fake_list)

    assert await _list_remote_backups(None) == [("20260818-030000.tar", "vivo")]


@pytest.mark.asyncio
async def test_a_disabled_destination_is_never_enqueued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`enabled: false` es del operador: no se le pregunta al worker."""
    from api_server import celery_client
    from api_server.routers.backup import _list_remote_backups

    _seed_destinations(
        monkeypatch,
        [{"type": "s3", "name": "apagado", "enabled": False, "config": {"bucket": "d"}}],
    )
    asked: list[str] = []

    async def _fake_list(config: dict[str, Any], *, timeout_s: float) -> list[str]:
        asked.append(str(config["name"]))
        return []

    monkeypatch.setattr(celery_client, "list_remote_backup_entries_and_wait", _fake_list)

    assert await _list_remote_backups(None) == []
    assert asked == []


# ===========================================================================
# El productor: lane, plazos y caducidad
# ===========================================================================
def test_the_probe_goes_to_the_lane_that_holds_the_credentials() -> None:
    """`privileged` no es una elección estética: es la ÚNICA lane del compose
    que lleva las `WORKERS_BACKUP_*` (servicio `workers-backup`). Mandarla a
    `default` reproduce exactamente el fallo que esta casilla arregla."""
    from api_server.celery_client import _BACKUP_PROBE_QUEUE, _RESTORE_QUEUE

    assert _BACKUP_PROBE_QUEUE == "privileged"
    assert _BACKUP_PROBE_QUEUE == _RESTORE_QUEUE


@pytest.mark.asyncio
async def test_a_probe_that_cannot_run_in_time_is_discarded_not_deferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`expires`: la lane privilegiada corre con `--concurrency=1` y drena el
    backup nocturno. Sin caducidad, una sonda encolada detrás de un backup de
    media hora se ejecutaría cuando el operador cerró el panel hace rato — gasta
    un turno de la lane para producir un resultado que nadie va a leer."""
    from api_server import celery_client

    captured: dict[str, Any] = {}

    class _FakeAsyncResult:
        def get(self, timeout: float | None = None) -> dict[str, Any]:
            captured["get_timeout"] = timeout
            return {"ok": True, "detail": "ok"}

    class _FakeCelery:
        def send_task(self, name: str, **kwargs: Any) -> _FakeAsyncResult:
            captured["name"] = name
            captured.update(kwargs)
            return _FakeAsyncResult()

    fake = _FakeCelery()
    monkeypatch.setattr(celery_client, "get_celery_client", lambda: fake)

    result = await celery_client.probe_backup_destination_and_wait(
        {"type": "s3", "name": "x", "bucket": "b"}, timeout_s=15.0
    )

    assert result == {"ok": True, "detail": "ok"}
    assert captured["name"] == "workers.backup_test_destination"
    assert captured["queue"] == "privileged"
    assert captured["expires"] == 15.0, (
        "la sonda se encoló SIN caducidad: detrás del backup nocturno se "
        "ejecutará cuando ya no le importe a nadie"
    )
    assert captured["get_timeout"] == 15.0, (
        "sin plazo en el `get`, el hilo del executor se queda colgado del "
        "backend de resultados para siempre y `to_thread` no puede matarlo"
    )


@pytest.mark.asyncio
async def test_a_broker_outage_is_a_none_not_a_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un botón de «probar conectividad» que devuelve 500 no le dice al operador
    nada que un FAIL con detalle no le diga mejor."""
    from api_server import celery_client

    class _DeadCelery:
        def send_task(self, name: str, **kwargs: Any) -> Any:
            raise OSError("Connection refused")

    dead = _DeadCelery()
    monkeypatch.setattr(celery_client, "get_celery_client", lambda: dead)

    assert (
        await celery_client.probe_backup_destination_and_wait(
            {"type": "s3", "name": "x"}, timeout_s=1.0
        )
        is None
    )
    assert (
        await celery_client.list_remote_backup_entries_and_wait(
            {"type": "s3", "name": "x"}, timeout_s=1.0
        )
        is None
    )


def test_the_request_deadline_sits_above_the_probe_deadline() -> None:
    """Los dos plazos no son redundantes y su ORDEN importa: el camino normal lo
    corta el `get` del productor (que devuelve un `None` limpio con motivo); el
    del request es el cinturón para lo que aquél no acota — un `send_task`
    colgado del socket del broker. Invertidos, el que vencería siempre sería el
    del request, y el operador vería «timeout» donde el worker sí contestó."""
    from api_server.routers.backup import PROBE_REQUEST_DEADLINE_S, REMOTE_PROBE_TIMEOUT_S

    assert PROBE_REQUEST_DEADLINE_S > REMOTE_PROBE_TIMEOUT_S


# ===========================================================================
# La mitad de worker
# ===========================================================================
def test_the_worker_tasks_are_registered_under_the_names_the_producer_sends() -> None:
    """Un productor que encola un nombre que ningún worker registra no falla:
    la tarea se queda en la cola hasta caducar y el endpoint dice «no contestó».
    Un fallo mudo, que es el peor que puede tener esta pareja."""
    from api_server.celery_client import _BACKUP_LIST_REMOTE_TASK, _BACKUP_TEST_DESTINATION_TASK
    from workers import backup_probe_task
    from workers.celery_app import build_celery_app

    assert backup_probe_task.backup_test_destination.name == _BACKUP_TEST_DESTINATION_TASK
    assert backup_probe_task.backup_list_remote.name == _BACKUP_LIST_REMOTE_TASK

    # Y el módulo entra en el `imports` del app, que es lo que hace que un worker
    # los registre al arrancar.
    assert "workers.backup_probe_task" in build_celery_app().conf.imports


def test_the_worker_probe_never_raises_and_never_echoes_a_library_error() -> None:
    """El endpoint pinta lo que devuelve esta tarea. Una excepción cruda se
    convertiría en un 500 del panel, y el mensaje de una excepción de librería
    puede llevar material sensible (una URL firmada, una cabecera de auth): de
    las que no son `DestinationError` sólo sale el TIPO."""
    import workers.backup_destinations as bd
    from workers.backup_probe_task import backup_list_remote, backup_test_destination

    class _Exploding:
        def test_connectivity(self) -> Any:
            raise RuntimeError("AWS_SECRET_ACCESS_KEY=AKIAsupersecreto rejected")

        def list_remote(self) -> Any:
            raise RuntimeError("AWS_SECRET_ACCESS_KEY=AKIAsupersecreto rejected")

    original = bd.build_destination
    bd.build_destination = lambda config, **kwargs: _Exploding()  # type: ignore[assignment]
    try:
        probe = backup_test_destination({"type": "s3", "name": "x", "bucket": "b"})
        listing = backup_list_remote({"type": "s3", "name": "x", "bucket": "b"})
    finally:
        bd.build_destination = original  # type: ignore[assignment]

    assert probe["ok"] is False
    assert "supersecreto" not in probe["detail"]
    assert "RuntimeError" in probe["detail"]
    assert listing == []


def test_a_destination_error_keeps_its_message_because_it_is_not_leaky() -> None:
    """El contrario del anterior: `DestinationError` es el tipo que los
    adaptadores garantizan no-filtrante, y su mensaje es justo el que el
    operador necesita para arreglar el destino. Tragárselo dejaría un FAIL sin
    causa."""
    import workers.backup_destinations as bd
    from workers.backup_probe_task import backup_test_destination

    original = bd.build_destination

    def _reject(config: dict[str, Any], **kwargs: Any) -> Any:
        raise bd.DestinationError("destination 's3' requires 'bucket'")

    bd.build_destination = _reject  # type: ignore[assignment]
    try:
        probe = backup_test_destination({"type": "s3", "name": "x"})
    finally:
        bd.build_destination = original  # type: ignore[assignment]

    assert probe == {"ok": False, "detail": "destination 's3' requires 'bucket'"}
