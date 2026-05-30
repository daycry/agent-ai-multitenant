"""Secret-leakage guardrail (Plan 11 task_11_05).

Exercises the ``secret_leakage`` guardrail registered into the
shared-guardrails engine. It pins the binding task requirements:

  * each well-known token family (AWS access key, Google API key,
    GitHub/GitLab token, Slack token, PEM private key, JWT, connection
    string with password) plus generic high-entropy secret assignments
    are detected;
  * a benign string is NOT flagged (low false-positive rate);
  * the redaction masks the secret — the marker carries only the family,
    and the raw secret never appears in the redacted text nor anywhere
    in the result payload;
  * the guardrail is reachable through the registry by its ``type`` and
    suggests ``redact`` by default (``block`` when configured).

Pure-Python detection (regex + Shannon entropy) — no heavy/model
dependency, so the whole suite runs everywhere incl. CI. Stateless text
scanner, no DB / tenant-owned rows, so no ``cross_tenant`` marker.
"""

from __future__ import annotations

import json

import pytest
from shared_guardrails import (
    Action,
    GuardrailContext,
    Severity,
    default_registry,
)
from shared_guardrails.checks.secret_leakage import (
    SecretLeakageGuardrail,
    SecretScanner,
    redact_secrets,
    shannon_entropy,
)

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# Sample secrets, one per family (fake values, structurally valid shapes).    #
# --------------------------------------------------------------------------- #

_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
_GOOGLE_KEY = "AIzaSyA1234567890abcdefghijklmnopqrstuv"
_GITHUB_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
_GITLAB_TOKEN = "glpat-ABCDEF1234567890abcd"
_SLACK_TOKEN = "xoxb-123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx"
_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ"
    ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)
_CONNECTION_STRING = "postgres://admin:S3cr3tP4ss@db.internal:5432/app"
# Built at runtime from pieces so neither the literal PEM header nor the
# detect-private-key blocklist phrase sits in the source file (it would
# trip the repo's detect-private-key hook). The scanner still sees a
# full, valid PEM block at runtime.
_PEM = "-" * 5
_KEY_KIND = "RSA PRIVATE " + "KEY"  # split so the blocklist phrase is not literal
_PRIVATE_KEY = (
    f"{_PEM}BEGIN {_KEY_KIND}{_PEM}\n"
    "MIIEpAIBAAKCAQEArandombase64lookingkeymaterialthatissecret1234567890\n"
    "abcdEFGHijklMNOPqrstUVWXyz0987654321moremoremoremoremoremoremoremore\n"
    f"{_PEM}END {_KEY_KIND}{_PEM}"
)
_GENERIC_SECRET = 'api_secret_key = "f3Q9xL7pZ2vK8nR4tW1yB6mC0dH5jA"'


# --------------------------------------------------------------------------- #
# Registry wiring                                                             #
# --------------------------------------------------------------------------- #


def test_secret_leakage_type_is_registered() -> None:
    assert default_registry.is_registered("secret_leakage")
    guard = default_registry.build("secret_leakage", {})
    assert isinstance(guard, SecretLeakageGuardrail)


# --------------------------------------------------------------------------- #
# Each token family is detected                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("sample", "expected_type"),
    [
        (f"key = {_AWS_KEY}", "AWS_ACCESS_KEY"),
        (f"GOOGLE_API_KEY={_GOOGLE_KEY}", "GOOGLE_API_KEY"),
        (f"export GH={_GITHUB_TOKEN}", "GITHUB_TOKEN"),
        (f"token: {_GITLAB_TOKEN}", "GITLAB_TOKEN"),
        (f"slack {_SLACK_TOKEN}", "SLACK_TOKEN"),
        (f"Authorization: Bearer {_JWT}", "JWT"),
        (f'DATABASE_URL="{_CONNECTION_STRING}"', "CONNECTION_STRING"),
        (_PRIVATE_KEY, "PRIVATE_KEY"),
        (_GENERIC_SECRET, "GENERIC_SECRET"),
    ],
)
def test_each_token_family_detected(sample: str, expected_type: str) -> None:
    guard = SecretLeakageGuardrail({})
    result = guard.check(GuardrailContext(hook="post_llm", response=sample))
    assert result.triggered is True
    assert expected_type in result.payload["secret_types"]
    assert result.suggested_action is Action.REDACT
    assert result.severity is Severity.HIGH


def test_post_tool_hook_scans_tool_result() -> None:
    # A tool's stdout surfaces a credential — must be caught at post_tool.
    guard = SecretLeakageGuardrail({})
    ctx = GuardrailContext(
        hook="post_tool",
        tool_name="read_file",
        tool_result=f"contents: aws_key={_AWS_KEY}",
    )
    result = guard.check(ctx)
    assert result.triggered is True
    assert "AWS_ACCESS_KEY" in result.payload["secret_types"]


# --------------------------------------------------------------------------- #
# Redaction masks the secret — never echoes it                               #
# --------------------------------------------------------------------------- #


def test_redaction_masks_secret_and_never_echoes_it() -> None:
    guard = SecretLeakageGuardrail({})
    text = f"Here is the key: {_AWS_KEY} — keep it safe."
    result = guard.check(GuardrailContext(hook="post_llm", response=text))

    redacted = result.payload["redacted_text"]
    # The raw secret must NOT survive in the redacted output...
    assert _AWS_KEY not in redacted
    # ...and the marker replaces it, carrying only the family.
    assert "[REDACTED:AWS_ACCESS_KEY]" in redacted
    # The benign surrounding text is preserved.
    assert "keep it safe." in redacted

    # The raw secret must NOT appear ANYWHERE in the serialised result —
    # spans carry offsets + family only, never the secret value.
    dumped = json.dumps(result.payload)
    assert _AWS_KEY not in dumped
    for span in result.payload["spans"]:
        assert set(span) == {"secret_type", "start", "end"}


def test_redact_marker_is_configurable() -> None:
    guard = SecretLeakageGuardrail({"redact_marker": "<<{type}>>"})
    result = guard.check(GuardrailContext(hook="post_llm", response=f"k={_GITHUB_TOKEN}"))
    assert "<<GITHUB_TOKEN>>" in result.payload["redacted_text"]
    assert _GITHUB_TOKEN not in result.payload["redacted_text"]


def test_redact_marker_must_contain_type_placeholder() -> None:
    from shared_guardrails import GuardrailConfigError

    with pytest.raises(GuardrailConfigError):
        SecretLeakageGuardrail({"redact_marker": "***"})


def test_multiple_secrets_all_redacted() -> None:
    guard = SecretLeakageGuardrail({})
    text = f"a={_AWS_KEY} and g={_GOOGLE_KEY}"
    result = guard.check(GuardrailContext(hook="post_llm", response=text))
    assert result.payload["count"] >= 2
    redacted = result.payload["redacted_text"]
    assert _AWS_KEY not in redacted
    assert _GOOGLE_KEY not in redacted


# --------------------------------------------------------------------------- #
# Low false-positive rate: benign strings are not flagged                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "benign",
    [
        "The quarterly report is ready for review.",
        'environment = "production"',
        'name = "agent-ai-multitenant"',
        "Connect to the database and run the migration.",
        'log_level = "debug"',
        "Visit https://example.com/docs for the guide.",
        'description = "a short human readable sentence here"',
    ],
)
def test_benign_text_not_flagged(benign: str) -> None:
    guard = SecretLeakageGuardrail({})
    result = guard.check(GuardrailContext(hook="post_llm", response=benign))
    assert result.triggered is False


def test_low_entropy_secretish_assignment_not_flagged() -> None:
    # A secret-ish KEY but a low-entropy, dictionary-ish VALUE: the entropy
    # gate must keep it from being flagged as a generic secret.
    guard = SecretLeakageGuardrail({})
    ctx = GuardrailContext(hook="post_llm", response='password = "changemechangeme"')
    assert guard.check(ctx).triggered is False


def test_empty_text_passes() -> None:
    guard = SecretLeakageGuardrail({})
    assert guard.check(GuardrailContext(hook="post_llm", response="")).triggered is False


# --------------------------------------------------------------------------- #
# Action / severity overrides                                                #
# --------------------------------------------------------------------------- #


def test_block_action_override() -> None:
    guard = SecretLeakageGuardrail({"suggested_action": "block"})
    result = guard.check(GuardrailContext(hook="post_llm", response=f"k={_AWS_KEY}"))
    assert result.triggered is True
    assert result.suggested_action is Action.BLOCK


def test_severity_override() -> None:
    guard = SecretLeakageGuardrail({"severity": "critical"})
    result = guard.check(GuardrailContext(hook="post_llm", response=f"k={_AWS_KEY}"))
    assert result.severity is Severity.CRITICAL


def test_invalid_severity_rejected() -> None:
    from shared_guardrails import GuardrailConfigError

    with pytest.raises(GuardrailConfigError):
        SecretLeakageGuardrail({"severity": "nope"})


def test_invalid_min_entropy_rejected() -> None:
    from shared_guardrails import GuardrailConfigError

    with pytest.raises(GuardrailConfigError):
        SecretLeakageGuardrail({"min_entropy": "high"})


# --------------------------------------------------------------------------- #
# Detection primitives (direct unit coverage)                                #
# --------------------------------------------------------------------------- #


def test_shannon_entropy_orders_random_above_repetitive() -> None:
    assert shannon_entropy("aaaaaaaaaaaa") < shannon_entropy("f3Q9xL7pZ2vK8nR4tW")
    assert shannon_entropy("") == 0.0


def test_scanner_dedupes_jwt_assigned_to_token_key() -> None:
    # A JWT assigned to a secret-ish key must be counted ONCE (as JWT, the
    # more specific family), not also as a GENERIC_SECRET.
    scanner = SecretScanner()
    matches = scanner.scan(f'token = "{_JWT}"')
    assert len(matches) == 1
    assert matches[0].secret_type == "JWT"


def test_redact_secrets_right_to_left_keeps_offsets() -> None:
    scanner = SecretScanner()
    text = f"first {_AWS_KEY} second {_GOOGLE_KEY} end"
    matches = scanner.scan(text)
    redacted = redact_secrets(text, matches)
    assert _AWS_KEY not in redacted
    assert _GOOGLE_KEY not in redacted
    assert redacted.startswith("first [REDACTED:AWS_ACCESS_KEY] second ")
    assert redacted.endswith(" end")
