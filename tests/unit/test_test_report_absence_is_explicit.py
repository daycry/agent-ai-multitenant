"""La ausencia de tests deja de ser indistinguible del diseño (ADR 0162, D2/B).

El defecto que fija este fichero, en una línea: `_format_test_report_block`
devolvía **cadena vacía** cuando no había outcomes, y entonces el bloque
`<test-report>` **desaparecía** del prompt del reviewer. Un proyecto sin tests y
un proyecto cuyos tests reventaron producían así EXACTAMENTE el mismo prompt —
lo que el ADR 0162 llama «un verde que no significa nada».

Lo que se exige aquí:

* con outcomes, el bloque es **byte a byte el de hoy** (no-regresión: la opción
  B del ADR informa, no cambia lo que ya se dice);
* sin outcomes, el bloque **existe** y dice CUÁL de los tres casos es, y el
  discriminante sale de datos reales (el runtime declarado por el proyecto, los
  criterios ejecutables de la tarea, y si la fase de tests llegó a arrancar);
* los dos prompts sembrados del reviewer (ES y EN) dicen qué hacer cuando el
  bloque declara que NO se ejecutó nada.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

pytestmark = pytest.mark.unit


def _block(
    outcomes: list[dict[str, object]],
    *,
    project_declares_runtime: bool = True,
    executable_criteria: int = 1,
    tests_were_launched: bool = True,
) -> str:
    from orchestrator.dispatch import _format_test_report_block

    return _format_test_report_block(
        outcomes,
        project_declares_runtime=project_declares_runtime,
        executable_criteria=executable_criteria,
        tests_were_launched=tests_were_launched,
    )


# ---------------------------------------------------------------------------
# No-regresión: con outcomes, el bloque no se mueve un byte
# ---------------------------------------------------------------------------
def test_a_failed_outcome_renders_exactly_as_before() -> None:
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


def test_a_passed_outcome_now_carries_its_log_tail() -> None:
    """Este test CAMBIÓ, y conviene decir por qué en vez de dejarlo mudo.

    Hasta el 2026-08-29 afirmaba lo contrario —que un outcome verde renderiza sin
    logs— y por eso se llamaba `..._renders_exactly_as_before`. Pero eso no era
    una no-regresión: era el defecto de la §«trampa» del ADR 0162 fijado por un
    test. `exit_code == 0` no significa «los tests pasaron», y la línea que lo
    desmiente («No tests executed!») vive justo en la cola que se descartaba.

    La forma exacta del caso verde la cubre
    `tests/unit/test_green_test_report_shows_its_logs.py`; aquí sólo se re-ancla
    para que este fichero no siga afirmando lo derogado."""
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


def test_a_timed_out_outcome_renders_exactly_as_before() -> None:
    out = _block(
        [
            {
                "runtime": "php-phpunit",
                "exit_codes": [137],
                "all_passed": False,
                "timed_out": True,
                "logs_tail": "",
            }
        ]
    )
    assert out == (
        "<test-report>\n"
        "- runtime php-phpunit: FAILED (exit_codes=[137], timed_out=true)\n"
        "</test-report>"
    )


# ---------------------------------------------------------------------------
# El hallazgo: sin outcomes ya NO hay cadena vacía
# ---------------------------------------------------------------------------
def test_no_outcomes_no_longer_disappears_from_the_prompt() -> None:
    """El bloque llega SIEMPRE. Si no llega, el reviewer no puede distinguir
    «no había tests» de «los tests no corrieron»."""
    out = _block([], project_declares_runtime=False, executable_criteria=0)
    assert out != ""
    assert out.startswith("<test-report>")
    assert out.endswith("</test-report>")


def test_case_1_the_project_declares_no_runtime() -> None:
    """Caso 1: no había NADA que ejecutar, y eso es una propiedad del proyecto."""
    out = _block(
        [],
        project_declares_runtime=False,
        executable_criteria=0,
        tests_were_launched=False,
    )
    assert "NO TEST RESULTS" in out
    assert "no test runtime" in out
    # No puede confundirse con los otros dos casos.
    assert "INFRASTRUCTURE" not in out
    assert "not executable" not in out


def test_case_2_criteria_existed_but_none_was_executable() -> None:
    """Caso 2: había criterios, pero ninguno traía `runtime` + `command`, así que
    no se lanzó nada. El número de criterios sale de la tarea, no de una
    suposición."""
    out = _block(
        [],
        project_declares_runtime=True,
        executable_criteria=0,
        tests_were_launched=False,
    )
    assert "NO TEST RESULTS" in out
    assert "not executable" in out
    assert "INFRASTRUCTURE" not in out


def test_case_3_the_phase_launched_and_brought_nothing_back() -> None:
    """Caso 3: consta un `test_run_started` y no hay ni un solo outcome. Eso NO
    es un proyecto sin tests: es la plataforma fallando."""
    out = _block(
        [],
        project_declares_runtime=True,
        executable_criteria=2,
        tests_were_launched=True,
    )
    assert "NO TEST RESULTS" in out
    assert "INFRASTRUCTURE" in out
    assert "not executable" not in out


def test_executable_criteria_that_never_started_is_not_sold_as_no_tests() -> None:
    """La cuarta esquina: había criterios ejecutables y no consta que la fase
    arrancara. Tampoco puede leerse como «este proyecto no tiene tests»."""
    out = _block(
        [],
        project_declares_runtime=True,
        executable_criteria=3,
        tests_were_launched=False,
    )
    assert "NO TEST RESULTS" in out
    assert "no test runtime" not in out
    assert "did not run" in out


def test_an_infrastructure_failure_outcome_is_labelled_as_such() -> None:
    """El puente con la opción D: un outcome que viene marcado como fallo de
    INFRAESTRUCTURA no puede leerse como «los tests del tenant fallaron»."""
    out = _block(
        [
            {
                "runtime": "python-pytest",
                "exit_codes": [],
                "all_passed": False,
                "timed_out": False,
                "infrastructure_failure": "runtime_image_unavailable",
                "logs_tail": "no se pudo obtener la imagen fijada por digest",
            }
        ]
    )
    assert "INFRASTRUCTURE FAILURE" in out
    assert "runtime_image_unavailable" in out
    assert "no se pudo obtener la imagen fijada por digest" in out


# ---------------------------------------------------------------------------
# El contador de criterios ejecutables y el filtro del worker: el MISMO predicado
# ---------------------------------------------------------------------------
_CRITERIA_MATRIX: list[dict[str, object]] = [
    {"id": "ok", "runtime": "python-pytest", "command": "pytest -q"},
    {"id": "sin_command", "runtime": "python-pytest"},
    {"id": "sin_runtime", "command": "pytest -q"},
    {"id": "command_vacio", "runtime": "python-pytest", "command": ""},
    {"id": "manual", "kind": "human"},
    {"id": "ok2", "runtime": "node-jest", "command": "jest"},
]


@pytest.mark.asyncio
async def test_the_counter_agrees_with_what_the_worker_actually_launches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El orchestrator no importa el paquete de workers, así que el predicado
    «ejecutable» está escrito DOS veces. Si divergen, el bloque le dice al
    reviewer que había N criterios ejecutables cuando el worker lanzó otra
    cantidad — o sea, vuelve a mentir, que es el defecto que arregla el ADR."""
    from uuid import uuid4

    from orchestrator.dispatch import _count_executable_criteria
    from workers.config import Settings
    from workers.execution import _run_task_tests
    from workers.tasks import test_runtime_task

    dispatched: list[dict[str, object]] = []

    async def _fake_dispatch(request: dict[str, object]) -> dict[str, object]:
        dispatched.append(request)
        return {}

    monkeypatch.setattr(test_runtime_task, "dispatch_test_runtime_and_wait", _fake_dispatch)

    class _Task:
        acceptance_criteria = _CRITERIA_MATRIX

    await _run_task_tests(
        Settings(),
        tenant_id=uuid4(),
        task_id=uuid4(),
        worktree_host_path="/data/wt/t1",
        acceptance_criteria=list(_CRITERIA_MATRIX),
    )

    launched = list(dispatched[0]["acceptance_criteria"])  # type: ignore[arg-type]
    assert _count_executable_criteria(_Task()) == len(launched) == 2


def test_the_counter_survives_a_task_without_criteria() -> None:
    from orchestrator.dispatch import _count_executable_criteria

    class _Task:
        acceptance_criteria = None

    assert _count_executable_criteria(_Task()) == 0


def test_the_counter_ignores_plain_string_criteria() -> None:
    """Las tareas antiguas traen criterios en prosa: no son ejecutables."""
    from orchestrator.dispatch import _count_executable_criteria

    class _Task:
        acceptance_criteria: ClassVar[list[str]] = [
            "el endpoint devuelve 200",
            "los tests pasan",
        ]

    assert _count_executable_criteria(_Task()) == 0


# ---------------------------------------------------------------------------
# El prompt sembrado del reviewer, en los DOS idiomas
# ---------------------------------------------------------------------------
def _reviewer_prompts() -> tuple[str, str]:
    from api_server.seeds.builtin_agents import BUILTIN_AGENTS

    reviewer = next(a for a in BUILTIN_AGENTS if a.slug == "reviewer")
    return reviewer.system_prompt_es, reviewer.system_prompt_en


def test_the_spanish_reviewer_prompt_covers_tests_that_did_not_run() -> None:
    es, _ = _reviewer_prompts()
    assert "NO TEST RESULTS" in es
    assert "infraestructura" in es.lower()


def test_the_english_reviewer_prompt_covers_tests_that_did_not_run() -> None:
    _, en = _reviewer_prompts()
    assert "NO TEST RESULTS" in en
    assert "infrastructure" in en.lower()


@pytest.mark.parametrize("index", [0, 1])
def test_neither_prompt_orders_an_automatic_rejection(index: int) -> None:
    """La opción C del ADR 0162 (gate duro) NO está firmada: hoy bloquearía el
    100 % de las tareas. Decirle al reviewer «rechaza siempre que no haya tests»
    sería esa misma opción por la puerta de atrás."""
    prompt = _reviewer_prompts()[index]
    lowered = prompt.lower()
    for forbidden in (
        "always reject",
        "rechaza siempre",
        "siempre rechaza",
        "must reject",
        "debes rechazar",
    ):
        assert forbidden not in lowered, f"el prompt ordena rechazar: {forbidden!r}"


def test_the_prompt_and_the_block_use_the_same_literal() -> None:
    """El acoplamiento que nadie ve hasta que se rompe.

    El prompt del reviewer le enseña a leer el bloque «cuando empieza por
    `NO TEST RESULTS`». Ese literal lo emite el orchestrator desde una constante
    propia, en otro desplegable. Si alguien reescribe la cabecera del bloque —o
    traduce el prompt— sin tocar el otro lado, la instrucción deja de aplicarse y
    NADIE se entera: el bloque sigue llegando, el reviewer sigue revisando, y
    vuelve a tratar la ausencia de tests como si el proyecto no tuviera.

    Es exactamente el modo de fallo que el ADR 0162 denuncia, así que el literal
    se ata aquí en vez de confiarlo a que los dos ficheros se editen juntos.
    """
    from api_server.seeds.builtin_agents import BUILTIN_AGENTS
    from orchestrator.dispatch import _NO_TEST_RESULTS

    reviewers = [
        a
        for a in BUILTIN_AGENTS
        if "review" in str(getattr(a, "slug", "") or getattr(a, "role", "")).lower()
    ]
    assert reviewers, "no se encontró ningún agente reviewer sembrado"

    for agente in reviewers:
        prompts = [
            str(p)
            for p in (
                getattr(agente, "system_prompt", None),
                getattr(agente, "system_prompt_en", None),
                getattr(agente, "system_prompt_es", None),
            )
            if p
        ]
        for prompt in prompts:
            if _NO_TEST_RESULTS.lower() in prompt.lower() or "test-report" in prompt:
                assert _NO_TEST_RESULTS in prompt, (
                    f"el prompt de '{getattr(agente, 'slug', agente)}' habla del "
                    f"informe de tests pero no contiene el literal exacto que "
                    f"emite el orchestrator ({_NO_TEST_RESULTS!r}): la instrucción "
                    f"no se le aplicará al bloque que de verdad recibe"
                )
