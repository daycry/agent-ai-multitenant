"""Maven Surefire XML parser (Plan 06 task_06_14).

Surefire's XML is a near-superset of generic JUnit XML — same tags,
slightly different attributes (``time`` vs ``time``, ``classname``
always present). The cheap implementation: delegate to the JUnit
parser and override the runtime label.
"""

from __future__ import annotations

from shared_test_runtimes.parsers import junit_xml
from shared_test_runtimes.test_report import TestReport


def parse(text: str, *, runtime: str) -> TestReport | None:
    return junit_xml.parse(text, runtime=runtime)


__all__ = ["parse"]
