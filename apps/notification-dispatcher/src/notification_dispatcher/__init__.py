"""Notification dispatcher service — Plan 10.

task_10_02 ships the Celery app + the dedicated notification queues, the
``send_notification`` task that looks up a channel + log, validates
tenant ownership at the task boundary (the BYPASSRLS worker pattern from
Plan 06.14 task_06_14_02), dispatches to a :class:`ChannelAdapter`, and
dead-letters a failed send onto a Redis stream — never a blind
auto-retry. The real channel adapters (Telegram, Email, Slack, …) land in
Plan 10 Fase B/C; this task defines the adapter Protocol + an ``in_app``
no-op default so the dispatch path is testable end to end.
"""
