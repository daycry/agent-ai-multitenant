"""api-server notification helpers (Plan 10).

The dispatcher service (``apps/notification-dispatcher``) owns the SEND path
(adapters, retries, DLQ). The api-server owns the CONFIG path: the 3-layer
channel/preference CRUD endpoints (``routers/notifications.py``) write
channel rows whose secret must reach the DB encrypted at rest. This package
holds the write-side secret-encryption helper that mirrors the dispatcher's
read-side ``notification_dispatcher.secrets`` so the same Fernet key derives
on both sides.
"""
