"""La señal que declaró cada criterio llega al reviewer (ADR 0162, opción A).

`expected_signal` se evalúa desde la ola 1 —por check y con la salida de ese
check— y el resultado se persiste en el outcome del test-runtime como
``check_signals``. Y ahí se quedaba: el bloque ``<test-report>`` no lo miraba, así
que el reviewer seguía leyendo ``PASSED (exit_codes=[0])`` de un check cuyo propio
criterio decía que no se había verificado nada. **Un dato que nadie enseña no
cambia nada.**

Este fichero fija cómo se enseña, y la regla que manda sobre todo lo demás:

    **«no se pudo evaluar» NUNCA puede redactarse como «no se cumplió»**

Son los mismos tres estados que ya usa la línea de recuento, un piso más abajo:

  (a) la señal se cumple          → «declared signal HOLDS»
  (b) la señal NO se cumple       → «declared signal NOT met»
  (c) no se pudo evaluar          → «declared signal could not be evaluated»

Colapsar (c) en (b) fabricaría el falso FALLO que este ADR pone por delante de
todo: le diría al reviewer que el criterio no se cumplió cuando lo único cierto
es que la señal no era de las que sabemos comprobar, o que el recuento quedó
ausente.

**Y una línea sólo se paga cuando dice algo que la cabecera no dice.** El
``expected_signal`` por defecto —``exit_code == 0``— tiene por construcción el
mismo veredicto que el código de salida, que la cabecera ya imprime. Repetirlo
debajo invita a leer dos problemas donde hay uno, que es el mismo criterio con el
que el fallo de infraestructura no lleva línea de recuento. Así que el parque
actual —todo criterio que no declara señal— renderiza **byte a byte** como hoy.
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
    base: dict[str, Any] = {
        "runtime": "php-phpunit",
        "exit_codes": [0],
        "all_passed": True,
        "timed_out": False,
        "logs_tail": "",
        "test_counts": None,
        "checks_without_declared_check_type": 0,
        "check_signals": [],
    }
    base.update(overrides)
    return base


def _signal(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "check_id": "auto_01",
        "expected_signal": "exit_code == 0 and tests > 0",
        "exit_code": 0,
        "satisfied": True,
        "test_counts": None,
    }
    base.update(overrides)
    return base


def _signal_lines(block: str) -> list[str]:
    from orchestrator.dispatch import _SIGNAL_LINE_PREFIX

    return [line for line in block.splitlines() if line.startswith(_SIGNAL_LINE_PREFIX)]


# ---------------------------------------------------------------------------
# Los tres estados llegan, y llegan distintos
# ---------------------------------------------------------------------------
def test_a_satisfied_signal_reaches_the_reviewer() -> None:
    from orchestrator.dispatch import _SIGNAL_MET_MARKER

    (line,) = _signal_lines(_block([_outcome(check_signals=[_signal(satisfied=True)])]))

    assert _SIGNAL_MET_MARKER in line
    assert "auto_01" in line
    assert "exit_code == 0 and tests > 0" in line


def test_an_unmet_signal_says_the_green_proves_nothing() -> None:
    """El falso verde de la base de datos viva: exit 0 con cero tests ejecutados
    y un criterio que pedía ``tests > 0``."""
    from orchestrator.dispatch import _SIGNAL_UNMET_MARKER

    (line,) = _signal_lines(_block([_outcome(check_signals=[_signal(satisfied=False)])]))

    assert _SIGNAL_UNMET_MARKER in line
    assert "NOT evidence" in line


def test_an_unevaluated_signal_is_never_rendered_as_an_unmet_one() -> None:
    """**El test más importante del fichero.**

    Si (c) se redactase como (b), el reviewer leería «este criterio no se
    cumplió» cuando lo único cierto es que no supimos evaluar la señal. Es el
    falso fallo, que el operador puso por delante de todo lo demás."""
    from orchestrator.dispatch import _SIGNAL_UNEVALUATED_MARKER, _SIGNAL_UNMET_MARKER

    (line,) = _signal_lines(_block([_outcome(check_signals=[_signal(satisfied=None)])]))

    assert _SIGNAL_UNEVALUATED_MARKER in line
    assert _SIGNAL_UNMET_MARKER not in line
    assert "not a failure" in line


def test_the_three_wordings_share_no_discriminating_literal() -> None:
    """La propiedad de verdad: los tres estados son distinguibles A LA LECTURA.

    No basta con que existan tres textos; hace falta que el literal que
    identifica a cada uno no aparezca en los otros dos, porque el reviewer lee,
    no parsea. Misma disciplina que la línea de recuento."""
    from orchestrator.dispatch import (
        _SIGNAL_MET_MARKER,
        _SIGNAL_UNEVALUATED_MARKER,
        _SIGNAL_UNMET_MARKER,
    )

    textos = {
        estado: _signal_lines(_block([_outcome(check_signals=[_signal(satisfied=estado)])]))[0]
        for estado in (True, False, None)
    }

    assert len(set(textos.values())) == 3
    for marker, owner in (
        (_SIGNAL_MET_MARKER, True),
        (_SIGNAL_UNMET_MARKER, False),
        (_SIGNAL_UNEVALUATED_MARKER, None),
    ):
        assert marker in textos[owner]
        for estado, texto in textos.items():
            if estado is not owner:
                assert marker not in texto, f"{marker!r} se cuela en otra redacción: {texto!r}"


def test_every_check_gets_its_own_line() -> None:
    """La señal se evalúa POR CHECK, así que se reporta por check: un resumen
    del plan volvería a dejar que un check conteste por otro."""
    lines = _signal_lines(
        _block(
            [
                _outcome(
                    exit_codes=[0, 0],
                    check_signals=[
                        _signal(check_id="auto_01", satisfied=True),
                        _signal(check_id="auto_02", satisfied=False),
                    ],
                )
            ]
        )
    )

    assert len(lines) == 2
    assert "auto_01" in lines[0] and "auto_02" in lines[1]


# ---------------------------------------------------------------------------
# No-regresión: el parque actual no se mueve un byte
# ---------------------------------------------------------------------------
def test_a_default_signal_adds_no_line_because_the_header_already_says_it() -> None:
    """``exit_code == 0`` es el default de TODO criterio existente y su veredicto
    ES el código de salida, que la cabecera ya imprime. Una línea que repite la
    cabecera se paga en CADA revisión de CADA proyecto y no informa de nada."""
    assert (
        _signal_lines(_block([_outcome(check_signals=[_signal(expected_signal="exit_code == 0")])]))
        == []
    )


def test_an_outcome_without_the_signals_key_renders_byte_for_byte_as_today() -> None:
    """Todos los ``test_run_completed`` persistidos antes de esta ola son así."""
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


def test_a_run_with_only_default_signals_renders_byte_for_byte_as_today() -> None:
    """El parque REAL después de desplegar esto: los checks se ejecutan, la señal
    se evalúa y el bloque sale exactamente igual que antes."""
    out = _block(
        [
            {
                "runtime": "node-jest",
                "exit_codes": [0],
                "all_passed": True,
                "timed_out": False,
                "logs_tail": "ok",
                "check_signals": [
                    {
                        "check_id": "auto_01",
                        "expected_signal": "exit_code == 0",
                        "exit_code": 0,
                        "satisfied": True,
                        "test_counts": None,
                    }
                ],
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


def test_an_infrastructure_failure_gains_no_signal_line() -> None:
    """Su cabecera ya dice «the tests did NOT run» y su ``check_signals`` está
    vacío: no hay señal que reportar de un check que no se ejecutó."""
    from workers.tasks.test_runtime_task import infra_failure_outcome

    out = _block([infra_failure_outcome(stage="docker_unavailable", detail="no daemon")])

    assert _signal_lines(out) == []


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "auto_01: ok",
        [None, 3, "x"],
        [{"expected_signal": "exit_code == 0 and tests > 0"}],  # sin `satisfied`
        [{"satisfied": True}],  # sin señal declarada
        [{"expected_signal": "exit_code == 0 and tests > 0", "satisfied": "sí"}],
    ],
)
def test_a_malformed_signals_payload_adds_nothing_instead_of_guessing(payload: Any) -> None:
    """Viene de un JSONB de auditoría con años de versiones dentro. Lo que no se
    reconoce no se renderiza: inventarle un estado sería afirmar algo que nadie
    comprobó — y de los tres estados, el que se inventaría es el peor."""
    assert _signal_lines(_block([_outcome(check_signals=payload)])) == []


# ---------------------------------------------------------------------------
# El veredicto no se mueve: esto NO es la opción C
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("satisfied", [True, False, None])
def test_the_signal_never_changes_the_headline_verdict(satisfied: bool | None) -> None:
    """Un ``exit 0`` con la señal declarada incumplida sigue diciendo ``PASSED``
    en la cabecera. La opción A hace VISIBLE el falso verde; cerrarlo es la
    opción C, que **no está firmada**."""
    out = _block([_outcome(all_passed=True, check_signals=[_signal(satisfied=satisfied)])])

    assert "- runtime php-phpunit: PASSED (exit_codes=[0])" in out


# ---------------------------------------------------------------------------
# La costura entre los dos desplegables
# ---------------------------------------------------------------------------
def test_the_two_sides_of_the_seam_name_the_key_the_same() -> None:
    """El orchestrator no importa el paquete de workers, así que el nombre de la
    clave está escrito DOS veces."""
    from orchestrator.dispatch import _CHECK_SIGNALS_KEY
    from workers.tasks.test_runtime_task import CHECK_SIGNALS_KEY

    assert _CHECK_SIGNALS_KEY == CHECK_SIGNALS_KEY


@pytest.mark.parametrize(
    "raw",
    [
        "exit_code == 0",
        "exitcode==0",
        "  EXIT_CODE   ==  0 ",
        "",
        None,
        "exit_code == 0 and tests > 0",
        "exit_code == 0 and tests >= 1",
        "coverage >= 80%",
    ],
)
def test_the_default_signal_predicate_agrees_with_the_evaluator(raw: str | None) -> None:
    """La otra mitad duplicada: qué señal es «la de siempre».

    El orchestrator decide si una señal aporta algo sobre la cabecera, y el
    evaluador que la calcula vive en otro desplegable. Si las dos lecturas se
    separan, o aparecerían líneas ruidosas en cada revisión o desaparecería la
    línea del caso que importa — y en ninguno de los dos casos fallaría nada."""
    from orchestrator.dispatch import _signal_adds_nothing
    from shared_test_runtimes.signals import SIGNAL_EXIT_ZERO, normalise_signal

    es_la_de_siempre = not (raw or "").strip() or normalise_signal(raw) == SIGNAL_EXIT_ZERO

    assert _signal_adds_nothing(raw) is es_la_de_siempre


def test_the_adr_case_travels_from_the_runner_log_to_the_reviewer_prompt() -> None:
    """Extremo a extremo, sin escribir a mano ningún eslabón: la salida literal
    de PHPUnit del ADR entra por el ``exec_run`` del contenedor y sale por el
    prompt del reviewer."""
    from unittest.mock import MagicMock

    from orchestrator.dispatch import _SIGNAL_UNMET_MARKER
    from workers.config import Settings
    from workers.tasks.test_runtime_task import runtime_outcome
    from workers.test_runtime import TestRuntimeRunner, TestRuntimeSpec, group_tasks_by_runtime

    salida = b"PHPUnit 10.5.64 by Sebastian Bergmann and contributors.\n\nNo tests executed!\n"

    def _exec_run(cmd: Any, **_kw: Any) -> MagicMock:
        joined = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        return MagicMock(exit_code=0, output=salida if "phpunit" in joined else b"ok\n")

    container = MagicMock(id="c-0", exec_run=MagicMock(side_effect=_exec_run))
    client = MagicMock()
    client.containers.run.return_value = container
    network = MagicMock(remove=MagicMock())
    network.name = "task-net"
    client.networks.create.return_value = network

    plans = group_tasks_by_runtime(
        [
            {
                "id": "auto_01",
                "description": "la suite pasa",
                "runtime": "php-phpunit",
                "command": "vendor/bin/phpunit --testsuite E2E",
                "expected_signal": "exit_code == 0 and tests > 0",
            }
        ]
    )
    result = TestRuntimeRunner(Settings(), client=client).launch(
        TestRuntimeSpec(plan=plans[0], worktree_host_path="/data/wt/t1")
    )

    out = _block([runtime_outcome(result)])

    assert "- runtime php-phpunit: PASSED (exit_codes=[0])" in out, "el veredicto no se toca"
    (line,) = _signal_lines(out)
    assert _SIGNAL_UNMET_MARKER in line
