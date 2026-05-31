"""Pydantic schemas for the Copilot Device Flow surface (Plan 11.2 task_11_2_03).

Request/response shapes for ``/admin/llm/copilot/device-flow/{start,poll}``.

Secret discipline (CLAUDE.md / ADR 0028): NONE of these models carries a
credential. ``/start`` returns the operator-facing ``user_code`` +
``verification_uri`` and the opaque ``device_code`` the browser hands back
to ``/poll`` (the device code is a short-lived flow handle, NOT a
credential). ``/poll`` returns only a status + an ``authorized`` boolean —
the minted GitHub OAuth token is written to Vault by the router and is
NEVER serialised here.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

_BASE_CONFIG = ConfigDict(str_strip_whitespace=True)


class DeviceFlowStartRequest(BaseModel):
    """Start the device flow for an existing ``copilot`` provider."""

    model_config = _BASE_CONFIG

    provider_id: UUID


class DeviceFlowStartResponse(BaseModel):
    """The codes the operator + the browser need to drive the flow.

    ``user_code`` / ``verification_uri`` are shown to the operator;
    ``device_code`` / ``interval`` / ``expires_in`` are passed back to
    ``/poll``. No credential is present (the token is minted only on a
    successful poll, and even then never leaves Vault).
    """

    model_config = _BASE_CONFIG

    provider_id: UUID
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


class DeviceFlowPollRequest(BaseModel):
    """One poll attempt for a started device flow."""

    model_config = _BASE_CONFIG

    provider_id: UUID
    device_code: str
    # The polling interval GitHub suggested (default 5s); the response may
    # bump it on a slow_down so the UI can back off.
    interval: int = Field(default=5, ge=1, le=60)


class DeviceFlowPollResponse(BaseModel):
    """Result of one poll. Secret-free.

    ``status`` is one of ``authorized`` / ``pending`` / ``slow_down`` /
    ``expired`` / ``denied``. ``authorized`` is the boolean the UI flips to
    success — and is the ONLY terminal-success signal; the token itself
    never appears here (it is in Vault). ``interval`` echoes the seconds to
    wait before the next poll (backed off on ``slow_down``); ``None`` once
    authorised or terminal.
    """

    model_config = _BASE_CONFIG

    status: str
    authorized: bool
    interval: int | None = None


__all__ = [
    "DeviceFlowPollRequest",
    "DeviceFlowPollResponse",
    "DeviceFlowStartRequest",
    "DeviceFlowStartResponse",
]
