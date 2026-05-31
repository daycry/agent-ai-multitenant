"""SCIM 2.0 user provisioning (Plan 08 task_08_08).

Added ALONGSIDE the existing auth: SCIM is a machine-to-machine
provisioning channel an IdP uses to push users into a tenant. It does
NOT replace or touch local login, OIDC, or SAML — they all keep issuing
the same Redis session + JWT. SCIM authenticates with a per-tenant
bearer token (see :mod:`api_server.auth.scim.tokens`), never a JWT.
"""

from __future__ import annotations

from api_server.auth.scim.tokens import (
    SCIM_TOKEN_BYTES,
    SCIM_TOKEN_PREFIX_LEN,
    generate_scim_token,
    hash_scim_token,
)

__all__ = [
    "SCIM_TOKEN_BYTES",
    "SCIM_TOKEN_PREFIX_LEN",
    "generate_scim_token",
    "hash_scim_token",
]
