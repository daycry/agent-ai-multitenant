"""ADR 0080 — el worker que ejecuta una sesión de navegación aprobada.

Lo que se fija aquí es el contrato de seguridad del lanzamiento, no Chromium:

  * una sesión que NO está aprobada JAMÁS lanza el contenedor (el gate humano no
    es decorativo: es la última línea antes de abrir un navegador);
  * el contenedor va con el perfil hardened (cap-drop ALL, root read-only, sin
    socket Docker, no-root) y con la red interna del agente, cuya única salida es
    el egress-proxy;
  * el guion viaja por entorno y el resultado se cosecha de la línea JSON;
  * un runtime que revienta deja la sesión `failed` con su causa, nunca colgada.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from workers.browse_runner import (
    BrowseNotApproved,
    build_browse_container_spec,
    harvest_browse_result,
)

pytestmark = pytest.mark.unit

_STEPS = [{"action": "goto", "url": "https://example.com"}, {"action": "extract"}]


class _Settings:
    browser_runtime_image = "browser-runtime:v1"
    browse_session_timeout_s = 240
    egress_proxy_url = "http://egress-proxy:8888"


def test_an_unapproved_session_never_reaches_a_container() -> None:
    for status in ("pending_approval", "rejected", "done", "running"):
        with pytest.raises(BrowseNotApproved):
            build_browse_container_spec(
                _Settings(),  # type: ignore[arg-type]
                session_id="s1",
                status=status,
                steps=_STEPS,
                budgets={},
            )


def test_an_approved_session_is_launched_with_the_script_in_the_environment() -> None:
    spec = build_browse_container_spec(
        _Settings(),  # type: ignore[arg-type]
        session_id="s1",
        status="approved",
        steps=_STEPS,
        budgets={"max_pages": 2},
    )
    assert spec.image == "browser-runtime:v1"
    payload = json.loads(spec.env["BROWSE_SESSION_SPEC"])
    assert payload["steps"] == _STEPS
    assert payload["budgets"] == {"max_pages": 2}
    # Sin workspace: el navegador no toca disco del host (tmpfs efímero).
    assert spec.workspace_host_path is None
    assert spec.labels["com.agentic-platform.browse-session"] == "s1"


def test_the_result_line_is_harvested_from_the_logs() -> None:
    logs = (
        "algún ruido de chromium\n"
        + json.dumps({"event": "browse.result", "result": {"extracted": [{"text": "hola"}]}})
        + "\nmás ruido\n"
    )
    ok, result = harvest_browse_result(logs)
    assert ok is True
    assert result["extracted"] == [{"text": "hola"}]


def test_a_runtime_error_line_is_surfaced_as_a_failure() -> None:
    logs = json.dumps({"event": "browse.error", "error": "esquema no permitido: file"})
    ok, result = harvest_browse_result(logs)
    assert ok is False
    assert "esquema no permitido" in result["error"]


def test_logs_without_a_result_line_are_a_failure_not_a_silent_success() -> None:
    ok, result = harvest_browse_result("chromium petó y no dijo nada útil")
    assert ok is False
    assert result["error"], "una sesión sin resultado es un fallo con causa, no un done vacío"


def test_the_harvest_ignores_garbage_lines_and_takes_the_last_result() -> None:
    logs = (
        "{no es json\n"
        + json.dumps({"event": "browse.result", "result": {"pages_visited": 1}})
        + "\n"
        + json.dumps({"event": "browse.result", "result": {"pages_visited": 2}})
        + "\n"
    )
    ok, result = harvest_browse_result(logs)
    assert ok is True
    assert result["pages_visited"] == 2


def test_the_container_profile_is_the_hardened_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """No se re-testea el perfil (ya está pineado en el aislamiento del worker):
    se fija que el lanzador USA ese perfil y no uno propio más laxo."""
    import workers.browse_runner as mod

    captured: dict[str, Any] = {}

    class _Runner:
        def __init__(self, settings: Any, **kw: Any) -> None:
            captured["settings"] = settings

        def run(self, spec: Any, *, timeout: int | None = None) -> Any:
            captured["spec"] = spec
            captured["timeout"] = timeout

            class _Result:
                logs = json.dumps({"event": "browse.result", "result": {"pages_visited": 1}})
                timed_out = False

                def succeeded(self) -> bool:
                    return True

            return _Result()

    monkeypatch.setattr(mod, "AgentContainerRunner", _Runner)
    ok, result = mod.run_browse_container(
        _Settings(),  # type: ignore[arg-type]
        session_id="s1",
        status="approved",
        steps=_STEPS,
        budgets={},
    )
    assert ok is True
    assert result["pages_visited"] == 1
    # El lanzador reusa AgentContainerRunner → hereda cap-drop ALL, read-only,
    # no-new-privileges, seccomp, uid 1000 y la red interna con el egress-proxy.
    assert captured["spec"].image == "browser-runtime:v1"
    assert captured["timeout"] == 240
