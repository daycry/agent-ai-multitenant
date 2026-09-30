"""Formas que un modelo pequeño da al destilado (`_extract_items`): no se pierde lo aprovechable."""

from __future__ import annotations

import json

from api_server.memorizer.distillation import _parse_response_result

_OBS = {"content": "El worker usa asyncpg", "type": "semantic", "kind": "fact", "tags": ["db"]}
_OBS2 = {"content": "Migrar con alembic", "type": "semantic", "kind": "decision", "tags": ["db"]}
_SUM = {"content": "Se migró la base de datos", "kind": "summary"}


def test_a_bare_item_with_tags_is_an_item_not_its_tags_list() -> None:
    out, cause = _parse_response_result(json.dumps(_OBS))
    assert cause == "ok"
    assert [c.content for c in out] == [_OBS["content"]]


def test_wrapper_with_summary_and_observations_keeps_the_summary() -> None:
    raw = json.dumps({"summary": _SUM, "observations": [_OBS, _OBS2]})
    out, cause = _parse_response_result(raw)
    assert cause == "ok"
    assert out[0].kind == "summary"
    assert {c.content for c in out} == {_SUM["content"], _OBS["content"], _OBS2["content"]}


def test_wrapper_with_a_string_summary() -> None:
    raw = json.dumps({"summary": "Resumen en texto", "memories": [_OBS]})
    out, _ = _parse_response_result(raw)
    assert out[0].kind == "summary" and out[0].content == "Resumen en texto"
    assert len(out) == 2


def test_truncated_json_recovers_the_complete_objects() -> None:
    raw = (
        '{"observations": [' + json.dumps(_OBS) + "," + json.dumps(_OBS2) + ', {"content": "cortad'
    )
    out, cause = _parse_response_result(raw)
    assert cause == "ok"
    assert {c.content for c in out} == {_OBS["content"], _OBS2["content"]}


def test_truncated_top_level_array_recovers_too() -> None:
    raw = "```json\n[" + json.dumps(_OBS) + ', {"content": "cort'
    out, cause = _parse_response_result(raw)
    assert cause == "ok" and len(out) == 1


def test_nothing_parseable_is_unparseable_not_empty() -> None:
    for raw in ('{"foo": 1}', "sin json", '["a", "b"]', '{"observations": [{"conten'):
        out, cause = _parse_response_result(raw)
        assert (out, cause) == ([], "llm_unparseable"), raw


def test_an_empty_list_is_still_the_legit_empty() -> None:
    assert _parse_response_result("[]") == ([], "llm_empty")
    assert _parse_response_result('{"observations": []}') == ([], "llm_empty")
