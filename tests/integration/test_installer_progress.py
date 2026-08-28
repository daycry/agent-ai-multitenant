"""Install orchestration + progress streaming — wizard step 8 (Plan 15 task_15_05).

Exercises the install ORCHESTRATION with the host-touching work MOCKED behind
the :class:`installer_backend.install.StepExecutor` seam — a
:class:`FakeStepExecutor` that scripts success/failure, so no real ``docker
compose``, no ``/data`` writes and no Vault bootstrap happen. The real executor
(Phase B, tasks 15_07-15_09) is exercised only by the plan's Tests Humanos.

Coverage (per the task contract):
  * the pipeline runs its steps in the canonical order with the mock executor;
  * each step transitions pending → running → ok and progress events stream;
  * a failing step HALTS the pipeline, surfaces the error, and leaves later
    steps PENDING (no later step is executed);
  * a successful run ends in a terminal ``done`` event at 100%;
  * retry resumes from the failed step (already-OK steps are skipped);
  * the ``/api/install/steps`` route lists the ordered pipeline;
  * the ``/api/install/stream`` SSE route streams secret-free progress events
    and halts on failure;
  * no secret ever appears in a progress event / the stream.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from installer_backend.install import (
    INSTALL_STEP_ORDER,
    FakeStepExecutor,
    InstallOrchestrator,
    InstallStep,
    StepExecutionError,
    StepStatus,
    install_step_index,
)
from installer_backend.main import create_app, get_step_executor
from installer_backend.seams import ProgressEvent

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _autoriza_la_simulacion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Estos tests EJERCITAN la simulación, así que la piden expresamente.

    Desde la auditoría del 2026-08-28, `/api/install/stream` responde `501` si
    los seams cableados son fakes y nadie ha encendido
    `INSTALLER_ALLOW_SIMULATION` — un wizard que fingía una instalación completa
    y revelaba credenciales inventadas era el hallazgo GRAVE de esa lente. El
    contrato de las rutas (orden, halt, retry, ausencia de secretos en el
    stream) sigue siendo el mismo; lo que cambia es que ahora hay que declarar
    que se está simulando, aquí y en el contenedor. Sin esta línea los tests de
    ruta pasarían a verde **vacíos**: un `501` tampoco filtra secretos.
    """

    monkeypatch.setenv("INSTALLER_ALLOW_SIMULATION", "1")


# ---------------------------------------------------------------------------
# Pure orchestration logic (no route).
# ---------------------------------------------------------------------------
def test_pipeline_runs_all_steps_in_order() -> None:
    executor = FakeStepExecutor()
    orch = InstallOrchestrator(executor=executor, config={"system": {"domain": "x"}})

    events = list(orch.run())

    # The executor was invoked once per step, in canonical pipeline order.
    assert executor.executed == list(INSTALL_STEP_ORDER)
    # Every step ended OK and the run completed.
    assert orch.completed is True
    assert orch.failed is False
    assert all(s.status is StepStatus.OK for s in orch.ordered_states)
    # The stream ends with a terminal done event at 100%.
    assert events[-1].done is True
    assert events[-1].stage == "done"
    assert events[-1].percent == 100


def test_each_step_emits_running_then_ok_events() -> None:
    orch = InstallOrchestrator(executor=FakeStepExecutor())
    events = list(orch.run())

    # The very first event is the RUNNING marker for the first step.
    first = INSTALL_STEP_ORDER[0]
    assert events[0].stage == first.value
    assert events[0].failed is False
    assert events[0].done is False

    # Each step contributes a "completado" OK event tagged with its stage.
    ok_stages = [e.stage for e in events if e.message.endswith("completado.")]
    assert ok_stages == [s.value for s in INSTALL_STEP_ORDER]

    # Progress is monotonically non-decreasing and bounded 0..100.
    percents = [e.percent for e in events]
    assert percents == sorted(percents)
    assert all(0 <= p <= 100 for p in percents)


def test_failing_step_halts_and_leaves_later_steps_pending() -> None:
    # Fail at the third step (start_stack): the two before run, the rest don't.
    executor = FakeStepExecutor(fail_at=InstallStep.START_STACK, fail_message="docker daemon down")
    orch = InstallOrchestrator(executor=executor)

    events = list(orch.run())

    # Only up to and including the failing step were executed.
    assert executor.executed == [
        InstallStep.GENERATE_CONFIG,
        InstallStep.PULL_IMAGES,
        InstallStep.START_STACK,
    ]
    assert orch.failed is True
    assert orch.completed is False

    # Steps before the failure are OK; the failing one is FAILED with the
    # error message; steps after stay PENDING.
    assert orch.states[InstallStep.GENERATE_CONFIG].status is StepStatus.OK
    assert orch.states[InstallStep.PULL_IMAGES].status is StepStatus.OK
    assert orch.states[InstallStep.START_STACK].status is StepStatus.FAILED
    assert orch.states[InstallStep.START_STACK].error == "docker daemon down"
    assert orch.states[InstallStep.BOOTSTRAP_VAULT].status is StepStatus.PENDING
    assert orch.states[InstallStep.SEED_TENANT].status is StepStatus.PENDING

    # The stream surfaces exactly one failed event and no terminal done event.
    failed_events = [e for e in events if e.failed]
    assert len(failed_events) == 1
    assert failed_events[0].stage == InstallStep.START_STACK.value
    assert failed_events[0].message == "docker daemon down"
    assert not any(e.done for e in events)


def test_retry_resumes_from_the_failed_step() -> None:
    executor = FakeStepExecutor(fail_at=InstallStep.BOOTSTRAP_VAULT)
    orch = InstallOrchestrator(executor=executor)

    # First run fails at bootstrap_vault.
    list(orch.run())
    assert orch.failed is True
    assert orch.states[InstallStep.BOOTSTRAP_VAULT].status is StepStatus.FAILED

    # Clear the (transient) failure and retry: already-OK steps are skipped,
    # the run resumes from bootstrap_vault and completes.
    executor.clear_failure()
    executor.executed.clear()
    events = list(orch.run())

    assert executor.executed == [InstallStep.BOOTSTRAP_VAULT, InstallStep.SEED_TENANT]
    assert orch.completed is True
    assert orch.failed is False
    assert events[-1].done is True


def test_step_execution_error_message_is_carried_to_state() -> None:
    executor = FakeStepExecutor(
        fail_at=InstallStep.GENERATE_CONFIG,
        fail_message="ruta /data no escribible",
    )
    orch = InstallOrchestrator(executor=executor)
    list(orch.run())
    assert orch.states[InstallStep.GENERATE_CONFIG].error == "ruta /data no escribible"


def test_install_step_index_matches_canonical_order() -> None:
    for idx, step in enumerate(INSTALL_STEP_ORDER):
        assert install_step_index(step) == idx


def test_executor_raises_step_execution_error_at_configured_step() -> None:
    executor = FakeStepExecutor(fail_at=InstallStep.PULL_IMAGES)
    # Non-failing step returns its scripted lines.
    lines = executor.execute(InstallStep.GENERATE_CONFIG, {})
    assert lines  # scripted log lines present
    # The configured step raises.
    with pytest.raises(StepExecutionError):
        executor.execute(InstallStep.PULL_IMAGES, {})


# ---------------------------------------------------------------------------
# /api/install/steps route.
# ---------------------------------------------------------------------------
@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def test_steps_route_lists_ordered_pipeline(client: TestClient) -> None:
    body = client.get("/api/install/steps").json()
    steps = body["steps"]
    assert [s["id"] for s in steps] == [s.value for s in INSTALL_STEP_ORDER]
    assert [s["index"] for s in steps] == list(range(len(INSTALL_STEP_ORDER)))
    assert all(s["title_es"] and s["title_en"] for s in steps)


# ---------------------------------------------------------------------------
# /api/install/stream SSE route — with an INJECTED (mocked) executor.
# ---------------------------------------------------------------------------
def _parse_sse(text: str) -> list[dict[str, object]]:
    """Parse an SSE response body into the list of JSON event payloads."""

    events: list[dict[str, object]] = []
    for block in text.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:") :].strip()))
    return events


def _client_with_executor(executor: FakeStepExecutor) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_step_executor] = lambda: executor
    return TestClient(app)


def test_stream_route_streams_progress_to_completion() -> None:
    client = _client_with_executor(FakeStepExecutor())
    resp = client.post("/api/install/stream", json={"config": {}})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(resp.text)
    # Every pipeline stage appears, and the last event is the terminal done.
    stages = {e["stage"] for e in events}
    assert {s.value for s in INSTALL_STEP_ORDER}.issubset(stages)
    assert events[-1]["done"] is True
    assert events[-1]["percent"] == 100


def test_stream_route_halts_on_failure() -> None:
    client = _client_with_executor(FakeStepExecutor(fail_at=InstallStep.PULL_IMAGES))
    resp = client.post("/api/install/stream", json={"config": {}})

    assert resp.status_code == 200
    events = _parse_sse(resp.text)

    failed = [e for e in events if e["failed"]]
    assert len(failed) == 1
    assert failed[0]["stage"] == InstallStep.PULL_IMAGES.value
    # No terminal done event after a failure, and start_stack never ran.
    assert not any(e["done"] for e in events)
    assert not any(e["stage"] == InstallStep.START_STACK.value for e in events)


def test_stream_never_leaks_secrets() -> None:
    """A secret in the posted config must NOT appear in the streamed events."""

    secret = "s3cr3t-unseal-key-never-streamed"
    client = _client_with_executor(FakeStepExecutor())
    resp = client.post(
        "/api/install/stream",
        json={"config": {"providers": {"ollama": {"endpoint": "http://x"}}, "secret": secret}},
    )
    # The executor never echoes config into messages; the raw stream is clean.
    assert secret not in resp.text


def test_progress_event_defaults_are_secret_free_and_typed() -> None:
    """Sanity: ProgressEvent carries only stage/message/percent/done/failed."""

    ev = ProgressEvent(stage="generate_config", message="hello", percent=20)
    assert ev.done is False
    assert ev.failed is False
    assert ev.percent == 20
