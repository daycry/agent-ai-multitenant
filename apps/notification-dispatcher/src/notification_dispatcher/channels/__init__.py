"""Channel adapters for the notification-dispatcher (Plan 10 Fase B/C).

One cohesive module per transport (Telegram, Email, Slack, Teams,
Discord, WhatsApp, SMS, outbound webhooks). Each module implements the
:class:`~notification_dispatcher.adapters.ChannelAdapter` Protocol defined
in Fase A and registers itself with the shared registry
(:func:`~notification_dispatcher.adapters.register_adapter`) keyed by its
:class:`~api_server.db.notification.NotificationChannelType` value, so the
dispatcher routes a ``channel_type`` straight to the right adapter.

Importing this package imports every channel module for its registration
side effect, so a single ``import notification_dispatcher.channels`` wires
up all adapters. The Celery app / tasks layer triggers this import at
startup; tests import the specific channel module they exercise.
"""

from __future__ import annotations

from notification_dispatcher.channels import discord, email, slack, sms, teams, telegram, whatsapp

__all__ = ["discord", "email", "slack", "sms", "teams", "telegram", "whatsapp"]
