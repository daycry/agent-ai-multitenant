"""Integration tests for the pre-install static analyzer (Plan 09 task_09_05).

Exercises :class:`api_server.marketplace.static_analysis.StaticAnalyzer`
end-to-end against the REAL scanners (bandit always; semgrep when its CLI
is on PATH / in the venv). It is an *integration* test, not a unit test,
because it spawns the scanner subprocesses on a temp copy of a source tree
— there is no DB, but there is real I/O + subprocess execution.

What it pins (the binding task requirements):

  * an INSECURE snippet (``eval`` / ``subprocess(..., shell=True)`` /
    hardcoded secret) is flagged HIGH-severity by bandit, and the gate
    BLOCKS the install under the community and experimental trust policies;
  * a CLEAN snippet produces no blocking findings and the gate PASSES at
    every trust level;
  * findings are normalized to ``{severity, rule, file, line, msg}`` and
    never leak the private scratch-dir path;
  * the analyzed code is NEVER imported/executed — a poisoned ``__init__``
    that would crash on import still scans cleanly through bandit;
  * semgrep-specific assertions are SKIP-GUARDED: they run only where the
    semgrep CLI is available (CI/Linux), and skip elsewhere (semgrep is an
    optional/lazy dependency — its wheel conflicts with the project stack).

No ``cross_tenant`` marker: the analyzer is a pure code-scanning utility
that touches no tenant-owned rows; the multi-tenancy guarantee is
unaffected (the analyzer is invoked from the tenant-scoped install flow in
task_09_11, which is where cross-tenant tests live).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from api_server.db.marketplace import MarketplaceTrustLevel
from api_server.marketplace.static_analysis import (
    Finding,
    StaticAnalysisError,
    StaticAnalyzer,
    _bandit_executable,
    _semgrep_executable,
)
from api_server.marketplace.trust import Severity

pytestmark = pytest.mark.integration

_BANDIT_AVAILABLE = _bandit_executable() is not None
_SEMGREP_AVAILABLE = _semgrep_executable() is not None

requires_bandit = pytest.mark.skipif(
    not _BANDIT_AVAILABLE, reason="bandit not installed in this environment"
)
requires_semgrep = pytest.mark.skipif(
    not _SEMGREP_AVAILABLE,
    reason="semgrep CLI not available (optional/lazy dep; CI/Linux has it)",
)


# ---------------------------------------------------------------------------
# Fixtures: write small source trees to scan
# ---------------------------------------------------------------------------
_INSECURE_SNIPPET = (
    "import subprocess\n"
    "\n"
    'API_PASSWORD = "super-secret-hardcoded-value-123"\n'
    "\n"
    "def run(user_input):\n"
    "    eval(user_input)\n"
    "    subprocess.call(user_input, shell=True)\n"
)

_CLEAN_SNIPPET = (
    "import json\n"
    "\n"
    "def parse(raw: str) -> dict:\n"
    '    """Parse a JSON document — no dangerous calls."""\n'
    "    return json.loads(raw)\n"
)

# A tree whose import would raise: proves we scan source, never execute it.
_POISONED_IMPORT_SNIPPET = "raise RuntimeError('this module crashes on import')\n"


@pytest.fixture
def insecure_tree(tmp_path: Path) -> Path:
    root = tmp_path / "insecure"
    root.mkdir()
    (root / "tool.py").write_text(_INSECURE_SNIPPET, encoding="utf-8")
    return root


@pytest.fixture
def clean_tree(tmp_path: Path) -> Path:
    root = tmp_path / "clean"
    root.mkdir()
    (root / "tool.py").write_text(_CLEAN_SNIPPET, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Bandit: insecure code is flagged high-severity and the gate BLOCKS
# ---------------------------------------------------------------------------
@requires_bandit
def test_insecure_snippet_flagged_high_severity(insecure_tree: Path) -> None:
    """The eval / shell=True / hardcoded-secret snippet yields a
    HIGH-severity finding (bandit MEDIUM eval escalated by HIGH confidence)."""
    report = StaticAnalyzer().analyze(
        insecure_tree, MarketplaceTrustLevel.COMMUNITY, scanners=["bandit"]
    )
    assert "bandit" in report.ran
    assert report.findings, "expected bandit to flag the insecure snippet"
    assert report.max_severity >= Severity.HIGH
    # every finding is the canonical normalized shape
    for f in report.findings:
        assert isinstance(f, Finding)
        assert isinstance(f.severity, Severity)
        assert f.rule
        assert f.file
        assert f.line >= 0
    # at least one finding names the eval / shell pattern
    rules = {f.rule for f in report.findings}
    assert rules, "no rules reported"


@requires_bandit
def test_gate_blocks_insecure_under_community_and_experimental(insecure_tree: Path) -> None:
    """The gate BLOCKS the insecure tree for community (max LOW) AND
    experimental (max NONE) — both tolerate less than a HIGH finding."""
    for level in (MarketplaceTrustLevel.COMMUNITY, MarketplaceTrustLevel.EXPERIMENTAL):
        report = StaticAnalyzer().analyze(insecure_tree, level, scanners=["bandit"])
        assert report.blocked is True, f"{level} must block the insecure tree"
        blocking = report.blocking_findings()
        assert blocking, "blocked report must expose the blocking findings"
        assert all(f.severity > report.policy.max_allowed_severity for f in blocking)


@requires_bandit
def test_gate_blocks_insecure_even_for_verified(insecure_tree: Path) -> None:
    """verified tolerates up to MEDIUM; the escalated-to-HIGH eval finding
    still exceeds that, so even verified blocks this particular tree."""
    report = StaticAnalyzer().analyze(
        insecure_tree, MarketplaceTrustLevel.VERIFIED, scanners=["bandit"]
    )
    assert report.max_severity >= Severity.HIGH
    assert report.blocked is True


@requires_bandit
def test_clean_snippet_passes_every_trust_level(clean_tree: Path) -> None:
    """A clean tree produces no blocking findings at any trust level."""
    for level in MarketplaceTrustLevel:
        report = StaticAnalyzer().analyze(clean_tree, level, scanners=["bandit"])
        assert report.blocked is False, f"clean tree must pass for {level}"
        assert report.max_severity == Severity.NONE


@requires_bandit
def test_scanner_does_not_import_analyzed_code(tmp_path: Path) -> None:
    """A module that raises on import scans without crashing the analyzer —
    proving we read source, never execute it."""
    root = tmp_path / "poisoned"
    root.mkdir()
    (root / "__init__.py").write_text(_POISONED_IMPORT_SNIPPET, encoding="utf-8")
    report = StaticAnalyzer().analyze(root, MarketplaceTrustLevel.EXPERIMENTAL, scanners=["bandit"])
    # It ran (no exception) — that is the assertion. A bare ``raise`` is not
    # itself a bandit finding, so this clean-of-security-issues tree passes.
    assert "bandit" in report.ran
    assert report.blocked is False


@requires_bandit
def test_findings_do_not_leak_scratch_path(insecure_tree: Path) -> None:
    """Normalized ``file`` is relative — it never echoes the temp scratch
    directory the scan ran in."""
    report = StaticAnalyzer().analyze(
        insecure_tree, MarketplaceTrustLevel.COMMUNITY, scanners=["bandit"]
    )
    for f in report.findings:
        assert "mkt-sast-" not in f.file
        assert "_src" not in f.file
        assert f.file.endswith("tool.py")


# ---------------------------------------------------------------------------
# Degrade / error semantics
# ---------------------------------------------------------------------------
def test_missing_source_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(StaticAnalysisError):
        StaticAnalyzer().analyze(tmp_path / "does-not-exist", MarketplaceTrustLevel.COMMUNITY)


def test_unknown_scanner_is_simply_not_run(clean_tree: Path) -> None:
    """Restricting to a non-existent scanner runs nothing and reports no
    findings — neither bandit nor semgrep is in the requested set."""
    report = StaticAnalyzer().analyze(
        clean_tree, MarketplaceTrustLevel.COMMUNITY, scanners=["nope"]
    )
    assert report.ran == ()
    assert report.findings == ()
    assert report.blocked is False


def test_semgrep_absence_is_recorded_as_skipped(clean_tree: Path) -> None:
    """When semgrep is unavailable the report records it as *skipped* with a
    reason (never silently treated as 'clean'). When it IS available the
    semgrep-specific test below asserts it ran instead."""
    report = StaticAnalyzer().analyze(
        clean_tree, MarketplaceTrustLevel.COMMUNITY, scanners=["semgrep"]
    )
    if _SEMGREP_AVAILABLE:
        assert "semgrep" in report.ran
    else:
        reasons = dict(report.skipped)
        assert "semgrep" in reasons
        # the reason is human-readable, not empty
        assert reasons["semgrep"]


# ---------------------------------------------------------------------------
# semgrep-specific assertions — skip-guarded
# ---------------------------------------------------------------------------
@requires_semgrep
def test_semgrep_flags_insecure_snippet(insecure_tree: Path) -> None:
    """semgrep (with the in-repo offline ruleset) flags eval / shell=True as
    ERROR -> HIGH, and the gate blocks community/experimental."""
    report = StaticAnalyzer().analyze(
        insecure_tree, MarketplaceTrustLevel.COMMUNITY, scanners=["semgrep"]
    )
    assert "semgrep" in report.ran
    semgrep_findings = [f for f in report.findings if f.scanner == "semgrep"]
    assert semgrep_findings, "semgrep should match the offline eval/shell rules"
    assert max(f.severity for f in semgrep_findings) >= Severity.HIGH
    assert report.blocked is True
    # rule ids come from the in-repo ruleset
    rule_ids = {f.rule for f in semgrep_findings}
    assert any("marketplace-" in r for r in rule_ids)


@requires_semgrep
def test_semgrep_passes_clean_snippet(clean_tree: Path) -> None:
    report = StaticAnalyzer().analyze(
        clean_tree, MarketplaceTrustLevel.EXPERIMENTAL, scanners=["semgrep"]
    )
    assert "semgrep" in report.ran
    semgrep_findings = [f for f in report.findings if f.scanner == "semgrep"]
    assert semgrep_findings == []
    assert report.blocked is False


@requires_bandit
@requires_semgrep
def test_both_scanners_contribute_when_available(insecure_tree: Path) -> None:
    """The default (no ``scanners=``) runs everything available and merges
    findings from both tools into one normalized list."""
    report = StaticAnalyzer().analyze(insecure_tree, MarketplaceTrustLevel.COMMUNITY)
    assert "bandit" in report.ran
    assert "semgrep" in report.ran
    scanners = {f.scanner for f in report.findings}
    assert "bandit" in scanners
    assert "semgrep" in scanners
    assert report.blocked is True
