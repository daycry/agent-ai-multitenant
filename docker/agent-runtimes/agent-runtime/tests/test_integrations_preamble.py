"""`task_mk_21` (MK-05): las anclas de integración del proyecto en el preámbulo.

`spec["integrations"]` (= `project.integrations`) se pliega como bloque justo
tras la persona y antes de los comentarios y las skills: es contexto del
PROYECTO que las skills `atlassian-*` leen antes de caer al texto del plan.
"""

from __future__ import annotations

from agent_runtime.__main__ import assemble_system_preamble, build_integrations_preamble

_ANCHORS = {
    "jira": {"project_key": "PLAT", "parent_issue_key": "PLAT-120"},
    "confluence": {"space_key": "ENG", "root_page_id": "123456"},
}


def test_renders_every_anchor_with_a_human_label() -> None:
    pre = build_integrations_preamble(_ANCHORS)
    assert "Jira project: PLAT" in pre
    assert "Jira parent issue (epic): PLAT-120" in pre
    assert "Confluence space: ENG" in pre
    assert "Confluence root page id: 123456" in pre
    # Y dice que mandan sobre el texto del plan, que es el punto de la casilla.
    assert "Prefer these anchors" in pre


def test_optional_anchors_are_simply_absent() -> None:
    pre = build_integrations_preamble({"jira": {"project_key": "PLAT"}})
    anchor_lines = [ln for ln in pre.splitlines() if ln.startswith("- ")]
    assert anchor_lines == ["- Jira project: PLAT"]


def test_empty_or_malformed_yields_no_block() -> None:
    assert build_integrations_preamble(None) == ""
    assert build_integrations_preamble({}) == ""
    assert build_integrations_preamble({"jira": "PLAT"}) == ""
    assert build_integrations_preamble({"jira": {"project_key": "  "}}) == ""


def test_unknown_provider_still_reaches_the_model_generically() -> None:
    pre = build_integrations_preamble({"github": {"repo": "acme/api"}})
    assert "github repo: acme/api" in pre


def test_values_are_one_line_and_capped() -> None:
    pre = build_integrations_preamble({"jira": {"project_key": "A\nB" + "x" * 500}})
    line = next(ln for ln in pre.splitlines() if ln.startswith("- Jira project:"))
    assert "\n" not in line[2:]
    assert len(line) < 200


def test_lands_after_the_persona_and_before_comments_and_skills() -> None:
    spec = {
        "agent_persona": {"prompt": "PERSONA-BLOQUE"},
        "integrations": _ANCHORS,
        "task_comments": [{"scope": "task", "content": "COMENTARIO-BLOQUE"}],
        "skill_prompt_fragments": ["SKILL-BLOQUE"],
    }
    preamble = assemble_system_preamble(spec)
    assert preamble is not None
    persona = preamble.index("PERSONA-BLOQUE")
    anchors = preamble.index("Jira project: PLAT")
    comments = preamble.index("COMENTARIO-BLOQUE")
    skills = preamble.index("SKILL-BLOQUE")
    assert persona < anchors < comments < skills


def test_without_anchors_the_preamble_is_unchanged() -> None:
    spec = {"skill_prompt_fragments": ["SOLO-SKILLS"]}
    assert assemble_system_preamble(spec) == "SOLO-SKILLS"
    assert assemble_system_preamble({**spec, "integrations": {}}) == "SOLO-SKILLS"
