"""task_mem_04 — tipos de observación (`kind`) y resumen por ejecución.

El parser del destilado tiene que aguantar a un modelo de ~9B (ADR 0169): JSON con
texto alrededor, claves en otro orden, `kind` en mayúsculas o en español, dos
resúmenes o ninguno. Nada de eso pierde las memorias: se normaliza. Además, la
etiqueta `<private>…</private>` de un texto humano no llega al LLM ni a la fila.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from api_server.memorizer.distillation import (
    _HUMAN_SYSTEM_PROMPT,
    _SYSTEM_PROMPT,
    SUMMARY_MAX_CHARS,
    _human_user_prompt,
    _parse_response_result,
    _user_prompt,
    distil_execution_result,
)
from api_server.memorizer.kinds import MEMORY_KINDS, normalise_kind, parse_kind_filter
from api_server.memorizer.sanitize import sanitize_memory_content, strip_private_tags
from shared_llm.types import CompletionResponse, Message, StreamChunk, Usage

pytestmark = pytest.mark.unit


def _item(content: str, type_: str = "semantic", **extra: Any) -> dict[str, Any]:
    return {"content": content, "type": type_, "tags": [], "entities": [], **extra}


def _parse(payload: Any) -> list[Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return _parse_response_result(text)[0]


def test_the_vocabulary_is_closed_and_has_the_seven_kinds() -> None:
    assert set(MEMORY_KINDS) == {
        "summary",
        "decision",
        "bugfix",
        "pattern",
        "gotcha",
        "preference",
        "fact",
    }


def test_valid_kinds_are_kept_and_one_summary_comes_first() -> None:
    cands = _parse(
        [
            _item("Usar asyncpg, no psycopg3.", kind="decision"),
            _item("Se arregló el import.", "episodic", kind="summary"),
            _item("RLS exige tenant_id.", kind="gotcha"),
        ]
    )
    assert [c.kind for c in cands] == ["summary", "decision", "gotcha"]
    assert sum(1 for c in cands if c.kind == "summary") == 1


def test_the_summary_is_always_episodic_and_capped_at_600_chars() -> None:
    cands = _parse([_item("x" * 900, "semantic", kind="summary")])
    assert len(cands) == 1
    assert cands[0].type == "episodic"
    assert len(cands[0].content) == SUMMARY_MAX_CHARS == 600


def test_several_summaries_keep_the_first_and_demote_the_rest_to_fact() -> None:
    cands = _parse(
        [
            _item("Primero.", "episodic", kind="summary"),
            _item("Segundo.", "episodic", kind="summary"),
            _item("Tercero.", "episodic", kind="summary"),
        ]
    )
    assert [(c.content, c.kind) for c in cands] == [
        ("Primero.", "summary"),
        ("Segundo.", "fact"),
        ("Tercero.", "fact"),
    ]


def test_no_summary_leaves_the_execution_without_one_and_keeps_the_observations() -> None:
    # Decisión: no se fabrica un resumen a partir de las observaciones (sería un
    # duplicado con otro titular); la ejecución simplemente no tiene `summary`.
    cands = _parse([_item("Regla A.", kind="pattern"), _item("Hecho B.", "episodic", kind="fact")])
    assert [c.kind for c in cands] == ["pattern", "fact"]
    assert not any(c.kind == "summary" for c in cands)


def test_a_kind_outside_the_vocabulary_becomes_fact_never_an_error() -> None:
    cands = _parse([_item("Algo.", kind="banana"), _item("Otra.", kind=42)])
    assert [c.kind for c in cands] == ["fact", "fact"]


def test_a_missing_kind_stays_null_like_the_legacy_memories() -> None:
    cands = _parse([_item("Sin tipo.")])
    assert cands[0].kind is None


def test_uppercase_and_spanish_kinds_are_normalised() -> None:
    assert normalise_kind("DECISION") == "decision"
    assert normalise_kind("  Decisión ") == "decision"
    assert normalise_kind("resumen") == "summary"
    assert normalise_kind("Trampa") == "gotcha"
    assert normalise_kind("preferencia") == "preference"
    assert normalise_kind("patrón") == "pattern"
    assert normalise_kind("error") == "bugfix"
    assert normalise_kind("hecho") == "fact"
    assert normalise_kind("banana") == "fact"
    assert normalise_kind(None) is None
    assert normalise_kind("") is None


def test_imperfect_small_model_output_survives() -> None:
    text = (
        "Claro, aquí tienes el resultado:\n```json\n"
        '[{"kind": "RESUMEN", "tags": ["x"], "type": "Episodic", "entities": ["asyncpg"],'
        ' "content": "Se corrigió el driver."},\n'
        ' {"type": "semantic", "content": "Preferir asyncpg.", "kind": "Preferencia"},\n'
        ' {"content": "Dato suelto", "kind": "cosa rara", "type": "episodic"}]\n'
        "```\nEspero que sirva."
    )
    cands, cause = _parse_response_result(text)
    assert cause == "ok"
    assert [c.kind for c in cands] == ["summary", "preference", "fact"]
    assert cands[0].type == "episodic"  # "Episodic" en mayúscula aceptado


def test_an_object_wrapper_is_unwrapped() -> None:
    text = 'Resultado: {"memories": [{"content": "Hola", "type": "semantic", "kind": "fact"}]}'
    cands, cause = _parse_response_result(text)
    assert cause == "ok" and [c.kind for c in cands] == ["fact"]


def test_a_missing_type_is_inferred_from_a_valid_kind_but_junk_is_still_skipped() -> None:
    cands = _parse(
        [
            {"content": "Regla.", "kind": "pattern"},
            {"content": "Se hizo X.", "kind": "summary"},
            {"content": "Sin nada."},
        ]
    )
    assert [(c.kind, c.type) for c in cands] == [("summary", "episodic"), ("pattern", "semantic")]


def test_parse_kind_filter_drops_unknowns_and_dedups() -> None:
    assert parse_kind_filter(["Decision", "banana", "decisión", "gotcha"]) == ["decision", "gotcha"]
    assert parse_kind_filter(["banana"]) == []
    assert parse_kind_filter(None) == []


# --- <private> -------------------------------------------------------------
def test_strip_private_tags_removes_block_multiline_and_case_insensitive() -> None:
    text = (
        "Decidimos A. <private>la clave es hunter2\ny otra línea</private> "
        "Luego B. <PRIVATE>zzz</PRIVATE>"
    )
    out = strip_private_tags(text)
    assert "hunter2" not in out and "otra línea" not in out and "zzz" not in out
    assert "Decidimos A." in out and "Luego B." in out


def test_an_unterminated_private_tag_drops_everything_after_it() -> None:
    out = strip_private_tags("Público. <private>secreto sin cerrar")
    assert out.strip() == "Público."


def test_sanitize_memory_content_also_drops_private_blocks() -> None:
    clean, n = sanitize_memory_content("Regla útil <private>dato interno</private>")
    assert "dato interno" not in clean and "Regla útil" in clean
    assert n >= 1


def test_the_human_prompt_never_shows_private_text_to_the_llm() -> None:
    prompt = _human_user_prompt(
        session={"comments": "Elegí Redis. <private>presupuesto 90k</private>"},
        agent={"role": "pm"},
        user={"name": "Ana"},
    )
    assert "Elegí Redis." in prompt and "90k" not in prompt


def test_the_execution_prompt_strips_private_text_too() -> None:
    prompt = _user_prompt(
        execution={"status": "done", "output": "ok <private>token interno</private>"},
        agent={"role": "dev"},
    )
    assert "token interno" not in prompt


# --- prompts ---------------------------------------------------------------
@pytest.mark.parametrize("prompt", [_SYSTEM_PROMPT, _HUMAN_SYSTEM_PROMPT])
def test_the_prompts_ask_for_one_summary_and_typed_observations(prompt: str) -> None:
    assert '"kind"' in prompt
    assert "600" in prompt
    for kind in MEMORY_KINDS:
        assert kind in prompt


class _FakeLLM:
    name = "fake"

    def __init__(self, content: str) -> None:
        self.content = content

    async def complete(self, messages: Sequence[Message], **kw: Any) -> CompletionResponse:
        return CompletionResponse(
            content=self.content, model="m", provider="fake", usage=Usage(), tool_calls=None, raw={}
        )

    async def stream(self, messages: Sequence[Message], **kw: Any) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(delta="", usage=None, raw={})

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_distil_execution_result_carries_the_kinds() -> None:
    llm = _FakeLLM(json.dumps([_item("Hecho X.", "episodic", kind="summary")]))
    res = await distil_execution_result(
        execution={"status": "done", "output": "o", "steps_log": []}, agent={"role": "r"}, llm=llm
    )
    assert res.cause == "ok" and res.candidates[0].kind == "summary"


def test_bug_fix_with_a_space_is_a_bugfix() -> None:
    from api_server.memorizer.kinds import normalise_kind

    assert normalise_kind("bug fix") == "bugfix"
    assert normalise_kind("Bug Fix") == "bugfix"
