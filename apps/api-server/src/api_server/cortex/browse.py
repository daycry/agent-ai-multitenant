"""Sesiones de navegador del córtex — ciclo de vida y gate humano (ADR 0080).

El navegador real es la mayor superficie de ataque del sistema, así que el
operador firmó **validación humana POR SESIÓN**: el córtex no navega, *pide*
navegar. Cada petición queda pendiente hasta que el owner la aprueba viendo el
guion exacto (a qué URLs va, qué clica, qué teclea).

    pending_approval ──approve──▶ approved ──start──▶ running ──▶ done | failed
            └────────reject──────▶ rejected

Este módulo es la lógica PURA (máquina de estados + validación del guion); la
persistencia vive en ``db/browse_repo.py`` y la ejecución en el worker, que
lanza el `browser-runtime` efímero (cap-drop ALL, red solo al egress-proxy).

La validación del guion es **la misma** que aplica el runtime (catálogo cerrado
de pasos + anti-SSRF): así el owner nunca tiene delante una sesión que el
runtime rechazaría igualmente, y un guion inadmisible muere en la petición.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

# Estados y transiciones legales. Nada llega a `running` sin pasar por
# `approved`: ese es el gate humano, y es el invariante de este módulo.
BROWSE_PENDING = "pending_approval"
BROWSE_TERMINAL = ("done", "failed", "rejected")
_ALLOWED: dict[str, tuple[str, ...]] = {
    BROWSE_PENDING: ("approved", "rejected"),
    # approved→failed: el worker re-comprueba el kill-switch (y puede fallar el
    # arranque del runtime) ANTES de marcar `running`; una sesión aprobada que no
    # llega a ejecutarse acaba en `failed` con su causa, no colgada en `approved`.
    "approved": ("running", "failed", "rejected"),
    "running": ("done", "failed"),
    "done": (),
    "failed": (),
    "rejected": (),
}

# Espejo del catálogo cerrado del browser-runtime (los dos paquetes no se
# importan entre sí: el runtime vive en su propia imagen). Si uno crece, el otro
# también — el test de contrato lo pinea.
_ACTIONS = ("goto", "click", "fill", "wait_for", "extract")
_REQUIRED_FIELD = {"goto": "url", "click": "selector", "fill": "selector", "wait_for": "selector"}
_BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "metadata", "instance-data"}
MAX_STEPS = 24
MAX_GOAL_CHARS = 500


class BrowseTransitionError(RuntimeError):
    """Transición ilegal del ciclo de vida (p. ej. navegar sin aprobación)."""


@dataclass(frozen=True)
class BrowseSessionState:
    """La parte de la sesión que gobierna la máquina de estados."""

    status: str = BROWSE_PENDING
    error: str | None = None


def _move(state: BrowseSessionState, to: str, *, error: str | None = None) -> BrowseSessionState:
    if to not in _ALLOWED.get(state.status, ()):
        raise BrowseTransitionError(f"transición ilegal: {state.status} → {to}")
    return replace(state, status=to, error=error if error is not None else state.error)


def approve(state: BrowseSessionState) -> BrowseSessionState:
    """Decisión HUMANA: el owner autoriza este guion concreto."""
    return _move(state, "approved")


def reject(state: BrowseSessionState, *, reason: str = "") -> BrowseSessionState:
    """Decisión HUMANA: no se navega. Terminal."""
    return _move(state, "rejected", error=reason or "rechazada por el owner")


def start(state: BrowseSessionState) -> BrowseSessionState:
    """El worker arranca el runtime. Solo desde `approved` — sin atajos."""
    return _move(state, "running")


def finish(state: BrowseSessionState) -> BrowseSessionState:
    return _move(state, "done")


def fail(state: BrowseSessionState, *, error: str) -> BrowseSessionState:
    return _move(state, "failed", error=error[:500])


def _assert_navigable(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"esquema no permitido: {parts.scheme or '(vacío)'}")
    host = (parts.hostname or "").strip().lower()
    if not host:
        raise ValueError("URL sin host")
    if host in _BLOCKED_HOSTS or host.endswith(".localhost"):
        raise ValueError(f"host no navegable: {host}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise ValueError(f"IP no navegable: {host}")


def validate_browse_request(*, goal: str, steps: Any) -> dict[str, Any]:
    """Valida y normaliza la petición ANTES de pedirle nada a un humano.

    Levanta ``ValueError`` (que el router traduce a 4xx y la tool devuelve como
    error legible al modelo) si el guion no es admisible."""
    goal = " ".join(str(goal or "").split())
    if not goal:
        raise ValueError("la sesión necesita un objetivo (qué vas a hacer y para qué)")
    if len(goal) > MAX_GOAL_CHARS:
        raise ValueError(f"objetivo demasiado largo (> {MAX_GOAL_CHARS} caracteres)")
    if not isinstance(steps, list) or not steps:
        raise ValueError("la sesión necesita al menos un paso")
    if len(steps) > MAX_STEPS:
        raise ValueError(f"demasiados pasos ({len(steps)} > {MAX_STEPS})")

    normalised: list[dict[str, Any]] = []
    for raw in steps:
        if not isinstance(raw, dict):
            raise ValueError("cada paso es un objeto")
        action = str(raw.get("action") or "")
        if action not in _ACTIONS:
            raise ValueError(f"paso no permitido: {action!r} (permitidos: {', '.join(_ACTIONS)})")
        required = _REQUIRED_FIELD.get(action)
        if required and not str(raw.get(required) or "").strip():
            raise ValueError(f"el paso {action!r} exige {required!r}")
        if action == "goto":
            _assert_navigable(str(raw["url"]))
        step: dict[str, Any] = {"action": action}
        for key in ("url", "selector", "value"):
            if raw.get(key) is not None:
                step[key] = str(raw[key])
        normalised.append(step)
    return {"goal": goal, "steps": normalised}


__all__ = [
    "BROWSE_PENDING",
    "BROWSE_TERMINAL",
    "BrowseSessionState",
    "BrowseTransitionError",
    "approve",
    "fail",
    "finish",
    "reject",
    "start",
    "validate_browse_request",
]
