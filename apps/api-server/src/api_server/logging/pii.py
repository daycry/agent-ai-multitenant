"""PII (and secrets) redaction for log records.

The masker is conservative — patterns are tuned to catch the common
shapes our logs leak rather than be exhaustive:

  - email           keep first char + domain          a***@example.com
  - IBAN            keep country + last 4             ES** **** **** **** 1332
  - DNI / NIE       keep last 2                       ******12X
  - Bearer token    drop everything after "Bearer "   Bearer ***REDACTED***
  - JWT             drop the three b64url segments    ***REDACTED***

Anything not matching is left intact. We mask strings recursively
inside dicts and lists so structlog event_dicts that wrap nested
payloads (request bodies, error details, ...) get cleaned too.
"""

from __future__ import annotations

import re
from typing import Any

from structlog.types import EventDict, WrappedLogger

# ---------------------------------------------------------------------------
# Regex catalogue
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(
    r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})\b"
)

# IBAN: 2 letters + 2 check digits + 11-30 alphanumerics (optionally
# split in groups of 4 by spaces).
_IBAN_RE = re.compile(r"\b([A-Z]{2})\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}[ ]?[A-Z0-9]{1,4}\b")

# Spanish DNI (8 digits + letter) and NIE (X/Y/Z + 7 digits + letter).
_DNI_RE = re.compile(r"\b\d{8}[A-Za-z]\b")
_NIE_RE = re.compile(r"\b[XYZxyz]\d{7}[A-Za-z]\b")

# JWT — three base64url segments separated by dots.
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_=-]{4,}\.eyJ[A-Za-z0-9_=-]{4,}\.[A-Za-z0-9_=.+/-]{4,}\b")

# Bearer header value (the whole `Bearer xxx...` chunk).
_BEARER_RE = re.compile(r"(?i)Bearer\s+\S+")

_REDACTED = "***REDACTED***"


# ---------------------------------------------------------------------------
# Per-pattern replacement functions
# ---------------------------------------------------------------------------
def _mask_email(match: re.Match[str]) -> str:
    first, domain = match.group(1), match.group(2)
    return f"{first}***@{domain}"


def _mask_iban(match: re.Match[str]) -> str:
    country = match.group(1)
    raw = match.group(0).replace(" ", "")
    tail = raw[-4:]
    return f"{country}** **** **** **** {tail}"


def _mask_dni(match: re.Match[str]) -> str:
    raw = match.group(0)
    return f"******{raw[-2:]}"


def _mask_jwt(_match: re.Match[str]) -> str:
    return _REDACTED


def _mask_bearer(_match: re.Match[str]) -> str:
    return f"Bearer {_REDACTED}"


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------
def mask_pii_in_text(text: str) -> str:
    """Return `text` with every known PII pattern replaced."""
    if not text:
        return text
    # Order matters: JWT and Bearer go first because they may contain
    # substrings that look like other patterns. IBAN before DNI to keep
    # alphanumeric runs from being eaten piecewise.
    text = _JWT_RE.sub(_mask_jwt, text)
    text = _BEARER_RE.sub(_mask_bearer, text)
    text = _IBAN_RE.sub(_mask_iban, text)
    text = _NIE_RE.sub(_mask_dni, text)
    text = _DNI_RE.sub(_mask_dni, text)
    text = _EMAIL_RE.sub(_mask_email, text)
    return text


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_pii_in_text(value)
    if isinstance(value, dict):
        return {k: _mask_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_mask_value(item) for item in value)
    return value


def mask_pii_processor(
    _logger: WrappedLogger, _method_name: str, event_dict: EventDict
) -> EventDict:
    """structlog processor: recursively mask every string value."""
    return {key: _mask_value(value) for key, value in event_dict.items()}
