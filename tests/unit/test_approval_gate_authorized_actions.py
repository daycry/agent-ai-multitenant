"""ADR 0135 — el gate del sandbox canjea la acción que un humano ya aprobó.

Antes de esto, aprobar NO autorizaba: el ADR 0020 mandaba la task a `backlog`,
el dispatcher montaba un spec que solo llevaba `approval_policy`, y el gate —sin
memoria— volvía a aparcar la MISMA acción. Bucle, y con coste (cada vuelta es un
contenedor, un worktree y un run entero de tokens).

Lo que se fija aquí es la mitad sandbox de la decisión G1+S1+T1+N3:

* G1 — la acción EXACTA: misma tool + mismos args verbatim;
* T1 — un canje: la autorización se consume al usarse;
* y los límites: otra tool de la misma categoría, o los mismos args con un
  cambio mínimo, vuelven a aparcar.
"""

from __future__ import annotations

from typing import Any

from agent_runtime.approval import ApprovalGate
from shared_domain.approval_action import action_fingerprint
from shared_domain.approval_categories import APPROVAL_CATEGORIES

_STRICT = {"categories": dict.fromkeys(APPROVAL_CATEGORIES, "human_required")}

_ARGS = {"path": "src/app.py", "content": "print('hola')\n"}


def _authorized(tool: str, args: Any) -> list[dict[str, Any]]:
    """La forma en que el worker serializa una acción ya aprobada en el spec."""
    return [
        {
            "tool": tool,
            "args_hash": action_fingerprint(tool, args),
            "category": "code_changes",
            "resolved_at": "2026-07-31T10:00:00+00:00",
        }
    ]


# --- caso 1 del ADR: aprobar → re-ejecutar la misma acción → NO se aparca -----
def test_exact_approved_action_is_not_parked_again() -> None:
    gate = ApprovalGate(_STRICT, approved_actions=_authorized("write_file", _ARGS))
    assert gate.review("write_file", _ARGS) is None


def test_without_the_authorised_list_the_same_call_is_parked() -> None:
    """El criterio NEGATIVO que el ADR 0135 exige explícitamente: si borro la
    lista del spec y el caso 1 sigue pasando, el caso 1 no vale nada."""
    gate = ApprovalGate(_STRICT)
    assert gate.review("write_file", _ARGS) == "code_changes"


# --- caso 2: la misma tool con args distintos SÍ se aparca (G1, no G2) --------
def test_same_tool_different_args_is_parked() -> None:
    gate = ApprovalGate(_STRICT, approved_actions=_authorized("write_file", _ARGS))
    assert gate.review("write_file", {**_ARGS, "path": "src/otro.py"}) == "code_changes"


def test_a_single_whitespace_difference_is_parked() -> None:
    """El «casi igual» del eje 4. Se re-aparca (N3) — NO se cae a autorizar la
    tool entera, que es el fallback que el operador rechazó."""
    gate = ApprovalGate(_STRICT, approved_actions=_authorized("write_file", _ARGS))
    assert gate.review("write_file", {**_ARGS, "content": "print('hola')\n "}) == "code_changes"


# --- caso 3: otra tool de la misma categoría SÍ se aparca (G1, no G4) ---------
def test_different_tool_same_category_is_parked() -> None:
    """Aprobar «escribe este fichero» NO autoriza ejecutar shell arbitrario,
    aunque ambas sean `code_changes` en el mapa de hoy."""
    gate = ApprovalGate(_STRICT, approved_actions=_authorized("write_file", _ARGS))
    assert gate.review("shell_exec", _ARGS) == "code_changes"


# --- caso 4: T1, un solo canje ------------------------------------------------
def test_the_authorisation_is_consumed_after_one_use() -> None:
    gate = ApprovalGate(_STRICT, approved_actions=_authorized("write_file", _ARGS))
    assert gate.review("write_file", _ARGS) is None
    assert gate.review("write_file", _ARGS) == "code_changes"


def test_two_approvals_of_the_same_action_allow_two_uses() -> None:
    """Dos aprobaciones humanas = dos canjes. Ni una más."""
    approved = _authorized("write_file", _ARGS) * 2
    gate = ApprovalGate(_STRICT, approved_actions=approved)
    assert gate.review("write_file", _ARGS) is None
    assert gate.review("write_file", _ARGS) is None
    assert gate.review("write_file", _ARGS) == "code_changes"


# --- caso 6: los alias no evaden ni rompen la comparación (ADR 0048) ---------
def test_alias_call_redeems_the_canonical_authorisation() -> None:
    gate = ApprovalGate(_STRICT, approved_actions=_authorized("write_file", _ARGS))
    assert gate.review("file_write", _ARGS) is None


def test_canonical_call_redeems_an_alias_authorisation() -> None:
    gate = ApprovalGate(_STRICT, approved_actions=_authorized("file_write", _ARGS))
    assert gate.review("write_file", _ARGS) is None


# --- fallar cerrado -----------------------------------------------------------
def test_a_call_without_args_never_redeems_an_authorisation() -> None:
    """El gate viejo llamaba `review(tool)` sin args. Ese camino NO puede
    canjear nada: sin args no hay acción exacta que comparar."""
    gate = ApprovalGate(_STRICT, approved_actions=_authorized("write_file", _ARGS))
    assert gate.review("write_file") == "code_changes"


def test_unserialisable_args_are_parked() -> None:
    gate = ApprovalGate(_STRICT, approved_actions=_authorized("write_file", _ARGS))
    assert gate.review("write_file", {"blob": object()}) == "code_changes"


def test_entries_without_a_hash_are_ignored() -> None:
    """Una fila corrupta del spec no puede autorizar nada por omisión."""
    gate = ApprovalGate(
        _STRICT,
        approved_actions=[{"tool": "write_file", "category": "code_changes"}],
    )
    assert gate.review("write_file", _ARGS) == "code_changes"


def test_a_non_sensitive_tool_still_runs_without_consuming_anything() -> None:
    """`read_file` no está en el mapa de categorías: nunca se aparcó, y ahora
    tampoco puede gastar una autorización ajena."""
    gate = ApprovalGate(_STRICT, approved_actions=_authorized("write_file", _ARGS))
    assert gate.review("read_file", _ARGS) is None
    # …y la autorización de write_file sigue intacta.
    assert gate.review("write_file", _ARGS) is None


def test_an_auto_policy_is_unchanged_by_the_list() -> None:
    lax = {"categories": dict.fromkeys(APPROVAL_CATEGORIES, "auto")}
    gate = ApprovalGate(lax, approved_actions=_authorized("write_file", _ARGS))
    assert gate.review("write_file", {"path": "cualquiera"}) is None
