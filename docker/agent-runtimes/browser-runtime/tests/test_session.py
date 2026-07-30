"""ADR 0080 — sesión de navegador real (Playwright) en runtime sandbox.

La lógica de la sesión es PURA y determinista (parseo de pasos, anti-SSRF,
presupuestos, saneo de la salida): el navegador entra por un `PageDriver`
inyectable, así que esto se prueba sin Chromium.

Lo que se fija aquí (los controles que el ADR exige, no la mecánica de clicar):

  * los pasos son un CATÁLOGO CERRADO (goto/click/fill/wait_for/extract);
  * anti-SSRF: no se navega a IPs privadas/loopback/metadata ni a esquemas raros;
  * presupuestos duros: pasos, páginas, bytes y reloj — se corta, no se estira;
  * la salida es DATO saneado y truncado, nunca HTML/JS ejecutable;
  * un paso que revienta no tumba la sesión: se registra y se para.
"""

from __future__ import annotations

import pytest
from browser_runtime.session import (
    HARD_MAX_BYTES,
    HARD_MAX_PAGES,
    HARD_MAX_STEPS,
    BrowseBudgets,
    BrowseSpecError,
    parse_steps,
    run_session,
    sanitize_text,
)


class _FakeDriver:
    """Doble del navegador: registra lo que se le pide y sirve textos."""

    def __init__(self, texts: list[str] | None = None, fail_on: str | None = None) -> None:
        self.actions: list[tuple[str, str]] = []
        self.texts = texts or ["contenido"]
        self.fail_on = fail_on
        self.closed = False

    def _maybe_fail(self, action: str) -> None:
        if self.fail_on == action:
            raise RuntimeError(f"{action} reventó")

    def goto(self, url: str) -> None:
        self._maybe_fail("goto")
        self.actions.append(("goto", url))

    def click(self, selector: str) -> None:
        self._maybe_fail("click")
        self.actions.append(("click", selector))

    def fill(self, selector: str, value: str) -> None:  # noqa: ARG002 — value no vuelve
        self._maybe_fail("fill")
        self.actions.append(("fill", selector))  # el valor NO se registra

    def wait_for(self, selector: str, timeout_ms: int) -> None:  # noqa: ARG002
        self._maybe_fail("wait_for")
        self.actions.append(("wait_for", selector))

    def text(self, selector: str | None = None) -> str:
        self._maybe_fail("extract")
        self.actions.append(("extract", selector or ""))
        return self.texts.pop(0) if self.texts else ""

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------- parse_steps
def test_the_step_catalogue_is_closed() -> None:
    with pytest.raises(BrowseSpecError):
        parse_steps([{"action": "eval_js", "script": "fetch('/etc/passwd')"}])


def test_a_step_without_its_required_field_is_rejected() -> None:
    with pytest.raises(BrowseSpecError):
        parse_steps([{"action": "goto"}])
    with pytest.raises(BrowseSpecError):
        parse_steps([{"action": "click"}])


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/admin",
        "http://localhost/x",
        "http://169.254.169.254/latest/meta-data/",  # metadata cloud
        "http://10.0.0.5/interno",
        "http://[::1]/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
    ],
)
def test_ssrf_targets_are_refused_at_parse_time(url: str) -> None:
    with pytest.raises(BrowseSpecError):
        parse_steps([{"action": "goto", "url": url}])


def test_a_public_https_url_is_accepted() -> None:
    steps = parse_steps([{"action": "goto", "url": "https://example.com/docs"}])
    assert steps[0].action == "goto"
    assert steps[0].url == "https://example.com/docs"


def test_too_many_steps_are_refused() -> None:
    with pytest.raises(BrowseSpecError):
        parse_steps([{"action": "wait_for", "selector": "#x"}] * (HARD_MAX_STEPS + 1))


# ---------------------------------------------------------------- presupuestos
def test_budgets_are_clamped_to_the_hard_ceilings() -> None:
    """El modelo puede PEDIR lo que quiera; los techos los pone la plataforma."""
    budgets = BrowseBudgets.from_dict(
        {"max_pages": 10_000, "max_bytes": 10**9, "wall_clock_s": 86_400}
    )
    assert budgets.max_pages == HARD_MAX_PAGES
    assert budgets.max_bytes == HARD_MAX_BYTES


def test_the_page_budget_stops_the_session() -> None:
    steps = parse_steps([{"action": "goto", "url": f"https://ej{i}.com"} for i in range(4)])
    driver = _FakeDriver()
    result = run_session(driver, steps, BrowseBudgets(max_pages=2))
    assert result["pages_visited"] == 2
    assert result["stopped_by"] == "max_pages"
    assert len([a for a in driver.actions if a[0] == "goto"]) == 2
    assert driver.closed is True


def test_the_byte_budget_truncates_the_extraction() -> None:
    steps = parse_steps(
        [{"action": "goto", "url": "https://ej.com"}, {"action": "extract", "selector": "body"}]
    )
    driver = _FakeDriver(texts=["x" * 5_000])
    result = run_session(driver, steps, BrowseBudgets(max_bytes=100))
    assert len(result["extracted"][0]["text"]) <= 100
    assert result["truncated"] is True


def test_the_wall_clock_stops_the_session() -> None:
    steps = parse_steps([{"action": "goto", "url": f"https://ej{i}.com"} for i in range(3)])
    ticks = iter([0.0, 1.0, 99.0, 99.0, 99.0])
    result = run_session(
        driver := _FakeDriver(), steps, BrowseBudgets(wall_clock_s=10), clock=lambda: next(ticks)
    )
    assert result["stopped_by"] == "wall_clock"
    assert len([a for a in driver.actions if a[0] == "goto"]) < 3


# ---------------------------------------------------------------- saneo
def test_the_output_is_data_never_executable_markup() -> None:
    dirty = "<script>steal()</script>Hola <b>mundo</b><style>x{}</style>"
    clean = sanitize_text(dirty, max_bytes=1000)
    assert "script" not in clean.lower()
    assert "steal" not in clean
    assert "Hola" in clean and "mundo" in clean


def test_extraction_is_capped_per_step_and_in_total() -> None:
    steps = parse_steps(
        [
            {"action": "goto", "url": "https://ej.com"},
            {"action": "extract"},
            {"action": "extract"},
        ]
    )
    driver = _FakeDriver(texts=["a" * 900, "b" * 900])
    result = run_session(driver, steps, BrowseBudgets(max_bytes=1000))
    total = sum(len(e["text"]) for e in result["extracted"])
    assert total <= 1000
    assert result["truncated"] is True


# ---------------------------------------------------------------- robustez
def test_a_failing_step_stops_the_session_without_crashing_it() -> None:
    steps = parse_steps(
        [
            {"action": "goto", "url": "https://ej.com"},
            {"action": "click", "selector": "#no-existe"},
            {"action": "extract"},
        ]
    )
    driver = _FakeDriver(fail_on="click")
    result = run_session(driver, steps, BrowseBudgets())
    assert result["stopped_by"] == "step_failed"
    assert result["steps"][-1]["ok"] is False
    assert "reventó" in result["steps"][-1]["error"]
    assert result["extracted"] == []
    assert driver.closed is True


def test_secrets_typed_into_a_form_never_come_back_in_the_result() -> None:
    """`fill` es interacción aprobada (login), pero el valor tecleado NO viaja de
    vuelta al modelo ni a los logs: solo el selector."""
    steps = parse_steps(
        [
            {"action": "goto", "url": "https://ej.com/login"},
            {"action": "fill", "selector": "#pass", "value": "s3cr3t-DO-NOT-LEAK"},
        ]
    )
    result = run_session(_FakeDriver(), steps, BrowseBudgets())
    assert "s3cr3t-DO-NOT-LEAK" not in str(result)
