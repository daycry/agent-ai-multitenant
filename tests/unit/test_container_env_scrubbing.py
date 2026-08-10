"""prod-07 task_prod07_10 — el env capturado no arrastra valores sensibles.

`ContainerResult.config_env` es una copia del entorno del contenedor que SOBREVIVE
al contenedor: viaja en el resultado y de ahí a cualquier volcado de diagnóstico.
Tras esta tarea la credencial del proveedor ya no está en el env, pero el token
interno del agente sí — y lo que este filtro protege de verdad es la variable
sensible que alguien añada mañana.
"""

from __future__ import annotations

from typing import Any

import pytest
from workers.container import AgentContainerRunner

pytestmark = pytest.mark.unit

_SECRET = "OPAQUE-CREDENTIAL-MARKER-9f2c"


class _Container:
    def __init__(self, env: list[str]) -> None:
        self.id = "fake"
        self.attrs: dict[str, Any] = {
            "State": {"ExitCode": 0},
            "Config": {"Env": env},
            "HostConfig": {},
            "NetworkSettings": {"Networks": {}},
        }

    def logs(self, **_: Any) -> bytes:
        return b""


def _capture(env: list[str]) -> tuple[str, ...]:
    return AgentContainerRunner._capture(_Container(env), timed_out=False).config_env


def test_sensitive_values_are_redacted_but_their_names_survive() -> None:
    captured = _capture(
        [
            f"AGENTIC_INTERNAL_TOKEN={_SECRET}",
            f"ANTHROPIC_API_KEY={_SECRET}",
            f"SOME_CLIENT_SECRET={_SECRET}",
            f"DB_PASSWORD={_SECRET}",
        ]
    )
    joined = " ".join(captured)
    assert _SECRET not in joined, "un valor sensible sobrevive en el env capturado"
    # El NOMBRE se conserva: saber que la variable estaba puesta es justo lo que
    # se diagnostica con esta captura.
    assert "AGENTIC_INTERNAL_TOKEN=***" in captured
    assert "ANTHROPIC_API_KEY=***" in captured
    assert "SOME_CLIENT_SECRET=***" in captured
    assert "DB_PASSWORD=***" in captured


def test_ordinary_variables_are_left_alone() -> None:
    """Redactarlo todo sería igual de inútil que no redactar nada: el env
    capturado existe para diagnosticar el sandbox (HOME, PATH, el proxy)."""
    captured = _capture(["HOME=/home/agent", "PATH=/usr/bin", "HTTP_PROXY=http://egress:8888"])
    assert captured == ("HOME=/home/agent", "PATH=/usr/bin", "HTTP_PROXY=http://egress:8888")


def test_an_entry_without_an_equals_sign_is_left_as_is() -> None:
    assert _capture(["WEIRD"]) == ("WEIRD",)
