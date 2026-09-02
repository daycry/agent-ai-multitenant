"""Worker-test: orchestrate test-runtime containers per task (Plan 06 Fase B).

This is the heterogeneous-stacks brother of ``container.py``. While the
agent-runtime container runs the LangGraph loop, the **test-runtime**
container runs *only* the acceptance-criteria commands the agent's
output is supposed to satisfy. Different stacks → different runtimes
(python-pytest, node-jest, php-phpunit, …), all resolved through
:mod:`shared_test_runtimes.catalog`.

The four tasks of Fase B all live here:

  * ``group_tasks_by_runtime`` (task_06_04) — read each task's
    ``acceptance_criteria`` list, drop the entries DECLARED non-automated
    (manual / human), and group the rest by runtime. Each
    :class:`RuntimePlan` becomes one container launch downstream.
    Ojo con el matiz del ADR 0162: un criterio que no declara nada NO es
    «automated», es NO DECLARADO — se ejecuta igual que siempre, pero queda
    contado aparte (:meth:`RuntimePlan.undeclared_check_type_count`).
  * :class:`TestRuntimeRunner.launch` (task_06_05) — wire the
    template's image + worktree mount + dep-cache mount + aux network
    + ephemeral compose into ``docker.containers.run``, with the same
    hardened envelope ``container.py`` uses (cap-drop ALL, no-new-priv,
    read-only root, non-root uid, ``network=none`` by default).
  * :class:`AuxServiceSpec` + :meth:`TestRuntimeRunner.compose_aux`
    (task_06_06) — describe the postgres-test / redis-test sidecars
    each project can opt into, run them on the task's private bridge
    network, and tear them down at end-of-task.

`task_wf_57`: aquí vivía además un modo «testcontainers» que levantaba un
proxy del socket de Docker como sidecar para que las librerías de
testcontainers pudieran hablar con el daemon. Se ha RETIRADO: era el único
camino del sistema que montaba el socket en algún sitio, nunca se ejercitó en
producción, y una vía de escape que nadie usa no compensa por muy endurecida
que esté. Si algún día hace falta, vuelve con su ADR.

Implementation note: ``container.py``'s ``AgentContainerRunner`` exists
for *one container per task*. ``TestRuntimeRunner`` exists for *one
container per (task, runtime) pair*, with siblings for aux services
sharing an ephemeral bridge. We keep them separate so the hardening
profile each one applies stays explicit.
"""

from __future__ import annotations

import contextlib
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import structlog
from docker.types import Mount
from shared_test_runtimes.catalog import get as get_template
from shared_test_runtimes.counts import TestCounts, count_tests
from shared_test_runtimes.images import pinned_pull_reference
from shared_test_runtimes.signals import evaluate_signal
from shared_test_runtimes.types import RuntimeTemplate

import docker
from workers.config import Settings
from workers.isolation import (
    AGENT_HOME,
    AGENT_UID_GID,
    DockerSocketLeakError,
    assert_no_docker_socket,
    build_security_opt,
)

_log = structlog.get_logger("workers.test_runtime")

# Labels stamped on every container/network the test-runtime launches.
# Mirrors container.py's _BASE_LABELS so the same reaper sweeps both.
_TEST_LABELS: dict[str, str] = {
    "com.agentic-platform.component": "test-runtime",
    "com.agentic-platform.managed": "true",
}

# Force git-based deps to HTTPS so they traverse the HTTP registry-proxy —
# tinyproxy can't tunnel git-over-SSH (ADR 0094). Injected ONLY when a launch
# has proxied egress; git reads GIT_CONFIG_KEY_<n>/VALUE_<n> for n in
# 0..GIT_CONFIG_COUNT-1. `composer`/`go`/`pip` VCS deps that default to
# ``git@host:owner/repo`` get rewritten to ``https://host/owner/repo``.
_GIT_HTTPS_ENV: dict[str, str] = {
    "GIT_CONFIG_COUNT": "3",
    "GIT_CONFIG_KEY_0": "url.https://github.com/.insteadOf",
    "GIT_CONFIG_VALUE_0": "git@github.com:",
    "GIT_CONFIG_KEY_1": "url.https://gitlab.com/.insteadOf",
    "GIT_CONFIG_VALUE_1": "git@gitlab.com:",
    "GIT_CONFIG_KEY_2": "url.https://bitbucket.org/.insteadOf",
    "GIT_CONFIG_VALUE_2": "git@bitbucket.org:",
}

# Default test-runtime wall-clock cap. Tests longer than this almost
# always indicate a hung process, not legitimate work; the project can
# override per task via ``acceptance_criteria[*].timeout_s``.
DEFAULT_TIMEOUT_S = 600


# ---------------------------------------------------------------------------
# ADR 0148 — procedencia de la imagen de runtime
# ---------------------------------------------------------------------------


class RuntimeImageUnavailableError(RuntimeError):
    """No se pudo obtener la imagen fijada por digest, y NO hay plan B.

    Existe para que el fallo sea explícito. La alternativa —seguir adelante con
    lo que hubiera en el daemon local bajo ese nombre— produce un run verde
    ejecutado sobre una imagen que nadie puede identificar, que es exactamente
    el problema que el ADR 0148 vino a cerrar. Ver su condición 2.
    """


def ensure_runtime_image(client: Any, image_reference: str) -> str:
    """Garantizar la procedencia de la imagen y devolver con qué lanzarla.

    * Referencia **con digest** (el catálogo tras una release): se descarga por
      digest y se devuelve la forma canónica ``repo@sha256:…``, que es la que se
      pasa a ``containers.run``. Si el pull falla, se ABORTA.
    * Referencia **sin digest** (el catálogo mientras no haya release publicada,
      o la imagen propia de un proyecto del ADR 0129, construida en el host): se
      devuelve tal cual. No hay procedencia que verificar y exigir un pull la
      rompería.

    El atajo de «ya está en local» solo aplica al caso fijado por digest, y es
    seguro justo por eso: un digest es direccionable por contenido, así que una
    imagen presente bajo ese digest **es** la imagen correcta. Sin él cada
    lanzamiento pagaría una ida al registry.
    """
    pull_reference = pinned_pull_reference(image_reference)
    if pull_reference is None:
        return image_reference

    try:
        client.images.get(pull_reference)
    except Exception:  # no está en local (o el daemon protesta): manda el pull
        pass
    else:
        return pull_reference

    try:
        client.images.pull(pull_reference)
    except Exception as exc:
        _log.error(
            "runtime_image_pull_failed",
            image=image_reference,
            pull_reference=pull_reference,
            error=str(exc),
        )
        raise RuntimeImageUnavailableError(
            f"no se pudo obtener la imagen de runtime fijada por digest "
            f"{image_reference!r}: {exc}. La tarea se ABORTA: caer a una imagen "
            f"local con el mismo tag ejecutaría código no confiable en una imagen "
            f"que nadie puede identificar (ADR 0148, condición 2). Comprueba el "
            f"acceso del host al registry, o apunta RUNTIME_IMAGE_REGISTRY a un "
            f"mirror que sirva ese digest."
        ) from exc
    _log.info("runtime_image_pulled", pull_reference=pull_reference)
    return pull_reference


# ---------------------------------------------------------------------------
# task_06_04 — Grouping
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AcceptanceCheck:
    """One ``acceptance_criteria`` entry, normalised.

    The DB column is ``list[Any]`` so projects can pass arbitrary
    extra fields; we only care about a closed set here. Anything we
    don't recognise stays in ``raw`` for the parser/reporter to use.
    """

    id: str
    description: str
    runtime: str
    command: str
    expected_signal: str = "exit_code == 0"
    """Qué tiene que ocurrir para que este criterio se dé por verificado.

    **Desde el ADR 0162 (opción A) SÍ se evalúa** —``shared_test_runtimes.signals``,
    reportado en :attr:`TestRuntimeResult.check_signals`— pero sigue sin decidir:
    el veredicto de un check (:meth:`TestRuntimeResult.all_passed`) continúa
    saliendo sólo del código de salida. Convertir esta señal en gate es la opción
    C, que **no está firmada**.

    El default ``exit_code == 0`` es también el agujero que documenta el ADR: en
    la base de datos viva hay dos PHPUnit en verde con ``No tests executed!``. Un
    criterio que quiera cerrarlo declara ``exit_code == 0 and tests > 0``."""
    timeout_s: int | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)
    # ADR 0162: qué `check_type` DECLARÓ el criterio, o ``None`` si nadie lo
    # declaró. No es lo mismo que «automated»: ver :func:`_coerce_check`.
    declared_check_type: str | None = None


@dataclass(frozen=True)
class CheckSignal:
    """Si la señal que UN check declaró se cumplió — o si no se pudo saber.

    ADR 0162 (opción A). Es el par del recuento de tests un piso más abajo: mide
    por CHECK, que es donde el dato nace, y con los mismos tres estados que no se
    pueden colapsar.

    ``satisfied``:
      * ``True``  — la señal declarada se cumple.
      * ``False`` — no se cumple. El caso que el ADR persigue: exit 0 con cero
        tests ejecutados cuando el criterio pedía ``tests > 0``.
      * ``None``  — **no se pudo evaluar**: la señal no es de las que sabemos
        comprobar, o exigía un recuento y el recuento quedó AUSENTE. Nunca
        ``False``, que sería acusar al código del tenant de algo que sólo dice
        que no supimos leer la salida.

    Y no decide nada: ``TestRuntimeResult.all_passed()`` no lo mira.
    """

    check_id: str
    expected_signal: str
    exit_code: int
    satisfied: bool | None
    test_counts: TestCounts | None

    def as_dict(self) -> dict[str, Any]:
        """Forma JSON-safe: acaba en el JSONB de auditoría del test-runtime."""
        return {
            "check_id": self.check_id,
            "expected_signal": self.expected_signal,
            "exit_code": self.exit_code,
            "satisfied": self.satisfied,
            "test_counts": self.test_counts.as_dict() if self.test_counts is not None else None,
        }


@dataclass(frozen=True)
class _ChecksOutcome:
    """Lo que devuelve la fase de checks de un plan.

    Existe como tipo y no como tupla de cuatro porque el cuarto elemento
    —las señales— es información nueva y una tupla de cuatro posiciones es
    exactamente donde alguien acaba desempaquetando en el orden equivocado.
    """

    exit_codes: list[int]
    logs: str
    timed_out: bool
    signals: tuple[CheckSignal, ...]


@dataclass(frozen=True)
class RuntimePlan:
    """One ``(runtime, [checks])`` group ready to launch.

    The worker creates one ``TestRuntimeRunner.launch`` call per
    :class:`RuntimePlan` — different runtimes can run in parallel, but
    checks of the same runtime go through the same container to save
    pre_install cost.
    """

    template: RuntimeTemplate
    checks: tuple[AcceptanceCheck, ...]

    def undeclared_check_type_count(self) -> int:
        """Cuántos de estos checks corren sin que nadie declarase su tipo.

        Va al outcome como MÉTRICA, nunca como guarda: el ADR 0162 descarta
        expresamente bloquear por porcentaje —se aprende a jugar enseguida y
        castiga a los proyectos que legítimamente tienen poco que automatizar—.
        La diferencia con el estado anterior no es que se impida algo: es que
        antes ni siquiera se podía contar."""
        return sum(1 for check in self.checks if check.declared_check_type is None)


# Centinela para distinguir «la clave `check_type` NO ESTÁ» de «está y vale
# algo». `dict.get(k, "automated")` no sabe hacer esa distinción, y ahí estaba
# el defecto: el silencio se leía como una declaración.
_CHECK_TYPE_MISSING: Any = object()


def _coerce_check(entry: Mapping[str, Any]) -> AcceptanceCheck | None:
    """Best-effort coercion of one acceptance_criteria dict.

    Returns ``None`` when the entry is missing required fields or is a
    non-automated check (manual / human). The caller logs these as
    "skipped" so the user sees them in the worker output.

    **ADR 0162 — el silencio deja de significar «automated».** Esto era
    ``entry.get("check_type", "automated") != "automated"``: un criterio sin
    ``check_type`` se leía como «esto DEBERÍA verificarse a máquina», que es la
    misma regla que el ADR enuncia tres veces y descarta — *un valor ausente no
    puede significar nada más fuerte que «desconocido»*. Sin esa distinción no
    se puede separar «esta tarea no tiene nada que testear» (legítimo) de «esta
    tarea sí debía tenerlo y nadie lo declaró» (el defecto).

    Lo que **no** cambia es a quién se ejecuta: un criterio con ``runtime`` +
    ``command`` y sin ``check_type`` sigue ejecutándose exactamente como hoy, o
    las tareas que ya funcionan se quedarían sin tests de golpe. Lo que cambia
    es que queda constancia en :attr:`AcceptanceCheck.declared_check_type`.
    """
    raw_check_type = entry.get("check_type", _CHECK_TYPE_MISSING)
    if raw_check_type is _CHECK_TYPE_MISSING:
        # NO DECLARADO. Se ejecuta —comportamiento de siempre— pero se anota.
        declared_check_type: str | None = None
    elif str(raw_check_type) != "automated":
        # Declarado como manual / humano / vacío / nulo: se salta, como siempre.
        return None
    else:
        declared_check_type = "automated"
    runtime = entry.get("runtime")
    command = entry.get("command")
    if not runtime or not command:
        return None
    return AcceptanceCheck(
        id=str(entry.get("id") or ""),
        description=str(entry.get("description") or ""),
        runtime=str(runtime),
        command=str(command),
        expected_signal=str(entry.get("expected_signal") or "exit_code == 0"),
        timeout_s=int(entry["timeout_s"]) if entry.get("timeout_s") is not None else None,
        raw=dict(entry),
        declared_check_type=declared_check_type,
    )


def group_tasks_by_runtime(
    acceptance_criteria: Iterable[Mapping[str, Any]],
) -> tuple[RuntimePlan, ...]:
    """Group automated acceptance checks by their declared runtime.

    The catalog (:mod:`shared_test_runtimes.catalog`) resolves the
    runtime id to a :class:`RuntimeTemplate`. Unknown runtimes raise
    :class:`KeyError` — the caller is expected to surface this as a
    422 to the user (their task config references a runtime we don't
    ship).

    Plans are returned in the order their runtime first appears in the
    input. That makes the worker's launch order deterministic and
    matches what the user reads in the UI.
    """
    by_runtime: dict[str, list[AcceptanceCheck]] = {}
    for entry in acceptance_criteria:
        check = _coerce_check(entry)
        if check is None:
            continue
        by_runtime.setdefault(check.runtime, []).append(check)

    plans: list[RuntimePlan] = []
    for runtime_id, checks in by_runtime.items():
        template = get_template(runtime_id)
        plans.append(RuntimePlan(template=template, checks=tuple(checks)))
    return tuple(plans)


# ---------------------------------------------------------------------------
# task_06_16_03 — run_* runtime resolution by project stack
# ---------------------------------------------------------------------------

# The runtime the ``run_*`` docker_command tools fall back to when neither the
# project nor the tool pins one. ``run_pytest`` ships with
# ``implementation_ref='python-pytest'`` and the other three (``run_lint`` /
# ``run_typecheck`` / ``run_build``) ship with no ``implementation_ref`` at
# all — this constant is the single, backward-compatible default that keeps
# existing Python projects running pytest in ``python-pytest`` exactly as
# before Plan 06.16.
DEFAULT_RUN_RUNTIME_ID = "python-pytest"


class RuntimeResolutionError(ValueError):
    """A ``run_*`` tool referenced a runtime template we don't ship.

    Raised by :func:`resolve_run_runtime` when the resolved id (the
    project's ``default_runtime_template`` or the tool's
    ``implementation_ref``) is not in :mod:`shared_test_runtimes.catalog`.
    A subclass of :class:`ValueError` so existing ``except ValueError``
    boot-time handlers still catch it, while the message names the
    offending id + the known set so the operator sees a *clear* error
    instead of a bare ``KeyError`` crash.
    """


def resolve_run_runtime_id(
    *,
    project_default_runtime: str | None,
    tool_default_runtime: str | None,
) -> str:
    """Pick the runtime template id a ``run_*`` tool should execute in.

    Precedence (Plan 06.16 task_06_16_03):

      1. ``project_default_runtime`` (``projects.default_runtime_template``)
         when the project pins a stack — a PHP project with
         ``php-phpunit`` runs its ``run_*`` there, not in ``python-pytest``.
      2. the tool's own ``implementation_ref`` default when the project
         pins nothing (NULL) — e.g. ``run_pytest`` → ``python-pytest``.
      3. :data:`DEFAULT_RUN_RUNTIME_ID` as the final fallback for the
         ``run_*`` tools that carry no ``implementation_ref`` at all
         (``run_lint`` / ``run_typecheck`` / ``run_build``).

    Empty strings are treated as "unset" (the chips/UI never sends a tidy
    value). The returned id is NOT validated against the catalog here —
    use :func:`resolve_run_runtime` when you need the resolved template.
    """
    for candidate in (project_default_runtime, tool_default_runtime):
        if candidate and candidate.strip():
            return candidate.strip()
    return DEFAULT_RUN_RUNTIME_ID


def resolve_run_runtime(
    *,
    project_default_runtime: str | None,
    tool_default_runtime: str | None,
) -> RuntimeTemplate:
    """Resolve a ``run_*`` tool's :class:`RuntimeTemplate` from the stack.

    Combines :func:`resolve_run_runtime_id` (precedence: project default →
    tool default → ``python-pytest``) with the catalog lookup. An
    unknown/invalid id surfaces as a :class:`RuntimeResolutionError` with
    the known set spelled out — a clear error the operator can act on,
    never a bare ``KeyError`` taking the boot path down.
    """
    runtime_id = resolve_run_runtime_id(
        project_default_runtime=project_default_runtime,
        tool_default_runtime=tool_default_runtime,
    )
    try:
        return get_template(runtime_id)
    except KeyError as exc:
        # ``catalog.get`` already formats "unknown runtime template 'x';
        # known: a, b, …" — reuse that message verbatim so the operator
        # sees the same wording the rest of the platform uses.
        raise RuntimeResolutionError(str(exc).strip("\"'")) from exc


def resolve_run_runtime_image(
    project_default_runtime: str | None,
    tool_default_runtime: str | None,
) -> str:
    """Resolve a ``run_*`` tool's docker image from the project stack.

    The ``(project_default, tool_default) → image`` adapter the worker
    injects into the agent-runtime's ``tool_wiring.WiringContext`` as its
    ``runtime_image_resolver`` (Plan 06.16 task_06_16_03). Keeping the
    catalog lookup here means the agent-runtime never imports
    :mod:`shared_test_runtimes`. Raises :class:`RuntimeResolutionError`
    (a clear error) on an unknown/invalid runtime id.
    """
    template = resolve_run_runtime(
        project_default_runtime=project_default_runtime,
        tool_default_runtime=tool_default_runtime,
    )
    image: str = template.docker_image
    return image


# ---------------------------------------------------------------------------
# task_06_06 — Auxiliary services (postgres-test, redis-test, …)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuxServiceSpec:
    """One service the test-runtime can talk to over the private bridge.

    Defaults match the .docx's "postgres-test / redis-test
    parametrizables por proyecto" requirement: the worker spawns these
    on demand, alias-aliased inside the bridge so the test code can
    reach them via stable hostnames (``postgres-test``, ``redis-test``).
    """

    name: str
    image: str
    # Optional alias inside the bridge network. Defaults to ``name``.
    alias: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    # Healthcheck command run via ``docker exec`` after start. None
    # disables the wait — useful for short-lived helpers.
    healthcheck_cmd: tuple[str, ...] | None = None
    # Maximum seconds we'll poll the healthcheck before giving up.
    healthcheck_timeout_s: int = 30
    # Hardening caps (task_06_14_11 / container-isolation-1). When None
    # the runner falls back to the operator-tunable Settings defaults
    # (``aux_postgres_mem_limit`` / ``aux_redis_mem_limit`` /
    # ``aux_default_pids_limit``). Even a transient sidecar on the
    # private bridge gets cap-drop ALL + no-new-privileges + these caps
    # so a leak or fork-bomb cannot reach the host (CLAUDE.md §2).
    mem_limit: str | None = None
    pids_limit: int | None = None

    def resolved_alias(self) -> str:
        return self.alias or self.name


# Curated defaults for the two stacks every project asks for. The
# worker accepts user-provided AuxServiceSpec lists; these are just
# the names we register by default through `default_aux_services()`.
#
# ---------------------------------------------------------------------------
# FIJADAS POR DIGEST (prod-11 task_digest_pin_11 / gap5-3), 2026-08-19.
#
# Las 22 bases externas bajo `docker/` llevan `@sha256:` desde el 2026-07-31.
# Estas dos se quedaron fuera porque no viven en un Dockerfile sino aquí, en
# constantes de módulo, y la guarda que recorría Dockerfiles no las veía.
#
# No es un hueco de inventario. Son los ÚNICOS contenedores del sistema que
# comparten la red per-tarea con código no confiable: el agente escribe los
# tests que se ejecutan al lado de este postgres. Con un tag rodante, la
# pregunta que se hace después de un incidente —«¿qué binario corrió en ese
# run?»— no tiene respuesta.
#
# LA REGLA DURA DE LA FASE ES «sin refresco automático, no se pinea», y aquí el
# vehículo NO es Dependabot: su ecosistema `docker` parsea Dockerfiles y ficheros
# compose, no fuentes Python. El plan dejó cuatro salidas abiertas; se toma la
# (b) —pinear y revisar a mano, con la fecha escrita— y se descarta la (a)
# (mover las referencias a un fichero que exista sólo para que un bot lo lea) y
# la (c) (dejarlas por tag), porque «sidecar efímero» describe su ciclo de vida,
# no su vecindario: comparten red con el código del agente.
# El coste de (b) es honesto y está acotado: dos líneas cada revisión.
# Procedimiento y calendario en docs/06-runbooks/triage-vulnerabilidades.md.
#
# El tag legible va DENTRO de la referencia (`postgres:16-alpine@sha256:…`), no
# en un comentario al lado: así el operador que lee un `docker ps` sabe qué
# versión es, y el día que se cambie el digest sin cambiar el tag se ve en el
# diff. Ambos resueltos contra el registry con `docker buildx imagetools
# inspect` (digest del ÍNDICE multi-arch, no el del manifest de una plataforma:
# fijar el de amd64 rompería el arranque en un host arm64).
#
# review: 2026-11-19
# ---------------------------------------------------------------------------
DEFAULT_POSTGRES = AuxServiceSpec(
    name="postgres-test",
    # postgres:16-alpine == 16.15-alpine3.24 (resuelto 2026-08-19)
    image=(
        "postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685"
    ),
    env={
        "POSTGRES_USER": "test",
        "POSTGRES_PASSWORD": "test",
        "POSTGRES_DB": "test",
        "POSTGRES_INITDB_ARGS": "--encoding=UTF8",
    },
    healthcheck_cmd=("pg_isready", "-U", "test", "-d", "test"),
    # Postgres needs a touch more headroom than redis for shared_buffers.
    mem_limit="256m",
)

DEFAULT_REDIS = AuxServiceSpec(
    name="redis-test",
    # redis:7-alpine == 7.4.10-alpine (resuelto 2026-08-19). Ver el bloque de
    # DEFAULT_POSTGRES para el porqué del pin y su calendario de revisión.
    image=(
        "redis:7-alpine@sha256:e7723ff73d963f5cc6d9c4643ea3d989527a402a319239054e9472a7fb9219a2"
    ),
    healthcheck_cmd=("redis-cli", "ping"),
    mem_limit="128m",
)


def default_aux_services() -> tuple[AuxServiceSpec, ...]:
    """The two services every project gets by default."""
    return (DEFAULT_POSTGRES, DEFAULT_REDIS)


# The redis-test stack is the one we recognise by name when an aux spec
# leaves ``mem_limit`` unset, so we can pick the right operator default.
_REDIS_MEM_HINT = "redis"

# Common lockdown applied to every aux sidecar AND the DinD proxy: minimal
# Linux capabilities + no privilege escalation through setuid binaries.
# Mirrors :func:`isolation.build_hardened_run_kwargs` (same principles,
# CLAUDE.md §2) without the read-only root / non-root uid bits, which the
# stateful sidecars (postgres/redis write their data dirs as root) can't
# take. The resource caps are what bound a runaway / fork-bomb.
_AUX_SECURITY_OPT = ["no-new-privileges:true"]

# The capabilities the OFFICIAL images need to initialise as root and then
# drop to their service user (`gosu postgres`, `su-exec redis`, `gosu mysql`):
# chown the data dir, override DAC on it, and setuid/setgid to drop. This is
# the SAME set the main compose grants those images as `x-infra-caps`, and the
# gotcha `docker-cap-drop-all-breaks-official-images.md` documents why a bare
# `cap_drop ALL` crash-loops them. Audit 2026-09-01 (B-02): the sidecars were
# launched with `cap_drop ALL` and NO cap_add, so every project declaring
# `services` failed at `_wait_healthy` — and the only "test" was a MagicMock.
_AUX_CAP_ADD = ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"]


def build_aux_run_kwargs(
    settings: Settings,
    aux: AuxServiceSpec,
    network_name: str,
) -> dict[str, Any]:
    """Build the hardened ``docker.containers.run`` kwargs for one aux service.

    Extracted as a module-level helper (task_06_14_11) so the hardening
    envelope is testable the same way ``isolation.build_hardened_run_kwargs``
    is — assert cap_drop ALL + no-new-privileges + mem/pids caps without a
    live daemon. The mem/pids caps fall back to the operator-tunable
    Settings when the spec leaves them unset; the per-spec values
    (``DEFAULT_POSTGRES`` 256m / ``DEFAULT_REDIS`` 128m) win when present.
    """
    if aux.mem_limit is not None:
        mem_limit = aux.mem_limit
    elif _REDIS_MEM_HINT in aux.image.lower() or _REDIS_MEM_HINT in aux.name.lower():
        mem_limit = settings.aux_redis_mem_limit
    else:
        mem_limit = settings.aux_postgres_mem_limit
    pids_limit = aux.pids_limit if aux.pids_limit is not None else settings.aux_default_pids_limit
    return {
        "detach": True,
        "environment": dict(aux.env),
        "network": network_name,
        "network_mode": None,
        "hostname": aux.resolved_alias(),
        "cap_drop": ["ALL"],
        "cap_add": list(_AUX_CAP_ADD),
        "security_opt": list(_AUX_SECURITY_OPT),
        "mem_limit": mem_limit,
        "pids_limit": pids_limit,
        "labels": {**_TEST_LABELS, "com.agentic-platform.role": "aux-service"},
    }


# ---------------------------------------------------------------------------
# task_06_05 — Launching the test-runtime
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TestRuntimeSpec:
    """Everything the runner needs for one container launch."""

    plan: RuntimePlan
    # Host path of the task's worktree. Mounted at
    # ``plan.template.workspace_mount_path`` (default /workspace).
    worktree_host_path: str
    # Host path of the (shared) dep-cache. Only mounted if the template
    # declares ``dep_cache_mount``.
    dep_cache_host_path: str | None = None
    # Aux services to bring up on the task's bridge.
    aux_services: tuple[AuxServiceSpec, ...] = ()
    # Extra env injected into the MAIN container (ADR 0129): the connection vars
    # (DATABASE_URL, REDIS_URL, …) derived from the project's declared services
    # plus the project's own `env`. Merged AFTER the template/cache/egress env,
    # so it can override those; never overrides HOME (set from the template).
    main_env: Mapping[str, str] = field(default_factory=dict)
    # Override the template's default cpu/memory caps.
    cpu: float | None = None
    memory_mb: int | None = None
    # Override the template's default network policy.
    network_policy: str | None = None
    # Per-launch opt-in for proxied registry egress (ADR 0094). When True the
    # worker transiently attaches the allowlisted ``registry-proxy`` to this
    # task's internal bridge and injects HTTP(S)_PROXY so dependency installs
    # (composer/pip/npm/go/…) resolve. Independent of the stack: the call site
    # decides (stack_exec + cold-cache pre_install set it). The bridge stays
    # ``internal=True`` regardless — never raw NAT.
    dep_egress: bool = False
    # ADR 0162 (decisión 1): la raíz del PROYECTO dentro del worktree, relativa a
    # la raíz de éste (p. ej. ``ci4build``). ``None``/``""`` = el proyecto vive en
    # la raíz, que es el comportamiento de siempre. Va aquí y no en cada llamada
    # porque las dos bocas que deciden —``default_pre_install`` y los acceptance
    # checks— no tienen ningún parámetro por el que recibirla: corrían desde
    # ``/workspace`` hiciera lo que hiciera el agente.
    project_root: str | None = None


@dataclass(frozen=True)
class TestRuntimeResult:
    """Outcome of one :meth:`TestRuntimeRunner.launch` call.

    The per-check breakdown is what feeds Plan 06 Fase D's TestReport.
    ``logs`` is the *concatenated* stdout/stderr of every check command
    in the order they ran.
    """

    runtime: str
    exit_codes: tuple[int, ...]
    logs: str
    container_id: str
    timed_out: bool
    network_name: str
    # ADR 0162 (ola 1): cuántos tests corrieron de verdad, o ``None`` cuando no
    # se pudo saber. Los TRES estados del ADR viven en este campo y no se pueden
    # colapsar: ``TestCounts(total=N)`` = parseado con N; ``TestCounts(total=0)``
    # = parseado y CERO tests (el `No tests executed!` con exit 0 de la BD viva);
    # ``None`` = NO SE PUDO MEDIR. Nunca ``TestCounts(total=0)`` para este
    # último: eso le diría al reviewer que el cambio no ejecutó ni un test
    # cuando lo único cierto es que no supimos leer la salida — un falso fallo.
    test_counts: TestCounts | None = None
    # Cuántos de los checks que se ejecutaron no traían `check_type` declarado.
    # Métrica, no guarda (ver :meth:`RuntimePlan.undeclared_check_type_count`).
    checks_without_declared_check_type: int = 0
    # ADR 0162 (opción A): si la señal que declaró CADA check se cumplió. Vacío
    # cuando no llegó a ejecutarse ninguno —un `pre_install` que falla, p. ej.—,
    # y eso es honesto: no hay señal que reportar de un check que no corrió.
    # Rellenarlo de `False` ahí diría que los criterios no se cumplieron, que es
    # acusar al código del tenant de un fallo de la plataforma.
    check_signals: tuple[CheckSignal, ...] = ()

    def all_passed(self) -> bool:
        """El veredicto, y NO lo tocan los recuentos de arriba.

        Deliberadamente sigue saliendo sólo del código de salida, así que un
        ``No tests executed!`` con exit 0 **sigue dando verde**, y lo sigue dando
        aunque :attr:`check_signals` diga que la señal declarada NO se cumple. No
        es un descuido: el gate es la opción C del ADR 0162 y no está firmada,
        precisamente porque ahí viven los falsos fallos. Esta ola hace visible
        el falso verde; cerrarlo es otra decisión y otra firma."""
        return not self.timed_out and all(rc == 0 for rc in self.exit_codes)


class TestRuntimeRunner:
    """Launches one test-runtime per :class:`RuntimePlan`.

    The runner owns the *Docker side* — creating the task's private
    bridge, starting aux services, starting the optional DinD proxy,
    starting the main test container, executing each check command in
    sequence, then tearing the whole compose down.

    El parseo de la salida sigue viviendo en ``shared_test_runtimes.parsers``;
    lo que el runner sí hace desde el ADR 0162 es LLAMARLO
    (:meth:`_count_tests`), para que el resultado diga cuántos tests corrieron.
    Esos parsers llevaban escritos desde el Plan 06 y nadie en ``apps/`` los
    importaba, así que la plataforma no distinguía una suite de 200 tests en
    verde de un ``--filter`` que no casó con nada.
    """

    def __init__(self, settings: Settings, *, client: Any = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    # --- public ---------------------------------------------------------

    def launch(self, spec: TestRuntimeSpec) -> TestRuntimeResult:
        """Launch the test-runtime for one :class:`RuntimePlan`.

        Always tears down every container + network it created, even
        on failure / timeout. The bridge name is randomised so two
        concurrent tasks on the same worker host never share a network.
        """
        network = self._create_bridge(spec)
        aux_containers: list[Any] = []
        registry_proxy: Any = None
        main_container: Any = None
        try:
            aux_containers = self._start_aux_services(spec, network.name)
            if self._egress_enabled(spec):
                registry_proxy = self._attach_registry_proxy(network)
            main_container = self._start_main(spec, network.name, egress=registry_proxy is not None)
            # ADR 0094 D2: pre_install needs egress; the check phase must NOT.
            failed_codes, pre_logs = self._run_pre_install(spec, main_container)
            if registry_proxy is not None:
                self._detach_proxy(network, registry_proxy)
                registry_proxy = None
            undeclared = spec.plan.undeclared_check_type_count()
            if failed_codes is not None:
                # El `pre_install` falló: no llegó a correr ni un check, así que
                # el recuento queda AUSENTE. Poner cero aquí sería decir «este
                # cambio no ejecutó ningún test» cuando lo cierto es que no se
                # llegó a intentar — la confusión (c)→(b) del ADR 0162.
                return TestRuntimeResult(
                    runtime=spec.plan.template.id,
                    exit_codes=tuple(failed_codes),
                    logs=pre_logs,
                    container_id=getattr(main_container, "id", "") or "",
                    timed_out=False,
                    network_name=network.name,
                    checks_without_declared_check_type=undeclared,
                )
            checks = self._run_test_checks(spec, main_container)
            return TestRuntimeResult(
                runtime=spec.plan.template.id,
                exit_codes=tuple(checks.exit_codes),
                logs=pre_logs + checks.logs,
                container_id=getattr(main_container, "id", "") or "",
                timed_out=checks.timed_out,
                network_name=network.name,
                # Se cuenta sobre los logs de los CHECKS, NO sobre
                # `pre_logs + checks.logs`: la salida de `composer install` /
                # `npm ci` no es un informe de tests, y meterla en el texto sólo
                # añade formas de que un reconocedor vea un epílogo donde no lo
                # hay.
                test_counts=self._count_tests(spec, checks.logs),
                checks_without_declared_check_type=undeclared,
                check_signals=checks.signals,
            )
        finally:
            self._cleanup(
                main_container,
                aux_containers,
                network,
                registry_proxy=registry_proxy,
            )

    def run_command(
        self,
        spec: TestRuntimeSpec,
        command: str,
        *,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        cwd: str | None = None,
    ) -> tuple[int, str]:
        """Run ONE ad-hoc command in the stack runtime (ADR 0093 / ``stack_exec``).

        The agent asks the worker (via ``/internal/agent/run-stack``) to run a
        stack command — ``composer install`` / ``vendor/bin/phpunit`` /
        ``php spark`` — in the project's runtime template, over the task's
        worktree (RW). Mirrors :meth:`launch`'s envelope (private bridge, aux
        services, hardened main container, guaranteed cleanup) but executes a
        SINGLE caller-provided command instead of the plan's acceptance checks,
        and does NOT run ``default_pre_install`` — the agent's own
        ``composer install`` IS the install; running pre_install too would double
        it. Returns ``(exit_code, logs)``; an exit_code of 124 means the command
        was killed by the timeout wrapper. Always tears down.
        """
        network = self._create_bridge(spec)
        aux_containers: list[Any] = []
        registry_proxy: Any = None
        main_container: Any = None
        try:
            aux_containers = self._start_aux_services(spec, network.name)
            if self._egress_enabled(spec):
                registry_proxy = self._attach_registry_proxy(network)
            main_container = self._start_main(spec, network.name, egress=registry_proxy is not None)
            # stack_exec: egress stays attached for the whole command — the
            # command IS the install (ADR 0094 D2). ``cwd`` (ADR 0093, 2026-07-24)
            # runs the command in a SUBDIRECTORY of the worktree (e.g. a project
            # scaffolded under ``ci4build/``) so the toolchain bootstraps with the
            # right relative paths; cuando el agente no lo pide, manda la raíz
            # declarada del proyecto (ADR 0162).
            return self._exec(
                main_container,
                command,
                timeout_s=timeout_s,
                cwd=effective_cwd(cwd, spec.project_root),
            )
        finally:
            self._cleanup(
                main_container,
                aux_containers,
                network,
                registry_proxy=registry_proxy,
            )

    # --- bridge ---------------------------------------------------------

    def _create_bridge(self, spec: TestRuntimeSpec) -> Any:
        """Create a one-shot internal bridge for this task.

        ``internal=True`` ALWAYS (ADR 0094 D1) — the per-task bridge never
        gets raw NAT. The only connectivity the container has is to the
        sidecars sharing the bridge, plus the allowlisted ``registry-proxy``
        when a launch asks for egress (see :meth:`_attach_registry_proxy`).
        The legacy ``network_policy='open'`` raw-NAT path is gone; ``open``
        is now an alias of ``registries`` (proxied egress)."""
        suffix = secrets.token_hex(4)
        name = f"test-runtime-{spec.plan.template.id}-{suffix}"
        return self.client.networks.create(
            name,
            driver="bridge",
            internal=True,
            labels=dict(_TEST_LABELS),
        )

    # --- registry egress (ADR 0094) -------------------------------------

    def _egress_enabled(self, spec: TestRuntimeSpec) -> bool:
        """Whether this launch should get proxied egress to the registries.

        Per-launch ``dep_egress`` is the primary control; a template/spec
        ``network_policy`` of ``registries``/``open`` also opts in."""
        if spec.dep_egress:
            return True
        policy = spec.network_policy or spec.plan.template.network_policy
        return policy in ("registries", "open")

    def _attach_registry_proxy(self, network: Any) -> Any:
        """Connect the allowlisted ``registry-proxy`` onto this task's internal
        bridge so the runtime resolves package registries through it (ADR 0094).

        Returns the proxy container, or ``None`` when no proxy is configured /
        reachable — in which case the runtime stays offline (cold installs fail,
        same posture as before). The proxy is a long-lived shared service: we
        only CONNECT it here and DISCONNECT at teardown; we never remove it."""
        name = self._settings.registry_proxy_container
        if not self._settings.registry_proxy_url or not name:
            _log.warning(
                "registry_egress_requested_but_unconfigured",
                detail="registry_proxy_url/container unset; runtime stays offline",
            )
            return None
        try:
            proxy = self.client.containers.get(name)
        except Exception as exc:  # NotFound / APIError — proxy not running
            _log.warning("registry_proxy_unavailable", container=name, error=str(exc))
            return None
        network.connect(proxy, aliases=[self._settings.registry_proxy_alias])
        return proxy

    def _detach_proxy(self, network: Any, proxy: Any) -> None:
        """Disconnect (NEVER remove) the shared registry-proxy from ``network``.

        Idempotent + best-effort: a double-detach or a torn-down network is
        swallowed. ``network.remove()`` would fail while the proxy endpoint is
        still attached, so this also gates a clean teardown."""
        if proxy is None:
            return
        with contextlib.suppress(Exception):
            network.disconnect(proxy, force=True)

    # --- aux services ---------------------------------------------------

    def _start_aux_services(
        self,
        spec: TestRuntimeSpec,
        network_name: str,
    ) -> list[Any]:
        """Bring up each aux service on the task's bridge.

        Each sidecar gets the hardened envelope (cap_drop ALL +
        no-new-privileges + mem/pids caps) via
        :func:`build_aux_run_kwargs` — task_06_14_11."""
        started: list[Any] = []
        for aux in spec.aux_services:
            run_kwargs = build_aux_run_kwargs(self._settings, aux, network_name)
            # ADR 0148 (condición 2) + prod-11 task_digest_pin_11: se lanza la
            # referencia CANÓNICA por digest, no el tag del que salió. Pasar el
            # tag aquí después de haber descargado el digest deja al daemon
            # elegir otra vez, y puede elegir distinto — el pin quedaría en
            # decoración. Si el pull falla se ABORTA: levantar «un postgres
            # cualquiera» bajo el nombre correcto, al lado del código no
            # confiable que el agente escribe, daría un run verde que nadie
            # puede auditar. Una referencia sin digest (aux declarada por un
            # proyecto, ADR 0129) pasa intacta.
            image = ensure_runtime_image(self.client, aux.image)
            container = self.client.containers.run(image, **run_kwargs)
            started.append(container)
            if aux.healthcheck_cmd is not None:
                self._wait_healthy(container, aux)
        return started

    def _wait_healthy(self, container: Any, aux: AuxServiceSpec) -> None:
        """Poll ``healthcheck_cmd`` until it returns 0 or we time out."""
        import time

        if aux.healthcheck_cmd is None:
            return
        cmd = list(aux.healthcheck_cmd)
        deadline = time.monotonic() + aux.healthcheck_timeout_s
        last_rc: int | None = None
        while time.monotonic() < deadline:
            exec_result = container.exec_run(cmd)
            last_rc = getattr(exec_result, "exit_code", None)
            if last_rc == 0:
                return
            time.sleep(0.5)
        raise RuntimeError(
            f"aux service {aux.name!r} did not become healthy within "
            f"{aux.healthcheck_timeout_s}s (last rc={last_rc})"
        )

    # --- DinD proxy -----------------------------------------------------

    # --- main container -------------------------------------------------

    def _start_main(self, spec: TestRuntimeSpec, network_name: str, *, egress: bool = False) -> Any:
        """Launch the test-runtime container (no checks yet).

        Splitting *start* from *run* is what lets ``launch`` register
        the container for cleanup BEFORE we ``exec_run`` anything; an
        ``exec_run`` that raises mid-sequence still leaves the
        container in our finally block. ``egress`` injects the proxy +
        git-https env when this launch has proxied registry egress.
        """
        template = spec.plan.template
        run_kwargs = self._build_test_kwargs(spec, network_name, egress=egress)
        assert_no_docker_socket(run_kwargs)
        # ADR 0148: se lanza la MISMA referencia que se acaba de resolver por
        # digest, no el tag del catálogo — o el daemon podría elegir otra cosa.
        image = ensure_runtime_image(self.client, template.docker_image)
        return self.client.containers.run(image, **run_kwargs)

    def _run_pre_install(
        self,
        spec: TestRuntimeSpec,
        container: Any,
    ) -> tuple[list[int] | None, str]:
        """Run ``default_pre_install`` in order.

        Returns ``(None, logs)`` on success, or ``([rc]*n_checks, logs)`` if a
        command fails — so the caller marks every check failed (couldn't even
        run them) and the reporter shows the failed install, not fake test
        failures. Pre_install runs while egress is attached (ADR 0094 D2).

        ADR 0162: corre bajo ``spec.project_root``. Sin eso, un proyecto anidado
        instalaba desde la raíz del worktree y ``composer`` contestaba «could not
        find a composer.json file in /workspace» — el fallo más repetido de la
        medición."""
        template = spec.plan.template
        all_logs: list[str] = []
        for cmd in template.default_pre_install:
            exec_rc, exec_logs = self._exec(
                container, cmd, timeout_s=DEFAULT_TIMEOUT_S, cwd=spec.project_root
            )
            all_logs.append(f"--- pre_install: {cmd}\n{exec_logs}\n")
            if exec_rc != 0:
                return [exec_rc] * len(spec.plan.checks), "".join(all_logs)
        return None, "".join(all_logs)

    def _run_test_checks(
        self,
        spec: TestRuntimeSpec,
        container: Any,
    ) -> _ChecksOutcome:
        """Run each acceptance check and report what happened with each one.

        Runs AFTER pre_install and AFTER egress is dropped (ADR 0094 D2), so the
        test phase has no network path off its internal bridge.

        ADR 0162: cada check corre bajo ``spec.project_root``, como el
        pre_install. Es la boca que decide si una tarea pasa, y era la que menos
        sabía dónde está el proyecto.

        ADR 0162 (opción A): además del código de salida, aquí se evalúa la señal
        que el criterio DECLARÓ, y se evalúa **con la salida de ese check y sólo
        de ese check**. Contar sobre el log del plan entero dejaría que el
        epílogo de otro check —o el ruido de un ``composer install``— contestara
        por él, que es la clase de respuesta silenciosamente falsa que este ADR
        persigue en todas sus formas."""
        all_logs: list[str] = []
        exit_codes: list[int] = []
        signals: list[CheckSignal] = []
        timed_out = False

        for check in spec.plan.checks:
            budget = check.timeout_s or DEFAULT_TIMEOUT_S
            exec_rc, exec_logs = self._exec(
                container, check.command, timeout_s=budget, cwd=spec.project_root
            )
            all_logs.append(
                f"--- check {check.id or check.description!r}: {check.command}\n{exec_logs}\n"
            )
            exit_codes.append(exec_rc)
            signals.append(self._check_signal(spec, check, exit_code=exec_rc, logs=exec_logs))
            if exec_rc == 124:
                # 124 is the conventional "timeout" exit code from GNU
                # timeout / our exec wrapper. Stop running further
                # checks — something is wedged.
                timed_out = True
                break

        return _ChecksOutcome(
            exit_codes=exit_codes,
            logs="".join(all_logs),
            timed_out=timed_out,
            signals=tuple(signals),
        )

    def _check_signal(
        self,
        spec: TestRuntimeSpec,
        check: AcceptanceCheck,
        *,
        exit_code: int,
        logs: str,
    ) -> CheckSignal:
        """Evaluar la señal de UN check contra su propia salida (ADR 0162).

        El ``except`` ancho es la misma decisión que en :meth:`_count_tests` y por
        la misma razón: la evaluación es nueva y el veredicto lleva años
        funcionando. Un bug aquí pierde la señal —que queda AUSENTE, dicho
        honestamente— y no puede tumbar una fase de tests que ya terminó.
        """
        counts = self._count_tests(spec, logs)
        try:
            satisfied = evaluate_signal(check.expected_signal, exit_code=exit_code, counts=counts)
        except Exception as exc:
            _log.error(
                "check_signal_evaluation_failed",
                runtime=spec.plan.template.id,
                check_id=check.id,
                error_type=exc.__class__.__name__,
                error=str(exc),
            )
            satisfied = None
        return CheckSignal(
            check_id=check.id or check.description,
            expected_signal=check.expected_signal,
            exit_code=exit_code,
            satisfied=satisfied,
            test_counts=counts,
        )

    def _count_tests(self, spec: TestRuntimeSpec, check_logs: str) -> TestCounts | None:
        """Cuántos tests corrieron, según los parsers que declara la plantilla.

        **Enciende los ocho parsers de ``shared_test_runtimes.parsers``**, que
        llevaban escritos desde el Plan 06 sin que nadie en ``apps/`` los
        importara. La plantilla declara cuáles y en qué orden
        (``output_parsers``); el catálogo ya expresa la preferencia.

        Devuelve ``None`` cuando no se pudo medir, y eso **no es cero**: son los
        estados (c) y (b) del ADR 0162 y confundirlos fabrica un falso fallo.

        **Granularidad, dicha para que nadie lea de más:** el recuento es del
        PLAN (un runtime), no de cada check. Se cuenta sobre el log concatenado
        de sus checks, así que si un plan lleva varios y sólo se entiende el
        epílogo de algunos, el total es la suma de LOS ENTENDIDOS — un
        subrecuento, no una medida completa. Es honesto para el caso normal (un
        check por plan) y se prefirió a la alternativa —declarar AUSENTE el plan
        entero en cuanto un check no se entienda—, que tiraría la señal buena
        junto con la que falta.

        El ``except`` ancho no es pereza y no tapa nada que decida: la medición
        es nueva, el veredicto lleva años funcionando, y un bug del contador no
        puede tumbar una fase de tests que ya terminó. Lo que se pierde es el
        recuento —que queda AUSENTE, dicho honestamente— y queda el log.
        """
        try:
            return count_tests(
                check_logs,
                runtime=spec.plan.template.id,
                parsers=tuple(spec.plan.template.output_parsers),
            )
        except Exception as exc:
            _log.error(
                "test_counts_failed",
                runtime=spec.plan.template.id,
                error_type=exc.__class__.__name__,
                error=str(exc),
            )
            return None

    def _build_test_kwargs(
        self,
        spec: TestRuntimeSpec,
        network_name: str,
        *,
        egress: bool = False,
    ) -> dict[str, Any]:
        """Build ``docker.containers.run`` kwargs for the main test
        container.

        Mirrors :func:`isolation.build_hardened_run_kwargs` but with
        the *bridge* of this task (so the test container can reach the
        aux services and the optional DinD proxy)."""
        template = spec.plan.template
        cpu = spec.cpu if spec.cpu is not None else template.default_resources.cpu
        mem_mb = (
            spec.memory_mb if spec.memory_mb is not None else template.default_resources.memory_mb
        )

        mounts: list[Mount] = [
            Mount(
                target=template.workspace_mount_path,
                source=spec.worktree_host_path,
                type="bind",
                read_only=False,
            )
        ]
        if template.dep_cache_mount and spec.dep_cache_host_path:
            mounts.append(
                Mount(
                    target=template.dep_cache_mount,
                    source=spec.dep_cache_host_path,
                    type="bind",
                    read_only=False,
                )
            )

        # C-01 (task_wf_20): esto era `HOME = workspace_mount_path`, o sea el
        # WORKTREE bind-montado en RW. Todo lo que la toolchain escribe «en el
        # home» (`~/.composer/auth.json`, `~/.npmrc`, `~/.cache/…`) aterrizaba
        # dentro del repo del proyecto, y `commit_task` hace `git add -A`: acaba
        # comiteado. Es el mismo bug que ya se corrigió en el agent-runtime, y
        # contradecía a la vez el comentario de tres líneas más abajo y las
        # propias imágenes, que declaran `ENV HOME=/home/agent` con el
        # directorio creado y `chown 1000:1000` (prod-12 img_01).
        env: dict[str, str] = {"HOME": AGENT_HOME}
        # Align the tool's $HOME-relative cache with the bind-mounted
        # dep_cache_mount (ADR 0094) — injected always; a warm cache helps even
        # offline acceptance runs. Won't override HOME (templates never set it).
        env.update(dict(template.cache_env))
        if egress:
            # Route the runtime's HTTP(S) through the allowlisted registry-proxy
            # the worker attached to this bridge, and force git deps to HTTPS so
            # they traverse it (ADR 0094). The bridge stays internal — this env
            # is just how the client finds the proxy, not the security boundary.
            proxy_url = self._settings.registry_proxy_url
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                env[key] = proxy_url
            env.update(_GIT_HTTPS_ENV)

        # ADR 0129: project connection env (DATABASE_URL/REDIS_URL/…) + the
        # project's own env, applied LAST so it wins — but never clobber HOME
        # (the template owns it and the toolchain caches hang off it).
        for k, v in spec.main_env.items():
            if k != "HOME":
                env[str(k)] = str(v)

        return {
            # Keep the container alive for exec_run via ENTRYPOINT, NOT a
            # `command`: the runtime images already declare
            # ``ENTRYPOINT ["sleep","infinity"]``, so passing command
            # ``["sleep","infinity"]`` too makes the daemon run
            # ``sleep infinity sleep infinity`` → "invalid time interval 'sleep'"
            # → the container exits at once and every exec_run 409s
            # ("not running"). Overriding entrypoint (no command) runs exactly
            # ``sleep infinity`` regardless of the image default.
            "entrypoint": ["sleep", "infinity"],
            "detach": True,
            "network": network_name,
            "mounts": mounts,
            "environment": env,
            "cap_drop": ["ALL"],
            # C-02 (task_wf_21): esto era `["no-new-privileges:true"]` a secas.
            # Este contenedor ejecuta el MISMO tipo de código no controlado que
            # el del agente — la toolchain del proyecto sobre el worktree — pero
            # los perfiles seccomp/apparmor que el operador configura solo se
            # aplicaban allí: existían en disco y aquí no se cableaban.
            "security_opt": build_security_opt(self._settings),
            # Y sin `pids_limit` un `make -j` desbocado o una fork-bomb del repo
            # bajo prueba no tenían tope. Ajuste propio y MÁS alto que el del
            # agente: un contenedor de tests arranca legítimamente más procesos
            # (compiladores en paralelo, watchers, servidores de prueba), y
            # heredar el 256 del agente cambiaría un riesgo por un falso negativo.
            "pids_limit": self._settings.test_runtime_pids_limit,
            "read_only": True,
            # La raíz va en solo lectura, así que el HOME necesita su propio
            # tmpfs o la toolchain se come un EROFS al escribir en él. NO
            # `noexec`: las toolchains ejecutan binarios desde su caché de home
            # (`~/.composer/vendor/bin`, npx), igual que el `/workspace` del
            # agent-runtime. El `dep_cache_mount` de la plantilla apunta DENTRO
            # de este home y se monta encima (Docker ordena los montajes por
            # profundidad del destino), así que la caché caliente sigue siendo
            # el bind y el tmpfs solo carga metadatos sueltos.
            "tmpfs": {
                # F3 (registry-egress-followups): era un literal de 64m mientras el
                # HOME de al lado ya era configurable. Por aquí pasan `composer
                # install` y `npm ci` —descargan y extraen en /tmp—, así que un
                # árbol de deps grande se quedaba sin sitio en frío. Sin `noexec`
                # por el mismo motivo que el HOME: los instaladores ejecutan desde
                # sus temporales.
                "/tmp": f"rw,nosuid,size={self._settings.test_runtime_tmp_size}",
                AGENT_HOME: (
                    f"rw,nosuid,size={self._settings.test_runtime_home_size},uid=1000,gid=1000"
                ),
            },
            "user": AGENT_UID_GID,
            # Use nano_cpus rather than --cpus so we round-trip safely
            # through json: int suffix vs float decimals.
            "nano_cpus": int(cpu * 1_000_000_000),
            "mem_limit": f"{mem_mb}m",
            "labels": {**_TEST_LABELS, "com.agentic-platform.runtime": template.id},
        }

    def _exec(
        self, container: Any, command: str, *, timeout_s: int, cwd: str | None = None
    ) -> tuple[int, str]:
        """Run one shell command inside the container, return rc + logs.

        We use ``exec_run`` rather than spawning a fresh container per
        check so the pre_install cost is amortised over all checks of
        the same runtime. ``timeout_s`` is not honored by ``exec_run``
        directly — we wrap the command in ``timeout`` so the test
        cannot wedge indefinitely.

        ``cwd`` (optional, ADR 0093) runs the command from a subdirectory of the
        worktree (``cd <cwd> && …`` relative to the container's ``/workspace``
        WORKDIR). Validated to stay INSIDE the worktree (no absolute path, no
        ``..`` traversal) — a project scaffolded under e.g. ``ci4build/`` runs
        its toolchain there instead of failing from the worktree root."""
        effective = _apply_cwd(command, cwd)
        # `task_cv_45` (B-08): sin `-k`, `timeout` sólo manda SIGTERM y un
        # proceso que lo ignora cuelga el hilo del worker sin techo.
        wrapped = f"timeout -k 10 {timeout_s} sh -c {_shell_quote(effective)}"
        result = container.exec_run(["sh", "-c", wrapped], demux=False)
        rc = getattr(result, "exit_code", 0) or 0
        out_bytes: bytes = getattr(result, "output", b"") or b""
        return rc, out_bytes.decode("utf-8", errors="replace")

    # --- cleanup --------------------------------------------------------

    def _cleanup(
        self,
        main_container: Any,
        aux_containers: list[Any],
        network: Any,
        *,
        registry_proxy: Any = None,
    ) -> None:
        # Disconnect (NEVER remove) the shared registry-proxy first — a left-over
        # endpoint makes network.remove() fail (ADR 0094). No-op if already
        # detached before the check phase, or if egress was never attached.
        self._detach_proxy(network, registry_proxy)
        for container in [main_container, *aux_containers]:
            if container is None:
                continue
            with contextlib.suppress(Exception):
                # `v=True`: postgres/mysql/redis declaran `VOLUME`, y sin esto cada
                # sidecar deja un volumen anónimo que el proxy (`VOLUMES=0`) no
                # deja podar desde el worker (auditoría 2026-09-01, B-02).
                container.remove(force=True, v=True)
        with contextlib.suppress(Exception):
            network.remove()


def _shell_quote(command: str) -> str:
    """Single-quote a command for safe embedding inside ``sh -c``."""
    return "'" + command.replace("'", "'\"'\"'") + "'"


class InvalidCwdError(ValueError):
    """Raised when a ``stack_exec`` ``cwd`` would escape the worktree or carries
    unsafe characters."""


def effective_cwd(explicit: str | None, project_root: str | None) -> str | None:
    """Qué directorio manda cuando hay dos candidatos (ADR 0162, decisión 1).

    Precedencia: el ``cwd`` explícito de la llamada del agente GANA sobre la raíz
    declarada del proyecto —su petición es más concreta que la configuración: en
    un monorepo puede estar tocando otro subproyecto—; sin él manda
    ``project_root``; sin ninguno de los dos, la raíz del worktree, que es el
    comportamiento de siempre.

    Un valor en blanco cuenta como AUSENTE, no como «corre en la raíz»: el agente
    que omite el parámetro (el 46 % medido) no está decidiendo nada, y tratarlo
    como decisión tiraría por tierra la configuración del proyecto justo en el
    caso que esto viene a arreglar.
    """
    if explicit and explicit.strip():
        return explicit
    if project_root and project_root.strip():
        return project_root
    return None


def _apply_cwd(command: str, cwd: str | None) -> str:
    """Prefix ``command`` with ``cd <cwd> &&`` when a working directory is given.

    ``cwd`` is a path RELATIVE to the worktree root (the container's
    ``/workspace``). It is validated to stay inside the worktree: leading/
    trailing slashes are trimmed (an absolute path becomes relative), and any
    ``..``/empty/``.`` segment or non-``[A-Za-z0-9._/-]`` character is rejected —
    the value is concatenated into the ``sh -c`` command, so this guards both
    directory traversal and shell breakout. ``None``/empty → command unchanged
    (runs from the worktree root, the pre-existing behaviour)."""
    if not cwd or not cwd.strip():
        return command
    clean = cwd.strip().strip("/")
    parts = clean.split("/")
    if not clean or any(p in ("", ".", "..") for p in parts):
        raise InvalidCwdError(f"cwd must be a relative path inside the worktree, got {cwd!r}")
    if not all(c.isalnum() or c in "._-/" for c in clean):
        raise InvalidCwdError(f"cwd has unsafe characters: {cwd!r}")
    # A leading `-` is not a breakout — the charset above already closes that —
    # but it IS a `cd` that does something else: `cd -` jumps to the previous
    # directory and `cd -rf` is a flag error. Either way the command would run
    # somewhere nobody chose. Mirrored in the API-side validator so the two
    # halves cannot drift (ADR 0162).
    if any(p.startswith("-") for p in parts):
        raise InvalidCwdError(
            f"cwd segments must not start with '-' (cd reads them as flags): {cwd!r}"
        )
    return f"cd {clean} && {command}"


__all__ = [
    "DEFAULT_POSTGRES",
    "DEFAULT_REDIS",
    "DEFAULT_RUN_RUNTIME_ID",
    "AcceptanceCheck",
    "AuxServiceSpec",
    # Re-exported for tests
    "DockerSocketLeakError",
    "RuntimePlan",
    "RuntimeResolutionError",
    "TestRuntimeResult",
    "TestRuntimeRunner",
    "TestRuntimeSpec",
    "build_aux_run_kwargs",
    "default_aux_services",
    "effective_cwd",
    "group_tasks_by_runtime",
    "resolve_run_runtime",
    "resolve_run_runtime_id",
    "resolve_run_runtime_image",
]
