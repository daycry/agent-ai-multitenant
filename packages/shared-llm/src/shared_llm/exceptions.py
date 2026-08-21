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
    """The provider rate-limited the call (HTTP 429 or equivalent).

    ``retry_after`` carries the provider's own back-off hint in SECONDS,
    parsed from the ``Retry-After`` response header when it was present
    (`prod-07 task_prod07_01`). ``None`` means the provider did not say, and
    the caller falls back to exponential backoff. Guessing when the provider
    told us is how a retry storm turns one 429 into five.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ProviderError(LLMError):
    """Generic provider error — wraps the upstream status + payload."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        raw: object = None,
        transient: bool | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.raw = raw
        # ``transient`` overrides the status-code heuristic of
        # ``shared_llm.retry.is_transient``:
        #
        #   * ``True``  — the raiser KNOWS this is worth retrying even without a
        #     status code. The transport wrapper uses it: a reset socket or a
        #     read timeout has no HTTP status, and treating it as permanent is
        #     exactly the bug llm-2 described ("un blip de red mata el run").
        #   * ``False`` — the raiser KNOWS retrying is pointless despite a 5xx.
        #   * ``None``  — no opinion; classify by ``status_code``.
        self.transient = transient
        # Same meaning as on RateLimitError: a 503 may also carry Retry-After.
        self.retry_after = retry_after


__all__ = ["AuthError", "LLMError", "ProviderError", "RateLimitError"]
