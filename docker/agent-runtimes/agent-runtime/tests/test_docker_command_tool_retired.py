"""task_prod12_docker_01 (sandbox-7, decisión 3 opción b): `docker_command`
dentro del sandbox devuelve un error claro e inmediato, no un crash confuso.

La imagen del agent-runtime no instala el paquete `docker` ni recibe socket
POR DISEÑO (Dockerfile: "carries NO Docker client"); antes la primera llamada
moría con un ImportError/daemon-error críptico tras quemar un turno. Ahora el
error dice QUÉ pasa y CUÁL es la vía real (stack_exec, ADR 0093 — el worker
corre el toolchain en el runtime-template del proyecto).
"""

from __future__ import annotations

from agent_runtime.docker_command_tool import DockerCommandTool


def test_call_fails_fast_with_actionable_error_and_never_touches_docker() -> None:
    tool = DockerCommandTool(
        name="run_pytest",
        image="python-pytest:v1",
        command_template=["pytest", "{path}"],
    )
    result = tool({"path": "tests/"})
    assert result.ok is False
    assert "not supported inside the agent sandbox" in (result.error or "")
    assert "stack_exec" in (result.error or "")
    # Jamás intenta docker.from_env() (el atributo seam sigue virgen).
    assert tool.docker_client is None


def test_injected_client_seam_still_works_for_tests() -> None:
    """El seam de tests (docker_client inyectado) conserva el camino real —
    los tests unitarios del executor siguen pudiendo ejercitarlo."""

    class _FakeContainers:
        def run(self, _image: str, _command: list[str], **_kw: object) -> bytes:
            return b"ok"

    class _FakeClient:
        containers = _FakeContainers()

    tool = DockerCommandTool(
        name="run_pytest",
        image="python-pytest:v1",
        command_template=["pytest", "{path}"],
        docker_client=_FakeClient(),
    )
    result = tool({"path": "tests/"})
    assert result.ok is True
