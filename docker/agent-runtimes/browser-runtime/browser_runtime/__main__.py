"""Entrypoint del `browser-runtime` (ADR 0080).

Lee la sesión de `BROWSE_SESSION_SPEC` (JSON en el entorno), la ejecuta con
Chromium headless a través del `egress-proxy`, y emite **una línea JSON** con el
resultado saneado. Nunca escribe nada fuera de su tmpfs; el contenedor es
efímero y el worker lo decomisiona.

El navegador sale SIEMPRE por el proxy (`HTTPS_PROXY`, que el worker inyecta):
sin proxy configurado la sesión se rechaza en vez de abrir una salida directa a
Internet — un navegador con egress libre es justo lo que el ADR prohíbe.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from typing import Any

from browser_runtime.session import (
    BrowseBudgets,
    BrowseSpecError,
    PageDriver,
    parse_steps,
    run_session,
)

# Chromium en contenedor: el sandbox del navegador necesita user-namespaces que
# `cap-drop ALL` + seccomp no conceden — el SANDBOX ES EL CONTENEDOR (cap-drop
# ALL, root de solo lectura, red interna, sin socket Docker, no-root). Y
# /dev/shm es minúsculo por defecto: sin esto Chromium peta al renderizar.
_CHROMIUM_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]


class PlaywrightDriver:
    """`PageDriver` real. Import perezoso: los tests del módulo no traen Chromium."""

    def __init__(self, proxy_url: str, *, timeout_ms: int = 20_000) -> None:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=True,
            args=_CHROMIUM_ARGS,
            proxy={"server": proxy_url},
        )
        self._context = self._browser.new_context()
        self._context.set_default_timeout(timeout_ms)
        self._page = self._context.new_page()

    def goto(self, url: str) -> None:
        self._page.goto(url, wait_until="domcontentloaded")

    def click(self, selector: str) -> None:
        self._page.click(selector)

    def fill(self, selector: str, value: str) -> None:
        self._page.fill(selector, value)

    def wait_for(self, selector: str, timeout_ms: int) -> None:
        self._page.wait_for_selector(selector, timeout=timeout_ms)

    def text(self, selector: str | None = None) -> str:
        target = selector or "body"
        return str(self._page.inner_text(target))

    def close(self) -> None:
        for closer in (self._context.close, self._browser.close, self._pw.stop):
            with contextlib.suppress(Exception):  # el cierre nunca rompe el resultado
                closer()


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def main(driver_factory: Any = PlaywrightDriver) -> int:
    raw = os.environ.get("BROWSE_SESSION_SPEC", "")
    proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "").strip()
    try:
        spec = json.loads(raw) if raw else {}
        if not proxy:
            raise BrowseSpecError(
                "sin egress-proxy configurado: un navegador sin proxy no arranca (ADR 0080)"
            )
        steps = parse_steps(spec.get("steps"))
        budgets = BrowseBudgets.from_dict(spec.get("budgets"))
    except (BrowseSpecError, json.JSONDecodeError) as exc:
        _emit({"event": "browse.error", "error": str(exc)})
        return 2

    driver: PageDriver = driver_factory(proxy)
    result = run_session(driver, steps, budgets)
    _emit({"event": "browse.result", "result": result})
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
