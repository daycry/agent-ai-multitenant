"""Integration tests: the Celery worker launches agent containers
through the Docker SDK (task_02_06).

These drive a real Docker daemon — `AgentContainerRunner` actually
starts python:3.12-slim containers, waits for them, and reaps them.
"""

from __future__ import annotations

import json

import pytest
from workers.config import Settings
from workers.container import AgentContainerRunner, ContainerSpec
from workers.tasks import run_agent_container

import docker

from ._docker_helpers import BASE_IMAGE, docker_client, ensure_base_image, requires_docker

pytestmark = [pytest.mark.integration, requires_docker]


@pytest.fixture(scope="module", autouse=True)
def _base_image() -> None:
    client = docker_client()
    try:
        ensure_base_image(client)
    finally:
        client.close()


def _runner() -> AgentContainerRunner:
    return AgentContainerRunner(Settings())


def test_runner_launches_and_captures_stdout() -> None:
    result = _runner().run(
        ContainerSpec(
            image=BASE_IMAGE,
            command=["python", "-c", "print('hello from agent container')"],
        )
    )
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.succeeded() is True
    assert "hello from agent container" in result.logs
    assert result.container_id


def test_runner_reports_a_nonzero_exit_code() -> None:
    result = _runner().run(
        ContainerSpec(image=BASE_IMAGE, command=["python", "-c", "import sys; sys.exit(7)"])
    )
    assert result.exit_code == 7
    assert result.succeeded() is False


def test_runner_passes_environment_into_the_container() -> None:
    result = _runner().run(
        ContainerSpec(
            image=BASE_IMAGE,
            command=["python", "-c", "import os; print(os.environ['AGENT_TASK'])"],
            env={"AGENT_TASK": "task-42"},
        )
    )
    assert result.exit_code == 0
    assert "task-42" in result.logs


def test_runner_removes_the_container_afterwards() -> None:
    result = _runner().run(ContainerSpec(image=BASE_IMAGE, command=["python", "-c", "print(1)"]))
    client = docker_client()
    try:
        with pytest.raises(docker.errors.NotFound):
            client.containers.get(result.container_id)
    finally:
        client.close()


def test_runner_kills_a_container_past_its_time_budget() -> None:
    result = _runner().run(
        ContainerSpec(image=BASE_IMAGE, command=["python", "-c", "import time; time.sleep(60)"]),
        timeout=2,
    )
    assert result.timed_out is True
    assert result.succeeded() is False


def test_celery_task_returns_a_json_safe_result() -> None:
    out = run_agent_container(
        image=BASE_IMAGE, command=["python", "-c", "print('via celery task')"]
    )
    assert isinstance(out, dict)
    assert out["exit_code"] == 0
    assert out["timed_out"] is False
    assert "via celery task" in out["logs"]
    # The result backend serialises as JSON — this must not raise.
    assert json.loads(json.dumps(out)) == out


def test_ensure_network_creates_the_dedicated_internal_network() -> None:
    name = _runner().ensure_network()
    assert name == "agentic-agents"

    client = docker_client()
    try:
        net = client.networks.get(name)
        assert net.attrs["Internal"] is True
        assert net.attrs["Driver"] == "bridge"
    finally:
        client.close()


def test_ensure_network_is_idempotent() -> None:
    runner = _runner()
    assert runner.ensure_network() == runner.ensure_network() == "agentic-agents"


# ---------------------------------------------------------------------------
# prod-07 task_prod07_10 — la credencial llega por un mount read-only,
# nunca por el entorno.
# ---------------------------------------------------------------------------
_SECRET_MARKER = "OPAQUE-CREDENTIAL-MARKER-9f2c"


def test_secret_mount_delivers_the_credential_and_keeps_it_out_of_the_env(
    tmp_path: object,
) -> None:
    """El contrato completo, contra un daemon de verdad.

    Los tests unitarios prueban las dos mitades por separado (el worker parte el
    spec, el runtime lo hidrata). Esta comprueba la única cosa que ninguno de los
    dos puede: que **Docker** entrega el fichero dentro del contenedor, read-only,
    y que el env del contenedor no lleva el valor. Si el bind no resolviese —el
    modo de fallo real de un contenedor hermano cuya ruta origen se resuelve en
    el host— aquí se vería y en un unit test no.
    """
    from workers.model_secret import (
        MODEL_CREDENTIALS_PATH,
        split_model_credentials,
        stage_model_credentials,
    )

    public, secrets = split_model_credentials(
        {"kind": "claude_sdk", "model": "claude-opus-4", "oauth_token": _SECRET_MARKER}
    )
    assert secrets, "el split no movió nada: el resto del test no probaría nada"
    staged = stage_model_credentials(secrets, base_dir=str(tmp_path))
    try:
        result = _runner().run(
            ContainerSpec(
                image=BASE_IMAGE,
                command=[
                    "python",
                    "-c",
                    # Lee el fichero montado, e intenta escribirlo: read-only de
                    # verdad, no read-only "por convención de nombres".
                    "import json,os;"
                    f"p={MODEL_CREDENTIALS_PATH!r};"
                    "print(json.load(open(p))['oauth_token']);"
                    "\nfor _ in [0]:\n"
                    "    try:\n"
                    "        open(p,'w').write('x'); print('WRITABLE')\n"
                    "    except OSError: print('READONLY')\n",
                ],
                # `public` es lo que el worker mete en AGENT_TASK_SPEC.
                env={"AGENT_TASK_SPEC": json.dumps({"model": public})},
                extra_mounts=tuple(staged.mounts),
            )
        )
    finally:
        staged.cleanup()

    assert result.exit_code == 0, result.logs
    assert _SECRET_MARKER in result.logs, "el agente NO puede leer su credencial"
    assert "READONLY" in result.logs, "el mount de la credencial es escribible"
    # Y lo que motiva la tarea: el env del contenedor no lleva el valor.
    assert _SECRET_MARKER not in " ".join(result.config_env)
