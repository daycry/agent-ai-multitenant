"""Domain event contract for the `events:tasks` Redis Stream.

This is the consumer-side view of the contract documented in
ADR 0011. The api-server is the producer (`api_server.events`); both
sides must agree on the field names below.

A stream entry is a flat `dict[str, str]` (Redis Stream fields are
strings). `payload` carries event-specific JSON as a string.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# Event types carried in the `type` field.
EVENT_TASK_CREATED = "task.created"
EVENT_TASK_STATUS_CHANGED = "task.status_changed"

KNOWN_EVENT_TYPES = frozenset({EVENT_TASK_CREATED, EVENT_TASK_STATUS_CHANGED})


@dataclass(frozen=True)
class TaskEvent:
    """A parsed domain event off the stream.

    `stream_id` is the Redis entry id (`<ms>-<seq>`), needed to XACK.
    """

    stream_id: str
    type: str
    tenant_id: str
    project_id: str
    task_id: str
    occurred_at: str
    payload: dict[str, Any]


class MalformedEventError(ValueError):
    """Raised when a stream entry can't be parsed into a TaskEvent.

    The consumer ACKs malformed entries (so a poison message doesn't
    block the group forever) but logs them loudly.
    """


def parse_event(stream_id: str, fields: dict[str, str]) -> TaskEvent:
    """Turn a raw Redis Stream entry into a TaskEvent.

    Raises MalformedEventError when required fields are missing or the
    payload isn't valid JSON.
    """
    required = ("type", "tenant_id", "project_id", "task_id", "occurred_at")
    missing = [k for k in required if not fields.get(k)]
    if missing:
        raise MalformedEventError(f"entry {stream_id} missing fields: {missing}")

    raw_payload = fields.get("payload", "{}")
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise MalformedEventError(f"entry {stream_id} has non-JSON payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise MalformedEventError(f"entry {stream_id} payload is not an object")

    return TaskEvent(
        stream_id=stream_id,
        type=fields["type"],
        tenant_id=fields["tenant_id"],
        project_id=fields["project_id"],
        task_id=fields["task_id"],
        occurred_at=fields["occurred_at"],
        payload=payload,
    )
