"""Quien acaba de escribir el test declara con qué se verifica (ADR 0162, opción A).

**La reformulación, que es lo que se implementa aquí.** La versión original de la
opción A —«que el planner genere el comando»— la corrige el propio ADR, y por una
razón estructural: **el planner planifica antes de que el código exista**.
Pedirle el ``command`` es pedirle que prediga un nombre de fichero, y un modelo al
que se le pide algo que no puede comprobar escribe algo *plausible*. El fallo
resultante es peor que no tener comando: un ``--filter LoginTest`` inventado que
falla se lee como «el código está roto», no como «el criterio era ficticio».

Así que A no produce un comando: produce una **DECISIÓN**, y por cada criterio una
de dos —el silencio deja de ser una respuesta válida—:

    «esto se verifica ejecutando X»  —o—  «esto no es verificable a máquina, y
    este es el motivo»

Y la toma quien lo sabe: el implementador, al cerrar, en ``submit_result``
(ADR 0087). Eso invierte la naturaleza de la tarea — de **predecir** un nombre de
fichero a **reportar** lo que acaba de correr.

**Los dos proveedores no son simétricos, y el fichero lo trata como lo que es.**
En los HTTP (azure / copilot / ollama) ``submit_result`` es una tool y la
declaración llega ya estructurada. En ``claude_sdk`` no hay tool —un tool call
forzaría ``content=""`` y perdería la prosa del entregable—, así que el FINISH es
prosa y su estado estructurado se recupera del tag ``<finish status="…"/>``
(F1.5). La declaración viaja por el mismo camino ya probado: un bloque
``<checks>`` con JSON, parseado con tolerancia y **despojado** del entregable.

**El silencio se CUENTA, no bloquea.** Un criterio sin declaración es NO
DECLARADO —ni automático ni manual— y aparece en el ``steps_log`` con el mismo
vocabulario que ya usa el worker un piso más abajo:
``checks_without_declared_check_type``. Va como métrica a propósito: el ADR
descarta expresamente bloquear por porcentaje —se aprende a jugar enseguida y
castiga a los proyectos que legítimamente tienen poco que automatizar—. La
diferencia con el estado anterior no es que se impida algo: es que **antes ni
siquiera se podía contar**.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

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


def _resp(*, tool_calls: list[Any] | None = None, content: str = "") -> Any:
    return SimpleNamespace(
        tool_calls=tool_calls or [],
        content=content,
        model="m",
        usage=SimpleNamespace(input_tokens=1, output_tokens=2, cost_usd=0.0),
    )


def _call(name: str, **args: Any) -> Any:
    return SimpleNamespace(name=name, arguments=args)


# ---------------------------------------------------------------------------
# El canal estructurado: los proveedores HTTP
# ---------------------------------------------------------------------------


def test_la_tool_submit_result_ofrece_declarar_la_verificacion() -> None:
    """Si el esquema no lo pide, el modelo no lo manda: es la primera condición.

    Y NO puede ser obligatorio en el esquema: un ``submit_result`` sin
    declaraciones tiene que seguir cerrando la tarea igual que ayer, o esto
    dejaría de ser una métrica y sería un gate — la opción C, sin firmar."""
    from agent_runtime.providers import _SUBMIT_RESULT_TOOL

    props = _SUBMIT_RESULT_TOOL["function"]["parameters"]["properties"]
    assert "acceptance_checks" in props, (
        "la tool con la que el agente cierra no le ofrece declarar cómo se "
        "verifica cada criterio: la opción A no tiene por dónde entrar"
    )
    item = props["acceptance_checks"]["items"]["properties"]
    # Las dos mitades de la DECISIÓN tienen que caber las dos.
    assert {"criterion", "check_type", "command", "reason"} <= set(item)
    assert _SUBMIT_RESULT_TOOL["function"]["parameters"]["required"] == ["status", "summary"]


def test_la_declaracion_estructurada_llega_a_la_decision() -> None:
    from agent_runtime.providers import _decision_from

    resp = _resp(
        tool_calls=[
            _call(
                "submit_result",
                status="success",
                summary="Hecho.",
                acceptance_checks=_DECLARACIONES,
            )
        ]
    )
    decision = _decision_from(resp, model="m").decision

    assert decision.output == "Hecho."
    assert decision.finish_status == "success"
    assert len(decision.check_declarations) == 2
    automatico, manual = decision.check_declarations
    assert automatico["command"] == "vendor/bin/phpunit --filter HomeTest"
    assert automatico["expected_signal"] == "exit_code == 0 and tests > 0"
    assert manual["check_type"] == "manual"
    assert manual["reason"].startswith("es prosa")


def test_un_submit_result_sin_declaraciones_sigue_cerrando_igual() -> None:
    """NO-REGRESIÓN, y no es menor: hoy NINGÚN modelo declara nada. Si la
    ausencia rompiera el FINISH, la opción A tumbaría el 100 % de los runs el día
    que se despliegue."""
    from agent_runtime.model import DecisionKind
    from agent_runtime.providers import _decision_from

    resp = _resp(tool_calls=[_call("submit_result", status="success", summary="Hecho.")])
    decision = _decision_from(resp, model="m").decision

    assert decision.kind == DecisionKind.FINISH
    assert decision.output == "Hecho."
    assert decision.check_declarations == ()


def test_una_declaracion_basura_no_tumba_el_finish() -> None:
    """Lo que el modelo mande es texto no fiable. Una declaración mal formada se
    descarta —queda como NO DECLARADO, que es lo honesto— y el entregable, que ya
    está escrito en el worktree, se cierra igual."""
    from agent_runtime.model import DecisionKind
    from agent_runtime.providers import _decision_from

    resp = _resp(
        tool_calls=[
            _call(
                "submit_result",
                status="success",
                summary="Hecho.",
                acceptance_checks="esto no es una lista",
            )
        ]
    )
    decision = _decision_from(resp, model="m").decision

    assert decision.kind == DecisionKind.FINISH
    assert decision.check_declarations == ()


def test_una_declaracion_sin_criterio_ni_tipo_se_descarta() -> None:
    """El conjunto de claves es CERRADO y hay dos obligatorias: sin saber DE QUÉ
    criterio habla ni QUÉ declara, la entrada no dice nada — y una entrada que no
    dice nada contada como declaración volvería a convertir el silencio en una
    respuesta válida."""
    from agent_runtime.providers import _decision_from

    resp = _resp(
        tool_calls=[
            _call(
                "submit_result",
                status="success",
                summary="Hecho.",
                acceptance_checks=[
                    {"command": "pytest"},
                    {"criterion": "algo"},
                    {"criterion": "otra cosa", "check_type": "automated", "ruido": "x"},
                ],
            )
        ]
    )
    decision = _decision_from(resp, model="m").decision

    assert len(decision.check_declarations) == 1
    assert decision.check_declarations[0]["criterion"] == "otra cosa"
    assert "ruido" not in decision.check_declarations[0]


# ---------------------------------------------------------------------------
# El canal de prosa: claude_sdk
# ---------------------------------------------------------------------------


def test_claude_sdk_declara_por_el_bloque_de_prosa() -> None:
    """El proveedor que corre en la instalación viva no recibe tools de FINISH.

    Si la declaración sólo existiera por el canal estructurado, la opción A
    quedaría implementada para los proveedores que NO se están usando."""
    from agent_runtime.providers import _decision_from

    prosa = (
        "He implementado el controlador y su test.\n"
        f"<checks>{json.dumps(_DECLARACIONES)}</checks>\n"
        '<finish status="success"/>'
    )
    decision = _decision_from(_resp(content=prosa), model="m").decision

    assert decision.finish_status == "success"
    assert len(decision.check_declarations) == 2
    assert decision.check_declarations[0]["runtime"] == "php-phpunit"


def test_el_bloque_de_declaracion_no_ensucia_el_entregable() -> None:
    """El ``output`` del FINISH ES el entregable de la tarea. Un blob de JSON
    pegado al final lo contamina, y lo contamina igual si no supimos parsearlo —
    por eso se despoja en los dos casos, como ya hace el tag ``<finish/>``."""
    from agent_runtime.providers import _decision_from

    bueno = _decision_from(
        _resp(content=f"Resumen.\n<checks>{json.dumps(_DECLARACIONES)}</checks>"), model="m"
    ).decision
    roto = _decision_from(
        _resp(content="Resumen.\n<checks>{esto no es JSON}</checks>"), model="m"
    ).decision

    assert bueno.output == "Resumen."
    assert roto.output == "Resumen."
    assert roto.check_declarations == ()


def test_una_prosa_sin_bloque_se_comporta_exactamente_como_antes() -> None:
    from agent_runtime.providers import _decision_from

    decision = _decision_from(_resp(content="Terminé la tarea."), model="m").decision

    assert decision.output == "Terminé la tarea."
    assert decision.check_declarations == ()


# ---------------------------------------------------------------------------
# El silencio se cuenta — y queda escrito donde se puede auditar
# ---------------------------------------------------------------------------


def _run(criterios: list[Any], declaraciones: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Un run completo del grafo que termina declarando (o sin declarar).

    Anclado en ``run_agent`` y leyendo el ``steps_log`` que se persiste verbatim
    en ``executions.steps_log``: es el único canal por el que esta información
    sale hoy del contenedor efímero, así que medir en otro sitio sería medir algo
    que nadie llega a ver."""
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
    return [s for s in result.steps if "checks_without_declared_check_type" in s]


def test_el_paso_de_declaracion_queda_en_el_steps_log() -> None:
    pasos = _run(_CRITERIOS, _DECLARACIONES)

    assert len(pasos) == 1
    paso = pasos[0]
    assert paso["criteria_total"] == 2
    assert paso["checks_without_declared_check_type"] == 0
    assert len(paso["check_declarations"]) == 2


def test_un_criterio_que_nadie_declaro_se_cuenta_como_no_declarado() -> None:
    """El corazón de la opción A: el silencio deja de ser una respuesta válida.

    No pasa a bloquear nada —el run termina exactamente igual—, pero por primera
    vez la plataforma puede contestar «¿cuántos criterios nadie dijo cómo se
    verifican?» con un número en vez de con un encogimiento de hombros."""
    pasos = _run(_CRITERIOS, [_DECLARACIONES[0]])

    assert pasos[0]["criteria_total"] == 2
    assert pasos[0]["checks_without_declared_check_type"] == 1


def test_sin_declarar_nada_todos_los_criterios_son_no_declarados() -> None:
    """El estado de HOY, medido. Los 25 criterios del plan real del 2026-08-29
    caen aquí enteros."""
    pasos = _run(_CRITERIOS, None)

    assert pasos[0]["checks_without_declared_check_type"] == 2


def test_una_tarea_sin_criterios_no_deja_paso_de_declaracion() -> None:
    """Sin criterios no hay nada que declarar, y un paso que dijera «0 de 0» sería
    ruido en el timeline de cada run."""
    assert _run([], None) == []


def test_declarar_no_cambia_el_desenlace_del_run() -> None:
    """**El test que fija que esto NO es la opción C.** Declarar, declarar a
    medias o no declarar nada producen el mismo run: la métrica no toca el
    veredicto."""
    from agent_runtime.graph import AgentDeps, run_agent
    from agent_runtime.model import (
        DecisionKind,
        ModelDecision,
        ModelResponse,
        ScriptedModelClient,
    )
    from agent_runtime.state import STATUS_DONE

    for declaraciones in (None, [_DECLARACIONES[0]], _DECLARACIONES):
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
                "acceptance_criteria": _CRITERIOS,
            },
        )
        assert result.status == STATUS_DONE
        assert result.output == "hecho"


# ---------------------------------------------------------------------------
# El contrato que el modelo lee
# ---------------------------------------------------------------------------


def test_el_prompt_le_pide_al_agente_que_declare_por_los_dos_caminos() -> None:
    """Un esquema que nadie menciona en el prompt se rellena poco y mal, y el
    camino de prosa NO tiene esquema: si el ``<checks>`` no está instruido,
    claude_sdk —el proveedor de la instalación viva— no lo emitirá jamás."""
    from agent_runtime.providers import _DECIDE_SYSTEM

    assert "acceptance_checks" in _DECIDE_SYSTEM
    assert "<checks>" in _DECIDE_SYSTEM
    # Las dos mitades de la decisión, y que el silencio no vale.
    assert "check_type" in _DECIDE_SYSTEM
    assert "manual" in _DECIDE_SYSTEM
