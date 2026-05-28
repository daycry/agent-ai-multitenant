"""Catch-all raw-text "parser" (Plan 06 task_06_14).

Used as the fallback when no structured parser recognises the output.
We don't parse anything per se — we just wrap the text into a
:class:`TestReport` with ``status='failed'`` if the text mentions
"fail" / "error" anywhere, otherwise ``status='passed'`` (a best-
effort heuristic). The reviewer prompt template knows this is the
lowest-confidence path and surfaces the logs prominently.

Critically, this parser NEVER returns ``None`` — it's the floor for
every runtime's parser list. The worker registry depends on that.
"""

from __future__ import annotations

import re

from shared_test_runtimes.test_report import TestReport, TestStatus, TestSummary

_FAIL_RE = re.compile(r"\b(fail(ed|ure)?|error|traceback|assertion)\b", re.I)
# Hard cap on logs_excerpt — 2 KiB matches the contract in
# test_report.format_for_reviewer.
_MAX_LOG_BYTES = 2048


def parse(text: str, *, runtime: str) -> TestReport | None:
    excerpt = _tail_bytes(text, _MAX_LOG_BYTES)
    status: TestStatus = "failed" if _FAIL_RE.search(text) else "passed"

    return TestReport(
        runtime=runtime,
        status=status,
        summary=TestSummary(),
        logs_excerpt=excerpt,
    )


def _tail_bytes(text: str, limit_bytes: int) -> str:
    """Return the *last* ``limit_bytes`` of ``text`` (failure tails are
    almost always more useful than the headers)."""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit_bytes:
        return text
    tail = encoded[-limit_bytes:].decode("utf-8", errors="ignore")
    return "… (truncated)\n" + tail


__all__ = ["parse"]
