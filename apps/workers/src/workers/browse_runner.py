"""Lanzamiento de una sesión de navegador aprobada (ADR 0080).

El córtex PIDE navegar; el owner aprueba; y aquí —y solo aquí— se abre un
navegador de verdad, dentro de un contenedor `browser-runtime` **efímero**:

  * el perfil de aislamiento es el MISMO del agent-runtime (``AgentContainerRunner``
    → cap-drop ALL, root de solo lectura, no-new-privileges, seccomp, uid 1000,
    sin socket Docker) y la red es la interna del agente, cuya única salida a
    Internet es el `egress-proxy` con allowlist;
  * el guion viaja en ``BROWSE_SESSION_SPEC`` (entorno) y el resultado vuelve en
    una línea JSON por stdout: DATOS saneados, nunca marcado ejecutable;
  * sin aprobación NO se lanza nada — la comprobación se hace aquí, la última
    puerta antes del navegador, no solo en el endpoint que aprueba.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from workers.config import Settings
from workers.container import AgentContainerRunner, ContainerSpec

_log = structlog.get_logger("workers.browse_runner")

# Margen sobre el reloj de la sesión: el contenedor tiene que poder cerrar el
# navegador y emitir su resultado aunque agote su propio presupuesto.
_TIMEOUT_MARGIN_S = 30


class BrowseNotApproved(RuntimeError):  # noqa: N818 — es un gate, no un error de programa
    """Se intentó navegar con una sesión que el owner no ha aprobado."""


def build_browse_container_spec(
    settings: Settings,
    *,
    session_id: str,
    status: str,
    steps: list[dict[str, Any]],
    budgets: dict[str, Any] | None = None,
) -> ContainerSpec:
    """El contenedor de UNA sesión aprobada. Levanta si no lo está."""
    if status != "approved":
        raise BrowseNotApproved(
            f"la sesión {session_id} está en {status!r}: solo se navega tras la "
            "aprobación explícita del owner (ADR 0080)"
        )
    payload = json.dumps({"steps": steps, "budgets": budgets or {}}, ensure_ascii=False)
    return ContainerSpec(
        image=settings.browser_runtime_image,
        env={"BROWSE_SESSION_SPEC": payload},
        # Sin bind de workspace: el navegador no toca el disco del host. Su
        # /workspace es el tmpfs efímero que da el perfil hardened.
        workspace_host_path=None,
        labels={
            "com.agentic-platform.component": "browser-runtime",
            "com.agentic-platform.browse-session": session_id,
        },
    )


def harvest_browse_result(logs: str) -> tuple[bool, dict[str, Any]]:
    """La línea JSON del runtime. Sin resultado ⇒ FALLO con causa, nunca un
    `done` vacío (una sesión silenciosa es una sesión que no sabemos qué hizo)."""
    result: dict[str, Any] | None = None
    error: str | None = None
    for raw_line in (logs or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("event") == "browse.result" and isinstance(payload.get("result"), dict):
            result = payload["result"]
        elif payload.get("event") == "browse.error":
            error = str(payload.get("error") or "error del browser-runtime")
    if result is not None:
        return True, result
    return False, {"error": error or "el browser-runtime no devolvió resultado"}


def run_browse_container(
    settings: Settings,
    *,
    session_id: str,
    status: str,
    steps: list[dict[str, Any]],
    budgets: dict[str, Any] | None = None,
    client: Any = None,
) -> tuple[bool, dict[str, Any]]:
    """Lanza la sesión y devuelve ``(ok, resultado|error)``. Nunca deja el
    contenedor vivo: ``AgentContainerRunner.run`` lo borra pase lo que pase."""
    spec = build_browse_container_spec(
        settings, session_id=session_id, status=status, steps=steps, budgets=budgets
    )
    wall_clock = int((budgets or {}).get("wall_clock_s") or 0)
    timeout = (wall_clock + _TIMEOUT_MARGIN_S) if wall_clock else settings.browse_session_timeout_s
    runner = AgentContainerRunner(settings, client=client)
    outcome = runner.run(spec, timeout=timeout)
    ok, payload = harvest_browse_result(outcome.logs)
    if outcome.timed_out:
        return False, {"error": f"la sesión agotó su reloj ({timeout}s)"}
    if not ok:
        _log.warning("browse.session_failed", session=session_id, error=payload.get("error"))
    return ok, payload


__all__ = [
    "BrowseNotApproved",
    "build_browse_container_spec",
    "harvest_browse_result",
    "run_browse_container",
]
