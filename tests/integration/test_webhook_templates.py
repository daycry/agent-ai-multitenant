"""Integration tests for the incoming-webhook provider templates (task_13_09).

The templates registry (:mod:`api_server.webhooks.templates`) is the INBOUND
direction of Plan 13: task_13_08 VERIFIES the per-origin HMAC signature against
the project secret BEFORE any work; this layer — strictly on an already-verified
payload — PARSES each provider's payload into ONE normalised internal event
(``{origin, event_type, title, body, refs, actor, url}``) so the mapping phase
(task_13_10) never special-cases a provider.

This suite proves:

  * a representative, real-shaped payload PER provider (GitHub push, GitHub PR
    review, Jira issue created, Sentry error, Linear issue, GitLab MR) parses
    into the normalised event with the RIGHT fields;
  * the signature scheme PER origin is DECLARED on its template (and matches the
    task_13_08 verifier's table — single source of truth);
  * an unknown origin -> a TYPED error (:class:`UnknownOriginError`), not a
    ``KeyError``;
  * a malformed payload (not JSON / not an object / not UTF-8) is HANDLED
    (:class:`MalformedPayloadError`), never an unhandled crash;
  * field extraction is total — a partial / odd payload degrades gracefully
    instead of raising.

Pure-Python: parsing touches no DB, no secret, no network, so this suite needs
no fixtures. It is filed under ``tests/integration`` per the plan's task block.
"""

from __future__ import annotations

import json

import pytest
from api_server.webhooks.signatures import (
    IncomingWebhookOrigin,
    signature_scheme_for,
)
from api_server.webhooks.templates import (
    MalformedPayloadError,
    NormalizedEvent,
    UnknownOriginError,
    WebhookEventType,
    get_template,
    parse_incoming_event,
)

pytestmark = pytest.mark.integration


def _parse(
    origin: IncomingWebhookOrigin, payload: dict, event_type_header: str | None = None
) -> NormalizedEvent:
    return parse_incoming_event(
        origin=origin,
        raw_body=json.dumps(payload).encode("utf-8"),
        event_type_header=event_type_header,
    )


# ===========================================================================
# Per-provider: a representative real-shaped payload -> normalised event
# ===========================================================================
def test_github_push_parses() -> None:
    payload = {
        "ref": "refs/heads/main",
        "after": "9a1b2c3d4e5f6071829",
        "compare": "https://github.com/acme/api/compare/abc...def",
        "repository": {"full_name": "acme/api"},
        "pusher": {"name": "octocat"},
        "sender": {"login": "octocat"},
        "head_commit": {"message": "Fix the thing\n\nlonger body"},
    }
    event = _parse(IncomingWebhookOrigin.GITHUB, payload, event_type_header="push")
    assert event.origin is IncomingWebhookOrigin.GITHUB
    assert event.event_type is WebhookEventType.GITHUB_PUSH
    assert event.title == "Push to acme/api@main"
    assert event.body == "Fix the thing\n\nlonger body"
    assert event.refs["repo"] == "acme/api"
    assert event.refs["branch"] == "main"
    assert event.refs["head_sha"] == "9a1b2c3d4e5f6071829"
    assert event.actor == "octocat"
    assert event.url == "https://github.com/acme/api/compare/abc...def"


def test_github_pr_review_parses_via_event_header() -> None:
    payload = {
        "action": "submitted",
        "review": {
            "state": "approved",
            "body": "LGTM, ship it",
            "html_url": "https://github.com/acme/api/pull/42#pullrequestreview-1",
            "user": {"login": "reviewer-jane"},
        },
        "pull_request": {"number": 42, "title": "Add retry logic"},
        "repository": {"full_name": "acme/api"},
    }
    event = _parse(IncomingWebhookOrigin.GITHUB, payload, event_type_header="pull_request_review")
    assert event.event_type is WebhookEventType.GITHUB_PR_REVIEW
    assert event.title == "Add retry logic"
    assert event.body == "LGTM, ship it"
    assert event.refs["pr_number"] == "42"
    assert event.refs["review_state"] == "approved"
    assert event.refs["repo"] == "acme/api"
    assert event.actor == "reviewer-jane"
    assert event.url == "https://github.com/acme/api/pull/42#pullrequestreview-1"


def test_jira_issue_created_parses() -> None:
    payload = {
        "webhookEvent": "jira:issue_created",
        "user": {"displayName": "Alice Reporter"},
        "issue": {
            "key": "PROJ-123",
            "self": "https://acme.atlassian.net/rest/api/2/issue/10001",
            "fields": {
                "summary": "Login button is broken",
                "description": "Steps to reproduce ...",
                "project": {"key": "PROJ"},
                "issuetype": {"name": "Bug"},
                "reporter": {"displayName": "Alice Reporter"},
            },
        },
    }
    event = _parse(IncomingWebhookOrigin.JIRA, payload)
    assert event.event_type is WebhookEventType.JIRA_ISSUE_CREATED
    assert event.title == "[PROJ-123] Login button is broken"
    assert event.body == "Steps to reproduce ..."
    assert event.refs["issue_key"] == "PROJ-123"
    assert event.refs["project_key"] == "PROJ"
    assert event.refs["issue_type"] == "Bug"
    assert event.actor == "Alice Reporter"
    assert event.url == "https://acme.atlassian.net/rest/api/2/issue/10001"


def test_sentry_error_parses() -> None:
    payload = {
        "action": "created",
        "project": "backend-prod",
        "data": {
            "event": {
                "event_id": "ev-9f8e7d",
                "title": "TypeError: cannot read property 'x' of undefined",
                "culprit": "app/handlers/payment in process",
                "web_url": "https://sentry.io/organizations/acme/issues/555/events/ev-9f8e7d/",
                "issue_id": "555",
            }
        },
    }
    event = _parse(IncomingWebhookOrigin.SENTRY, payload)
    assert event.event_type is WebhookEventType.SENTRY_ERROR
    assert event.title == "TypeError: cannot read property 'x' of undefined"
    assert event.body == "app/handlers/payment in process"
    assert event.refs["project"] == "backend-prod"
    assert event.refs["issue_id"] == "555"
    assert event.refs["event_id"] == "ev-9f8e7d"
    assert event.url.endswith("/ev-9f8e7d/")


def test_linear_issue_parses() -> None:
    payload = {
        "type": "Issue",
        "action": "create",
        "data": {
            "identifier": "ENG-123",
            "title": "Flaky CI on macOS runners",
            "description": "The macOS runners time out intermittently.",
            "url": "https://linear.app/acme/issue/ENG-123",
            "team": {"key": "ENG"},
            "assignee": {"name": "Bob Dev"},
            "creator": {"name": "Carol PM"},
        },
    }
    event = _parse(IncomingWebhookOrigin.LINEAR, payload)
    assert event.event_type is WebhookEventType.LINEAR_ISSUE
    assert event.title == "Flaky CI on macOS runners"
    assert event.body == "The macOS runners time out intermittently."
    assert event.refs["identifier"] == "ENG-123"
    assert event.refs["team_key"] == "ENG"
    assert event.refs["action"] == "create"
    assert event.actor == "Bob Dev"
    assert event.url == "https://linear.app/acme/issue/ENG-123"


def test_gitlab_merge_request_parses() -> None:
    payload = {
        "object_kind": "merge_request",
        "user": {"username": "gl-dave", "name": "Dave GitLab"},
        "project": {"path_with_namespace": "acme/web"},
        "object_attributes": {
            "iid": 7,
            "title": "Refactor auth module",
            "description": "Splits the monolith handler.",
            "action": "open",
            "url": "https://gitlab.com/acme/web/-/merge_requests/7",
        },
    }
    event = _parse(IncomingWebhookOrigin.GITLAB, payload)
    assert event.event_type is WebhookEventType.GITLAB_MERGE_REQUEST
    assert event.title == "Refactor auth module"
    assert event.body == "Splits the monolith handler."
    assert event.refs["project"] == "acme/web"
    assert event.refs["mr_iid"] == "7"
    assert event.refs["action"] == "open"
    assert event.actor == "gl-dave"
    assert event.url == "https://gitlab.com/acme/web/-/merge_requests/7"


# ===========================================================================
# The signature scheme per origin is DECLARED (and matches the verifier table)
# ===========================================================================
@pytest.mark.parametrize(
    "origin",
    [
        IncomingWebhookOrigin.GITHUB,
        IncomingWebhookOrigin.GITLAB,
        IncomingWebhookOrigin.JIRA,
        IncomingWebhookOrigin.SENTRY,
        IncomingWebhookOrigin.LINEAR,
    ],
)
def test_template_declares_signature_scheme(origin: IncomingWebhookOrigin) -> None:
    """Each template declares its signature header/prefix, sourced from the
    SAME table task_13_08's verifier enforces (single source of truth)."""
    template = get_template(origin)
    expected_header, expected_prefixed = signature_scheme_for(origin)
    assert template.signature_header == expected_header
    assert template.signature_prefixed is expected_prefixed
    assert template.origin is origin


def test_github_gitlab_use_prefixed_sha256_scheme() -> None:
    """GitHub/GitLab sign in X-Hub-Signature-256 with a sha256= prefix."""
    gh = get_template(IncomingWebhookOrigin.GITHUB)
    gl = get_template(IncomingWebhookOrigin.GITLAB)
    assert gh.signature_header == "X-Hub-Signature-256"
    assert gh.signature_prefixed is True
    assert gl.signature_header == "X-Hub-Signature-256"
    assert gl.signature_prefixed is True


# ===========================================================================
# Unknown origin -> typed error
# ===========================================================================
def test_generic_origin_has_no_template_typed_error() -> None:
    with pytest.raises(UnknownOriginError) as exc_info:
        get_template(IncomingWebhookOrigin.GENERIC)
    assert exc_info.value.origin == "generic"


def test_parse_unknown_origin_typed_error() -> None:
    with pytest.raises(UnknownOriginError):
        parse_incoming_event(
            origin=IncomingWebhookOrigin.GENERIC,
            raw_body=b'{"x":1}',
        )


# ===========================================================================
# Malformed payload -> handled (not a crash)
# ===========================================================================
def test_non_json_body_raises_malformed() -> None:
    with pytest.raises(MalformedPayloadError):
        parse_incoming_event(
            origin=IncomingWebhookOrigin.GITHUB,
            raw_body=b"this is not json{{{",
            event_type_header="push",
        )


def test_json_array_body_raises_malformed() -> None:
    """A valid-JSON but non-object body (array/scalar) is malformed."""
    with pytest.raises(MalformedPayloadError):
        parse_incoming_event(
            origin=IncomingWebhookOrigin.JIRA,
            raw_body=b"[1, 2, 3]",
        )


def test_non_utf8_body_raises_malformed() -> None:
    with pytest.raises(MalformedPayloadError):
        parse_incoming_event(
            origin=IncomingWebhookOrigin.GITHUB,
            raw_body=b"\xff\xfe\x00not utf8",
            event_type_header="push",
        )


def test_empty_object_payload_degrades_gracefully() -> None:
    """A verified-but-empty payload parses (no crash) with safe defaults."""
    event = parse_incoming_event(
        origin=IncomingWebhookOrigin.GITHUB,
        raw_body=b"{}",
        event_type_header="push",
    )
    assert event.event_type is WebhookEventType.GITHUB_PUSH
    assert event.title == "Push"
    assert event.body == ""
    assert event.refs == {}
    assert event.actor is None
    assert event.url is None


def test_partial_jira_payload_degrades_gracefully() -> None:
    """Missing fields degrade to "" / None / omitted refs, never raising."""
    event = parse_incoming_event(
        origin=IncomingWebhookOrigin.JIRA,
        raw_body=b'{"issue": {"key": "PROJ-9"}}',
    )
    assert event.event_type is WebhookEventType.JIRA_ISSUE_CREATED
    assert event.title == "[PROJ-9]"
    assert event.body == ""
    assert event.refs == {"issue_key": "PROJ-9"}
    assert event.actor is None
    assert event.url is None


def test_wrong_typed_fields_do_not_crash() -> None:
    """A field whose type is wrong (e.g. repository as a string) degrades."""
    payload = {"ref": "refs/heads/dev", "repository": "not-a-dict", "head_commit": 123}
    event = parse_incoming_event(
        origin=IncomingWebhookOrigin.GITHUB,
        raw_body=json.dumps(payload).encode("utf-8"),
        event_type_header="push",
    )
    assert event.event_type is WebhookEventType.GITHUB_PUSH
    assert event.title == "Push to dev"
    assert event.body == ""
    assert event.refs["branch"] == "dev"
    assert "repo" not in event.refs


def test_github_unknown_event_header_falls_back_to_push() -> None:
    """An unknown X-GitHub-Event never crashes — it falls back to the default."""
    event = parse_incoming_event(
        origin=IncomingWebhookOrigin.GITHUB,
        raw_body=b'{"ref": "refs/heads/main"}',
        event_type_header="some_future_event",
    )
    assert event.event_type is WebhookEventType.GITHUB_PUSH
