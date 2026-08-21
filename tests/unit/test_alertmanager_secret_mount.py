"""El canal de respaldo tiene DÓNDE leer su credencial (prod-08 `task_prod08_alert_fallback_02`).

`alertmanager.yml` declara el receiver `critical-fallback` leyendo el webhook de
Slack de un fichero (`api_url_file: /etc/alertmanager/secrets/slack_api_url`) en
vez de incrustarlo: Alertmanager no expande `${ENV}` en su config y un webhook de
Slack es una credencial —quien la tiene, publica en el canal—, así que el fichero
es el único camino honesto.

**Pero declarar el camino no es tenerlo.** Hasta el 2026-08-10 el servicio
`alertmanager` del overlay de monitorización montaba exactamente dos cosas: su
propio `alertmanager.yml` y su volumen de datos. Nada en
`/etc/alertmanager/secrets/`. Es decir: el operador que consiguiera el webhook
—que es lo único que este plan declara pendiente de un humano— **no tenía dónde
ponerlo** sin editar el compose, y el runbook le pedía justamente eso
(«provisionarlo como fichero en `/etc/alertmanager/secrets/slack_api_url`»).

Y el fallo es del tipo caro: Alertmanager **arranca igual** sin el fichero
(`api_url_file` se lee al notificar, no al cargar la config), así que el stack
entero sale `healthy` y el canal de último recurso falla en cada envío, en
silencio, precisamente en el escenario para el que existe — el api-server caído,
que no puede entregarse a sí mismo la alerta de que está caído.

Este fichero cierra el hueco por descubrimiento: recorre la config buscando
CUALQUIER clave `*_file` y exige que haya un bind-mount detrás. Si mañana alguien
añade `auth_password_file` a un receiver de email, la guarda lo pide sola.

Lo que este test NO puede acreditar, y por eso la casilla del plan sigue abierta:
que el fichero contenga un webhook válido. Eso es una credencial y su custodia es
humana (prod-10).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _ROOT / "docker" / "docker-compose.monitoring.yml"
_ALERTMANAGER_YML = _ROOT / "docker" / "monitoring" / "alertmanager" / "alertmanager.yml"
#: Raíz de las rutas relativas del compose: el fichero vive en `docker/`.
_COMPOSE_CONTEXT = _COMPOSE.parent


def _alertmanager_service() -> dict[str, Any]:
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    services = compose.get("services") or {}
    assert "alertmanager" in services, "el overlay de monitorización perdió el alertmanager"
    return services["alertmanager"]


def _secret_files_the_config_reads() -> set[str]:
    """Toda ruta absoluta que la config lee de un fichero (`*_file`).

    Descubrimiento, no lista fija: recorre el YAML entero. Alertmanager usa el
    sufijo `_file` de forma uniforme para todos sus secretos (`api_url_file`,
    `auth_password_file`, `bearer_token_file`, `credentials_file`…), así que un
    receiver nuevo con credencial en fichero entra aquí sin tocar el test.
    """
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if (
                    isinstance(key, str)
                    and key.endswith("_file")
                    and isinstance(value, str)
                    and value.startswith("/")
                ):
                    found.add(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(yaml.safe_load(_ALERTMANAGER_YML.read_text(encoding="utf-8")))
    return found


def _bind_mounts() -> list[tuple[str, str, str]]:
    """`(host, container, modo)` de cada bind-mount del servicio."""
    mounts: list[tuple[str, str, str]] = []
    for entry in _alertmanager_service().get("volumes") or []:
        if not isinstance(entry, str):  # pragma: no cover - forma larga no usada aquí
            continue
        parts = entry.split(":")
        if len(parts) < 2 or not parts[0].startswith("."):
            continue  # volumen nombrado (alertmanager_data), no un bind
        mounts.append((parts[0], parts[1], parts[2] if len(parts) > 2 else "rw"))
    return mounts


def test_the_config_still_reads_its_credential_from_a_file() -> None:
    """Control de no-vacuidad: si nadie lee ficheros, el resto pasaría solo."""
    secrets = _secret_files_the_config_reads()

    assert secrets, (
        "ningún receiver lee credenciales de fichero: o el respaldo de "
        "`severity=critical` desapareció, o alguien incrustó el webhook en el "
        "YAML (que es una credencial en el repositorio)"
    )


def test_every_secret_file_has_a_mount_behind_it() -> None:
    """El hueco original: ruta declarada, sin volumen que la respalde.

    Sin esto el operador no tiene dónde dejar la credencial, y Alertmanager
    arranca igual — el canal de respaldo falla en cada envío sin decir nada.
    """
    mounts = _bind_mounts()
    assert mounts, "el alertmanager dejó de montar nada: la guarda pasaría en vacío"

    targets = [container for _, container, _ in mounts]
    for secret in sorted(_secret_files_the_config_reads()):
        covered = any(
            secret == target or secret.startswith(target.rstrip("/") + "/") for target in targets
        )
        assert covered, (
            f"`{secret}` no está cubierto por ningún bind-mount del servicio "
            f"alertmanager (monta: {targets}). El fichero nunca existirá dentro "
            "del contenedor y el envío falla en silencio."
        )


def test_the_secret_mount_is_read_only() -> None:
    """Alertmanager no tiene por qué escribir en el directorio de secretos."""
    secrets = _secret_files_the_config_reads()
    checked = 0

    for host, container, mode in _bind_mounts():
        if not any(
            secret == container or secret.startswith(container.rstrip("/") + "/")
            for secret in secrets
        ):
            continue
        checked += 1
        assert mode == "ro", f"el bind `{host}` → `{container}` no es read-only (modo `{mode}`)"

    assert checked, "no se comprobó ningún montaje de secretos: la guarda pasó en vacío"


def test_the_host_side_of_the_secret_mount_exists_in_the_repo() -> None:
    """Si el directorio del host no existe, Docker lo INVENTA como root.

    Y el contenedor corre como `nobody` (65534), así que el operador acaba con
    un directorio que no puede escribir y un canal que sigue sin funcionar. Que
    el directorio esté versionado le da además un sitio evidente donde dejar la
    credencial, que es lo que pide el runbook.
    """
    checked = 0
    for host, container, _ in _bind_mounts():
        if not any(
            secret == container or secret.startswith(container.rstrip("/") + "/")
            for secret in _secret_files_the_config_reads()
        ):
            continue
        checked += 1
        resolved = (_COMPOSE_CONTEXT / host).resolve()
        assert resolved.is_dir(), (
            f"el lado host del montaje de secretos (`{host}`) no existe en el "
            "repositorio: `docker compose up` lo creará como root y el "
            "alertmanager, que corre como nobody, no podrá leerlo"
        )

    assert checked, "no se comprobó ningún montaje de secretos: la guarda pasó en vacío"


def test_no_credential_is_committed_in_the_secret_directory() -> None:
    """El precio de versionar el directorio es que hay que vigilarlo."""
    checked = 0
    for host, container, _ in _bind_mounts():
        if not any(
            secret == container or secret.startswith(container.rstrip("/") + "/")
            for secret in _secret_files_the_config_reads()
        ):
            continue
        resolved = (_COMPOSE_CONTEXT / host).resolve()
        if not resolved.is_dir():
            continue
        checked += 1
        stray = [p.name for p in resolved.iterdir() if p.name != ".gitignore"]
        assert not stray, (
            f"hay ficheros en `{host}` además del `.gitignore`: {stray}. "
            "Ese directorio es el buzón de una credencial; nada suyo se comitea."
        )
        gitignore = resolved / ".gitignore"
        assert gitignore.is_file(), (
            f"`{host}` está versionado sin `.gitignore` propio: el día que "
            "alguien deje ahí el webhook de Slack, se comitea"
        )

    assert checked, "no se inspeccionó ningún directorio de secretos: la guarda pasó en vacío"
