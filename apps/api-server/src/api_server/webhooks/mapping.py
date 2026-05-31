"""Webhook -> system action MAPPING (Plan 13 Fase C, task_13_10).

The INBOUND pipeline so far: task_13_08 VERIFIES the per-origin HMAC signature
against the project secret BEFORE any work; task_13_09 PARSES a verified payload
into ONE provider-agnostic :class:`NormalizedEvent`. THIS layer is the next
hop — purely a FUNCTION of ``(NormalizedEvent, the config's mappings)`` — that
decides WHICH configurable system action a given event triggers in the target
project and renders its title/body TEMPLATES from the event. The actual
DB-touching execution lives in :mod:`api_server.webhooks.actions`; this module
is deliberately side-effect-free so it is trivially testable.

A config (``incoming_webhook_configs.action_mappings``) carries a LIST of rules,
each a small dict:

    {"event_type": "github.pull_request_review",   # which normalised event
     "action": "create_task",                       # what to do
     "title_template": "Review: {title}",           # optional templating
     "body_template": "{body}\n\nby {actor}",
     "target_task_id": "..."}                        # required for comment/escalate

Resolution (``resolve_action``):

  * the FIRST rule whose ``event_type`` equals the event's
    :attr:`NormalizedEvent.event_type` wins (order = priority); no match -> the
    event is recorded but triggers NO action (a no-op, not an error);
  * the rule's ``action`` must be a known :class:`WebhookActionKind`
    (``create_task`` / ``comment`` / ``escalate``); an unknown action is a typed
    :class:`InvalidMappingError` (operator misconfiguration, surfaced not
    swallowed);
  * ``comment`` / ``escalate`` REQUIRE a ``target_task_id`` (they act on an
    existing task); ``create_task`` forbids it.

Templating (``render_template``) is intentionally minimal + SAFE: Python
``str.format_map`` over a fixed, total field set drawn from the event
(``title`` / ``body`` / ``actor`` / ``url`` / ``origin`` / ``event_type`` + every
``refs`` key, missing -> ""). A template referencing an unknown placeholder
degrades to "" rather than raising (a misconfigured ``{nope}`` never 500s a
webhook). No ``str.format`` attribute/index access (``{x.__class__}``) — the
mapping is treated as a flat dict of strings, so a template can't reach into
object internals.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from api_server.webhooks.templates import NormalizedEvent, WebhookEventType


class WebhookActionKind(enum.StrEnum):
    """The closed set of system actions an incoming event can trigger.

    Persisted verbatim in each ``action_mappings`` rule's ``action`` field, so
    never rename an existing member (a stored config would dangle).
    """

    CREATE_TASK = "create_task"
    COMMENT = "comment"
    ESCALATE = "escalate"


class InvalidMappingError(Exception):
    """Raised when a mapping rule is malformed (operator misconfiguration).

    Surfaced (not swallowed) so a bad config is visible — e.g. an unknown
    ``action``, or a ``comment`` / ``escalate`` rule missing its
    ``target_task_id``. Carries no secret / payload content.
    """


@dataclass(frozen=True, slots=True)
class ResolvedAction:
    """A fully-resolved, ready-to-execute action (the output of resolve_action).

    Immutable. ``kind`` is the action; ``title`` / ``body`` are the rendered
    templates (always present, possibly ""); ``target_task_id`` is the existing
    task a ``comment`` / ``escalate`` acts on (None for ``create_task``).
    """

    kind: WebhookActionKind
    title: str
    body: str
    target_task_id: str | None = None


class _SafeFormatDict(dict[str, str]):
    """A format_map backing dict that yields "" for any missing key.

    Makes ``str.format_map`` total: a template referencing an unknown
    placeholder (``{nope}``) renders "" instead of raising ``KeyError`` — a
    misconfigured template degrades gracefully, never 500s a webhook.
    """

    def __missing__(self, key: str) -> str:  # - dict hook
        return ""


def _event_fields(event: NormalizedEvent) -> _SafeFormatDict:
    """Flatten a normalised event to the flat str->str map templates draw from.

    The fixed base fields plus every ``refs`` key (e.g. ``{pr_number}``,
    ``{issue_key}``). All values are strings; None becomes "". refs keys are
    added last but never shadow a base field name collision risk because the
    base set is namespaced/distinct from provider ref keys in practice; on a
    genuine clash the base field wins (added first, refs do not overwrite it).
    """
    fields = _SafeFormatDict()
    fields["title"] = event.title or ""
    fields["body"] = event.body or ""
    fields["actor"] = event.actor or ""
    fields["url"] = event.url or ""
    fields["origin"] = event.origin.value
    fields["event_type"] = event.event_type.value
    for key, value in event.refs.items():
        fields.setdefault(key, value)
    return fields


def render_template(template: str | None, event: NormalizedEvent) -> str:
    """Render one template string against an event's safe field map.

    ``None`` / "" template -> "". Uses ``str.format_map`` over a
    :class:`_SafeFormatDict`, so an unknown ``{placeholder}`` becomes "" rather
    than raising. A malformed brace (a lone ``{``) degrades to the raw template
    text — a webhook is never failed by a bad template.
    """
    if not template:
        return ""
    try:
        return template.format_map(_event_fields(event))
    except (ValueError, IndexError):
        # Malformed format spec (lone brace, positional {0}); fall back to the
        # literal template rather than failing the inbound event.
        return template


def _rule_get(rule: Mapping[str, Any], key: str) -> str | None:
    """Read a string field from a rule dict, or None when absent/blank."""
    value = rule.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _matches(rule: Mapping[str, Any], event_type: WebhookEventType) -> bool:
    """True if a rule targets this event_type (exact match) or is a wildcard.

    A rule with ``event_type`` ``"*"`` (or absent) matches ANY event, so a
    config can declare a single catch-all action; otherwise the value must
    equal the normalised :class:`WebhookEventType` value exactly.
    """
    declared = _rule_get(rule, "event_type")
    if declared is None or declared == "*":
        return True
    return declared == event_type.value


def resolve_action(event: NormalizedEvent, action_mappings: list[Any]) -> ResolvedAction | None:
    """Resolve the FIRST matching mapping rule into a :class:`ResolvedAction`.

    Returns None when no rule matches (the event is recorded, no action runs —
    a deliberate no-op, not an error). Raises :class:`InvalidMappingError` when
    a MATCHING rule is malformed (unknown action; comment/escalate without a
    target task; create_task WITH a target task). Renders the title/body
    templates from the event in the process.

    The first matching rule wins (list order = priority), so a config can put a
    specific ``event_type`` rule ahead of a ``"*"`` catch-all.
    """
    for raw in action_mappings:
        if not isinstance(raw, Mapping):
            continue
        if not _matches(raw, event.event_type):
            continue

        action_value = _rule_get(raw, "action")
        if action_value is None:
            raise InvalidMappingError("mapping rule is missing an 'action'")
        try:
            kind = WebhookActionKind(action_value)
        except ValueError as exc:
            raise InvalidMappingError(
                f"unknown webhook action {action_value!r} in mapping rule"
            ) from exc

        target_task_id = _rule_get(raw, "target_task_id")
        if kind is WebhookActionKind.CREATE_TASK:
            if target_task_id is not None:
                raise InvalidMappingError("create_task mapping rule must not set target_task_id")
        elif target_task_id is None:
            raise InvalidMappingError(f"{kind.value} mapping rule requires a target_task_id")

        title = render_template(_rule_get(raw, "title_template"), event) or event.title
        body = render_template(_rule_get(raw, "body_template"), event) or event.body
        return ResolvedAction(
            kind=kind,
            title=title,
            body=body,
            target_task_id=target_task_id,
        )

    return None


__all__ = [
    "InvalidMappingError",
    "ResolvedAction",
    "WebhookActionKind",
    "render_template",
    "resolve_action",
]
