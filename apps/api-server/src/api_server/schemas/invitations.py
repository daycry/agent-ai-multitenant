"""Schemas de las invitaciones de alta (ADR 0134, opción C).

Tres formas y una regla que las separa: **el token en claro sale una sola vez**,
en :class:`IssuedInvitationResponse`, y jamás vuelve a aparecer. El listado
(:class:`InvitationResponse`) enseña únicamente el prefijo, igual que hacen los
listados de ``api_tokens`` y ``scim_tokens``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

from api_server.auth.invitations import (
    DEFAULT_INVITATION_TTL_HOURS,
    MAX_INVITATION_TTL_HOURS,
)
from api_server.db.models import UserRole

# Estados derivados que la UI pinta. Se calculan de las tres columnas de ciclo
# de vida (`redeemed_at` / `revoked_at` / `expires_at`) en vez de guardarse, para
# que no puedan quedar desincronizados con la realidad de la fila.
INVITATION_STATUS_PENDING = "pending"
INVITATION_STATUS_REDEEMED = "redeemed"
INVITATION_STATUS_REVOKED = "revoked"
INVITATION_STATUS_EXPIRED = "expired"


class InvitationCreateRequest(BaseModel):
    """Payload de ``POST /admin/invitations``."""

    email: EmailStr
    tenant_id: UUID
    role: str = Field(default=UserRole.TENANT_USER.value)
    expires_in_hours: int = Field(
        default=DEFAULT_INVITATION_TTL_HOURS,
        ge=1,
        le=MAX_INVITATION_TTL_HOURS,
        description="Vigencia de la invitación en horas. Siempre finita (ADR 0134).",
    )

    @field_validator("role")
    @classmethod
    def _role_must_be_known(cls, value: str) -> str:
        """El rol acaba en una membresía, así que solo valen los del enum.

        Si no se validara aquí, un rol inventado se persistiría en la invitación
        y solo daría la cara al canjear —creando una membresía con un rol que
        Casbin no conoce—, es decir, un usuario dentro del tenant sin permisos
        y sin explicación.
        """
        allowed = {r.value for r in UserRole}
        if value not in allowed:
            raise ValueError(f"role must be one of {sorted(allowed)}")
        return value


class InvitationResponse(BaseModel):
    """Una invitación tal y como la ve el admin en el listado. SIN el token."""

    id: UUID
    tenant_id: UUID
    tenant_name: str | None = None
    email: EmailStr
    role: str
    token_prefix: str
    status: str
    expires_at: datetime
    redeemed_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime


class IssuedInvitationResponse(InvitationResponse):
    """La respuesta de la EMISIÓN — la única que lleva el token en claro.

    El valor de ``token`` no se persiste en ninguna parte: se enseña al admin
    para que se lo haga llegar al invitado y se pierde. Si se extravía, la vía
    es revocar y emitir otra, nunca «recuperarlo».
    """

    token: str


__all__ = [
    "INVITATION_STATUS_EXPIRED",
    "INVITATION_STATUS_PENDING",
    "INVITATION_STATUS_REDEEMED",
    "INVITATION_STATUS_REVOKED",
    "InvitationCreateRequest",
    "InvitationResponse",
    "IssuedInvitationResponse",
]
