"""AUD16-20 (auditoría 2026-07-16): fallos de TRANSPORTE repetidos de
``stack_exec`` abortan el run.

El 07-02 el docker-socket-proxy devolvió 502 en cascada y el run 019f21be-e5c0
quemó las 50 iteraciones repitiendo stack_exec con args distintos: el detector
de bucle no salta (args distintos) y las guardas por novedad no aplican
(stack_exec es producing-tool). Un error de transporte REPETIDO es señal de
infraestructura rota, no de estrategia del agente → corte explícito con
``stack_exec_unavailable`` tras N consecutivos. Un rc!=0 del toolchain del
usuario NO cuenta (transporte sano); cualquier stack_exec con transporte sano
resetea la racha.
"""

from __future__ import annotations

from agent_runtime.graph import (
    _STACK_EXEC_TRANSPORT_TRIP,
    _is_stack_exec_transport_failure,
)
from agent_runtime.safeguards import SafeguardCode


def _obs(*, ok: bool, error: str | None) -> dict[str, object]:
    return {"tool": "stack_exec", "ok": ok, "output": None, "error": error}


def test_abort_code_is_part_of_the_contract() -> None:
    assert str(SafeguardCode.STACK_EXEC_UNAVAILABLE) == "stack_exec_unavailable"


def test_trip_threshold_is_small_but_not_one() -> None:
    # Un blip aislado no corta el run; una cascada sí.
    assert 2 <= _STACK_EXEC_TRANSPORT_TRIP <= 5


def test_transport_failure_is_detected() -> None:
    obs = _obs(ok=False, error="stack_exec failed to reach the worker: HTTP 502 Bad Gateway")
    assert _is_stack_exec_transport_failure("stack_exec", obs) is True


def test_namespaced_stack_exec_counts_the_same() -> None:
    obs = _obs(ok=False, error="stack_exec failed to reach the worker: timeout")
    assert _is_stack_exec_transport_failure("stack.stack_exec", obs) is True


def test_user_toolchain_failure_is_not_transport() -> None:
    obs = _obs(ok=False, error="command exited with code 2")
    assert _is_stack_exec_transport_failure("stack_exec", obs) is False


def test_successful_call_is_not_transport_failure() -> None:
    assert _is_stack_exec_transport_failure("stack_exec", _obs(ok=True, error=None)) is False


def test_other_tools_never_count() -> None:
    obs = {"tool": "write_file", "ok": False, "error": "failed to reach the worker"}
    assert _is_stack_exec_transport_failure("write_file", obs) is False
