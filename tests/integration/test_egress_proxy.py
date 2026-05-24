"""Integration tests: egress controlado del sandbox agent-runtime
(task_02_35 / ADR 0019).

El sandbox sigue corriendo en una red `internal` (sin salida directa
a internet). El cableado de la tarea es:

  - cuando `WORKERS_EGRESS_PROXY_URL` está configurado, el worker
    inyecta `HTTP_PROXY` / `HTTPS_PROXY` en el contenedor para que los
    clientes HTTP del agente —incluidos los `ModelClient` reales—
    salgan a través del proxy;
  - la red `agentic-agents` se crea con ICC habilitado para que el
    agente pueda alcanzar al servicio `egress-proxy` cuando éste vive
    en la misma red.

El allowlisting real (qué hosts pasan, cuáles no) lo aplica el propio
proxy (`tinyproxy` con `FilterDefaultDeny`) — su verificación viva
con un proveedor real es manual / human_02_01; aquí lo importante es
que el cableado del sandbox al proxy funciona.
"""

from __future__ import annotations

import pytest
from workers.config import Settings
from workers.container import AgentContainerRunner, ContainerSpec

import docker

from ._docker_helpers import BASE_IMAGE, docker_client, ensure_base_image, requires_docker

pytestmark = [pytest.mark.integration, requires_docker]

# Una red distinta a la real (`agentic-agents`) para que los tests no
# pisen ni se vean pisados por la red compartida con los otros suites.
_TEST_NETWORK = "agentic-agents-egress-test"


@pytest.fixture(scope="module", autouse=True)
def _base_image() -> None:
    client = docker_client()
    try:
        ensure_base_image(client)
    finally:
        client.close()


@pytest.fixture(autouse=True)
def _clean_test_network() -> None:
    """Borra la red de tests antes (y después) de cada test, para que
    ensure_network siempre la recree con las opciones actuales."""
    client = docker_client()
    try:
        for _ in range(2):
            try:
                net = client.networks.get(_TEST_NETWORK)
                net.remove()
            except docker.errors.NotFound:
                break
        yield
        try:
            net = client.networks.get(_TEST_NETWORK)
            net.remove()
        except docker.errors.NotFound:
            pass
    finally:
        client.close()


def _runner(*, proxy_url: str = "") -> AgentContainerRunner:
    return AgentContainerRunner(Settings(agent_network=_TEST_NETWORK, egress_proxy_url=proxy_url))


_ENV_PROBE = (
    "import os; "
    "print('HTTP_PROXY=' + os.environ.get('HTTP_PROXY', '<unset>')); "
    "print('HTTPS_PROXY=' + os.environ.get('HTTPS_PROXY', '<unset>'))"
)


def test_proxy_env_vars_are_injected_when_configured() -> None:
    """Con `egress_proxy_url` puesto, el contenedor ve HTTP_PROXY/HTTPS_PROXY."""
    proxy = "http://egress-proxy.example:8888"
    result = _runner(proxy_url=proxy).run(
        ContainerSpec(image=BASE_IMAGE, command=["python", "-c", _ENV_PROBE]),
        timeout=30,
    )
    assert result.exit_code == 0
    assert f"HTTP_PROXY={proxy}" in result.logs
    assert f"HTTPS_PROXY={proxy}" in result.logs


def test_no_proxy_env_when_unconfigured() -> None:
    """Sin `egress_proxy_url`, el contenedor NO recibe las variables —
    el sandbox queda sin red de salida (comportamiento previo a
    task_02_35; sólo el `ScriptedModelClient` funciona)."""
    result = _runner().run(
        ContainerSpec(image=BASE_IMAGE, command=["python", "-c", _ENV_PROBE]),
        timeout=30,
    )
    assert result.exit_code == 0
    assert "HTTP_PROXY=<unset>" in result.logs
    assert "HTTPS_PROXY=<unset>" in result.logs


def test_per_spec_env_overrides_proxy_default() -> None:
    """Una variable ya presente en `ContainerSpec.env` gana — el
    proxy se inyecta sólo si no estaba ya definida."""
    override = "http://custom-proxy:9999"
    result = _runner(proxy_url="http://default-proxy:8888").run(
        ContainerSpec(
            image=BASE_IMAGE,
            command=["python", "-c", _ENV_PROBE],
            env={"HTTP_PROXY": override},
        ),
        timeout=30,
    )
    assert result.exit_code == 0
    assert f"HTTP_PROXY={override}" in result.logs


def test_agent_network_has_icc_enabled_so_agent_can_reach_proxy() -> None:
    """ADR 0019 / task_02_35: la red de agentes se crea con ICC=true
    (cambio respecto al Fase B original) para que un sibling como el
    `egress-proxy` sea alcanzable desde un contenedor agent-runtime."""
    name = _runner().ensure_network()
    assert name == _TEST_NETWORK

    client = docker_client()
    try:
        net = client.networks.get(name)
        options = net.attrs.get("Options") or {}
        icc = options.get("com.docker.network.bridge.enable_icc")
        # El default cuando la opción no se pasa es "true"; aceptamos
        # tanto la cadena explícita como la ausencia.
        assert icc in (None, "true")
        assert net.attrs["Internal"] is True
    finally:
        client.close()
