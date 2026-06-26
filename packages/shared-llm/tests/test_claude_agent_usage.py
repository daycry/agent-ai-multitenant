"""_usage_get reads SDK usage whether it's an object or a dict (regression 2026-06-27).

claude_sdk runs showed cost>0 but tokens=0: the SDK's ResultMessage carries
``total_cost_usd`` as an attribute but its ``usage`` arrived as a plain dict, so
``getattr(u, "input_tokens")`` silently returned 0. ``_usage_get`` reads both shapes.
"""

from __future__ import annotations

from typing import Any

from shared_llm.providers.claude_agent import _usage_get


class _Obj:
    def __init__(self, **kw: Any) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


def test_reads_object_attributes() -> None:
    u = _Obj(input_tokens=10, output_tokens=5)
    assert _usage_get(u, "input_tokens") == 10
    assert _usage_get(u, "output_tokens") == 5
    assert _usage_get(u, "missing") == 0


def test_reads_dict_keys() -> None:
    u = {"input_tokens": 7, "output_tokens": 3}
    assert _usage_get(u, "input_tokens") == 7
    assert _usage_get(u, "output_tokens") == 3
    assert _usage_get(u, "missing", 99) == 99


def test_none_and_falsy_coerce_to_int() -> None:
    assert _usage_get(None, "input_tokens") == 0
    assert _usage_get({"input_tokens": None}, "input_tokens") == 0
    assert _usage_get(_Obj(input_tokens="12"), "input_tokens") == 12
