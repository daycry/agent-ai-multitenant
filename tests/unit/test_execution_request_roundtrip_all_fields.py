"""Cada campo de `ExecutionRequest` sobrevive a `as_dict` → `from_dict` (`task_h00_02`).

Medido el 2026-09-29: de los 24 campos del contrato, `claim_id` era el único que
`from_dict` leía y `as_dict` no emitía. En producción no se notaba porque el
orquestador construye el payload a mano y añade la clave por su cuenta, pero
cualquier camino que serialice la dataclass —un reintento, una herramienta, un test—
perdía la identidad de la reclamación y el worker dejaba de descartar los mensajes
obsoletos.

El test recorre `dataclasses.fields`, así que el próximo campo que alguien añada y
olvide en uno de los dos lados también lo rompe.
"""

from __future__ import annotations

import dataclasses
from typing import Any, get_type_hints

import pytest
from workers.run_contract import ExecutionRequest

pytestmark = pytest.mark.unit


def _sentinel(name: str, hint: Any) -> Any:
    """Un valor distinto del default para cada campo, según su tipo declarado."""
    text = str(hint)
    if "bool" in text:
        return True
    if "list" in text:
        return [f"{name}-item"]
    if "dict" in text:
        return {"field": name}
    return f"{name}-value"


def _full_request() -> ExecutionRequest:
    hints = get_type_hints(ExecutionRequest)
    values = {
        f.name: _sentinel(f.name, hints[f.name]) for f in dataclasses.fields(ExecutionRequest)
    }
    return ExecutionRequest(**values)


def test_every_field_is_emitted_by_as_dict() -> None:
    names = {f.name for f in dataclasses.fields(ExecutionRequest)}
    missing = sorted(names - set(_full_request().as_dict()))
    assert not missing, f"campos que as_dict no emite: {missing}"


def test_every_field_survives_the_round_trip() -> None:
    original = _full_request()
    rebuilt = ExecutionRequest.from_dict(original.as_dict())
    lost = [
        f.name
        for f in dataclasses.fields(ExecutionRequest)
        if getattr(rebuilt, f.name) != getattr(original, f.name)
    ]
    assert not lost, f"campos que no sobreviven a la ida y vuelta: {lost}"


def test_claim_id_travels_through_the_canonical_serializer() -> None:
    request = ExecutionRequest(
        tenant_id="t", task_id="k", agent_id=None, task={}, model={}, claim_id="c-7"
    )
    assert request.as_dict()["claim_id"] == "c-7"
