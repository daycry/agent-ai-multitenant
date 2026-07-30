"""Contrato cruzado runtime↔worker del tag `<verdict>` (hallazgo H3).

El runtime (agent_runtime.review_contract) ANUNCIA el formato en sus prompts;
el worker (api_server.reviewer_bridge) lo PARSEA con regex. Son paquetes que no
pueden compartir la constante en runtime (el contenedor no lleva api_server),
así que este test del repo es el punto único que ata ambos lados: si cualquiera
cambia su lado del contrato, esto se pone rojo antes de que un run real degrade
en rechazos defensivos.
"""

from __future__ import annotations

from agent_runtime import review_contract as rc
from api_server.reviewer_bridge import parse_reviewer_output


def test_worker_parses_the_advertised_approve() -> None:
    summary = f"The output satisfies every criterion.\n{rc.VERDICT_APPROVE}"
    assert parse_reviewer_output(summary).label == "approve"


def test_worker_parses_the_advertised_reject() -> None:
    summary = (
        "The second criterion is not met.\n"
        f"{rc.VERDICT_REJECT}\n"
        "<rejection><failed_criterion>tests missing</failed_criterion>"
        "<what_to_fix>add the regression test</what_to_fix></rejection>"
    )
    verdict = parse_reviewer_output(summary)
    assert verdict.label == "reject"


def test_worker_parses_the_nudge_shaped_closing() -> None:
    """Un run que obedece el cierre EXACTO que empujan los nudges debe parsear."""
    summary = f"Reviewed the worktree against the criteria. {rc.VERDICT_APPROVE}"
    assert parse_reviewer_output(summary).label == "approve"
