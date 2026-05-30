"""Pydantic schemas for SCIM 2.0 user provisioning (Plan 08 task_08_08).

Implements the slice of the SCIM 2.0 core schema (RFC 7643) + protocol
(RFC 7644) the platform needs to let an IdP create / read / update /
deactivate users in a tenant:

  * ``urn:ietf:params:scim:schemas:core:2.0:User`` — the User resource.
  * ``urn:ietf:params:scim:api:messages:2.0:ListResponse`` — GET list.
  * ``urn:ietf:params:scim:api:messages:2.0:PatchOp`` — PATCH.
  * ``urn:ietf:params:scim:api:messages:2.0:Error`` — error envelope.

We map a SCIM User onto the platform's (global) ``users`` row +
per-tenant ``user_org_memberships`` row:

  * ``userName`` / ``emails[primary].value`` -> ``users.email``
  * ``name.formatted`` / ``displayName``     -> ``users.full_name``
  * ``externalId``                            -> echoed back (the IdP's id)
  * ``active``                                -> the membership ``is_active``
    (deprovisioning is per-tenant; the global user row is untouched).

Only the fields the platform actually uses are modelled; unknown SCIM
attributes are accepted and ignored (``extra="ignore"``) so a chatty IdP
does not get a 400 for sending the full resource.

SCIM is camelCase on the wire. Rather than a single ``alias`` (which the
mypy dataclass-transform reads as the *init parameter* name, forcing
camelCase keyword construction in the router), we use direction-specific
aliases:

  * outbound models -> ``serialization_alias`` (``model_dump(by_alias=True)``
    emits camelCase; the router constructs with snake_case names).
  * inbound models  -> ``validation_alias`` (``model_validate`` accepts the
    IdP's camelCase payload; Python attributes stay snake_case).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

# SCIM URNs (RFC 7643 §3 / RFC 7644). Stable strings the IdP matches on.
SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_LIST_RESPONSE_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCIM_PATCH_OP_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCIM_ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"

# The resource type + the meta.location path stem for a User.
SCIM_USER_RESOURCE_TYPE = "User"

# Inbound: accept camelCase (validation_alias) but allow snake_case too,
# and ignore unmapped SCIM attributes.
_IN = ConfigDict(populate_by_name=True, extra="ignore")
# Outbound: serialise camelCase (serialization_alias) on model_dump(by_alias=True).
_OUT = ConfigDict(populate_by_name=True, extra="ignore")


# ---------------------------------------------------------------------------
# Sub-objects (used in both directions; snake_case init, camelCase wire)
# ---------------------------------------------------------------------------
class ScimEmail(BaseModel):
    """A SCIM multi-valued ``emails`` entry."""

    model_config = _OUT

    value: str
    primary: bool = False
    type: str | None = None


class ScimName(BaseModel):
    """A SCIM ``name`` complex attribute (only ``formatted`` is used)."""

    model_config = _OUT

    formatted: str | None = None
    given_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("givenName", "given_name"),
        serialization_alias="givenName",
    )
    family_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("familyName", "family_name"),
        serialization_alias="familyName",
    )


class ScimMeta(BaseModel):
    """SCIM ``meta`` — resource type, timestamps, location, version."""

    model_config = _OUT

    resource_type: str = Field(serialization_alias="resourceType")
    created: datetime
    last_modified: datetime = Field(serialization_alias="lastModified")
    location: str
    version: str | None = None


# ---------------------------------------------------------------------------
# User resource (request body for POST/PUT)
# ---------------------------------------------------------------------------
class ScimUserRequest(BaseModel):
    """Inbound SCIM User (POST create / PUT replace).

    The IdP sends camelCase; we accept it and ignore attributes we do not
    map. ``userName`` is required (it is the SCIM mandatory attribute and
    the platform uses it as the email when ``emails`` is absent).
    """

    model_config = _IN

    schemas: list[str] = Field(default_factory=lambda: [SCIM_USER_SCHEMA])
    user_name: str = Field(
        validation_alias=AliasChoices("userName", "user_name"),
        min_length=1,
        max_length=320,
    )
    external_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("externalId", "external_id"),
        max_length=255,
    )
    display_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("displayName", "display_name"),
        max_length=255,
    )
    name: ScimName | None = None
    emails: list[ScimEmail] = Field(default_factory=list)
    # SCIM `active` defaults to true on create when omitted.
    active: bool = True


# ---------------------------------------------------------------------------
# User resource (response body)
# ---------------------------------------------------------------------------
class ScimUserResponse(BaseModel):
    """Outbound SCIM User — the shape every read/write returns."""

    model_config = _OUT

    schemas: list[str] = Field(default_factory=lambda: [SCIM_USER_SCHEMA])
    id: str
    external_id: str | None = Field(default=None, serialization_alias="externalId")
    user_name: str = Field(serialization_alias="userName")
    display_name: str | None = Field(default=None, serialization_alias="displayName")
    name: ScimName | None = None
    emails: list[ScimEmail] = Field(default_factory=list)
    active: bool
    meta: ScimMeta


class ScimListResponse(BaseModel):
    """SCIM ListResponse envelope for GET /Users (with filter/paging)."""

    model_config = _OUT

    schemas: list[str] = Field(default_factory=lambda: [SCIM_LIST_RESPONSE_SCHEMA])
    total_results: int = Field(serialization_alias="totalResults")
    start_index: int = Field(serialization_alias="startIndex")
    items_per_page: int = Field(serialization_alias="itemsPerPage")
    resources: list[ScimUserResponse] = Field(default_factory=list, serialization_alias="Resources")


class ScimPatchOperation(BaseModel):
    """A single SCIM PATCH operation (RFC 7644 §3.5.2)."""

    model_config = _IN

    op: str
    path: str | None = None
    value: Any = None


class ScimPatchRequest(BaseModel):
    """SCIM PatchOp request body for PATCH /Users/{id}."""

    model_config = _IN

    schemas: list[str] = Field(default_factory=lambda: [SCIM_PATCH_OP_SCHEMA])
    operations: list[ScimPatchOperation] = Field(
        validation_alias=AliasChoices("Operations", "operations")
    )


class ScimError(BaseModel):
    """SCIM error envelope (RFC 7644 §3.12)."""

    model_config = _OUT

    schemas: list[str] = Field(default_factory=lambda: [SCIM_ERROR_SCHEMA])
    detail: str
    status: str
    scim_type: str | None = Field(default=None, serialization_alias="scimType")


# ---------------------------------------------------------------------------
# Token management (the Tenant-Admin UI mints/lists/revokes SCIM tokens)
# ---------------------------------------------------------------------------
class ScimTokenCreateRequest(BaseModel):
    """Body for minting a new SCIM token (tenant_admin)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    description: str | None = Field(default=None, max_length=255)


class ScimTokenResponse(BaseModel):
    """A SCIM token's metadata — NEVER the token value."""

    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    token_prefix: str
    description: str | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class ScimTokenCreatedResponse(ScimTokenResponse):
    """The mint response — carries the clear token EXACTLY ONCE.

    The clear ``token`` is returned only here, at creation time, and is
    never retrievable again (only its SHA-256 digest is stored).
    """

    token: str


__all__ = [
    "SCIM_ERROR_SCHEMA",
    "SCIM_LIST_RESPONSE_SCHEMA",
    "SCIM_PATCH_OP_SCHEMA",
    "SCIM_USER_RESOURCE_TYPE",
    "SCIM_USER_SCHEMA",
    "ScimEmail",
    "ScimError",
    "ScimListResponse",
    "ScimMeta",
    "ScimName",
    "ScimPatchOperation",
    "ScimPatchRequest",
    "ScimTokenCreateRequest",
    "ScimTokenCreatedResponse",
    "ScimTokenResponse",
    "ScimUserRequest",
    "ScimUserResponse",
]
