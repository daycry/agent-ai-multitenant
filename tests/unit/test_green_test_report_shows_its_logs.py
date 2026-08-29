"""El reviewer revisaba a ciegas justo cuando más importaba (ADR 0162, §trampa).

El defecto, en una línea: `_format_test_report_block` sólo adjuntaba la cola de
logs `if not passed and logs_tail`. O sea, **la prueba de que no se probó nada se
le ocultaba al reviewer precisamente en el caso verde**, que es el único donde esa
prueba cambia el veredicto.

La base de datos de la instalación viva tiene el caso literal:

```text
vendor/bin/phpunit --testsuite E2E --colors=never   =>  ok=true
No tests executed!
```

`exit_code == 0` con cero tests ejecutados. El dato estaba en la variable
`logs_tail` y el código decidía no enseñarlo.

Lo que fija este fichero:

* un outcome que PASA lleva su cola de logs (antes: nada);
* un outcome que FALLA sigue llevando la cola larga, byte a byte (no-regresión:
  el diagnóstico de un fallo necesita el traceback entero, y esto no lo toca);
* el caso concreto del ADR — logs con «No tests executed!» y `all_passed=true` —
  llega al prompt del reviewer;
* la asimetría de tamaño es deliberada y está acotada por constantes con nombre:
  el verde paga una cola corta en CADA revisión, así que no puede costar lo mismo
  que el rojo.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _block(outcomes: list[dict[str, object]]) -> str:
    from orchestrator.dispatch import _format_test_report_block

    return _format_test_report_block(
        outcomes,
        project_declares_runtime=True,
        executable_criteria=1,
        tests_were_launched=True,
    )


def _fenced_logs(block: str) -> str:
    """El contenido del fence de logs, sin la cabecera del outcome.

    Afirmar sobre el bloque entero contaría de más: la propia cabecera lleva
    `exit_codes`, así que un `block.count("x")` suma la `x` de «exit» y el test
    mide una cosa distinta de la que dice medir."""
    lines = block.splitlines()
    opens = [i for i, line in enumerate(lines) if line == "  ```"]
    assert len(opens) == 2, f"se esperaba un único fence de logs en:\n{block}"
    return "\n".join(lines[opens[0] + 1 : opens[1]])


# ---------------------------------------------------------------------------
# El hallazgo: el caso verde ya no se revisa a ciegas
# ---------------------------------------------------------------------------
def test_a_passing_outcome_now_carries_its_log_tail() -> None:
    out = _block(
        [
            {
                "runtime": "node-jest",
                "exit_codes": [0],
                "all_passed": True,
                "timed_out": False,
                "logs_tail": "Tests: 12 passed, 12 total",
            }
        ]
    )
    assert "logs (tail):" in out
    assert "Tests: 12 passed, 12 total" in out


def test_the_phpunit_zero_tests_case_from_the_adr_reaches_the_reviewer() -> None:
    """El caso medido, entero: PHPUnit sale con 0 y no ejecutó ni un test.

    Sin la cola de logs, el bloque decía literalmente `PASSED (exit_codes=[0])` y
    nada más. El reviewer no tenía cómo saberlo, y la plataforma tampoco se lo
    contaba en ningún otro sitio."""
    logs = (
        "PHPUnit 10.5.64 by Sebastian Bergmann and contributors.\n"
        "\n"
        "Runtime:       PHP 8.3.32\n"
        "Configuration: /workspace/ci4build/phpunit.xml\n"
        "\n"
        "No tests executed!\n"
    )
    out = _block(
        [
            {
                "runtime": "php-phpunit",
                "exit_codes": [0],
                "all_passed": True,
                "timed_out": False,
                "logs_tail": logs,
            }
        ]
    )
    assert "runtime php-phpunit: PASSED" in out
    assert "No tests executed!" in out


def test_a_passing_outcome_without_logs_adds_no_empty_fence() -> None:
    """Un outcome verde sin logs no puede inventarse un bloque de código vacío:
    un fence vacío se lee como «no hubo salida», que es una afirmación distinta de
    «el runtime no reportó cola»."""
    out = _block(
        [
            {
                "runtime": "python-pytest",
                "exit_codes": [0],
                "all_passed": True,
                "timed_out": False,
                "logs_tail": "",
            }
        ]
    )
    assert out == "<test-report>\n- runtime python-pytest: PASSED (exit_codes=[0])\n</test-report>"


# ---------------------------------------------------------------------------
# No-regresión: el caso rojo no se mueve un byte
# ---------------------------------------------------------------------------
def test_a_failed_outcome_is_byte_for_byte_what_it_was() -> None:
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


def test_a_failure_keeps_the_long_tail() -> None:
    """El rojo conserva su presupuesto largo: diagnosticar un fallo pide el
    traceback, no el recuento."""
    from orchestrator.dispatch import _TEST_REPORT_LOG_TAIL

    logs = "x" * (_TEST_REPORT_LOG_TAIL * 3)
    out = _block(
        [
            {
                "runtime": "python-pytest",
                "exit_codes": [1],
                "all_passed": False,
                "timed_out": False,
                "logs_tail": logs,
            }
        ]
    )
    assert _fenced_logs(out) == logs[-_TEST_REPORT_LOG_TAIL:]


# ---------------------------------------------------------------------------
# El precio del verde está acotado, y por una constante con nombre
# ---------------------------------------------------------------------------
def test_the_green_tail_is_bounded_by_its_own_named_constant() -> None:
    """El verde se paga en CADA revisión, incluidas las de los proyectos que
    siempre pasan. Si costase lo mismo que el rojo, arreglar la ceguera del
    reviewer engordaría todos los prompts del sistema."""
    from orchestrator.dispatch import _TEST_REPORT_LOG_TAIL, _TEST_REPORT_PASSED_LOG_TAIL

    assert _TEST_REPORT_PASSED_LOG_TAIL < _TEST_REPORT_LOG_TAIL

    logs = "y" * (_TEST_REPORT_PASSED_LOG_TAIL * 5)
    out = _block(
        [
            {
                "runtime": "node-jest",
                "exit_codes": [0],
                "all_passed": True,
                "timed_out": False,
                "logs_tail": logs,
            }
        ]
    )
    assert _fenced_logs(out) == logs[-_TEST_REPORT_PASSED_LOG_TAIL:]


def test_the_green_tail_fits_the_longest_summary_epilogue_in_the_catalog() -> None:
    """El tamaño no es un número redondo: es el peor epílogo del catálogo.

    La línea que dice cuántos tests corrieron vive al final de la salida de todas
    las plantillas, pero Maven/Gradle imprimen detrás un banner de cierre
    (`BUILD SUCCESS`, separadores, `Total time`, `Finished at`). La cola verde
    tiene que ser lo bastante larga para que ese banner no empuje al recuento
    fuera del bloque — si lo empuja, el caso «0 tests» vuelve a ser invisible, que
    es el defecto que este fichero arregla."""
    from orchestrator.dispatch import _TEST_REPORT_LOG_TAIL, _TEST_REPORT_PASSED_LOG_TAIL

    maven_epilogue = (
        "[INFO] Tests run: 12, Failures: 0, Errors: 0, Skipped: 0\n"
        "[INFO] \n"
        "[INFO] " + "-" * 72 + "\n"
        "[INFO] BUILD SUCCESS\n"
        "[INFO] " + "-" * 72 + "\n"
        "[INFO] Total time:  12.345 s\n"
        "[INFO] Finished at: 2026-08-29T10:00:00+02:00\n"
        "[INFO] " + "-" * 72 + "\n"
    )
    out = _block(
        [
            {
                "runtime": "java-maven",
                "exit_codes": [0],
                "all_passed": True,
                "timed_out": False,
                "logs_tail": "ruido previo\n" * 500 + maven_epilogue,
            }
        ]
    )
    # Ésta SÍ mide: con un epílogo de forma realista, el recuento sobrevive al
    # recorte y llega al reviewer.
    assert "Tests run: 12" in out

    # Ésta NO mide Maven y conviene decirlo en vez de aparentarlo: `maven_epilogue`
    # es una cadena escrita en este mismo fichero, así que comprobar que cabe en la
    # constante es comprobar que el autor eligió bien su propio ejemplo. Se queda
    # como SUPUESTO DOCUMENTADO —el epílogo real de Maven ronda estos 400 y pico
    # caracteres— y lo que se afirma de verdad es la relación entre las dos colas,
    # que sí es una propiedad del código: el caso verde se paga en CADA revisión,
    # así que no puede costar lo mismo que el fallo, y a la vez tiene que dejar
    # sitio de sobra para un epílogo de este tamaño.
    assert len(maven_epilogue) <= _TEST_REPORT_PASSED_LOG_TAIL, (
        "el supuesto de este test (un epílogo de cierre de ~400 caracteres) ya no "
        "cabe en la cola verde: o el supuesto cambió, o alguien bajó la constante"
    )
    assert _TEST_REPORT_PASSED_LOG_TAIL < _TEST_REPORT_LOG_TAIL
    assert 2 * len(maven_epilogue) // 3 <= _TEST_REPORT_PASSED_LOG_TAIL


def test_a_multi_check_runtime_can_still_bury_an_early_summary() -> None:
    """La limitación que este arreglo NO cierra, fijada a propósito.

    El `logs_tail` de un outcome es el de TODO el runtime —el worker concatena la
    salida de todos sus checks (`test_runtime_task.py`, `result.logs[-4000:]`)—, no
    el de cada check. Así que la cola verde muestra el final del último check: si
    el que ejecutó cero tests fue el PRIMERO de tres, su recuento sigue quedando
    fuera del bloque.

    Se deja escrito en vez de tapado porque la alternativa —presupuestar por check—
    exige que el worker registre logs por check, y el arreglo de verdad es otro:
    los parsers de salida (`junit_xml`) están cableados en el catálogo y MUERTOS en
    el camino vivo, y hasta que se enciendan no hay recuento de tests que consultar.
    Ese trabajo pertenece a la opción A del ADR 0162.

    Este test existe para que la limitación se vea al leer el fichero, y para que
    caiga el día que alguien la cierre — momento en el que hay que borrarlo.
    """
    primero_sin_tests = "No tests executed!\n"
    ultimo_normal = "OK (14 tests)\n"
    out = _block(
        [
            {
                "runtime": "php-phpunit",
                "exit_codes": [0, 0],
                "all_passed": True,
                "timed_out": False,
                "logs_tail": primero_sin_tests + "x" * 4000 + ultimo_normal,
            }
        ]
    )
    assert ultimo_normal.strip() in out
    assert "No tests executed" not in out, (
        "si esto pasa a fallar es BUENA noticia: significa que el recuento por "
        "check ya llega al reviewer. Borra este test y su nota."
    )
