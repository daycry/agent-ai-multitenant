"""Sesión de navegador real, sandboxeada (ADR 0080).

El córtex NO ejecuta un navegador: pide una **sesión de navegación** que un
runtime efímero (`browser-runtime`) ejecuta con `cap-drop ALL`, root de solo
lectura, sin socket Docker y con la red SOLO hacia el `egress-proxy`. Este
módulo es la lógica pura de esa sesión — sin Playwright: el navegador entra por
un ``PageDriver`` inyectable, así que los controles se prueban sin Chromium.

Los controles del ADR viven aquí:

* **Catálogo cerrado de pasos** — `goto` / `click` / `fill` / `wait_for` /
  `extract`. No hay `eval_js` ni nada que ejecute código arbitrario del modelo:
  la interacción aprobada (login, formularios, clicks) se expresa con estos
  cinco verbos y nada más.
* **Anti-SSRF en el parseo** — se rechazan esquemas que no sean http/https e
  IPs privadas / loopback / link-local / metadata cloud. La red interna del
  contenedor (sin NAT, solo el proxy) es el control DURO; esto es la defensa en
  profundidad que además hace el fallo legible.
* **Presupuestos duros** — pasos, páginas, bytes y reloj. Los pide el modelo,
  los ACOTA la plataforma: pasarse no estira el techo, corta la sesión.
* **La salida es DATO** — texto visible saneado y truncado, jamás marcado
  ejecutable. Y lo que se teclea en un formulario (`fill`) no vuelve: solo el
  selector, para que un secreto no acabe en el contexto del modelo ni en logs.
"""

from __future__ import annotations

import contextlib
import ipaddress
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

# Techos de plataforma. El modelo puede pedir menos; nunca más.
HARD_MAX_STEPS = 24
HARD_MAX_PAGES = 8
HARD_MAX_BYTES = 200_000
HARD_WALL_CLOCK_S = 180
# Tope por extracción individual (una página gigante no se come el presupuesto).
STEP_EXTRACT_CAP = 20_000
# Timeout por espera de selector.
DEFAULT_WAIT_MS = 10_000

_ACTIONS = ("goto", "click", "fill", "wait_for", "extract")
_REQUIRED_FIELD = {"goto": "url", "click": "selector", "fill": "selector", "wait_for": "selector"}
# Hostnames que jamás son un destino legítimo de navegación.
_BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "metadata", "instance-data"}


class BrowseSpecError(ValueError):
    """La sesión pedida no es admisible (paso desconocido, campo ausente, URL
    insegura, demasiados pasos). Se rechaza ANTES de abrir el navegador."""


class PageDriver(Protocol):
    """Lo único que la sesión necesita de un navegador (Playwright lo cumple)."""

    def goto(self, url: str) -> None: ...
    def click(self, selector: str) -> None: ...
    def fill(self, selector: str, value: str) -> None: ...
    def wait_for(self, selector: str, timeout_ms: int) -> None: ...
    def text(self, selector: str | None = None) -> str: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class BrowseStep:
    action: str
    url: str | None = None
    selector: str | None = None
    value: str | None = None
    timeout_ms: int = DEFAULT_WAIT_MS


@dataclass(frozen=True)
class BrowseBudgets:
    max_pages: int = HARD_MAX_PAGES
    max_bytes: int = HARD_MAX_BYTES
    wall_clock_s: int = HARD_WALL_CLOCK_S

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> BrowseBudgets:
        """Presupuestos pedidos, ACOTADOS a los techos de plataforma."""
        raw = raw or {}

        def _clamp(key: str, ceiling: int) -> int:
            try:
                asked = int(raw.get(key, ceiling))
            except (TypeError, ValueError):
                return ceiling
            return max(1, min(asked, ceiling))

        return cls(
            max_pages=_clamp("max_pages", HARD_MAX_PAGES),
            max_bytes=_clamp("max_bytes", HARD_MAX_BYTES),
            wall_clock_s=_clamp("wall_clock_s", HARD_WALL_CLOCK_S),
        )


def assert_navigable(url: str) -> None:
    """Anti-SSRF: solo http/https a un host público. Levanta `BrowseSpecError`."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise BrowseSpecError(f"esquema no permitido: {parts.scheme or '(vacío)'}")
    host = (parts.hostname or "").strip().lower()
    if not host:
        raise BrowseSpecError("URL sin host")
    if host in _BLOCKED_HOSTS or host.endswith(".localhost"):
        raise BrowseSpecError(f"host no navegable: {host}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # nombre de dominio: lo resuelve el egress-proxy, que tiene allowlist
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise BrowseSpecError(f"IP no navegable: {host}")


def parse_steps(raw_steps: Any) -> list[BrowseStep]:
    """Valida el guion de la sesión (catálogo cerrado + anti-SSRF + tope)."""
    if not isinstance(raw_steps, list) or not raw_steps:
        raise BrowseSpecError("la sesión necesita al menos un paso")
    if len(raw_steps) > HARD_MAX_STEPS:
        raise BrowseSpecError(f"demasiados pasos ({len(raw_steps)} > {HARD_MAX_STEPS})")
    steps: list[BrowseStep] = []
    for raw in raw_steps:
        if not isinstance(raw, dict):
            raise BrowseSpecError("cada paso es un objeto")
        action = str(raw.get("action") or "")
        if action not in _ACTIONS:
            raise BrowseSpecError(f"paso no permitido: {action!r} (permitidos: {_ACTIONS})")
        required = _REQUIRED_FIELD.get(action)
        if required and not str(raw.get(required) or "").strip():
            raise BrowseSpecError(f"el paso {action!r} exige {required!r}")
        if action == "goto":
            assert_navigable(str(raw["url"]))
        steps.append(
            BrowseStep(
                action=action,
                url=str(raw["url"]) if action == "goto" else None,
                selector=(str(raw["selector"]) if raw.get("selector") else None),
                value=(str(raw["value"]) if raw.get("value") is not None else None),
                timeout_ms=min(int(raw.get("timeout_ms") or DEFAULT_WAIT_MS), 30_000),
            )
        )
    return steps


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPTISH_RE = re.compile(r"<(script|style|iframe|object|embed)\b.*?</\1>", re.S | re.I)


def sanitize_text(raw: str, *, max_bytes: int) -> str:
    """El contenido vuelve como DATO: fuera lo ejecutable, texto visible, truncado."""
    text = _SCRIPTISH_RE.sub(" ", raw or "")
    text = _TAG_RE.sub(" ", text)
    text = " ".join(text.split())
    return text[:max_bytes]


def run_session(  # noqa: PLR0912 — un verbo por rama: partirlo lo haría menos legible
    driver: PageDriver,
    steps: list[BrowseStep],
    budgets: BrowseBudgets,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Ejecuta el guion contra `driver` respetando los presupuestos.

    Devuelve SIEMPRE un resultado (nunca levanta): un paso que revienta para la
    sesión y queda registrado con su error. El navegador se cierra pase lo que
    pase. `stopped_by` dice por qué acabó: `completed` | `max_pages` |
    `wall_clock` | `step_failed`."""
    started = clock()
    executed: list[dict[str, Any]] = []
    extracted: list[dict[str, Any]] = []
    pages = 0
    budget_left = budgets.max_bytes
    truncated = False
    stopped_by = "completed"

    try:
        for step in steps:
            if clock() - started >= budgets.wall_clock_s:
                stopped_by = "wall_clock"
                break
            if step.action == "goto" and pages >= budgets.max_pages:
                stopped_by = "max_pages"
                break
            record: dict[str, Any] = {"action": step.action, "ok": True}
            if step.selector:
                record["selector"] = step.selector
            try:
                if step.action == "goto":
                    driver.goto(str(step.url))
                    pages += 1
                    record["url"] = step.url
                elif step.action == "click":
                    driver.click(str(step.selector))
                elif step.action == "fill":
                    # El valor tecleado NO se registra ni se devuelve (puede ser
                    # una credencial: no debe volver al contexto del modelo).
                    driver.fill(str(step.selector), str(step.value or ""))
                elif step.action == "wait_for":
                    driver.wait_for(str(step.selector), step.timeout_ms)
                elif step.action == "extract":
                    cap = min(STEP_EXTRACT_CAP, budget_left)
                    text = sanitize_text(driver.text(step.selector), max_bytes=cap)
                    if len(text) >= budget_left:
                        truncated = True
                    budget_left = max(0, budget_left - len(text))
                    extracted.append({"selector": step.selector, "text": text})
                    record["bytes"] = len(text)
                    if budget_left == 0:
                        truncated = True
            except Exception as exc:  # un paso roto NO tumba la sesión
                record["ok"] = False
                record["error"] = str(exc)[:300]
                executed.append(record)
                stopped_by = "step_failed"
                break
            executed.append(record)
    finally:
        with contextlib.suppress(Exception):  # el cierre jamás rompe el resultado
            driver.close()

    return {
        "steps": executed,
        "extracted": extracted,
        "pages_visited": pages,
        "bytes_extracted": budgets.max_bytes - budget_left,
        "truncated": truncated,
        "stopped_by": stopped_by,
        "elapsed_s": round(clock() - started, 2),
    }


__all__ = [
    "DEFAULT_WAIT_MS",
    "HARD_MAX_BYTES",
    "HARD_MAX_PAGES",
    "HARD_MAX_STEPS",
    "HARD_WALL_CLOCK_S",
    "BrowseBudgets",
    "BrowseSpecError",
    "BrowseStep",
    "PageDriver",
    "assert_navigable",
    "parse_steps",
    "run_session",
    "sanitize_text",
]
