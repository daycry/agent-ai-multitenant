"""P1-5 (investigación 2026-07-11): lo evictado deja un rastro condensado.

La ventana de contexto (_CONTEXT_WINDOW=8) evictaba sin resumen: la cadena de
razonamiento/observaciones más antigua desaparecía y solo los digests de
lectura rescataban una brizna. `_condense_evicted` (determinista, acotado)
produce una línea por item evictado (rol + esencia truncada) que
`_decide_messages` antepone como bloque «EARLIER (condensed)».
"""

from __future__ import annotations

from agent_runtime.providers import _CONTEXT_WINDOW, _condense_evicted, _decide_messages


def _items(n: int) -> list[dict[str, str]]:
    return [{"role": "observation", "note": f"paso-{i}: hice la cosa {i}"} for i in range(n)]


def test_nothing_evicted_no_block() -> None:
    state = {"task": {"title": "T"}, "context": _items(_CONTEXT_WINDOW)}
    user = _decide_messages(state)[1].content
    assert "EARLIER (condensed)" not in user


def test_evicted_items_leave_condensed_lines() -> None:
    state = {"task": {"title": "T"}, "context": _items(_CONTEXT_WINDOW + 3)}
    user = _decide_messages(state)[1].content
    assert "EARLIER (condensed)" in user
    # Los 3 primeros (evictados) aparecen condensados; el bloque va ANTES de
    # "Context so far" (cronología: lo viejo primero).
    assert "paso-0" in user and "paso-2" in user
    assert user.index("EARLIER (condensed)") < user.index("Context so far:")


def test_condensed_lines_are_bounded() -> None:
    long_items = [
        {"role": "observation", "note": "x" * 2000, "extra": "y" * 2000} for _ in range(60)
    ]
    lines = _condense_evicted(long_items)
    assert len(lines) <= 16  # cabecera-menos: cap de líneas
    assert all(len(line) <= 200 for line in lines)
