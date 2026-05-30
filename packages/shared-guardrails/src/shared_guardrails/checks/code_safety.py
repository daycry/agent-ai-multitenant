"""Code-safety guardrail (Plan 11, Phase B — task_11_08).

Registers the ``code_safety`` guardrail type. It statically detects
dangerous constructs in *generated* code — code the model wrote
(``post_llm``) or that surfaces in a tool result (``post_tool``, e.g. a
file the agent just wrote) — so the host can block it before it ever
runs.

Hooks
-----
Primary hooks are ``post_llm`` (the model emitted a code block) and
``post_tool`` (a tool returned a file / patch / generated snippet), but
it works at any hook since it only reads ``GuardrailContext.primary_text()``.

Detection strategy (pure Python — no mandatory heavy dependency)
----------------------------------------------------------------
Two complementary, self-contained analyzers, both deterministic and
importable everywhere (incl. CI):

  * **Python AST analysis.** When the snippet parses as Python, walk the
    tree and flag dangerous *constructs* structurally (not by substring),
    so ``eval(...)`` is caught but ``"eval" in s`` is not:
      - ``eval`` / ``exec`` / ``compile`` calls,
      - ``os.system`` and ``subprocess.*`` with ``shell=True``,
      - dynamic import (``__import__`` / ``importlib.import_module``),
      - ``pickle.loads`` / ``yaml.load`` (unsafe deserialization),
      - ``open(..., "w"|"a"|...)`` / ``os.remove`` / ``shutil.rmtree``
        writing outside the workspace (best-effort: absolute or
        parent-traversal paths),
      - network exfiltration primitives (``socket.socket``,
        ``urllib.request.urlopen``, ``requests.post`` to a literal URL).
  * **Shell / regex analysis.** For shell snippets (and as a fallback for
    any text, including embedded shell inside Python strings), high-signal
    regexes for ``rm -rf`` (esp. ``rm -rf /``), ``curl|wget ... | sh``
    (pipe-to-shell), ``chmod 777``, ``:(){ :|:& };:`` fork bombs,
    ``mkfs`` / ``dd of=/dev/...`` and ``shell=True`` written literally.

When the snippet does not parse as Python the AST pass is simply skipped
(typed as "not python"); the shell/regex pass still runs, so non-Python
code is never silently ignored. There is **no** heavy / model dependency:
the optional reuse of Plan 09's marketplace ``StaticAnalyzer`` (bandit)
is intentionally NOT a hard dependency — this guardrail is self-contained
and pure.

The detection is side-effect-free: the engine applies the action; this
module only *suggests* one — configurable, defaulting to ``block`` (a
dangerous construct in generated code should not reach execution).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any

from shared_guardrails.exceptions import GuardrailConfigError
from shared_guardrails.registry import register_guardrail
from shared_guardrails.types import Action, GuardrailContext, GuardrailResult, Severity

# --------------------------------------------------------------------------- #
# Detected-construct record                                                   #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CodeFinding:
    """One detected dangerous construct.

    ``category`` is a stable identifier so hosts can group / alert by
    construct (``eval_exec``, ``shell_injection``, ``destructive_fs``,
    ``dynamic_import``, ``unsafe_deserialization``, ``unsafe_file_write``,
    ``network_exfiltration``). ``detail`` is a short human description.
    ``line`` is the 1-based source line (0 when unknown — e.g. a regex
    match outside a parseable snippet). ``snippet`` is the offending
    construct/line text for the host's audit log (not a secret).
    """

    category: str
    detail: str
    line: int
    snippet: str
    severity: Severity


# Stable category vocabulary (kept in one place so hosts can enumerate it).
CODE_SAFETY_CATEGORIES: tuple[str, ...] = (
    "eval_exec",
    "shell_injection",
    "destructive_fs",
    "dynamic_import",
    "unsafe_deserialization",
    "unsafe_file_write",
    "network_exfiltration",
)


# --------------------------------------------------------------------------- #
# Python AST analyzer (pure — only runs when the snippet parses as Python)     #
# --------------------------------------------------------------------------- #

# Bare-name dangerous builtins: eval / exec / compile / __import__.
_DANGEROUS_BUILTINS: dict[str, tuple[str, str, Severity]] = {
    "eval": ("eval_exec", "use of eval()", Severity.HIGH),
    "exec": ("eval_exec", "use of exec()", Severity.HIGH),
    "compile": ("eval_exec", "use of compile()", Severity.MEDIUM),
    "__import__": ("dynamic_import", "dynamic __import__()", Severity.MEDIUM),
}

# Dotted-attribute calls flagged unconditionally (module.attr -> finding).
_DANGEROUS_DOTTED: dict[str, tuple[str, str, Severity]] = {
    "os.system": ("shell_injection", "os.system() shell call", Severity.HIGH),
    "os.popen": ("shell_injection", "os.popen() shell call", Severity.HIGH),
    "os.remove": ("destructive_fs", "os.remove()", Severity.MEDIUM),
    "os.unlink": ("destructive_fs", "os.unlink()", Severity.MEDIUM),
    "os.rmdir": ("destructive_fs", "os.rmdir()", Severity.MEDIUM),
    "shutil.rmtree": ("destructive_fs", "shutil.rmtree()", Severity.HIGH),
    "pickle.loads": ("unsafe_deserialization", "pickle.loads() of untrusted data", Severity.HIGH),
    "pickle.load": ("unsafe_deserialization", "pickle.load() of untrusted data", Severity.HIGH),
    "marshal.loads": ("unsafe_deserialization", "marshal.loads()", Severity.HIGH),
    "importlib.import_module": ("dynamic_import", "importlib.import_module()", Severity.MEDIUM),
    "socket.socket": ("network_exfiltration", "raw socket creation", Severity.MEDIUM),
    "urllib.request.urlopen": (
        "network_exfiltration",
        "urllib network request",
        Severity.MEDIUM,
    ),
}

# Calls flagged only when a keyword argument has a specific (literal) value,
# e.g. subprocess.run(..., shell=True). Keyed by the *attribute* name so any
# subprocess.<call> matches.
_SUBPROCESS_CALLS: frozenset[str] = frozenset(
    {"run", "call", "check_call", "check_output", "Popen"}
)

# requests.<verb>(...) network calls (exfiltration via outbound HTTP).
_REQUESTS_VERBS: frozenset[str] = frozenset({"get", "post", "put", "patch", "delete", "request"})

# File-open modes that *write* (a write outside the workspace is the risk).
_WRITE_MODES: frozenset[str] = frozenset({"w", "a", "x", "w+", "a+", "wb", "ab", "xb", "r+"})


def _dotted_name(node: ast.AST) -> str | None:
    """Reconstruct a dotted name (``a.b.c``) from an attribute / name node.

    Returns ``None`` for anything that is not a pure attribute chain over a
    bare name (e.g. a subscript or call result), so we never misattribute a
    construct.
    """
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _has_kwarg_true(call: ast.Call, name: str) -> bool:
    """Whether ``call`` passes ``name=True`` as a literal keyword."""
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return True
    return False


def _looks_outside_workspace(path: str) -> bool:
    """Best-effort: an absolute or parent-traversal path escapes the workspace.

    Generated code that writes / opens an absolute path (``/etc/passwd``,
    ``C:\\Windows\\...``) or climbs out with ``..`` is the dangerous case;
    a plain relative path inside the workspace is fine.
    """
    p = path.strip()
    if not p:
        return False
    if p.startswith("/") or p.startswith("\\"):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", p):  # Windows drive-absolute
        return True
    return ".." in re.split(r"[\\/]", p)


class _PythonDangerVisitor(ast.NodeVisitor):
    """Walks a parsed Python snippet collecting dangerous constructs."""

    def __init__(self) -> None:
        self.findings: list[CodeFinding] = []

    def _add(self, node: ast.AST, category: str, detail: str, severity: Severity) -> None:
        line = getattr(node, "lineno", 0) or 0
        self.findings.append(
            CodeFinding(
                category=category,
                detail=detail,
                line=line,
                snippet=_safe_unparse(node),
                severity=severity,
            )
        )

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 (ast visitor naming)
        func = node.func

        # Bare builtins: eval / exec / compile / __import__.
        if isinstance(func, ast.Name) and func.id in _DANGEROUS_BUILTINS:
            category, detail, severity = _DANGEROUS_BUILTINS[func.id]
            self._add(node, category, detail, severity)

        dotted = _dotted_name(func)
        if dotted is not None:
            # Unconditionally dangerous dotted calls.
            if dotted in _DANGEROUS_DOTTED:
                category, detail, severity = _DANGEROUS_DOTTED[dotted]
                self._add(node, category, detail, severity)

            # subprocess.<call>(..., shell=True) — shell injection surface.
            if isinstance(func, ast.Attribute) and func.attr in _SUBPROCESS_CALLS:
                root = dotted.split(".")[0]
                if root == "subprocess" and _has_kwarg_true(node, "shell"):
                    self._add(
                        node,
                        "shell_injection",
                        f"subprocess.{func.attr}(..., shell=True)",
                        Severity.HIGH,
                    )

            # requests.<verb>(<literal url>, ...) — outbound HTTP exfiltration.
            if isinstance(func, ast.Attribute) and func.attr in _REQUESTS_VERBS:
                root = dotted.split(".")[0]
                if root == "requests":
                    self._add(
                        node,
                        "network_exfiltration",
                        f"requests.{func.attr}() outbound request",
                        Severity.MEDIUM,
                    )

        # open(path, mode) where mode writes and the path escapes the workspace.
        if isinstance(func, ast.Name) and func.id == "open":
            self._check_open(node)

        self.generic_visit(node)

    def _check_open(self, node: ast.Call) -> None:
        mode = _literal_str_arg(node, index=1, keyword="mode")
        # Default open() mode is read; only writing modes are a concern here.
        if mode is None or mode not in _WRITE_MODES:
            return
        path = _literal_str_arg(node, index=0, keyword="file")
        if path is not None and _looks_outside_workspace(path):
            self._add(
                node,
                "unsafe_file_write",
                f"open({path!r}, {mode!r}) writes outside the workspace",
                Severity.HIGH,
            )


def _literal_str_arg(call: ast.Call, *, index: int, keyword: str) -> str | None:
    """Return the literal str value of a positional/keyword arg, else ``None``."""
    if index < len(call.args):
        arg = call.args[index]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    for kw in call.keywords:
        if (
            kw.arg == keyword
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, str)
        ):
            return kw.value.value
    return None


def _safe_unparse(node: ast.AST) -> str:
    """``ast.unparse`` the node, truncated, never raising."""
    try:
        text = ast.unparse(node)
    except Exception:  # pragma: no cover - unparse is total on 3.12 for our nodes
        return ""
    text = text.strip()
    return text if len(text) <= 200 else text[:197] + "..."


def analyze_python(source: str) -> list[CodeFinding] | None:
    """Run the AST analysis over a Python snippet.

    Returns the list of findings, or ``None`` when ``source`` does not
    parse as Python (the caller then relies on the shell/regex pass).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    visitor = _PythonDangerVisitor()
    visitor.visit(tree)
    return visitor.findings


# --------------------------------------------------------------------------- #
# Shell / regex analyzer (always runs — catches shell + embedded constructs)   #
# --------------------------------------------------------------------------- #

# (category, detail, severity, compiled pattern). Patterns are high-signal so
# benign text is not flagged.
_SHELL_PATTERNS: tuple[tuple[str, str, Severity, re.Pattern[str]], ...] = (
    (
        "destructive_fs",
        "rm -rf (recursive force delete)",
        Severity.HIGH,
        re.compile(r"\brm\s+(?:-[a-zA-Z]*\s+)*-?[a-zA-Z]*r[a-zA-Z]*f|\brm\s+-rf\b", re.IGNORECASE),
    ),
    (
        "destructive_fs",
        "rm -rf / (root wipe)",
        Severity.CRITICAL,
        re.compile(r"\brm\s+-[rf]{1,2}\b[^\n]*\s/(?:\s|$|\*)", re.IGNORECASE),
    ),
    (
        "shell_injection",
        "pipe-to-shell (curl|wget ... | sh)",
        Severity.HIGH,
        re.compile(
            r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b",
            re.IGNORECASE,
        ),
    ),
    (
        "shell_injection",
        "shell=True passed to a subprocess call",
        Severity.HIGH,
        re.compile(r"\bshell\s*=\s*True\b"),
    ),
    (
        "destructive_fs",
        "chmod 777 (world-writable)",
        Severity.MEDIUM,
        re.compile(r"\bchmod\s+(?:-R\s+)?0?777\b", re.IGNORECASE),
    ),
    (
        "destructive_fs",
        "fork bomb",
        Severity.CRITICAL,
        re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
    ),
    (
        "destructive_fs",
        "dd writing to a device / mkfs (disk wipe)",
        Severity.CRITICAL,
        re.compile(r"\b(?:dd\s+[^\n]*\bof=/dev/|mkfs(?:\.\w+)?\s+/dev/)", re.IGNORECASE),
    ),
)


def analyze_shell(text: str) -> list[CodeFinding]:
    """Run the regex analysis over arbitrary text (shell + embedded)."""
    findings: list[CodeFinding] = []
    lines = text.splitlines()
    for category, detail, severity, pattern in _SHELL_PATTERNS:
        for m in pattern.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            snippet = lines[line_no - 1].strip() if 0 < line_no <= len(lines) else m.group(0)
            findings.append(
                CodeFinding(
                    category=category,
                    detail=detail,
                    line=line_no,
                    snippet=snippet[:200],
                    severity=severity,
                )
            )
    return findings


# --------------------------------------------------------------------------- #
# Config coercion helpers (mirror builtins.py / the other checks)              #
# --------------------------------------------------------------------------- #


def _coerce_severity(value: Any, default: Severity = Severity.HIGH) -> Severity:
    if value is None:
        return default
    if isinstance(value, Severity):
        return value
    try:
        return Severity(str(value).lower())
    except ValueError as exc:
        raise GuardrailConfigError(f"Invalid severity {value!r}.") from exc


def _coerce_action(value: Any) -> Action | None:
    if value is None:
        return None
    if isinstance(value, Action):
        return value
    try:
        return Action(str(value).lower())
    except ValueError as exc:
        raise GuardrailConfigError(f"Invalid action {value!r}.") from exc


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


# --------------------------------------------------------------------------- #
# The guardrail                                                               #
# --------------------------------------------------------------------------- #


class CodeSafetyGuardrail:
    """Statically detects dangerous constructs in generated code.

    Config:
      - ``severity``          str  — the *floor* severity; per-construct
        severities (e.g. ``rm -rf /`` -> critical) raise it. Default
        ``high``.
      - ``suggested_action``  str  — override the default action. When
        unset the guardrail suggests ``block``.
      - ``categories``        optional list[str] restricting which
        construct categories count as a trigger. Unset = all.
      - ``min_severity``      str  — drop findings below this severity
        (e.g. only block ``high``+). Default ``low`` (report everything).

    The result ``payload`` carries:
      - ``categories``  sorted unique categories detected,
      - ``count``       number of findings,
      - ``findings``    per-finding ``{category, detail, line, snippet,
        severity}``,
      - ``language``    ``"python"`` when the AST pass parsed, else
        ``"other"``.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._severity = _coerce_severity(config.get("severity"))
        self._suggested_override = _coerce_action(config.get("suggested_action"))
        self._min_severity = _coerce_severity(config.get("min_severity"), default=Severity.LOW)

        raw_categories = config.get("categories")
        if raw_categories is not None:
            if not isinstance(raw_categories, list) or not all(
                isinstance(c, str) for c in raw_categories
            ):
                raise GuardrailConfigError(
                    "code_safety guardrail 'categories' must be a list of strings."
                )
            self._only: set[str] | None = {str(c) for c in raw_categories}
        else:
            self._only = None

    def _suggested_action(self) -> Action:
        if self._suggested_override is not None:
            return self._suggested_override
        return Action.BLOCK

    def _resolve_severity(self, findings: list[CodeFinding]) -> Severity:
        """The result severity is the gravest finding, floored by config."""
        best = self._severity
        for f in findings:
            if _SEVERITY_RANK[f.severity] > _SEVERITY_RANK[best]:
                best = f.severity
        return best

    def check(self, context: GuardrailContext) -> GuardrailResult:
        text = context.primary_text()
        if not text:
            return GuardrailResult.ok()

        python_findings = analyze_python(text)
        language = "python" if python_findings is not None else "other"

        findings: list[CodeFinding] = list(python_findings or [])
        # The shell/regex pass always runs so embedded shell inside a Python
        # string (or a non-Python snippet) is never missed.
        findings.extend(analyze_shell(text))

        # Apply the config filters: category opt-out + minimum severity.
        floor = _SEVERITY_RANK[self._min_severity]
        findings = [
            f
            for f in findings
            if _SEVERITY_RANK[f.severity] >= floor
            and (self._only is None or f.category in self._only)
        ]

        if not findings:
            return GuardrailResult(triggered=False, payload={"language": language})

        categories = sorted({f.category for f in findings})
        return GuardrailResult(
            triggered=True,
            severity=self._resolve_severity(findings),
            detail=(
                f"Detected {len(findings)} dangerous code construct(s) "
                f"[{', '.join(categories)}] in {context.hook} ({language}) code."
            ),
            suggested_action=self._suggested_action(),
            payload={
                "language": language,
                "categories": categories,
                "count": len(findings),
                "findings": [
                    {
                        "category": f.category,
                        "detail": f.detail,
                        "line": f.line,
                        "snippet": f.snippet,
                        "severity": f.severity.value,
                    }
                    for f in findings
                ],
            },
        )


@register_guardrail("code_safety")
def _build_code_safety(config: dict[str, Any]) -> CodeSafetyGuardrail:
    return CodeSafetyGuardrail(config)


__all__ = [
    "CODE_SAFETY_CATEGORIES",
    "CodeFinding",
    "CodeSafetyGuardrail",
    "analyze_python",
    "analyze_shell",
]
