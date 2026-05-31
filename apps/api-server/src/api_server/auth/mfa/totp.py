"""TOTP (RFC 6238) primitives + recovery codes (Plan 08 task_08_09).

Pure-Python helpers around :mod:`pyotp`. They never touch the DB or
Redis — they only generate secrets / URIs and verify codes — so they are
fully testable offline (pyotp can generate a valid code for any secret).

Two kinds of credential live here, and BOTH are protected at rest by the
caller (CLAUDE.md: no plaintext secrets in the DB):

  * the **TOTP secret** — a base32 seed. The caller Fernet-encrypts it
    (see :mod:`api_server.auth.mfa.secrets`) before it reaches the DB; the
    clear seed only ever lives in memory here.
  * **recovery codes** — one-time fallback codes shown to the user once at
    enrollment. The caller stores only :func:`hash_recovery_code` of each,
    so the clear codes never reach the DB either.

Verification uses a small ``valid_window`` so a code that is one step
stale (clock skew, the user typing slowly) still passes — the standard
RFC 6238 allowance — without widening the window enough to matter for
brute force (the per-attempt rate limit lives at the endpoint).
"""

from __future__ import annotations

import hashlib
import secrets

import pyotp

# RFC 6238 defaults pyotp uses: 6 digits, 30-second step, SHA-1. Named
# here so the provisioning URI and the verifier agree and there are no
# magic numbers.
TOTP_DIGITS = 6
TOTP_PERIOD_SECONDS = 30
# Accept the adjacent time steps (±1) to tolerate clock skew / slow entry.
# This is the conventional allowance; the endpoint rate-limits attempts so
# the slightly wider window is not a brute-force concern.
TOTP_VALID_WINDOW = 1

# The issuer label shown in the authenticator app next to the account.
TOTP_ISSUER = "Agentic Platform"

# Recovery codes: count generated at enrollment + entropy per code. 10
# codes of 10 hex chars (40 bits) each — plenty against guessing, and the
# at-rest form is the SHA-256 digest (the clear code is shown once).
RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_BYTES = 5  # 5 bytes -> 10 hex chars


def generate_secret() -> str:
    """Return a fresh base32 TOTP seed (clear text).

    Fernet-encrypted by the caller before it reaches the DB; the clear
    value is shown to the user (via the provisioning URI / QR) exactly
    once at enrollment.
    """
    return str(pyotp.random_base32())


def provisioning_uri(secret: str, *, account_name: str, issuer: str = TOTP_ISSUER) -> str:
    """Build the ``otpauth://totp/...`` URI an authenticator app scans.

    The UI renders this as a QR code; the same string can also be typed in
    manually. ``account_name`` is typically the user's email.
    """
    uri = pyotp.TOTP(
        secret,
        digits=TOTP_DIGITS,
        interval=TOTP_PERIOD_SECONDS,
    ).provisioning_uri(name=account_name, issuer_name=issuer)
    return str(uri)


def verify_code(secret: str, code: str) -> bool:
    """Return True iff ``code`` is a currently-valid TOTP for ``secret``.

    Tolerates ±1 time step (:data:`TOTP_VALID_WINDOW`). Non-numeric /
    wrong-length input simply fails (pyotp returns False), never raises.
    """
    cleaned = code.strip().replace(" ", "")
    if not cleaned:
        return False
    totp = pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_PERIOD_SECONDS)
    return bool(totp.verify(cleaned, valid_window=TOTP_VALID_WINDOW))


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Return ``count`` fresh one-time recovery codes (clear text).

    Shown to the user once at enrollment. The caller persists only
    :func:`hash_recovery_code` of each, so the clear codes never reach the
    DB.
    """
    return [secrets.token_hex(RECOVERY_CODE_BYTES) for _ in range(count)]


def hash_recovery_code(code: str) -> str:
    """SHA-256 hex digest of a recovery code (the at-rest form).

    Deterministic so a presented code can be matched by equality against
    the stored digests. Safe as a plain digest because a recovery code is
    high-entropy random (like the SCIM token), so it is not brute-forceable
    and there is no rainbow-table risk.
    """
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


__all__ = [
    "RECOVERY_CODE_BYTES",
    "RECOVERY_CODE_COUNT",
    "TOTP_DIGITS",
    "TOTP_ISSUER",
    "TOTP_PERIOD_SECONDS",
    "TOTP_VALID_WINDOW",
    "generate_recovery_codes",
    "generate_secret",
    "hash_recovery_code",
    "provisioning_uri",
    "verify_code",
]
