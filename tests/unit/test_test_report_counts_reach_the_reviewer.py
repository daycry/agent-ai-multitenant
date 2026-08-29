"""El recuento de tests llega al reviewer, y sin colapsar los tres estados.

La ola 1 del ADR 0162 encendió los parsers y dejó el recuento en el outcome que
se persiste (`test_counts`). Pero un dato que nadie enseña no cambia nada: el
reviewer seguía leyendo `PASSED (exit_codes=[0])` para el caso literal de la
base de datos viva —`No tests executed!` con exit 0— sin que nada en el prompt
dijese que ese verde no había probado nada.

Esta ola lo enseña. Y la regla que manda sobre cómo se enseña, porque es la que
evita fabricar el falso fallo contrario:

    **«no se pudo parsear» NUNCA puede convertirse en «cero tests»**

Son tres estados y tienen que seguir siendo tres al llegar al prompt:

  (a) se midió, N tests      → «12 executed, 12 passed»
  (b) se midió, CERO tests   → «ZERO tests ran» (el falso verde del ADR)
  (c) no se pudo medir       → «could not determine how many tests ran»

Lo que fija este fichero:

* las tres redacciones existen y **ninguna contiene el literal discriminante de
  otra** — si (c) se redactase como (b), el reviewer leería «este cambio no
  ejecutó ni un test» cuando lo único cierto es que no supimos leer la salida;
* un outcome ANTERIOR a la medición (todo el parque hasta que esto se despliegue)
  renderiza **byte a byte** lo de hoy;
* el recuento **no toca el veredicto**: un `ZERO tests` con exit 0 sigue diciendo
  `PASSED` en la cabecera. El gate es la opción C del ADR y no está firmada;
* el dato viaja de verdad desde donde NACE —el log del runner → `count_tests` →
  `runtime_outcome` → el bloque— y no desde un dict escrito a mano en este
  fichero, que es como se cuelan los tests que no miden nada;
* los dos prompts sembrados del reviewer (ES y EN) citan los MISMOS literales que
  emite el orchestrator.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit


def _block(outcomes: list[dict[str, Any]]) -> str:
    from orchestrator.dispatch import _format_test_report_block

    return _format_test_report_block(
        outcomes,
        project_declares_runtime=True,
        executable_criteria=1,
        tests_were_launched=True,
    )


def _outcome(**overrides: Any) -> dict[str, Any]:
    """Un outcome con la forma que persiste el worker, con `test_counts` puesto.

    Ojo: se usa SÓLO para las variantes de redacción. Que la clave se llame
    `test_counts` y traiga esta forma lo fija el test de la costura de más
    abajo, con objetos reales del worker — si esto fuese lo único, un renombrado
    en el worker dejaría estos tests verdes midiendo nada."""
    base: dict[str, Any] = {
        "runtime": "php-phpunit",
        "exit_codes": [0],
        "all_passed": True,
        "timed_out": False,
        "logs_tail": "",
        "test_counts": None,
        "checks_without_declared_check_type": 0,
    }
    base.update(overrides)
    return base


def _counts(
    total: int,
    passed: int,
    *,
    failed: int = 0,
    errored: int = 0,
    skipped: int = 0,
    source: str = "phpunit_text",
) -> dict[str, Any]:
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "errored": errored,
        "skipped": skipped,
        "source": source,
    }


def _tests_line(block: str) -> str | None:
    lines = [line for line in block.splitlines() if line.startswith("  tests: ")]
    assert len(lines) <= 1, f"más de una línea de recuento en:\n{block}"
    return lines[0] if lines else None


# ---------------------------------------------------------------------------
# (a) se midió, y salieron N
# ---------------------------------------------------------------------------
def test_a_measured_count_reaches_the_reviewer() -> None:
    line = _tests_line(_block([_outcome(test_counts=_counts(12, 12))]))
    assert line is not None
    assert "12 executed" in line
    assert "12 passed" in line
    # De dónde salió el número: un epílogo de stdout no vale lo mismo que un
    # informe estructurado, y el reviewer tiene derecho a saberlo.
    assert "phpunit_text" in line


def test_a_measured_count_breaks_down_the_non_passing_buckets() -> None:
    line = _tests_line(
        _block(
            [
                _outcome(
                    all_passed=False,
                    exit_codes=[1],
                    test_counts=_counts(14, 11, failed=2, errored=0, skipped=1),
                )
            ]
        )
    )
    assert line is not None
    assert "14 executed" in line
    assert "11 passed" in line
    assert "2 failed" in line
    assert "1 skipped" in line
    # Los buckets a cero no se imprimen: ruido en cada revisión del sistema.
    assert "errored" not in line


# ---------------------------------------------------------------------------
# (b) se midió, y salieron CERO — el falso verde del ADR
# ---------------------------------------------------------------------------
def test_zero_tests_with_a_green_exit_code_is_called_out_as_not_evidence() -> None:
    from orchestrator.dispatch import _ZERO_TESTS_MARKER

    out = _block([_outcome(test_counts=_counts(0, 0))])
    line = _tests_line(out)
    assert line is not None
    assert _ZERO_TESTS_MARKER in line
    # Y el punto entero del ADR: que el exit 0 no se lea como evidencia.
    assert "NOT evidence" in line
    # El veredicto NO se toca. Bloquear es la opción C y no está firmada.
    assert "- runtime php-phpunit: PASSED (exit_codes=[0])" in out


def test_zero_tests_with_a_red_exit_code_does_not_claim_a_green_exit() -> None:
    """El mismo cero, pero el comando falló. Decir «salió con código 0» ahí sería
    sencillamente falso, y una frase falsa en el prompt vale menos que ninguna."""
    from orchestrator.dispatch import _ZERO_TESTS_MARKER

    line = _tests_line(
        _block([_outcome(all_passed=False, exit_codes=[1], test_counts=_counts(0, 0))])
    )
    assert line is not None
    assert _ZERO_TESTS_MARKER in line
    assert "exited 0" not in line


# ---------------------------------------------------------------------------
# (c) NO se pudo medir — y esto es lo que no puede parecerse a (b)
# ---------------------------------------------------------------------------
def test_an_unmeasured_count_is_never_rendered_as_zero() -> None:
    from orchestrator.dispatch import _UNMEASURED_TESTS_MARKER, _ZERO_TESTS_MARKER

    line = _tests_line(_block([_outcome(test_counts=None)]))
    assert line is not None
    assert _UNMEASURED_TESTS_MARKER in line
    assert _ZERO_TESTS_MARKER not in line
    # Y lo dice explícitamente, porque el modelo que lo lee tiende a redondear.
    assert "not zero" in line.lower()


def test_a_malformed_counts_payload_degrades_to_unmeasured_not_to_zero() -> None:
    """El payload es JSONB libre: puede llegar cualquier cosa. La regla del ADR
    aplicada al pie de la letra — ante la duda, (c), NUNCA (b)."""
    from orchestrator.dispatch import _UNMEASURED_TESTS_MARKER, _ZERO_TESTS_MARKER

    for garbage in ("12", [], {"source": "phpunit_text"}, {"total": "doce"}, {"total": None}):
        line = _tests_line(_block([_outcome(test_counts=garbage)]))
        assert line is not None, f"sin línea de recuento para {garbage!r}"
        assert _UNMEASURED_TESTS_MARKER in line, garbage
        assert _ZERO_TESTS_MARKER not in line, garbage


def test_the_three_wordings_share_no_discriminating_literal() -> None:
    """La propiedad de verdad: los tres estados son distinguibles a la lectura.

    No basta con que existan tres textos; hace falta que el literal que
    identifica a cada uno no aparezca en los otros dos, o el reviewer —que lee,
    no parsea— acabará mezclándolos."""
    from orchestrator.dispatch import _UNMEASURED_TESTS_MARKER, _ZERO_TESTS_MARKER

    medido = _tests_line(_block([_outcome(test_counts=_counts(12, 12))])) or ""
    cero = _tests_line(_block([_outcome(test_counts=_counts(0, 0))])) or ""
    sin_medir = _tests_line(_block([_outcome(test_counts=None)])) or ""

    assert medido and cero and sin_medir
    assert len({medido, cero, sin_medir}) == 3

    for marker, owner, others in (
        (_ZERO_TESTS_MARKER, cero, (medido, sin_medir)),
        (_UNMEASURED_TESTS_MARKER, sin_medir, (medido, cero)),
    ):
        assert marker in owner
        for other in others:
            assert marker not in other, f"{marker!r} se cuela en otra redacción: {other!r}"


# ---------------------------------------------------------------------------
# No-regresión: el parque actual no se mueve un byte
# ---------------------------------------------------------------------------
def test_an_outcome_without_the_counts_key_renders_byte_for_byte_as_today() -> None:
    """Todos los `test_run_completed` ya persistidos son de antes de la ola 1: no
    traen la clave. Ésos NO se re-redactan — un informe anterior a la medición no
    dice nada sobre el recuento, ni siquiera que no se pudiera medir, y añadirle
    una línea sería afirmar algo que nadie comprobó."""
    out = _block(
        [
            {
                "runtime": "python-pytest",
                "exit_codes": [1],
                "all_passed": False,
                "timed_out": False,
                "logs_tail": "E   assert 1 == 2",
            }
        ]
    )
    assert out == (
        "<test-report>\n"
        "- runtime python-pytest: FAILED (exit_codes=[1])\n"
        "  logs (tail):\n"
        "  ```\n"
        "E   assert 1 == 2\n"
        "  ```\n"
        "</test-report>"
    )


def test_a_legacy_passing_outcome_renders_byte_for_byte_as_today() -> None:
    out = _block(
        [
            {
                "runtime": "node-jest",
                "exit_codes": [0],
                "all_passed": True,
                "timed_out": False,
                "logs_tail": "ok",
            }
        ]
    )
    assert out == (
        "<test-report>\n"
        "- runtime node-jest: PASSED (exit_codes=[0])\n"
        "  logs (tail):\n"
        "  ```\n"
        "ok\n"
        "  ```\n"
        "</test-report>"
    )


def test_an_infrastructure_failure_does_not_gain_a_redundant_count_line() -> None:
    """Su cabecera ya dice «the tests did NOT run», que es la versión FUERTE de
    (c). Repetirlo debajo en versión débil invita a leerlos como dos problemas
    distintos."""
    out = _block(
        [
            _outcome(
                runtime="python-pytest",
                exit_codes=[],
                all_passed=False,
                infrastructure_failure="runtime_image_unavailable",
                logs_tail="no se pudo obtener la imagen",
                test_counts=None,
            )
        ]
    )
    assert "INFRASTRUCTURE FAILURE" in out
    assert _tests_line(out) is None


# ---------------------------------------------------------------------------
# La costura: el dato viaja desde donde NACE
# ---------------------------------------------------------------------------
def _rendered_from_real_run(logs: str, *, runtime: str, exit_code: int) -> str:
    """Log del runner → `count_tests` → `TestRuntimeResult` → `runtime_outcome`
    → bloque del reviewer, con los objetos REALES de cada eslabón.

    Existe porque los tests de arriba escriben el outcome a mano: si el worker
    renombrase `test_counts` o cambiase la forma del dict, seguirían verdes sin
    medir nada — el precedente exacto que esta rama ya ha pagado dos veces."""
    from shared_test_runtimes import catalog
    from shared_test_runtimes.counts import count_tests
    from workers.tasks.test_runtime_task import runtime_outcome
    from workers.test_runtime import TestRuntimeResult

    template = catalog.get(runtime)
    counts = count_tests(logs, runtime=runtime, parsers=template.output_parsers)
    result = TestRuntimeResult(
        runtime=runtime,
        exit_codes=(exit_code,),
        logs=logs,
        container_id="c0ffee",
        timed_out=False,
        network_name="task-net",
        test_counts=counts,
    )
    return _block([runtime_outcome(result)])


def test_the_two_sides_of_the_seam_name_the_key_the_same() -> None:
    """El orchestrator no importa el paquete de workers (dos desplegables), así
    que el nombre de la clave está escrito DOS veces. El test de la costura de
    abajo ya lo detectaría, pero fallaría diciendo «no hay línea de recuento»;
    esto falla diciendo lo que de verdad pasó."""
    from orchestrator.dispatch import _TEST_COUNTS_KEY
    from workers.tasks.test_runtime_task import TEST_COUNTS_KEY

    assert _TEST_COUNTS_KEY == TEST_COUNTS_KEY


def test_the_adr_case_travels_from_the_runner_log_to_the_reviewer_prompt() -> None:
    """El caso literal de la base de datos viva, extremo a extremo."""
    from orchestrator.dispatch import _ZERO_TESTS_MARKER

    out = _rendered_from_real_run(
        "PHPUnit 10.5.64 by Sebastian Bergmann and contributors.\n"
        "\n"
        "Runtime:       PHP 8.3.32\n"
        "Configuration: /workspace/ci4build/phpunit.xml\n"
        "\n"
        "No tests executed!\n",
        runtime="php-phpunit",
        exit_code=0,
    )
    assert _ZERO_TESTS_MARKER in out
    assert "NOT evidence" in out
    # El veredicto sigue siendo verde: esta ola hace VISIBLE el falso verde, no
    # lo cierra (eso es la opción C, sin firmar).
    assert "PASSED (exit_codes=[0])" in out


def test_a_real_pytest_run_travels_as_a_number_not_as_a_warning() -> None:
    out = _rendered_from_real_run(
        "==================== test session starts ====================\n"
        "collected 13 items\n"
        "\n"
        "==================== 13 passed in 0.42s ====================\n",
        runtime="python-pytest",
        exit_code=0,
    )
    line = _tests_line(out)
    assert line is not None
    assert "13 executed" in line
    assert "13 passed" in line
    assert "pytest_text" in line


def test_an_unreadable_real_log_travels_as_unmeasured_not_as_zero() -> None:
    """Una salida que ningún reconocedor entiende. El worker manda `None` y el
    bloque tiene que decir «no lo sé», jamás «cero»."""
    from orchestrator.dispatch import _UNMEASURED_TESTS_MARKER, _ZERO_TESTS_MARKER

    out = _rendered_from_real_run(
        "--- check 1 ---\nBuilding...\nDone in 3s.\n",
        runtime="php-phpunit",
        exit_code=0,
    )
    assert _UNMEASURED_TESTS_MARKER in out
    assert _ZERO_TESTS_MARKER not in out


# ---------------------------------------------------------------------------
# Los prompts sembrados, en los DOS idiomas
# ---------------------------------------------------------------------------
def _reviewer_prompts() -> tuple[str, str]:
    from api_server.seeds.builtin_agents import BUILTIN_AGENTS

    reviewer = next(a for a in BUILTIN_AGENTS if a.slug == "reviewer")
    return reviewer.system_prompt_es, reviewer.system_prompt_en


@pytest.mark.parametrize("index", [0, 1])
def test_both_prompts_quote_the_literals_the_orchestrator_actually_emits(index: int) -> None:
    """El acoplamiento que nadie ve hasta que se rompe (mismo patrón que
    `NO TEST RESULTS`): el prompt enseña a leer un literal que emite OTRO
    desplegable. Si alguien reescribe la redacción sin tocar el prompt, la
    instrucción deja de aplicarse y nadie se entera."""
    from orchestrator.dispatch import _UNMEASURED_TESTS_MARKER, _ZERO_TESTS_MARKER

    prompt = _reviewer_prompts()[index]
    assert _ZERO_TESTS_MARKER in prompt
    assert _UNMEASURED_TESTS_MARKER in prompt


def test_the_spanish_prompt_says_a_green_exit_with_zero_tests_proves_nothing() -> None:
    es, _ = _reviewer_prompts()
    lowered = es.lower()
    assert "exit_code == 0" in lowered or "código 0" in lowered
    assert "no prueba" in lowered or "no demuestra" in lowered


def test_the_english_prompt_says_a_green_exit_with_zero_tests_proves_nothing() -> None:
    _, en = _reviewer_prompts()
    lowered = en.lower()
    assert "exit_code == 0" in lowered or "exit code 0" in lowered
    assert "does not prove" in lowered or "is not proof" in lowered


@pytest.mark.parametrize("index", [0, 1])
def test_both_prompts_separate_unmeasured_from_zero(index: int) -> None:
    """Si el prompt no distingue los dos, da igual que el bloque sí lo haga."""
    prompt = _reviewer_prompts()[index].lower()
    assert "not the same" in prompt or "no es lo mismo" in prompt


@pytest.mark.parametrize("index", [0, 1])
def test_neither_prompt_turns_the_count_into_a_gate(index: int) -> None:
    """La opción C del ADR 0162 no está firmada, y el encargo del operador manda
    expresamente evitar los FALSOS fallos. Decirle al reviewer «rechaza si el
    recuento es cero» sería ese gate por la puerta de atrás — y hoy dispararía
    en todo proyecto cuya salida no sepamos parsear."""
    prompt = _reviewer_prompts()[index].lower()
    for forbidden in (
        "always reject",
        "rechaza siempre",
        "siempre rechaza",
        "must reject",
        "debes rechazar",
        "reject if",
        "rechaza si",
    ):
        assert forbidden not in prompt, f"el prompt ordena rechazar: {forbidden!r}"
