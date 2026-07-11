"""P0-1: la persona del agente enmarca el run (build_persona_preamble).

Hasta ahora la persona (`agents.system_prompt`, riquísima en los equipos
built-in) se descartaba en ejecución: implementador y reviewer corrían con el
system genérico + fragmentos de skill. Este preámbulo la prepende COMO PRIMER
bloque del system preamble — identidad antes que los preámbulos por-tarea
(comentarios/feedback/review) y que las skills.

La persona la configura el tenant (misma capa de confianza que los
prompt_fragment de las skills, que tampoco van fenced), pero se acota
explícitamente: guía el CÓMO, nunca reescribe las reglas de operación.
"""

from __future__ import annotations

from agent_runtime.__main__ import assemble_system_preamble, build_persona_preamble


def test_renders_prompt_with_role_and_name() -> None:
    pre = build_persona_preamble(
        {
            "prompt": "Eres el backend CI4, experto en HMVC.",
            "role": "backend_dev",
            "name": "ci4-backend",
        }
    )
    assert "Eres el backend CI4, experto en HMVC." in pre
    assert "ci4-backend" in pre
    assert "backend_dev" in pre


def test_persona_is_bounded_guidance() -> None:
    pre = build_persona_preamble({"prompt": "usa git a saco"})
    assert "never override your operating rules" in pre


def test_empty_or_blank_yields_empty_string() -> None:
    assert build_persona_preamble(None) == ""
    assert build_persona_preamble({}) == ""
    assert build_persona_preamble({"prompt": "   "}) == ""


def test_defensive_cap_with_marker() -> None:
    pre = build_persona_preamble({"prompt": "x" * 20_000})
    assert len(pre) < 12_000
    assert "truncated" in pre


# ---------------------------------------------------------------------------
# assemble_system_preamble: orden de bloques y backward-compat.
# ---------------------------------------------------------------------------
def test_persona_lands_first_before_comments_and_skills() -> None:
    spec = {
        "agent_persona": {"prompt": "PERSONA-BLOQUE"},
        "task_comments": [{"scope": "task", "content": "COMENTARIO-BLOQUE"}],
        "skill_prompt_fragments": ["SKILL-BLOQUE"],
    }
    preamble = assemble_system_preamble(spec)
    assert preamble is not None
    assert (
        preamble.index("PERSONA-BLOQUE")
        < preamble.index("COMENTARIO-BLOQUE")
        < preamble.index("SKILL-BLOQUE")
    )


def test_review_run_keeps_review_preamble_and_gets_persona() -> None:
    spec = {
        "review": True,
        "review_context": {"acceptance_criteria": "CRITERIOS-X"},
        "agent_persona": {"prompt": "PERSONA-REVIEWER"},
    }
    preamble = assemble_system_preamble(spec)
    assert "PERSONA-REVIEWER" in preamble
    assert "CRITERIOS-X" in preamble
    assert preamble.index("PERSONA-REVIEWER") < preamble.index("CRITERIOS-X")


def test_spec_without_persona_is_backward_compatible() -> None:
    spec = {"skill_prompt_fragments": ["SOLO-SKILLS"]}
    assert assemble_system_preamble(spec) == "SOLO-SKILLS"
    assert assemble_system_preamble({}) is None
