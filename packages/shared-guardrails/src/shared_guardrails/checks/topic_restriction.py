"""Topic-restriction guardrail (Plan 11, Phase B — task_11_09).

Registers the ``topic_restriction`` guardrail type. It keeps a model's output
*within* a set of allowed topics and/or *away from* a set of forbidden topics,
so the host can flag (or block) off-topic / out-of-scope answers — the plan's
"topic adherence" baseline for the planning chat (section 19.5 / task_11_22).

Hooks
-----
Works at any hook; most useful at ``post_llm`` (did the answer stay on topic?)
and ``pre_llm`` (is the user steering off topic?).

Two restriction modes (configure either or both)
-------------------------------------------------
  * **allowed_topics** (``mode="stay_within"`` behaviour) — the text must
    touch at least one allowed topic; output that matches *none* of them is
    flagged as off-topic.
  * **forbidden_topics** (``mode="stay_away"`` behaviour) — the text must
    not touch any forbidden topic; a match is flagged.

A "topic" is a label with one or more keyword/phrase cues. Matching is a
case-insensitive whole-word/phrase containment heuristic (pure Python). For a
semantic upgrade the matcher lives behind an injectable :class:`TopicMatcher`
seam (``match(text, topics) -> set[str]``) so an embedding-based matcher can be
plugged later behind an optional extra — exactly like ``pii``'s ``analyzer``
and ``prompt_injection``'s ``detector`` seams. The default backend is the
keyword matcher; no heavy dependency is shipped by this task.

The detection is side-effect-free: the engine applies the action; this module
only *suggests* one — configurable, defaulting to ``warn``.
"""

from __future__ import annotations

import re
from typing import Any, Protocol, runtime_checkable

from shared_guardrails.checks._common import coerce_action, coerce_severity
from shared_guardrails.exceptions import GuardrailConfigError
from shared_guardrails.registry import register_guardrail
from shared_guardrails.types import Action, GuardrailContext, GuardrailResult, Severity

# A topic spec: {label: [cue, cue, ...]}.
TopicSpec = dict[str, list[str]]


@runtime_checkable
class TopicMatcher(Protocol):
    """Returns the set of topic labels whose cues appear in ``text``.

    The shipped backend is :class:`KeywordTopicMatcher`. An embedding-based
    matcher (optional extra, lazily imported) can satisfy this Protocol and be
    injected via the guardrail's ``matcher`` config key without touching the
    engine.
    """

    def match(self, text: str, topics: TopicSpec) -> set[str]: ...


class KeywordTopicMatcher:
    """Case-insensitive whole-word/phrase containment matcher (pure Python)."""

    def match(self, text: str, topics: TopicSpec) -> set[str]:
        hay = text.lower()
        hit: set[str] = set()
        for label, cues in topics.items():
            for cue in cues:
                needle = cue.lower().strip()
                if not needle:
                    continue
                # Word-boundary match so "art" doesn't match "start".
                if re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", hay):
                    hit.add(label)
                    break
        return hit


def _coerce_topics(value: Any, *, field: str) -> TopicSpec:
    """Coerce ``{label: [cues]}`` or ``[label, ...]`` into a TopicSpec.

    A bare list of strings is treated as labels that are their own single cue
    (the common case ``allowed_topics: ["billing", "shipping"]``).
    """
    if value is None:
        return {}
    if isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            raise GuardrailConfigError(
                f"topic_restriction guardrail '{field}' list must contain strings."
            )
        return {str(item): [str(item)] for item in value}
    if isinstance(value, dict):
        spec: TopicSpec = {}
        for label, cues in value.items():
            if isinstance(cues, str):
                spec[str(label)] = [cues]
            elif isinstance(cues, list) and all(isinstance(c, str) for c in cues):
                spec[str(label)] = [str(c) for c in cues]
            else:
                raise GuardrailConfigError(
                    f"topic_restriction guardrail '{field}' cues must be a string or list of "
                    "strings."
                )
        return spec
    raise GuardrailConfigError(
        f"topic_restriction guardrail '{field}' must be a list or a mapping."
    )


class TopicRestrictionGuardrail:
    """Flags output that strays from allowed topics or into forbidden ones.

    Config (configure at least one of allowed/forbidden):
      - ``allowed_topics``   list[str] | {label: [cues]} — output must touch
        at least one of these; matching *none* is off-topic.
      - ``forbidden_topics`` list[str] | {label: [cues]} — output must touch
        none of these; a match is flagged.
      - ``matcher``          (seam) inject a pre-built :class:`TopicMatcher`.
      - ``severity``         str — default ``low``.
      - ``suggested_action`` str — override the default action. When unset
        the guardrail suggests ``warn``.

    The result ``payload`` carries ``reason`` (``"off_topic"`` |
    ``"forbidden_topic"``), the ``allowed_hits`` / ``forbidden_hits`` topic
    labels, and the set of ``configured`` allowed topics for the audit log.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._allowed = _coerce_topics(config.get("allowed_topics"), field="allowed_topics")
        self._forbidden = _coerce_topics(config.get("forbidden_topics"), field="forbidden_topics")
        if not self._allowed and not self._forbidden:
            raise GuardrailConfigError(
                "topic_restriction guardrail requires 'allowed_topics' and/or 'forbidden_topics'."
            )
        injected = config.get("matcher")
        if injected is not None and not isinstance(injected, TopicMatcher):
            raise GuardrailConfigError(
                "topic_restriction guardrail 'matcher' must implement the TopicMatcher protocol."
            )
        self._matcher: TopicMatcher = injected or KeywordTopicMatcher()
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

        forbidden_hits = (
            sorted(self._matcher.match(text, self._forbidden)) if self._forbidden else []
        )
        allowed_hits = sorted(self._matcher.match(text, self._allowed)) if self._allowed else []

        # Forbidden takes precedence: a banned topic is the stronger signal.
        if forbidden_hits:
            return GuardrailResult(
                triggered=True,
                severity=self._severity,
                detail=f"Output touches forbidden topic(s): {', '.join(forbidden_hits)}.",
                suggested_action=self._suggested_action(),
                payload={
                    "reason": "forbidden_topic",
                    "forbidden_hits": forbidden_hits,
                    "allowed_hits": allowed_hits,
                },
            )

        # Off-topic: allowed topics configured but none matched.
        if self._allowed and not allowed_hits:
            return GuardrailResult(
                triggered=True,
                severity=self._severity,
                detail="Output is off-topic: it touches none of the allowed topics.",
                suggested_action=self._suggested_action(),
                payload={
                    "reason": "off_topic",
                    "forbidden_hits": forbidden_hits,
                    "allowed_hits": allowed_hits,
                    "configured": sorted(self._allowed),
                },
            )

        return GuardrailResult(triggered=False)


@register_guardrail("topic_restriction")
def _build_topic_restriction(config: dict[str, Any]) -> TopicRestrictionGuardrail:
    return TopicRestrictionGuardrail(config)


__all__ = [
    "KeywordTopicMatcher",
    "TopicMatcher",
    "TopicRestrictionGuardrail",
    "TopicSpec",
]
