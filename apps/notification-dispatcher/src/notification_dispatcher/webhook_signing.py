"""Reusable HMAC-SHA256 webhook signing + verification (Plan 10 task_10_12).

The outbound-webhook channel (``notification_dispatcher.channels.webhook``)
signs every POST so a receiver can authenticate it AND reject replays. This
module is the cohesive, transport-agnostic crypto core, deliberately split out
from the adapter so it is reusable by:

  * the outbound adapter (signs the request it POSTs), and
  * the inbound-webhook verifier of **Plan 13** (the docstring of the plan
    scopes inbound to that later phase) — the :func:`verify_webhook` helper
    here is the exact check a receiver runs, exercised by this task's test.

The scheme (Plan 10 Decisiones Clave: *HMAC SHA-256 + nonce + timestamp
anti-replay*):

  * **Signature** — ``HMAC-SHA256(secret, timestamp + "." + nonce + "." + body)``,
    hex-encoded. Binding the timestamp and nonce INTO the signed material (not
    just sending them alongside) is what makes them tamper-evident: a receiver
    that re-derives the MAC over the same three parts detects any change to the
    body, the timestamp, or the nonce.
  * **Timestamp** (``X-Timestamp``, Unix seconds) — bounds *freshness*. The
    receiver rejects a signature whose timestamp is outside a small skew window
    (:func:`verify_webhook`'s ``max_skew_s``), so a captured-but-old request
    can't be replayed indefinitely.
  * **Nonce** (``X-Nonce``, a random 128-bit token) — bounds *single use*
    within the freshness window. The receiver remembers nonces it has already
    accepted (a ``seen_nonce`` callback here; a TTL store keyed to the skew
    window in a real receiver) and rejects a repeat, so a request captured
    inside the freshness window still can't be replayed.

Constant-time comparison (:func:`hmac.compare_digest`) guards the signature
check against timing attacks. The signing secret is the channel secret resolved
IN MEMORY by :func:`notification_dispatcher.secrets.resolve_channel_secret`
(Vault ``secret_ref`` / Fernet ``secret_encrypted`` — never plaintext in the DB,
never logged).
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256

# Header names the outbound adapter stamps and an inbound verifier reads. Kept
# here (not inline in the adapter) so the inbound verifier of Plan 13 reuses the
# identical contract. The ``X-`` prefix matches the de-facto webhook convention
# (GitHub / Stripe / Slack all sign with an ``X-*-Signature`` family of headers).
SIGNATURE_HEADER = "X-Signature"
TIMESTAMP_HEADER = "X-Timestamp"
NONCE_HEADER = "X-Nonce"

# Nonce entropy in bytes (128-bit). secrets.token_hex(16) -> a 32-char hex token;
# collision probability is negligible within any sane freshness window.
_NONCE_BYTES = 16


def generate_nonce() -> str:
    """Return a fresh, single-use, cryptographically-random nonce (hex)."""
    return secrets.token_hex(_NONCE_BYTES)


def current_timestamp() -> int:
    """Return the current Unix time in whole seconds (the signed timestamp)."""
    return int(time.time())


def _signing_material(*, timestamp: int, nonce: str, body: bytes) -> bytes:
    """Canonical bytes the MAC is computed over: ``ts.nonce.<body>``.

    The timestamp + nonce are folded INTO the signed material (ASCII, '.'
    separated, then the raw body bytes) so tampering with any of the three is
    detectable. Using the raw request body bytes (not a re-serialised copy)
    keeps the signer and verifier byte-for-byte identical regardless of JSON
    key ordering / whitespace.
    """
    prefix = f"{timestamp}.{nonce}.".encode("ascii")
    return prefix + body


def sign_webhook(secret: str, body: bytes, timestamp: int, nonce: str) -> str:
    """Compute the hex HMAC-SHA256 signature for one webhook request.

    ``secret`` is the channel signing secret (resolved in memory; never
    logged). ``body`` is the exact request body bytes that will be sent.
    ``timestamp`` (Unix seconds) and ``nonce`` are also sent in headers so the
    receiver can re-derive this signature and enforce freshness + single-use.
    """
    material = _signing_material(timestamp=timestamp, nonce=nonce, body=body)
    return hmac.new(secret.encode("utf-8"), material, sha256).hexdigest()


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of :func:`verify_webhook` — ``ok`` plus a machine-readable reason.

    ``reason`` is one of ``"ok"`` / ``"bad_signature"`` / ``"stale_timestamp"``
    / ``"replayed_nonce"`` / ``"malformed"`` so a caller (the test here, an
    inbound verifier in Plan 13) can branch / log without string-matching.
    """

    ok: bool
    reason: str


def verify_webhook(
    secret: str,
    body: bytes,
    *,
    signature: str,
    timestamp: str | int,
    nonce: str,
    max_skew_s: int,
    now: int | None = None,
    seen_nonce: Callable[[str], bool] | None = None,
) -> VerificationResult:
    """Verify a signed webhook request, enforcing freshness + single-use.

    The receiver-side check (reused by Plan 13 inbound). Checks, in order:

      1. **Well-formedness** — ``timestamp`` parses as an int and the signature
         / nonce are present.
      2. **Freshness** — ``|now - timestamp| <= max_skew_s`` (rejects a stale or
         far-future timestamp → ``stale_timestamp``).
      3. **Single use** — if ``seen_nonce`` is supplied and reports the nonce as
         already accepted, reject as ``replayed_nonce``. The caller owns the
         nonce store (a TTL set keyed to the skew window in production); this
         helper stays storage-agnostic.
      4. **Signature** — recompute the HMAC over ``ts.nonce.body`` and compare
         in constant time (:func:`hmac.compare_digest`). Any tamper to the
         body / timestamp / nonce, or a wrong secret, fails here →
         ``bad_signature``.

    Order matters: freshness + replay are cheap and rejected first; the
    constant-time MAC comparison is the authentication gate. A real receiver
    records the nonce as seen ONLY after a fully successful verification (so a
    bad-signature attempt can't burn a victim's nonce) — that record step is the
    caller's responsibility, kept out of this pure function.
    """
    if not signature or not nonce:
        return VerificationResult(ok=False, reason="malformed")
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return VerificationResult(ok=False, reason="malformed")

    moment = current_timestamp() if now is None else now
    if abs(moment - ts) > max_skew_s:
        return VerificationResult(ok=False, reason="stale_timestamp")

    if seen_nonce is not None and seen_nonce(nonce):
        return VerificationResult(ok=False, reason="replayed_nonce")

    expected = sign_webhook(secret, body, ts, nonce)
    if not hmac.compare_digest(expected, signature):
        return VerificationResult(ok=False, reason="bad_signature")

    return VerificationResult(ok=True, reason="ok")


__all__ = [
    "NONCE_HEADER",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "VerificationResult",
    "current_timestamp",
    "generate_nonce",
    "sign_webhook",
    "verify_webhook",
]
