"""Channel adapter interface + the ``in_app`` no-op default (task_10_02).

The real channel adapters — Telegram, Email (SMTP), Slack, Teams,
Discord, WhatsApp, SMS, outbound webhooks — land in Plan 10 Fase B/C.
Fase A defines the *contract* every adapter implements so the dispatch
path (``notification_dispatcher.tasks.send_notification``) is testable end
to end today, and ships the ``in_app`` adapter as the default: an in-app
notification is "delivered" the moment its ``notification_logs`` row is
written, so the adapter is a no-op that simply reports success.

Secret handling (CLAUDE.md: NO plaintext secrets). A channel's secret is
resolved (Vault ``secret_ref`` or Fernet ``secret_encrypted``) into a
:class:`ResolvedChannel` *in memory* by the dispatcher and handed to the
adapter via :class:`ChannelMessage.secret`; an adapter never touches the
DB, never logs the secret, and never persists it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ChannelMessage:
    """Everything an adapter needs to deliver one notification.

    Built by the dispatcher from the resolved channel + the rendered
    payload. Immutable — an adapter must not mutate it.
    """

    # The transport this message goes over (matches NotificationChannelType).
    channel_type: str
    # Where the message is addressed (chat id, email, phone, webhook URL).
    # Non-secret — safe to log / persist on the NotificationLog.
    target: str | None
    # The rendered body (Jinja2 templating lands in task_10_03; for now a
    # caller passes the final text / structured payload directly).
    body: str
    # Optional structured payload (Slack blocks, Teams card, Discord embed).
    structured: dict[str, Any] | None = None
    # The channel's plaintext secret, resolved in memory at send time
    # (NEVER persisted, NEVER logged by an adapter). None for secretless
    # channels such as ``in_app``.
    secret: str | None = None
    # Non-secret transport config (SMTP host/port, …) from the channel row.
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeliveryResult:
    """The outcome of one adapter ``send`` attempt.

    ``ok=False`` means a terminal failure for this attempt; the dispatcher
    records it as ``failed`` and dead-letters it. The retry/backoff policy
    (task_10_13) is layered on top later — Fase A never blind-retries.
    """

    ok: bool
    # Provider-side id / receipt when the channel returns one (Slack ts,
    # SMTP message-id, …). Optional.
    provider_message_id: str | None = None
    # Human-readable failure reason when ``ok`` is False (truncated by the
    # caller before it reaches the NotificationLog.error column).
    error: str | None = None


class ChannelSendError(RuntimeError):
    """An adapter raises this to signal a non-recoverable send failure.

    The dispatcher catches it, records the log row as ``failed`` and
    dead-letters the send. Adapters may also return ``DeliveryResult(ok=
    False, ...)`` instead of raising — both are treated as a failed send.
    """


@runtime_checkable
class ChannelAdapter(Protocol):
    """The contract every channel adapter implements (Fase B/C).

    ``channel_type`` is the transport this adapter handles (one of
    :class:`~api_server.db.notification.NotificationChannelType`). ``send``
    delivers one :class:`ChannelMessage` and reports a
    :class:`DeliveryResult`; it must be safe to call from an async context
    and must never touch the DB or log the secret.
    """

    channel_type: str

    async def send(self, message: ChannelMessage) -> DeliveryResult:
        """Deliver ``message``. Return the result; raise
        :class:`ChannelSendError` (or return ``ok=False``) on failure."""
        ...


class InAppAdapter:
    """The default ``in_app`` adapter — a no-op success.

    An in-app notification is delivered the instant its
    ``notification_logs`` row is written (the in-app inbox UI, task_10_16,
    reads those rows), so there is no external transport to call. This
    adapter keeps the dispatch path uniform: the dispatcher always routes
    through *some* adapter, and the ``in_app`` one simply reports success.
    """

    channel_type: str = "in_app"

    async def send(self, message: ChannelMessage) -> DeliveryResult:
        # No external transport: the in-app inbox row IS the delivery. Echo
        # the (non-secret) target as the "receipt" so the dispatcher has a
        # stable provider id to record.
        return DeliveryResult(ok=True, provider_message_id=message.target)


# The adapter registry the dispatcher resolves a channel_type through.
# Fase B/C register their adapters here (Telegram, Email, …). Fase A ships
# only the in_app no-op so the wire path is complete and testable.
_ADAPTERS: dict[str, ChannelAdapter] = {
    InAppAdapter.channel_type: InAppAdapter(),
}


def register_adapter(adapter: ChannelAdapter) -> None:
    """Register (or replace) the adapter for ``adapter.channel_type``.

    Fase B/C call this at import time; tests use it to inject a fake
    adapter that records / fails a send.
    """
    _ADAPTERS[adapter.channel_type] = adapter


def get_adapter(channel_type: str) -> ChannelAdapter | None:
    """Return the registered adapter for ``channel_type``, or None.

    None means "no adapter wired yet" — the dispatcher treats that as a
    failed send (the channel type is in the catalogue but its Fase B/C
    adapter has not landed), records it, and dead-letters it rather than
    crashing.
    """
    return _ADAPTERS.get(channel_type)


__all__ = [
    "ChannelAdapter",
    "ChannelMessage",
    "ChannelSendError",
    "DeliveryResult",
    "InAppAdapter",
    "get_adapter",
    "register_adapter",
]
