"""Pre-configured incoming-webhook provider templates (Plan 13 task_13_09).

A DATA-DRIVEN registry, keyed by :class:`IncomingWebhookOrigin`, that turns each
provider's raw JSON payload into ONE normalised internal event
(:class:`NormalizedEvent`) the mapping phase (task_13_10) acts on. This is the
INBOUND direction of Plan 13: task_13_08 already VERIFIED the per-origin HMAC
signature against the project secret BEFORE any work; here — strictly AFTER a
verified payload — we PARSE that payload into a provider-agnostic shape so the
rest of the system never has to special-case GitHub vs Jira vs Sentry.

The registry is the single source of truth: each template DECLARES which
signature scheme/header its origin signs in (re-using
:func:`api_server.webhooks.signatures.signature_scheme_for`, so the declaration
and the verification table never drift) and HOW to extract the normalised
fields from the payload. Adding a provider variant means editing ONE table.

Normalised event shape (``{origin, event_type, title, body, refs, actor,
url}``):

  * ``origin``      — the :class:`IncomingWebhookOrigin` the payload came from.
  * ``event_type``  — a provider-namespaced, lowercase event kind
                      (``"github.push"``, ``"jira.issue_created"``, ...). A
                      named constant; never a free-form provider string.
  * ``title``       — a short human summary (issue title, PR title, push ref).
  * ``body``        — the longer text (issue/PR body, error message), or "".
  * ``refs``        — provider identifiers worth keying on (issue key, PR
                      number, commit sha, repo full name), as a flat str->str
                      map. Always present (possibly empty); values stringified.
  * ``actor``       — who triggered it (login / display name), or None.
  * ``url``         — the canonical web URL of the subject, or None.

Robustness contract (the test's "malformed payload -> handled, not a crash"):
parsing a provider payload NEVER raises on shape — a missing / wrong-typed
field degrades to "" / None / an omitted ref via the small ``_str`` / ``_get``
helpers. Only two things raise, both typed:

  * a body that is not valid JSON / not a JSON object  -> :class:`MalformedPayloadError`
  * an origin with no template registered              -> :class:`UnknownOriginError`

Nothing here touches the DB, the secret, or the network — it is a pure function
of (origin, bytes), so the test exercises it without any fixtures.
"""

from __future__ import annotations

import enum
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from api_server.webhooks.signatures import (
    IncomingWebhookOrigin,
    signature_scheme_for,
)

# ---------------------------------------------------------------------------
# Errors — both typed, so the caller (and the test) can branch without string
# matching. Neither ever carries the secret or the raw payload.
# ---------------------------------------------------------------------------


class WebhookTemplateError(Exception):
    """Base class for incoming-webhook template parsing errors."""


class UnknownOriginError(WebhookTemplateError):
    """Raised when no template is registered for the requested origin.

    The signature gate (task_13_08) only accepts an origin from the closed
    :class:`IncomingWebhookOrigin` catalogue, so in practice this guards against
    an origin that verifies but has no PARSE template yet — surfaced as a typed
    error rather than a ``KeyError`` so the caller can map it deliberately.
    """

    def __init__(self, origin: str) -> None:
        super().__init__(f"no incoming-webhook template registered for origin {origin!r}")
        self.origin = origin


class MalformedPayloadError(WebhookTemplateError):
    """Raised when a payload is not decodable JSON / not a JSON object.

    A VERIFIED payload that is nonetheless garbage (truncated, not JSON, a bare
    array/scalar) is a handled error, never an unhandled crash. Carries no
    payload content.
    """


# ---------------------------------------------------------------------------
# Normalised event — the provider-agnostic shape every template produces.
# ---------------------------------------------------------------------------


class WebhookEventType(enum.StrEnum):
    """Named, provider-namespaced event kinds (the closed set we normalise to).

    The value is ``"<origin>.<kind>"`` (lowercase, snake_case kind) so it is
    stable to persist and to branch on in the mapping phase (task_13_10) without
    re-deriving the provider from a free-form string.
    """

    GITHUB_PUSH = "github.push"
    GITHUB_PR_REVIEW = "github.pull_request_review"
    GITLAB_MERGE_REQUEST = "gitlab.merge_request"
    JIRA_ISSUE_CREATED = "jira.issue_created"
    SENTRY_ERROR = "sentry.error"
    LINEAR_ISSUE = "linear.issue"


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    """A provider-agnostic incoming event (``{origin, event_type, title, body,
    refs, actor, url}``) — the output of :func:`parse_incoming_event`.

    ``event_type`` is a :class:`WebhookEventType` (a named constant, never a raw
    provider string). ``refs`` is always a (possibly empty) flat str->str map.
    Immutable: the mapping phase reads it, it does not mutate it.
    """

    origin: IncomingWebhookOrigin
    event_type: WebhookEventType
    title: str
    body: str
    refs: Mapping[str, str] = field(default_factory=dict)
    actor: str | None = None
    url: str | None = None


# ---------------------------------------------------------------------------
# Safe extraction helpers — these are what make parsing total (never raise on a
# missing / wrong-typed field). A template composes them; it never reaches into
# the dict directly.
# ---------------------------------------------------------------------------


def _get(payload: Mapping[str, Any], *path: str) -> Any:
    """Walk a nested-dict ``path``; return None if any hop is missing/not a dict.

    ``_get(p, "repository", "full_name")`` is ``p["repository"]["full_name"]``
    but yields None instead of raising when ``repository`` is absent or not a
    mapping — so a malformed/partial payload degrades gracefully.
    """
    current: Any = payload
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _str(value: Any) -> str:
    """Coerce a value to a trimmed string; None / non-scalar -> ""."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, int | float | bool):
        return str(value)
    return ""


def _opt_str(value: Any) -> str | None:
    """Like :func:`_str` but returns None (not "") for an absent/empty value."""
    text = _str(value)
    return text or None


def _refs(**pairs: Any) -> dict[str, str]:
    """Build the ``refs`` map, dropping any pair whose stringified value is empty.

    Keeps ``refs`` a clean, present-only set of identifiers (no ``{"pr": ""}``
    noise when a field is missing).
    """
    out: dict[str, str] = {}
    for key, value in pairs.items():
        text = _str(value)
        if text:
            out[key] = text
    return out


# ---------------------------------------------------------------------------
# Per-provider parsers — each maps a verified payload dict to a NormalizedEvent.
# Real-shaped against the providers' documented webhook payloads.
# ---------------------------------------------------------------------------


def _parse_github_push(payload: Mapping[str, Any]) -> NormalizedEvent:
    """GitHub ``push`` event -> normalised push.

    Title is the ref pushed to (``refs/heads/main`` -> ``main``); body is the
    head commit message; refs carry the repo, ref and head sha; actor is the
    pusher; url is the compare URL.
    """
    ref = _str(_get(payload, "ref"))
    branch = ref.rsplit("/", 1)[-1] if ref else ""
    repo = _str(_get(payload, "repository", "full_name"))
    head_sha = _str(_get(payload, "after"))
    head_message = _str(_get(payload, "head_commit", "message"))
    title = f"Push to {branch}" if branch else "Push"
    if repo:
        title = f"Push to {repo}@{branch}" if branch else f"Push to {repo}"
    actor = _opt_str(_get(payload, "pusher", "name")) or _opt_str(_get(payload, "sender", "login"))
    return NormalizedEvent(
        origin=IncomingWebhookOrigin.GITHUB,
        event_type=WebhookEventType.GITHUB_PUSH,
        title=title,
        body=head_message,
        refs=_refs(repo=repo, ref=ref, branch=branch, head_sha=head_sha),
        actor=actor,
        url=_opt_str(_get(payload, "compare")),
    )


def _parse_github_pr_review(payload: Mapping[str, Any]) -> NormalizedEvent:
    """GitHub ``pull_request_review`` event -> normalised PR review.

    Title is the PR title; body is the review body; refs carry the repo, PR
    number and review state (approved/changes_requested/commented); actor is the
    reviewer; url is the review's HTML URL.
    """
    pr_number = _str(_get(payload, "pull_request", "number"))
    repo = _str(_get(payload, "repository", "full_name"))
    state = _str(_get(payload, "review", "state"))
    title = _str(_get(payload, "pull_request", "title")) or (
        f"PR #{pr_number} review" if pr_number else "Pull request review"
    )
    return NormalizedEvent(
        origin=IncomingWebhookOrigin.GITHUB,
        event_type=WebhookEventType.GITHUB_PR_REVIEW,
        title=title,
        body=_str(_get(payload, "review", "body")),
        refs=_refs(repo=repo, pr_number=pr_number, review_state=state),
        actor=_opt_str(_get(payload, "review", "user", "login")),
        url=_opt_str(_get(payload, "review", "html_url")),
    )


def _parse_gitlab_merge_request(payload: Mapping[str, Any]) -> NormalizedEvent:
    """GitLab ``Merge Request Hook`` event -> normalised merge request.

    GitLab nests the subject under ``object_attributes``. Title/body come from
    there; refs carry the project path, MR iid and action; actor is ``user``;
    url is the MR url.
    """
    attrs = _get(payload, "object_attributes")
    attrs = attrs if isinstance(attrs, Mapping) else {}
    iid = _str(attrs.get("iid"))
    project = _str(_get(payload, "project", "path_with_namespace"))
    action = _str(attrs.get("action"))
    title = _str(attrs.get("title")) or (f"MR !{iid}" if iid else "Merge request")
    return NormalizedEvent(
        origin=IncomingWebhookOrigin.GITLAB,
        event_type=WebhookEventType.GITLAB_MERGE_REQUEST,
        title=title,
        body=_str(attrs.get("description")),
        refs=_refs(project=project, mr_iid=iid, action=action),
        actor=_opt_str(_get(payload, "user", "username"))
        or _opt_str(_get(payload, "user", "name")),
        url=_opt_str(attrs.get("url")),
    )


def _parse_jira_issue_created(payload: Mapping[str, Any]) -> NormalizedEvent:
    """Jira ``jira:issue_created`` webhook -> normalised issue.

    Jira nests the issue under ``issue`` with its data under ``fields``. Title
    is the summary; body is the description; refs carry the issue key, project
    key and issue type; actor is the reporter / event ``user``; url is the
    issue's REST ``self`` link.
    """
    key = _str(_get(payload, "issue", "key"))
    summary = _str(_get(payload, "issue", "fields", "summary"))
    project_key = _str(_get(payload, "issue", "fields", "project", "key"))
    issue_type = _str(_get(payload, "issue", "fields", "issuetype", "name"))
    title = f"[{key}] {summary}".strip() if key else summary or "Jira issue"
    actor = _opt_str(_get(payload, "user", "displayName")) or _opt_str(
        _get(payload, "issue", "fields", "reporter", "displayName")
    )
    return NormalizedEvent(
        origin=IncomingWebhookOrigin.JIRA,
        event_type=WebhookEventType.JIRA_ISSUE_CREATED,
        title=title,
        body=_str(_get(payload, "issue", "fields", "description")),
        refs=_refs(issue_key=key, project_key=project_key, issue_type=issue_type),
        actor=actor,
        url=_opt_str(_get(payload, "issue", "self")),
    )


def _parse_sentry_error(payload: Mapping[str, Any]) -> NormalizedEvent:
    """Sentry issue/error webhook -> normalised error.

    Sentry's ``event_alert`` / issue webhook nests the subject under ``data`` ->
    ``event`` (or ``issue``). Title is the error title; body is the culprit /
    message; refs carry the project slug and issue/event id; url is the web
    ``url`` of the event when present.
    """
    event = _get(payload, "data", "event")
    event = event if isinstance(event, Mapping) else {}
    issue = _get(payload, "data", "issue")
    issue = issue if isinstance(issue, Mapping) else {}
    title = _str(event.get("title")) or _str(issue.get("title")) or "Sentry error"
    project = _str(payload.get("project")) or _str(event.get("project"))
    issue_id = _str(issue.get("id")) or _str(event.get("issue_id"))
    event_id = _str(event.get("event_id")) or _str(event.get("id"))
    body = _str(event.get("culprit")) or _str(event.get("message")) or _str(issue.get("culprit"))
    return NormalizedEvent(
        origin=IncomingWebhookOrigin.SENTRY,
        event_type=WebhookEventType.SENTRY_ERROR,
        title=title,
        body=body,
        refs=_refs(project=project, issue_id=issue_id, event_id=event_id),
        actor=None,
        url=_opt_str(event.get("web_url"))
        or _opt_str(issue.get("web_url"))
        or _opt_str(payload.get("url")),
    )


def _parse_linear_issue(payload: Mapping[str, Any]) -> NormalizedEvent:
    """Linear ``Issue`` webhook -> normalised issue.

    Linear sends ``{type, action, data: {...}}``. Title is the issue title;
    body is the description; refs carry the issue identifier (e.g. ``ENG-123``),
    team key and action; actor is the assignee/creator name; url is the issue
    url.
    """
    data = _get(payload, "data")
    data = data if isinstance(data, Mapping) else {}
    identifier = _str(data.get("identifier"))
    team_key = _str(_get(data, "team", "key"))
    action = _str(payload.get("action"))
    title = _str(data.get("title")) or (identifier or "Linear issue")
    actor = _opt_str(_get(data, "assignee", "name")) or _opt_str(_get(data, "creator", "name"))
    return NormalizedEvent(
        origin=IncomingWebhookOrigin.LINEAR,
        event_type=WebhookEventType.LINEAR_ISSUE,
        title=title,
        body=_str(data.get("description")),
        refs=_refs(identifier=identifier, team_key=team_key, action=action),
        actor=actor,
        url=_opt_str(data.get("url")),
    )


# ---------------------------------------------------------------------------
# The registry — ONE source of truth by origin. Each template declares its
# signature scheme (from the same table the verifier enforces) + its parser.
# ---------------------------------------------------------------------------

_PayloadParser = Callable[[Mapping[str, Any]], NormalizedEvent]


@dataclass(frozen=True, slots=True)
class WebhookTemplate:
    """A pre-configured provider template (Plan 13 task_13_09).

    Binds an :class:`IncomingWebhookOrigin` to (a) the signature scheme its
    sender uses — the header it signs in and whether the digest is ``sha256=``
    prefixed, sourced from :func:`signature_scheme_for` so the template and the
    task_13_08 verifier never disagree — and (b) the parser that turns a
    verified payload into a :class:`NormalizedEvent`.
    """

    origin: IncomingWebhookOrigin
    signature_header: str
    signature_prefixed: bool
    parser: _PayloadParser

    def parse(self, payload: Mapping[str, Any]) -> NormalizedEvent:
        """Parse an already-JSON-decoded payload dict into a normalised event."""
        return self.parser(payload)


def _build_template(origin: IncomingWebhookOrigin, parser: _PayloadParser) -> WebhookTemplate:
    """Build a template, sourcing its signature scheme from the verifier table."""
    header, prefixed = signature_scheme_for(origin)
    return WebhookTemplate(
        origin=origin,
        signature_header=header,
        signature_prefixed=prefixed,
        parser=parser,
    )


# Registry keyed by origin. ``generic`` deliberately has NO template: a generic
# sender carries no provider-specific schema to normalise, so requesting its
# template is an UnknownOriginError (the mapping phase handles generic events by
# their declared event_type header, not via a parser).
_TEMPLATES: dict[IncomingWebhookOrigin, WebhookTemplate] = {
    IncomingWebhookOrigin.GITHUB: _build_template(IncomingWebhookOrigin.GITHUB, _parse_github_push),
    IncomingWebhookOrigin.GITLAB: _build_template(
        IncomingWebhookOrigin.GITLAB, _parse_gitlab_merge_request
    ),
    IncomingWebhookOrigin.JIRA: _build_template(
        IncomingWebhookOrigin.JIRA, _parse_jira_issue_created
    ),
    IncomingWebhookOrigin.SENTRY: _build_template(
        IncomingWebhookOrigin.SENTRY, _parse_sentry_error
    ),
    IncomingWebhookOrigin.LINEAR: _build_template(
        IncomingWebhookOrigin.LINEAR, _parse_linear_issue
    ),
}

# GitHub serves TWO event kinds on the SAME origin (push + PR review); the
# generic template entry above picks push, but the endpoint knows the actual
# kind from the ``X-GitHub-Event`` header. This sub-table routes a GitHub
# payload to the right parser by that header value; an unknown header falls back
# to the registry default (push), so a new GitHub event never crashes.
_GITHUB_EVENT_PARSERS: dict[str, _PayloadParser] = {
    "push": _parse_github_push,
    "pull_request_review": _parse_github_pr_review,
}


def get_template(origin: IncomingWebhookOrigin) -> WebhookTemplate:
    """Return the template for an origin, or raise :class:`UnknownOriginError`.

    The single lookup point — used by the endpoint and the test to discover a
    provider's signature scheme + parser. ``generic`` and any origin without a
    registered template raise.
    """
    template = _TEMPLATES.get(origin)
    if template is None:
        raise UnknownOriginError(origin.value)
    return template


def _decode_payload(raw_body: bytes) -> Mapping[str, Any]:
    """Decode raw bytes to a JSON object, or raise :class:`MalformedPayloadError`.

    A verified body that is not UTF-8, not JSON, or not a JSON OBJECT (a bare
    array / scalar carries no event to normalise) is a handled, typed error.
    """
    try:
        decoded = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MalformedPayloadError("incoming webhook body is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise MalformedPayloadError("incoming webhook body is not a JSON object")
    return decoded


def parse_incoming_event(
    *,
    origin: IncomingWebhookOrigin,
    raw_body: bytes,
    event_type_header: str | None = None,
) -> NormalizedEvent:
    """Parse a VERIFIED incoming webhook body into a :class:`NormalizedEvent`.

    The single entry point for the mapping phase (task_13_10). Selects the
    origin's template (or :class:`UnknownOriginError`), decodes the body (or
    :class:`MalformedPayloadError`), and — for GitHub, which multiplexes several
    event kinds on one origin — routes by ``event_type_header``
    (``X-GitHub-Event``) to the right parser. Field extraction itself never
    raises: a missing/odd field degrades to "" / None / an omitted ref.

    Pre-condition (task_13_08): the body's HMAC signature has ALREADY been
    verified against the project secret. This function does no crypto, no DB,
    no network — it is a pure function of (origin, bytes, header).
    """
    template = get_template(origin)
    payload = _decode_payload(raw_body)

    if origin is IncomingWebhookOrigin.GITHUB and event_type_header:
        parser = _GITHUB_EVENT_PARSERS.get(event_type_header.strip().lower())
        if parser is not None:
            return parser(payload)

    return template.parse(payload)


__all__ = [
    "MalformedPayloadError",
    "NormalizedEvent",
    "UnknownOriginError",
    "WebhookEventType",
    "WebhookTemplate",
    "WebhookTemplateError",
    "get_template",
    "parse_incoming_event",
]
