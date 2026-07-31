"""`/admin/invitations` — emisión, listado y revocación (ADR 0134, opción C).

Con ``POST /auth/register`` cerrado, este router es la única forma de que un
usuario nuevo pueda darse de alta: un System Admin emite un token para un email,
un tenant y un rol concretos, y se lo hace llegar al invitado.

Está bajo el prefijo ``/admin``, así que el montaje en :mod:`api_server.main`
le aplica automáticamente ``require_hardened_system_admin`` (MFA + allowlist de
IP + TTL corto en staging/prod). No hay que acordarse de la dependencia: la
guarda va por el mero hecho de estar montado.

El token en claro se devuelve **una sola vez**, en la respuesta de la emisión.
El listado enseña únicamente ``token_prefix`` — igual que los de ``api_tokens``
y ``scim_tokens``. Si el admin lo pierde, la vía es revocar y emitir otra.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.auth.audit import write_audit_log
from api_server.auth.deps import (
    AuthPrincipal,
    get_admin_session,
    get_client_ip,
    require_system_admin,
)
from api_server.auth.invitations import generate_invitation_token
from api_server.db.invitation import UserInvitation
from api_server.db.models import Organization
from api_server.schemas.invitations import (
    INVITATION_STATUS_EXPIRED,
    INVITATION_STATUS_PENDING,
    INVITATION_STATUS_REDEEMED,
    INVITATION_STATUS_REVOKED,
    InvitationCreateRequest,
    InvitationResponse,
    IssuedInvitationResponse,
)

router = APIRouter(prefix="/admin/invitations", tags=["admin", "invitations"])


def _status_of(invitation: UserInvitation, *, now: datetime) -> str:
    """Estado derivado de las tres columnas de ciclo de vida.

    Derivado y no almacenado: una columna `status` habría que mantenerla al día
    con el paso del tiempo (nadie escribe una fila cuando caduca), y acabaría
    mintiendo. El orden importa — canjeada gana sobre revocada, y ambas sobre
    caducada — porque describe lo que de hecho le pasó a la invitación.
    """
    if invitation.redeemed_at is not None:
        return INVITATION_STATUS_REDEEMED
    if invitation.revoked_at is not None:
        return INVITATION_STATUS_REVOKED
    if invitation.expires_at <= now:
        return INVITATION_STATUS_EXPIRED
    return INVITATION_STATUS_PENDING


def _to_response(
    invitation: UserInvitation,
    *,
    tenant_name: str | None,
    now: datetime,
) -> InvitationResponse:
    return InvitationResponse(
        id=invitation.id,
        tenant_id=invitation.tenant_id,
        tenant_name=tenant_name,
        email=invitation.email,
        role=invitation.role,
        token_prefix=invitation.token_prefix,
        status=_status_of(invitation, now=now),
        expires_at=invitation.expires_at,
        redeemed_at=invitation.redeemed_at,
        revoked_at=invitation.revoked_at,
        created_at=invitation.created_at,
    )


async def _get_live_tenant_or_404(session: AsyncSession, tenant_id: UUID) -> Organization:
    org = (
        await session.execute(select(Organization).where(Organization.id == tenant_id))
    ).scalar_one_or_none()
    if org is None or org.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tenant not found")
    return org


# ---------------------------------------------------------------------------
# POST /admin/invitations — emitir
# ---------------------------------------------------------------------------
@router.post("", response_model=IssuedInvitationResponse, status_code=status.HTTP_201_CREATED)
async def create_invitation(
    payload: InvitationCreateRequest,
    request: Request,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> IssuedInvitationResponse:
    """Emite una invitación y devuelve el token en claro UNA sola vez.

    Se comprueba que el tenant existe y está vivo antes de emitir: una
    invitación hacia un tenant borrado sería un token que solo sirve para
    fallar en el canje, y el invitado no tendría forma de entender por qué.

    Si ya hay una invitación PENDIENTE para el mismo (tenant, email) se
    responde 409 en vez de acumular tokens vivos para la misma persona — cada
    token vivo es una puerta abierta más que auditar.
    """
    email = payload.email.lower()
    await _get_live_tenant_or_404(session, payload.tenant_id)

    now = datetime.now(UTC)
    pending = (
        await session.execute(
            select(UserInvitation.id).where(
                UserInvitation.tenant_id == payload.tenant_id,
                UserInvitation.email == email,
                UserInvitation.redeemed_at.is_(None),
                UserInvitation.revoked_at.is_(None),
                UserInvitation.expires_at > now,
            )
        )
    ).scalar_one_or_none()
    if pending is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a pending invitation already exists for this email and tenant",
        )

    minted = generate_invitation_token()
    invitation = UserInvitation(
        id=uuid7(),
        tenant_id=payload.tenant_id,
        email=email,
        token_hash=minted.token_hash,
        token_prefix=minted.prefix,
        role=payload.role,
        expires_at=now + timedelta(hours=payload.expires_in_hours),
        created_by=principal.user_id,
    )
    session.add(invitation)
    await session.flush()

    await write_audit_log(
        session,
        action="invitation.issued",
        actor_user_id=principal.user_id,
        tenant_id=payload.tenant_id,
        resource_type="user_invitation",
        resource_id=invitation.id,
        # El rastro lleva a QUIÉN se invitó y con qué rol; el token JAMÁS.
        changes={"email": email, "role": payload.role, "prefix": minted.prefix},
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await session.refresh(invitation)

    org = await _get_live_tenant_or_404(session, payload.tenant_id)
    base = _to_response(invitation, tenant_name=org.name, now=now)
    return IssuedInvitationResponse(**base.model_dump(), token=minted.token)


# ---------------------------------------------------------------------------
# GET /admin/invitations — listar
# ---------------------------------------------------------------------------
@router.get("", response_model=list[InvitationResponse])
async def list_invitations(
    tenant_id: UUID | None = Query(default=None),
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> list[InvitationResponse]:
    """Lista las invitaciones (opcionalmente las de un tenant). SIN el token."""
    stmt = (
        select(UserInvitation, Organization.name)
        .join(Organization, Organization.id == UserInvitation.tenant_id, isouter=True)
        .order_by(UserInvitation.created_at.desc())
    )
    if tenant_id is not None:
        stmt = stmt.where(UserInvitation.tenant_id == tenant_id)

    now = datetime.now(UTC)
    rows = (await session.execute(stmt)).all()
    return [_to_response(inv, tenant_name=name, now=now) for inv, name in rows]


# ---------------------------------------------------------------------------
# POST /admin/invitations/{id}/revoke — revocar
# ---------------------------------------------------------------------------
@router.post("/{invitation_id}/revoke", response_model=InvitationResponse)
async def revoke_invitation(
    invitation_id: UUID,
    request: Request,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> InvitationResponse:
    """Revoca una invitación pendiente. Idempotente sobre una ya revocada.

    Una invitación **ya canjeada** no se revoca: el usuario existe y quitarle el
    acceso es un gesto distinto (revocar su membresía en ``/admin/users``).
    Fingir que se «revoca» aquí daría al admin la falsa sensación de haber
    cerrado una puerta que sigue abierta.
    """
    invitation = (
        await session.execute(select(UserInvitation).where(UserInvitation.id == invitation_id))
    ).scalar_one_or_none()
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invitation not found")
    if invitation.redeemed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="invitation already redeemed; revoke the user's membership instead",
        )

    now = datetime.now(UTC)
    if invitation.revoked_at is None:
        invitation.revoked_at = now
        await session.flush()
        await write_audit_log(
            session,
            action="invitation.revoked",
            actor_user_id=principal.user_id,
            tenant_id=invitation.tenant_id,
            resource_type="user_invitation",
            resource_id=invitation.id,
            changes={"email": invitation.email, "prefix": invitation.token_prefix},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        await session.refresh(invitation)

    org = (
        await session.execute(
            select(Organization.name).where(Organization.id == invitation.tenant_id)
        )
    ).scalar_one_or_none()
    return _to_response(invitation, tenant_name=org, now=now)


__all__ = ["router"]
