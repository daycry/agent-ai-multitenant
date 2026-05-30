"""Factuality / citations guardrail (Plan 11, Phase B — task_11_09).

Registers the ``factuality_citations`` guardrail type. It heuristically flags
factual claims in a model's output that are **not** backed by a citation /
source, so the host can warn (or escalate) on potentially-hallucinated facts —
the plan's "hallucination check over numbers" baseline (section 19.5 /
task_11_22).

Hooks
-----
Primary hook is ``post_llm`` (check the model's answer). It works at any hook.

Detection strategy (pure-Python heuristic — no model dependency)
----------------------------------------------------------------
This is a *heuristic*, deliberately not an LLM-as-judge (that heavier path can
be added later behind the content-safety-style seam). It:

  1. Extracts **factual claims** — sentences that contain a numeric claim
     (a percentage, a year, a money amount, a large/decimal number, an
     ordinal/statistic) or a direct quotation (text in quotes).
  2. Detects **citation signals** anywhere in the text — markdown links,
     bare URLs, bracketed references (``[1]``, ``[Smith 2020]``), DOIs, and
     "source/según/fuente/according to/cf." style lead-ins (es + en).
  3. Triggers when there is at least one factual claim and **no** citation
     signal covering it: a sentence carrying a numeric/quoted claim that
     itself contains no citation signal is flagged as *unsupported*.

The aim is a low-cost, explainable nudge, not ground-truth verification, so it
favours recall on obviously-unsupported numeric/quoted claims while not firing
on prose with inline references.

The detection is side-effect-free: the engine applies the action; this module
only *suggests* one — configurable, defaulting to ``warn`` (surface the
unsupported claim; blocking factual output outright is too aggressive a
default).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from shared_guardrails.checks._common import coerce_action, coerce_severity
from shared_guardrails.registry import register_guardrail
from shared_guardrails.types import Action, GuardrailContext, GuardrailResult, Severity

# Sentence splitter: split on .!? followed by whitespace, keep it simple and
# deterministic (no NLP dependency). Newlines also delimit.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

# Numeric-claim signals inside a sentence (a fact a citation should back).
_NUMERIC_CLAIM_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d+(?:[.,]\d+)?\s?%"),  # percentages: "42%", "3.5 %"
    re.compile(r"(?<![\w.])(?:19|20)\d{2}(?![\w.])"),  # years: 1900-2099
    re.compile(r"[$€£]\s?\d[\d.,]*"),  # money amounts
    re.compile(r"\b\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?\b"),  # grouped big numbers
    re.compile(r"\b\d+\.\d+\b"),  # decimals
    re.compile(r"\b\d{4,}\b"),  # large bare integers (>= 1000-ish)
)

# A direct quotation (straight or smart quotes) of a few words.
_QUOTE_CLAIM_RE = re.compile(r"[\"“][^\"”]{8,}[\"”]")

# Citation signals (anywhere). A sentence carrying one is considered supported.
_CITATION_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bhttps?://\S+", re.IGNORECASE),  # bare URL
    re.compile(r"\]\(\s*https?://", re.IGNORECASE),  # markdown link target
    re.compile(r"\[[^\]]*\d[^\]]*\]"),  # [1], [Smith 2020], [12, 13]
    re.compile(r"\bdoi:\s*\S+", re.IGNORECASE),  # DOI
    re.compile(
        r"\b(?:source|sources|reference|references|cited|citation|cf\.?"
        r"|according to|as reported by|per the"
        r"|fuente|fuentes|según|de acuerdo con|referencia)\b",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class ClaimFinding:
    """One factual claim and whether the sentence carrying it is supported."""

    sentence: str
    kind: str  # "numeric" | "quote"
    supported: bool


def _has_citation(text: str) -> bool:
    return any(rx.search(text) for rx in _CITATION_RES)


def _claim_kind(sentence: str) -> str | None:
    if any(rx.search(sentence) for rx in _NUMERIC_CLAIM_RES):
        return "numeric"
    if _QUOTE_CLAIM_RE.search(sentence):
        return "quote"
    return None


def find_unsupported_claims(text: str) -> list[ClaimFinding]:
    """Return factual claims, marking each as supported / unsupported.

    A sentence with a numeric or quoted claim is *unsupported* when neither
    the sentence itself nor (as a fallback) the whole document carries a
    citation signal that we can attribute to it.
    """
    document_cited = _has_citation(text)
    findings: list[ClaimFinding] = []
    for raw in _SENTENCE_SPLIT_RE.split(text):
        sentence = raw.strip()
        if not sentence:
            continue
        kind = _claim_kind(sentence)
        if kind is None:
            continue
        supported = _has_citation(sentence) or document_cited
        findings.append(ClaimFinding(sentence=sentence[:200], kind=kind, supported=supported))
    return findings


class FactualityCitationsGuardrail:
    """Flags factual (numeric/quoted) claims that lack a citation.

    Config:
      - ``require_document_citation`` bool — when ``True``, a citation
        anywhere in the output is NOT enough; each claim's *own* sentence
        must carry a citation. Default ``False`` (a cited document
        suffices).
      - ``severity``         str — default ``low``.
      - ``suggested_action`` str — override the default action. When unset
        the guardrail suggests ``warn``.

    The result ``payload`` carries the count of ``unsupported`` claims and a
    per-claim list of ``{sentence, kind}``.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._strict = bool(config.get("require_document_citation", False))
        self._severity = coerce_severity(config.get("severity"), default=Severity.LOW)
        self._suggested_override = coerce_action(config.get("suggested_action"))

    def _suggested_action(self) -> Action:
        if self._suggested_override is not None:
            return self._suggested_override
        return Action.WARN

    def check(self, context: GuardrailContext) -> GuardrailResult:
        text = context.primary_text()
        if not text:
            return GuardrailResult.ok()

        claims = find_unsupported_claims(text)
        if self._strict:
            # A whole-document citation does not rescue a claim; only the
            # claim's own sentence counts.
            unsupported = [c for c in claims if not _has_citation(c.sentence)]
        else:
            unsupported = [c for c in claims if not c.supported]

        if not unsupported:
            return GuardrailResult(triggered=False)

        return GuardrailResult(
            triggered=True,
            severity=self._severity,
            detail=(f"Found {len(unsupported)} factual claim(s) without a supporting citation."),
            suggested_action=self._suggested_action(),
            payload={
                "unsupported_count": len(unsupported),
                "unsupported": [{"sentence": c.sentence, "kind": c.kind} for c in unsupported],
            },
        )


@register_guardrail("factuality_citations")
def _build_factuality_citations(config: dict[str, Any]) -> FactualityCitationsGuardrail:
    return FactualityCitationsGuardrail(config)


__all__ = ["ClaimFinding", "FactualityCitationsGuardrail", "find_unsupported_claims"]
