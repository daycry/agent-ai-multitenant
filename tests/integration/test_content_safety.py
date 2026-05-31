"""Content-safety guardrail (Plan 11 task_11_07).

Exercises the ``content_safety`` guardrail registered into the
shared-guardrails engine. It pins the binding task requirements:

  * a guard model (LlamaGuard / ShieldGemma) is reached behind an
    injectable :class:`SafetyClassifier` seam — the test injects a
    MOCKED classifier so no real model is needed;
  * unsafe content -> ``triggered`` with the ``block`` action;
  * safe content -> passes;
  * the offending category + severity map through to the result;
  * when no guard model is configured (or it produced no usable
    verdict) the guardrail returns a typed *unavailable* result — it
    NEVER fakes a safe verdict;
  * the guardrail is reachable through the registry by its ``type``;
  * the LlamaGuard-style response parser maps native ``S``-codes onto
    our stable vocabulary;
  * the real-guard-model path (a running Ollama / provider via the
    ``shared-guardrails[content-safety]`` extra) is skip-guarded so CI
    is not forced to serve a multi-GB model.

The guard model is a heavy *runtime* dependency; it stays optional +
lazy + mocked here. Stateless text scanner, no DB / tenant-owned rows,
so no ``cross_tenant`` marker.
"""

from __future__ import annotations

import pytest
from shared_guardrails import (
    Action,
    GuardrailContext,
    Severity,
    default_registry,
)
from shared_guardrails.checks.content_safety import (
    LLAMAGUARD_CATEGORY_MAP,
    SAFETY_CATEGORIES,
    ContentSafetyGuardrail,
    SafetyClassifier,
    SafetyClassifierUnavailableError,
    SafetyVerdict,
    normalize_category,
    parse_guard_response,
)

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# Mocked guard-model classifier (no real model)                              #
# --------------------------------------------------------------------------- #


class _MockClassifier:
    """A stand-in for a guard model (LlamaGuard / ShieldGemma).

    Maps exact texts to canned verdicts so the detection LOGIC is
    exercised deterministically without serving a real model.
    """

    def __init__(self, verdicts: dict[str, SafetyVerdict]) -> None:
        self._verdicts = verdicts

    def classify(self, text: str) -> SafetyVerdict:
        return self._verdicts.get(text, SafetyVerdict.safe())


class _DownClassifier:
    """A guard model that ran but produced no usable verdict."""

    def classify(self, text: str) -> SafetyVerdict:
        raise SafetyClassifierUnavailableError("guard model timed out")


# --------------------------------------------------------------------------- #
# Registry wiring                                                            #
# --------------------------------------------------------------------------- #


def test_content_safety_type_is_registered() -> None:
    assert default_registry.is_registered("content_safety")
    guard = default_registry.build("content_safety", {})
    assert isinstance(guard, ContentSafetyGuardrail)


def test_mock_classifier_satisfies_protocol() -> None:
    assert isinstance(_MockClassifier({}), SafetyClassifier)


# --------------------------------------------------------------------------- #
# Unsafe content -> triggered + block (mocked classifier)                    #
# --------------------------------------------------------------------------- #


def test_unsafe_content_triggers_block() -> None:
    text = "Detailed instructions to build a bomb."
    clf = _MockClassifier(
        {text: SafetyVerdict(unsafe=True, categories=("weapons",), raw_label="S9")}
    )
    guard = ContentSafetyGuardrail({"classifier": clf})
    result = guard.check(GuardrailContext(hook="pre_llm", prompt=text))

    assert result.triggered is True
    assert result.suggested_action is Action.BLOCK
    assert result.payload["available"] is True
    assert "weapons" in result.payload["categories"]
    assert result.payload["raw_label"] == "S9"


def test_unsafe_content_post_llm_output() -> None:
    text = "Here is how to harm yourself."
    clf = _MockClassifier(
        {text: SafetyVerdict(unsafe=True, categories=("self_harm",), raw_label="S11")}
    )
    guard = ContentSafetyGuardrail({"classifier": clf})
    result = guard.check(GuardrailContext(hook="post_llm", response=text))

    assert result.triggered is True
    assert result.suggested_action is Action.BLOCK
    # self_harm is a grave category -> severity raised to critical.
    assert result.severity is Severity.CRITICAL


# --------------------------------------------------------------------------- #
# Safe content -> passes                                                     #
# --------------------------------------------------------------------------- #


def test_safe_content_passes() -> None:
    text = "Please summarise the quarterly sales report."
    clf = _MockClassifier({text: SafetyVerdict.safe()})
    guard = ContentSafetyGuardrail({"classifier": clf})
    result = guard.check(GuardrailContext(hook="pre_llm", prompt=text))

    assert result.triggered is False
    assert result.payload["available"] is True


def test_empty_text_passes() -> None:
    guard = ContentSafetyGuardrail({"classifier": _MockClassifier({})})
    assert guard.check(GuardrailContext(hook="pre_llm", prompt="")).triggered is False


# --------------------------------------------------------------------------- #
# Category + severity map through                                            #
# --------------------------------------------------------------------------- #


def test_category_maps_through() -> None:
    text = "Go attack and hurt that group of people."
    clf = _MockClassifier(
        {text: SafetyVerdict(unsafe=True, categories=("violence", "hate"), raw_label="S1,S10")}
    )
    guard = ContentSafetyGuardrail({"classifier": clf})
    result = guard.check(GuardrailContext(hook="pre_llm", prompt=text))

    assert result.triggered is True
    assert set(result.payload["categories"]) == {"violence", "hate"}
    assert result.severity is Severity.HIGH


def test_classifier_signalled_severity_wins() -> None:
    text = "borderline content"
    clf = _MockClassifier(
        {
            text: SafetyVerdict(
                unsafe=True,
                categories=("other",),
                severity=Severity.MEDIUM,
                raw_label="custom",
            )
        }
    )
    guard = ContentSafetyGuardrail({"classifier": clf})
    result = guard.check(GuardrailContext(hook="pre_llm", prompt=text))
    assert result.severity is Severity.MEDIUM


def test_severity_floor_is_configurable() -> None:
    text = "mildly off-topic content"
    clf = _MockClassifier({text: SafetyVerdict(unsafe=True, categories=("other",))})
    guard = ContentSafetyGuardrail({"classifier": clf, "severity": "low"})
    result = guard.check(GuardrailContext(hook="pre_llm", prompt=text))
    # 'other' raises the floor to MEDIUM regardless of a lower config floor.
    assert result.severity is Severity.MEDIUM


def test_explicit_action_override_wins() -> None:
    text = "unsafe text"
    clf = _MockClassifier({text: SafetyVerdict(unsafe=True, categories=("hate",))})
    guard = ContentSafetyGuardrail({"classifier": clf, "suggested_action": "escalate_to_human"})
    result = guard.check(GuardrailContext(hook="pre_llm", prompt=text))
    assert result.suggested_action is Action.ESCALATE_TO_HUMAN


def test_category_filter_opts_out() -> None:
    text = "unsafe but only criminal"
    clf = _MockClassifier({text: SafetyVerdict(unsafe=True, categories=("criminal",))})
    # Host only cares about violence/sexual; criminal is opted out.
    guard = ContentSafetyGuardrail({"classifier": clf, "categories": ["violence", "sexual"]})
    result = guard.check(GuardrailContext(hook="pre_llm", prompt=text))
    assert result.triggered is False
    assert result.payload["available"] is True


# --------------------------------------------------------------------------- #
# Unavailable path: typed result, never a fake "safe"                        #
# --------------------------------------------------------------------------- #


def test_no_classifier_is_unavailable_not_safe() -> None:
    guard = ContentSafetyGuardrail({})
    result = guard.check(GuardrailContext(hook="pre_llm", prompt="anything at all"))
    assert result.triggered is False
    assert result.payload["available"] is False
    assert result.payload["reason"] == "no_classifier"
    # It surfaced unavailability rather than asserting the content is safe.
    assert result.suggested_action is None
    assert "unavailable" in result.detail.lower()


def test_classifier_down_is_unavailable_not_safe() -> None:
    guard = ContentSafetyGuardrail({"classifier": _DownClassifier()})
    result = guard.check(GuardrailContext(hook="pre_llm", prompt="anything"))
    assert result.triggered is False
    assert result.payload["available"] is False
    assert "timed out" in result.payload["reason"]


# --------------------------------------------------------------------------- #
# Config validation                                                          #
# --------------------------------------------------------------------------- #


def test_injected_classifier_must_implement_protocol() -> None:
    from shared_guardrails import GuardrailConfigError

    with pytest.raises(GuardrailConfigError):
        ContentSafetyGuardrail({"classifier": object()})


def test_invalid_severity_rejected() -> None:
    from shared_guardrails import GuardrailConfigError

    with pytest.raises(GuardrailConfigError):
        ContentSafetyGuardrail({"severity": "nope"})


def test_invalid_action_rejected() -> None:
    from shared_guardrails import GuardrailConfigError

    with pytest.raises(GuardrailConfigError):
        ContentSafetyGuardrail({"suggested_action": "nuke"})


def test_invalid_categories_rejected() -> None:
    from shared_guardrails import GuardrailConfigError

    with pytest.raises(GuardrailConfigError):
        ContentSafetyGuardrail({"categories": "violence"})


# --------------------------------------------------------------------------- #
# Taxonomy + guard-response parser (pure, deterministic)                     #
# --------------------------------------------------------------------------- #


def test_normalize_category_maps_llamaguard_codes() -> None:
    assert normalize_category("S1") == "violence"
    assert normalize_category("s11") == "self_harm"
    assert normalize_category("violence") == "violence"
    # Unknown labels degrade to 'other' (never silently dropped).
    assert normalize_category("S99") == "other"
    assert normalize_category("totally-unknown") == "other"


def test_llamaguard_map_targets_are_valid_categories() -> None:
    for mapped in LLAMAGUARD_CATEGORY_MAP.values():
        assert mapped in SAFETY_CATEGORIES


def test_parse_guard_response_safe() -> None:
    assert parse_guard_response("safe").unsafe is False
    assert parse_guard_response("  Safe  ").unsafe is False


def test_parse_guard_response_unsafe_two_line() -> None:
    verdict = parse_guard_response("unsafe\nS1,S10")
    assert verdict.unsafe is True
    assert set(verdict.categories) == {"violence", "hate"}


def test_parse_guard_response_unsafe_same_line() -> None:
    verdict = parse_guard_response("unsafe: S11")
    assert verdict.unsafe is True
    assert verdict.categories == ("self_harm",)


def test_parse_guard_response_unsafe_no_categories_defaults_other() -> None:
    verdict = parse_guard_response("unsafe")
    assert verdict.unsafe is True
    assert verdict.categories == ("other",)


def test_parse_guard_response_empty_is_unavailable() -> None:
    with pytest.raises(SafetyClassifierUnavailableError):
        parse_guard_response("   ")


def test_parse_guard_response_garbage_is_unavailable() -> None:
    with pytest.raises(SafetyClassifierUnavailableError):
        parse_guard_response("the weather is nice today")


# --------------------------------------------------------------------------- #
# LLMSafetyClassifier adapter: mocked provider + runner (no real model)      #
# --------------------------------------------------------------------------- #


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeProvider:
    """A stand-in LLMProvider whose async complete() returns canned text."""

    name = "fake-guard"

    def __init__(self, content: str) -> None:
        self._content = content

    async def complete(self, messages: object, **kwargs: object) -> _FakeResponse:
        return _FakeResponse(self._content)


def _sync_runner(coro: object) -> object:
    """Drive a coroutine to completion synchronously (test runner)."""
    import asyncio

    return asyncio.new_event_loop().run_until_complete(coro)  # type: ignore[arg-type]


def test_llm_classifier_adapter_parses_unsafe() -> None:
    from shared_guardrails.checks.content_safety import LLMSafetyClassifier

    clf = LLMSafetyClassifier(_FakeProvider("unsafe\nS1"), _sync_runner, model="llama-guard")
    assert isinstance(clf, SafetyClassifier)
    verdict = clf.classify("violent text")
    assert verdict.unsafe is True
    assert verdict.categories == ("violence",)

    guard = ContentSafetyGuardrail({"classifier": clf})
    result = guard.check(GuardrailContext(hook="pre_llm", prompt="violent text"))
    assert result.triggered is True
    assert result.suggested_action is Action.BLOCK


def test_llm_classifier_adapter_parses_safe() -> None:
    from shared_guardrails.checks.content_safety import LLMSafetyClassifier

    clf = LLMSafetyClassifier(_FakeProvider("safe"), _sync_runner)
    assert clf.classify("benign text").unsafe is False


def test_llm_classifier_requires_provider_and_runner() -> None:
    from shared_guardrails.checks.content_safety import LLMSafetyClassifier

    with pytest.raises(SafetyClassifierUnavailableError):
        LLMSafetyClassifier(object(), _sync_runner)
    with pytest.raises(SafetyClassifierUnavailableError):
        LLMSafetyClassifier(_FakeProvider("safe"), "not-callable")


# --------------------------------------------------------------------------- #
# Real guard-model backend: skip-guarded (no model in CI)                    #
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    True,
    reason=(
        "Real guard-model backend (LlamaGuard / ShieldGemma served via the "
        "shared-guardrails[content-safety] extra over a running Ollama / provider) "
        "is not exercised in CI. With a model available this would importorskip "
        "shared_llm, build an LLMSafetyClassifier over a real provider, and assert "
        "the lazy backend classifies known-unsafe text."
    ),
)
def test_real_guard_model_backend_placeholder() -> None:  # pragma: no cover - documented seam
    raise AssertionError("unreachable: skip-guarded real-model placeholder")
