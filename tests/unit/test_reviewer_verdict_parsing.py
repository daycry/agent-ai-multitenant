"""parse_reviewer_output tolerates verdict-tag format drift (audit cluster C1 / F37).

The strict `<verdict>\\s*(approve|reject)\\s*</verdict>` regex flipped any minor
drift ("<verdict>approve - LGTM</verdict>", "<verdict>I approve</verdict>") to
`unknown`, which the worker turned into a defensive reject → wrongly blocked task.
"""

from __future__ import annotations

import pytest
from api_server.reviewer_bridge import parse_reviewer_output


@pytest.mark.parametrize(
    "text,expected",
    [
        ("<verdict>approve</verdict>", "approve"),
        ("<verdict>reject</verdict>", "reject"),
        ("<verdict>approve - looks good</verdict>", "approve"),
        ("<verdict>I approve this output</verdict>", "approve"),
        ("done.\n<verdict>Approved</verdict>", "approve"),
        ("<verdict>reject — needs tests</verdict>", "reject"),
        ("<verdict>rejected</verdict>", "reject"),
        ("no verdict tag at all", "unknown"),
        # A bare word in prose without the tag is NOT honoured (false-positive risk).
        ("I would approve this", "unknown"),
    ],
)
def test_verdict_tag_tolerance(text: str, expected: str) -> None:
    assert parse_reviewer_output(text).label == expected


def test_last_decisive_verdict_wins() -> None:
    # The agent changed its mind: reject then approve → approve (the final call).
    out = "<verdict>reject</verdict>\nwait, actually\n<verdict>approve</verdict>"
    assert parse_reviewer_output(out).label == "approve"


def test_reject_pulls_rejection_block() -> None:
    out = (
        "<verdict>reject</verdict>"
        "<rejection><failed_criterion>missing tests</failed_criterion>"
        "<what_to_fix>add a regression test</what_to_fix></rejection>"
    )
    verdict = parse_reviewer_output(out)
    assert verdict.label == "reject"
    assert verdict.failed_criterion == "missing tests"
    assert verdict.what_to_fix == "add a regression test"
