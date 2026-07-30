"""P0-7: el preámbulo del fracaso previo no-review (build_prior_failure_preamble).

Un intento anterior que murió sin terminar (failed/aborted: loop, budget, bug)
no dejaba rastro en el prompt del reintento — solo los rechazos del reviewer
viajaban. Este preámbulo avisa al implementador del callejón sin salida, con la
cola del output anterior dentro del fence UNTRUSTED (es texto influenciable por
outputs de tools del run muerto).
"""

from __future__ import annotations

from agent_runtime.__main__ import assemble_system_preamble, build_prior_failure_preamble


def test_renders_status_code_and_tail_fenced() -> None:
    pre = build_prior_failure_preamble(
        {
            "status": "aborted",
            "abort_code": "loop_detected",
            "output_tail": "kept re-reading composer.json forever",
        }
    )
    assert "aborted" in pre
    assert "loop_detected" in pre
    assert "<<<UNTRUSTED_DATA" in pre
    assert pre.index("<<<UNTRUSTED_DATA") < pre.index("kept re-reading")


def test_empty_or_invalid_yields_empty_string() -> None:
    assert build_prior_failure_preamble(None) == ""
    assert build_prior_failure_preamble({}) == ""
    assert build_prior_failure_preamble({"status": "", "abort_code": None}) == ""


def test_failure_block_lands_between_feedback_and_review_blocks() -> None:
    spec = {
        "prior_review_feedback": [
            {"failed_criterion": "FEEDBACK-BLOQUE", "what_to_fix": "f", "testreport_evidence": ""}
        ],
        "prior_failure": {"status": "failed", "abort_code": "CODIGO-FALLO"},
        "skill_prompt_fragments": ["SKILL-BLOQUE"],
    }
    preamble = assemble_system_preamble(spec)
    assert preamble is not None
    assert (
        preamble.index("FEEDBACK-BLOQUE")
        < preamble.index("CODIGO-FALLO")
        < preamble.index("SKILL-BLOQUE")
    )
