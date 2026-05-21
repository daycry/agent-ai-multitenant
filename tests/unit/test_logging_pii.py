"""PII masking — unit tests for `api_server.logging.pii`.

Each pattern (email / IBAN / DNI / NIE / JWT / Bearer) is exercised
in isolation plus a "nested structures" check confirming the
structlog processor walks dicts and lists.
"""

from __future__ import annotations

import pytest
from api_server.logging.pii import (
    mask_pii_in_text,
    mask_pii_processor,
)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
def test_email_keeps_first_char_and_domain() -> None:
    out = mask_pii_in_text("login attempt: alice@example.com")
    assert out == "login attempt: a***@example.com"


def test_email_multiple_in_one_string() -> None:
    out = mask_pii_in_text("from=bob@a.test, to=carol@b.test")
    assert out == "from=b***@a.test, to=c***@b.test"


def test_non_email_at_sign_is_left_alone() -> None:
    # Twitter-style handle: no domain after @, must not match.
    assert mask_pii_in_text("see @alice for help") == "see @alice for help"


# ---------------------------------------------------------------------------
# IBAN
# ---------------------------------------------------------------------------
def test_iban_with_spaces() -> None:
    raw = "wire to ES91 2100 0418 4502 0005 1332"
    out = mask_pii_in_text(raw)
    assert out.startswith("wire to ES**")
    assert "1332" in out  # last 4 preserved
    assert "0005" not in out.replace("1332", "")  # interior groups don't leak verbatim


def test_iban_without_spaces() -> None:
    out = mask_pii_in_text("IBAN=ES9121000418450200051332")
    assert "21000418" not in out
    assert "1332" in out


# ---------------------------------------------------------------------------
# DNI / NIE
# ---------------------------------------------------------------------------
def test_dni_masked_keeps_last_two() -> None:
    out = mask_pii_in_text("DNI 12345678Z")
    assert out == "DNI ******8Z"
    assert "12345" not in out


def test_nie_masked_keeps_last_two() -> None:
    out = mask_pii_in_text("NIE X1234567L")
    assert out.endswith("******7L")
    assert "12345" not in out


# ---------------------------------------------------------------------------
# JWT / Bearer
# ---------------------------------------------------------------------------
def test_jwt_is_fully_redacted() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9." "eyJzdWIiOiIxMjMiLCJleHAiOjE3MDB9." "abcdefABCDEF1234567890"
    out = mask_pii_in_text(f"token={jwt}")
    assert "eyJhbGci" not in out
    assert "***REDACTED***" in out


def test_bearer_header_is_redacted() -> None:
    out = mask_pii_in_text("Authorization: Bearer ey.something.lookslikeJWT")
    assert "Bearer ***REDACTED***" in out
    assert "ey.something" not in out


def test_bearer_case_insensitive() -> None:
    assert "***REDACTED***" in mask_pii_in_text("bearer abc.def.ghi")
    assert "***REDACTED***" in mask_pii_in_text("BEARER xyz.abc.123")


# ---------------------------------------------------------------------------
# Plain text passes through
# ---------------------------------------------------------------------------
def test_no_pii_string_returns_unchanged() -> None:
    msg = "user logged in from 10.0.0.1 at 2026-05-20T12:00:00Z"
    assert mask_pii_in_text(msg) == msg


def test_empty_string_returns_empty() -> None:
    assert mask_pii_in_text("") == ""


# ---------------------------------------------------------------------------
# structlog processor — recurses into dicts and lists
# ---------------------------------------------------------------------------
def test_processor_masks_top_level_strings() -> None:
    event = {"event": "login attempt", "email": "alice@example.com"}
    out = mask_pii_processor(None, "info", event)
    assert out["email"] == "a***@example.com"


def test_processor_walks_nested_dict() -> None:
    event = {
        "event": "register",
        "payload": {"email": "bob@a.test", "full_name": "Bob"},
    }
    out = mask_pii_processor(None, "info", event)
    assert out["payload"]["email"] == "b***@a.test"
    assert out["payload"]["full_name"] == "Bob"


def test_processor_walks_list_of_dicts() -> None:
    event = {
        "event": "batch",
        "rows": [
            {"email": "x@y.test"},
            {"email": "z@y.test"},
        ],
    }
    out = mask_pii_processor(None, "info", event)
    assert out["rows"][0]["email"] == "x***@y.test"
    assert out["rows"][1]["email"] == "z***@y.test"


def test_processor_preserves_non_string_types() -> None:
    event = {"event": "x", "count": 42, "ratio": 0.5, "flag": True, "nothing": None}
    out = mask_pii_processor(None, "info", event)
    assert out["count"] == 42
    assert out["ratio"] == 0.5
    assert out["flag"] is True
    assert out["nothing"] is None


# ---------------------------------------------------------------------------
# Mixed sample — sanity check
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,must_not_contain",
    [
        ("Login by alice@example.com OK", "alice"),
        ("DNI 99887766K migrated", "99887766"),
        (
            "IBAN ES91 2100 0418 4502 0005 1332 sent",
            "21000418",
        ),
        (
            "Authorization: Bearer eyJabc.eyJdef.signaturepart",
            "eyJabc",
        ),
    ],
)
def test_mixed_real_samples(raw: str, must_not_contain: str) -> None:
    assert must_not_contain not in mask_pii_in_text(raw)
