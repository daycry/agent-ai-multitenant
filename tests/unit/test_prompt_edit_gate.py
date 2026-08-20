"""Las piezas PURAS del gate de edición de prompts (`task_gov_05`).

Lo que se puede comprobar sin base de datos ni LLM: el vocabulario de estados,
qué presets bloquean, cómo se nombra un escenario y qué dice el mensaje de
rechazo. La mitad con base de datos —bloquea en `production`, avisa en
`development`, la válvula— vive en
``tests/integration/test_prompt_edit_eval_gate.py``.

Dos de estas guardas existen porque su ausencia no produciría ningún error
visible, sólo un sistema equivocado en silencio:

* la que ata :class:`PromptGateOutcome` a
  :class:`~api_server.evals.ci_run.GateOutcome`: si un día divergen, las dos
  mitades del gate (CI y API) dejan de poder leerse en el mismo informe y nadie
  se entera hasta que alguien compara dos tableros a mano;
* la de ``apply_partial_update(..., exclude=...)``: una instancia declarativa de
  SQLAlchemy acepta atributos arbitrarios, así que olvidar el `exclude` NO
  rompe nada — deja un atributo fantasma sobre la fila y se descubre meses
  después.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from api_server.evals.ci_run import GateOutcome
from api_server.evals.prompt_edit_gate import (
    MAX_NAMED_SCENARIOS,
    OVERRIDE_MIN_REASON_CHARS,
    GateScope,
    PromptGateOutcome,
    rejection_message,
    scenario_label,
)
from api_server.seeds.builtin_approval_policies import BUILTIN_POLICIES, STRICT_PRESETS

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# El vocabulario
# ---------------------------------------------------------------------------
def test_the_three_shared_outcomes_say_exactly_what_the_ci_gate_says() -> None:
    """Mismo texto, misma semántica — las dos mitades del gate son comparables.

    `task_gov_04` cerró la mitad de CI y ésta es la del API. Si los valores
    divergen («blocked» aquí, «block» allí) un informe que junte las dos tiene
    que traducir, y quien lo lea no sabrá si son el mismo estado.
    """
    for shared in (GateOutcome.PASSED, GateOutcome.BLOCKED, GateOutcome.INCONCLUSIVE):
        assert PromptGateOutcome(shared.value).value == shared.value

    # Y el cuarto NO existe en CI, a propósito: allí el invocante nombra el
    # dataset, así que «no hay nada que gatear» no es un estado posible.
    assert "not_gated" not in {o.value for o in GateOutcome}
    assert PromptGateOutcome.NOT_GATED.value == "not_gated"


def test_only_the_two_strict_presets_reject_the_write() -> None:
    """El contrato del operador: bloquea en production/customer-external.

    Se recorren los presets REALES del seed, no una lista escrita aquí: si
    mañana nace un quinto preset, este test obliga a decidir de qué lado cae en
    vez de dejarlo caer en «avisa» por omisión.
    """
    blocking = {p.slug for p in BUILTIN_POLICIES if GateScope(p.slug, None, "test").blocking}
    assert blocking == {"production", "customer-external"} == set(STRICT_PRESETS)
    assert {p.slug for p in BUILTIN_POLICIES} - blocking == {"development", "sandbox"}


# ---------------------------------------------------------------------------
# El nombre de un escenario
# ---------------------------------------------------------------------------
def test_the_scenario_takes_the_title_the_promotion_wrote() -> None:
    """`title` es lo que escribe `_build_input` al promocionar una task real."""
    assert scenario_label({"title": "Alta de usuario con SSO"}, uuid4()) == (
        "Alta de usuario con SSO"
    )


def test_a_scenario_without_a_title_falls_back_before_giving_up() -> None:
    item = uuid4()
    assert scenario_label({"name": "caso raro"}, item) == "caso raro"
    assert scenario_label({"prompt": "resume  esto\n  ya"}, item) == "resume esto ya"
    # Ni título ni nada: el id corto, que ES investigable. Devolver "" haría un
    # mensaje del tipo «empeoran: «», «»», que no se puede accionar.
    fallback = scenario_label({"otra_cosa": 3}, item)
    assert str(item)[:8] in fallback


def test_a_very_long_title_is_cut_instead_of_flooding_the_message() -> None:
    label = scenario_label({"title": "x" * 500}, uuid4())
    assert len(label) < 100
    assert label.endswith("…")


# ---------------------------------------------------------------------------
# El mensaje: el punto (1) del enunciado
# ---------------------------------------------------------------------------
def test_the_rejection_names_the_scenarios_it_does_not_say_the_eval_failed() -> None:
    """«El mensaje dice QUÉ empeoró», que es lo que lo hace accionable.

    Un rechazo mudo se salta desactivando la feature, y entonces la feature no
    existe. Por eso los nombres van en el TEXTO y no sólo en un campo aparte
    que un cliente puede no pintar.
    """
    text = rejection_message(
        ["Alta de usuario con SSO", "Rechazo de tarjeta caducada"],
        preset="production",
        drop=Decimal("0.25"),
    )
    assert "Alta de usuario con SSO" in text
    assert "Rechazo de tarjeta caducada" in text
    assert "production" in text
    assert "0.25" in text


def test_the_rejection_says_the_valve_does_not_apply_to_a_measured_regression() -> None:
    """Si no lo dijera, el siguiente paso del usuario sería intentar el override.

    Y al no funcionarle, el paso siguiente sería bajar el preset del proyecto —
    que es el agujero grande, permanente y sin auditar que la válvula existe
    para evitar.
    """
    text = rejection_message(["algo"], preset="customer-external", drop=None)
    assert "válvula" in text.lower()
    assert "NO aplica" in text


def test_a_long_list_of_scenarios_is_summarised_not_dumped() -> None:
    scenarios = [f"escenario {i}" for i in range(MAX_NAMED_SCENARIOS + 4)]
    text = rejection_message(scenarios, preset="production", drop=None)
    assert "escenario 0" in text
    assert f"escenario {MAX_NAMED_SCENARIOS + 3}" not in text
    assert "y 4 más" in text


def test_a_regression_with_no_named_items_still_says_something_honest() -> None:
    """Puede pasar: los items sólo cambian de tasa sin que ninguno cambie de veredicto.

    Escribir «empeoran: » sería peor que decirlo.
    """
    text = rejection_message([], preset="production", drop=Decimal("0.1"))
    assert "(ninguno con nombre)" in text


# ---------------------------------------------------------------------------
# La válvula: el listón del motivo
# ---------------------------------------------------------------------------
def test_the_override_reason_bar_is_the_one_claude_md_uses_for_gate_override() -> None:
    """80 caracteres, el mismo listón que el `gate_override` del roadmap.

    No es un número al azar: el problema es idéntico —una excepción sin
    justificación auditable es la forma barata de saltarse la regla— y
    `CLAUDE.md` §«La excepción al gate» ya lo fijó ahí.
    """
    assert OVERRIDE_MIN_REASON_CHARS == 80

    from api_server.schemas.agents import EvalGateOverrideRequest

    with pytest.raises(ValueError):
        EvalGateOverrideRequest(reason="porque sí")
    with pytest.raises(ValueError):
        # Espacios en blanco no cuentan: si contaran, el listón se salta con la
        # barra espaciadora y el campo vuelve a ser decorativo.
        EvalGateOverrideRequest(reason=" " * (OVERRIDE_MIN_REASON_CHARS + 5))
    ok = EvalGateOverrideRequest(
        reason=(
            "el proveedor LLM del juez lleva caído desde las 09:00 (incidencia "
            "INC-4412) y este prompt corrige una fuga de datos en producción"
        )
    )
    assert ok.reason.startswith("el proveedor")


# ---------------------------------------------------------------------------
# El `exclude` de apply_partial_update
# ---------------------------------------------------------------------------
def test_a_request_directive_never_lands_on_the_row() -> None:
    """`eval_gate_override` es una directiva de la petición, no una columna.

    Esta guarda tiene que ser explícita porque su ausencia NO falla: una
    instancia declarativa de SQLAlchemy acepta cualquier atributo, así que sin
    `exclude` el override se quedaría pegado a la fila en memoria sin que nada
    protestara.
    """
    from api_server.db.domain import Agent
    from api_server.routers._helpers import apply_partial_update
    from api_server.schemas.agents import AgentUpdateRequest

    agent = Agent(
        id=uuid4(),
        tenant_id=uuid4(),
        name="antes",
        role="backend_dev",
        system_prompt="viejo",
        model_config={},
    )
    payload = AgentUpdateRequest.model_validate(
        {
            "system_prompt": "nuevo",
            "eval_gate_override": {"reason": "x" * (OVERRIDE_MIN_REASON_CHARS + 1)},
        }
    )

    apply_partial_update(
        agent,
        payload,
        enum_fields=("agent_type", "role", "memory_scope"),
        rename={"llm_config": "model_config"},
        exclude=("eval_gate_override",),
    )

    assert agent.system_prompt == "nuevo"
    assert not hasattr(agent, "eval_gate_override"), (
        "la directiva de la petición se ha escrito sobre la fila del agente"
    )
