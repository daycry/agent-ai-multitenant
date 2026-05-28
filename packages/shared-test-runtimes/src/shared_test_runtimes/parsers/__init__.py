"""Output parsers per test runner (Plan 06 task_06_14).

Each parser reads the raw output a test runtime produced (a JUnit XML
file, Jest's --json output, TAP stream, etc.) and returns a canonical
:class:`shared_test_runtimes.test_report.TestReport`. The worker
iterates the runtime template's ``output_parsers`` tuple in order and
keeps the *first* one whose ``parse(...)`` returns a non-``None``
report — that way a runtime can list a primary parser plus
``raw_text`` as a fallback.

Parsers shipped in Fase D:

  * :mod:`junit_xml`        — generic JUnit XML (pytest, mocha, …).
  * :mod:`jest_json`        — Jest's ``--json`` output.
  * :mod:`playwright_json`  — Playwright's JSON reporter.
  * :mod:`surefire_xml`     — Maven Surefire (almost-but-not-quite
                               JUnit XML).
  * :mod:`tap`              — TAP 13 stream (generic-shell).
  * :mod:`trx`              — .NET ``dotnet test --logger trx``.
  * :mod:`raw_text`         — fallback: every runtime falls back to
                               this when its primary parser produces
                               nothing.

Each parser exposes a single ``parse(text: str, *, runtime: str) ->
TestReport | None`` function. ``None`` means "I don't recognise this
output"; the worker tries the next parser.
"""

from __future__ import annotations

from collections.abc import Callable

from shared_test_runtimes.parsers import (
    jest_json,
    junit_xml,
    playwright_json,
    raw_text,
    surefire_xml,
    tap,
    trx,
)
from shared_test_runtimes.test_report import TestReport

# Registry mapping parser id (closed set in
# shared_test_runtimes.types.OutputParser) to the parse function.
ParseFn = Callable[..., "TestReport | None"]

PARSERS: dict[str, ParseFn] = {
    "junit_xml": junit_xml.parse,
    "jest_json": jest_json.parse,
    "playwright_json": playwright_json.parse,
    "surefire_xml": surefire_xml.parse,
    "tap": tap.parse,
    "trx": trx.parse,
    "raw_text": raw_text.parse,
}


def get(parser_id: str) -> ParseFn:
    """Resolve a parser by id. Raises :class:`KeyError` on unknown."""
    try:
        return PARSERS[parser_id]
    except KeyError as exc:
        known = ", ".join(sorted(PARSERS))
        raise KeyError(f"unknown parser {parser_id!r}; known: {known}") from exc


def try_parsers(
    text: str,
    *,
    runtime: str,
    parsers: tuple[str, ...],
) -> TestReport:
    """Try each parser in order; first non-None wins.

    Always returns a :class:`TestReport` — :mod:`raw_text` is the
    catch-all that never returns ``None``. If ``parsers`` doesn't
    include ``raw_text``, this falls back to it explicitly to
    guarantee the contract.
    """
    for parser_id in parsers:
        result = PARSERS[parser_id](text, runtime=runtime)
        if result is not None:
            return result
    # Belt and braces — raw_text never returns None.
    result = raw_text.parse(text, runtime=runtime)
    assert result is not None
    return result


__all__ = ["PARSERS", "ParseFn", "get", "try_parsers"]
