"""docker-compose generator — wizard config → runtime stack (Plan 15 task_15_07).

Phase B fills the real generators the install orchestration (task 15_05's
``generate_config`` step) calls. This module is the **compose generator**: given
the wizard config (profile / GPU on-off / storage choices / enabled LLM
providers / ports) it produces the runtime stack's ``docker-compose.yml`` as a
plain ``dict`` (and, via :func:`render_compose_yaml`, the YAML text written at
install time).

Why a dict, not a template
---------------------------
The canonical base compose (``docker/docker-compose.yml``) is the source of
truth for the *shape* of every service (image pins, healthchecks, named
volumes, the two networks). The installer never ships a half-baked deployment
where the operator hand-edits YAML; instead this builds the compose
*programmatically* from a typed catalogue so the wizard and the CLI share one
generator and the result is deterministic + assertable. The produced mapping is
serialised to YAML with :func:`render_compose_yaml` and written under the data
root at install time (NOT committed — that write lives behind the install
seams; this module is pure, no I/O).

Hardening defaults
------------------
Every generated service carries the platform hardening defaults consistent with
the existing compose: ``restart: unless-stopped``, capped json-file logging,
``cap_drop: [ALL]`` + ``security_opt: ["no-new-privileges:true",
"apparmor=agentic-default"]`` and a ``deploy.resources.limits`` cap. Images are
pinned (never ``:latest``). The two networks (``agentic-net`` + the internal
``agentic-agents``) and the named volumes match the canonical compose.

These are all TRUSTED first-party platform services, so — like the canonical
``docker-compose.yml`` (revised, ADR 0040) — they rely on Docker's DEFAULT
seccomp profile (by NOT overriding it) rather than a hand-rolled default-deny
allowlist. The hand-rolled profile, when force-applied to every service,
SIGSEGV'd the Go services (vault/minio) and broke postgres; the strict
default-deny allowlist is reserved for the UNTRUSTED agent/test/review runtimes
the worker launches (``docker/seccomp/agent-runtime.json``). The generated
services still pin ``apparmor=agentic-default`` for host MAC confinement.

Secrets
-------
The generated compose references credentials via ``${ENV}`` placeholders ONLY —
it NEVER embeds a literal secret, and for a *production* install it omits the
``:-changeme…`` dev fallbacks the dev compose carries (so the generated YAML
passes the platform's prod secret guard: it contains none of the dev-default
markers ``changeme`` / ``dev-only`` / ``minioadmin``). The real values are
written to the ``.env`` / Vault by tasks 15_08-15_09; this module only wires the
references. Nothing here is logged.
"""

from __future__ import annotations

import copy
from typing import Any

import yaml

from installer_backend.config import (
    Environment,
    InstallerConfig,
    LLMProviderKind,
)
from installer_backend.platform_images import load_platform_manifest

# ---------------------------------------------------------------------------
# Pinned images — kept in lockstep with docker/docker-compose.yml +
# docker-compose.monitoring.yml (supply-chain hygiene: never :latest).
# ---------------------------------------------------------------------------
IMAGE_POSTGRES = "pgvector/pgvector:pg16"
IMAGE_REDIS = "redis:7-alpine"
IMAGE_MINIO = "minio/minio:RELEASE.2024-11-07T00-52-20Z"
IMAGE_VAULT = "hashicorp/vault:1.17"
IMAGE_CLAMAV = "clamav/clamav:1.4"
IMAGE_DOCLING = "ghcr.io/docling-project/docling-serve:v1.20.0"
IMAGE_OLLAMA = "ollama/ollama:0.31.1"
IMAGE_PROMETHEUS = "prom/prometheus:v2.54.1"
IMAGE_GRAFANA = "grafana/grafana:11.2.0"
IMAGE_NODE_EXPORTER = "prom/node-exporter:v1.8.2"
IMAGE_ALERTMANAGER = "prom/alertmanager:v0.27.0"
IMAGE_CADVISOR = "gcr.io/cadvisor/cadvisor:v0.49.1"
# One-shot que abre el drop-dir del textfile collector (ver TEXTFILE_* abajo).
# Misma imagen que el `textfile-init` de docker-compose.monitoring.yml.
IMAGE_BUSYBOX = "busybox:1.36"
# Read-only Docker API gateway with a per-endpoint ACL (Plan prod-01 task_09,
# ADR 0060). The workers reach the daemon ONLY through this, never the raw
# socket (Principio 2).
IMAGE_DOCKER_SOCKET_PROXY = "tecnativa/docker-socket-proxy:0.3.0"
IMAGE_CADDY = "caddy:2.8-alpine"
# Voice mode (ADR 0073): STT (faster-whisper) + TTS (Kokoro), OpenAI-compatible
# HTTP APIs, kept in lockstep with docker/docker-compose.yml. Both ship CPU
# images here (the GPU variants are a documented overlay — the canonical compose
# pins the same CPU tags); upstream uses rolling tags (no semver) so pin by
# digest for a fully reproducible prod if needed.
IMAGE_STT = "fedirz/faster-whisper-server:latest-cpu"
IMAGE_TTS = "ghcr.io/remsky/kokoro-fastapi-cpu:v0.2.2"

#: The application images the platform builds. La referencia ya NO se compone
#: aquí con dos constantes: sale del manifiesto de release
#: (:mod:`installer_backend.platform_images`, ADR 0148 aplicado a las seis por el
#: orden duro del ADR 0161), que es quien sabe si hay digests publicados.
#:
#: Mientras no los haya —el estado de hoy— el manifiesto devuelve exactamente la
#: misma cadena que había escrita aquí: `${PLATFORM_REGISTRY:-…}/<app>:${PLATFORM_IMAGE_TAG:-…}`.
#: Vacío significa vacío: el mecanismo degrada, no rompe. En cuanto una release
#: publique las seis y el pipeline resuelva sus digests, la misma llamada empieza
#: a devolver `…:v1.0.0@sha256:…` sin tocar una línea de este fichero.
#:
#: Se lee al IMPORTAR a propósito: un manifiesto corrupto tiene que doler aquí,
#: no en el host del operador cuando `docker compose pull` resuelva una
#: referencia inventada.
PLATFORM_IMAGES = load_platform_manifest()
APP_IMAGE_TAG = PLATFORM_IMAGES.tag_expression()
APP_IMAGE_REGISTRY = PLATFORM_IMAGES.registry_expression()


def app_image(app: str) -> str:
    """Imagen publicada de ``app``, pineada por digest si hay release.

    Punto de enganche único: todo `image:` de una app de este repo pasa por aquí,
    de modo que el día que existan digests no queda ningún servicio componiendo
    su referencia por su cuenta. La guarda que lo comprueba es
    `tests/unit/test_platform_images_wiring.py`.
    """
    return PLATFORM_IMAGES.reference(app)


# Dev-default markers the prod secret guard rejects (mirror of
# api_server.config._DEV_SECRET_MARKERS). The generated *production* compose
# must contain none of these.
_DEV_SECRET_MARKERS = ("changeme", "dev-only", "minioadmin")

# Compose top-level name (matches the canonical stack so `docker compose`
# treats a re-generated file as the same project).
PROJECT_NAME = "agentic-platform"

# ---------------------------------------------------------------------------
# Dónde viven los auxiliares que la instalación trae consigo.
#
# Toda ruta `./x` de este fichero resuelve contra el directorio donde el compose
# ACABA, que no es el repo: es la raíz de datos (`cli.py` → `compose_dir =
# config.storage.data_root`). Hasta el 2026-08-27 este generador arrastraba las
# rutas relativas del compose canónico —que sí vive junto a `docker/`— y encima
# con DOS bases implícitas incompatibles: `./postgres/init` y `./vault/config.hcl`
# sólo resolvían si el compose estaba en `docker/`, mientras que `./docker/seccomp`
# sólo resolvía si estaba en la raíz del repo. Ninguna de las dos era la real.
#
# El arreglo no es reescribir las rutas una a una hacia la base correcta: es que
# haya UNA sola base y un solo subárbol. Todo lo que la instalación escribe
# verbatim cuelga de aquí, y eso compra dos invariantes que antes dependían de la
# suerte:
#
#   1. **Nada aterriza dentro del almacén de datos de otro servicio.** El caso que
#      lo motiva: `./postgres/init` resolvía a `{data_root}/postgres/init`, o sea
#      DENTRO del PGDATA. Docker crea el lado host ausente de un bind como
#      directorio vacío, así que el PGDATA dejaba de estar vacío antes del
#      `initdb`: el cluster no se inicializaba, los cinco scripts de
#      `docker/postgres/init/` no corrían jamás y la base nacía sin `pgvector` y
#      sin los roles. Postgres salía `healthy`; la avería se cobraba en la primera
#      consulta que necesitaba la extensión.
#   2. **Se ve de un vistazo qué trajo el instalador y qué es estado.** `stack/`
#      es reproducible desde el paquete; el resto de la raíz de datos, no.
#
# El contenido de este subárbol viaja dentro del paquete Python
# (`installer_backend.stack_assets`) y lo escribe el paso GENERATE_CONFIG.
#
# El `caddy/Caddyfile` NO cuelga de aquí a propósito: no viaja verbatim, se GENERA
# a partir de la configuración de cada instalación (dominio, modo TLS), ya lo
# escribía GENERATE_CONFIG y no cae dentro de ningún almacén. `stack/` es «lo que
# el instalador trae dentro», no «todo lo que el instalador escribe».
# ---------------------------------------------------------------------------
#: Nombre del subdirectorio, relativo al directorio del compose.
STACK_ASSETS_DIR_NAME = "stack"

#: Prefijo con el que se montan. `./` explícito: compose exige que un bind
#: relativo empiece por `./` o `../`, o lo interpreta como volumen nombrado.
STACK = f"./{STACK_ASSETS_DIR_NAME}"

#: Canonical core services always present in the runtime stack.
CORE_SERVICES: tuple[str, ...] = (
    "postgres",
    "redis",
    "minio",
    "vault",
    "clamav",
    "docling-serve",
    "egress-proxy",
    "registry-proxy",
    "docker-socket-proxy",
    "migrations",
    "api-server",
    "orchestrator",
    "workers",
    # Auditoría 2026-09-01 (A-02): la lane que drena `test`/`review` sin servir
    # `default`, para que la espera síncrona de la fase de tests no se
    # inanicione en el pool genérico. Va en el NÚCLEO por el mismo motivo que
    # `workers-marketplace`: sin ella la fase de tests depende de que el pool
    # genérico tenga un slot libre justo cuando otro run lo ocupa esperando.
    "workers-aux",
    "workers-privileged",
    # prod-13 task_prod13_01: la lane de las puertas del marketplace. Va en el
    # NÚCLEO porque su cola se declara en `QUEUE_NAMES`: dejarla fuera dejaría
    # una cola sin consumidor justo en la instalación de producción, que es el
    # error que el ADR 0083 retiró para `heavy`/`gpu`.
    "workers-marketplace",
    "cortex-beat",
    "notification-dispatcher",
    # prod-08 task_prod08_watchdog_14: NÚCLEO, no overlay opcional. Es lo que
    # reinicia postgres/redis/minio/vault/clamav y los dos proxies cuando se
    # caen, y lo que avisa a un humano cuando no consigue levantarlos. Dejarlo
    # fuera del núcleo era lo que hacía que la instalación de producción —la que
    # nadie vigila— fuese el único despliegue SIN recuperación automática.
    "watchdog",
    "admin-panel",
    "caddy",
)

# ---------------------------------------------------------------------------
# El one-shot de finalización (ADR 0161, paso 8). Ver `_bootstrap_service`.
#
# NO entra en CORE_SERVICES a propósito, y no es un descuido: esa tupla es «lo
# que `docker compose up -d` levanta y lo que los diagramas de topología
# dibujan» (`tests/docs/test_diagram_guards.py`, `tests/unit/
# test_docs_governance.py`). Un one-shot que el operador ejecuta una vez y que
# sale no forma parte del stack que corre; dibujarlo como si lo fuera sería la
# misma clase de mentira que los servicios fantasma que esas guardas persiguen.
# Se añade en `selected_services`, siempre, porque siempre hace falta.
# ---------------------------------------------------------------------------
#: Nombre del servicio. Es el MISMO símbolo que imprime el banner del CLI
#: (`installer_backend.cli.BOOTSTRAP_SERVICE`): si los dos se separan, el
#: operador recibe un `no such service`.
BOOTSTRAP_SERVICE = "bootstrap"

#: Módulo que ejecuta el one-shot dentro de la imagen del api-server. Es la
#: costura con la otra mitad del paso 8; el contrato está escrito en el docstring
#: de `_bootstrap_service`.
BOOTSTRAP_ENTRYPOINT = "api_server.bootstrap"

#: Services added only when the monitoring overlay is requested. Mirrors
#: docker/docker-compose.monitoring.yml so a production install has the SAME
#: observability as dev — including Alertmanager (routes Prometheus' alert rules
#: to the platform notifier) and cAdvisor (per-container metrics).
MONITORING_SERVICES: tuple[str, ...] = (
    "prometheus",
    "textfile-init",
    "node-exporter",
    "alertmanager",
    "cadvisor",
    "grafana",
)

# ---------------------------------------------------------------------------
# Textfile collector de node-exporter — el ÚNICO camino por el que las métricas
# de APLICACIÓN llegan a Prometheus en este stack (no hay sidecar de
# instrumentación: un proceso deja un `.prom` en el drop-dir y node-exporter
# re-exporta sus muestras; ver `workers/textfile_collector.py`).
#
# POR QUÉ ESTÁ AQUÍ (2026-08-12): este generador NO cableaba nada de esto —ni el
# mount, ni el volumen, ni la bandera `--collector.textfile.directory`—, así que
# en una instalación hecha por el instalador `workers.sample_queue_metrics`
# escribía cada 30 s en un `/host/textfile/` INEXISTENTE dentro del contenedor.
# El writer trata un sink ausente como «topología sin monitorización» y calla a
# propósito (si no, inundaría el log ~2880 veces/día), de modo que la avería era
# SILENCIOSA: las cuatro series de aplicación
#
#     agentic_celery_queue_depth · agentic_tasks_by_status
#     agentic_dlq_depth          · agentic_executions_24h
#
# sencillamente no existían, y las CUATRO reglas de alerta montadas sobre ellas
# en docker/monitoring/prometheus/rules/app_alerts.yml (CeleryQueueGrowing,
# NotificationsDLQNotEmpty, ExecutionFailureRateHigh, TasksBlockedHigh) estaban
# cargadas y armadas sin poder disparar JAMÁS. Un dashboard vacío se nota; una
# alerta que no puede sonar —`agentic_dlq_depth > 0` es trabajo PERDIDO— parece
# que no hay nada que sonar. El stack de desarrollo sí lo hacía bien
# (docker-compose.monitoring.yml + docker-compose.monitoring.apps.yml); esto lo
# lleva a la instalación generada.
# ---------------------------------------------------------------------------
#: Volumen nombrado compartido: lo escriben las lanes de workers, lo lee
#: node-exporter. Mismo nombre que en docker-compose.monitoring.yml.
TEXTFILE_COLLECTOR_VOLUME = "node_exporter_textfile"

#: Punto de montaje. Es el default del código del worker
#: (`workers.config.queue_metrics_textfile_path` / `backup_metrics_textfile_path`
#: cuelgan de aquí), así que montándolo en esta ruta NO hace falta ninguna
#: variable de entorno extra.
TEXTFILE_COLLECTOR_DIR = "/host/textfile"

#: One-shot que deja el drop-dir en 1777 antes de que arranquen sus escritores.
TEXTFILE_INIT_SERVICE = "textfile-init"

#: Los servicios que ESCRIBEN ficheros `.prom`, y por tanto necesitan el drop-dir
#: montado en lectura-escritura. `workers` drena la cola `default`
#: (`sample_queue_metrics` cada 30 s + las métricas de curiosidad del córtex) y
#: `workers-privileged` la cola `privileged` (backup diario → `agentic_backup_*`,
#: la fuente de BackupLastRunFailed/BackupTooOld). Montar solo uno dejaría la
#: mitad de las series sin publicar, que es otra forma de mentir.
TEXTFILE_WRITER_SERVICES: tuple[str, ...] = ("workers", "workers-privileged")

# ---------------------------------------------------------------------------
# Healthcheck de los DOS tinyproxy (prod-08 task_prod08_egress_health_15 /
# deploy-9). Una sola constante para los dos servicios y COPIA LITERAL de la
# línea del compose canónico: son la misma imagen y el mismo demonio, y tenerlo
# escrito dos veces es exactamente cómo el egress-proxy y el registry-proxy
# heredaron el mismo defecto por copy-paste.
#
# Tres decisiones dentro de una línea, ninguna cosmética:
#
#  * **`|| exit 1`, no `|| true`.** Con `|| true` el comando SIEMPRE devolvía 0:
#    el contenedor salía `healthy` con tinyproxy muerto. Como el egress-proxy es
#    la única salida de los agent-runtimes hacia los LLM (ADR 0019), los agentes
#    se quedaban sin red y el stack no delataba la causa. Y desde que el watchdog
#    vigila los dos proxies (`task_prod08_watchdog_14`), un estado mentiroso
#    también desactiva la recuperación automática: no reinicia lo que cree sano.
#  * **`-Y off`, no `--no-proxy`.** La imagen lleva el wget de BusyBox, que NO
#    reconoce `--no-proxy`: salía por el mensaje de uso con rc≠0, así que este
#    healthcheck NUNCA fue válido. Invisible mientras hubo un `|| true` delante.
#    Portar sólo el final —el «arreglo de dos caracteres» que el plan dictó
#    durante tres pasadas— habría dejado los dos proxies permanentemente
#    `unhealthy` y al watchdog reiniciándolos en bucle: peor que no vigilar.
#  * **Se afirma el `403 Access denied`, no la palabra «tinyproxy».** El cuerpo
#    de la página de error no viaja con `-q`; la línea de estado sí. Un 403 a una
#    petición DIRECTA prueba que el demonio escucha y aplica su política (sólo
#    sirve peticiones proxificadas); caído, wget diría «Connection refused».
#
# Verificado contra el binario, no contra el YAML: con el stack arriba,
# `docker inspect` de agentic-egress-proxy y agentic-registry-proxy →
# `healthy`, `FailingStreak=0` (2026-08-12).
#
# Guardado por tests/unit/test_compose_healthchecks_honest.py, que exige que
# esta cadena sea IDÉNTICA a la del compose canónico — no parecida.
# ---------------------------------------------------------------------------
TINYPROXY_HEALTHCHECK_CMD = (
    "wget -q -O- -Y off http://127.0.0.1:8888/ 2>&1 | grep -q '403 Access denied' || exit 1"
)

# ---------------------------------------------------------------------------
# Buzón de credenciales del receiver de RESPALDO de Alertmanager (prod-08
# task_prod08_alert_fallback_02).
#
# POR QUÉ ESTÁ AQUÍ (2026-08-12): `monitoring/alertmanager/alertmanager.yml` —el
# MISMO fichero que este generador monta— declara el receiver de último recurso
# leyendo el webhook de Slack de un fichero
# (`api_url_file: /etc/alertmanager/secrets/slack_api_url`) en vez de incrustarlo:
# Alertmanager no expande `${ENV}` en su config y un webhook de Slack es una
# credencial. Pero declarar la ruta no es tenerla: hasta hoy este generador montaba
# exactamente dos cosas en el alertmanager —su `alertmanager.yml` y su directorio
# de estado—, así que en una instalación hecha por el instalador esa ruta NO
# EXISTÍA dentro del contenedor y el operador no tenía dónde dejar la credencial
# sin editar a mano un compose generado (justo lo que el runbook le pide no hacer).
#
# El fallo es del tipo caro: `api_url_file` se lee al NOTIFICAR, no al cargar la
# config, de modo que Alertmanager ARRANCA IGUAL, el stack entero sale `healthy` y
# el canal de respaldo falla en cada envío EN SILENCIO — precisamente en el único
# escenario para el que existe: el api-server caído, que no puede entregarse a sí
# mismo la alerta de que está caído. El stack de desarrollo lo cableó el
# 2026-08-10 (docker/docker-compose.monitoring.yml); esto lo lleva a la
# instalación generada.
# ---------------------------------------------------------------------------
#: Lado HOST, relativo al directorio del compose. Misma convención (y mismo árbol
#: `monitoring/` copiado junto al compose) que el resto de la configuración de
#: monitorización que ya se monta así: prometheus.yml, las reglas, alertmanager.yml
#: y el provisioning de Grafana.
ALERTMANAGER_SECRETS_HOST_DIR = f"{STACK}/monitoring/alertmanager/secrets"

#: Lado CONTENEDOR. No es una elección libre: es el directorio del que cuelga el
#: `api_url_file` del receiver `critical-fallback` en `alertmanager.yml`.
ALERTMANAGER_SECRETS_DIR = "/etc/alertmanager/secrets"

#: The in-stack Ollama service + its model-pull one-shot, added when
#: ``ollama_mode != "none"`` (ADR 0056). ``GPU_SERVICE`` is kept as a
#: backward-compatible alias of ``OLLAMA_SERVICE``.
OLLAMA_SERVICE = "ollama"
OLLAMA_BOOTSTRAP_SERVICE = "ollama-bootstrap"
GPU_SERVICE = OLLAMA_SERVICE

#: The in-stack voice services, added when ``voice_mode != "none"`` (ADR 0073).
#: ``stt`` = faster-whisper (POST /v1/audio/transcriptions), ``tts`` = Kokoro
#: (POST /v1/audio/speech). Both are reached internally by the api-server (which
#: also serves the córtex voice turn) at ``stt:8000`` / ``tts:8880``.
STT_SERVICE = "stt"
TTS_SERVICE = "tts"
VOICE_SERVICES: tuple[str, ...] = (STT_SERVICE, TTS_SERVICE)

#: Named volume that caches the Whisper model (downloaded on first use) so it
#: survives restarts instead of being re-pulled every boot. Matches the
#: canonical compose's ``whisper_models`` volume.
WHISPER_MODELS_VOLUME = "whisper_models"

#: Name of the AppArmor MAC profile every generated service pins via
#: ``security_opt: apparmor=…`` (Plan 15 task_15_16). Unlike seccomp (a path),
#: AppArmor profiles are referenced by the NAME they were loaded under with
#: ``apparmor_parser`` on the host. The installer ships
#: docker/apparmor/agentic-default.profile and the install/runbook step loads
#: it (real load is a host/HUMAN step — the kernel cannot be exercised in CI).
APPARMOR_DEFAULT_PROFILE = "agentic-default"

#: El perfil del `docker-socket-proxy`, y de ningún otro servicio.
#:
#: `agentic-default` deniega el socket de Docker a todo el mundo —Principio 2,
#: «a socket leak == host takeover»— y este servicio es el único que existe para
#: sostenerlo. Con el perfil compartido puesto, HAProxy arrancaba y sus
#: peticiones morían con `503 … SC--`: no alcanzaba su propio backend (medido en
#: el e2e run 33177824929).
#:
#: La alternativa —abrir el socket en el perfil compartido— se lo habría dado
#: también a los workers, que son quienes ejecutan código no confiable. Un
#: servicio roto cambiado por el agujero exacto que el Principio 2 cierra.
APPARMOR_SOCKET_PROXY_PROFILE = "agentic-socket-proxy"


def _logging_block() -> dict[str, Any]:
    """The capped json-file logging block every service shares."""

    return {
        "driver": "json-file",
        "options": {"max-size": "10m", "max-file": "5"},
    }


# Capabilities the official infra images need back on top of cap_drop:[ALL] to
# self-initialise: chown/chmod their data dir as root and drop to their service
# user via gosu/su-exec. Without them postgres/redis/clamav/egress-proxy
# crash-loop on start ("chmod/chown: Operation not permitted", "Permission
# denied", "Unable to change to group"). prod-01: the cap_drop baseline
# (task_08) was too broad for stateful official images — these add back ONLY the
# self-init caps, never the dangerous ones (NET_ADMIN, SYS_ADMIN, …). Mirrors the
# canonical compose's x-infra-caps anchor.
_INFRA_CAPS = ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"]


def _hardening(
    *,
    limits_cpus: str,
    limits_memory: str,
    cap_drop_all: bool = True,
    apparmor_profile: str = APPARMOR_DEFAULT_PROFILE,
) -> dict[str, Any]:
    """Platform hardening defaults applied to a generated service.

    ``cap_drop: [ALL]`` + ``no-new-privileges`` mirror the canonical compose's
    hardened services; the AppArmor MAC profile (``apparmor=agentic-default``,
    Plan 15 task_15_16) lets the host kernel deny the container-escape
    primitives; ``deploy.resources.limits`` caps CPU/memory so a runaway
    container can't starve the single host. A few infra images (Vault needs
    ``IPC_LOCK``) opt out of the blanket cap-drop via ``cap_drop_all=False``.

    These are TRUSTED first-party services: like the canonical
    ``docker-compose.yml`` (revised, ADR 0040) they rely on Docker's DEFAULT
    seccomp profile (NOT overridden). The hand-rolled default-deny allowlist
    SIGSEGV'd the Go services and broke postgres when force-applied here; it is
    reserved for the UNTRUSTED agent/test runtimes (the worker pins
    ``docker/seccomp/agent-runtime.json`` at launch). Operators who want the
    extra-hardening opt-in profile (``docker/seccomp/default.json``) can pin it
    after validating it on their own kernel.
    """

    block: dict[str, Any] = {
        "restart": "unless-stopped",
        "logging": _logging_block(),
        "security_opt": [
            "no-new-privileges:true",
            f"apparmor={apparmor_profile}",
        ],
        "deploy": {
            "resources": {"limits": {"cpus": limits_cpus, "memory": limits_memory}},
        },
    }
    if cap_drop_all:
        block["cap_drop"] = ["ALL"]
    return block


def _healthcheck(test: str, *, start_period: str = "30s", timeout: str = "10s") -> dict[str, Any]:
    """A CMD-SHELL healthcheck block (task_prod01_07). ``start_period`` gives a
    grace window for boot before failures count (Celery workers take longer)."""

    return {
        "test": ["CMD-SHELL", test],
        "interval": "30s",
        "timeout": timeout,
        "retries": 5,
        "start_period": start_period,
    }


def _http_healthcheck(url: str, *, start_period: str = "30s") -> dict[str, Any]:
    """HTTP liveness probe using python's stdlib (no shell, no external tool).

    The first-party app images are ``python:3.12-slim``, which ships NEITHER
    wget NOR curl. A wget-based healthcheck therefore marks api-server /
    orchestrator permanently unhealthy, so ``depends_on: service_healthy`` is
    never satisfied and the whole stack fails to come up (prod-01: verified live
    — the api-server only went healthy once the probe used python). Celery lanes
    use ``celery inspect ping`` (binary present in their image), so they keep the
    CMD-SHELL ``_healthcheck`` helper.
    """

    code = (
        "import urllib.request,sys;"
        f"sys.exit(0 if urllib.request.urlopen('{url}',timeout=5).status==200 else 1)"
    )
    return {
        "test": ["CMD", "python", "-c", code],
        "interval": "30s",
        "timeout": "5s",
        "retries": 5,
        "start_period": start_period,
    }


def _env_ref(var: str, dev_default: str | None, *, prod: bool) -> str:
    """Una referencia a ``VAR`` que en producción **aborta** si falta.

    Tres formas posibles, y sólo dos son aceptables:

    * ``${VAR:-default}`` — fail-OPEN. El despliegue arranca con el literal de
      desarrollo. Es el hallazgo ``secrets-6`` de prod-10; se conserva sólo
      fuera de producción, para las comodidades de dev.
    * ``${VAR}`` a secas — **también fail-open, y peor porque no lo parece**.
      Docker Compose avisa por stderr («variable is not set, defaulting to a
      blank string») y sigue adelante con la variable VACÍA. Esta función la
      emitía en modo prod mientras este mismo docstring prometía lo contrario
      (auditoría 2026-08-27). El daño concreto: con ``APP_USER_PASSWORD`` vacía
      sobre un PGDATA nuevo, ``stack/postgres/init/02-roles.sh`` hace
      ``${APP_USER_PASSWORD:-<literal de dev>}`` y bash trata la cadena vacía
      como ausente, así que el rol nace con la contraseña publicada en este
      repositorio. Y lo que ve el operador depende de qué variable se le caiga:
      unas revientan ruidosamente y otras no.
    * ``${VAR:?mensaje}`` — fail-CLOSED, y la que se emite en producción. El
      ``up`` aborta antes de arrancar un solo contenedor, con un mensaje que
      dice dónde poner la variable. Misma forma y mismo criterio que exige
      ``tests/unit/test_compose_no_default_credentials.py`` sobre el compose
      canónico; ``test_a_missing_credential_aborts_the_generated_stack`` lo
      exige sobre el generado.

    El mensaje nombra el ``.env`` a propósito: la interpolación ocurre al CARGAR
    el fichero, así que el aborto alcanza también a ``ps``, ``logs``, ``config`` y
    ``down`` — justo los comandos con los que alguien intentaría diagnosticarlo.
    Sin instrucción, ese aborto es una sesión de depuración.

    Y va en ASCII puro, que no es descuido: este texto se repite en ~30 valores
    del YAML generado, y una sola vocal acentuada obliga a PyYAML a volcarlos en
    estilo entrecomillado con escapes ``ó`` y continuaciones de línea. El
    fichero sigue siendo válido —y ``docker compose config`` lo acepta—, pero el
    artefacto que el ADR 0161 pide que el operador AUDITE antes de ejecutarlo se
    vuelve ilegible. Un fichero que no se puede leer no se audita.
    """

    if prod:
        return f"${{{var}:?falta {var} en el .env junto a este compose}}"
    if dev_default is None:
        return f"${{{var}}}"
    return f"${{{var}:-{dev_default}}}"


def _redis_dsn(db: int, *, prod: bool) -> str:
    """DSN autenticada contra el Redis del stack: ``redis://:<clave>@redis:6379/N``.

    La dirección es fija (la impone este compose), la credencial no: viaja como
    referencia al `.env`, como cualquier otro secreto. Redis no tiene usuario en
    este stack —sólo contraseña—, de ahí los dos puntos con el usuario vacío.

    Punto de enganche ÚNICO a propósito. Las once DSN de Redis del stack estaban
    escritas a mano, once veces, y por eso las once se quedaron sin credencial a
    la vez cuando prod-10 puso `requirepass` en el compose canónico. Con una sola
    función, añadir un consumidor nuevo sin credencial exige saltársela.

    Y la contraseña también es obligatoria DENTRO de la URL: con
    ``${REDIS_PASSWORD}`` a secas y la variable ausente, esto produce
    ``redis://:@redis:6379/1``, que es una URL perfectamente válida con
    contraseña vacía — un servicio que arranca y no se puede autenticar.
    """

    return f"redis://:{_env_ref('REDIS_PASSWORD', None, prod=prod)}@redis:6379/{db}"


# ---------------------------------------------------------------------------
# Individual service builders. Each returns a compose service mapping. They are
# parametrised by the wizard config (ports, data root, resources, prod-ness).
# ---------------------------------------------------------------------------
def _postgres_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    svc: dict[str, Any] = {
        "image": IMAGE_POSTGRES,
        "environment": {
            "POSTGRES_USER": _env_ref("POSTGRES_USER", "postgres", prod=prod),
            "POSTGRES_PASSWORD": _env_ref("POSTGRES_PASSWORD", None, prod=prod),
            "POSTGRES_DB": _env_ref("POSTGRES_DB", "agentic_platform", prod=prod),
            "POSTGRES_INITDB_ARGS": "--encoding=UTF8 --locale=C",
            "MIGRATIONS_USER_PASSWORD": _env_ref("MIGRATIONS_USER_PASSWORD", None, prod=prod),
            "APP_USER_PASSWORD": _env_ref("APP_USER_PASSWORD", None, prod=prod),
            # prod-14 task_prod14_04/05. La contraseña de `service_user`, el rol
            # BYPASSRLS SIN DDL con el que corren workers, orchestrator,
            # dispatcher y la superficie /admin. La consumen
            # `stack/postgres/init/04-service-role.sql` (que crea el rol con un
            # literal de desarrollo) y `05-service-role-password.sh` (que lo
            # corrige desde aquí). Faltaba, así que el init caía SIEMPRE al
            # literal — la llave que se salta la RLS de todos los tenants,
            # escrita en este repositorio y alcanzable desde cualquier contenedor
            # de `agentic-net`. Lo avisaba por el stderr del contenedor de
            # postgres, donde nadie mira.
            #
            # Es la misma regresión que prod-14 arregló en el compose canónico: su
            # guarda (`tests/security/test_service_user_password_is_wired.py`)
            # sigue verde porque sólo mira `docker/docker-compose.yml`, y el que
            # se instala en casa del operador es éste.
            "SERVICE_USER_PASSWORD": _env_ref("SERVICE_USER_PASSWORD", None, prod=prod),
        },
        "volumes": [
            f"{cfg.storage.data_root}/postgres:/var/lib/postgresql/data",
            # Los scripts de inicialización van bajo `stack/`, NO bajo
            # `{data_root}/postgres/...`: ahí caerían dentro del PGDATA de la línea
            # de arriba y el `initdb` se saltaría la inicialización entera.
            f"{STACK}/postgres/init:/docker-entrypoint-initdb.d:ro",
        ],
        "healthcheck": {
            "test": [
                "CMD-SHELL",
                "pg_isready -U ${POSTGRES_USER:-postgres} -d ${POSTGRES_DB:-agentic_platform}",
            ],
            "interval": "10s",
            "timeout": "5s",
            "retries": 5,
            "start_period": "30s",
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="2.0", limits_memory="2g"))
    svc["cap_add"] = list(_INFRA_CAPS)  # postgres self-inits PGDATA as root
    return svc


def _redis_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    """Redis 7 — sesiones de servidor, broker de Celery y contadores de rate limit.

    **Con `requirepass`, y no por higiene.** Esto NO es una caché de resultados:
    según la referencia del propio repo (`docs/04-reference/mandatory-env-vars.md`)
    ahí viven las SESIONES de servidor, el broker de Celery —o sea, la capacidad
    de encolar trabajo para los workers— y los contadores de rate limit. Corría
    sin autenticación: un `redis-cli` desde cualquier contenedor de `agentic-net`,
    o desde el propio host por la IP del bridge y sin necesidad de puerto
    publicado, leía sesiones vivas, encolaba ejecuciones y ponía los contadores a
    cero. El operador no veía nada, porque el stack funciona perfectamente.

    Es el hallazgo `secrets-7` de prod-10 otra vez: su guarda
    (`tests/unit/test_compose_redis_auth_and_dev_binds.py`) exige `--requirepass`
    en `docker/docker-compose.yml` y sigue verde, porque el compose que se
    instala —éste— no lo miraba nadie.

    Dos detalles que van juntos y no se pueden separar:

    * La contraseña es **obligatoria** (`${REDIS_PASSWORD:?…}` en prod): un
      despliegue que olvide la variable debe abortar, no quedarse abierto. Con
      `${REDIS_PASSWORD}` a secas arrancaría un `requirepass ''`.
    * El healthcheck se **autentica**. Con `requirepass`, un `redis-cli ping`
      pelado responde NOAUTH y sale != 0: el contenedor se quedaría `unhealthy`
      para siempre y todos los `depends_on: service_healthy` bloquearían el stack
      entero. Poner la contraseña sin arreglar la sonda cambia un agujero por una
      avería total. Se afirma el `PONG` porque `redis-cli -a … ping` devuelve 0
      aunque la respuesta sea un error.
    """

    svc: dict[str, Any] = {
        "image": IMAGE_REDIS,
        "command": [
            "redis-server",
            "--requirepass",
            _env_ref("REDIS_PASSWORD", None, prod=prod),
            "--appendonly",
            "yes",
            "--appendfsync",
            "everysec",
            "--save",
            "60 1",
            "--maxmemory",
            "${REDIS_MAX_MEM:-512mb}",
            "--maxmemory-policy",
            "allkeys-lru",
        ],
        "environment": {
            # Sólo para que el healthcheck de abajo pueda autenticarse DENTRO del
            # contenedor. `redis-server` no lee REDIS_PASSWORD del entorno: su
            # contraseña es la del `--requirepass` de arriba.
            "REDIS_PASSWORD": _env_ref("REDIS_PASSWORD", None, prod=prod),
        },
        "volumes": [f"{cfg.storage.data_root}/redis:/data"],
        "healthcheck": {
            "test": ["CMD-SHELL", 'redis-cli -a "$$REDIS_PASSWORD" ping | grep -q PONG'],
            "interval": "10s",
            "timeout": "3s",
            "retries": 5,
            "start_period": "10s",
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="1.0", limits_memory="1g"))
    svc["cap_add"] = list(_INFRA_CAPS)  # redis chowns /data + drops to redis user
    return svc


def _minio_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    svc: dict[str, Any] = {
        "image": IMAGE_MINIO,
        "command": 'server /data --console-address ":9001"',
        "environment": {
            "MINIO_ROOT_USER": _env_ref("MINIO_ROOT_USER", "minioadmin", prod=prod),
            "MINIO_ROOT_PASSWORD": _env_ref("MINIO_ROOT_PASSWORD", None, prod=prod),
        },
        "volumes": [f"{cfg.storage.data_root}/minio:/data"],
        "healthcheck": {
            "test": ["CMD", "mc", "ready", "local"],
            "interval": "10s",
            "timeout": "5s",
            "retries": 5,
            "start_period": "30s",
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="2.0", limits_memory="2g"))
    return svc


def _vault_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    # Vault drops ALL caps like every other service but adds IPC_LOCK back to
    # mlock its memory (matches the canonical compose's cap_drop:[ALL] +
    # cap_add:[IPC_LOCK] — one hardening criterion, prod-01 task_08).
    svc: dict[str, Any] = {
        "image": IMAGE_VAULT,
        # IPC_LOCK to mlock memory; SETFCAP because the entrypoint setcaps its
        # own binary; plus the self-init/user-drop caps (_INFRA_CAPS).
        "cap_add": ["IPC_LOCK", "SETFCAP", *_INFRA_CAPS],
        "environment": {
            "VAULT_ADDR": "http://0.0.0.0:8200",
            "VAULT_API_ADDR": "http://0.0.0.0:8200",
        },
        "volumes": [
            f"{cfg.storage.data_root}/vault/file:/vault/file",
            f"{cfg.storage.data_root}/vault/logs:/vault/logs",
            # Bind de FICHERO, no de directorio: si el lado host no existe, Docker
            # lo inventa como directorio y `vault server` no encuentra su config.
            # Lo escribe GENERATE_CONFIG desde `installer_backend.stack_assets`.
            f"{STACK}/vault/config.hcl:/vault/config/config.hcl:ro",
        ],
        "command": ["server"],
        "healthcheck": {
            "test": [
                "CMD-SHELL",
                "wget -qO- 'http://127.0.0.1:8200/v1/sys/health"
                "?standbyok=true&sealedcode=200&uninitcode=200' || exit 1",
            ],
            "interval": "10s",
            "timeout": "5s",
            "retries": 10,
            "start_period": "30s",
        },
        "networks": ["agentic-net"],
    }
    # cap_drop:[ALL] + cap_add:[IPC_LOCK] (above) — drop everything, add back
    # only the cap Vault needs to mlock memory. no-new-privileges + limits apply.
    #
    # `memlock` sin límite, y NO `disable_mlock` (2026-08-28). Vault bloquea su
    # memoria para que las claves no acaben en swap; el ADR 0145 se apoya en eso,
    # así que apagarlo para que arranque habría sido cambiar un fallo ruidoso por
    # una fuga silenciosa.
    #
    # Medido en el e2e (run 33175714605), con Postgres y Redis ya sanos:
    #
    #   vault-1 | Error initializing core: Failed to lock memory: cannot allocate memory
    #
    # ENOMEM de `mlock`, que es la firma del `RLIMIT_MEMLOCK` del host: un runner
    # Linux trae 64 KiB por defecto. En Docker Desktop no se reproduce —su
    # default es efectivamente ilimitado—, y por eso este fallo no aparece en
    # ninguna máquina de desarrollo Windows: sólo donde se instala de verdad.
    svc["ulimits"] = {"memlock": {"soft": -1, "hard": -1}}
    svc.update(_hardening(limits_cpus="1.0", limits_memory="512m"))
    return svc


def _clamav_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    svc: dict[str, Any] = {
        "image": IMAGE_CLAMAV,
        "environment": {"CLAMAV_NO_FRESHCLAMD": "false"},
        "volumes": [f"{cfg.storage.data_root}/clamav:/var/lib/clamav"],
        "healthcheck": {
            "test": ["CMD-SHELL", "clamdscan --version || exit 1"],
            "interval": "30s",
            "timeout": "10s",
            "retries": 5,
            "start_period": "120s",
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="2.0", limits_memory="2g"))
    svc["cap_add"] = list(_INFRA_CAPS)  # clamav chowns /var/lib/clamav + drops user
    return svc


def _docling_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    svc: dict[str, Any] = {
        "image": IMAGE_DOCLING,
        "environment": {"DOCLING_SERVE_ENABLE_UI": "false"},
        "healthcheck": {
            # GET, not --spider (HEAD): docling's /health rejects HEAD, so a
            # spider check wrongly marks it unhealthy though it serves 200 on GET.
            "test": ["CMD-SHELL", "wget -q -O /dev/null http://localhost:5001/health || exit 1"],
            "interval": "30s",
            "timeout": "5s",
            "retries": 5,
            "start_period": "60s",
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="2.0", limits_memory="4g"))
    return svc


def _egress_proxy_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    # On TWO networks: agentic-net (egress to internet) + the internal
    # agentic-agents (the only path the sandbox runtime has to a provider).
    svc: dict[str, Any] = {
        # ------------------------------------------------------------------
        # POR QUÉ EL CONTEXTO VIAJA, EN VEZ DE PUBLICAR LA IMAGEN (2026-08-27).
        #
        # Los dos tinyproxy son los únicos servicios del NÚCLEO que se construyen
        # en el destino, y había dos formas de que dejaran de pedir un contexto
        # que no existía: (a) que la instalación lo escriba —lo que se hizo— o
        # (b) referenciarlos como imagen publicada, como las seis de aplicación.
        #
        # (b) está DESCARTADA hoy, y no por gusto:
        #
        #   * **Nadie las publica.** `release-images.yml` publica seis imágenes de
        #     aplicación y ninguna es ésta; `ci.yml` las construye para pasarles
        #     Trivy y las tira. Poner `image:` sin `build:` no las hace existir:
        #     hace que `docker compose pull` —el paso PULL_IMAGES del propio
        #     wizard— intente bajarlas de un registro donde no están y salga con
        #     rc=1. Es EXACTAMENTE la regresión que este repo ya midió y revirtió
        #     el 2026-08-22 («poner `image:` rompió `docker compose pull`, y con
        #     él la instalación»), sólo que entonces se encontró antes de
        #     desplegar y aquí se estaría entregando como diseño.
        #   * **La decisión no es de este fichero.** Publicarlas es la pregunta 6
        #     del ADR 0161, que sigue `proposed`. Ese mismo ADR recomienda hacer
        #     primero el suelo —los auxiliares— y publicar después, y advierte de
        #     que publicarlas obliga además a mover dos guardas
        #     (`test_infra_images_are_scanned.py`). Decidirlo por implementación
        #     sería saltarse la cadena de precedencia del CLAUDE.md.
        #   * **`filter.txt` es política del operador, no un detalle de build.**
        #     Es la allowlist de hosts del ADR 0019, y su propia cabecera invita a
        #     editarla («si el operador usa un custom domain… lo añade aquí»). Con
        #     el contexto en disco eso es editar un fichero y reconstruir; con una
        #     imagen publicada, forkear el repo y esperar a un release ajeno.
        #
        # Y (a) no añade requisitos: el `up` construye la imagen que falta, y la
        # instalación ya necesita salida a internet para bajar el resto del stack.
        # El día que se firme el ADR 0161 y se publiquen, esto se sustituye por
        # `image:` + digest y el contexto deja de escribirse.
        # ------------------------------------------------------------------
        "build": f"{STACK}/egress-proxy",
        # `image:` explícito además del `build:`. Sin él la imagen se llama
        # `<proyecto>-egress-proxy`, y el proyecto lo elige cada instalación: el mismo
        # Dockerfile acabaría con un nombre distinto en cada host y ninguno
        # coincidiría con el que construye y escanea CI. Guardado por
        # tests/unit/test_infra_images_are_scanned.py.
        "image": "agentic-platform/egress-proxy:v1",
        # `pull_policy: build` va PEGADO al `image:`, no es decoracion. Con un
        # `image:` declarado, `docker compose pull` deja de saltarse el servicio e
        # intenta bajarlo de Docker Hub, donde no existe: rc=1. Y ese `pull` es el
        # paso PULL_IMAGES del propio wizard (real_step_executor.py), asi que la
        # instalacion abortaria. Medido: sin esta linea rc=1 «pull access denied»,
        # con ella rc=0 «Skipped». Guardado por
        # tests/unit/test_infra_images_are_scanned.py.
        "pull_policy": "build",
        "container_name": "agentic-egress-proxy",
        "healthcheck": {
            "test": ["CMD-SHELL", TINYPROXY_HEALTHCHECK_CMD],
            "interval": "30s",
            "timeout": "5s",
            "retries": 3,
        },
        "networks": ["agentic-net", "agentic-agents"],
    }
    svc.update(_hardening(limits_cpus="0.5", limits_memory="256m"))
    svc["cap_add"] = list(_INFRA_CAPS)  # tinyproxy setgid/setuid drop on start
    return svc


def _registry_proxy_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    # ADR 0094: egress allowlisted de los runtime-templates a los registries de
    # paquetes públicos. SOLO en agentic-net (egress a internet); NUNCA en
    # agentic-agents — el agent-runtime no debe alcanzar github/pypi/etc. El
    # worker lo conecta a los bridges efímeros per-task de los runtimes.
    svc: dict[str, Any] = {
        # Contexto de build escrito por la instalación, por las mismas tres
        # razones que el egress-proxy (ver el comentario largo de arriba): las
        # imágenes no se publican, publicarlas es la pregunta 6 de un ADR todavía
        # `proposed`, y su `filter.txt` es la allowlist de registros de paquetes
        # que el operador ajusta a su red (ADR 0094).
        "build": f"{STACK}/registry-proxy",
        # `image:` explícito por lo mismo que el egress-proxy: sin él el nombre
        # lo pone el proyecto de cada instalación y no coincide con el de CI.
        "image": "agentic-platform/registry-proxy:v1",
        "pull_policy": "build",
        "container_name": "agentic-registry-proxy",
        "healthcheck": {
            "test": ["CMD-SHELL", TINYPROXY_HEALTHCHECK_CMD],
            "interval": "30s",
            "timeout": "5s",
            "retries": 3,
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="0.5", limits_memory="256m"))
    svc["cap_add"] = list(_INFRA_CAPS)  # tinyproxy setgid/setuid drop on start
    return svc


def _docker_socket_proxy_service(
    cfg: InstallerConfig,  # noqa: ARG001 — uniform builder signature
    *,
    prod: bool,  # noqa: ARG001 — uniform builder signature
) -> dict[str, Any]:
    """Least-privilege Docker API gateway (Plan prod-01 task_09 / sandbox-1, ADR
    0060). The workers must launch ephemeral runtime containers, but handing them
    the raw ``/var/run/docker.sock`` is a full host-root escape (Principio 2). So
    this proxy holds the socket (read-only mount) and exposes a TCP API on a
    DEDICATED internal network with a per-endpoint ACL: containers/images/
    networks + POST + EXEC are allowed (create + wire runtimes, and run the
    acceptance checks / `pre_install` / `stack_exec` bridge INSIDE them, ADR
    0093); volumes/swarm and everything else are denied.

    EXEC=1 is not a relaxation added lightly (audit 2026-09-01, B-01): with
    EXEC=0 every `exec_run` of `TestRuntimeRunner` answered 403 in a
    wizard-generated stack — checks came back as `runtime_launch_failed`,
    `stack_exec` errored, sidecars never passed their healthcheck — and this
    generator's own test pinned the broken value while
    `docker-compose.manuals.yml` had already learnt the lesson. The ACL is
    still least-privilege: the proxy lives on `agentic-docker` (workers only)
    and the agent-runtime never touches the socket (Principio 2).
    """

    svc: dict[str, Any] = {
        "image": IMAGE_DOCKER_SOCKET_PROXY,
        "environment": {
            # Allow only what launching a sandbox runtime — and running the
            # toolchain inside it — needs.
            "CONTAINERS": "1",
            "IMAGES": "1",
            "NETWORKS": "1",
            "POST": "1",
            "EXEC": "1",
            # Deny the dangerous surface explicitly (defaults are 0, pinned for
            # clarity + as a regression guard).
            "VOLUMES": "0",
            "SWARM": "0",
            "SECRETS": "0",
            "CONFIGS": "0",
            "NODES": "0",
            "SERVICES": "0",
            "TASKS": "0",
            "PLUGINS": "0",
            "SYSTEM": "0",
            "INFO": "0",
        },
        "volumes": ["/var/run/docker.sock:/var/run/docker.sock:ro"],
        "healthcheck": _healthcheck(
            "wget -q --spider http://localhost:2375/_ping || exit 1", start_period="10s"
        ),
        # Dedicated internal net ONLY (no agentic-net, no agentic-agents): only
        # the workers reach the Docker API, never the untrusted runtimes.
        "networks": ["agentic-docker"],
    }
    # Su propio perfil, no el compartido: ver APPARMOR_SOCKET_PROXY_PROFILE.
    svc.update(
        _hardening(
            limits_cpus="0.5",
            limits_memory="256m",
            apparmor_profile=APPARMOR_SOCKET_PROXY_PROFILE,
        )
    )
    return svc


def _app_environment(cfg: InstallerConfig, prefix: str, *, prod: bool) -> dict[str, Any]:
    """Config EVERY platform app service reads, emitted PREFIXED with that
    service's pydantic ``env_prefix`` (``API_SERVER_`` / ``ORCHESTRATOR_`` /
    ``WORKERS_`` / ``NOTIFY_``).

    Emitting these UNprefixed (the old behaviour) meant the runtime — which reads
    ``<PREFIX><FIELD>`` — silently fell back to its dev default and the prod
    dev-secret guard never even saw ``environment=prod`` (finding secrets-2,
    deploy-3 pata 1). Only the two keys read by every service live here; each
    service builder adds its own keys (see ``_app_env`` usage). Secrets are
    ``${ENV}`` references only (no ``:-default`` in prod → fail loud).
    """

    return {
        # `.runtime_value`, NO `.value`: el enum del wizard dice `production` y el
        # runtime solo acepta {dev, staging, prod}. Emitirlo en crudo impedía
        # arrancar al api-server generado por el instalador en cuanto el guard de
        # `environment` pasó a fail-closed (prod-09 task_02).
        f"{prefix}ENVIRONMENT": cfg.system.environment.runtime_value,
        # Reference the per-service DSN the .env carries (config_generators
        # writes one per service: api-server gets the app role, workers/notify
        # the migrations role, etc.) — NOT a shared bare var.
        f"{prefix}DATABASE_URL": _env_ref(f"{prefix}DATABASE_URL", None, prod=prod),
    }


def _migrations_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    """One-shot that runs ``alembic upgrade head`` before the apps start (Plan
    prod-01 task_12 / deploy-6). Uses the api-server image (it ships the
    migrations + alembic) as the migrations role (``ADMIN_DATABASE_URL``,
    BYPASSRLS). env.py takes a ``pg_advisory_xact_lock`` so concurrent runs
    serialize. The app services ``depends_on`` it with
    ``service_completed_successfully`` (wired in :func:`generate_compose`)."""

    svc: dict[str, Any] = {
        "image": app_image("api-server"),
        "command": "alembic upgrade head",
        "environment": {
            # Alembic reads DATABASE_URL; migrations run as the migrations role.
            "DATABASE_URL": _env_ref("ADMIN_DATABASE_URL", None, prod=prod),
        },
        "depends_on": {"postgres": {"condition": "service_healthy"}},
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="1.0", limits_memory="512m"))
    svc["restart"] = "no"  # one-shot: run once and exit
    return svc


def _api_server_env(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    """El entorno completo de un proceso que corre con el paquete ``api_server``.

    Sale de ``_api_server_service`` para que el one-shot ``bootstrap`` —misma
    imagen, mismas ``api_server.config.Settings``— lo herede ENTERO en vez de
    llevar una copia recortada a mano. La copia recortada es el modo de fallo:
    ``Settings`` es fail-closed en producción, así que una variable que falte no
    degrada una función, impide construir el objeto y el contenedor muere antes
    de hacer nada. Y aquí «antes de hacer nada» puede significar «después de que
    Vault haya emitido las unseal keys».
    """

    env = _app_environment(cfg, "API_SERVER_", prod=prod)
    env.update(
        {
            "API_SERVER_ADMIN_DATABASE_URL": _env_ref(
                "API_SERVER_ADMIN_DATABASE_URL", None, prod=prod
            ),
            # In-stack service URLs: la DIRECCIÓN la fija este compose, así que
            # va literal; la CREDENCIAL no, así que va por referencia al `.env`
            # (ver `_redis_dsn`). Vault y MinIO no llevan credencial en la URL.
            # Redis logical DBs: 0 cache, 1 broker, 2 result.
            "API_SERVER_REDIS_URL": _redis_dsn(0, prod=prod),
            "API_SERVER_BROKER_URL": _redis_dsn(1, prod=prod),
            "API_SERVER_RESULT_BACKEND": _redis_dsn(2, prod=prod),
            "API_SERVER_VAULT_URL": "http://vault:8200",
            "API_SERVER_MINIO_URL": "http://minio:9000",
            # TODA la salida a Internet del api-server por el proxy con allowlist:
            # las web tools del córtex (ADR 0067) y la prueba de conexión de un MCP
            # remoto (ADR 0165 D9). El hermano de `WORKERS_EGRESS_PROXY_URL`, y hasta
            # la corrección A1 del addendum de ese ADR NO EXISTÍA: el api-server se
            # quedaba con el default de `api_server.config` (`http://localhost:8888`,
            # que dentro del contenedor no es nada), así que la proxificación de
            # «Probar conexión» no habría funcionado en ninguna instalación salida
            # del wizard. Alcanzable: este servicio va en `agentic-net` +
            # `agentic-agents` (`_api_server_service`) y el egress-proxy está en esas
            # dos mismas redes (`_egress_proxy_service`), así que el nombre
            # `egress-proxy` resuelve por el DNS del compose.
            "API_SERVER_EGRESS_PROXY_URL": "http://egress-proxy:8888",
            # Secrets: reference the per-service prefixed .env var that
            # config_generators.build_env_vars writes (the compose↔.env contract
            # is asserted by tests/unit/test_compose_env_contract.py). VAULT_TOKEN
            # is NOT here: it is optional (default None) and injected by the Vault
            # bootstrap (task 15_09), not the .env.
            "API_SERVER_JWT_SECRET": _env_ref("API_SERVER_JWT_SECRET", None, prod=prod),
            # ADR 0136: secreto DEDICADO de los tokens internos del sandbox. Sin él
            # el api-server NO ARRANCA en prod (guard fail-closed de anti-defaults),
            # y su ausencia en el generador es la que dejó el stack sin levantar.
            "API_SERVER_INTERNAL_TOKEN_SECRET": _env_ref(
                "API_SERVER_INTERNAL_TOKEN_SECRET", None, prod=prod
            ),
            # NOTIF-2: Bearer del ingest de Alertmanager (fail-closed sin el).
            "API_SERVER_ALERTS_INGEST_TOKEN": _env_ref(
                "API_SERVER_ALERTS_INGEST_TOKEN", None, prod=prod
            ),
            "API_SERVER_MINIO_ACCESS_KEY": _env_ref("API_SERVER_MINIO_ACCESS_KEY", None, prod=prod),
            "API_SERVER_MINIO_SECRET_KEY": _env_ref("API_SERVER_MINIO_SECRET_KEY", None, prod=prod),
            "API_SERVER_SSO_ENCRYPTION_KEY": _env_ref(
                "API_SERVER_SSO_ENCRYPTION_KEY", None, prod=prod
            ),
            "API_SERVER_NOTIFICATION_ENCRYPTION_KEY": _env_ref(
                "API_SERVER_NOTIFICATION_ENCRYPTION_KEY", None, prod=prod
            ),
            "API_SERVER_REVIEW_URL_SIGNING_SECRET": _env_ref(
                "API_SERVER_REVIEW_URL_SIGNING_SECRET", None, prod=prod
            ),
            "API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEY": _env_ref(
                "API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEY", None, prod=prod
            ),
            # Public base URL the IdP redirects the BROWSER back to. Carries the
            # reverse proxy's /api prefix (ADR 0061): the IdP returns to
            # https://{domain}/api/auth/sso/oidc/callback and Caddy's
            # handle_path /api/* strips /api before reaching the api-server (the
            # app's dev default localhost:8001 is wrong for prod).
            "API_SERVER_SSO_REDIRECT_BASE_URL": f"https://{cfg.system.domain}/api",
        }
    )
    return env


def _bootstrap_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    """El one-shot de FINALIZACIÓN: init de Vault + siembra del tenant + revelado.

    Es el segundo de los dos comandos que el CLI le deja al operador (ADR 0161,
    opción D). Existe como servicio del compose, y no como una capacidad del
    contenedor del instalador, por una razón concreta: **Vault y postgres sólo
    son alcanzables desde dentro de `agentic-net`**, y el instalador corre con
    `network_mode: none` y sin socket de Docker, que es justo lo que hace que la
    opción D no necesite una excepción al ADR 0060.

    Hasta el 2026-08-27 este servicio NO estaba declarado, y el banner del CLI lo
    mandaba ejecutar igualmente. Lo que recibía el operador era
    `no such service: bootstrap` sobre un stack `Up (healthy)` —el healthcheck de
    Vault acepta `sealedcode=200&uninitcode=200` a propósito— con Vault sin
    inicializar y sellado, sin tenant, sin usuario admin y sin ningún revelado de
    credenciales. La instalación PARECE terminada y no lo está, que es el peor
    modo de fallo que puede tener un instalador.

    Las cinco decisiones de este bloque, y por qué cada una:

    * **`profiles: [bootstrap]`** — separa «se ejecuta una vez, a mano» de
      «arranca con el stack». Sin el perfil, `docker compose up -d --wait` lo
      lanzaría en CADA arranque del host: un one-shot reintentando inicializar
      Vault en cada reinicio, y un `--wait` esperando a un contenedor que sale.
      `docker compose run` activa solo el perfil del servicio que nombra, así que
      el comando del banner sigue funcionando tal cual.
    * **`restart: "no"`** — un one-shot con reinicio automático es un bucle.
    * **La imagen del api-server** — es la que trae los seeds
      (`api_server.seeds`, `api_server.seeds.init_tenant`) y `hvac`. Publicar una
      séptima imagen para tres comandos sería una cosa más que versionar, escanear
      y pinear por digest.
    * **`depends_on` con las tres condiciones** — Vault arriba (aunque SELLADO:
      su healthcheck lo acepta a propósito, y desellar es precisamente parte del
      trabajo de este one-shot), postgres sano, y el esquema YA migrado. Sembrar
      antes de Alembic falla con `relation "organizations" does not exist`, y ése
      es el peor momento para descubrirlo: después de que Vault haya emitido unas
      unseal keys que se muestran EXACTAMENTE UNA VEZ.
    * **`INIT_ADMIN_PASSWORD` NO viaja en el entorno** — la contraseña del primer
      System Owner la mintea el propio one-shot y la enseña una vez por stdout.
      Ponerla en el compose la escribiría en el `.env` del host: legible por
      cualquiera que ya lo tenga, y superviviente a la sesión. Eso no es un
      revelado único, es un secreto en un fichero.

    **La otra mitad de esta costura vive fuera de este módulo.**
    :data:`BOOTSTRAP_ENTRYPOINT` nombra el módulo que la imagen del api-server
    tiene que exponer, y su contrato es exactamente lo que promete el banner del
    CLI: (1) `vault operator init` + unseal + KV v2 + políticas por servicio,
    idempotente en el límite del init —una Vault ya inicializada NO se re-inicia,
    que sería destructivo y sin recuperación—; (2) `api_server.seeds` +
    `api_server.seeds.init_tenant` con el tenant y el email de aquí y una
    contraseña CSPRNG minteada dentro; (3) el revelado por stdout, una vez, de
    las unseal keys, el root token y esa contraseña. Se nombra como símbolo, y no
    como cadena suelta, por la misma razón por la que `cli.py` hizo lo propio con
    el nombre del servicio: es el único sitio donde las dos mitades se tocan.

    **Ese módulo ya existe desde el 2026-08-28**
    (``apps/api-server/src/api_server/bootstrap/``), así que las dos mitades se
    tocan de verdad y el banner del CLI vuelve a ordenar el comando sin reservas.

    Este bloque se declaró un día antes, con la otra mitad todavía sin escribir, y
    conviene dejar dicho por qué: declarar el servicio sin el módulo no tapaba el
    agujero, lo movía a un sitio mejor. Sin el servicio, el operador recibía
    `no such service: bootstrap` —un error que apunta a su Docker— y no tenía
    dónde mirar; con él, recibía un `No module named api_server.bootstrap` que
    nombra la pieza que falta y la imagen donde debe estar. Hoy ese mensaje sigue
    siendo el diagnóstico correcto para una imagen del api-server ANTERIOR al
    módulo, que es el único caso en que puede volver a aparecer.
    """

    env = _api_server_env(cfg, prod=prod)
    env.update(
        {
            # Los argumentos del one-shot, no ajustes del runtime: van sin el
            # prefijo `API_SERVER_` a propósito, porque no son campos de
            # `api_server.config.Settings` y emitirlos con el prefijo haría creer
            # al contrato de prefijos que hay dos campos que no existen.
            "AGENTIC_BOOTSTRAP_TENANT_NAME": cfg.tenant.tenant_name,
            "AGENTIC_BOOTSTRAP_ADMIN_EMAIL": str(cfg.tenant.admin_email),
        }
    )
    svc: dict[str, Any] = {
        "image": app_image("api-server"),
        "command": ["python", "-m", BOOTSTRAP_ENTRYPOINT],
        # El almacén de artefactos del marketplace, y SÓLO él.
        #
        # La siembra de listings los escribe (`marketplace/seed.py:414`) y sin
        # este montaje el `mkdir` fallaba con `Permission denied: '/data'`,
        # tumbando la instalación en su ÚLTIMO paso, con Vault ya inicializado
        # y el revelado ya emitido (e2e run 33195432130).
        #
        # Se monta el subárbol, no la raíz: la api-server no monta `/data` a
        # propósito —las operaciones de git y de disco van al worker, y hay una
        # regresión documentada por saltarse eso— y este one-shot corre con su
        # misma imagen. Un almacén de artefactos no es el árbol de worktrees.
        "volumes": [f"{cfg.storage.data_root}/marketplace:/data/agent-platform/marketplace"],
        "environment": env,
        # Fuera del `up`: lo ejecuta el operador, una vez, con
        # `docker compose run --rm bootstrap`.
        "profiles": [BOOTSTRAP_SERVICE],
        "depends_on": {
            "postgres": {"condition": "service_healthy"},
            "vault": {"condition": "service_healthy"},
            "migrations": {"condition": "service_completed_successfully"},
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="1.0", limits_memory="1g"))
    svc["restart"] = "no"  # one-shot: corre una vez y sale
    return svc


def _api_server_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    env = _api_server_env(cfg, prod=prod)
    svc: dict[str, Any] = {
        "image": app_image("api-server"),
        "environment": env,
        # No host ports: the TLS reverse proxy (caddy) is the only published
        # surface (ADR 0061 / deploy-7); api-server is reached internally on
        # agentic-net as api-server:8000.
        "depends_on": {
            "postgres": {"condition": "service_healthy"},
            "redis": {"condition": "service_healthy"},
            "vault": {"condition": "service_healthy"},
        },
        "healthcheck": _http_healthcheck("http://localhost:8000/healthz"),
        # agentic-agents (internal) so the sandbox runtimes can reach the
        # internal API directly, bypassing the egress-proxy (ADR 0060 B1,
        # task_11). The PUBLIC surface stays on agentic-net / behind the TLS
        # reverse proxy (Fase E).
        "networks": ["agentic-net", "agentic-agents"],
    }
    svc.update(_hardening(limits_cpus="2.0", limits_memory="2g"))
    return svc


def _orchestrator_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    env = _app_environment(cfg, "ORCHESTRATOR_", prod=prod)
    env.update(
        {
            "ORCHESTRATOR_REDIS_URL": _redis_dsn(0, prod=prod),
            "ORCHESTRATOR_BROKER_URL": _redis_dsn(1, prod=prod),
        }
    )
    svc: dict[str, Any] = {
        "image": app_image("orchestrator"),
        "environment": env,
        "healthcheck": _http_healthcheck("http://localhost:8002/healthz"),
        "depends_on": {
            "postgres": {"condition": "service_healthy"},
            "redis": {"condition": "service_healthy"},
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="1.0", limits_memory="1g"))
    return svc


# Celery queue split (mirror of workers.celery_app.QUEUE_NAMES — the unit test
# cross-checks this against the real queue topology so it cannot drift). The
# generic pool drains every non-privileged queue; the ``privileged`` queue
# (backups, key rotation — touches Vault/secrets) is drained ONLY by the
# singleton workers-privileged lane under the strictest profile, never the
# generic pool (runbook 06-capacity-management.md). ``heavy``/``gpu`` removed by
# ADR 0083 (prod-06 colas_02) — dead lanes on a single host.
_WORKER_GENERIC_QUEUES = "default,ingestion,test,review"
_WORKER_PRIVILEGED_QUEUE = "privileged"
# Auditoría 2026-09-01 (A-02): la fase de tests espera de forma SÍNCRONA
# (`AsyncResult.get()` bajo `allow_join_result`) dentro del task `run_execution`
# de la lane `default`. Si la cola `test` la sirviera SÓLO el pool genérico, dos
# runs esperando a la vez ocuparían los dos slots y la fase de tests no tendría
# dónde correr: inanición hasta agotar el presupuesto. El compose de dev lleva
# `workers-aux` (`--queues=test,review`) por ese mismo motivo, medido (run
# 019f252e); el generado por el instalador —el de producción— no lo tenía.
_WORKER_AUX_QUEUES = "test,review"
# prod-13 task_prod13_01: la lane de las puertas de seguridad del marketplace
# (bandit + semgrep + prueba de humo del sandbox). Lane propia porque un trabajo
# de ~4 min no cabe en los pools de arriba sin arriesgar la inanición que
# documenta `workers-aux` en el compose de dev. Declararla en `QUEUE_NAMES` sin
# drenarla aquí sería la cola muerta que el ADR 0083 retiró para heavy/gpu, y
# `tests/unit/test_compose_generator.py` compara ambas cosas.
_WORKER_MARKETPLACE_QUEUE = "marketplace"

# Both worker lanes sit on three nets (task_09): agentic-net (general), the
# internal agentic-agents (reach the egress-proxy + the runtimes they launch),
# and the internal agentic-docker (reach the docker-socket-proxy).
_WORKER_NETWORKS = ["agentic-net", "agentic-agents", "agentic-docker"]


def _workers_env(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    """The WORKERS_* environment shared by both worker lanes (same Settings)."""
    env = _app_environment(cfg, "WORKERS_", prod=prod)
    env.update(
        {
            "WORKERS_BROKER_URL": _redis_dsn(1, prod=prod),
            "WORKERS_RESULT_BACKEND": _redis_dsn(2, prod=prod),
            # prod-01 A10 (auditoría 2026-07-06): DB 0, la MISMA que lee el WS del
            # api-server (API_SERVER_REDIS_URL) y el orchestrator — los streams
            # exec:{id} del worker se publican aquí. Con /3 (sin consumidor) el
            # streaming en vivo de logs quedaba roto (manuals.yml ya lo corrigió).
            "WORKERS_EVENTS_REDIS_URL": _redis_dsn(0, prod=prod),
            "WORKERS_DATA_ROOT": cfg.storage.data_root,
            # Docker API via the least-privilege proxy, never the raw socket
            # (task_09, ADR 0060). DOCKER_HOST is read by the docker SDK itself,
            # not a WORKERS_ Settings field, so it stays unprefixed.
            "DOCKER_HOST": "tcp://docker-socket-proxy:2375",
            # Launched runtimes reach LLM providers only through the egress
            # allowlist proxy (field egress_proxy_url).
            "WORKERS_EGRESS_PROXY_URL": "http://egress-proxy:8888",
            # STRICT profiles the worker pins onto the UNTRUSTED runtimes it
            # launches (task_10 / sandbox-2). Without these the defaults are ""
            # and the sandboxes run with Docker's default profiles. The seccomp
            # JSON is bind-mounted by _workers_volumes; the AppArmor profile is
            # referenced by the NAME loaded on the host (runbook + installer
            # prereq load docker/apparmor/agent-runtime.profile).
            "WORKERS_SECCOMP_PROFILE_PATH": "/etc/agentic/seccomp/agent-runtime.json",
            "WORKERS_APPARMOR_PROFILE": "agent-runtime",
            # `task_cv_44`: las imágenes que el worker lanza salen del manifiesto
            # de la release (pineadas por digest cuando la hay), no de un tag
            # local que nadie publica.
            "WORKERS_AGENT_RUNTIME_IMAGE": app_image("agent-runtime"),
            "WORKERS_BROWSER_RUNTIME_IMAGE": app_image("browser-runtime"),
            # Backup wiring (workers-6 / prod-04 task_prod_04_09). Los VALORES
            # los pone `config_generators._backup_env`, que es quien conoce el
            # layout de binds de ESTE compose; aquí solo se referencian. Antes se
            # copiaba `WORKERS_DATABASE_URL` (una URL de SQLAlchemy que `pg_dump`
            # no entiende) y se dejaba que `WORKERS_BACKUP_VOLUMES` heredase los
            # named volumes del stack de manuales, que aquí no existen: el backup
            # diario de una instalación por el instalador fallaba cada noche.
            "WORKERS_BACKUP_DATABASE_URL": _env_ref("WORKERS_BACKUP_DATABASE_URL", None, prod=prod),
            "WORKERS_BACKUP_BIND_PATHS": _env_ref("WORKERS_BACKUP_BIND_PATHS", None, prod=prod),
            "WORKERS_BACKUP_PROJECTS_ROOT": _env_ref(
                "WORKERS_BACKUP_PROJECTS_ROOT", None, prod=prod
            ),
            # task_prod_04_06: Redis con artefacto propio (BGREWRITEAOF + tar del
            # appendonlydir) y el árbol de Vault con captura verificada estable.
            "WORKERS_BACKUP_REDIS_DIR": _env_ref("WORKERS_BACKUP_REDIS_DIR", None, prod=prod),
            "WORKERS_BACKUP_STABLE_SNAPSHOT_PATHS": _env_ref(
                "WORKERS_BACKUP_STABLE_SNAPSHOT_PATHS", None, prod=prod
            ),
            # Cifrado en reposo: OFF de fábrica, y no por descuido. El motor es
            # fail-closed (task_prod_04_07): con el cifrado encendido y sin huella
            # de custodia declarada, el backup falla ANTES del dump — y con razón,
            # porque un bundle cifrado cuya clave no está custodiada es
            # irrecuperable. Un instalador no puede depositar una clave en un sobre
            # sellado, así que encenderlo aquí solo producía un stack cuyo backup
            # fallaba todas las noches. El opt-in en dos pasos (generar clave →
            # custodiarla → encender) está en docs/06-runbooks/dr-manual-backup.md.
            "WORKERS_BACKUP_ENCRYPTION_ENABLED": "false",
            "WORKERS_BACKUP_ENCRYPTION_VAULT_KEY": "agentic-platform/backups/encryption-key",
            # --- las DOS variables con prefijo AJENO que el worker necesita ---
            # El worker mintea el token del sandbox (`AGENTIC_INTERNAL_TOKEN`)
            # importando `mint_agent_token` del paquete del api-server, así que ese
            # camino lee `api_server.config` y sus variables `API_SERVER_*`. Es la
            # excepción al contrato de prefijos: el worker corre DOS clases de
            # Settings. Sin estas dos, el stack generado tenía dos averías:
            #
            #   1. sin `API_SERVER_INTERNAL_TOKEN_SECRET`, el worker firmaba con el
            #      default de dev y el api-server —que sí lleva el real— rechazaba
            #      el token: el sandbox no podía llamar a `/internal/agent/*`;
            #   2. sin `API_SERVER_ENVIRONMENT`, ese `Settings` se creía en `dev`,
            #      así que los guards anti-defaults NO disparaban dentro del worker
            #      y la avería (1) ocurría en silencio en vez de al arrancar.
            #
            # El valor del secreto es el MISMO que el del api-server a propósito:
            # los dos lados verifican la misma firma. Lo que NO puede compartir es
            # `API_SERVER_JWT_SECRET`, que ya no viaja al worker (ADR 0136).
            "API_SERVER_ENVIRONMENT": cfg.system.environment.runtime_value,
            "API_SERVER_INTERNAL_TOKEN_SECRET": _env_ref(
                "API_SERVER_INTERNAL_TOKEN_SECRET", None, prod=prod
            ),
        }
    )
    return env


def _workers_volumes(cfg: InstallerConfig) -> list[str]:
    """Binds both worker lanes need: the data root (bare repos + per-task git
    worktrees, same path in/out so worktree paths resolve) and the seccomp
    profiles the worker pins onto the UNTRUSTED runtimes it launches
    (``docker/seccomp/agent-runtime.json``; ``WORKERS_SECCOMP_PROFILE`` points
    at it — set in task_prod01_10)."""
    return [
        f"{cfg.storage.data_root}:{cfg.storage.data_root}",
        # Los perfiles los escribe GENERATE_CONFIG bajo `stack/`. Antes ponía
        # `./docker/seccomp`, que sólo resolvía si el compose viviera en la raíz
        # del repo — una segunda base implícita, distinta de la que usaban las
        # demás rutas relativas de este mismo fichero, y falsa las dos.
        f"{STACK}/seccomp:/etc/agentic/seccomp:ro",
    ]


def _workers_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    # worker_replicas / worker_memory_gib come from the wizard's ResourceConfig
    # (parametrised resource allocation, task 15_03).
    mem = f"{cfg.resources.worker_memory_gib}g"
    svc: dict[str, Any] = {
        "image": app_image("workers"),
        "command": f"celery -A workers.celery_app worker --queues={_WORKER_GENERIC_QUEUES}",
        "environment": _workers_env(cfg, prod=prod),
        "volumes": _workers_volumes(cfg),
        "healthcheck": _healthcheck(
            # G-06: ping a ESTE nodo — sin -d es un broadcast al broker
            # compartido y contesta cualquier worker vivo (falso healthy). El
            # timeout de 30s cubre el arranque de celery bajo carga (el default
            # de 10s producía unhealthy crónico sin fallo real).
            "celery -A workers.celery_app inspect ping -d celery@$$HOSTNAME -t 5 || exit 1",
            start_period="40s",
            timeout="30s",
        ),
        "depends_on": {
            "postgres": {"condition": "service_healthy"},
            "redis": {"condition": "service_healthy"},
        },
        "networks": _WORKER_NETWORKS,
    }
    svc.update(_hardening(limits_cpus="4.0", limits_memory=mem))
    # Los workers hacen EXACTAMENTE el mismo baile de auto-inicialización que
    # postgres, redis, vault y clamav —arrancan como root, reparan la propiedad
    # de su árbol de datos y bajan a su usuario de servicio— pero eran los únicos
    # a los que no se les devolvían las capacidades. Medido (e2e run
    # 33184204178):
    #
    #   chown: changing ownership of '/data/agent-platform/…': Operation not permitted
    #   setpriv: setresuid failed: Operation not permitted
    #
    # El `chown` del entrypoint lleva `|| true` y sólo hace ruido; el que mata es
    # `setpriv`, que va sin red y con `set -eu` detrás
    # (`apps/workers/docker-entrypoint.sh:38`). Sin SETUID/SETGID el contenedor
    # muere al arrancar, su healthcheck queda en `unhealthy` y el `up --wait`
    # aborta la instalación entera.
    #
    # Se concede la MISMA lista que a los otros cuatro, no una propia: un
    # segundo conjunto para el mismo patrón se justifica una vez y diverge
    # después. Y sigue siendo mucho más estricto que el stack de desarrollo, que
    # corre estos servicios con las capacidades por defecto de Docker (medido:
    # `CapAdd=[] CapDrop=[]`).
    svc["cap_add"] = list(_INFRA_CAPS)
    # Scale the GENERIC Celery worker pool per the wizard's resource choice.
    svc["deploy"]["replicas"] = cfg.resources.worker_replicas
    return svc


#: prod-04 task_prod_04_09: este compose NO declara named volumes — cada store es
#: un bind bajo `{data_root}` (`{data_root}/minio:/data`, …). Aquí había una lista
#: de nombres copiada del stack de manuales (`agentic-platform_minio_data`, …) que
#: en este layout eran FANTASMA: `tar` sobre
#: `/var/lib/docker/volumes/<fantasma>/_data` devuelve rc≠0 y el contrato
#: clean-failure del motor borraba el bundle entero, incluido el `pg_dump` bueno.
#: Lo que se captura ahora son bind paths que emite `config_generators._backup_env`,
#: que es quien conoce el layout. El worker sigue necesitando root para leerlos
#: (uid 999 de redis, uid 100 de vault, modo 0700).


def _workers_aux_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    """La lane que drena `test` y `review` SIN servir `default`.

    Misma imagen, env, volúmenes, redes y endurecimiento que `workers` —se
    deriva de él para que un cambio en la envoltura no se olvide aquí— y sólo
    cambia el `command`. Es lo que hace segura la espera síncrona de la fase de
    tests: el slot que espera (lane `default`) nunca es el slot que la fase
    necesita (lane `test`). Ver `_WORKER_AUX_QUEUES`.
    """
    svc = _workers_service(cfg, prod=prod)
    svc["command"] = f"celery -A workers.celery_app worker --queues={_WORKER_AUX_QUEUES}"
    return svc


def _workers_privileged_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    """Separate lane that drains ONLY the ``privileged`` queue (backups, key
    rotation). Singleton (replicas=1) — its periodic jobs must not double-run.
    Same image + WORKERS_* env as the generic pool; different queue + scale.

    prod-01 A9 (auditoría 2026-07-06): esta lane ejecuta el volume-tar del
    backup, que lee los ``_data`` de los named volumes (redis uid 999, vault uid
    100) a 0700 → necesita correr como ROOT (``WORKERS_RUN_AS_ROOT=1``; el
    entrypoint self-heal baja a 1000 salvo esta bandera) y bind-montear
    ``/var/lib/docker/volumes``. Sin esto el volume-tar daba EACCES y el backup
    fallaba en una instalación por el instalador (solo funcionaba en manuals.yml).
    """
    env = _workers_env(cfg, prod=prod)
    env.update(
        {
            # Backup como root: leer los `_data`/binds de los stores a 0700
            # (prod-01 A9 / prod-04).
            "WORKERS_RUN_AS_ROOT": "1",
            "WORKERS_BACKUP_VOLUMES": _env_ref("WORKERS_BACKUP_VOLUMES", None, prod=prod),
        }
    )
    volumes = [*_workers_volumes(cfg), "/var/lib/docker/volumes:/var/lib/docker/volumes"]
    svc: dict[str, Any] = {
        "image": app_image("workers"),
        "command": (
            f"celery -A workers.celery_app worker "
            f"--queues={_WORKER_PRIVILEGED_QUEUE} --concurrency=1"
        ),
        "environment": env,
        "volumes": volumes,
        "healthcheck": _healthcheck(
            # G-06: ping a ESTE nodo — sin -d es un broadcast al broker
            # compartido y contesta cualquier worker vivo (falso healthy). El
            # timeout de 30s cubre el arranque de celery bajo carga (el default
            # de 10s producía unhealthy crónico sin fallo real).
            "celery -A workers.celery_app inspect ping -d celery@$$HOSTNAME -t 5 || exit 1",
            start_period="40s",
            timeout="30s",
        ),
        "depends_on": {
            "postgres": {"condition": "service_healthy"},
            "redis": {"condition": "service_healthy"},
        },
        "networks": _WORKER_NETWORKS,
    }
    # Corre como root (el volume-tar del backup lo exige); NO fijamos user 1000.
    svc.update(_hardening(limits_cpus="2.0", limits_memory="2g"))
    svc["deploy"]["replicas"] = 1
    # Misma imagen y mismo entrypoint que `workers`: root -> chown -> setpriv.
    # Sin estas capacidades `setpriv` falla y el contenedor muere al arrancar.
    svc["cap_add"] = list(_INFRA_CAPS)
    return svc


def _workers_marketplace_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    """Lane que drena SOLO la cola ``marketplace`` (prod-13 task_prod13_01).

    Las puertas de seguridad de una instalación —bandit y semgrep por
    ``subprocess`` con 120 s de plazo cada uno, más la prueba de humo del
    sandbox— corrían dentro del request del api-server. ``asyncio.to_thread`` ya
    impedía que congelasen el event loop, pero no las saca del HTTP: el request
    seguía durando minutos y eso lo corta un proxy.

    ``--concurrency=1`` porque instalar es una acción humana y rara, y así dos
    instalaciones simultáneas no se pelean por la CPU del host. No es singleton
    por corrección (a diferencia de la lane ``privileged``, cuyos jobs periódicos
    no pueden doblarse): es una elección de capacidad, y subirla es cambiar este
    número.

    Necesita ``DOCKER_HOST`` porque la puerta 5 lanza un contenedor efímero de
    prueba de humo — precisamente lo que el api-server NO puede hacer (no tiene
    socket Docker, principio 2). Esta lane ES el «sandbox out-of-process» que el
    ADR 0081 pedía para poder cerrar su Fase B/C.
    """
    svc: dict[str, Any] = {
        "image": app_image("workers"),
        "command": (
            f"celery -A workers.celery_app worker "
            f"--queues={_WORKER_MARKETPLACE_QUEUE} --concurrency=1"
        ),
        "environment": _workers_env(cfg, prod=prod),
        "volumes": _workers_volumes(cfg),
        "healthcheck": _healthcheck(
            "celery -A workers.celery_app inspect ping -d celery@$$HOSTNAME -t 5 || exit 1",
            start_period="40s",
            timeout="30s",
        ),
        "depends_on": {
            "postgres": {"condition": "service_healthy"},
            "redis": {"condition": "service_healthy"},
        },
        "networks": _WORKER_NETWORKS,
    }
    svc.update(_hardening(limits_cpus="2.0", limits_memory="2g"))
    svc["deploy"]["replicas"] = 1
    # Misma imagen y mismo entrypoint que `workers`: root -> chown -> setpriv.
    # Sin estas capacidades `setpriv` falla y el contenedor muere al arrancar.
    svc["cap_add"] = list(_INFRA_CAPS)
    return svc


def _cortex_beat_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    """El Celery beat (scheduler) — prod-01 A9 (auditoría 2026-07-06).

    Sin este servicio, en una instalación por el instalador NADA se agenda:
    backup diario, rotación de credenciales, sweepers de mantenimiento (zombis,
    promoción DAG de red, reconciliación de pipeline, poda de worktrees/dep-cache),
    sync de precios/FX, escalado humano y los bucles de fondo del córtex — todos
    definidos en ``workers/beat_schedule.py``. Solo existía en ``manuals.yml``.
    Singleton (un solo beat, o los jobs se duplican). Healthcheck propio: beat no
    es un worker (``inspect ping`` no aplica) ni tiene HTTP — se comprueba que el
    proceso beat es el PID 1 vivo del contenedor."""
    svc: dict[str, Any] = {
        "image": app_image("workers"),
        "command": (
            # `--schedule` explícito: sin él, beat escribe `celerybeat-schedule`
            # en su CWD, que es `/app` — el directorio de CÓDIGO. El kernel lo
            # denegaba (`mknod /app/celerybeat-schedule`, e2e run 33190944410) y
            # la respuesta correcta no era abrir `/app` a escritura: el estado de
            # ejecución no va en el árbol de la aplicación. Este servicio no monta
            # volumen, así que `/tmp` es su sitio; lo que se pierde al reiniciar
            # son las marcas de «última ejecución», y beat retoma su cadencia.
            "celery -A workers.celery_app beat --loglevel=info --schedule=/tmp/celerybeat-schedule"
        ),
        "environment": _workers_env(cfg, prod=prod),
        "healthcheck": {
            "test": [
                "CMD",
                "python",
                "-c",
                "import sys; sys.exit(0 if b'beat' in open('/proc/1/cmdline','rb').read() else 1)",
            ],
            "interval": "30s",
            "timeout": "5s",
            "retries": 3,
            "start_period": "20s",
        },
        "depends_on": {
            "postgres": {"condition": "service_healthy"},
            "redis": {"condition": "service_healthy"},
        },
        "networks": _WORKER_NETWORKS,
    }
    svc.update(_hardening(limits_cpus="0.5", limits_memory="512m"))
    svc["deploy"]["replicas"] = 1
    # Misma imagen y mismo entrypoint que `workers`: root -> chown -> setpriv.
    # Sin estas capacidades `setpriv` falla y el contenedor muere al arrancar.
    svc["cap_add"] = list(_INFRA_CAPS)
    return svc


def _notification_dispatcher_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    env = _app_environment(cfg, "NOTIFY_", prod=prod)
    env.update(
        {
            "NOTIFY_BROKER_URL": _redis_dsn(1, prod=prod),
            "NOTIFY_RESULT_BACKEND": _redis_dsn(2, prod=prod),
            # AUD16 (H10): la DB del bus de eventos/DLQ debe ser la MISMA que
            # miran los consumidores (workers/api-server/orchestrator = DB 0).
            # Con la antigua DB 3, el stream dlq:notifications era invisible
            # para el sampler de métricas y NotificationsDLQNotEmpty no podía
            # disparar jamás en prod (dev ya usaba DB 0).
            "NOTIFY_EVENTS_REDIS_URL": _redis_dsn(0, prod=prod),
            "NOTIFY_NOTIFICATION_ENCRYPTION_KEY": _env_ref(
                "NOTIFY_NOTIFICATION_ENCRYPTION_KEY", None, prod=prod
            ),
        }
    )
    svc: dict[str, Any] = {
        "image": app_image("notification-dispatcher"),
        "environment": env,
        # -A debe ser el módulo REAL de la app (el mismo target del CMD del
        # Dockerfile): `-A notification_dispatcher` no carga («no attribute
        # 'celery'») y dejaba el servicio permanentemente unhealthy (cazado en
        # vivo 2026-07-10). `-d celery@$$HOSTNAME` hace ping a ESTE nodo — el
        # broker es compartido y un ping sin destino contestaría cualquier
        # worker vivo aunque este contenedor estuviera roto.
        "healthcheck": _healthcheck(
            "celery -A notification_dispatcher.celery_app:app inspect ping "
            "-d celery@$$HOSTNAME -t 5 || exit 1",
            start_period="40s",
            timeout="30s",
        ),
        "depends_on": {
            "postgres": {"condition": "service_healthy"},
            "redis": {"condition": "service_healthy"},
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="1.0", limits_memory="512m"))
    return svc


def _watchdog_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    """Vigilante de salud: reinicia con backoff y, al agotarlo, AVISA a un humano.

    prod-08 ``task_prod08_watchdog_14`` (observability-6 + deploy-10). Hasta que
    esto existió, ``apps/watchdog`` era código escrito, probado y **no
    desplegado**: el compose canónico lo declaró el 2026-08-02, y el que se
    ejecuta en casa del operador —éste— siguió sin él. O sea que la
    «recuperación automática de la plataforma» no existía justo en el entorno
    donde no hay nadie mirando ``docker ps``.

    Tres decisiones que van juntas y conviene no separar:

    * **Sin socket Docker.** El enunciado del plan pedía montarlo; el principio
      rector 2 y ``tests/security/test_pentest_findings.py`` lo prohíben, y con
      razón: un contenedor con ``/var/run/docker.sock`` escapa al host de forma
      trivial. Habla con el daemon por ``DOCKER_HOST`` contra el
      ``docker-socket-proxy`` del **ADR 0060**, al que le basta ``CONTAINERS=1``
      + ``POST=1`` para listar y reiniciar. El riesgo 4 del plan queda así
      disuelto, no mitigado.
    * **DOS redes, y las dos hacen falta.** ``agentic-docker`` (``internal``)
      para RESOLVER el proxy —el fallo del 2026-08-10 en el compose canónico fue
      declararlo sólo en ``agentic-net``: el nombre DNS no resolvía, el watchdog
      no veía NINGÚN contenedor, no reiniciaba nada y se callaba, que es
      indistinguible de un stack sano— y ``agentic-net`` para POSTear la alerta
      al api-server, sin la cual la alerta terminal vuelve a ser una línea de log
      local dentro de un contenedor (el defecto original de observability-6).
    * **La alerta comparte secreto con el endpoint.** ``WATCHDOG_ALERTS_INGEST_TOKEN``
      referencia el MISMO ``${API_SERVER_ALERTS_INGEST_TOKEN}`` que valida
      ``/internal/alerts/ingest`` (el instalador ya lo genera y lo escribe en el
      ``.env``). Inventar aquí una variable propia daría un watchdog que cree
      avisar y se come un 401 en cada alerta — peor que no tenerlo, porque nadie
      mira.

    Sin perfil, a diferencia del compose canónico: allí va bajo ``profiles:``
    porque ese fichero es la capa de infraestructura y no construye imágenes de
    aplicación. Aquí las apps SON parte del stack, y un vigilante que hay que
    acordarse de levantar con un flag no vigila nada.
    """
    svc: dict[str, Any] = {
        "image": app_image("watchdog"),
        "environment": {
            # ADR 0060: la pasarela de mínimo privilegio, NUNCA el socket crudo.
            "DOCKER_HOST": "tcp://docker-socket-proxy:2375",
            # Sin esto el watchdog busca contenedores del proyecto por defecto y
            # no encuentra los de esta instalación: resuelve por las etiquetas
            # `com.docker.compose.project` que Docker pone en cada contenedor.
            "WATCHDOG_COMPOSE_PROJECT": PROJECT_NAME,
            "WATCHDOG_POLL_INTERVAL": "30",
            "WATCHDOG_ALERTS_INGEST_URL": ("http://api-server:8000/internal/alerts/ingest"),
            "WATCHDOG_ALERTS_INGEST_TOKEN": _env_ref(
                "API_SERVER_ALERTS_INGEST_TOKEN", None, prod=prod
            ),
        },
        "depends_on": {
            # El proxy del daemon: sin él arranca igual (degradación deliberada,
            # lo dice en el log) pero no ve nada. Ordenarlo evita una ventana de
            # `container_missing` en cada `up`.
            "docker-socket-proxy": {"condition": "service_healthy"},
        },
        "networks": ["agentic-net", "agentic-docker"],
        # Healthcheck PROPIO, y hace falta declararlo (2026-08-28, e2e run
        # 33192295213).
        #
        # `apps/watchdog/Dockerfile` se construye `FROM ${BASE_IMAGE}`, que es la
        # imagen del api-server — y ésa declara un `HEALTHCHECK` que pega a
        # `http://localhost:8000/healthz` (api-server/Dockerfile:137). El watchdog
        # NO sirve HTTP: es un bucle de sondeo. Así que heredaba una sonda que no
        # podía pasar jamás, quedaba `unhealthy` para siempre y el `up --wait`
        # abortaba la instalación entera — con el watchdog funcionando
        # perfectamente y diciéndolo en su propio log.
        #
        # Un healthcheck heredado que no aplica es peor que ninguno: no mide lo
        # que dice medir, y su rojo permanente enseña a ignorarlo.
        #
        # Se usa el patrón que `cortex-beat` ya emplea para lo mismo —comprobar
        # que el proceso vigilado sigue siendo el PID 1— porque para un servicio
        # sin puerto eso es exactamente lo que «sano» significa.
        "healthcheck": {
            "test": [
                "CMD",
                "python",
                "-c",
                "import sys; sys.exit(0 if b'watchdog' in "
                "open('/proc/1/cmdline','rb').read() else 1)",
            ],
            "interval": "30s",
            "timeout": "5s",
            "retries": 3,
            "start_period": "20s",
        },
    }
    svc.update(_hardening(limits_cpus="0.25", limits_memory="192m"))
    return svc


def _admin_panel_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    svc: dict[str, Any] = {
        "image": app_image("admin-panel"),
        "environment": {
            "NODE_ENV": "production" if prod else "development",
            "PLATFORM_DOMAIN": cfg.system.domain,
        },
        # No host ports: the SPA is served through the TLS reverse proxy (caddy),
        # reached internally as admin-panel:3000 (ADR 0061 / deploy-7). NOTE: the
        # caddy service depends_on this one with condition=service_healthy; that
        # is satisfied by the HEALTHCHECK baked into the admin-panel image
        # (apps/admin-panel/Dockerfile), not a compose-level healthcheck here.
        "depends_on": {"api-server": {"condition": "service_healthy"}},
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="1.0", limits_memory="512m"))
    return svc


def _reverse_proxy_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    """The single TLS-terminating reverse proxy — and the ONLY service that
    publishes host ports (ADR 0061, Plan prod-01 task_15 / deploy-7).

    Caddy serves one origin ``https://{domain}``: the admin-panel SPA at ``/``
    and the api-server under ``/api/*`` (see :mod:`installer_backend.proxy_generator`
    for the routing). The generated ``Caddyfile`` is bind-mounted read-only; the
    internal CA / ACME material persists under ``{data_root}/caddy/data`` so the
    self-signed root is not regenerated on every restart. With ``cap_drop:[ALL]``
    the process cannot bind 80/443, so ``NET_BIND_SERVICE`` is added back — the
    single capability needed, mirroring Vault's ``IPC_LOCK`` exception.
    """

    data_root = cfg.storage.data_root
    volumes = [
        "./caddy/Caddyfile:/etc/caddy/Caddyfile:ro",
        f"{data_root}/caddy/data:/data",
        f"{data_root}/caddy/config:/config",
    ]
    if cfg.system.tls_mode == "provided":
        # The corporate cert+key the operator dropped under {data_root}/caddy/tls.
        volumes.append(f"{data_root}/caddy/tls:/etc/caddy/tls:ro")

    svc: dict[str, Any] = {
        "image": IMAGE_CADDY,
        # The ONLY published surface. Caddy listens on 80/443 inside the container.
        "ports": ["80:80", "443:443"],
        "volumes": volumes,
        "depends_on": {
            "api-server": {"condition": "service_healthy"},
            "admin-panel": {"condition": "service_healthy"},
        },
        # Plain-HTTP /healthz on :80 (no redirect to https) so the self-signed
        # cert + 308 don't mark the proxy unhealthy.
        "healthcheck": _healthcheck(
            "wget -q --spider http://127.0.0.1:80/healthz || exit 1", start_period="15s"
        ),
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="1.0", limits_memory="512m"))
    svc["cap_add"] = ["NET_BIND_SERVICE"]
    return svc


def _ollama_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    """In-stack Ollama for embeddings (+ optional local LLMs) — added when
    ``ollama_mode != "none"`` (ADR 0056).

    On ``cpu`` it runs without any device reservation (enough for embeddings and
    small models). On ``gpu`` it adds the Compose-native NVIDIA reservation
    (``deploy.resources.reservations.devices``) so Docker schedules it on the GPU
    — requires the NVIDIA Container Toolkit. Hardened like the rest of the stack;
    the model lives under ``{data_root}/ollama`` (bind mount).
    """

    svc: dict[str, Any] = {
        "image": IMAGE_OLLAMA,
        "environment": {"OLLAMA_HOST": "0.0.0.0:11434"},
        "volumes": [f"{cfg.storage.data_root}/ollama:/root/.ollama"],
        "healthcheck": {
            "test": ["CMD-SHELL", "ollama list >/dev/null 2>&1 || exit 1"],
            "interval": "30s",
            "timeout": "5s",
            "retries": 5,
            "start_period": "30s",
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="4.0", limits_memory="8g"))
    if cfg.resources.ollama_mode == "gpu":
        # Compose-native GPU reservation (requires the NVIDIA Container Toolkit).
        # `capabilities` is a flat list of strings per the Compose schema.
        svc["deploy"]["resources"]["reservations"] = {
            "devices": [{"driver": "nvidia", "count": "all", "capabilities": ["gpu"]}],
        }
    return svc


def _ollama_bootstrap_service(
    cfg: InstallerConfig,
    *,
    prod: bool,  # noqa: ARG001 — uniform builder signature; the bootstrap ignores prod
) -> dict[str, Any]:
    """One-shot that pulls the embedding model into the Ollama volume once the
    server is healthy, then exits (ADR 0056 option B-A).

    Without it the first ``/api/embed`` fails with ``model not found``. Idempotent
    (a re-run with the model present is a fast no-op; the model persists in the
    bind mount). Not hardened/limited like the long-lived services — it is a
    short-lived init that shares the model name with the api-server embedder.
    """

    model = cfg.resources.embedding_model
    svc: dict[str, Any] = {
        "image": IMAGE_OLLAMA,
        "depends_on": {OLLAMA_SERVICE: {"condition": "service_healthy"}},
        "environment": {"OLLAMA_HOST": "http://ollama:11434"},
        "entrypoint": ["/bin/sh", "-c"],
        "command": [f"ollama pull {model}"],
        "networks": ["agentic-net"],
    }
    # Same hardening posture as the long-lived services, but a one-shot must not
    # restart (it pulls once and exits) — override the restart policy to "no".
    svc.update(_hardening(limits_cpus="1.0", limits_memory="2g"))
    svc["restart"] = "no"
    return svc


def _python_health(binary: str, url: str, *, start_period: str) -> dict[str, Any]:
    """A python-stdlib HTTP liveness probe (urllib) for the voice images.

    The stt/tts images ship NEITHER wget NOR curl (only a python interpreter),
    so a wget-based check would mark them permanently unhealthy. ``binary`` is
    ``python3`` for faster-whisper and ``python`` for Kokoro (its venv exposes
    ``python``), mirroring docker/docker-compose.yml. ``urlopen`` raises (exit
    != 0) when /health is not yet serving.
    """

    return {
        "test": [
            "CMD",
            binary,
            "-c",
            f"import urllib.request; urllib.request.urlopen('{url}', timeout=4)",
        ],
        "interval": "30s",
        "timeout": "5s",
        "retries": 5,
        "start_period": start_period,
    }


def _stt_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    """Speech-to-Text for the Assistant + córtex voice mode (ADR 0073).

    faster-whisper (CTranslate2) with an OpenAI-compatible API. The Whisper
    model is downloaded on first use and cached in the ``whisper_models`` named
    volume (the long ``start_period`` covers that first download). Internal
    service: no host ports — the api-server reaches it at ``stt:8000``. Mirrors
    docker/docker-compose.yml; the healthcheck probes with python3 (no wget in
    the image).
    """

    svc: dict[str, Any] = {
        "image": IMAGE_STT,
        "environment": {
            # CPU-friendly ES+EN default; large-v3 lives behind the GPU overlay.
            "WHISPER__MODEL": "Systran/faster-whisper-small",
            "WHISPER__INFERENCE_DEVICE": "cpu",
        },
        "volumes": [f"{WHISPER_MODELS_VOLUME}:/root/.cache/huggingface"],
        # 1st boot downloads the model → generous grace window.
        "healthcheck": _python_health(
            "python3", "http://localhost:8000/health", start_period="120s"
        ),
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="2.0", limits_memory="4g"))
    return svc


def _tts_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    """Text-to-Speech for the voice mode (ADR 0073) — Kokoro-82M with an
    OpenAI-compatible API (ES+EN voices). Internal: the api-server reaches it at
    ``tts:8880``. Mirrors docker/docker-compose.yml; healthcheck probes with
    python (the image exposes ``python`` in its venv, not wget)."""

    svc: dict[str, Any] = {
        "image": IMAGE_TTS,
        "healthcheck": _python_health("python", "http://localhost:8880/health", start_period="60s"),
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="2.0", limits_memory="2g"))
    return svc


def _prometheus_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    svc: dict[str, Any] = {
        "image": IMAGE_PROMETHEUS,
        "user": "65534:65534",
        "command": [
            "--config.file=/etc/prometheus/prometheus.yml",
            "--storage.tsdb.path=/prometheus",
            "--storage.tsdb.retention.time=15d",
            "--web.enable-lifecycle",
        ],
        "volumes": [
            f"{STACK}/monitoring/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro",
            f"{STACK}/monitoring/prometheus/rules:/etc/prometheus/rules:ro",
            f"{cfg.storage.data_root}/prometheus:/prometheus",
        ],
        "healthcheck": {
            "test": ["CMD-SHELL", "wget -q --spider http://localhost:9090/-/healthy || exit 1"],
            "interval": "30s",
            "timeout": "5s",
            "retries": 5,
            "start_period": "30s",
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="1.0", limits_memory="1g"))
    return svc


def _node_exporter_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    svc: dict[str, Any] = {
        "image": IMAGE_NODE_EXPORTER,
        "command": [
            "--path.procfs=/host/proc",
            "--path.sysfs=/host/sys",
            "--path.rootfs=/host/root",
            "--collector.filesystem.mount-points-exclude=^/(sys|proc|dev|host|etc)($$|/)",
            # Textfile collector: sin esta bandera node-exporter NO mira el
            # drop-dir y las métricas de aplicación que dejan ahí los workers no
            # se re-exportan — el mount por sí solo no basta.
            f"--collector.textfile.directory={TEXTFILE_COLLECTOR_DIR}",
        ],
        "pid": "host",
        "volumes": [
            "/proc:/host/proc:ro",
            "/sys:/host/sys:ro",
            "/:/host/root:ro,rslave",
            # Solo LECTURA: aquí node-exporter consume; quien escribe son las
            # lanes de workers (TEXTFILE_WRITER_SERVICES), que lo montan RW.
            f"{TEXTFILE_COLLECTOR_VOLUME}:{TEXTFILE_COLLECTOR_DIR}:ro",
        ],
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="0.5", limits_memory="256m"))
    return svc


def _textfile_init_service(
    cfg: InstallerConfig,  # noqa: ARG001 — firma uniforme de los builders
    *,
    prod: bool,  # noqa: ARG001 — firma uniforme de los builders
) -> dict[str, Any]:
    """One-shot que abre el drop-dir del textfile collector (espejo del
    ``textfile-init`` de docker-compose.monitoring.yml).

    El directorio es MULTI-ESCRITOR: ``workers`` escribe como uid 1000 (el
    entrypoint degrada de root salvo bandera) y ``workers-privileged`` como root
    (``WORKERS_RUN_AS_ROOT=1``, se lo exige el volume-tar del backup). Un volumen
    nombrado nace ``root:root 0755``, así que el sampler recibiría EACCES en cada
    pasada — y en silencio, porque publicar métricas es best-effort. Modo 1777
    sticky (como ``/tmp``): cualquier escritor deja su ``.prom`` y nadie puede
    borrar el de otro. Los escritores lo esperan con
    ``service_completed_successfully`` (ver :func:`generate_compose`).

    Efímero pero corre como root, así que lleva la misma línea base de
    endurecimiento que el resto: ``chmod`` sobre un directorio cuyo dueño ya es
    root no necesita ninguna capability (el euid coincide con el propietario, no
    hace falta CAP_FOWNER), de modo que ``cap_drop: [ALL]`` no le quita nada.
    """

    svc: dict[str, Any] = {
        "image": IMAGE_BUSYBOX,
        "command": ["chmod", "1777", TEXTFILE_COLLECTOR_DIR],
        "volumes": [f"{TEXTFILE_COLLECTOR_VOLUME}:{TEXTFILE_COLLECTOR_DIR}"],
        # Sin red: solo toca un volumen local.
        "network_mode": "none",
    }
    svc.update(_hardening(limits_cpus="0.1", limits_memory="64m"))
    svc["restart"] = "no"  # one-shot: hace el chmod y sale
    return svc


def _alertmanager_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    """Alertmanager — routes Prometheus' firing alerts to the platform notifier.

    Mirrors docker/docker-compose.monitoring.yml: the routing/receiver config is
    ``monitoring/alertmanager/alertmanager.yml`` (its default receiver webhooks
    the api-server's ``/internal/alerts/ingest``, reusing the Plan 10 notifier).
    The file itself carries NO secret: the ``severity=critical`` backup receiver
    reads its Slack webhook from a file in the secrets mailbox mounted below.
    Without this service the alert RULES Prometheus evaluates would have nowhere
    to go in production.
    """
    svc: dict[str, Any] = {
        "image": IMAGE_ALERTMANAGER,
        "user": "65534:65534",
        "command": [
            "--config.file=/etc/alertmanager/alertmanager.yml",
            "--storage.path=/alertmanager",
        ],
        "volumes": [
            f"{STACK}/monitoring/alertmanager/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro",
            # Buzón de la credencial del receiver de RESPALDO. El porqué, con el
            # dato concreto, está en el bloque ``ALERTMANAGER_SECRETS_*`` de
            # arriba: sin este montaje la ruta que declara ``api_url_file`` no
            # existe en el contenedor, Alertmanager ARRANCA IGUAL (ese fichero se
            # lee al notificar, no al cargar la config) y el canal de último
            # recurso falla en cada envío en silencio, con el stack `healthy` —
            # en el único escenario para el que existe, el api-server caído.
            #
            # Read-only: Alertmanager solo lee. Corre como ``nobody`` (65534), así
            # que el lado host debe existir con permisos de lectura para él antes
            # de levantar el stack; si no existe, Docker lo inventa como root.
            f"{ALERTMANAGER_SECRETS_HOST_DIR}:{ALERTMANAGER_SECRETS_DIR}:ro",
            f"{cfg.storage.data_root}/alertmanager:/alertmanager",
        ],
        "healthcheck": {
            "test": ["CMD-SHELL", "wget -q --spider http://localhost:9093/-/healthy || exit 1"],
            "interval": "30s",
            "timeout": "5s",
            "retries": 5,
            "start_period": "30s",
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="0.5", limits_memory="256m"))
    return svc


def _cadvisor_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:  # noqa: ARG001
    """cAdvisor — per-container CPU/memory/network/fs metrics.

    prod-12 task_prod12_cadv_01 (sandbox-8, decisión 5 opción a): cAdvisor ya
    NO corre ``privileged`` ni monta ``/dev/kmsg`` — los stats de contenedor
    salen de los bind-mounts read-only (cgroups vía /sys + /rootfs +
    /var/lib/docker), que funcionan con ``cap_drop: [ALL]`` + AppArmor pineado
    como cualquier otro servicio de primera parte (validado empíricamente:
    families container_cpu/memory/network/fs presentes sin privileged; lo que
    se pierde es la decodificación de eventos OOM-kill del kernel vía
    /dev/kmsg — trade-off documentado en el runbook de monitoring, con el
    override legacy-privileged como opt-in manual para quien lo necesite).
    Esto RESUELVE la contradicción sandbox-8 con
    docker/docker-compose.monitoring.yml (que siempre pineó apparmor).
    """
    svc: dict[str, Any] = {
        "image": IMAGE_CADVISOR,
        "command": ["--docker_only=true", "--housekeeping_interval=30s"],
        "volumes": [
            "/:/rootfs:ro",
            "/var/run:/var/run:ro",
            "/sys:/sys:ro",
            "/var/lib/docker/:/var/lib/docker:ro",
            "/dev/disk/:/dev/disk:ro",
        ],
        "healthcheck": {
            "test": ["CMD", "wget", "-q", "--spider", "http://localhost:8080/healthz"],
            "interval": "30s",
            "timeout": "5s",
            "retries": 5,
            "start_period": "30s",
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="0.5", limits_memory="256m"))
    return svc


def _grafana_service(cfg: InstallerConfig, *, prod: bool) -> dict[str, Any]:
    svc: dict[str, Any] = {
        "image": IMAGE_GRAFANA,
        "user": "472:472",
        "environment": {
            "GF_SECURITY_ADMIN_USER": _env_ref("GRAFANA_ADMIN_USER", "admin", prod=prod),
            "GF_SECURITY_ADMIN_PASSWORD": _env_ref("GRAFANA_ADMIN_PASSWORD", None, prod=prod),
            "GF_USERS_ALLOW_SIGN_UP": "false",
            "GF_AUTH_ANONYMOUS_ENABLED": "false",
            "GF_ANALYTICS_REPORTING_ENABLED": "false",
            "GF_ANALYTICS_CHECK_FOR_UPDATES": "false",
        },
        "volumes": [
            f"{STACK}/monitoring/grafana/provisioning:/etc/grafana/provisioning:ro",
            f"{STACK}/monitoring/grafana/dashboards:/var/lib/grafana/dashboards:ro",
            f"{cfg.storage.data_root}/grafana:/var/lib/grafana",
        ],
        "depends_on": ["prometheus"],
        "healthcheck": {
            "test": ["CMD-SHELL", "wget -q --spider http://localhost:3000/api/health || exit 1"],
            "interval": "30s",
            "timeout": "5s",
            "retries": 5,
            "start_period": "30s",
        },
        "networks": ["agentic-net"],
    }
    svc.update(_hardening(limits_cpus="1.0", limits_memory="512m"))
    return svc


#: Builder dispatch — one entry per service name.
_BUILDERS = {
    "postgres": _postgres_service,
    "redis": _redis_service,
    "minio": _minio_service,
    "vault": _vault_service,
    "clamav": _clamav_service,
    "docling-serve": _docling_service,
    "egress-proxy": _egress_proxy_service,
    "registry-proxy": _registry_proxy_service,
    "docker-socket-proxy": _docker_socket_proxy_service,
    "migrations": _migrations_service,
    "api-server": _api_server_service,
    BOOTSTRAP_SERVICE: _bootstrap_service,
    "orchestrator": _orchestrator_service,
    "workers": _workers_service,
    "workers-aux": _workers_aux_service,
    "workers-privileged": _workers_privileged_service,
    "workers-marketplace": _workers_marketplace_service,
    "cortex-beat": _cortex_beat_service,
    "notification-dispatcher": _notification_dispatcher_service,
    "watchdog": _watchdog_service,
    "admin-panel": _admin_panel_service,
    "caddy": _reverse_proxy_service,
    "ollama": _ollama_service,
    "ollama-bootstrap": _ollama_bootstrap_service,
    "stt": _stt_service,
    "tts": _tts_service,
    "prometheus": _prometheus_service,
    TEXTFILE_INIT_SERVICE: _textfile_init_service,
    "node-exporter": _node_exporter_service,
    "alertmanager": _alertmanager_service,
    "cadvisor": _cadvisor_service,
    "grafana": _grafana_service,
}


def _provider_env_for(cfg: InstallerConfig) -> dict[str, str]:
    """Provider wiring injected into the app services from the enabled providers.

    Each enabled ADR-0021 provider contributes its (non-secret) wiring: a feature
    flag + endpoint reference. The real credentials live in Vault (task 15_09);
    here we only toggle which providers the runtime is configured to use, so a
    disabled provider leaves NO wiring in the compose at all.
    """

    env: dict[str, str] = {}
    providers = cfg.providers
    if providers.claude_sdk.enabled:
        env["LLM_CLAUDE_SDK_ENABLED"] = "true"
    if providers.copilot.enabled:
        env["LLM_COPILOT_ENABLED"] = "true"
    if providers.azure_foundry.enabled:
        env["LLM_AZURE_FOUNDRY_ENABLED"] = "true"
        if providers.azure_foundry.apim_endpoint:
            env["LLM_AZURE_FOUNDRY_ENDPOINT"] = providers.azure_foundry.apim_endpoint
    if providers.ollama.enabled:
        env["LLM_OLLAMA_ENABLED"] = "true"
        # Prefer an explicit endpoint; default to the in-stack service when one
        # is deployed (cpu or gpu), otherwise leave the wizard-provided endpoint.
        if providers.ollama.endpoint:
            env["LLM_OLLAMA_ENDPOINT"] = providers.ollama.endpoint
        elif cfg.resources.ollama_mode != "none":
            env["LLM_OLLAMA_ENDPOINT"] = "http://ollama:11434"

    # Embedder wiring (ADR 0056): when the in-stack Ollama is deployed, point the
    # api-server embedder (and the memory back-fill worker) at it and pin the
    # model to the same one the bootstrap pulls. API_SERVER_* are read only by
    # the api-server; WORKERS_* only by the workers — harmless on the others.
    if cfg.resources.ollama_mode != "none":
        env["API_SERVER_OLLAMA_URL"] = "http://ollama:11434"
        env["API_SERVER_EMBEDDING_MODEL"] = cfg.resources.embedding_model
        env["WORKERS_MEMORY_EMBEDDER_BASE_URL"] = "http://ollama:11434"

    # Voice wiring (ADR 0073): when the in-stack stt/tts are deployed, point the
    # api-server (which serves BOTH the Assistant voice loop and the córtex voice
    # turn — they read the same assistant_{stt,tts}_url settings) at them.
    # Without these the runtime falls back to its localhost dev defaults and the
    # voice mode silently fails in production (the bug this fixes). API_SERVER_*
    # are read only by the api-server → harmless on the other app services.
    if cfg.resources.voice_mode != "none":
        env["API_SERVER_ASSISTANT_STT_URL"] = f"http://{STT_SERVICE}:8000"
        env["API_SERVER_ASSISTANT_TTS_URL"] = f"http://{TTS_SERVICE}:8880"
    return env


def enabled_providers(cfg: InstallerConfig) -> tuple[LLMProviderKind, ...]:
    """The ordered tuple of providers enabled in the wizard config."""

    out: list[LLMProviderKind] = []
    if cfg.providers.claude_sdk.enabled:
        out.append(LLMProviderKind.CLAUDE_SDK)
    if cfg.providers.copilot.enabled:
        out.append(LLMProviderKind.COPILOT)
    if cfg.providers.azure_foundry.enabled:
        out.append(LLMProviderKind.AZURE_FOUNDRY)
    if cfg.providers.ollama.enabled:
        out.append(LLMProviderKind.OLLAMA)
    return tuple(out)


def selected_services(cfg: InstallerConfig, *, monitoring: bool) -> list[str]:
    """The ordered list of service names the generated compose will contain.

    Core services are always present; the ``bootstrap`` one-shot is always
    DECLARED but never started by ``up`` (it lives behind its own profile — ADR
    0161 paso 8); the in-stack ``ollama`` service + its ``ollama-bootstrap``
    one-shot are added when ``ollama_mode != "none"`` (ADR 0056); the voice
    ``stt``/``tts`` services when ``voice_mode != "none"`` (ADR 0073); the
    monitoring overlay services only when requested.
    """

    services = list(CORE_SERVICES)
    # El one-shot de finalización: SIEMPRE declarado, NUNCA arrancado por `up`
    # (vive bajo `profiles: [bootstrap]`). Va aquí y no en CORE_SERVICES porque
    # esa tupla describe el stack que corre — ver el bloque de BOOTSTRAP_SERVICE.
    services.append(BOOTSTRAP_SERVICE)
    if cfg.resources.ollama_mode != "none":
        services.append(OLLAMA_SERVICE)
        services.append(OLLAMA_BOOTSTRAP_SERVICE)
    if cfg.resources.voice_mode != "none":
        services.extend(VOICE_SERVICES)
    if monitoring:
        services.extend(MONITORING_SERVICES)
    return services


def _networks_block() -> dict[str, Any]:
    """The platform networks: agentic-net (egress), the internal agentic-agents
    (sandbox ↔ egress-proxy), and the internal agentic-docker — a DEDICATED net
    that carries ONLY the workers ↔ docker-socket-proxy traffic so the Docker
    API is never reachable from the untrusted agent runtimes (Plan prod-01
    task_09, ADR 0060)."""

    return {
        "agentic-net": {"name": "agentic-net", "driver": "bridge"},
        "agentic-agents": {
            "name": "agentic-agents",
            "driver": "bridge",
            "internal": True,
            "driver_opts": {"com.docker.network.bridge.enable_icc": "true"},
        },
        "agentic-docker": {
            "name": "agentic-docker",
            "driver": "bridge",
            "internal": True,
        },
    }


def _wire_textfile_collector(services: dict[str, Any]) -> None:
    """Monta el drop-dir del textfile collector en los servicios que publican
    métricas de aplicación y los hace esperar al ``textfile-init``.

    El POR QUÉ, con el dato concreto, está en el bloque ``TEXTFILE_*`` de arriba:
    sin este mount las cuatro series de aplicación (``agentic_celery_queue_depth``,
    ``agentic_tasks_by_status``, ``agentic_dlq_depth``, ``agentic_executions_24h``)
    no existen en una instalación generada por el instalador, y las cuatro reglas
    de ``monitoring/prometheus/rules/app_alerts.yml`` que las evalúan quedan
    armadas sin poder disparar nunca.

    En RW a propósito (node-exporter es quien lo monta ``:ro``), y sin variables
    de entorno: el punto de montaje ES el default que ya trae el código del
    worker.
    """

    mount = f"{TEXTFILE_COLLECTOR_VOLUME}:{TEXTFILE_COLLECTOR_DIR}"
    for name in TEXTFILE_WRITER_SERVICES:
        svc = services.get(name)
        if svc is None:  # pragma: no cover — ambas lanes son servicios core
            continue
        volumes = svc.setdefault("volumes", [])
        assert isinstance(volumes, list)
        if mount not in volumes:
            volumes.append(mount)
        # Arrancar antes del chmod significa que el PRIMER sample muere con
        # EACCES (y en silencio, porque publicar métricas es best-effort).
        deps = svc.setdefault("depends_on", {})
        assert isinstance(deps, dict)
        deps[TEXTFILE_INIT_SERVICE] = {"condition": "service_completed_successfully"}


def generate_compose(
    cfg: InstallerConfig,
    *,
    monitoring: bool = False,
) -> dict[str, Any]:
    """Build the runtime stack ``docker-compose`` mapping from the wizard config.

    Parameters mirror the wizard choices:
      * ``cfg.resources.gpu_enabled`` → adds the GPU ``ollama`` service (profile
        ``gpu``) with an NVIDIA device reservation.
      * ``cfg.providers`` → only the enabled ADR-0021 providers get their wiring
        injected into the application services' environment.
      * ``cfg.ports`` → retained in the wizard model for back-compat / dev
        overrides, but NO LONGER mapped to the host in the generated production
        compose: the TLS reverse proxy (``caddy``) is the only published surface
        (ADR 0061).
      * ``cfg.storage.data_root`` → the bind-mount base for every stateful
        service.
      * ``monitoring`` → adds the Prometheus/Grafana/node-exporter overlay.

    Returns a plain ``dict`` (serialise with :func:`render_compose_yaml`). The
    mapping is hardened + secret-free (``${ENV}`` references only) and, for a
    production environment, carries no dev-default secret marker.
    """

    prod = cfg.system.environment is Environment.PRODUCTION
    service_names = selected_services(cfg, monitoring=monitoring)
    provider_env = _provider_env_for(cfg)

    # The platform app services that read the schema → must wait for the
    # one-shot migrations to finish (task_12 / deploy-6).
    migration_dependents = (
        "api-server",
        "orchestrator",
        "workers",
        "workers-aux",
        "workers-privileged",
        "workers-marketplace",
        "cortex-beat",
        "notification-dispatcher",
    )

    services: dict[str, Any] = {}
    for name in service_names:
        builder = _BUILDERS[name]
        svc = builder(cfg, prod=prod)
        # Inject the provider wiring into the application services only.
        # `bootstrap` incluido: `api_server.seeds` embebe el corpus del catálogo
        # contra Ollama, y sin el cableado del embebedor caería a su default de
        # dev (localhost) — un seed que revienta aborta la siembra ENTERA, y aquí
        # sería después de que Vault haya emitido las unseal keys.
        if name in (
            "api-server",
            "orchestrator",
            "workers",
            "notification-dispatcher",
            BOOTSTRAP_SERVICE,
        ):
            env = svc.setdefault("environment", {})
            assert isinstance(env, dict)
            env.update(provider_env)
        # Gate the apps on the schema being migrated.
        if name in migration_dependents:
            deps = svc.setdefault("depends_on", {})
            assert isinstance(deps, dict)
            deps["migrations"] = {"condition": "service_completed_successfully"}
        services[name] = svc

    # Métricas de APLICACIÓN: solo con monitorización desplegada. El mount y el
    # depends_on NO pueden colarse en una instalación sin monitorización — ahí no
    # existen ni el volumen ni el one-shot, y compose se negaría a levantar el
    # proyecto entero por un volumen no declarado / un depends_on huérfano.
    if monitoring:
        _wire_textfile_collector(services)

    compose: dict[str, Any] = {
        "name": PROJECT_NAME,
        "services": services,
        "networks": _networks_block(),
    }
    # Declare the named volume(s) any generated service references. Every
    # stateful service uses a {data_root} bind mount; the exceptions are the
    # Whisper model cache (voice) and the textfile-collector drop dir
    # (monitoring), each declared only when its feature is on so the compose
    # carries no dangling volume otherwise.
    volumes: dict[str, Any] = {}
    if STT_SERVICE in services:
        volumes[WHISPER_MODELS_VOLUME] = None
    if TEXTFILE_INIT_SERVICE in services:
        volumes[TEXTFILE_COLLECTOR_VOLUME] = None
    if volumes:
        compose["volumes"] = volumes
    return compose


def render_compose_yaml(compose: dict[str, Any]) -> str:
    """Serialise a generated compose mapping to deterministic YAML text.

    ``sort_keys=False`` preserves the builder ordering (services first in
    pipeline order), ``default_flow_style=False`` keeps the block style the rest
    of the repo's compose files use. The output is what the install seam writes
    to disk; this function performs NO I/O.
    """

    text: str = yaml.safe_dump(
        copy.deepcopy(compose),
        sort_keys=False,
        default_flow_style=False,
        width=100,
    )
    return text


def assert_no_dev_secret_markers(yaml_text: str) -> None:
    """Raise ``ValueError`` if a dev-default secret marker leaked into prod YAML.

    The prod secret guard (``api_server.config._DEV_SECRET_MARKERS``) rejects
    these substrings; the generator must never emit them for a production
    install. This is a belt-and-braces self-check the CLI/wizard can call after
    generating a production compose.
    """

    lowered = yaml_text.lower()
    found = [marker for marker in _DEV_SECRET_MARKERS if marker in lowered]
    if found:
        raise ValueError(
            "El docker-compose generado para producción contiene marcadores de "
            f"secreto de desarrollo: {', '.join(found)}."
        )
