"""OpenAPI 3.1 document + Swagger UI for the public v1 API (task_13_06).

The public ``/api/v1`` surface is a published contract: a tenant's
external tooling reads this spec to know the paths, schemas and — crucially
— HOW to authenticate. So we publish a SELF-CONTAINED OpenAPI 3.1 document
scoped to JUST the v1 routes (not the whole internal app surface) at
``/api/v1/openapi.json`` and a Swagger UI for it at ``/api/v1/docs``.

Two things matter beyond "FastAPI generates OpenAPI for free":

  * **Version 3.1.x.** FastAPI 0.99+ emits OpenAPI 3.1 by default (it is
    what :data:`FastAPI.openapi_version` resolves to), and we pin it
    explicitly here so a future default change can never silently
    downgrade the published contract.
  * **The ``X-API-Token`` security scheme.** The v1 endpoints authenticate
    through the Fase A header dependency, which is invisible to FastAPI's
    automatic schema generation (it reads a bare ``Header(...)``, not a
    declared :mod:`fastapi.security` scheme). Without help the spec would
    show the paths but not tell a reader they need a token. We therefore
    DECLARE an ``apiKey`` security scheme (in the ``X-API-Token`` header)
    and apply it as the document's global security requirement, so every
    v1 operation shows the lock + the "Authorize" affordance in Swagger UI.

The document is built ONCE and cached on first request (the v1 route set is
fixed at import time), mirroring FastAPI's own ``app.openapi()`` memoisation.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.routing import BaseRoute

from api_server.routers.api_v1.router import api_v1_router

# Public, stable paths the published contract lives at (versioned in the
# PATH per Plan 13 Decisiones Clave).
OPENAPI_JSON_PATH = "/api/v1/openapi.json"
SWAGGER_UI_PATH = "/api/v1/docs"

# Name + location of the auth credential, matching the Fase A
# ``X-API-Token`` HEADER dependency exactly.
API_TOKEN_HEADER = "X-API-Token"
_SECURITY_SCHEME_NAME = "ApiTokenAuth"

_TITLE = "Agentic Platform — Public API"
_VERSION = "v1"
# Pin 3.1.x explicitly: do not inherit a (mutable) framework default for a
# published contract.
_OPENAPI_VERSION = "3.1.0"

_DESCRIPTION = (
    "Public, versioned REST API. Authenticate with a per-tenant API token "
    "in the `X-API-Token` request header (never a query parameter). The "
    "token scopes every request to its own tenant; a `read`-scope token may "
    "only GET, a `write`-scope token may also create."
)

# Built lazily on first request and cached — the v1 route set is fixed at
# import time, so a single build is correct for the process lifetime. A
# one-slot list holds the cache so the memoising helper mutates a container
# rather than rebinding a module global (avoids the `global` statement).
_cache: list[dict[str, Any]] = []


def _v1_routes() -> list[BaseRoute]:
    """The public v1 routes the document is generated from.

    Only the ``/api/v1/...`` endpoints are included — the published spec
    must describe the public contract, not the internal app surface.
    """
    return list(api_v1_router.routes)


def build_v1_openapi() -> dict[str, Any]:
    """Build the OpenAPI 3.1 document for the public v1 surface.

    FastAPI's :func:`get_openapi` produces the paths + component schemas
    from the v1 routes. We then inject the ``apiKey`` security scheme and a
    global security requirement so the document advertises HOW to
    authenticate (the Fase A header dependency is opaque to automatic
    generation).
    """
    schema = get_openapi(
        title=_TITLE,
        version=_VERSION,
        openapi_version=_OPENAPI_VERSION,
        description=_DESCRIPTION,
        routes=_v1_routes(),
    )
    # Declare the X-API-Token apiKey header scheme so Swagger UI shows an
    # "Authorize" button and every operation carries the lock.
    components = schema.setdefault("components", {})
    security_schemes = components.setdefault("securitySchemes", {})
    security_schemes[_SECURITY_SCHEME_NAME] = {
        "type": "apiKey",
        "in": "header",
        "name": API_TOKEN_HEADER,
        "description": (
            "Per-tenant API token. Minted by a Tenant Admin and presented "
            "verbatim in the `X-API-Token` header on every request."
        ),
    }
    # Apply it globally so it is the default requirement for every operation
    # (FastAPI does not emit a per-operation `security` for the Fase A
    # dependency, so the document-level requirement is what conveys it).
    schema["security"] = [{_SECURITY_SCHEME_NAME: []}]
    return schema


def get_v1_openapi() -> dict[str, Any]:
    """Return the cached v1 OpenAPI document, building it on first call."""
    if not _cache:
        _cache.append(build_v1_openapi())
    return _cache[0]


# ---------------------------------------------------------------------------
# Router: the published JSON + a Swagger UI bound to it
# ---------------------------------------------------------------------------
# These are PUBLIC docs endpoints (no auth): a developer reads the contract
# before they have wired up a token. They expose only the schema shape, not
# any tenant data.
api_v1_docs_router = APIRouter(tags=["public-api-v1"])


@api_v1_docs_router.get(OPENAPI_JSON_PATH, include_in_schema=False)
async def v1_openapi_json() -> JSONResponse:
    """Serve the public v1 OpenAPI 3.1 document."""
    return JSONResponse(get_v1_openapi())


@api_v1_docs_router.get(SWAGGER_UI_PATH, include_in_schema=False)
async def v1_swagger_ui() -> HTMLResponse:
    """Serve Swagger UI bound to the public v1 OpenAPI document."""
    return get_swagger_ui_html(
        openapi_url=OPENAPI_JSON_PATH,
        title=f"{_TITLE} — Swagger UI",
    )


__all__ = [
    "API_TOKEN_HEADER",
    "OPENAPI_JSON_PATH",
    "SWAGGER_UI_PATH",
    "api_v1_docs_router",
    "build_v1_openapi",
    "get_v1_openapi",
]
