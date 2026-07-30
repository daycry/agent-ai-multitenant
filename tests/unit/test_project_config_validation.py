"""La config de proyecto deja de aceptarse y descartarse en silencio.

Hallazgo aparecido al preparar `task_wf_35` (la UI de configuración de proyecto):
antes de darle pantalla a `execution_budgets` y `guardrails_config`, resulta que
la API los aceptaba SIN VALIDAR y aguas abajo se descartaban sin decir nada.

Las dos mitades degradan de forma distinta y conviene no confundirlas:

* **Presupuestos**: `resolve_execution_budgets` tira las claves desconocidas y
  los valores no numéricos o ≤ 0. El run corre con el presupuesto de
  plataforma. El operador cree que ha capado el gasto de un proyecto y no ha
  capado nada.
* **Guardrails**: `_resolve_guardrails_config` captura el `GuardrailConfigError`
  y degrada a `None`, y el runtime cae a su baseline. Verificado
  empíricamente: **NO** es que los runs queden sin tamizar — eso sería un
  agujero, y no lo es. Es que la config del proyecto se ignora entera.

En los dos casos el fallo es el mismo: 200 y silencio. Ahora es 422 en la puerta.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit


def _update(**kwargs: Any) -> Any:
    from api_server.schemas.projects import ProjectUpdateRequest

    return ProjectUpdateRequest(**kwargs)


# --- Presupuestos ---------------------------------------------------------


def test_an_unknown_budget_key_is_rejected_instead_of_dropped() -> None:
    """El caso real: la `_s` que falta. `max_wall_clock` no existe, así que el
    resolver lo tiraba y el proyecto seguía con el presupuesto de plataforma."""
    with pytest.raises(ValueError, match="max_wall_clock"):
        _update(execution_budgets={"max_wall_clock": 3600})


def test_a_non_numeric_budget_is_rejected() -> None:
    with pytest.raises(ValueError, match="número"):
        _update(execution_budgets={"max_tokens": "muchos"})


def test_a_boolean_cannot_pose_as_a_budget() -> None:
    """`bool` es subclase de `int`: sin el guardia explícito, `True` pasaría por
    cantidad y luego el resolver lo tiraría — el mismo silencio."""
    with pytest.raises(ValueError, match="max_tokens"):
        _update(execution_budgets={"max_tokens": True})


@pytest.mark.parametrize("bad", [0, -1, -0.5])
def test_a_non_positive_budget_is_rejected(bad: float) -> None:
    with pytest.raises(ValueError, match="mayor que cero"):
        _update(execution_budgets={"max_cost_usd": bad})


def test_the_error_lists_the_valid_keys() -> None:
    """Un 422 que no dice cuáles son las claves buenas obliga a leer el código."""
    with pytest.raises(ValueError, match="max_wall_clock_s"):
        _update(execution_budgets={"nope": 1})


def test_a_valid_budget_is_accepted() -> None:
    payload = {"max_wall_clock_s": 600, "max_tokens": 50_000, "max_cost_usd": 2.5}
    assert _update(execution_budgets=payload).execution_budgets == payload


def test_a_value_over_the_ceiling_is_still_accepted() -> None:
    """El recorte al techo está documentado y es intencionado: no es un
    descarte, así que no se convierte en error. Solo se rechaza lo que el
    resolver iba a TIRAR, que es lo indistinguible de no escribir nada."""
    from api_server.budgets.envelope import EXECUTION_BUDGET_CEILING

    huge = float(EXECUTION_BUDGET_CEILING["max_tokens"]) * 1000
    assert _update(execution_budgets={"max_tokens": huge}) is not None


def test_no_budget_override_stays_valid() -> None:
    assert _update(execution_budgets=None).execution_budgets is None
    assert _update(execution_budgets={}).execution_budgets == {}


# --- Guardrails -----------------------------------------------------------


def test_a_malformed_guardrails_config_is_rejected() -> None:
    """La forma equivocada más común: colgar los checks de la raíz en vez de
    del punto del ciclo (`pre_llm`, `post_tool`, …)."""
    with pytest.raises(ValueError, match="guardrails"):
        _update(guardrails_config={"checks": [{"id": "x"}]})


def test_the_api_uses_the_same_parser_as_the_worker() -> None:
    """La API no puede aceptar nada que el worker vaya a rechazar después: si
    los dos lados divergen, vuelve el 200 mudo por otra puerta."""
    from api_server.schemas.projects import _validate_guardrails_config
    from shared_guardrails.exceptions import GuardrailConfigError
    from shared_guardrails.layers import LayerConfig

    bad = {"punto_inventado": []}
    with pytest.raises(GuardrailConfigError):
        LayerConfig.from_dict("project", bad)
    with pytest.raises(ValueError):
        _validate_guardrails_config(bad)


def test_a_well_formed_guardrails_config_is_accepted() -> None:
    ok = {"pre_llm": []}
    assert _update(guardrails_config=ok).guardrails_config == ok


def test_no_guardrails_override_stays_valid() -> None:
    assert _update(guardrails_config=None).guardrails_config is None


# --- secrets_vault_id retirado (task_wf_35) -------------------------------


def test_secrets_vault_id_is_no_longer_accepted() -> None:
    """La columna está DEPRECATED desde P1-04 y no tiene ni un lector. El plan
    pedía darle UI; la decisión (aprobada por el operador) fue la contraria:
    retirarla. Aceptarla era el mismo no-op mudo que los presupuestos — el
    operador rellena un campo y no pasa nada."""
    from api_server.schemas.projects import ProjectCreateRequest, ProjectUpdateRequest

    assert "secrets_vault_id" not in ProjectCreateRequest.model_fields
    assert "secrets_vault_id" not in ProjectUpdateRequest.model_fields


def test_secrets_vault_id_still_travels_in_the_response() -> None:
    """No se retira de la RESPUESTA: romperia los SDK generados (python y
    typescript) sin darle nada a nadie. Viaja siempre `null` hasta que se borre
    la columna, y ese día se retira también de aquí."""
    from api_server.schemas.projects import ProjectResponse

    assert "secrets_vault_id" in ProjectResponse.model_fields
