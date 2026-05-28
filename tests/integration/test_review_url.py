"""Integration tests: signed review URL (Plan 06 task_06_27)."""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import pytest

pytestmark = pytest.mark.integration


SECRET = b"super-secret-not-in-prod"


def test_sign_and_verify_round_trip() -> None:
    from workers.review_runtime import sign_review_url, verify_review_url

    exp = time.time() + 3600
    url = sign_review_url(
        base_url="https://platform.example",
        session_id="abc123",
        expires_at=exp,
        secret=SECRET,
    )
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    sig = qs["sig"][0]
    assert verify_review_url(session_id="abc123", expires_at=exp, sig=sig, secret=SECRET)


def test_url_contains_session_id_and_exp() -> None:
    from workers.review_runtime import sign_review_url

    exp = 1_234_567_890
    url = sign_review_url(
        base_url="https://x.test/",
        session_id="sess-9",
        expires_at=exp,
        secret=SECRET,
    )
    parsed = urlparse(url)
    assert parsed.path == "/review/sess-9"
    qs = parse_qs(parsed.query)
    assert qs["exp"][0] == str(exp)
    assert "sig" in qs


def test_expired_url_rejected() -> None:
    from workers.review_runtime import verify_review_url

    exp = time.time() - 60  # 1 minute ago
    assert not verify_review_url(
        session_id="abc",
        expires_at=exp,
        sig="anything",
        secret=SECRET,
    )


def test_tampered_signature_rejected() -> None:
    from workers.review_runtime import sign_review_url, verify_review_url

    exp = time.time() + 3600
    url = sign_review_url(base_url="https://x", session_id="abc", expires_at=exp, secret=SECRET)
    sig = parse_qs(urlparse(url).query)["sig"][0]
    assert not verify_review_url(
        session_id="abc",
        expires_at=exp,
        sig=sig + "X",  # flipped
        secret=SECRET,
    )


def test_different_secret_rejected() -> None:
    from workers.review_runtime import sign_review_url, verify_review_url

    exp = time.time() + 3600
    url = sign_review_url(base_url="https://x", session_id="abc", expires_at=exp, secret=SECRET)
    sig = parse_qs(urlparse(url).query)["sig"][0]
    assert not verify_review_url(
        session_id="abc",
        expires_at=exp,
        sig=sig,
        secret=b"different-secret",
    )
