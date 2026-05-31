"""PII detection guardrail (Plan 11 task_11_04).

Exercises the ``pii`` guardrail registered into the shared-guardrails
engine. It pins the binding task requirements:

  * text containing an email / credit card / phone number is flagged with
    the right entity types and a ``redact`` suggestion on ``post_llm``;
  * a ``pre_llm`` prompt with PII is flagged with a ``block`` suggestion
    (the plan baseline — PII must never leak to an external LLM);
  * clean text passes (``triggered=False``);
  * the guardrail is reachable through the registry by its ``type``;
  * the **regex fallback / detection logic** runs everywhere (no heavy
    dependency), and is also tested against a MOCKED analyzer so the
    Presidio-vocabulary path has coverage without the model;
  * the **Presidio-specific** assertions are SKIP-GUARDED: they run only
    where ``presidio-analyzer`` is importable, and skip elsewhere
    (Presidio is an optional/lazy extra — its spaCy model is heavy);
  * the strict ``presidio`` backend, when the dep is absent, degrades to a
    typed *unavailable* result instead of crashing.

Pure library code — no DB, no tenant-owned rows — so no ``cross_tenant``
marker (multi-tenancy is unaffected by a stateless text scanner).
"""

from __future__ import annotations

import pytest
from shared_guardrails import (
    Action,
    GuardrailContext,
    GuardrailResult,
    Severity,
    default_registry,
)
from shared_guardrails.checks.pii import (
    PiiGuardrail,
    PiiMatch,
    RegexPiiAnalyzer,
    presidio_available,
)

pytestmark = pytest.mark.integration


# A clearly card-shaped number that passes Luhn (a Visa test number).
_GOOD_CARD = "4111 1111 1111 1111"


# --------------------------------------------------------------------------- #
# Registry wiring                                                             #
# --------------------------------------------------------------------------- #


def test_pii_type_is_registered() -> None:
    assert default_registry.is_registered("pii")
    guard = default_registry.build("pii", {"backend": "regex"})
    assert isinstance(guard, PiiGuardrail)


# --------------------------------------------------------------------------- #
# Regex fallback detection logic (runs everywhere, no heavy dep)              #
# --------------------------------------------------------------------------- #


def test_email_flagged_with_redact_on_post_llm() -> None:
    guard = PiiGuardrail({"backend": "regex"})
    ctx = GuardrailContext(hook="post_llm", response="Contact me at jane.doe@example.com please.")
    result = guard.check(ctx)
    assert result.triggered is True
    assert "EMAIL_ADDRESS" in result.payload["entity_types"]
    assert result.suggested_action is Action.REDACT
    assert result.severity is Severity.HIGH
    # The matched span is reported so the host's redact action can mask it.
    spans = result.payload["spans"]
    assert any(s["text"] == "jane.doe@example.com" for s in spans)


def test_credit_card_flagged_and_luhn_filters_noise() -> None:
    guard = PiiGuardrail({"backend": "regex"})
    ctx = GuardrailContext(hook="post_llm", response=f"card {_GOOD_CARD} on file")
    result = guard.check(ctx)
    assert result.triggered is True
    assert "CREDIT_CARD" in result.payload["entity_types"]

    # A 16-digit run that FAILS the Luhn check is not a card.
    bad = PiiGuardrail({"backend": "regex"})
    bad_ctx = GuardrailContext(hook="post_llm", response="ref 1234 5678 9012 3456")
    bad_result = bad.check(bad_ctx)
    assert "CREDIT_CARD" not in bad_result.payload.get("entity_types", [])


def test_phone_number_flagged() -> None:
    guard = PiiGuardrail({"backend": "regex"})
    ctx = GuardrailContext(hook="post_llm", response="Call +1 (415) 555-2671 to reach the desk.")
    result = guard.check(ctx)
    assert result.triggered is True
    assert "PHONE_NUMBER" in result.payload["entity_types"]


def test_pre_llm_prompt_with_pii_suggests_block() -> None:
    guard = PiiGuardrail({"backend": "regex"})
    ctx = GuardrailContext(hook="pre_llm", prompt="My email is bob@corp.io, summarize the doc.")
    result = guard.check(ctx)
    assert result.triggered is True
    assert result.suggested_action is Action.BLOCK


def test_clean_text_passes() -> None:
    guard = PiiGuardrail({"backend": "regex"})
    ctx = GuardrailContext(hook="post_llm", response="The quarterly report is ready for review.")
    result = guard.check(ctx)
    assert result.triggered is False
    assert result.payload.get("available") is True


def test_empty_text_passes() -> None:
    guard = PiiGuardrail({"backend": "regex"})
    assert guard.check(GuardrailContext(hook="post_llm", response="")).triggered is False


def test_suggested_action_override() -> None:
    guard = PiiGuardrail({"backend": "regex", "suggested_action": "escalate_to_human"})
    ctx = GuardrailContext(hook="post_llm", response="reach me: a@b.com")
    result = guard.check(ctx)
    assert result.suggested_action is Action.ESCALATE_TO_HUMAN


def test_entities_filter_restricts_detection() -> None:
    # Only ask for EMAIL_ADDRESS; the card must then NOT be reported.
    guard = PiiGuardrail({"backend": "regex", "entities": ["EMAIL_ADDRESS"]})
    ctx = GuardrailContext(hook="post_llm", response=f"a@b.com / {_GOOD_CARD}")
    result = guard.check(ctx)
    assert result.payload["entity_types"] == ["EMAIL_ADDRESS"]


def test_min_score_drops_low_confidence_matches() -> None:
    # An IPv4 scores 0.6 in the regex backend; raising min_score above it
    # drops the only match -> clean.
    guard = PiiGuardrail({"backend": "regex", "min_score": 0.7})
    ctx = GuardrailContext(hook="post_llm", response="server at 10.0.12.34 is up")
    assert guard.check(ctx).triggered is False


def test_invalid_backend_rejected() -> None:
    from shared_guardrails import GuardrailConfigError

    with pytest.raises(GuardrailConfigError):
        PiiGuardrail({"backend": "nope"})


def test_regex_analyzer_directly() -> None:
    analyzer = RegexPiiAnalyzer()
    matches = analyzer.analyze("ping bob@corp.io now", None)
    assert [m.entity_type for m in matches] == ["EMAIL_ADDRESS"]
    assert matches[0].text == "bob@corp.io"


# --------------------------------------------------------------------------- #
# Mocked-analyzer path: exercise the Presidio *vocabulary* without the model  #
# --------------------------------------------------------------------------- #


class _MockAnalyzer:
    """Stand-in PII analyzer (satisfies the PiiAnalyzer protocol).

    Returns a fixed PERSON + LOCATION result — entity types the regex
    fallback cannot produce — so the guardrail's result-shaping logic is
    covered without loading Presidio's NER model.
    """

    def analyze(self, text: str, entities: list[str] | None) -> list[PiiMatch]:
        out = [
            PiiMatch("PERSON", "Alice Smith", 0, 11, 0.85),
            PiiMatch("LOCATION", "Berlin", 20, 26, 0.55),
        ]
        if entities is not None:
            out = [m for m in out if m.entity_type in entities]
        return out


def test_injected_analyzer_drives_detection() -> None:
    guard = PiiGuardrail({"analyzer": _MockAnalyzer()})
    ctx = GuardrailContext(hook="post_llm", response="Alice Smith lives in Berlin.")
    result = guard.check(ctx)
    assert result.triggered is True
    assert result.payload["entity_types"] == ["LOCATION", "PERSON"]
    assert result.suggested_action is Action.REDACT


def test_injected_analyzer_min_score_filters() -> None:
    # LOCATION scores 0.55; min_score 0.8 keeps only PERSON.
    guard = PiiGuardrail({"analyzer": _MockAnalyzer(), "min_score": 0.8})
    ctx = GuardrailContext(hook="post_llm", response="Alice Smith lives in Berlin.")
    result = guard.check(ctx)
    assert result.payload["entity_types"] == ["PERSON"]


def test_injected_non_analyzer_rejected() -> None:
    from shared_guardrails import GuardrailConfigError

    with pytest.raises(GuardrailConfigError):
        PiiGuardrail({"analyzer": object()})


# --------------------------------------------------------------------------- #
# Optional-Presidio degrade: strict backend + dep absent -> typed unavailable #
# --------------------------------------------------------------------------- #


def test_presidio_backend_degrades_when_unavailable() -> None:
    if presidio_available():
        pytest.skip("presidio-analyzer is installed; the degrade path is N/A here.")
    guard = PiiGuardrail({"backend": "presidio"})
    ctx = GuardrailContext(hook="post_llm", response="email a@b.com")
    result = guard.check(ctx)
    # Must NOT crash, must NOT silently mark triggered: a typed unavailable.
    assert isinstance(result, GuardrailResult)
    assert result.triggered is False
    assert result.payload["available"] is False
    assert result.payload["backend"] == "presidio"
    assert result.suggested_action is None


# --------------------------------------------------------------------------- #
# Presidio backend (SKIP-GUARDED: needs the optional extra + spaCy model)     #
# --------------------------------------------------------------------------- #


def test_presidio_backend_detects_email() -> None:
    pytest.importorskip(
        "presidio_analyzer",
        reason="Presidio is an optional [pii] extra; skipping the real-backend test.",
    )
    if not presidio_available():  # pragma: no cover - defensive
        pytest.skip("presidio-analyzer not importable")
    try:
        guard = PiiGuardrail({"backend": "presidio"})
        ctx = GuardrailContext(hook="post_llm", response="reach me at jane@example.com")
        result = guard.check(ctx)
    except Exception as exc:  # pragma: no cover - model missing in this env
        pytest.skip(f"Presidio engine/model unavailable: {exc}")
    if result.payload.get("available") is False:  # pragma: no cover
        pytest.skip("Presidio backend reported unavailable (model missing).")
    assert result.triggered is True
    assert "EMAIL_ADDRESS" in result.payload["entity_types"]
    assert result.payload["backend"] == "presidio"
