"""TAP 13 parser (Plan 06 task_06_14).

A subset sufficient for the runtimes we ship. We recognise:

  * The plan line: ``1..N`` (anywhere in the stream)
  * Result lines: ``ok 1 - description`` / ``not ok 2 - description``
  * Skip directives: ``# SKIP`` / ``# skip``
  * Diagnostic YAML blocks under a "not ok" line (between ``---`` and
    ``...``) — surfaced as the failure traceback.

We do NOT support nested subtests beyond pass-through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from shared_test_runtimes.test_report import (
    TestFailure,
    TestReport,
    TestStatus,
    TestSummary,
)

_PLAN_RE = re.compile(r"^1\.\.(\d+)\s*$")
_OK_RE = re.compile(r"^(ok|not ok)\s+(\d+)\s*(?:-\s*)?(.*?)(?:\s+#\s*(skip|todo).*)?$", re.I)


@dataclass
class _Counters:
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    plan_total: int | None = None


def _read_yaml_block(lines: list[str], start: int) -> tuple[str | None, int]:
    """Return (traceback or None, next index) starting at ``lines[start]``."""
    if start >= len(lines) or lines[start].strip() != "---":
        return None, start
    buf: list[str] = []
    j = start + 1
    while j < len(lines) and lines[j].strip() != "...":
        buf.append(lines[j])
        j += 1
    return ("\n".join(buf) if buf else None), j


def _handle_result(
    match: re.Match[str],
    lines: list[str],
    idx: int,
    counters: _Counters,
    failures: list[TestFailure],
) -> int:
    """Apply one ``ok``/``not ok`` line; return next index."""
    kind, _num, description, directive = match.groups()
    kind = kind.lower()
    directive = (directive or "").lower()
    counters.total += 1
    if directive == "skip":
        counters.skipped += 1
        return idx + 1
    if kind == "ok":
        counters.passed += 1
        return idx + 1
    counters.failed += 1
    traceback, next_idx = _read_yaml_block(lines, idx + 1)
    failures.append(
        TestFailure(
            test_id=description.strip() or f"#{counters.total}",
            message=description.strip(),
            traceback=traceback,
        )
    )
    return next_idx + 1


def _status_for(counters: _Counters, errored: int) -> TestStatus:
    if counters.failed > 0:
        return "failed"
    if errored > 0:
        return "error"
    if counters.total == 0 or (counters.skipped == counters.total and counters.total > 0):
        return "skipped"
    return "passed"


def parse(text: str, *, runtime: str) -> TestReport | None:
    lines = text.splitlines()
    if not any(_PLAN_RE.match(line) or line.startswith(("ok ", "not ok ")) for line in lines):
        return None

    counters = _Counters()
    failures: list[TestFailure] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        plan_match = _PLAN_RE.match(line)
        if plan_match:
            counters.plan_total = int(plan_match.group(1))
            i += 1
            continue
        result_match = _OK_RE.match(line)
        if not result_match:
            i += 1
            continue
        i = _handle_result(result_match, lines, i, counters, failures)

    errored = 0
    if counters.plan_total is not None and counters.total < counters.plan_total:
        errored = counters.plan_total - counters.total
        counters.total = counters.plan_total

    return TestReport(
        runtime=runtime,
        status=_status_for(counters, errored),
        summary=TestSummary(
            total=counters.total,
            passed=counters.passed,
            failed=counters.failed,
            errored=errored,
            skipped=counters.skipped,
        ),
        failures=tuple(failures),
    )


__all__ = ["parse"]
