"""La plataforma pasa a saber CUÁNTOS tests corrieron (ADR 0162, ola 1).

El hallazgo que esto ataca está en el §«La trampa que hay que cerrar CON A» del
ADR 0162, y no es teoría: en la base de datos viva hay dos ejecuciones de PHPUnit
registradas como correctas cuyo log dice ``No tests executed!``. El veredicto de
un check sale HOY sólo del código de salida, y ``exit_code == 0`` no significa
«los tests pasaron»: puede significar «no había tests» — un ``--filter`` que no
casa con nada, una suite mal nombrada, un ``phpunit.xml`` que no ve el
directorio.

Los ocho parsers de :mod:`shared_test_runtimes.parsers` llevaban escritos desde
el Plan 06 y **nadie los llamaba**. Esta ola los enciende para MEDIR, no para
decidir: ningún recuento cambia si un check se da por pasado o fallado.

**La regla que ordena todo el fichero, y la razón de que la mitad de los tests
existan:** «no se pudo parsear» NUNCA puede convertirse en «cero tests». Son tres
estados y tienen que seguir siéndolo de punta a punta:

  (a) parseado, N tests   → ``test_counts.total == N``
  (b) parseado, CERO tests → ``test_counts.total == 0``  (¡objeto presente!)
  (c) no parseable         → ``test_counts is None``     (AUSENTE, jamás 0)

Confundir (c) con (b) fabrica un falso fallo: le diría al reviewer «este cambio
no ejecutó ni un test» cuando lo cierto es «no supimos leer la salida». El
operador pidió expresamente evitar los falsos fallos, así que ante la duda el
estado es (c).

Los tests se anclan en :meth:`TestRuntimeRunner.launch` —donde el dato NACE, con
la salida del contenedor entrando por ``exec_run``— y no construyendo un
``TestRuntimeResult`` a mano, que es como se puede escribir un verde que no mide
nada.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from workers.config import Settings

pytestmark = pytest.mark.unit


# --- salidas REALES de los runners ------------------------------------------

# PHPUnit con tests de verdad. La forma canónica del epílogo.
PHPUNIT_OK = b"""PHPUnit 10.5.64 by Sebastian Bergmann and contributors.

..............                                                    14 / 14 (100%)

Time: 00:00.412, Memory: 20.00 MB

OK (14 tests, 28 assertions)
"""

# La salida LITERAL del ADR 0162: exit 0 y cero tests ejecutados.
PHPUNIT_NO_TESTS = b"""PHPUnit 10.5.64 by Sebastian Bergmann and contributors.

No tests executed!
"""

PHPUNIT_FAILURES = b"""PHPUnit 10.5.64 by Sebastian Bergmann and contributors.

.F...                                                               5 / 5 (100%)

FAILURES!
Tests: 5, Assertions: 9, Failures: 1.
"""

# Ninguno de los parsers del catalogo entiende esto, y el reconocedor de
# epilogos tampoco. Es el caso (c).
GIBBERISH = b"""Building the world, please hold.
[####------] 42%
done in 3s
"""

PYTEST_MIXED = b"""
============================= test session starts ==============================
collected 14 items

tests/test_a.py .F...                                                    [100%]

=========================== short test summary info ============================
FAILED tests/test_a.py::test_b - assert 1 == 2
========================= 1 failed, 13 passed in 0.42s =========================
"""

PYTEST_NO_TESTS = b"""
============================= test session starts ==============================
collected 0 items

============================ no tests ran in 0.01s =============================
"""


# --- doble de Docker --------------------------------------------------------


def _fake_client(
    outputs: dict[str, tuple[int, bytes]],
    *,
    default: tuple[int, bytes] = (0, b""),
) -> MagicMock:
    """Cliente Docker falso cuyo ``exec_run`` contesta según el comando.

    Las claves de ``outputs`` se buscan como subcadena del comando envuelto que
    ``_exec`` construye (``timeout N sh -c '…'``), así que basta con el nombre
    del binario para distinguir el ``pre_install`` del check.
    """

    def _exec_run(cmd: Any, **_kw: Any) -> MagicMock:
        joined = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        for needle, (rc, out) in outputs.items():
            if needle in joined:
                return MagicMock(exit_code=rc, output=out)
        return MagicMock(exit_code=default[0], output=default[1])

    def _run(image: str, **kwargs: Any) -> MagicMock:
        container = MagicMock()
        container.id = "container-0"
        container.image = image
        container.kwargs = kwargs
        container.exec_run = MagicMock(side_effect=_exec_run)
        return container

    client = MagicMock()
    client.containers.run.side_effect = _run
    network = MagicMock(remove=MagicMock())
    network.name = "test-runtime-php-phpunit-abcd"
    client.networks.create.return_value = network
    return client


def _spec(runtime_id: str, command: str, **overrides: Any) -> Any:
    from shared_test_runtimes.catalog import get
    from workers.test_runtime import AcceptanceCheck, RuntimePlan, TestRuntimeSpec

    plan = RuntimePlan(
        template=get(runtime_id),
        checks=(
            AcceptanceCheck(
                id="auto_01",
                description="suite",
                runtime=runtime_id,
                command=command,
            ),
        ),
    )
    base: dict[str, Any] = {"plan": plan, "worktree_host_path": "/data/wt/t1"}
    base.update(overrides)
    return TestRuntimeSpec(**base)


def _launch_phpunit(output: bytes, *, rc: int = 0) -> Any:
    from workers.test_runtime import TestRuntimeRunner

    client = _fake_client({"phpunit": (rc, output)}, default=(0, b"composer: ok\n"))
    runner = TestRuntimeRunner(Settings(), client=client)
    return runner.launch(_spec("php-phpunit", "vendor/bin/phpunit"))


# ---------------------------------------------------------------------------
# (a) parseado, N tests
# ---------------------------------------------------------------------------


def test_phpunit_ok_line_yields_the_real_count() -> None:
    """``OK (14 tests, 28 assertions)`` tiene que valer 14, no 0.

    Es el caso que hace útil todo lo demás: sin él, un proyecto PHP sano y uno
    que no ejecutó nada seguirían siendo indistinguibles."""
    result = _launch_phpunit(PHPUNIT_OK)

    assert result.test_counts is not None
    assert result.test_counts.total == 14
    assert result.test_counts.passed == 14
    assert result.test_counts.failed == 0


def test_phpunit_failures_epilogue_is_counted_too() -> None:
    result = _launch_phpunit(PHPUNIT_FAILURES, rc=1)

    assert result.test_counts is not None
    assert result.test_counts.total == 5
    assert result.test_counts.failed == 1
    assert result.test_counts.passed == 4


def test_pytest_summary_line_is_counted() -> None:
    from workers.test_runtime import TestRuntimeRunner

    client = _fake_client({"pytest -q": (1, PYTEST_MIXED)}, default=(0, b"pip: ok\n"))
    runner = TestRuntimeRunner(Settings(), client=client)
    result = runner.launch(_spec("python-pytest", "pytest -q"))

    assert result.test_counts is not None
    assert result.test_counts.total == 14
    assert result.test_counts.passed == 13
    assert result.test_counts.failed == 1


# ---------------------------------------------------------------------------
# (b) parseado, CERO tests — el falso verde del ADR, ahora visible
# ---------------------------------------------------------------------------


def test_no_tests_executed_is_zero_and_present() -> None:
    """La salida LITERAL del ADR. Cero es un recuento, no una ausencia."""
    result = _launch_phpunit(PHPUNIT_NO_TESTS)

    assert result.test_counts is not None, "cero tests es un HECHO medido, no una ausencia"
    assert result.test_counts.total == 0


def test_pytest_no_tests_ran_is_zero_and_present() -> None:
    from workers.test_runtime import TestRuntimeRunner

    client = _fake_client({"pytest -q": (0, PYTEST_NO_TESTS)}, default=(0, b"pip: ok\n"))
    runner = TestRuntimeRunner(Settings(), client=client)
    result = runner.launch(_spec("python-pytest", "pytest -q"))

    assert result.test_counts is not None
    assert result.test_counts.total == 0


# ---------------------------------------------------------------------------
# (c) no parseable — EL test que evita el falso fallo
# ---------------------------------------------------------------------------


def test_unparseable_output_is_absence_never_zero() -> None:
    """Si nadie entiende la salida, el recuento está AUSENTE.

    Si esto devolviera ``total=0`` la plataforma le diría al reviewer que el
    cambio no ejecutó ni un test cuando lo único cierto es que no supimos leer
    la salida. Ese es exactamente el falso fallo que el encargo prohíbe."""
    result = _launch_phpunit(GIBBERISH)

    assert result.test_counts is None


def test_raw_text_parser_never_fabricates_a_count() -> None:
    """``raw_text`` es una heurística de ESTADO, no una medición.

    Está declarado en el ``output_parsers`` de todas las plantillas, nunca
    devuelve ``None`` y siempre trae ``TestSummary()`` a ceros. Meterlo en la
    cadena de recuento convertiría CUALQUIER salida desconocida en «cero tests»
    — la confusión (c)→(b) en su forma más fácil de cometer."""
    from shared_test_runtimes.counts import count_tests
    from shared_test_runtimes.parsers import raw_text

    text = GIBBERISH.decode()
    # El parser sí produce un informe (con summary a ceros)…
    report = raw_text.parse(text, runtime="php-phpunit")
    assert report is not None
    assert report.summary.total == 0
    # …y aun así el recuento tiene que quedar AUSENTE.
    assert count_tests(text, runtime="php-phpunit", parsers=("junit_xml", "raw_text")) is None


# ---------------------------------------------------------------------------
# El veredicto NO se mueve
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("output", "rc"),
    [
        (PHPUNIT_OK, 0),
        (PHPUNIT_NO_TESTS, 0),
        (GIBBERISH, 0),
    ],
)
def test_exit_zero_still_passes_whatever_the_count_says(output: bytes, rc: int) -> None:
    """Cero cambios de veredicto, y el caso del medio es el importante.

    ``No tests executed!`` con exit 0 SIGUE dando verde. No es un descuido: el
    gate es la opción C del ADR 0162 y **no está firmada**, precisamente porque
    ahí viven los falsos fallos. Esta ola mide y enseña; no decide."""
    result = _launch_phpunit(output, rc=rc)

    assert result.all_passed() is True


@pytest.mark.parametrize("output", [PHPUNIT_OK, PHPUNIT_NO_TESTS, GIBBERISH])
def test_exit_nonzero_still_fails_whatever_the_count_says(output: bytes) -> None:
    result = _launch_phpunit(output, rc=1)

    assert result.all_passed() is False


def test_a_broken_counter_cannot_change_the_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el contador revienta, se pierde el recuento — nunca el veredicto.

    La medición es nueva y el veredicto lleva años funcionando; un bug en lo
    nuevo no puede tumbar lo viejo."""
    import workers.test_runtime as tr

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("el contador explotó")

    monkeypatch.setattr(tr, "count_tests", _boom)
    result = _launch_phpunit(PHPUNIT_OK)

    assert result.test_counts is None
    assert result.all_passed() is True


# ---------------------------------------------------------------------------
# El outcome que consume la siguiente ola
# ---------------------------------------------------------------------------


def test_outcome_dict_carries_the_three_states() -> None:
    """El dict que viaja al JSONB de auditoría preserva los tres estados."""
    from workers.tasks.test_runtime_task import runtime_outcome

    counted = runtime_outcome(_launch_phpunit(PHPUNIT_OK))
    assert counted["test_counts"] == {
        "total": 14,
        "passed": 14,
        "failed": 0,
        "errored": 0,
        "skipped": 0,
        "source": "phpunit_text",
    }

    zero = runtime_outcome(_launch_phpunit(PHPUNIT_NO_TESTS))
    assert zero["test_counts"] is not None
    assert zero["test_counts"]["total"] == 0

    absent = runtime_outcome(_launch_phpunit(GIBBERISH))
    assert absent["test_counts"] is None, "ausencia, no cero"

    # La clave existe SIEMPRE: un consumidor que haga `o.get("test_counts")`
    # no puede confundir «no vino en el payload» con «no se pudo medir».
    assert "test_counts" in absent


def test_outcome_dict_keeps_the_verdict_keys_untouched() -> None:
    from workers.tasks.test_runtime_task import runtime_outcome

    outcome = runtime_outcome(_launch_phpunit(PHPUNIT_NO_TESTS))

    assert outcome["all_passed"] is True
    assert outcome["exit_codes"] == [0]
    assert outcome["timed_out"] is False
    assert outcome["runtime"] == "php-phpunit"


# ---------------------------------------------------------------------------
# check_type: el silencio deja de significar «automated»
# ---------------------------------------------------------------------------


def test_criterion_without_check_type_still_runs() -> None:
    """NO-REGRESIÓN. Es la mitad que no puede moverse.

    Hoy un criterio con ``runtime`` + ``command`` y sin ``check_type`` se
    ejecuta; si dejara de hacerlo, las tareas que ya funcionan se quedarían sin
    tests de golpe — un falso verde nuevo, y masivo."""
    from workers.test_runtime import group_tasks_by_runtime

    plans = group_tasks_by_runtime([{"id": "x", "runtime": "python-pytest", "command": "pytest"}])

    assert len(plans) == 1
    assert plans[0].checks[0].id == "x"


def test_criterion_without_check_type_is_recorded_as_undeclared() -> None:
    """Lo que SÍ cambia: el sistema sabe que nadie lo declaró.

    ``entry.get("check_type", "automated")`` leía el silencio como «esto debería
    verificarse a máquina». Es la misma regla que el ADR enuncia tres veces: un
    valor ausente no puede significar nada más fuerte que «desconocido»."""
    from workers.test_runtime import group_tasks_by_runtime

    plans = group_tasks_by_runtime([{"id": "x", "runtime": "python-pytest", "command": "pytest"}])

    assert plans[0].checks[0].declared_check_type is None


def test_criterion_with_explicit_automated_records_the_declaration() -> None:
    from workers.test_runtime import group_tasks_by_runtime

    plans = group_tasks_by_runtime(
        [
            {
                "id": "x",
                "check_type": "automated",
                "runtime": "python-pytest",
                "command": "pytest",
            }
        ]
    )

    assert plans[0].checks[0].declared_check_type == "automated"


def test_non_automated_check_types_are_still_skipped() -> None:
    from workers.test_runtime import group_tasks_by_runtime

    criteria = [
        {"id": "h", "check_type": "human", "runtime": "python-pytest", "command": "pytest"},
        {"id": "m", "check_type": "manual", "runtime": "python-pytest", "command": "pytest"},
    ]

    assert group_tasks_by_runtime(criteria) == ()


def test_outcome_reports_how_many_checks_nobody_declared() -> None:
    """El recuento de no-declarados llega al outcome, que es donde se puede ver.

    Va como MÉTRICA, no como guarda: el ADR 0162 descarta expresamente bloquear
    por porcentaje."""
    from shared_test_runtimes.catalog import get
    from workers.tasks.test_runtime_task import runtime_outcome
    from workers.test_runtime import RuntimePlan, TestRuntimeRunner, TestRuntimeSpec
    from workers.test_runtime import group_tasks_by_runtime as group

    plans = group(
        [
            {"id": "a", "runtime": "php-phpunit", "command": "vendor/bin/phpunit"},
            {
                "id": "b",
                "check_type": "automated",
                "runtime": "php-phpunit",
                "command": "vendor/bin/phpunit --testsuite E2E",
            },
        ]
    )
    assert isinstance(plans[0], RuntimePlan)
    assert plans[0].template == get("php-phpunit")

    client = _fake_client({"phpunit": (0, PHPUNIT_OK)}, default=(0, b"composer: ok\n"))
    runner = TestRuntimeRunner(Settings(), client=client)
    result = runner.launch(TestRuntimeSpec(plan=plans[0], worktree_host_path="/data/wt/t1"))

    assert runtime_outcome(result)["checks_without_declared_check_type"] == 1


# ---------------------------------------------------------------------------
# El reconocedor de epílogos, a solas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # pytest imprime otras líneas de `=` que NO son el resumen; contarlas
        # daría un número inventado, así que sólo cuenta la que trae ` in <t>s`.
        ("=========================== short test summary info ===========================", None),
        ("=================================== FAILURES ==================================", None),
        # Una palabra desconocida hace ABANDONAR el reconocimiento entero: no
        # sabemos si cuenta como test, y ante la duda el estado es AUSENTE.
        ("===== 3 passed, 1 flurbled in 0.1s =====", None),
        # `deselected` y `warning` NO son tests ejecutados.
        ("===== 2 passed, 3 deselected, 1 warning in 0.1s =====", 2),
        ("===== 1 xfailed, 1 xpassed in 0.1s =====", 2),
    ],
)
def test_pytest_epilogue_recogniser_is_conservative(text: str, expected: int | None) -> None:
    from shared_test_runtimes.counts import count_tests

    counts = count_tests(text, runtime="python-pytest", parsers=("junit_xml", "raw_text"))

    if expected is None:
        assert counts is None
    else:
        assert counts is not None
        assert counts.total == expected


def test_two_runners_in_the_same_log_are_ambiguous_so_absent() -> None:
    """Dos epílogos de runners distintos en el mismo log = no sabemos sumar.

    Podría ser un check de PHP y otro de Python en el mismo plan (imposible hoy:
    el plan agrupa por runtime) o un log contaminado. Ante la duda, AUSENTE."""
    from shared_test_runtimes.counts import count_tests

    mixed = PHPUNIT_OK.decode() + "\n" + PYTEST_MIXED.decode()

    assert count_tests(mixed, runtime="php-phpunit", parsers=("junit_xml", "raw_text")) is None


def test_junit_xml_still_wins_when_the_output_is_actually_xml() -> None:
    """El parser estructurado manda sobre el epílogo de texto cuando lo hay.

    Hoy no se recoge ningún ``junit.xml`` (ver 'pendiente'), pero el cableado
    queda hecho y anclado: el día que alguien lo recoja, cuenta el XML."""
    from shared_test_runtimes.counts import count_tests

    xml = (
        '<testsuite name="s" tests="7" failures="2" errors="0" skipped="1">'
        '<testcase classname="A" name="t"/></testsuite>'
    )
    counts = count_tests(xml, runtime="php-phpunit", parsers=("junit_xml", "raw_text"))

    assert counts is not None
    assert counts.source == "junit_xml"
    assert counts.total == 7
    assert counts.failed == 2
    assert counts.skipped == 1


def test_counting_skips_parsers_the_registry_does_not_implement() -> None:
    """``go_test_json`` está en el Literal del esquema y NO en el registro.

    ``parsers.try_parsers`` hace ``PARSERS[parser_id]`` y revienta con KeyError
    para la plantilla ``go-test``. El recuento no puede heredar ese fallo: un
    parser que no existe es un parser que no dice nada, no una excepción que se
    lleve por delante la fase de tests."""
    from shared_test_runtimes.counts import count_tests

    counts = count_tests(
        PHPUNIT_OK.decode(),
        runtime="go-test",
        parsers=("go_test_json", "raw_text"),
    )

    assert counts is not None
    assert counts.total == 14


# ---------------------------------------------------------------------------
# `pytest -q`: el modo canónico de este repo, que el reconocedor no veía
# ---------------------------------------------------------------------------
def test_the_quiet_pytest_summary_is_counted() -> None:
    """Sin banner de `=`, que es como imprime `pytest -q`.

    El reconocedor exigía `^==== … ====$`, y con eso el runtime más usado de la
    plataforma —y el comando exacto que corre la suite de este repositorio— se
    quedaba sin medir. No era un falso fallo (devolvía ausencia, que es el lado
    seguro), pero sí dejaba ciega la medición justo donde más datos hay.

    La línea es la salida REAL de `pytest tests/unit -q` de este repo.
    """
    from shared_test_runtimes.counts import count_tests

    counts = count_tests(
        "5347 passed, 9 skipped, 2 warnings in 231.07s (0:03:51)",
        runtime="python-pytest",
        parsers=("junit_xml", "raw_text"),
    )

    assert counts is not None, "el resumen de `pytest -q` sigue sin reconocerse"
    assert counts.total == 5356  # 5347 pasados + 9 saltados; los warnings no son tests
    assert counts.passed == 5347
    assert counts.skipped == 9


def test_the_quiet_form_still_refuses_to_guess() -> None:
    """Aceptar líneas sueltas no puede abrir la puerta a inventarse un recuento.

    El discriminante que lo impide sigue siendo doble: hace falta el sufijo
    `in <t>s` Y que todos los elementos casen `<n> <palabra>`. Una línea de log
    que cumpla lo primero y no lo segundo tiene que dar AUSENCIA, no un cero.
    """
    from shared_test_runtimes.counts import count_tests

    for ruido in (
        "composer install completed in 3.2s",
        "Nothing to see here",
        "Build finished in 12.5s",
        "downloaded 42 packages in 1.0s",
    ):
        assert (
            count_tests(ruido, runtime="python-pytest", parsers=("junit_xml", "raw_text")) is None
        ), f"se fabricó un recuento a partir de ruido: {ruido!r}"
