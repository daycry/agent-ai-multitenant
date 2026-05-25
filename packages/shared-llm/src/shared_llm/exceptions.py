"""Typed errors callers can catch (ADR 0021).

Three-layer hierarchy so the host can react with the right granularity:

  LLMError                    — anything from the LLM layer
   ├── AuthError              — bad / expired / revoked credentials
   ├── RateLimitError         — the provider asked us to slow down
   └── ProviderError          — anything else the provider returned
                                (carries status + raw payload)
"""

from __future__ import annotations


class LLMError(Exception):
    """Base class for every error this layer raises."""


class AuthError(LLMError):
    """Authentication failure — token missing, invalid, expired, revoked."""


class RateLimitError(LLMError):
    """The provider rate-limited the call (HTTP 429 or equivalent)."""


class ProviderError(LLMError):
    """Generic provider error — wraps the upstream status + payload."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        raw: object = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.raw = raw


__all__ = ["AuthError", "LLMError", "ProviderError", "RateLimitError"]
