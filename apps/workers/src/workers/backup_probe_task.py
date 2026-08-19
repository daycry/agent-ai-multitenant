"""Sondas de destinos remotos de backup, EJECUTADAS EN EL WORKER.

prod-15 ``task_gov_app_boundary_11`` (hallazgo api-9, decisión D5).

## Qué se movió aquí y por qué no era cosmética

``routers/backup.py`` construía los adaptadores de destino (boto3 / paramiko /
rclone) **dentro del api-server** con dos ``from workers…`` diferidos, para dos
cosas: el botón «probar conectividad» del panel y el listado de bundles remotos
de la pantalla de restore. Eso rompía la frontera que ``celery_client.py``
declara en su primera línea —«we never import the `workers` package»— pero, sobre
todo, **no podía funcionar donde estaba**:

Los adaptadores resuelven sus credenciales por el seam de secretos, y el que se
les pasaba desde el router es :class:`workers.backup_encryption.EnvSecretsProvider`,
que lee ``os.environ`` **del proceso que lo ejecuta**
(``backup_s3_access_key_id`` → ``WORKERS_BACKUP_S3_ACCESS_KEY_ID``). El proceso
era el api-server, y el api-server **no declara ni una sola variable
``WORKERS_*``**: las credenciales de destino viven en la lane ``privileged``
(servicio ``workers-backup`` del compose), que es donde
``docs/04-reference/backup-restore.md`` manda ponerlas. En cuanto alguien
configurase un destino con credencial siguiendo la documentación, el botón
«probar» habría dicho FAIL con un «faltan credenciales» perfectamente correcto y
perfectamente inútil, y el listado remoto habría vuelto vacío en silencio (es
best-effort). Nadie lo había visto porque en este stack no hay ningún destino
configurado.

O sea: la sonda tiene que correr **donde están los secretos**. Esta es la mitad
de worker; la de api-server es
:func:`api_server.celery_client.probe_backup_destination_and_wait`.

## La cola: `privileged`, y el porqué del `expires`

Se encolan en ``privileged`` porque es la única lane que hoy lleva las
``WORKERS_BACKUP_*``. Tiene una pega conocida y aceptada: ese pool corre con
``--concurrency=1`` y es el que ejecuta el backup nocturno y los restores, así
que una sonda encolada mientras corre un backup de media hora esperaría detrás.
Por eso el productor las manda con ``expires``: una sonda que no ha arrancado
cuando su plazo vence se **descarta** en el broker en vez de ejecutarse tarde,
contra un operador que hace rato dejó de mirar. El endpoint devuelve entonces un
FAIL acotado y explícito, que es la degradación honesta.

La salida definitiva —una lane propia para sondas, o replicar las
``WORKERS_BACKUP_*`` en ``default``— es diseño de despliegue y se decide con el
compose delante, no aquí.

## Nada secreto viaja por el broker

El productor manda la config **NO secreta** del destino, la misma que valida
``api_server.db.platform_settings.validate_backup_destinations`` con una
allow-list por tipo que rechaza con 422 cualquier campo que huela a credencial.
Las credenciales las resuelve este proceso, desde su propio entorno.
"""

from __future__ import annotations

from typing import Any

import structlog

from workers.celery_app import app

_log = structlog.get_logger("workers.backup_probe_task")


@app.task(name="workers.backup_test_destination")  # type: ignore[untyped-decorator]
def backup_test_destination(config: dict[str, Any]) -> dict[str, Any]:
    """Sonda barata de alcanzabilidad/auth de UN destino.

    ``config`` es ``{type, name, **config_no_secreta}``, tal cual lo espera la
    fábrica. Devuelve ``{"ok": bool, "detail": str}``.

    NUNCA propaga una excepción: el endpoint que espera este resultado pinta
    FAIL + detalle, y una excepción cruda se convertiría en un 500 del panel. Un
    ``DestinationError`` ya viene con mensaje no-filtrante (los adaptadores
    garantizan que el detalle no lleva credencial); de cualquier otro sólo se
    devuelve el TIPO, porque el mensaje de una excepción de librería sí podría
    llevar material sensible (una URL firmada, un header de auth).
    """
    from workers.backup_destinations import DestinationError, build_destination
    from workers.backup_encryption import EnvSecretsProvider

    name = str(config.get("name") or "?")
    try:
        destination = build_destination(dict(config), secrets=EnvSecretsProvider())
        result = destination.test_connectivity()
    except DestinationError as exc:
        return {"ok": False, "detail": str(exc)}
    except Exception as exc:  # defensivo: el panel nunca ve un 500 por esto
        _log.warning("backup.probe.error", destination=name, error=type(exc).__name__)
        return {"ok": False, "detail": f"connectivity probe failed: {type(exc).__name__}"}
    return {"ok": bool(result.ok), "detail": str(result.detail)}


@app.task(name="workers.backup_list_remote")  # type: ignore[untyped-decorator]
def backup_list_remote(config: dict[str, Any]) -> list[str]:
    """Enumera los objetos de UN destino remoto. Devuelve sus NOMBRES.

    Una tarea por destino, no una por lista: así el «best-effort por destino» del
    listado de restore sigue siendo por destino. Un destino inalcanzable devuelve
    ``[]`` y el listado del panel conserva los demás, que es exactamente lo que
    hacía el bucle del router antes de mudarse aquí.
    """
    from workers.backup_destinations import DestinationError, build_destination
    from workers.backup_encryption import EnvSecretsProvider

    name = str(config.get("name") or "?")
    try:
        destination = build_destination(dict(config), secrets=EnvSecretsProvider())
        return [str(entry.name) for entry in destination.list_remote()]
    except DestinationError as exc:
        _log.info("backup.list_remote.skipped", destination=name, error=str(exc)[:200])
        return []
    except Exception as exc:  # defensivo: un destino roto no vacía la lista
        _log.warning("backup.list_remote.error", destination=name, error=type(exc).__name__)
        return []


__all__ = ["backup_list_remote", "backup_test_destination"]
