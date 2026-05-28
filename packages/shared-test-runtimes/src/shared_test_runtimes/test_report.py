"""Canonical TestReport schema + reviewer-input formatter (Plan 06 Fase D).

A *TestReport* is the contract between worker-test (which runs tests
in a heterogeneous stack) and the agent reviewer (which decides if a
task is ``done``). Every parser in :mod:`shared_test_runtimes.parsers`
emits this shape so the reviewer doesn't care whether the original
output came from JUnit XML, Jest JSON, TAP, or anything else.

Three tasks of Fase D live in this module:

  * :class:`TestReport` (06_13) — frozen dataclass with status,
    summary counts, per-test failures, log excerpt, artifacts.
  * Each parser registered in :mod:`shared_test_runtimes.parsers`
    returns a :class:`TestReport` (06_14).
  * :func:`format_for_reviewer` (06_15) turns a :class:`TestReport`
    into a stable, structured Markdown block the agent reviewer
    consumes as input.

The schema is intentionally narrow — every field has a clear contract
the reviewer can rely on. Adding a field is a coordinated change across
parsers + the reviewer prompt; not something to do casually.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

# Coarse outcome of one test run. Mapped 1:1 by every parser.
#
#   passed    — every test ran and reported success.
#   failed    — at least one test reported failure.
#   error     — the test harness itself blew up (compile error,
#               import error, etc.) BEFORE any test ran. Distinct
#               from `failed` because the reviewer treats it
#               differently (look at logs, not at failures[]).
#   skipped   — no tests selected by the filter / all marked skip.
#   timeout   — wall clock exceeded; partial run.
TestStatus = Literal["passed", "failed", "error", "skipped", "timeout"]


@dataclass(frozen=True)
class TestFailure:
    """One failed-or-errored test, normalised across parsers."""

    test_id: str
    """Stable per-test identifier (path::test_name, FQN, …)."""

    message: str
    """The failure message — what assertion failed, what exception
    was raised, etc. Single line preferred but multi-line tolerated."""

    file: str | None = None
    """Source file the test lives in. Best-effort: some parsers
    don't surface this (TAP, raw_text)."""

    line: int | None = None
    """Line number. Same best-effort caveat as :attr:`file`."""

    traceback: str | None = None
    """Full traceback / stack trace. The reviewer renders this in a
    fenced block; truncating happens at the formatter, not here."""

    duration_ms: int | None = None
    """Wall-clock duration of the failed test."""


@dataclass(frozen=True)
class TestSummary:
    """Counts at a glance — what every parser must surface."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    skipped: int = 0
    duration_ms: int | None = None


@dataclass(frozen=True)
class TestReport:
    """One canonical run report.

    Built by:
      * Each parser in :mod:`shared_test_runtimes.parsers` from one
        runtime's output.
      * The worker-test combiner when a task spans multiple runtimes
        (it merges per-runtime reports into one TestReport with
        ``runtime="multi"``).
    """

    runtime: str
    """Runtime id the report comes from (``python-pytest``,
    ``multi``, …). The reviewer uses this to format runtime-specific
    suggestions."""

    status: TestStatus
    """Coarse outcome — see :data:`TestStatus`."""

    summary: TestSummary = field(default_factory=TestSummary)

    failures: tuple[TestFailure, ...] = ()
    """Per-test failures; empty when ``status='passed'``."""

    logs_excerpt: str = ""
    """A trimmed slice of the container's stdout/stderr, ~2 KiB. The
    reviewer renders this as the "what the test saw" context."""

    artifacts: tuple[str, ...] = ()
    """On-host paths to files the test produced (junit.xml, screenshots,
    HAR files, …). The reviewer references them by path; the worker
    handles upload to object storage when needed."""

    extra: Mapping[str, str] = field(default_factory=dict)
    """Runtime-specific extras parsers want to surface but that don't
    fit a closed field (Playwright trace zip path, coverage %, …).
    The reviewer treats them as opaque key→string."""


# ---------------------------------------------------------------------------
# task_06_15 — Formatter for the reviewer agent
# ---------------------------------------------------------------------------

# Hard cap on the formatted block. The reviewer is an LLM call and we
# don't want to pay for tokens past the useful threshold. 8 KiB is
# enough to render summary + ~5 failures with tracebacks; if the test
# emits 200 failures, the formatter truncates with a "..." marker.
_MAX_FORMATTED_BYTES = 8 * 1024

# Per-failure traceback cap. Most assertion stacks fit in 1 KiB;
# rare deep stacks get trimmed.
_MAX_TRACEBACK_BYTES = 1024


def format_for_reviewer(report: TestReport) -> str:
    """Render a TestReport as the reviewer's input block.

    The output is stable Markdown (so the reviewer prompt template can
    rely on the exact structure) and capped at :data:`_MAX_FORMATTED_BYTES`.

    Layout:

        ## Test report — <runtime>
        **Status:** <status>
        **Summary:** total=N passed=N failed=N errored=N skipped=N

        ### Failures
        - **<test_id>** (`<file>:<line>`)
            > <message>
            ```
            <traceback>
            ```

        ### Logs (excerpt)
        ```
        <logs_excerpt>
        ```

        ### Artifacts
        - <path>

    Truncation rules (in order):
      1. Each failure traceback capped at 1 KiB.
      2. If the total still exceeds 8 KiB, drop failures from the
         tail with a "(… N more failures omitted)" line.
      3. Logs excerpt is appended last so it gets trimmed first if
         needed.
    """
    parts: list[str] = []
    parts.append(f"## Test report — {report.runtime}")
    parts.append(f"**Status:** {report.status}")
    s = report.summary
    parts.append(
        f"**Summary:** total={s.total} passed={s.passed} "
        f"failed={s.failed} errored={s.errored} skipped={s.skipped}"
    )
    if s.duration_ms is not None:
        parts.append(f"**Duration:** {s.duration_ms} ms")

    if report.failures:
        parts.append("")
        parts.append("### Failures")
        for fail in report.failures:
            parts.extend(_format_failure(fail))

    if report.artifacts:
        parts.append("")
        parts.append("### Artifacts")
        parts.extend(f"- {a}" for a in report.artifacts)

    if report.extra:
        parts.append("")
        parts.append("### Extras")
        parts.extend(f"- **{k}:** {v}" for k, v in sorted(report.extra.items()))

    # Logs go last because they're the cheapest to drop on truncation.
    if report.logs_excerpt:
        parts.append("")
        parts.append("### Logs (excerpt)")
        parts.append("```")
        parts.append(report.logs_excerpt)
        parts.append("```")

    text = "\n".join(parts)
    return _truncate_to(text, _MAX_FORMATTED_BYTES)


def _format_failure(fail: TestFailure) -> Sequence[str]:
    """Format one failure as a bullet item with traceback."""
    location = ""
    if fail.file:
        location = f" (`{fail.file}"
        if fail.line is not None:
            location += f":{fail.line}"
        location += "`)"

    lines = [f"- **{fail.test_id}**{location}"]
    if fail.message:
        # Quote-prefix each line of the message so it stays under the
        # bullet item visually.
        for msg_line in fail.message.splitlines() or [""]:
            lines.append(f"    > {msg_line}")

    if fail.traceback:
        tb = _truncate_to(fail.traceback, _MAX_TRACEBACK_BYTES)
        lines.append("    ```")
        for tb_line in tb.splitlines():
            lines.append(f"    {tb_line}")
        lines.append("    ```")

    return lines


def _truncate_to(text: str, limit_bytes: int) -> str:
    """Truncate ``text`` to at most ``limit_bytes`` UTF-8 bytes, adding
    a "…" sentinel when it bites."""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit_bytes:
        return text
    # Decode the prefix back to text, leaving room for the sentinel.
    sentinel = "\n… (truncated)"
    sentinel_bytes = sentinel.encode("utf-8")
    budget = max(0, limit_bytes - len(sentinel_bytes))
    truncated = encoded[:budget].decode("utf-8", errors="ignore")
    return truncated + sentinel


__all__ = [
    "TestFailure",
    "TestReport",
    "TestStatus",
    "TestSummary",
    "format_for_reviewer",
]
