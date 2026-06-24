"""Preview ingress for the review-runtime (ADR 0062).

Unit-level checks of the signed-URL builder the api-server hands the operator so
they can click through to the running app. The proxy + verdict endpoints are
verified live against the containerized stack (they need a running container).
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import pytest

pytestmark = pytest.mark.integration


def test_build_review_urls_signs_spa_and_app() -> None:
    from api_server.config import get_settings
    from api_server.routers.review import build_review_urls
    from workers.review_runtime import verify_review_url

    sid = "11111111-1111-1111-1111-111111111111"
    exp = time.time() + 3600
    urls = build_review_urls(sid, exp)

    secret = get_settings().review_url_signing_secret.get_secret_value().encode()

    # The SPA URL verifies against the session signature.
    review_q = parse_qs(urlparse(urls["review_url"]).query)
    assert verify_review_url(session_id=sid, expires_at=exp, sig=review_q["sig"][0], secret=secret)

    # The app-preview URL carries the SAME signature (it reuses the session's),
    # and targets the /app/ proxy path under the same session.
    app = urls["app_url"]
    app_q = parse_qs(urlparse(app).query)
    assert app_q["sig"][0] == review_q["sig"][0]
    assert app_q["exp"][0] == review_q["exp"][0]
    assert f"/review/{sid}/app/" in app

    # Both are reachable through the reverse proxy (carry the /api prefix).
    assert "/api/review/" in urls["review_url"]
    assert "/api/review/" in app

    # The verdict URL is signed too and targets the /verdict path.
    verdict = urls["verdict_url"]
    verdict_q = parse_qs(urlparse(verdict).query)
    assert verdict_q["sig"][0] == review_q["sig"][0]
    assert f"/review/{sid}/verdict" in verdict


def test_build_review_urls_distinct_sessions_distinct_sigs() -> None:
    from api_server.routers.review import build_review_urls

    exp = time.time() + 3600
    a = build_review_urls("aaaaaaaa-0000-0000-0000-000000000000", exp)
    b = build_review_urls("bbbbbbbb-0000-0000-0000-000000000000", exp)
    sig_a = parse_qs(urlparse(a["review_url"]).query)["sig"][0]
    sig_b = parse_qs(urlparse(b["review_url"]).query)["sig"][0]
    assert sig_a != sig_b
