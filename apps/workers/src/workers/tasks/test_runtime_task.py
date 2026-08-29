"""Test-runtime Celery task (Plan 06.5 Fase C/F — task_06_5_16).

Acepta un dict JSON-safe con los ``acceptance_criteria`` de una tarea, emite
los audit events (`test_run_started`/`test_run_completed`) y lanza cada
``RuntimePlan`` sobre el worktree. Docker-aware: sin daemon degrada a stub
(`status="docker_unavailable"`) para que el camino del orchestrator sea
testeable sin infraestructura.

**ADR 0162 (decisión 2, opción D).** Ese «degrada» era, hasta 2026-08-28,
sinónimo de *calla*: cinco puntos de este módulo convertían una excepción en
silencio, y el silencio llega al reviewer EXACTAMENTE igual que «este proyecto
no declara tests». Ahora cada uno de esos puntos produce un outcome visible
(:func:`infra_failure_outcome`) que se persiste como ``test_run_completed`` y
por tanto aparece en el ``<test-report>`` del reviewer como FALLO. Lo que **no**
cambia es el flujo: nada de esto tumba un run del agente que ya había terminado
bien.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.db import worker_engine
from workers.docker_client import get_docker_client

_log = structlog.get_logger("workers.tasks")

# --- despacho de la FASE DE TESTS a la cola `test` (task_wf_22, C-04) --------
#
# Presupuesto de espera. Los checks corren EN SERIE dentro del mismo contenedor
# (el `exec_run` amortiza el pre_install), así que el techo es la suma de sus
# timeouts; el margen cubre lo que no es un check: pull/arranque de la imagen,
# servicios auxiliares del proyecto (ADR 0129) y teardown.
_TEST_PHASE_DEFAULT_CHECK_TIMEOUT_S = 600
_TEST_PHASE_SPINUP_MARGIN_S = 180
# Techo duro: cien checks de 600 s no pueden dejar un slot bloqueado 16 horas.
_TEST_PHASE_MAX_WAIT_S = 3600

# --- fallos de INFRAESTRUCTURA visibles (ADR 0162, decisión 2 opción D) ------
#
# El hallazgo, en una línea: la ausencia de tests era indistinguible del diseño.
# Sin outcomes el bloque `<test-report>` desaparecía del prompt, así que un
# proyecto sin tests y un proyecto cuyos tests reventaron producían exactamente
# el mismo prompt de reviewer — el «verde que no significa nada» del ADR.
#
# El outcome de fallo tiene la MISMA forma que un outcome real (para que el
# bloque del reviewer lo renderice sin casos especiales) más una clave
# discriminante: `all_passed=False` dice FALLO, y `infrastructure_failure` dice
# que el fallo es de la plataforma y no del código del tenant. Esa distinción
# importa porque un reviewer que lea «FAILED» a secas culpará al diff.
INFRA_FAILURE_KEY = "infrastructure_failure"
# --- recuento de tests (ADR 0162, ola 1) ------------------------------------
#
# La clave del outcome que dice CUÁNTOS tests corrieron. Es una unión
# discriminada por presencia —dict = se midió (aunque sea 0); ``None`` = NO se
# pudo medir— y esa diferencia es todo el valor de la ola: hoy el veredicto sale
# sólo del código de salida, y en la BD viva hay dos ejecuciones de PHPUnit con
# exit 0 y `No tests executed!` registradas como correctas.
TEST_COUNTS_KEY = "test_counts"
# Cola del detalle. Acaba en un JSONB de auditoría y en el prompt del reviewer,
# igual que el `logs_tail` de un outcome real; mismo orden de magnitud.
_INFRA_DETAIL_TAIL = 2000


def infra_failure_outcome(*, stage: str, detail: str, runtime: str = "unknown") -> dict[str, Any]:
    """Un fallo de infraestructura con forma de outcome de test.

    JSON-safe a propósito: viaja en el ``payload`` de un ``test_run_completed``
    y de ahí al prompt del reviewer, así que aquí no entra ni una excepción ni
    un objeto — solo cadenas, listas y booleanos.
    """
    return {
        "runtime": runtime,
        "exit_codes": [],
        "all_passed": False,
        "container_id": "",
        "network_name": "",
        "timed_out": False,
        INFRA_FAILURE_KEY: stage,
        "logs_tail": detail[-_INFRA_DETAIL_TAIL:],
        # AUSENTE, jamás cero (ADR 0162, ola 1): un fallo de infraestructura es
        # el caso donde más caro sale confundir «no se pudo medir» con «no había
        # tests» — la misma confusión que la opción D vino a cerrar, un piso más
        # abajo. Aquí ni siquiera hubo salida que leer.
        TEST_COUNTS_KEY: None,
        "checks_without_declared_check_type": 0,
    }


def runtime_outcome(result: Any) -> dict[str, Any]:
    """Serializar un ``TestRuntimeResult`` al outcome que se persiste.

    Vive como función y no en línea dentro del bucle porque este dict **es el
    contrato**: con el JSONB de auditoría, con el ``<test-report>`` del reviewer
    y con la siguiente ola. Un contrato que sólo existe dentro de un bucle no se
    puede fijar con un test sin montar media fase de tests alrededor.

    ``test_counts`` es una unión discriminada por presencia y hay que leerla como
    tal (ADR 0162, ola 1):

      * ``{"total": N, "passed": …, "failed": …, "errored": …, "skipped": …,
        "source": "junit_xml" | "phpunit_text" | …}`` — se midió. ``total`` puede
        ser 0, y entonces significa que se ejecutaron CERO tests: el
        ``No tests executed!`` con exit 0 que hay en la base de datos viva.
      * ``None`` — **no se pudo medir**. NO es cero.

    La clave está SIEMPRE presente, con valor ``None`` cuando no hay medición,
    para que quien la consuma no tenga que distinguir además «no vino en el
    payload» (un outcome anterior a esta ola). El idioma que NO hay que usar es
    ``(o.get("test_counts") or {}).get("total", 0)``: colapsa los tres estados
    en dos y reintroduce el falso fallo que todo esto viene a evitar.
    """
    counts = result.test_counts
    return {
        "runtime": result.runtime,
        "exit_codes": list(result.exit_codes),
        "all_passed": result.all_passed(),
        "container_id": result.container_id,
        "network_name": result.network_name,
        "timed_out": result.timed_out,
        # Truncate logs to keep the JSONB payload reasonable — full logs are
        # available via `docker logs <container_id>` if needed (until cleanup).
        "logs_tail": result.logs[-4000:] if result.logs else "",
        TEST_COUNTS_KEY: counts.as_dict() if counts is not None else None,
        "checks_without_declared_check_type": result.checks_without_declared_check_type,
    }


async def _append_infra_failure_event(request: dict[str, Any], outcome: dict[str, Any]) -> bool:
    """Persistir ``outcome`` como ``test_run_completed`` de la tarea del request."""
    from api_server.db.task_audit_repo import append_audit_event

    tenant_id = UUID(str(request["tenant_id"]))
    task_id = UUID(str(request["task_id"]))
    engine = worker_engine(get_settings())
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as session, session.begin():
            await append_audit_event(
                session,
                tenant_id=tenant_id,
                task_id=task_id,
                kind="test_run_completed",
                actor="system:celery",
                payload=outcome,
            )
    finally:
        await engine.dispose()
    return True


async def _record_infra_failure(request: dict[str, Any], outcome: dict[str, Any]) -> bool:
    """Dejar constancia de un fallo de infraestructura de la fase de tests.

    El informe que lee el reviewer se arma con EVENTOS de auditoría, no con el
    valor de retorno de esta tarea —que no consume nadie—, así que registrar el
    fallo aquí es la única forma de que llegue a alguien.

    Best-effort de verdad, y por eso el ``except`` ancho: el run del agente YA
    terminó bien y su tarea ya está en review. Si la BD tampoco responde, se
    LOGUEA a nivel error y se sigue — que es justo lo contrario de lo que hacía
    este módulo antes del ADR 0162, donde el fallo desaparecía sin traza.
    """
    try:
        return await _append_infra_failure_event(request, outcome)
    except Exception as exc:
        _log.error(
            "workers.test_phase_failure_not_recorded",
            task_id=str(request.get("task_id", "")),
            stage=str(outcome.get(INFRA_FAILURE_KEY, "")),
            error_type=exc.__class__.__name__,
            error=str(exc),
        )
        return False


def test_phase_wait_budget_s(acceptance_criteria: list[Any]) -> int:
    """Cuánto esperar como mucho por la fase de tests de una tarea.

    Un ``timeout_s`` ausente o basura cae al default en vez de restar: si un
    valor corrupto produjera una espera de 0 s, la fase se cortaría antes de
    empezar y el reviewer volvería a quedarse sin ``<test-report>`` — el
    hallazgo C1/F51 otra vez, por la puerta de atrás.
    """
    if not acceptance_criteria:
        return 0
    total = 0
    for check in acceptance_criteria:
        raw = check.get("timeout_s") if isinstance(check, dict) else None
        try:
            per_check = int(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            per_check = _TEST_PHASE_DEFAULT_CHECK_TIMEOUT_S
        if per_check <= 0:
            per_check = _TEST_PHASE_DEFAULT_CHECK_TIMEOUT_S
        total += per_check
    return min(total + _TEST_PHASE_SPINUP_MARGIN_S, _TEST_PHASE_MAX_WAIT_S)


async def dispatch_test_runtime_and_wait(request: dict[str, Any]) -> dict[str, Any]:
    """Encolar la fase de tests en la cola ``test`` y esperar su resultado.

    Corría **en proceso** (``await _run_test_runtime(...)``) dentro del worker de
    la cola ``default``: el slot que un run acababa de liberar se quedaba
    orquestando Docker —levantar el runtime, los servicios auxiliares, N checks
    de hasta 600 s, teardown— con los recursos del worker equivocado, y además
    arrastraba al worker ``default`` el import del SDK de Docker y de
    ``shared_test_runtimes`` que este módulo aplaza justamente para evitarlo.
    ``stack_exec`` ya enruta a ``test`` por este motivo (ADR 0093); esta fase se
    había quedado atrás (C-04, task_wf_22).

    **Se sigue esperando, y a propósito**: el reviewer se despacha después y
    necesita encontrar un ``<test-report>`` real — sin la espera volvería la
    carrera que dejaba al reviewer a ciegas (C1/F51). Lo que cambia es DÓNDE se
    hace el trabajo, no si se espera.

    Best-effort en sentido estricto: el run YA terminó bien y la tarea ya se
    movió a review, así que un broker caído, la ausencia de worker en ``test`` o
    el vencimiento del presupuesto **nunca lanzan**.

    Lo que sí cambió con el ADR 0162 (D): antes eso se resolvía con un
    ``_log.warning`` y un ``{}``, y el log no lo lee el reviewer. Ahora el fallo
    se persiste además como ``test_run_completed`` de infraestructura, para que
    «la fase de tests no llegó a correr» no se le presente al reviewer como «este
    proyecto no tiene tests».
    """
    criteria = request.get("acceptance_criteria") or []
    if not criteria:
        return {}
    budget = test_phase_wait_budget_s(list(criteria))

    def _send_and_wait() -> dict[str, Any]:
        async_result = app.send_task("workers.run_test_runtime", args=[request], queue="test")
        result = async_result.get(timeout=budget)
        return dict(result) if isinstance(result, dict) else {}

    try:
        return await asyncio.to_thread(_send_and_wait)
    except Exception as exc:
        _log.warning(
            "workers.test_phase_dispatch_failed",
            task_id=str(request.get("task_id", "")),
            budget_s=budget,
            error_type=exc.__class__.__name__,
        )
        outcome = infra_failure_outcome(
            stage="test_phase_dispatch_failed",
            detail=(
                f"la fase de tests no llegó a ejecutarse: "
                f"{exc.__class__.__name__}: {exc} "
                f"(presupuesto de espera {budget} s)"
            ),
        )
        await _record_infra_failure(request, outcome)
        return {
            "task_id": str(request.get("task_id", "")),
            "status": "dispatch_failed",
            "all_passed": False,
            "runtimes": [outcome],
        }


@app.task(name="workers.run_test_runtime")  # type: ignore[untyped-decorator]
def run_test_runtime(request: dict[str, Any]) -> dict[str, Any]:
    """Run the test-runtime for one task (Plan 06.5 Fase F task_06_5_16).

    Expected ``request`` shape::

        {
          "tenant_id": "<uuid>",
          "task_id": "<uuid>",
          "acceptance_criteria": [{
              "id": "auto_01_a",
              "runtime": "python-pytest",
              "command": "pytest -q",
              "expected_signal": "exit_code == 0",
              "timeout_s": 600
          }, ...],
          "worktree_host_path": "/data/wt/<task>",
          "dep_cache_host_path": "/data/dep-cache",  // optional
          "aux_services": [...],                     // optional
          "cpu": 2.0, "memory_mb": 4096,             // optional overrides
        }

    Audit events emitted:
      1. ``test_run_started`` at queue time — captures runtime + paths.
      2. ``test_run_completed`` after each `RuntimePlan` finishes —
         carries exit_codes, container_id, network_name, timed_out.
         Un fallo de infraestructura emite uno igual con
         ``infrastructure_failure`` puesto (ADR 0162, D): es la única forma de
         que el reviewer distinga «no corrieron» de «no había».

    Returns a dict with the per-runtime outcomes.

    Docker-aware: if `docker.from_env()` fails (e.g. CI without a
    daemon, or running under a sandbox), the task falls back to a
    stub and returns `status="docker_unavailable"`. That keeps the
    orchestrator path testable without infrastructure — pero el stub ya no es
    mudo: emite también su ``test_run_completed`` de infraestructura.
    """
    settings = get_settings()
    return asyncio.run(_run_test_runtime(request, settings))


async def _run_test_runtime(request: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Async core. Audit event always; real launch when Docker is up."""
    # Lazy imports — workers without the `test` queue routed shouldn't
    # pay the cost of importing docker SDK / shared_test_runtimes.
    from api_server.db.task_audit_repo import append_audit_event

    tenant_id = UUID(str(request["tenant_id"]))
    task_id = UUID(str(request["task_id"]))

    engine = worker_engine(settings)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

        # 1. Always emit the "started" event so audit shows the queue moment.
        async with sessionmaker() as session, session.begin():
            await append_audit_event(
                session,
                tenant_id=tenant_id,
                task_id=task_id,
                kind="test_run_started",
                actor="system:celery",
                payload={
                    "runtime": request.get("runtime"),
                    "worktree_host_path": request.get("worktree_host_path"),
                    "queued_at_unix": time.time(),
                },
            )

        # 2. Try the real launch. Ya no hay rama silenciosa: los fallos de
        #    infraestructura vuelven como outcomes visibles (ADR 0162, D) en vez
        #    de un `None` que hacía salir de aquí SIN persistir nada — y sin
        #    evento, para el reviewer el fallo no había ocurrido.
        outcomes = await _launch_test_runtime_plans(request, settings)

        # 3. Persist one "completed" event per runtime plan run.
        async with sessionmaker() as session, session.begin():
            for outcome in outcomes:
                await append_audit_event(
                    session,
                    tenant_id=tenant_id,
                    task_id=task_id,
                    kind="test_run_completed",
                    actor="system:celery",
                    payload=outcome,
                )
    finally:
        await engine.dispose()

    all_passed = all(o.get("all_passed", False) for o in outcomes)
    return {
        "task_id": str(task_id),
        # El contrato documentado del stub se conserva —sin daemon el estado
        # sigue siendo `docker_unavailable`, que es lo que hace testeable el
        # camino del orchestrator sin infraestructura—; lo que cambia es que
        # ahora, además, ese caso deja un evento de auditoría.
        "status": _launch_status(outcomes),
        "all_passed": all_passed,
        "runtimes": outcomes,
    }


def _launch_status(outcomes: list[dict[str, Any]]) -> str:
    """El estado agregado de la fase, para quien lea el valor de retorno."""
    if any(o.get(INFRA_FAILURE_KEY) == "docker_unavailable" for o in outcomes):
        return "docker_unavailable"
    return "completed"


async def _launch_test_runtime_plans(
    request: dict[str, Any], settings: Settings
) -> list[dict[str, Any]]:
    """Build RuntimePlans from `acceptance_criteria` and launch each.

    Devuelve **siempre** una lista JSON-safe de outcomes. Los caminos que antes
    devolvían ``None`` o ``[]`` al tropezar —SDK ausente, daemon caído, runtime
    id fuera del catálogo, servicios auxiliares mal declarados, la imagen del
    runtime que no se pudo obtener— devuelven ahora un outcome de fallo de
    infraestructura, porque «no se pudo ejecutar» tiene que verse como FALLO y
    no como ausencia (ADR 0162, decisión 2 opción D).

    El flujo de control NO cambia: ninguno de esos caminos lanza, y el único que
    seguía adelante pese al tropiezo (servicios auxiliares) sigue haciéndolo.
    La lista vacía se reserva para el único caso en que de verdad no había nada
    que ejecutar: la tarea no trae checks.
    """
    try:
        from workers.runtime_services import (
            RuntimeServicesConfigError,
            build_project_runtime_services,
        )
        from workers.test_runtime import (
            TestRuntimeRunner,
            TestRuntimeSpec,
            group_tasks_by_runtime,
        )
    except ImportError as exc:
        # Un worker enrutado a la cola `test` sin el SDK de Docker o sin
        # `shared_test_runtimes` es un despliegue mal montado, no un proyecto
        # sin tests. Antes se devolvía `None` sin una sola línea de log.
        _log.error(
            "workers.test_runtime_sdk_missing",
            task_id=str(request.get("task_id", "")),
            error=str(exc),
        )
        return [
            infra_failure_outcome(
                stage="runtime_sdk_missing",
                detail=f"el worker de la cola `test` no puede importar el test-runtime: {exc}",
            )
        ]

    if get_docker_client() is None:
        _log.error(
            "workers.test_runtime_docker_unavailable",
            task_id=str(request.get("task_id", "")),
        )
        return [
            infra_failure_outcome(
                stage="docker_unavailable",
                detail=(
                    "el SDK de Docker no pudo conectar con el daemon: la fase de "
                    "tests no se ejecutó"
                ),
            )
        ]

    acceptance = request.get("acceptance_criteria") or []
    if not acceptance:
        return []

    try:
        plans = group_tasks_by_runtime(acceptance)
    except KeyError as exc:
        # Unknown runtime id. El comentario que había aquí decía que «el
        # orchestrator es responsable de exponer la mala configuración al
        # usuario»; no lo hacía nadie, porque cero outcomes es exactamente lo
        # que produce un proyecto sin tests.
        _log.error(
            "workers.test_runtime_unknown_runtime",
            task_id=str(request.get("task_id", "")),
            error=str(exc),
        )
        return [
            infra_failure_outcome(
                stage="unknown_runtime",
                detail=f"la tarea declara un runtime que no está en el catálogo: {exc}",
            )
        ]

    outcomes: list[dict[str, Any]] = []
    # ADR 0129: the project's declared services (+ connection env). The request
    # carries `repository_config` when the orchestrator threads it; absent →
    # empty (backward-compatible, no services).
    try:
        services = build_project_runtime_services(request.get("repository_config"))
    except RuntimeServicesConfigError as exc:
        # A bad services config must not sink the whole test run — run without
        # them (the checks that need a DB will fail visibly, which is truthful).
        # Lo que no era truthful es callarlo: un proyecto con la config de
        # servicios rota quedaba indistinguible de uno que no declara ninguno.
        _log.error(
            "workers.test_runtime_aux_services_invalid",
            task_id=str(request.get("task_id", "")),
            error=str(exc),
        )
        outcomes.append(
            infra_failure_outcome(
                stage="aux_services_config_invalid",
                detail=(
                    f"los servicios declarados del proyecto (ADR 0129) no son válidos, "
                    f"así que los checks corrieron SIN ellos: {exc}"
                ),
            )
        )
        services = build_project_runtime_services(None)

    # ADR 0162: la raíz del proyecto dentro del worktree. Es la MISMA fuente que
    # ya se lee arriba para los servicios, así que no hace falta ir a la BD.
    #
    # Ojo con por qué esta línea existe: sin ella el cableado del `cwd` llegaba
    # sólo a la vía del agente (`stack_exec_task`), que era justamente la única
    # de las cuatro bocas que ya funcionaba. `default_pre_install` y los
    # acceptance checks —las dos que deciden si una tarea se da por buena—
    # seguían corriendo desde la raíz del worktree, y para un proyecto anidado
    # eso significa ni instalar dependencias ni encontrar los tests.
    _repo_cfg = request.get("repository_config")
    _project_root = _repo_cfg.get("project_root") if isinstance(_repo_cfg, dict) else None

    runner = TestRuntimeRunner(settings)
    for plan in plans:
        spec = TestRuntimeSpec(
            plan=plan,
            worktree_host_path=str(request["worktree_host_path"]),
            project_root=_project_root,
            dep_cache_host_path=request.get("dep_cache_host_path"),
            # ADR 0094: cold-cache default_pre_install needs to resolve its
            # registries; the runner drops the proxy before the check phase so
            # the tests themselves still run offline.
            dep_egress=True,
            # ADR 0129: services on the bridge + connection env for the checks.
            aux_services=services.aux_services,
            main_env=services.main_env,
        )
        try:
            result = runner.launch(spec)
        except Exception as exc:
            # El ejemplo que nombra el propio ADR 0162: `RuntimeImageUnavailableError`
            # («no se pudo obtener la imagen fijada por digest»). Salía de aquí,
            # tumbaba la tarea Celery entera —llevándose por delante los planes
            # que quedaban— y acababa convertida en `{}` por el despacho, o sea
            # en nada. Ahora el plan que falla lo dice y los demás siguen.
            _log.error(
                "workers.test_runtime_launch_failed",
                task_id=str(request.get("task_id", "")),
                runtime=plan.template.id,
                error_type=exc.__class__.__name__,
                error=str(exc),
            )
            outcomes.append(
                infra_failure_outcome(
                    stage="runtime_launch_failed",
                    detail=f"{exc.__class__.__name__}: {exc}",
                    runtime=plan.template.id,
                )
            )
            continue
        outcomes.append(runtime_outcome(result))
    return outcomes
