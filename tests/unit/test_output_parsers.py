"""Unit tests for the output parsers (Plan 06 task_06_14)."""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# junit_xml
# ---------------------------------------------------------------------------


def test_junit_xml_minimal_passed() -> None:
    from shared_test_runtimes.parsers import junit_xml

    xml = """<?xml version="1.0"?>
<testsuites>
  <testsuite name="tests.test_x" tests="3" failures="0" errors="0" skipped="0" time="0.5">
    <testcase classname="tests.test_x" name="test_a" time="0.1"/>
    <testcase classname="tests.test_x" name="test_b" time="0.2"/>
    <testcase classname="tests.test_x" name="test_c" time="0.2"/>
  </testsuite>
</testsuites>"""
    r = junit_xml.parse(xml, runtime="python-pytest")
    assert r is not None
    assert r.status == "passed"
    assert r.summary.total == 3
    assert r.summary.passed == 3
    assert r.summary.failed == 0


def test_junit_xml_with_failure() -> None:
    from shared_test_runtimes.parsers import junit_xml

    xml = """<testsuite name="s" tests="2" failures="1">
  <testcase classname="A" name="test_pass"/>
  <testcase classname="A" name="test_fail" file="a.py" line="42">
    <failure message="assertion failed">
Traceback line 1
Traceback line 2
    </failure>
  </testcase>
</testsuite>"""
    r = junit_xml.parse(xml, runtime="python-pytest")
    assert r is not None
    assert r.status == "failed"
    assert r.summary.failed == 1
    assert len(r.failures) == 1
    fail = r.failures[0]
    assert fail.test_id == "A::test_fail"
    assert fail.file == "a.py"
    assert fail.line == 42
    assert fail.message == "assertion failed"
    assert "Traceback line 1" in (fail.traceback or "")


def test_junit_xml_returns_none_on_non_xml() -> None:
    from shared_test_runtimes.parsers import junit_xml

    assert junit_xml.parse("hello world", runtime="python-pytest") is None
    assert junit_xml.parse("<broken>", runtime="python-pytest") is None


# ---------------------------------------------------------------------------
# jest_json
# ---------------------------------------------------------------------------


def test_jest_json_passed() -> None:
    from shared_test_runtimes.parsers import jest_json

    payload = {
        "numTotalTests": 5,
        "numPassedTests": 5,
        "numFailedTests": 0,
        "numPendingTests": 0,
        "success": True,
        "testResults": [
            {
                "name": "src/foo.test.ts",
                "testResults": [
                    {"fullName": "foo > test 1", "status": "passed"},
                ],
            }
        ],
    }
    r = jest_json.parse(json.dumps(payload), runtime="node-jest")
    assert r is not None
    assert r.status == "passed"
    assert r.summary.passed == 5


def test_jest_json_with_failure() -> None:
    from shared_test_runtimes.parsers import jest_json

    payload = {
        "numTotalTests": 2,
        "numPassedTests": 1,
        "numFailedTests": 1,
        "numPendingTests": 0,
        "success": False,
        "testResults": [
            {
                "name": "src/auth.test.ts",
                "testResults": [
                    {"fullName": "auth > pass", "status": "passed"},
                    {
                        "fullName": "auth > fail",
                        "status": "failed",
                        "failureMessages": ["Expected 200 but got 500\n  at Object.<anonymous>"],
                        "location": {"line": 23},
                        "duration": 42,
                    },
                ],
            }
        ],
    }
    r = jest_json.parse(json.dumps(payload), runtime="node-jest")
    assert r is not None
    assert r.status == "failed"
    assert len(r.failures) == 1
    f = r.failures[0]
    assert f.test_id == "auth > fail"
    assert f.file == "src/auth.test.ts"
    assert f.line == 23
    assert f.duration_ms == 42


def test_jest_json_returns_none_for_non_jest_json() -> None:
    from shared_test_runtimes.parsers import jest_json

    # Valid JSON but not Jest shape.
    assert jest_json.parse('{"foo": "bar"}', runtime="node-jest") is None
    assert jest_json.parse("not json", runtime="node-jest") is None


# ---------------------------------------------------------------------------
# playwright_json
# ---------------------------------------------------------------------------


def test_playwright_json_passed() -> None:
    from shared_test_runtimes.parsers import playwright_json

    payload = {
        "config": {},
        "suites": [
            {
                "title": "auth",
                "specs": [
                    {
                        "title": "login",
                        "tests": [{"results": [{"status": "passed", "duration": 100}]}],
                    }
                ],
                "suites": [],
            }
        ],
    }
    r = playwright_json.parse(json.dumps(payload), runtime="node-playwright")
    assert r is not None
    assert r.status == "passed"
    assert r.summary.passed == 1


def test_playwright_json_with_failure() -> None:
    from shared_test_runtimes.parsers import playwright_json

    payload = {
        "config": {},
        "suites": [
            {
                "specs": [
                    {
                        "title": "checkout",
                        "file": "tests/checkout.spec.ts",
                        "line": 10,
                        "tests": [
                            {
                                "results": [
                                    {
                                        "status": "failed",
                                        "duration": 250,
                                        "errors": [
                                            {
                                                "message": "Click target not found",
                                                "stack": "Error: ...",
                                            }
                                        ],
                                    }
                                ]
                            }
                        ],
                    }
                ]
            }
        ],
    }
    r = playwright_json.parse(json.dumps(payload), runtime="node-playwright")
    assert r is not None
    assert r.status == "failed"
    assert r.failures[0].test_id == "checkout"
    assert r.failures[0].file == "tests/checkout.spec.ts"


# ---------------------------------------------------------------------------
# surefire_xml (delegates to junit_xml; smoke test)
# ---------------------------------------------------------------------------


def test_surefire_delegates_to_junit() -> None:
    from shared_test_runtimes.parsers import surefire_xml

    xml = '<testsuite name="s" tests="1" failures="0"><testcase name="t"/></testsuite>'
    r = surefire_xml.parse(xml, runtime="java-maven")
    assert r is not None
    assert r.status == "passed"
    assert r.runtime == "java-maven"


# ---------------------------------------------------------------------------
# tap
# ---------------------------------------------------------------------------


def test_tap_passed() -> None:
    from shared_test_runtimes.parsers import tap

    text = "1..3\nok 1 - first\nok 2 - second\nok 3 - third\n"
    r = tap.parse(text, runtime="generic-shell")
    assert r is not None
    assert r.status == "passed"
    assert r.summary.total == 3
    assert r.summary.passed == 3


def test_tap_with_failure_and_yaml() -> None:
    from shared_test_runtimes.parsers import tap

    text = (
        "1..2\n"
        "ok 1 - first\n"
        "not ok 2 - second\n"
        "  ---\n"
        "  message: expected 1 but got 2\n"
        "  stack: |\n"
        "    at line 10\n"
        "  ...\n"
    )
    r = tap.parse(text, runtime="generic-shell")
    assert r is not None
    assert r.status == "failed"
    assert r.summary.failed == 1
    assert "expected 1" in (r.failures[0].traceback or "")


def test_tap_skip_directive() -> None:
    from shared_test_runtimes.parsers import tap

    text = "1..2\nok 1 - first\nok 2 - second # SKIP not on linux\n"
    r = tap.parse(text, runtime="generic-shell")
    assert r is not None
    assert r.summary.skipped == 1
    assert r.summary.passed == 1


def test_tap_returns_none_for_non_tap() -> None:
    from shared_test_runtimes.parsers import tap

    assert tap.parse("hello world\nthis is not TAP\n", runtime="x") is None


# ---------------------------------------------------------------------------
# trx
# ---------------------------------------------------------------------------


def test_trx_passed() -> None:
    from shared_test_runtimes.parsers import trx

    xml = """<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">
  <ResultSummary outcome="Completed">
    <Counters total="3" executed="3" passed="3" failed="0" error="0"
              inconclusive="0" notExecuted="0" timeout="0"/>
  </ResultSummary>
  <Results/>
</TestRun>"""
    r = trx.parse(xml, runtime="dotnet-test")
    assert r is not None
    assert r.status == "passed"
    assert r.summary.passed == 3


def test_trx_with_failure() -> None:
    from shared_test_runtimes.parsers import trx

    xml = """<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">
  <ResultSummary outcome="Failed">
    <Counters total="2" executed="2" passed="1" failed="1" error="0"
              inconclusive="0" notExecuted="0" timeout="0"/>
  </ResultSummary>
  <Results>
    <UnitTestResult testName="MyTests.TestA" outcome="Passed"/>
    <UnitTestResult testName="MyTests.TestB" outcome="Failed" duration="00:00:01.234">
      <Output><ErrorInfo>
        <Message>Expected true but was false</Message>
        <StackTrace>at MyTests.TestB() in Foo.cs:line 42</StackTrace>
      </ErrorInfo></Output>
    </UnitTestResult>
  </Results>
</TestRun>"""
    r = trx.parse(xml, runtime="dotnet-test")
    assert r is not None
    assert r.status == "failed"
    assert r.summary.failed == 1
    assert r.failures[0].test_id == "MyTests.TestB"
    assert r.failures[0].duration_ms == 1234


# ---------------------------------------------------------------------------
# raw_text (always returns a report)
# ---------------------------------------------------------------------------


def test_raw_text_with_no_failure_marker() -> None:
    from shared_test_runtimes.parsers import raw_text

    r = raw_text.parse("everything is fine\nno problems here\n", runtime="generic-shell")
    assert r is not None
    assert r.status == "passed"


def test_raw_text_with_failure_marker() -> None:
    from shared_test_runtimes.parsers import raw_text

    r = raw_text.parse("Traceback (most recent call last):\nFooError", runtime="x")
    assert r is not None
    assert r.status == "failed"
    assert "Traceback" in r.logs_excerpt


def test_raw_text_truncates_long_output() -> None:
    from shared_test_runtimes.parsers import raw_text

    long = "line\n" * 10_000  # ~50 KiB
    r = raw_text.parse(long, runtime="x")
    assert r is not None
    # Cap is 2 KiB + small sentinel.
    assert len(r.logs_excerpt.encode("utf-8")) <= 2048 + 64


# ---------------------------------------------------------------------------
# try_parsers — registry
# ---------------------------------------------------------------------------


def test_try_parsers_first_match_wins() -> None:
    from shared_test_runtimes.parsers import try_parsers

    xml = '<testsuite name="s" tests="1"><testcase name="t"/></testsuite>'
    r = try_parsers(xml, runtime="python-pytest", parsers=("junit_xml", "raw_text"))
    # Should be the JUnit parser's output (status passed because 1
    # testcase with no failures), not raw_text's failure heuristic.
    assert r.status == "passed"
    assert r.summary.total == 1


def test_try_parsers_falls_back_to_raw_text() -> None:
    from shared_test_runtimes.parsers import try_parsers

    r = try_parsers("just some stdout", runtime="x", parsers=("junit_xml", "raw_text"))
    assert r.status == "passed"  # no fail markers
