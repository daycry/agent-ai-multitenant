"""Pre-install static analysis of a skill/tool source tree (Plan 09 task_09_05).

Before a listing is installed (task_09_11), its source tree is scanned for
dangerous code. Two scanners run, both as **subprocesses on a temporary
copy** of the code — the analyzed code is NEVER imported or executed, so a
malicious ``__init__`` / decorator cannot run inside the api-server:

  * **Bandit** (``pip install bandit``) — the Python AST security linter.
    A clean, pure-Python wheel; it is the primary scanner and always
    available wherever the api-server runs.
  * **semgrep** (``pip install semgrep``) — generic multi-language pattern
    matching with a small ruleset shipped in-repo (:data:`SEMGREP_RULES`).
    semgrep's published wheel hard-pins ``opentelemetry`` / ``protobuf``
    versions that CONFLICT with this project's stack (installing it
    downgrades OTel and breaks the api-server import). It is therefore an
    **optional / lazy** dependency, never added to ``pyproject``: the
    analyzer imports it lazily (locates the ``semgrep`` CLI on ``PATH`` /
    in the venv ``Scripts``) and degrades to a typed *unavailable* result
    when it is absent — exactly the xmlsec / python3-saml precedent
    (:mod:`api_server.auth.sso.saml`). Bandit alone is sufficient for the
    Python skills/tools that make up the catalog; semgrep is defence in
    depth where the environment (CI/Linux) ships it.

The scanners' raw output is normalized into a flat list of
:class:`Finding` records ``{severity, rule, file, line, msg}`` plus the
scanner that produced it. A :class:`StaticAnalysisReport` then answers the
one question the install flow asks: does any finding exceed the trust
policy's ``max_allowed_severity`` (from task_09_04)? If so the gate BLOCKS
the install.

Severity normalization (the subtle bit): bandit rates ``eval`` as
MEDIUM-severity / HIGH-confidence and ``subprocess(..., shell=True)`` as
LOW / HIGH by default. A *high-confidence* dangerous pattern is a
high-risk finding regardless of bandit's conservative base severity, so we
**escalate by confidence**: a HIGH-confidence finding is bumped one
severity rung (MEDIUM->HIGH, LOW->MEDIUM). This makes ``eval`` /
``shell=True`` / a hardcoded secret block reliably under the community and
experimental policies, while a HIGH-confidence MEDIUM stays MEDIUM-or-above
so even ``verified`` (max MEDIUM) is unaffected by genuinely low-risk
nits. The mapping is data (:data:`_BANDIT_SEVERITY` /
:data:`_CONFIDENCE_ESCALATION`) so it stays testable and auditable.

No new *required* dependency, no network (bandit is offline; the semgrep
ruleset is local), no import of the analyzed code.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess  # - we run trusted scanners on a temp copy, never the analyzed code
import sys
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import structlog

from api_server.db.marketplace import MarketplaceTrustLevel
from api_server.marketplace.trust import (
    Severity,
    TrustPolicy,
    trust_policy,
)

_log = structlog.get_logger("marketplace.static_analysis")

# Wall-clock cap per scanner. A scan that runs longer than this is treated
# as a failure (we never silently pass an un-scanned tree).
DEFAULT_SCAN_TIMEOUT_S = 120

# Files we copy into the scratch tree. We scan source, never vendored
# binaries / caches, so the scan is fast and deterministic.
_IGNORED_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        ".venv",
        "venv",
        ".tox",
    }
)


# =============================================================================
# Normalized finding + report
# =============================================================================
@dataclass(frozen=True, slots=True)
class Finding:
    """One normalized static-analysis finding.

    The canonical ``{severity, rule, file, line, msg}`` shape plus the
    ``scanner`` that produced it (so a report can be filtered/grouped by
    tool). ``file`` is relative to the scanned root so it never leaks the
    scratch directory path back to the caller.
    """

    severity: Severity
    rule: str
    file: str
    line: int
    msg: str
    scanner: str


@dataclass(frozen=True, slots=True)
class StaticAnalysisReport:
    """The verdict of a scan run against a trust policy.

    ``findings`` is the flat, scanner-agnostic list. ``ran`` names the
    scanners that actually executed; ``skipped`` names those that were
    unavailable (e.g. semgrep absent) with the reason — so the install
    flow + audit log can record *why* a scanner did not contribute,
    rather than silently treating "absent" as "clean".
    """

    findings: tuple[Finding, ...]
    policy: TrustPolicy
    ran: tuple[str, ...]
    skipped: tuple[tuple[str, str], ...] = ()

    @property
    def max_severity(self) -> Severity:
        """Highest severity across all findings (``NONE`` when clean)."""
        return max((f.severity for f in self.findings), default=Severity.NONE)

    @property
    def blocked(self) -> bool:
        """``True`` when any finding exceeds the policy's tolerated severity.

        The gate: a finding strictly above ``max_allowed_severity`` blocks
        the install. ``experimental`` tolerates ``NONE`` so ANY finding
        blocks; ``community`` tolerates ``LOW``; ``verified`` ``MEDIUM``.
        """
        return self.max_severity > self.policy.max_allowed_severity

    def blocking_findings(self) -> tuple[Finding, ...]:
        """The subset of findings that individually exceed the policy."""
        threshold = self.policy.max_allowed_severity
        return tuple(f for f in self.findings if f.severity > threshold)


class StaticAnalysisError(RuntimeError):
    """A scanner failed to run (timeout, crash, unparseable output).

    Distinct from "scanner unavailable" (a degrade, recorded in
    :attr:`StaticAnalysisReport.skipped`) and from "scanner ran and found
    issues" (findings). A hard error means we could not establish a verdict
    and the caller MUST treat the tree as unsafe (fail closed)."""


# =============================================================================
# Bandit (Python) — primary scanner
# =============================================================================
# Bandit's own three-level severity, mapped onto our ordered Severity.
_BANDIT_SEVERITY: dict[str, Severity] = {
    "LOW": Severity.LOW,
    "MEDIUM": Severity.MEDIUM,
    "HIGH": Severity.HIGH,
    "UNDEFINED": Severity.LOW,
}

# How much a finding's confidence escalates its severity (in rungs). A
# HIGH-confidence dangerous pattern is high-risk even when bandit's base
# severity is conservative (eval => MEDIUM/HIGH, shell=True => LOW/HIGH).
_CONFIDENCE_ESCALATION: dict[str, int] = {
    "HIGH": 1,
    "MEDIUM": 0,
    "LOW": 0,
    "UNDEFINED": 0,
}


def _escalate(base: Severity, confidence: str) -> Severity:
    """Bump ``base`` up by the confidence escalation, capped at CRITICAL."""
    bumped = int(base) + _CONFIDENCE_ESCALATION.get(confidence.upper(), 0)
    return Severity(min(bumped, int(Severity.CRITICAL)))


def _bandit_executable() -> list[str] | None:
    """Resolve a runnable bandit invocation, or ``None`` if unavailable.

    Prefer ``python -m bandit`` (uses the same interpreter as the
    api-server, so the wheel is guaranteed importable) and fall back to a
    ``bandit`` on PATH. Returns the argv prefix.
    """
    try:
        import bandit  # noqa: F401  (availability probe only — never used directly)
    except ImportError:
        bandit_bin = shutil.which("bandit")
        return [bandit_bin] if bandit_bin else None
    return [sys.executable, "-m", "bandit"]


def _parse_bandit(stdout: str) -> list[Finding]:
    """Parse bandit ``-f json`` output into normalized findings.

    Bandit exits 1 when it finds issues (0 when clean), so the caller
    keys off the JSON, not the exit code. Each ``results[*]`` entry is one
    finding; severity is escalated by confidence (see module docstring)."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise StaticAnalysisError(f"bandit produced unparseable JSON: {exc}") from exc
    findings: list[Finding] = []
    for item in payload.get("results", []):
        base = _BANDIT_SEVERITY.get(str(item.get("issue_severity", "")).upper(), Severity.LOW)
        confidence = str(item.get("issue_confidence", "UNDEFINED"))
        findings.append(
            Finding(
                severity=_escalate(base, confidence),
                rule=str(item.get("test_id") or item.get("test_name") or "bandit"),
                file=_relativize(str(item.get("filename", ""))),
                line=int(item.get("line_number", 0) or 0),
                msg=str(item.get("issue_text", "")).strip(),
                scanner="bandit",
            )
        )
    return findings


# =============================================================================
# semgrep (generic) — optional / lazy, degrades to unavailable
# =============================================================================
# A small, OFFLINE ruleset shipped in-repo so the scan is deterministic and
# needs no network (the public ``p/python`` registry config does). Covers
# the canonical dangerous patterns the gate must catch in any language
# semgrep parses; bandit covers Python in more depth.
SEMGREP_RULES: dict[str, object] = {
    "rules": [
        {
            "id": "marketplace-python-eval",
            "languages": ["python"],
            "severity": "ERROR",
            "message": "Use of eval() on untrusted input is a code-execution risk.",
            "patterns": [{"pattern": "eval(...)"}],
        },
        {
            "id": "marketplace-python-exec",
            "languages": ["python"],
            "severity": "ERROR",
            "message": "Use of exec() is a code-execution risk.",
            "patterns": [{"pattern": "exec(...)"}],
        },
        {
            "id": "marketplace-subprocess-shell-true",
            "languages": ["python"],
            "severity": "ERROR",
            "message": "subprocess with shell=True enables shell injection.",
            "patterns": [{"pattern": "subprocess.$FUNC(..., shell=True, ...)"}],
        },
    ]
}

# semgrep's own four-level severity onto our ordered Severity. ERROR is the
# blocking tier; INFO is informational only.
_SEMGREP_SEVERITY: dict[str, Severity] = {
    "ERROR": Severity.HIGH,
    "WARNING": Severity.MEDIUM,
    "INFO": Severity.LOW,
    "INVENTORY": Severity.LOW,
    "EXPERIMENT": Severity.LOW,
}


def _semgrep_executable() -> list[str] | None:
    """Resolve a runnable semgrep invocation, or ``None`` if unavailable.

    semgrep ships an OCaml ``semgrep-core`` binary alongside the Python
    package; ``python -m semgrep`` is deprecated, so we look for the
    ``semgrep`` console-script on PATH first, then in the running
    interpreter's ``Scripts`` / ``bin`` dir (the venv that runs the
    api-server). Returns ``None`` when not found — the caller degrades.
    """
    on_path = shutil.which("semgrep")
    if on_path:
        return [on_path]
    bindir = Path(sys.executable).parent
    for candidate in ("semgrep.exe", "semgrep"):
        exe = bindir / candidate
        if exe.exists():
            return [str(exe)]
    return None


def _parse_semgrep(stdout: str) -> list[Finding]:
    """Parse semgrep ``--json`` output into normalized findings."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise StaticAnalysisError(f"semgrep produced unparseable JSON: {exc}") from exc
    findings: list[Finding] = []
    for item in payload.get("results", []):
        extra = item.get("extra", {}) or {}
        sev_str = str(extra.get("severity", "INFO")).upper()
        start = item.get("start", {}) or {}
        findings.append(
            Finding(
                severity=_SEMGREP_SEVERITY.get(sev_str, Severity.LOW),
                rule=str(item.get("check_id", "semgrep")),
                file=_relativize(str(item.get("path", ""))),
                line=int(start.get("line", 0) or 0),
                msg=str(extra.get("message", "")).strip(),
                scanner="semgrep",
            )
        )
    return findings


# =============================================================================
# The analyzer
# =============================================================================
def _relativize(path: str) -> str:
    """Reduce an absolute scratch path to a leaf-ish relative form.

    The scanners report paths inside our private temp dir; we never want
    to leak that back to the caller. We keep only the path tail after the
    temp root marker, falling back to the basename.
    """
    norm = path.replace("\\", "/").lstrip("./")
    marker = "/_src/"
    if marker in norm:
        return norm.split(marker, 1)[1]
    return os.path.basename(norm) or norm


class StaticAnalyzer:
    """Runs the pre-install scanners on a copy of a skill/tool source tree.

    Stateless and cheap to construct. The single entry point
    :meth:`analyze` copies the tree into a private temp dir, runs each
    available scanner as a subprocess against the COPY, normalizes the
    findings, and returns a :class:`StaticAnalysisReport` carrying the
    gate verdict for the given trust level.
    """

    def __init__(self, *, timeout_s: int = DEFAULT_SCAN_TIMEOUT_S) -> None:
        self._timeout_s = timeout_s

    # --- public ---------------------------------------------------------
    def analyze(
        self,
        source_dir: str | os.PathLike[str],
        trust_level: MarketplaceTrustLevel | str,
        *,
        scanners: Sequence[str] | None = None,
    ) -> StaticAnalysisReport:
        """Scan ``source_dir`` and return the report for ``trust_level``.

        ``scanners`` restricts which scanners run (default: all available);
        bandit always runs when available, semgrep degrades to *skipped*
        when its CLI is absent. Raises :class:`StaticAnalysisError` when a
        scanner that IS available fails to run or produce parseable output
        — the caller must fail closed.
        """
        policy = trust_policy(trust_level)
        src = Path(source_dir)
        if not src.is_dir():
            raise StaticAnalysisError(f"source_dir is not a directory: {source_dir!r}")

        wanted = set(scanners) if scanners is not None else {"bandit", "semgrep"}
        findings: list[Finding] = []
        ran: list[str] = []
        skipped: list[tuple[str, str]] = []

        with tempfile.TemporaryDirectory(prefix="mkt-sast-") as tmp:
            scan_root = Path(tmp) / "_src"
            self._copy_source(src, scan_root)

            if "bandit" in wanted:
                argv = _bandit_executable()
                if argv is None:
                    skipped.append(("bandit", "bandit not installed"))
                else:
                    findings.extend(self._run_bandit(argv, scan_root))
                    ran.append("bandit")

            if "semgrep" in wanted:
                argv = _semgrep_executable()
                if argv is None:
                    skipped.append(("semgrep", "semgrep CLI not found on PATH or in venv"))
                else:
                    findings.extend(self._run_semgrep(argv, scan_root))
                    ran.append("semgrep")

        report = StaticAnalysisReport(
            findings=tuple(findings),
            policy=policy,
            ran=tuple(ran),
            skipped=tuple(skipped),
        )
        _log.info(
            "marketplace.static_analysis.done",
            trust_level=str(policy.level),
            ran=report.ran,
            skipped=[name for name, _ in report.skipped],
            findings=len(report.findings),
            max_severity=report.max_severity.name,
            blocked=report.blocked,
        )
        return report

    # --- copy -----------------------------------------------------------
    @staticmethod
    def _copy_source(src: Path, dst: Path) -> None:
        """Copy the source tree, skipping VCS / cache / vendored dirs.

        ``copytree`` with an ignore predicate — we never follow symlinks
        (a malicious tree could symlink to ``/etc`` or the host fs); the
        copy is what the scanners read, never the original."""

        def _ignore(_dir: str, names: list[str]) -> set[str]:
            return {n for n in names if n in _IGNORED_DIR_NAMES}

        shutil.copytree(src, dst, ignore=_ignore, symlinks=False, ignore_dangling_symlinks=True)

    # --- scanner runs ---------------------------------------------------
    def _run_bandit(self, argv: list[str], scan_root: Path) -> list[Finding]:
        """Run bandit recursively over ``scan_root`` and parse the JSON."""
        cmd = [*argv, "-r", "-f", "json", "-q", str(scan_root)]
        proc = self._exec(cmd, scanner="bandit")
        # bandit exits 1 when it finds issues, 0 when clean; >1 is a real
        # error. Either way the JSON on stdout is authoritative.
        if proc.returncode not in (0, 1):
            raise StaticAnalysisError(
                f"bandit failed (rc={proc.returncode}): {proc.stderr.strip()[:500]}"
            )
        return _parse_bandit(proc.stdout)

    def _run_semgrep(self, argv: list[str], scan_root: Path) -> list[Finding]:
        """Run semgrep with the in-repo offline ruleset and parse the JSON."""
        # Write the ruleset to a temp file alongside the scratch tree so
        # the scan needs no network (no registry ``--config p/...``).
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", prefix="mkt-semgrep-rules-", delete=False, encoding="utf-8"
        ) as rules_file:
            import yaml

            yaml.safe_dump(SEMGREP_RULES, rules_file)
            rules_path = rules_file.name
        try:
            cmd = [
                *argv,
                "--config",
                rules_path,
                "--json",
                "--quiet",
                "--no-git-ignore",
                "--disable-version-check",
                "--metrics",
                "off",
                str(scan_root),
            ]
            proc = self._exec(cmd, scanner="semgrep")
            if proc.returncode not in (0, 1):
                raise StaticAnalysisError(
                    f"semgrep failed (rc={proc.returncode}): {proc.stderr.strip()[:500]}"
                )
            return _parse_semgrep(proc.stdout)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(rules_path)

    def _exec(self, cmd: list[str], *, scanner: str) -> subprocess.CompletedProcess[str]:
        """Run a scanner subprocess with a wall-clock cap, never a shell.

        ``shell=False`` (argv list) so nothing in the analyzed path is
        shell-interpreted; the scanner reads files, it never executes the
        analyzed code. A timeout is a hard error (fail closed)."""
        try:
            return subprocess.run(  # - trusted scanner argv, shell=False, no analyzed code executed
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise StaticAnalysisError(f"{scanner} timed out after {self._timeout_s}s") from exc
        except OSError as exc:  # pragma: no cover - defensive (exe vanished mid-run)
            raise StaticAnalysisError(f"{scanner} could not be executed: {exc}") from exc


def severities_of(findings: Iterable[Finding]) -> list[Severity]:
    """Convenience: the severities of ``findings`` (used by callers/tests)."""
    return [f.severity for f in findings]


__all__ = [
    "DEFAULT_SCAN_TIMEOUT_S",
    "SEMGREP_RULES",
    "Finding",
    "StaticAnalysisError",
    "StaticAnalysisReport",
    "StaticAnalyzer",
    "severities_of",
]
