"""Baseline security response headers for the api-server (prod-09, api-7).

The API had no response-hardening headers at all. Three of them cost nothing and
close real classes of attack against the surfaces the api-server serves directly
(the signed review SPA, error bodies, JSON that a browser might sniff):

  * ``X-Content-Type-Options: nosniff`` — stop content-type sniffing, so a JSON
    body a browser might guess to be HTML/JS is never executed as such.
  * ``X-Frame-Options`` — deny framing, so no third-party page can embed an
    api-server response for a clickjacking overlay.
  * ``Referrer-Policy: no-referrer`` — the api-server URLs carry SIGNED query
    strings (``/review/{id}?exp=&sig=``, and today's ``/ws/...?token=``); a
    default ``Referer`` leaks those credentials to every third-party host the
    page links to or loads a resource from. This one is the least decorative
    header of the three.
  * ``Strict-Transport-Security`` — only over TLS and only outside dev (see
    :func:`_hsts_applies`). Sending it over plain HTTP is meaningless, and
    sending it in dev would pin ``localhost`` to HTTPS in the developer's
    browser for a year — an unpleasant, hard-to-diagnose foot-gun.

FRAMING EXCEPTION. ``X-Frame-Options: DENY`` blocks even SAME-ORIGIN framing, and
the review SPA (``/review/{id}``) is designed to embed the live app preview it
proxies at ``/review/{id}/app/`` in an iframe (ADR 0129/0130; see the
``app_configured`` note in ``routers/review.py``, which exists precisely so the
SPA can avoid "un iframe roto"). A blanket DENY would therefore ship a header
that breaks the preview the moment the SPA bundle lands — the kind of change that
looks free and is not. Paths under ``/review`` get ``SAMEORIGIN`` instead: still
no cross-origin framing, but the same-origin preview keeps working.

Headers already set by a handler are NEVER overwritten: a route that deliberately
sets its own policy (a future CSP for the review SPA) stays in control.
"""

from __future__ import annotations

from typing import cast

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# One year, the usual HSTS floor for preload-eligible policies.
_HSTS_VALUE = "max-age=31536000; includeSubDomains"

# Path roots whose responses are legitimately framed by a SAME-ORIGIN page.
_SAMEORIGIN_ROOTS = ("review",)


def _frame_options(path: str) -> str:
    """``SAMEORIGIN`` for the review surface, ``DENY`` everywhere else.

    Matched on the first path SEGMENT, not as a string prefix: a naive
    ``startswith("/review")`` also relaxes ``/reviewer-ish`` — any future route
    whose name merely begins with those letters would silently become framable.
    An over-broad match here weakens a security header, so it is worth the split.
    """
    first_segment = path.lstrip("/").split("/", 1)[0]
    return "SAMEORIGIN" if first_segment in _SAMEORIGIN_ROOTS else "DENY"


def _hsts_applies(request: Request, *, environment: str) -> bool:
    """True iff HSTS should be emitted for this request.

    Two conditions, both required:

      * NOT ``dev`` — a stray HSTS header on ``localhost`` makes every developer's
        browser force HTTPS on every localhost port for a year;
      * the request actually arrived over TLS, either directly or through the
        reverse proxy (``X-Forwarded-Proto: https``). ``X-Forwarded-Proto`` is
        only meaningful because prod-01 terminates TLS in front of the api-server
        and the header cannot reach us except through it; and the failure mode of
        trusting a spoofed value here is emitting a header the browser ignores
        on a plain-HTTP response, not a bypass.
    """
    if environment == "dev":
        return False
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


class SecurityHeadersMiddleware:
    """Pure-ASGI middleware adding the baseline headers to every response.

    Written against the ASGI interface rather than ``BaseHTTPMiddleware`` so it
    also covers streaming responses (SSE) and does not wrap the body in an extra
    task — and so it can simply pass ``websocket`` scopes through untouched
    (response headers are meaningless for the WS handshake).
    """

    def __init__(self, app: ASGIApp, *, environment: str) -> None:
        self.app = app
        self.environment = environment

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        extra = self.headers_for(request)

        async def send_with_headers(message: Message) -> None:
            if message.get("type") == "http.response.start":
                raw = list(cast("list[tuple[bytes, bytes]]", message.get("headers") or []))
                present = {name.lower() for name, _ in raw}
                for name_str, value in extra.items():
                    encoded = name_str.lower().encode()
                    if encoded not in present:
                        raw.append((encoded, value.encode()))
                message = {**message, "headers": raw}
            await send(message)

        await self.app(scope, receive, send_with_headers)

    def headers_for(self, request: Request) -> dict[str, str]:
        """The headers this middleware would add to ``request``'s response.

        Exposed (and pure) so the invariants can be asserted directly, without
        standing up an app — including the two conditional ones, which are the
        easy things to get wrong.
        """
        headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": _frame_options(request.url.path),
            "Referrer-Policy": "no-referrer",
        }
        if _hsts_applies(request, environment=self.environment):
            headers["Strict-Transport-Security"] = _HSTS_VALUE
        return headers


__all__ = ["SecurityHeadersMiddleware"]
