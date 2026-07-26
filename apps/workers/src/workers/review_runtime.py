"""Review-runtime: long-lived container for the human plan validation
step (Plan 06 Fase G).

When all tasks of a plan are ``done``, the orchestrator transitions
the plan to ``pending_human_validation`` and spawns a *review-runtime*
container: the plan's worktree mounted + aux compose services running
+ the app's main service up. The human gets a signed URL, opens it
in a browser, plays with the app, ticks the checklist (the plan's
``human_*`` tests), and either approves or rejects the plan.

Nine tasks of Fase G all live in this module:

  * :class:`ReviewRuntimeSpec` (06_26) — what to compose.
  * :func:`sign_review_url` / :func:`verify_review_url` (06_27) —
    HMAC-signed URL with caducidad = the session's expires_at.
  * The terminal-web (06_28) and websocket-logs (06_29) endpoints
    live in api-server (see ``routers/review.py``); here we
    only model the session lifecycle they consult.
  * The "rerun tests" button (06_30) calls back into worker-test
    via :meth:`ReviewSession.queue_rerun`.
  * The checklist (06_31) is rendered on the frontend; the model is
    :class:`HumanCheckItem`.
  * Suspension after 4 h idle (06_32), 48 h verdict timeout (06_33),
    and tenant cap (06_34) live in :class:`ReviewRuntimeManager`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

import structlog

_log = structlog.get_logger("workers.review_runtime")

# Defaults from Plan 06.
DEFAULT_VERDICT_TIMEOUT_S = 48 * 60 * 60  # 48 h
DEFAULT_IDLE_SUSPEND_S = 4 * 60 * 60  # 4 h
DEFAULT_TENANT_CAP = 5  # max concurrent review-runtimes per tenant

ReviewStatus = Literal["running", "suspended", "approved", "rejected", "expired", "cancelled"]


# ---------------------------------------------------------------------------
# task_06_26 — Review runtime spec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuxComposeService:
    """One sidecar that runs alongside the main app in the review-runtime.

    Same shape as workers.test_runtime.AuxServiceSpec but persistent
    (lives as long as the review session does — no per-task teardown)."""

    name: str
    image: str
    env: Mapping[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# task_06_31 — Human check item (defined first so ReviewRuntimeSpec can
# reference the type non-string)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HumanCheckItem:
    """One ``human_*`` test the reviewer must tick.

    The plan's ``Tests Humanos del Plan`` block in the markdown
    roadmap is the source. The orchestrator parses each entry into
    one :class:`HumanCheckItem` and attaches it to the spec.
    """

    id: str
    description: str
    hint: str | None = None
    checklist: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewRuntimeSpec:
    """The compose definition for one review-runtime session."""

    plan_id: str
    project_id: str
    tenant_id: str
    repo_name: str
    worktree_host_path: str
    main_image: str
    """The image that runs the project's main service (e.g.
    ``backend:latest``). The platform doesn't build this — the
    project's CI does, and the worker references it by tag."""
    main_port: int = 8080
    aux_services: tuple[AuxComposeService, ...] = ()
    expires_at: float = 0.0
    """Absolute time when the session auto-expires. The manager fills
    this in with ``now + DEFAULT_VERDICT_TIMEOUT_S`` at create time."""

    human_checklist: tuple[HumanCheckItem, ...] = ()


# ---------------------------------------------------------------------------
# task_06_27 — Signed URLs
# ---------------------------------------------------------------------------


def sign_review_url(
    *,
    base_url: str,
    session_id: str,
    expires_at: float,
    secret: bytes,
) -> str:
    """Return a one-shot URL the reviewer opens in their browser.

    Layout: ``{base_url}/review/{session_id}?exp={ts}&sig={hmac}``.
    ``sig`` is HMAC-SHA256(``session_id|exp``) base64-urlsafe-encoded
    so it survives a URL without URL-encoding.

    The URL is read-only for cryptographic purposes — anyone with it
    can open the session, but they can't extend the expiry without
    re-signing. The api-server's review router verifies on every
    request.
    """
    payload = f"{session_id}|{int(expires_at)}".encode()
    digest = hmac.new(secret, payload, hashlib.sha256).digest()
    sig = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    base = base_url.rstrip("/")
    return f"{base}/review/{session_id}?exp={int(expires_at)}&sig={sig}"


def verify_review_url(
    *,
    session_id: str,
    expires_at: float,
    sig: str,
    secret: bytes,
    now: float | None = None,
) -> bool:
    """Validate a signed review URL.

    Returns False on any of: bad signature, expired ``exp``,
    malformed input. Constant-time compare via ``hmac.compare_digest``.
    """
    ref = now if now is not None else time.time()
    if ref > expires_at:
        return False
    payload = f"{session_id}|{int(expires_at)}".encode()
    expected = hmac.new(secret, payload, hashlib.sha256).digest()
    expected_sig = base64.urlsafe_b64encode(expected).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(expected_sig, sig)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@dataclass
class ReviewSession:
    """One running (or suspended/finished) review-runtime."""

    id: str
    spec: ReviewRuntimeSpec
    status: ReviewStatus = "running"
    created_at: float = 0.0
    last_activity_at: float = 0.0
    expires_at: float = 0.0
    container_ids: tuple[str, ...] = ()
    verdict: Literal["approved", "rejected"] | None = None
    rejection_reason: str | None = None
    rerun_requested: bool = False

    def is_terminal(self) -> bool:
        return self.status in {"approved", "rejected", "expired", "cancelled"}

    def time_idle(self, now: float | None = None) -> float:
        ref = now if now is not None else time.time()
        return max(0.0, ref - self.last_activity_at)


__all__ = [
    "AuxComposeService",
    "DEFAULT_IDLE_SUSPEND_S",
    "DEFAULT_TENANT_CAP",
    "DEFAULT_VERDICT_TIMEOUT_S",
    "HumanCheckItem",
    "ReviewRuntimeSpec",
    "ReviewSession",
    "ReviewStatus",
    "sign_review_url",
    "verify_review_url",
]
