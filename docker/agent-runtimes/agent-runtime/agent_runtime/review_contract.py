"""Single source of the reviewer's verdict wire-format (hallazgo H3, 2026-07-07).

The ``<verdict>`` tag is the WIRE CONTRACT between a REVIEW run and the worker
(``api_server.reviewer_bridge.parse_reviewer_output`` regex-scans the run's final
prose for it). Its format used to be spelled out literally in FIVE prompt sites
(the worker-threaded preamble, the reviewer system prompt and the three review
nudges) — a wording drift in any of them silently degrades verdict parsing into
defensive rejects. Every prompt now interpolates these constants instead of
re-typing the tag; the cross-package test (`tests/unit/test_review_verdict_wire_
contract.py`) pins that what these constants advertise is exactly what the
worker parses.
"""

from __future__ import annotations

# The canonical tag tokens the worker's `_VERDICT_RE` reads.
VERDICT_APPROVE = "<verdict>approve</verdict>"
VERDICT_REJECT = "<verdict>reject</verdict>"

# The shared closing sentence of every review NUDGE (graph.py) — the actionable
# "stop and deliver the verdict" push. One wording, one place.
REVIEW_FINISH_SUMMARY = (
    "reply with your final summary ending in exactly one "
    f"{VERDICT_APPROVE} or {VERDICT_REJECT} tag"
)
