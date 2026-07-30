"""Invariante DooD del data-root: `WORKERS_DATA_ROOT` == destino del montaje.

Guarda estática del gotcha
[`worktree-bind-dood-empty-vs-named-volume.md`](../../docs/03-guides/gotchas/worktree-bind-dood-empty-vs-named-volume.md)
(prod-18). Carga el YAML del overlay; **no** habla con Docker.

## Qué está en juego

El worker corre dentro de un contenedor y lanza el `agent-runtime` contra el daemon
del host (Docker-out-of-Docker). En `docker run -v origen:destino`, el **origen lo
resuelve el daemon en el FS del host**, no el rootfs del worker. Así que el
`workspace_host_path` que el worker calcula —`{WORKERS_DATA_ROOT}/projects/…`— tiene
que ser un path que signifique **lo mismo** dentro del worker y para el daemon.

Cuando no coinciden no hay error: el daemon crea el directorio inexistente, lo monta
**vacío**, y el agente quema sus 50 iteraciones escribiendo en un `/workspace` que
nadie lee. Es un fallo silencioso y caro, y su única señal es esta línea de
configuración. En Docker Desktop/WSL2 la forma correcta es el volumen nombrado
montado en su ruta daemon-side (`/var/lib/docker/volumes/<vol>/_data`), que es lo
que hace este overlay.

## Los dos grupos de servicios, y por qué no se les pide lo mismo

  * **los que lanzan agentes/sandboxes** (`--queues` con `default`/`test`/`review`):
    tienen que montar el volumen `agent_data` EXACTAMENTE en `WORKERS_DATA_ROOT`.
  * **el resto que declara `WORKERS_DATA_ROOT`** (hoy `workers-backup`, en la cola
    `privileged`): no lanza agentes, pero el path tiene que seguir siendo
    alcanzable dentro del contenedor — `workers-backup` monta
    `/var/lib/docker/volumes` entero, que lo contiene. Se exige cobertura, no
    igualdad: pedirle el mismo montaje sería inventarse un requisito.

Y el tercero: `worktrees-init` prepara ese mismo path (`mkdir` + `chown` al uid del
worker). Si preparara otro, el worker encontraría un directorio del que no puede
escribir — el mismo síntoma con otra causa.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker" / "docker-compose.manuals.yml"

#: Nombre del volumen que transporta el data-root de agentes.
_AGENT_DATA_VOLUME = "agent_data"

#: Colas cuyo consumo implica lanzar contenedores de agente / sandbox.
_AGENT_QUEUES = frozenset({"default", "test", "review"})


def _compose() -> dict[str, Any]:
    data = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "docker-compose.manuals.yml no es un mapeo YAML"
    return data


def _services() -> dict[str, Any]:
    services = _compose().get("services")
    return services if isinstance(services, dict) else {}


def _env(service: dict[str, Any]) -> dict[str, str]:
    """`environment` normalizado a dict (compose admite mapa o lista `K=V`)."""
    raw = service.get("environment") or {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if v is not None}
    out: dict[str, str] = {}
    for item in raw:
        key, _, value = str(item).partition("=")
        out[key] = value
    return out


def _mounts(service: dict[str, Any]) -> list[tuple[str, str]]:
    """(origen, destino) de cada montaje en formato corto `src:dst[:mode]`."""
    out: list[tuple[str, str]] = []
    for entry in service.get("volumes") or []:
        if not isinstance(entry, str):
            continue
        parts = entry.split(":")
        if len(parts) >= 2:
            out.append((parts[0], parts[1]))
    return out


def _celery_queues(service: dict[str, Any]) -> frozenset[str]:
    command = service.get("command") or []
    tokens = command if isinstance(command, list) else str(command).split()
    for token in tokens:
        text = str(token)
        if text.startswith("--queues="):
            return frozenset(q.strip() for q in text.removeprefix("--queues=").split(","))
    return frozenset()


def _data_root_services() -> dict[str, dict[str, Any]]:
    return {n: s for n, s in _services().items() if _env(s).get("WORKERS_DATA_ROOT")}


def _agent_launching_services() -> dict[str, dict[str, Any]]:
    return {n: s for n, s in _data_root_services().items() if _celery_queues(s) & _AGENT_QUEUES}


# --- la guarda no puede pasar vacíamente -----------------------------------


def test_compose_overlay_exists_and_parses() -> None:
    assert _COMPOSE.is_file(), "falta docker/docker-compose.manuals.yml"
    assert _services(), "el overlay no declara servicios (¿YAML roto?)"


def test_the_guard_finds_the_services_it_is_about() -> None:
    """Si el descubrimiento deja de encontrar servicios, su verde no vale nada."""
    with_root = _data_root_services()
    launching = _agent_launching_services()
    assert len(with_root) >= 3, (
        f"solo {len(with_root)} servicio(s) declaran WORKERS_DATA_ROOT: el "
        f"descubrimiento se rompió (vistos: {sorted(with_root)})"
    )
    assert len(launching) >= 2, (
        f"solo {len(launching)} servicio(s) lanzan agentes según sus --queues "
        f"{ {n: sorted(_celery_queues(s)) for n, s in with_root.items()} }: el "
        "parseo del comando de Celery se rompió"
    )


# --- la invariante -----------------------------------------------------------


def test_agent_launching_services_mount_agent_data_at_the_data_root() -> None:
    """El destino del montaje de `agent_data` == `WORKERS_DATA_ROOT`, exacto."""
    offenders: list[str] = []
    for name, service in _agent_launching_services().items():
        data_root = _env(service)["WORKERS_DATA_ROOT"]
        destinations = [dst for src, dst in _mounts(service) if src == _AGENT_DATA_VOLUME]
        if not destinations:
            offenders.append(f"{name}: lanza agentes pero no monta {_AGENT_DATA_VOLUME}")
            continue
        if data_root not in destinations:
            offenders.append(
                f"{name}: WORKERS_DATA_ROOT={data_root!r} pero {_AGENT_DATA_VOLUME} "
                f"se monta en {destinations!r}"
            )
    assert not offenders, (
        "el path que el worker pasa al daemon (DooD) no coincide con el destino "
        "del montaje: el daemon montaría un directorio inexistente y /workspace "
        f"aparecería VACÍO sin dar error. {offenders}"
    )


def test_every_declared_data_root_is_reachable_inside_the_container() -> None:
    """Todo `WORKERS_DATA_ROOT` cae dentro de algún destino montado.

    Cubre los servicios que declaran el data-root sin lanzar agentes: el path
    tiene que existir dentro del contenedor por algún montaje (exacto o un bind
    que lo contenga), o las operaciones sobre `data_root` escriben en el rootfs
    efímero del contenedor.
    """
    offenders: list[str] = []
    for name, service in _data_root_services().items():
        data_root = _env(service)["WORKERS_DATA_ROOT"].rstrip("/")
        destinations = [dst.rstrip("/") for _src, dst in _mounts(service)]
        covered = any(data_root == dst or data_root.startswith(f"{dst}/") for dst in destinations)
        if not covered:
            offenders.append(f"{name}: WORKERS_DATA_ROOT={data_root!r} no está en {destinations!r}")
    assert not offenders, f"data-root no montado dentro del contenedor: {offenders}"


def test_all_agent_launching_services_share_one_data_root() -> None:
    """Dos workers con data-roots distintos = worktrees que el otro no ve."""
    roots = {n: _env(s)["WORKERS_DATA_ROOT"] for n, s in _agent_launching_services().items()}
    assert len(set(roots.values())) == 1, (
        "los servicios que lanzan agentes no comparten el mismo data-root "
        f"({roots}): una tarea provisionada por uno sería invisible para el otro"
    )


def test_worktrees_init_prepares_the_same_path() -> None:
    """El servicio de init hace `mkdir`/`chown` sobre el MISMO path.

    Si preparara otro, el worker encontraría el directorio con el uid equivocado
    y la provisión del worktree fallaría por permisos (mismo síntoma, otra causa).
    """
    services = _services()
    init = services.get("worktrees-init")
    assert init is not None, "el overlay ya no tiene el servicio worktrees-init"

    roots = {_env(s)["WORKERS_DATA_ROOT"] for s in _agent_launching_services().values()}
    assert len(roots) == 1
    data_root = roots.pop()

    command = init.get("command") or []
    script = " ".join(str(t) for t in command) if isinstance(command, list) else str(command)
    assert data_root in script, (
        f"worktrees-init no prepara {data_root!r} (su comando es {script!r}): el "
        "worker encontraría el data-root sin crear o con el uid equivocado"
    )
    assert "chown" in script, (
        "worktrees-init ya no hace chown: el worker corre como uid no-root y no "
        "podría escribir en el data-root"
    )
    init_mounts = [dst for src, dst in _mounts(init) if src == _AGENT_DATA_VOLUME]
    assert data_root in init_mounts, (
        f"worktrees-init monta {_AGENT_DATA_VOLUME} en {init_mounts!r}, no en "
        f"{data_root!r}: prepararía un directorio de su propio rootfs"
    )


def test_agent_data_volume_is_external_and_named() -> None:
    """Durabilidad (incidente 2026-07-02): el volumen es EXTERNO.

    Un volumen no-externo se lo lleva `docker compose down -v`, y en Docker
    Desktop/WSL2 un bind por path vive en el rootfs efímero de la VM (cada
    engine-restart lo recreaba vacío). Externo + nombre fijo sobrevive a las dos
    cosas.
    """
    volumes = _compose().get("volumes") or {}
    spec = volumes.get(_AGENT_DATA_VOLUME)
    assert isinstance(spec, dict), f"el overlay no declara el volumen {_AGENT_DATA_VOLUME}"
    assert spec.get("external") is True, (
        f"{_AGENT_DATA_VOLUME} no es external: `docker compose down -v` se llevaría "
        "los bare repos y los worktrees de todos los proyectos"
    )
    name = str(spec.get("name") or "")
    assert name, f"{_AGENT_DATA_VOLUME} external sin `name` fijo"
    # El nombre del volumen aparece en la ruta daemon-side que se monta.
    roots = {_env(s)["WORKERS_DATA_ROOT"] for s in _agent_launching_services().values()}
    assert any(name in root for root in roots), (
        f"el data-root {roots} no apunta a la ruta daemon-side del volumen "
        f"{name!r} (/var/lib/docker/volumes/{name}/_data): el bind DooD no "
        "resolvería dentro del volumen"
    )
