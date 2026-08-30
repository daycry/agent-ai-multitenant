"""La declaración del implementador sale del contenedor y se vuelve criterio.

**Qué estaba a medias, y por qué eso no servía de nada.** La ola 1 del ADR 0162
(opción A) montó el vocabulario, el canal y la medición: un criterio conserva su
forma estructurada, el implementador declara con qué se verifica —por
``submit_result`` en los proveedores HTTP y por bloque ``<checks>`` en
``claude_sdk``— y el silencio se cuenta en el ``steps_log``. Pero la declaración
**se quedaba ahí**: no viajaba en el resultado del run, no llegaba al worker y no
tocaba ``tasks.acceptance_criteria``. O sea, se contaba y no se usaba — «nadie
ejecuta todavía el comando que el agente declaró», dice el propio ADR.

Esta ola cierra el círculo en dos tramos, y los dos se fijan aquí:

1. **Sale del contenedor** por la MISMA vía que el resto del resultado: la línea
   ``execution.finished``. No un canal nuevo — ``approval`` y ``finish_status``
   ya viajan así, y el worker los lee del mismo sitio.
2. **Se persiste como criterio de la tarea**, fusionando en vez de pisar, para
   que el test-runtime lo dispare.

**La disciplina del tramo 2 no es negociable y tiene precedente escrito**:
``api_server.chat.sync_to_kanban._merge_acceptance`` existe porque un replan
convertía en prosa el único dato que hace verificable a una tarea. La regla que
enunció —*una escritura no puede destruir información que la otra mitad no sabe
expresar*— se aplica aquí en su forma más estricta, porque el que escribe ahora
es un LLM y **nada distingue en la columna lo que puso el operador a mano de lo
que puso un run anterior**:

* lo ya escrito NO se pisa: la declaración sólo rellena huecos;
* una declaración que no casa con ningún criterio se DESCARTA — jamás añade un
  criterio fantasma que nadie pidió;
* y un criterio EJECUTABLE no puede volverse no-ejecutable porque el agente lo
  declare manual. Ésa es exactamente la salida barata que el ADR anticipa en su
  §«El riesgo de que se juegue».

**Y nada de esto bloquea.** Persistir es información, no gate: el número de
criterios no cambia, ninguno desaparece y ``all_passed()`` sigue saliendo sólo
del código de salida. La opción C **no está firmada**.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.unit


_CRITERIOS: list[Any] = [
    "El GET a / responde 200 con el saludo",
    "El README explica la puesta en marcha",
]

_DECLARACIONES: list[dict[str, Any]] = [
    {
        "criterion": "El GET a / responde 200 con el saludo",
        "check_type": "automated",
        "runtime": "php-phpunit",
        "command": "vendor/bin/phpunit --filter HomeTest",
        "expected_signal": "exit_code == 0 and tests > 0",
    },
    {
        "criterion": "El README explica la puesta en marcha",
        "check_type": "manual",
        "reason": "es prosa: ningún comando comprueba que un texto se entienda",
    },
]


# ===========================================================================
# TRAMO 1 — la declaración SALE del contenedor
# ===========================================================================
def _finished_line(
    criterios: list[Any], declaraciones: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """El payload ``result`` de la línea ``execution.finished``, de un run REAL.

    Se ancla en ``run_agent`` y se serializa con ``as_dict()`` porque es
    exactamente lo que el entrypoint emite por stdout
    (``__main__``: ``_emit({"event": "execution.finished", "result":
    result.as_dict()})``). Construir el dict a mano aquí dejaría verde un
    ``as_dict()`` que hubiese dejado de incluir la clave — que es justo el
    defecto que este tramo repara.
    """
    from agent_runtime.graph import AgentDeps, run_agent
    from agent_runtime.model import (
        DecisionKind,
        ModelDecision,
        ModelResponse,
        ScriptedModelClient,
    )

    finish = ModelResponse(
        decision=ModelDecision(
            kind=DecisionKind.FINISH,
            output="hecho",
            rationale="finish",
            finish_status="success",
            check_declarations=tuple(declaraciones or ()),
        )
    )
    result = run_agent(
        AgentDeps(model=ScriptedModelClient(decisions=[finish], reviews=[])),
        {
            "id": "t-1",
            "title": "Home",
            "description": "una home",
            "acceptance_criteria": criterios,
        },
    )
    # El viaje real es por stdout: JSON de ida y vuelta, o un tipo no
    # serializable pasaría este test y moriría en el contenedor.
    payload: dict[str, Any] = json.loads(json.dumps(result.as_dict()))
    return payload


def test_la_declaracion_viaja_en_el_resultado_del_run() -> None:
    """El tramo que faltaba: hasta ahora sólo llegaba al ``steps_log``."""
    payload = _finished_line(_CRITERIOS, _DECLARACIONES)

    assert payload["check_declarations"] == _DECLARACIONES


def test_sin_declarar_nada_el_resultado_lleva_una_lista_vacia() -> None:
    """Vacía, no ausente: quien la consuma no tiene que distinguir además «no
    vino en el payload». Y vacía NO es una declaración de nada."""
    payload = _finished_line(_CRITERIOS, None)

    assert payload["check_declarations"] == []


def test_el_worker_lee_la_declaracion_de_la_misma_linea_que_el_resto() -> None:
    """La costura, con los dos lados de verdad.

    El productor es ``ExecutionResult.as_dict()`` (contenedor) y el consumidor
    ``workers.execution._declared_checks_from_result``. Si uno renombrase la
    clave, esto cae — que es de lo que sirve un test de costura."""
    from workers.execution import _declared_checks_from_result

    payload = _finished_line(_CRITERIOS, _DECLARACIONES)

    assert _declared_checks_from_result(payload) == _DECLARACIONES


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"check_declarations": None},
        {"check_declarations": "vendor/bin/phpunit"},
        {"check_declarations": [None, 3, "x"]},
        # Sin `criterion` no se sabe de qué criterio habla; sin `check_type` no
        # declara nada. Ninguna de las dos es una declaración: es ruido.
        {"check_declarations": [{"check_type": "automated", "command": "pytest"}]},
        {"check_declarations": [{"criterion": "algo"}]},
    ],
)
def test_lo_que_no_es_una_declaracion_se_descarta_sin_romper(payload: Any) -> None:
    """Lo que llega aquí lo escribió un LLM dentro de un sandbox. El worker
    revalida en vez de fiarse, y lo mal formado se descarta —el criterio queda NO
    DECLARADO, que es lo honesto— en vez de tumbar un run que ya terminó."""
    from workers.execution import _declared_checks_from_result

    assert _declared_checks_from_result(payload) == []


# ===========================================================================
# TRAMO 2 — la declaración se vuelve criterio de la tarea, FUSIONANDO
# ===========================================================================
def _merge(criterios: list[Any], declaraciones: list[dict[str, Any]]) -> list[Any]:
    from workers.execution import merge_declared_checks

    return merge_declared_checks(criterios, declaraciones)


def test_una_declaracion_convierte_la_prosa_en_un_criterio_ejecutable() -> None:
    """El defecto que el recorrido E2E del 2026-08-29 reprodujo en vivo: los 25
    criterios de un plan real eran STRINGS, así que el test-runtime no se
    disparaba ni una vez — ni siquiera en la tarea de QA."""
    salida = _merge(_CRITERIOS, _DECLARACIONES)

    assert salida[0] == {
        "description": "El GET a / responde 200 con el saludo",
        "check_type": "automated",
        "runtime": "php-phpunit",
        "command": "vendor/bin/phpunit --filter HomeTest",
        "expected_signal": "exit_code == 0 and tests > 0",
    }


def test_la_declaracion_manual_deja_constancia_del_motivo() -> None:
    """«esto no es verificable a máquina, y este es el motivo». Sin el motivo,
    «manual» sería indistinguible del silencio que la opción A retira."""
    salida = _merge(_CRITERIOS, _DECLARACIONES)

    assert salida[1]["check_type"] == "manual"
    assert salida[1]["reason"].startswith("es prosa")
    assert "runtime" not in salida[1] and "command" not in salida[1]


def test_el_numero_de_criterios_no_cambia_nunca() -> None:
    """Fusionar, no reescribir: ni se añade ni se pierde un criterio."""
    assert len(_merge(_CRITERIOS, _DECLARACIONES)) == len(_CRITERIOS)
    assert len(_merge(_CRITERIOS, [])) == len(_CRITERIOS)


def test_una_declaracion_que_no_casa_con_nada_se_descarta() -> None:
    """Y NO añade un criterio fantasma. Un modelo que se inventa un criterio no
    puede fabricar trabajo que nadie pidió — ni hacer desaparecer el silencio
    sobre uno que sí existe."""
    salida = _merge(
        _CRITERIOS,
        [
            {
                "criterion": "un criterio que nadie escribió",
                "check_type": "automated",
                "runtime": "php-phpunit",
                "command": "vendor/bin/phpunit",
            }
        ],
    )

    assert salida == _CRITERIOS


def test_casa_con_tolerancia_de_espacios_y_mayusculas() -> None:
    """Quien reescribe el criterio en la declaración es un modelo copiando de su
    propio prompt: exigir igualdad byte a byte convertiría cada espacio de más en
    un «no declarado» falso."""
    salida = _merge(
        ["  El GET a  /   responde 200 con el saludo "],
        [
            {
                "criterion": "el get a / RESPONDE 200 con el saludo",
                "check_type": "automated",
                "runtime": "php-phpunit",
                "command": "vendor/bin/phpunit",
            }
        ],
    )

    assert salida[0]["command"] == "vendor/bin/phpunit"


def test_casa_tambien_por_id_cuando_el_criterio_es_estructurado() -> None:
    salida = _merge(
        [{"id": "ac_1", "description": "la home responde"}],
        [
            {
                "criterion": "ac_1",
                "check_type": "automated",
                "runtime": "php-phpunit",
                "command": "vendor/bin/phpunit",
            }
        ],
    )

    assert salida[0]["command"] == "vendor/bin/phpunit"
    assert salida[0]["description"] == "la home responde", "la descripción no se toca"


def test_lo_ya_escrito_no_se_pisa() -> None:
    """**La condición que manda sobre las demás.**

    Nada distingue en la columna lo que escribió el operador a mano de lo que
    dejó un run anterior, así que se trata todo lo ya escrito como del operador:
    la declaración RELLENA huecos y no sobrescribe ninguno. El error caro es el
    contrario — un agente reescribiendo en cada run el comando que un humano
    ajustó."""
    del_operador: list[Any] = [
        {
            "description": "El GET a / responde 200 con el saludo",
            "runtime": "php-phpunit",
            "command": "vendor/bin/phpunit --testsuite Feature",
            "expected_signal": "exit_code == 0 and tests > 0",
        }
    ]
    salida = _merge(del_operador, [_DECLARACIONES[0]])

    assert salida[0]["command"] == "vendor/bin/phpunit --testsuite Feature"
    assert salida[0]["runtime"] == "php-phpunit"
    # Lo que SÍ rellena: el hueco que el operador dejó.
    assert salida[0]["check_type"] == "automated"


def test_el_agente_no_puede_apagar_un_criterio_ejecutable_declarandolo_manual() -> None:
    """La salida barata del §«El riesgo de que se juegue», cerrada donde importa.

    ``test_runtime`` salta todo criterio cuyo ``check_type`` no sea
    ``automated``. Si una declaración pudiera escribir ``manual`` sobre un
    criterio que ya trae ``runtime`` y ``command``, el agente desactivaría con
    una frase el test que otro escribió — y el resultado se leería como un
    proyecto que legítimamente no tiene nada que automatizar."""
    del_operador: list[Any] = [
        {
            "description": "La suite pasa",
            "runtime": "php-phpunit",
            "command": "vendor/bin/phpunit",
        }
    ]
    salida = _merge(
        del_operador,
        [
            {
                "criterion": "La suite pasa",
                "check_type": "manual",
                "reason": "prefiero no ejecutarlo",
            }
        ],
    )

    assert salida[0].get("check_type") != "manual"
    assert salida[0]["command"] == "vendor/bin/phpunit"


def test_una_declaracion_manual_no_arrastra_comando_ni_senal() -> None:
    """Si el propio agente dice que no es automatizable, su ``command`` sobra: es
    una declaración contradictoria y se respeta la mitad que decide."""
    salida = _merge(
        ["algo que no se puede comprobar"],
        [
            {
                "criterion": "algo que no se puede comprobar",
                "check_type": "manual",
                "reason": "es un juicio de diseño",
                "runtime": "php-phpunit",
                "command": "true",
                "expected_signal": "exit_code == 0",
            }
        ],
    )

    assert salida[0]["check_type"] == "manual"
    assert "command" not in salida[0]
    assert "runtime" not in salida[0]
    assert "expected_signal" not in salida[0]


def test_sin_declaraciones_la_lista_sale_identica() -> None:
    """El parque de hoy: nadie declara nada. Ni un byte se mueve, así que la
    escritura ni siquiera llega a ocurrir."""
    criterios: list[Any] = [{"description": "x"}, "y"]

    assert _merge(criterios, []) == criterios


def test_una_declaracion_que_no_aporta_nada_deja_la_lista_igual() -> None:
    """Redeclarar lo mismo no es un cambio, y no puede parecerlo: una escritura
    por run sobre una columna JSONB sin novedad es ruido de auditoría."""
    ya_completo: list[Any] = [
        {
            "description": "La suite pasa",
            "check_type": "automated",
            "runtime": "php-phpunit",
            "command": "vendor/bin/phpunit",
        }
    ]
    salida = _merge(ya_completo, [{**ya_completo[0], "criterion": "La suite pasa"}])

    assert salida == ya_completo


# ---------------------------------------------------------------------------
# El circuito, entero: de lo que declaró el agente a lo que ejecuta el runtime
# ---------------------------------------------------------------------------
def test_lo_declarado_llega_hasta_el_check_que_el_test_runtime_ejecutaria() -> None:
    """MEDIO 7: la condición del recuento por fin tiene PRODUCTOR.

    ``exit_code == 0 and tests > 0`` sólo existía en los prompts del agente: no
    había nadie que lo escribiera en un criterio, así que el evaluador de señales
    era código inalcanzable disfrazado de funcionalidad. Con el tramo 2 el
    productor es el propio implementador, y este test recorre el camino entero
    —run del grafo → línea ``execution.finished`` → worker → criterio →
    ``group_tasks_by_runtime``— sin construir a mano ninguno de los eslabones."""
    from workers.execution import _declared_checks_from_result, merge_declared_checks
    from workers.test_runtime import group_tasks_by_runtime

    payload = _finished_line(_CRITERIOS, _DECLARACIONES)
    criterios = merge_declared_checks(_CRITERIOS, _declared_checks_from_result(payload))
    plans = group_tasks_by_runtime([c for c in criterios if isinstance(c, dict)])

    assert len(plans) == 1
    (check,) = plans[0].checks
    assert check.runtime == "php-phpunit"
    assert check.command == "vendor/bin/phpunit --filter HomeTest"
    assert check.expected_signal == "exit_code == 0 and tests > 0"
    assert check.declared_check_type == "automated"


def test_antes_del_tramo_2_ese_mismo_plan_no_ejecutaba_nada() -> None:
    """La foto del defecto, para que se vea qué cambia: los mismos criterios sin
    fusionar no producen ni un plan de tests."""
    from workers.test_runtime import group_tasks_by_runtime

    assert group_tasks_by_runtime([c for c in _CRITERIOS if isinstance(c, dict)]) == ()


# ---------------------------------------------------------------------------
# La escritura en la fila de la tarea
# ---------------------------------------------------------------------------
class _NullTxn:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


class _FakeSession:
    def __init__(self, task: Any) -> None:
        self.task = task
        self.gets = 0

    async def get(self, _model: Any, _pk: Any) -> Any:
        self.gets += 1
        return self.task

    def begin(self) -> _NullTxn:
        return _NullTxn()

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


def _fake_sessionmaker(task: Any) -> tuple[Any, _FakeSession]:
    session = _FakeSession(task)
    return (lambda: session), session


@pytest.mark.asyncio
async def test_la_declaracion_se_escribe_en_la_fila_de_la_tarea() -> None:
    from workers.execution import _persist_declared_checks

    task = SimpleNamespace(acceptance_criteria=list(_CRITERIOS))
    sm, _session = _fake_sessionmaker(task)

    salida = await _persist_declared_checks(
        sm, task_id=uuid4(), declarations=_DECLARACIONES, fallback=list(_CRITERIOS)
    )

    assert task.acceptance_criteria[0]["command"] == "vendor/bin/phpunit --filter HomeTest"
    assert salida == task.acceptance_criteria, "el caller sigue con lo que quedó escrito"


@pytest.mark.asyncio
async def test_se_fusiona_contra_lo_que_hay_en_la_fila_no_contra_la_foto_del_run() -> None:
    """El operador puede haber editado los criterios mientras el run corría.

    Fusionar contra el snapshot que el run se llevó al empezar reintroduciría el
    pisotón por la puerta de atrás: escribiría una lista construida sobre una
    versión de la columna que ya no existe."""
    from workers.execution import _persist_declared_checks

    task = SimpleNamespace(
        acceptance_criteria=[
            "El GET a / responde 200 con el saludo",
            "El README explica la puesta en marcha",
            "Y esto lo añadió el operador durante el run",
        ]
    )
    sm, _session = _fake_sessionmaker(task)

    await _persist_declared_checks(
        sm, task_id=uuid4(), declarations=_DECLARACIONES, fallback=list(_CRITERIOS)
    )

    assert len(task.acceptance_criteria) == 3
    assert task.acceptance_criteria[2] == "Y esto lo añadió el operador durante el run"


@pytest.mark.asyncio
async def test_sin_declaraciones_no_se_toca_la_fila() -> None:
    from workers.execution import _persist_declared_checks

    task = SimpleNamespace(acceptance_criteria=list(_CRITERIOS))
    sm, session = _fake_sessionmaker(task)

    salida = await _persist_declared_checks(
        sm, task_id=uuid4(), declarations=[], fallback=list(_CRITERIOS)
    )

    assert session.gets == 0, "un run que no declara nada no abre ni la transacción"
    assert salida == _CRITERIOS


@pytest.mark.asyncio
async def test_un_fallo_al_persistir_no_rompe_un_run_que_ya_termino() -> None:
    """Best-effort como el resto del post-proceso: el entregable ya está escrito
    en el worktree y la fila de la execution ya está finalizada. Se devuelve la
    foto del run para que la fase de tests siga con lo que tenía."""
    from workers.execution import _persist_declared_checks

    def _explota() -> Any:
        raise RuntimeError("la BD se cayó")

    salida = await _persist_declared_checks(
        _explota, task_id=uuid4(), declarations=_DECLARACIONES, fallback=list(_CRITERIOS)
    )

    assert salida == _CRITERIOS


# ---------------------------------------------------------------------------
# El cableado: quien recoge la declaración es el post-proceso del implementador
# ---------------------------------------------------------------------------
def _prepared() -> SimpleNamespace:
    return SimpleNamespace(
        execution_id=uuid4(),
        worktree_inputs=("tenant-a", "proj-a", str(uuid4()), str(uuid4()), "plan-slug"),
        task_acceptance_criteria=list(_CRITERIOS),
    )


async def _post_process(
    monkeypatch: pytest.MonkeyPatch,
    *,
    declaraciones: list[dict[str, Any]],
    status: str = "done",
) -> tuple[list[str], list[Any]]:
    """Corre `_implementer_post_process` con dobles y devuelve
    ``(secuencia, criterios_con_los_que_se_lanzaron_los_tests)``."""
    from workers import execution as exec_mod
    from workers import orchestration_drain
    from workers.config import Settings

    calls: list[str] = []
    lanzados: list[Any] = []
    task = SimpleNamespace(acceptance_criteria=list(_CRITERIOS))

    async def spy_drain(*_a: Any, **_k: Any) -> None:
        calls.append("drain_comments")

    async def spy_commit(*_a: Any, **_k: Any) -> None:
        calls.append("commit_and_push")

    async def spy_tests(*_a: Any, **kwargs: Any) -> None:
        calls.append("run_test_runtime")
        lanzados.extend(kwargs["acceptance_criteria"])

    monkeypatch.setattr(orchestration_drain, "drain_task_comment_effects", spy_drain)
    monkeypatch.setattr(exec_mod, "_commit_and_push_worktree", spy_commit)
    monkeypatch.setattr(exec_mod, "_run_task_tests", spy_tests)

    sm, _session = _fake_sessionmaker(task)
    original = exec_mod._persist_declared_checks

    async def spy_persist(*args: Any, **kwargs: Any) -> Any:
        calls.append("persist_declared_checks")
        return await original(*args, **kwargs)

    monkeypatch.setattr(exec_mod, "_persist_declared_checks", spy_persist)

    await exec_mod._implementer_post_process(
        Settings(),
        sm,
        prepared=_prepared(),
        workspace=SimpleNamespace(host_path="/data/wt/t", read_only=False, error=None),
        result=SimpleNamespace(
            status=status, abort_code=None, output="hecho", iterations=1, steps=[], usage={}
        ),
        task_id=uuid4(),
        tenant_id=uuid4(),
        exec_id=str(uuid4()),
        check_declarations=declaraciones,
    )
    return calls, lanzados


@pytest.mark.asyncio
async def test_el_post_proceso_persiste_la_declaracion_antes_de_lanzar_los_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El orden importa: si se persistiera después, la fase de tests de ESTE run
    seguiría lanzándose con los criterios de prosa que el agente acaba de
    sustituir, y el circuito sólo se cerraría un run más tarde."""
    calls, lanzados = await _post_process(monkeypatch, declaraciones=_DECLARACIONES)

    assert calls.index("persist_declared_checks") < calls.index("run_test_runtime")
    ejecutables = [c for c in lanzados if isinstance(c, dict) and c.get("command")]
    assert [c["command"] for c in ejecutables] == ["vendor/bin/phpunit --filter HomeTest"], (
        "los tests se lanzaron con la foto del run, no con lo que quedó escrito: "
        f"la declaración no llega a ejecutarse (recibido: {lanzados})"
    )


@pytest.mark.asyncio
async def test_un_run_sin_declaraciones_lanza_exactamente_lo_de_siempre(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NO-REGRESIÓN del parque actual: nadie declara nada todavía."""
    _calls, lanzados = await _post_process(monkeypatch, declaraciones=[])

    assert lanzados == _CRITERIOS


@pytest.mark.asyncio
async def test_un_run_escalado_tambien_deja_escrita_su_declaracion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`needs_human_review` no testea —eso no cambia— pero sí declara: lo que el
    agente averiguó sobre cómo se verifica la tarea vale para quien la retome."""
    calls, _lanzados = await _post_process(
        monkeypatch, declaraciones=_DECLARACIONES, status="needs_human_review"
    )

    assert "persist_declared_checks" in calls
    assert "run_test_runtime" not in calls


# ---------------------------------------------------------------------------
# La costura de verdad: del stdout del contenedor al worker
# ---------------------------------------------------------------------------
#
# Los tests de arriba fijan los dos EXTREMOS —lo que emite `as_dict()` y lo que
# entiende `_declared_checks_from_result`— pero entre ambos queda la línea que de
# verdad recoge el dato: la que `_launch_and_stream` devuelve al final. Sin este
# test, sustituir esa expresión por `[]` dejaría toda la suite en verde y la
# declaración volvería a no llegar a ninguna parte. Es exactamente el modo de
# fallo que esta rama ya ha visto tres veces.
class _FakeRunner:
    """Un contenedor que escupe las líneas dadas por stdout y termina en 0."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def run_streamed(self, _spec: Any, on_line: Any, timeout: float) -> Any:
        from workers.container import ContainerResult

        for line in self._lines:
            on_line(line)
        return ContainerResult(
            container_id="c-0",
            exit_code=0,
            logs="\n".join(self._lines),
            timed_out=False,
            host_config={},
            config_env=(),
            networks=(),
        )

    def kill_by_label(self, _label: str) -> None:  # pragma: no cover - no se cancela
        return None


async def _launch(monkeypatch: pytest.MonkeyPatch, lines: list[str]) -> tuple[Any, Any, Any]:
    """Corre `_launch_and_stream` de verdad, con el docker/redis/BD sustituidos."""
    from workers import execution as exec_mod
    from workers.config import Settings
    from workers.run_contract import ExecutionRequest

    async def _no_publish(*_a: Any, **_k: Any) -> None:
        return None

    async def _no_execution(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(exec_mod, "publish_execution_event", _no_publish)
    monkeypatch.setattr(exec_mod, "get_execution", _no_execution)
    monkeypatch.setattr(exec_mod, "_build_runtime_env", lambda *_a, **_k: {})
    monkeypatch.setattr(exec_mod, "_stage_model_credentials", lambda spec, **_k: (spec, None))

    sm, _session = _fake_sessionmaker(SimpleNamespace(acceptance_criteria=[]))
    return await exec_mod._launch_and_stream(
        ExecutionRequest(
            tenant_id=str(uuid4()),
            task_id=str(uuid4()),
            agent_id=None,
            task={"id": "t-1", "title": "Home", "description": "una home"},
            model={"kind": "scripted"},
        ),
        settings=Settings(),
        sessionmaker=sm,
        redis=object(),
        prepared=SimpleNamespace(
            execution_id=uuid4(),
            resolved_model={"kind": "scripted"},
            task_acceptance_criteria=list(_CRITERIOS),
            approval_policy=None,
            guardrails=None,
            approved_actions=[],
        ),
        workspace=SimpleNamespace(host_path=None, read_only=False, code_diff=None),
        exec_id=str(uuid4()),
        runner=_FakeRunner(lines),
        cancel_poll_interval_s=3600.0,
    )


@pytest.mark.asyncio
async def test_la_declaracion_llega_al_worker_desde_el_stdout_del_contenedor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El camino completo, sin atajos: la línea que emite el entrypoint entra por
    el stdout del contenedor y sale por el valor de retorno del worker."""
    payload = _finished_line(_CRITERIOS, _DECLARACIONES)
    linea = json.dumps({"event": "execution.finished", "result": payload})

    result, _approval, declaraciones = await _launch(monkeypatch, [linea])

    assert result.status == "done"
    assert declaraciones == _DECLARACIONES


@pytest.mark.asyncio
async def test_un_contenedor_que_no_declara_nada_devuelve_lista_vacia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _finished_line(_CRITERIOS, None)
    linea = json.dumps({"event": "execution.finished", "result": payload})

    _result, _approval, declaraciones = await _launch(monkeypatch, [linea])

    assert declaraciones == []


@pytest.mark.asyncio
async def test_un_contenedor_que_muere_sin_resultado_no_inventa_declaraciones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ausencia, no silencio interpretado: sin línea terminal no hay nada que
    declarar, y desde luego no un «no hay nada que verificar»."""
    _result, _approval, declaraciones = await _launch(monkeypatch, [])

    assert declaraciones == []


def test_un_run_que_no_llego_a_cerrar_no_reporta_declaraciones() -> None:
    """La declaración es de un CIERRE, no de un run cualquiera.

    El guardrail `post_llm` y el gate de aprobación reescriben la decisión
    conservando el resto de sus campos, así que una declaración puede quedarse
    pegada a un `last_decision` que ya no es un FINISH. Reportarla ahí le
    atribuiría al run una verificación que nadie llegó a hacer — el mismo error
    de leer un dato fuera del estado que lo produjo.
    """
    from agent_runtime.graph import AgentDeps, run_agent
    from agent_runtime.model import (
        DecisionKind,
        ModelDecision,
        ModelResponse,
        ScriptedModelClient,
    )
    from agent_runtime.safeguards import Budgets

    # Un ACT que arrastra la declaración de un FINISH reescrito, y un presupuesto
    # que hace que el run muera sin volver a cerrar.
    act = ModelResponse(
        decision=ModelDecision(
            kind=DecisionKind.ACT,
            tool="noop",
            tool_args={"reason": "tu respuesta anterior la bloqueó un guardrail"},
            check_declarations=tuple(_DECLARACIONES),
        )
    )
    result = run_agent(
        AgentDeps(model=ScriptedModelClient(decisions=[act], reviews=[])),
        {
            "id": "t-1",
            "title": "Home",
            "description": "una home",
            "acceptance_criteria": _CRITERIOS,
        },
        budgets=Budgets(max_iterations=1),
    )

    assert result.status != "done"
    assert result.as_dict()["check_declarations"] == []


# ---------------------------------------------------------------------------
# Las guardas se deciden sobre el check_type EFECTIVO, no sobre el que se escribe
# ---------------------------------------------------------------------------
def test_una_declaracion_manual_no_desarma_un_criterio_que_ya_era_automatico() -> None:
    """El agujero que anulaba las dos guardas del tramo 2.

    `_accepted_fields` descarta todo campo que el criterio YA trae, así que un
    criterio con `check_type` propio nunca lo metía en `fields` — y la rama se
    decidía leyendo `fields`, que contestaba «automated» pasara lo que pasara.

    Consecuencia medida: criterio ya `automated` + declaración `manual` con
    `command: "true"` acababa ejecutando `true`, saliendo con 0, y llegándole al
    reviewer como PASSED. Un verde por un criterio cuya propia declaración decía
    que ninguna máquina lo comprueba.
    """
    from workers.execution import _accepted_fields

    base = {"description": "X", "check_type": "automated"}
    declaracion = {
        "criterion": "X",
        "check_type": "manual",
        "reason": "no se puede comprobar",
        "runtime": "php-phpunit",
        "command": "true",
    }

    campos = _accepted_fields(base, declaracion)

    assert "command" not in campos, (
        "la declaración coló un comando sobre un criterio que ya era ejecutable: "
        f"{campos!r}. `test_runtime` lo despacharía y `true` saldría verde"
    )
    assert "runtime" not in campos
    assert "check_type" not in campos, (
        "la declaración desarmó un criterio ejecutable que no escribió este run"
    )


def test_una_declaracion_automatica_no_arma_un_criterio_declarado_manual() -> None:
    """El caso espejo: menos grave, pero igual de incoherente.

    Un criterio que alguien declaró `manual` —con su motivo— no puede ganar
    `runtime` y `command` por una declaración posterior y quedarse `manual`: eso
    deja una fila que dice «esto no lo comprueba ninguna máquina» con una máquina
    apuntada al lado.
    """
    from workers.execution import _accepted_fields

    base = {"description": "Y", "check_type": "manual", "reason": "es un juicio"}
    campos = _accepted_fields(
        base,
        {
            "criterion": "Y",
            "check_type": "automated",
            "runtime": "php-phpunit",
            "command": "vendor/bin/phpunit",
        },
    )

    assert "command" not in campos and "runtime" not in campos, (
        f"un criterio declarado manual ganó un comando: {campos!r}"
    )


# ---------------------------------------------------------------------------
# La costura FINAL: la línea que une los dos extremos ya anclados
# ---------------------------------------------------------------------------
def test_conduct_execution_pasa_las_declaraciones_al_post_proceso() -> None:
    """El eslabón que nadie medía, y que deja el circuito abierto sin avisar.

    Los dos extremos SÍ estaban anclados: `_launch_and_stream` devuelve las
    declaraciones (con su test de contenedor falso) y `_implementer_post_process`
    las persiste (con el suyo). Pero la línea que los une vive en
    `conduct_execution`, y ningún test la ejercitaba: sustituirla por
    `check_declarations=[]` dejaba la suite ENTERA en verde con el circuito roto
    en producción — comprobado.

    Es el mismo modo de fallo que esta rama ya ha visto cuatro veces: un test
    anclado en las piezas y no en la costura. Se fija leyendo el AST, que es lo
    único que distingue «pasa la variable» de «pasa una lista vacía» sin montar
    medio worker.
    """
    import ast
    from pathlib import Path

    ruta = (
        Path(__file__).resolve().parents[2]
        / "apps"
        / "workers"
        / "src"
        / "workers"
        / "execution.py"
    )
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))

    fn = next(
        n
        for n in ast.walk(arbol)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "conduct_execution"
    )

    # (a) el resultado del lanzamiento se desempaqueta en tres, y el tercero es
    #     la variable de declaraciones — no se descarta con un `_`.
    destinos = [
        ast.unparse(t)
        for n in ast.walk(fn)
        if isinstance(n, ast.Assign)
        for t in n.targets
        if "_launch_and_stream" in ast.unparse(n.value)
    ]
    assert destinos, "conduct_execution ya no llama a _launch_and_stream"
    assert any("check_declarations" in d for d in destinos), (
        f"el retorno de _launch_and_stream se desempaqueta en {destinos!r} y las "
        f"declaraciones se pierden ahí mismo"
    )

    # (b) y esa MISMA variable llega al post-proceso. Un literal vacío aquí abre
    #     el circuito sin que nada se ponga rojo.
    argumento = next(
        (
            kw.value
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            for kw in n.keywords
            if kw.arg == "check_declarations"
        ),
        None,
    )
    assert argumento is not None, (
        "conduct_execution ya no pasa `check_declarations` al post-proceso: lo "
        "declarado por el implementador no llegará a la tarea"
    )
    assert isinstance(argumento, ast.Name) and argumento.id == "check_declarations", (
        f"se pasa {ast.unparse(argumento)!r} en vez de la variable que trajo el "
        f"lanzamiento: el circuito queda abierto y la suite no se entera"
    )
