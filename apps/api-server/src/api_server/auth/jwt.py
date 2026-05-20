"""JWT encode/decode wrappers.

Claims used by the platform:

  sub  (subject)    user id (UUID v7, string form)
  tid  (tenant id)  optional — the active tenant of the session; absent
                    if the user is still picking one
  iat  (issued at)  unix timestamp
  exp  (expires)    unix timestamp

Signed HS256 with the shared secret from settings.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from jose import JWTError, jwt

from api_server.config import get_settings


class InvalidTokenError(Exception):
    """Raised when a JWT fails to decode (bad signature, expired, malformed)."""


def encode_jwt(
    user_id: UUID,
    tenant_id: UUID | None = None,
    *,
    expires_in: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Sign a token. `expires_in` overrides the default settings TTL."""
    settings = get_settings()
    now = datetime.now(tz=UTC)
    ttl = expires_in or timedelta(minutes=settings.jwt_expiration_minutes)
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    if tenant_id is not None:
        claims["tid"] = str(tenant_id)
    if extra_claims:
        claims.update(extra_claims)
    encoded: str = jwt.encode(
        claims,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return encoded


def decode_jwt(token: str) -> dict[str, Any]:
    """Return the decoded claims or raise InvalidTokenError."""
    settings = get_settings()
    try:
        decoded: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
        )
        return decoded
    except JWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
