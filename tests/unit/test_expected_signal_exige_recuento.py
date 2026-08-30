"""`expected_signal` deja de guardarse sin que nadie lo lea (ADR 0162, opción A).

**La trampa que esto cierra, y no es teoría.** En la base de datos de la
instalación viva hay dos ejecuciones de PHPUnit registradas como CORRECTAS cuyo
log dice ``No tests executed!``. El motivo: ``exit_code == 0`` es el
``expected_signal`` por defecto y **no significa «los tests pasaron»** — puede
significar «no había tests»: un ``--filter`` que no casa con nada, una suite mal
nombrada, un ``phpunit.xml`` que no ve el directorio.

Por qué eso convierte la opción A en un peligro si se firma tal cual: con A, el
mismo agente que escribe el test declara el comando que lo verifica, y un comando
que pasa trivialmente —``true``, o un filtro vacío— es la salida barata. Arriba
está la prueba de que ocurre **sin que nadie lo pretenda**. Por eso el ADR exige
que un criterio ejecutable pueda pedir además que el recuento sea mayor que cero.

**Las dos reglas que este fichero fija, y son las dos mitades del encargo:**

1. La señal se EVALÚA. ``exit_code == 0 and tests > 0`` es falsa cuando se
   ejecutaron cero tests, aunque el proceso saliera con 0.
2. **La ausencia de recuento NUNCA se lee como cero.** Si no se pudo medir, la
   señal queda ``None`` — «no se puede afirmar que fallara»—, jamás ``False``.
   Confundir «no supimos leer la salida» con «no ejecutó ni un test» fabrica un
   falso fallo, y evitar los falsos fallos es la mitad del encargo que manda
   sobre la otra.

**Y la tercera, la que impide que esto se convierta en la opción C:** nada de
esto mueve un veredicto. ``all_passed()`` sigue saliendo sólo del código de
salida, así que los mismos exit codes producen hoy exactamente el mismo resultado
que ayer. La opción C —el gate— **no está firmada**.

Los tests se anclan en :meth:`TestRuntimeRunner.launch`, con la salida del
contenedor entrando por ``exec_run``, que es donde el dato NACE.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from workers.config import Settings

pytestmark = pytest.mark.unit


# --- salidas REALES de los runners -------------------------------------------

PHPUNIT_OK = b"""PHPUnit 10.5.64 by Sebastian Bergmann and contributors.

..............                                                    14 / 14 (100%)

OK (14 tests, 28 assertions)
"""

# La salida LITERAL del §«La trampa que hay que cerrar CON A» del ADR 0162.
PHPUNIT_NO_TESTS = b"""PHPUnit 10.5.64 by Sebastian Bergmann and contributors.

No tests executed!
"""

# Nada que ningún reconocedor entienda: el estado (c), «no se pudo medir».
GIBBERISH = b"something happened, in a format no recogniser knows\n"


def _fake_client(
    outputs: dict[str, tuple[int, bytes]],
    *,
    default: tuple[int, bytes] = (0, b"composer: ok\n"),
) -> MagicMock:
    """Cliente Docker falso cuyo ``exec_run`` contesta según el comando."""

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
        container.exec_run = MagicMock(side_effect=_exec_run)
        return container

    client = MagicMock()
    client.containers.run.side_effect = _run
    network = MagicMock(remove=MagicMock())
    network.name = "test-runtime-php-phpunit-abcd"
    client.networks.create.return_value = network
    return client


def _launch(output: bytes, *, rc: int = 0, expected_signal: str | None = None) -> Any:
    """Un check de PHPUnit que se ejecuta de verdad contra el runner.

    El criterio entra por ``group_tasks_by_runtime`` —la boca real, la que lee
    ``expected_signal`` del diccionario del criterio— y no construyendo un
    ``AcceptanceCheck`` a mano: si la señal se perdiera por el camino, un test
    que la inyecte directamente seguiría en verde."""
    from workers.test_runtime import TestRuntimeRunner, TestRuntimeSpec, group_tasks_by_runtime

    criterio: dict[str, Any] = {
        "id": "auto_01",
        "description": "la suite pasa",
        "runtime": "php-phpunit",
        "command": "vendor/bin/phpunit",
    }
    if expected_signal is not None:
        criterio["expected_signal"] = expected_signal
    plans = group_tasks_by_runtime([criterio])
    client = _fake_client({"phpunit": (rc, output)})
    runner = TestRuntimeRunner(Settings(), client=client)
    return runner.launch(TestRuntimeSpec(plan=plans[0], worktree_host_path="/data/wt/t1"))


# ---------------------------------------------------------------------------
# El evaluador, a solas
# ---------------------------------------------------------------------------


def test_la_senal_por_defecto_solo_mira_el_codigo_de_salida() -> None:
    """NO-REGRESIÓN: `exit_code == 0` es el default de TODO criterio existente.

    Si evaluarla empezara a mirar el recuento, cada criterio ya escrito pasaría
    a exigir algo que nadie declaró — la opción C por la puerta de atrás."""
    from shared_test_runtimes.signals import evaluate_signal

    assert evaluate_signal("exit_code == 0", exit_code=0, counts=None) is True
    assert evaluate_signal("exit_code == 0", exit_code=1, counts=None) is False


def test_la_senal_con_recuento_es_falsa_con_cero_tests() -> None:
    """El caso del ADR: exit 0 y `No tests executed!`."""
    from shared_test_runtimes.counts import TestCounts
    from shared_test_runtimes.signals import evaluate_signal

    cero = TestCounts(total=0, passed=0, failed=0, errored=0, skipped=0, source="phpunit_text")

    assert evaluate_signal("exit_code == 0 and tests > 0", exit_code=0, counts=cero) is False


def test_la_senal_con_recuento_es_cierta_con_tests_de_verdad() -> None:
    from shared_test_runtimes.counts import TestCounts
    from shared_test_runtimes.signals import evaluate_signal

    catorce = TestCounts(total=14, passed=14, failed=0, errored=0, skipped=0, source="phpunit_text")

    assert evaluate_signal("exit_code == 0 and tests > 0", exit_code=0, counts=catorce) is True


def test_sin_recuento_la_senal_no_se_puede_afirmar_ni_negar() -> None:
    """**El test más importante del fichero.**

    Si esto devolviera ``False``, la plataforma diría «este criterio no se
    cumplió» cuando lo único cierto es «no supimos leer la salida». Es la
    confusión (c)→(b) del ADR en su forma más cara: un falso fallo, que es lo que
    el operador puso por delante de todo lo demás."""
    from shared_test_runtimes.signals import evaluate_signal

    assert evaluate_signal("exit_code == 0 and tests > 0", exit_code=0, counts=None) is None


def test_un_codigo_de_salida_distinto_de_cero_no_necesita_recuento() -> None:
    """Con el proceso fallando la señal es falsa sin ambigüedad: no hace falta
    medir nada para saber que no se cumplió, y devolver ``None`` aquí perdería
    una certeza que sí tenemos."""
    from shared_test_runtimes.signals import evaluate_signal

    assert evaluate_signal("exit_code == 0 and tests > 0", exit_code=1, counts=None) is False


def test_una_senal_desconocida_no_se_adivina() -> None:
    """Una señal que no sabemos evaluar queda AUSENTE, no falsa. Inventar un
    veredicto para una expresión que no entendemos es la misma clase de error que
    leer el silencio como una declaración."""
    from shared_test_runtimes.signals import evaluate_signal

    assert evaluate_signal("coverage >= 80%", exit_code=0, counts=None) is None
    assert evaluate_signal("", exit_code=0, counts=None) is None


# ---------------------------------------------------------------------------
# La señal llega al resultado del runner… midiendo, no decidiendo
# ---------------------------------------------------------------------------


def test_el_resultado_reporta_la_senal_de_cada_check() -> None:
    from workers.test_runtime import TestRuntimeResult

    result: TestRuntimeResult = _launch(PHPUNIT_OK, expected_signal="exit_code == 0 and tests > 0")

    assert len(result.check_signals) == 1
    senal = result.check_signals[0]
    assert senal.check_id == "auto_01"
    assert senal.expected_signal == "exit_code == 0 and tests > 0"
    assert senal.exit_code == 0
    assert senal.satisfied is True
    assert senal.test_counts is not None
    assert senal.test_counts.total == 14


def test_cero_tests_con_exit_cero_incumple_la_senal_declarada() -> None:
    """El falso verde de la base de datos viva, por fin visible como tal."""
    result = _launch(PHPUNIT_NO_TESTS, expected_signal="exit_code == 0 and tests > 0")

    assert result.check_signals[0].satisfied is False


def test_una_salida_ilegible_deja_la_senal_sin_afirmar() -> None:
    result = _launch(GIBBERISH, expected_signal="exit_code == 0 and tests > 0")

    assert result.check_signals[0].satisfied is None, "ausencia, jamás falso"
    assert result.check_signals[0].test_counts is None


def test_la_senal_se_mide_con_el_log_del_propio_check() -> None:
    """El recuento del check sale de SU salida, no del log concatenado del plan.

    Importa porque el log del plan lleva también el ``pre_install``: si la señal
    de un check se evaluara sobre el texto de todos, un ``composer install``
    ruidoso o el epílogo de OTRO check podrían contestar por él."""
    result = _launch(PHPUNIT_OK, expected_signal="exit_code == 0 and tests > 0")

    assert result.check_signals[0].test_counts is not None
    assert result.check_signals[0].test_counts.source == "phpunit_text"


# ---------------------------------------------------------------------------
# NADA BLOQUEA — la opción C no está firmada
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("output", "signal"),
    [
        (PHPUNIT_OK, None),
        (PHPUNIT_OK, "exit_code == 0 and tests > 0"),
        (PHPUNIT_NO_TESTS, None),
        (PHPUNIT_NO_TESTS, "exit_code == 0 and tests > 0"),
        (GIBBERISH, None),
        (GIBBERISH, "exit_code == 0 and tests > 0"),
    ],
)
def test_los_mismos_exit_codes_dan_el_mismo_veredicto_que_ayer(
    output: bytes, signal: str | None
) -> None:
    """**El test que fija que esto NO es la opción C.**

    Exit 0 sigue siendo verde, declare el criterio lo que declare y diga el
    recuento lo que diga. La opción A hace VISIBLE el falso verde; cerrarlo es
    otra decisión y otra firma, y el operador puso los falsos fallos por delante
    de todo."""
    assert _launch(output, rc=0, expected_signal=signal).all_passed() is True
    assert _launch(output, rc=1, expected_signal=signal).all_passed() is False


def test_un_evaluador_roto_no_puede_tumbar_una_fase_de_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La evaluación es nueva; el veredicto lleva años funcionando. Un bug en lo
    nuevo pierde la señal —que queda AUSENTE, dicho honestamente— y nada más."""
    import workers.test_runtime as tr

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("el evaluador explotó")

    monkeypatch.setattr(tr, "evaluate_signal", _boom)
    result = _launch(PHPUNIT_OK, expected_signal="exit_code == 0 and tests > 0")

    assert result.all_passed() is True
    assert result.check_signals[0].satisfied is None


# ---------------------------------------------------------------------------
# El outcome que se persiste y llega al informe
# ---------------------------------------------------------------------------


def test_el_outcome_lleva_las_senales_con_sus_tres_estados() -> None:
    from workers.tasks.test_runtime_task import runtime_outcome

    cierta = runtime_outcome(_launch(PHPUNIT_OK, expected_signal="exit_code == 0 and tests > 0"))
    falsa = runtime_outcome(
        _launch(PHPUNIT_NO_TESTS, expected_signal="exit_code == 0 and tests > 0")
    )
    ausente = runtime_outcome(_launch(GIBBERISH, expected_signal="exit_code == 0 and tests > 0"))

    assert cierta["check_signals"][0]["satisfied"] is True
    assert falsa["check_signals"][0]["satisfied"] is False
    assert ausente["check_signals"][0]["satisfied"] is None
    # La clave existe SIEMPRE: quien la consuma no tiene que distinguir además
    # «no vino en el payload» de «no se pudo evaluar».
    assert "check_signals" in ausente


def test_el_outcome_es_json_safe() -> None:
    """Acaba en un JSONB de auditoría: ni un objeto, ni una excepción."""
    import json

    from workers.tasks.test_runtime_task import runtime_outcome

    outcome = runtime_outcome(_launch(PHPUNIT_OK, expected_signal="exit_code == 0 and tests > 0"))

    json.dumps(outcome)  # no lanza


def test_un_fallo_de_infraestructura_no_inventa_senales() -> None:
    """Sin llegar a ejecutar un check no hay señal que reportar. Una lista vacía
    dice «nada medido»; una llena de `false` diría que los criterios no se
    cumplieron, que es acusar al código del tenant de un fallo de la
    plataforma."""
    from workers.tasks.test_runtime_task import infra_failure_outcome

    outcome = infra_failure_outcome(stage="docker_unavailable", detail="sin daemon")

    assert outcome["check_signals"] == []


# ---------------------------------------------------------------------------
# MEDIO 6 — la evaluación POR CHECK, anclada de verdad
# ---------------------------------------------------------------------------
#
# `test_la_senal_se_mide_con_el_log_del_propio_check` (arriba) NO ancla nada:
# lanza UN solo check, así que cambiar `logs=exec_logs` por el log concatenado
# del plan lo deja igual de verde — el único texto que hay es el suyo. Una
# afirmación que no puede fallar no es una comprobación.
#
# Los dos tests de abajo sí caen con esa mutación, porque hacen que el texto de
# OTRO check (y el del `pre_install`) contradiga al del check bajo prueba.


def _launch_varios(
    salidas: list[tuple[str, int, bytes]],
    *,
    expected_signal: str,
    pre_install_output: bytes = b"composer: ok\n",
) -> Any:
    """Un plan con VARIOS checks, cada uno con su propia salida.

    ``salidas`` es ``[(marca_del_comando, exit_code, salida)]``; la marca es lo
    que distingue un comando de otro en el ``exec_run`` del cliente falso.
    ``pre_install_output`` permite que el ``composer install`` mienta a
    propósito: es el otro texto que se colaría si el recuento se hiciera sobre
    el log del plan entero.
    """
    from workers.test_runtime import TestRuntimeRunner, TestRuntimeSpec, group_tasks_by_runtime

    criterios = [
        {
            "id": f"auto_{i:02d}",
            "description": f"criterio {i}",
            "runtime": "php-phpunit",
            "command": f"vendor/bin/phpunit --testsuite {marca}",
            "expected_signal": expected_signal,
        }
        for i, (marca, _rc, _out) in enumerate(salidas)
    ]
    plans = group_tasks_by_runtime(criterios)
    outputs = {f"--testsuite {marca}": (rc, out) for marca, rc, out in salidas}
    client = _fake_client(outputs, default=(0, pre_install_output))
    runner = TestRuntimeRunner(Settings(), client=client)
    return runner.launch(TestRuntimeSpec(plan=plans[0], worktree_host_path="/data/wt/t1"))


def test_el_recuento_de_un_check_no_lo_contesta_el_check_anterior() -> None:
    """El segundo check no entiende su salida: su señal queda AUSENTE.

    Si la evaluación se hiciera sobre el log acumulado del plan, el segundo
    leería el «OK (14 tests, 28 assertions)» del PRIMERO y su señal saldría
    ``True``: la plataforma daría por verificado un criterio con la evidencia de
    otro. Es la misma «respuesta silenciosamente falsa» que el ADR 0162 persigue
    en todas sus formas, y la más cara de detectar."""
    result = _launch_varios(
        [("Unit", 0, PHPUNIT_OK), ("E2E", 0, GIBBERISH)],
        expected_signal="exit_code == 0 and tests > 0",
    )

    primero, segundo = result.check_signals
    assert primero.check_id == "auto_00"
    assert primero.satisfied is True
    assert primero.test_counts is not None
    assert primero.test_counts.total == 14

    assert segundo.check_id == "auto_01"
    assert segundo.test_counts is None, (
        "el segundo check heredó un recuento que no salió de su propia salida: "
        "la señal se está evaluando sobre el log de todo el plan"
    )
    assert segundo.satisfied is None, "ausencia, jamás la evidencia de otro check"


def test_el_recuento_de_un_check_no_lo_contesta_el_pre_install() -> None:
    """La otra mitad de la mutación: `pre_logs + checks.logs`.

    El `composer install` de este proyecto imprime algo que un reconocedor de
    texto lee como un epílogo de PHPUnit. No es rebuscado —los pre_install de
    los proyectos reales imprimen de todo—, y es exactamente por lo que el
    recuento de un check no puede salir de un texto que incluya el suyo."""
    result = _launch_varios(
        [("E2E", 0, GIBBERISH)],
        expected_signal="exit_code == 0 and tests > 0",
        pre_install_output=b"Generating autoload files\nOK (99 tests, 99 assertions)\n",
    )

    (senal,) = result.check_signals
    assert senal.test_counts is None, (
        "el check heredó el recuento del `composer install`: la señal se está "
        "evaluando sobre un texto que no es la salida del check"
    )
    assert senal.satisfied is None
    # Y el recuento del PLAN tampoco: `launch` cuenta sobre `checks.logs`, no
    # sobre `pre_logs + checks.logs`. El comentario que lo dice llevaba escrito
    # desde la ola 1 sin un test que lo sostuviera.
    assert result.test_counts is None, (
        "el recuento del plan salió del `composer install`: la salida de un "
        "instalador de dependencias no es un informe de tests"
    )
