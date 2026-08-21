"""`/auth/*` endpoints — register, login, logout, me.

The endpoints in this router are scoped to a single user, not to a
tenant. They open their own AsyncSession directly (without going
through `get_tenant_session`) because the user is, by definition,
not yet authenticated.

Login enforces a per-IP AND a per-email rate limit independently:
either tripping the threshold returns 429. This frustrates credential
stuffing (lots of emails from one IP) and password spraying (one email
from lots of IPs) at the same time.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.auth.audit import write_audit_log
from api_server.auth.cookies import clear_session_cookies, issue_session_cookies
from api_server.auth.deps import (
    AuthPrincipal,
    get_client_ip,
    get_mfa_challenge_store,
    get_principal,
    get_rate_limiter,
    get_session_store,
)
from api_server.auth.invitations import hash_invitation_token, verify_invitation_token
from api_server.auth.jwt import encode_jwt
from api_server.auth.mfa.challenge_store import MfaChallenge, MfaChallengeStore, new_challenge_token
from api_server.auth.mfa.store import user_mfa_methods
from api_server.auth.passwords import burn_password_verification, hash_password, verify_password
from api_server.auth.rate_limit import RateLimiter
from api_server.auth.sessions import SessionStore
from api_server.config import get_settings
from api_server.db.invitation import UserInvitation
from api_server.db.models import Organization, User, UserOrganizationMembership
from api_server.db.session import get_admin_sessionmaker, get_sessionmaker
from api_server.schemas.auth import (
    RESOLUTION_STATE_ADMIN,
    RESOLUTION_STATE_MULTIPLE,
    RESOLUTION_STATE_NO_ACCESS,
    RESOLUTION_STATE_SINGLE,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    ResolvedMembership,
    SelectTenantRequest,
    SessionResolutionResponse,
    UserResponse,
)
from api_server.schemas.mfa import MfaRequiredResponse

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_user_response(u: User) -> UserResponse:
    return UserResponse(
        id=u.id,
        email=u.email,
        full_name=u.full_name,
        is_system_admin=u.is_system_admin,
        is_system_owner=u.is_system_owner,
        is_active=u.is_active,
    )


async def _fetch_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Look up a user by email. RLS is NOT involved (users is un-RLSed)."""
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


def _verify_login_password(user: User | None, plain: str) -> bool:
    """Único punto de decisión del primer factor — y gasta argon2 SIEMPRE (authz-7).

    Las tres formas de «este login no puede prosperar» (email desconocido,
    usuario inactivo, identidad aprovisionada por SSO) no tienen contra qué
    comparar, y devolver False sin más dejaba la respuesta decenas de
    milisegundos por delante de un intento con contraseña incorrecta: eso es
    un oráculo de enumeración de usuarios medible desde fuera. La rama de
    relleno quema el mismo trabajo.

    El caso SSO además NO puede pasar por `verify_password`: su
    `password_hash` es un centinela que no es una codificación argon2 válida
    y levantaría `ValueError` (500 en vez de 401).
    """
    if user is None or not user.is_active or user.is_sso_provisioned:
        burn_password_verification(plain)
        return False
    return verify_password(plain, user.password_hash)


async def _load_active_memberships(user_id: UUID) -> list[ResolvedMembership]:
    """Return the user's ACTIVE, non-deleted tenant memberships (ADR 0047).

    Tenant access is granted EXCLUSIVELY by ``UserOrganizationMembership``
    that the admin assigns AFTER login — no email-domain claiming, no
    auto-created membership (ADR 0047). This is the single source of truth
    for the post-login resolution: 0 rows → "no access", 1 → enter, >1 →
    picker.

    Runs on the BYPASSRLS admin engine because the caller's session has no
    active tenant yet (``app.tenant_id`` is unset), so the RLS policy on
    ``user_org_memberships`` would hide every row. Cross-tenant exposure is
    prevented by constraining the query to ``user_id = user_id`` — the
    user only ever sees their OWN memberships, never another user's.
    """
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(
            select(
                UserOrganizationMembership.tenant_id,
                UserOrganizationMembership.role,
                Organization.name.label("tenant_name"),
            )
            .join(
                Organization,
                Organization.id == UserOrganizationMembership.tenant_id,
            )
            .where(
                UserOrganizationMembership.user_id == user_id,
                UserOrganizationMembership.is_active.is_(True),
                UserOrganizationMembership.deleted_at.is_(None),
                # Only memberships into a live tenant count (a soft-deleted
                # organization grants no access).
                Organization.deleted_at.is_(None),
            )
            .order_by(Organization.name)
        )
        return [
            ResolvedMembership(
                tenant_id=row.tenant_id,
                tenant_name=row.tenant_name,
                role=row.role,
            )
            for row in result.all()
        ]


async def _is_active_member(user_id: UUID, tenant_id: UUID) -> bool:
    """True iff ``user_id`` has an ACTIVE, non-deleted membership in a live
    ``tenant_id`` (ADR 0047). The select-tenant endpoint's authorization
    check — a user can only activate a tenant they actually belong to."""
    memberships = await _load_active_memberships(user_id)
    return any(m.tenant_id == tenant_id for m in memberships)


async def _mint_tenant_session(
    sessions: SessionStore,
    *,
    user_id: UUID,
    tenant_id: UUID | None,
    is_system_admin: bool,
    is_system_owner: bool = False,
    response: Response | None = None,
) -> LoginResponse:
    """Mint a session + JWT bound to ``tenant_id`` (or tenant-less if None).

    Shared by the password login, the post-login single-tenant
    auto-resolution, and the explicit tenant pick. The Redis session and
    the JWT share one TTL so they expire together; the JWT's ``tid`` claim
    is what ``get_principal`` reads to scope RLS for a REGULAR user (who,
    unlike a system admin, cannot override the tenant via ``X-Tenant-Id``).

    When ``response`` is given the fresh token also REPLACES the panel's session
    cookie (ADR 0133). That is not a nicety: the cookie is the browser's only
    credential now, so a tenant-scoped token that stayed in the body would leave
    the browser sending the tenant-LESS identity token forever and every
    tenant-scoped write answering "active tenant required".
    """
    settings = get_settings()
    session_id = uuid7()
    ttl_seconds = settings.jwt_expiration_minutes * 60
    await sessions.create(
        session_id,
        user_id=user_id,
        tenant_id=tenant_id,
        ttl_seconds=ttl_seconds,
    )
    token = encode_jwt(
        user_id=user_id,
        session_id=session_id,
        tenant_id=tenant_id,
        is_system_admin=is_system_admin,
        is_system_owner=is_system_owner,
    )
    if response is not None:
        issue_session_cookies(response, token=token, max_age_seconds=ttl_seconds)
    return LoginResponse(access_token=token, token_type="bearer", expires_in=ttl_seconds)


# ---------------------------------------------------------------------------
# POST /auth/register  — cerrado al público, alta por invitación (ADR 0134)
# ---------------------------------------------------------------------------
# Cuerpo ÚNICO del rechazo. Todos los caminos por los que un desconocido no
# puede darse de alta terminan aquí con el mismo código y el mismo texto:
#
#   * no trae token de invitación,
#   * trae uno inventado,
#   * trae uno caducado, revocado o ya canjeado,
#   * trae uno bueno pero para otro email.
#
# Que sean indistinguibles es el punto, no un descuido: el registro abierto era
# un oráculo de enumeración perfecto (409 = «ese email existe», 201 = «no»), y
# cerrarlo sin unificar la respuesta habría movido el oráculo en vez de
# cerrarlo (ADR 0134, condición 2). Por lo mismo, la comprobación de la puerta
# ocurre ANTES de tocar la tabla `users` con el email presentado: en el camino
# cerrado no hay INSERT que pueda chocar, así que no hay diferencia de tiempo
# entre un email conocido y uno desconocido.
_REGISTRATION_CLOSED_DETAIL = "registration is by invitation only"


async def _resolve_invitation(
    session: AsyncSession, token: str | None, *, email: str
) -> UserInvitation | None:
    """Resuelve el token presentado a la invitación que sí puede canjearse.

    Devuelve ``None`` ante CUALQUIER motivo de rechazo — quien llama lo traduce
    al 403 único, sin decir nunca cuál de los motivos fue.

    Corre sobre la sesión BYPASSRLS porque la petición aún no tiene tenant: es
    el token lo que determina en qué tenant entra el invitado, igual que un
    bearer de SCIM o un ``X-API-Token`` determinan el suyo. No hay fuga
    cross-tenant porque la búsqueda va por ``token_hash``, que es globalmente
    único y no es adivinable.
    """
    if not token:
        return None
    result = await session.execute(
        select(UserInvitation).where(UserInvitation.token_hash == hash_invitation_token(token))
    )
    invitation = result.scalar_one_or_none()
    if invitation is None:
        return None
    # Redundante con la búsqueda por igualdad, pero deja la comparación del
    # secreto en el helper de tiempo constante en vez de en el planner de
    # PostgreSQL, que es donde el repo la tiene para las otras credenciales.
    if not verify_invitation_token(token, invitation.token_hash):
        return None
    if not invitation.is_redeemable():
        return None
    # El email es parte de la credencial: sin esto, una invitación filtrada
    # (correo reenviado, captura de pantalla) daría de alta a cualquiera con la
    # dirección que eligiera.
    if invitation.email.lower() != email:
        return None
    return invitation


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: RegisterRequest,
    request: Request,
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
) -> UserResponse:
    """Alta de un usuario. El registro está CERRADO al público (ADR 0134).

    Dos —y solo dos— formas de pasar por aquí:

    **1. El arranque de la instalación.** Con la tabla ``users`` vacía el
    registro se permite SIEMPRE y sin invitación, y promociona al primer usuario
    a ``is_system_admin`` **y** ``is_system_owner``. No es una comodidad: sin
    esta puerta una instalación nueva —o una reinstalación tras vaciar la base—
    quedaría inaccesible para siempre, porque no habría nadie que pudiera emitir
    la primera invitación. El chequeo y el INSERT van en la misma transacción, y
    el índice único parcial ``uq_users_system_owner`` garantiza que una carrera
    entre dos registros simultáneos no pueda acuñar dos propietarios.

    **2. Una invitación válida**, emitida por un admin desde
    ``/admin/invitations``, no caducada, no revocada, no canjeada y emitida para
    ESTE email. El canje es atómico: sella la invitación con un UPDATE
    condicional (compare-and-set, así que dos canjes simultáneos del mismo token
    no pueden colar dos altas) y crea la ``UserOrganizationMembership`` con el
    tenant y el rol que la invitación llevaba — sin esa membresía el invitado
    entraría solo para ver la pantalla ``no_access``.

    Cualquier otra cosa es un 403 genérico (ver
    ``_REGISTRATION_CLOSED_DETAIL``). Un invitado NUNCA sale system admin ni
    system owner: esos dos bits son exclusivos del arranque.
    """
    settings = get_settings()
    # Ventana deslizante por IP ANTES de tocar la base de datos (authz-6). Sin
    # ella, `invitation_token` es un secreto que se puede probar en bucle
    # gratis, y cada intento cuesta al servidor una consulta y —cuando el token
    # cuela— un argon2 de 64 MiB. El presupuesto se gasta pase lo que pase con
    # el intento: si solo contásemos los fallos, quien acierta reinicia el
    # reloj. La puerta de arranque (tabla `users` vacía) NO es excepción.
    allowed, _ = await rate_limiter.check(
        f"rl:register:ip:{get_client_ip(request)}",
        limit=settings.register_rate_limit_count,
        window_seconds=settings.register_rate_limit_window_seconds,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many registration attempts; retry later",
            headers={"Retry-After": str(settings.register_rate_limit_window_seconds)},
        )

    email = payload.email.lower()
    # Sesión BYPASSRLS: hay que leer/escribir `user_invitations` y
    # `user_org_memberships` (ambas con RLS) sin tenant activo — la petición es
    # anónima por definición. Es el mismo motor que ya usan la resolución de
    # membresías post-login y todo `/admin/*`.
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        existing_users = await session.execute(select(User.id).limit(1))
        is_first_user = existing_users.scalar_one_or_none() is None

        invitation: UserInvitation | None = None
        if not is_first_user:
            invitation = await _resolve_invitation(session, payload.invitation_token, email=email)
            if invitation is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=_REGISTRATION_CLOSED_DETAIL,
                )

        user = User(
            id=uuid7(),
            email=email,
            password_hash=hash_password(payload.password),
            full_name=payload.full_name,
            is_system_admin=is_first_user,
            # The very first operator is also the System Owner (córtex F0, ADR 0074).
            # Singleton enforced by the partial unique index; subsequent users default
            # to false and ownership is never granted via SSO nor by an invitation.
            is_system_owner=is_first_user,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="email already registered",
            ) from exc

        if invitation is not None:
            await _redeem_invitation(session, invitation, user_id=user.id)

        # Re-read to grab DB-side defaults (is_active, is_system_admin).
        await session.refresh(user)
        return _to_user_response(user)


async def _redeem_invitation(
    session: AsyncSession, invitation: UserInvitation, *, user_id: UUID
) -> None:
    """Sella la invitación y materializa la membresía, en la misma transacción.

    El UPDATE lleva las tres condiciones de validez EN EL WHERE, no en Python:
    es un compare-and-set. Dos canjes simultáneos del mismo token se serializan
    en la fila; el segundo re-evalúa el predicado tras el lock, ve
    ``redeemed_at`` ya puesto y afecta a 0 filas — el «un solo uso» queda
    garantizado por la base de datos y no por el orden en que se lean las cosas.
    """
    redeemed = await session.execute(
        update(UserInvitation)
        .where(
            UserInvitation.id == invitation.id,
            UserInvitation.redeemed_at.is_(None),
            UserInvitation.revoked_at.is_(None),
            UserInvitation.expires_at > func.now(),
        )
        .values(redeemed_at=func.now(), redeemed_by_user_id=user_id)
    )
    # Un UPDATE devuelve CursorResult en runtime; `Result[Any]` no tipa rowcount.
    if int(getattr(redeemed, "rowcount", 0) or 0) != 1:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_REGISTRATION_CLOSED_DETAIL,
        )

    session.add(
        UserOrganizationMembership(
            id=uuid7(),
            tenant_id=invitation.tenant_id,
            user_id=user_id,
            role=invitation.role,
            is_active=True,
        )
    )
    await session.flush()


async def _audit_login(action: str, *, user_id: UUID | None, email: str, ip: str | None) -> None:
    """Rastro append-only del login en ``audit_log`` (AUD16-16 / F6).

    Sesión admin PROPIA (BYPASSRLS, tenant NULL) fuera de la txn del lookup —
    una excepción en el flujo de login no puede revertir el rastro, y un fallo
    del rastro JAMÁS rompe el login (best-effort). El payload lleva el email y
    la IP; nunca una contraseña."""
    try:
        sessionmaker = get_admin_sessionmaker()
        async with sessionmaker() as db, db.begin():
            await write_audit_log(
                db,
                action=action,
                actor_user_id=user_id,
                tenant_id=None,
                resource_type="auth_session",
                changes={"email": email},
                ip_address=ip,
            )
    except Exception:  # pragma: no cover — el rastro nunca tumba el login
        import logging

        logging.getLogger(__name__).warning("auth.login_audit_failed", exc_info=True)


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------
@router.post("/login", response_model=LoginResponse | MfaRequiredResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    sessions: SessionStore = Depends(get_session_store),
    challenges: MfaChallengeStore = Depends(get_mfa_challenge_store),
) -> LoginResponse | MfaRequiredResponse:
    """Verify credentials, then issue a session — OR, if the user has a
    confirmed TOTP factor, return an interim ``mfa_required`` challenge.

    A user WITHOUT a confirmed second factor logs in EXACTLY as before:
    the password check passes straight to a Redis session + JWT. A user
    WITH a confirmed TOTP factor gets NO session here — only a single-use,
    short-lived challenge token to complete at ``/auth/mfa/totp/verify``.
    """
    settings = get_settings()
    ip = get_client_ip(request)
    email = payload.email.lower()

    # Per-IP and per-email sliding windows. Both record the hit
    # whether or not the credentials end up matching — this prevents
    # an attacker from resetting the clock by cycling emails.
    allowed_ip, _ = await rate_limiter.check(
        f"rl:login:ip:{ip}",
        limit=settings.login_rate_limit_count,
        window_seconds=settings.login_rate_limit_window_seconds,
    )
    allowed_email, _ = await rate_limiter.check(
        f"rl:login:email:{email}",
        limit=settings.login_rate_limit_count,
        window_seconds=settings.login_rate_limit_window_seconds,
    )
    if not allowed_ip or not allowed_email:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many login attempts; retry later",
            headers={"Retry-After": str(settings.login_rate_limit_window_seconds)},
        )

    sessionmaker = get_sessionmaker()
    failed_user_id: UUID | None = None
    login_failed = False
    async with sessionmaker() as db, db.begin():
        user = await _fetch_user_by_email(db, email)

        # Generic 401 — never leak whether the email exists, whether it's
        # inactive, or whether it is an SSO-only identity. `_verify_login_password`
        # spends the same argon2 work on every one of those branches, so the
        # response time does not leak it either (authz-7).
        password_ok = _verify_login_password(user, payload.password)
        if user is None or not password_ok:
            login_failed = True
            failed_user_id = user.id if user else None
        else:
            user_id = user.id
            is_system_admin = user.is_system_admin
            is_system_owner = user.is_system_owner
    if login_failed:
        # AUD16-16 (F6): el rastro se escribe FUERA de la txn del lookup (una
        # excepción dentro la habría revertido) y antes del 401 genérico. El
        # user_id viaja cuando el email era conocido; la contraseña, jamás.
        await _audit_login("auth.login.failure", user_id=failed_user_id, email=email, ip=ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid email or password",
        )

    # First factor passed. If the user has ANY confirmed second factor
    # (TOTP or WebAuthn), do NOT mint a session here — return an interim
    # challenge instead, advertising the methods the user can complete it
    # with. A user without MFA falls straight through to a session, exactly
    # as before. The check runs outside the login txn (it is a narrow,
    # tenant-agnostic existence probe on the admin role).
    methods = await user_mfa_methods(user_id)
    if methods:
        mfa_token = new_challenge_token()
        await challenges.create(
            mfa_token,
            MfaChallenge(user_id=user_id, tenant_id=None, is_system_admin=is_system_admin),
            ttl_seconds=settings.mfa_challenge_ttl_seconds,
        )
        await _audit_login("auth.login.mfa_challenge", user_id=user_id, email=email, ip=ip)
        return MfaRequiredResponse(mfa_token=mfa_token, mfa_methods=methods)

    # Issue a session id and persist it in Redis with the same
    # TTL as the JWT — both expire together.
    session_id = uuid7()
    ttl_seconds = settings.jwt_expiration_minutes * 60
    await sessions.create(
        session_id,
        user_id=user_id,
        tenant_id=None,
        ttl_seconds=ttl_seconds,
    )

    token = encode_jwt(
        user_id=user_id,
        session_id=session_id,
        tenant_id=None,
        is_system_admin=is_system_admin,
        is_system_owner=is_system_owner,
    )

    # AUD16-16 (F6): rastro del login bueno — audit_log llevaba 0 filas en toda
    # la historia y el docstring de write_audit_log afirmaba 'called from login'.
    await _audit_login("auth.login.success", user_id=user_id, email=email, ip=ip)

    # ADR 0133: la sesión del PANEL viaja como cookie httpOnly. El
    # `access_token` del cuerpo se conserva —es la pata de compatibilidad para
    # `curl`, los SDK y `scripts/`— pero el panel ya no lo guarda en ningún
    # sitio: el agujero era `localStorage`, no la respuesta del login.
    issue_session_cookies(response, token=token, max_age_seconds=ttl_seconds)

    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=ttl_seconds,
    )


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------
@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    principal: AuthPrincipal = Depends(get_principal),
    sessions: SessionStore = Depends(get_session_store),
) -> None:
    """Revoke the current session. Subsequent requests with the same
    JWT will be rejected (401) because the sid is gone from Redis.

    The session + CSRF cookies are expired too (ADR 0133). The Redis revocation
    is the authoritative half — a browser that ignored the ``Set-Cookie`` still
    holds a dead credential — but leaving the cookie in place would make the
    panel believe it still has a session and bounce the user around a 401 loop.
    """
    await sessions.revoke(principal.session_id)
    clear_session_cookies(response)


# ---------------------------------------------------------------------------
# GET /auth/session/resolve  (ADR 0047, task_sso_03)
# ---------------------------------------------------------------------------
@router.get("/session/resolve", response_model=SessionResolutionResponse)
async def resolve_session(
    response: Response,
    principal: AuthPrincipal = Depends(get_principal),
    sessions: SessionStore = Depends(get_session_store),
) -> SessionResolutionResponse:
    """Resolve the tenant the authenticated identity may enter (ADR 0047).

    Called by the client RIGHT AFTER a successful login — password OR SSO,
    both produce the same tenant-less IDENTITY session — to turn the user's
    ACTIVE memberships into a typed next step:

      * **0 memberships** → ``state="no_access"``: the session stays valid
        (it proves identity) but the user has NO tenant; the admin-panel
        shows the "sin permisos, contacta al administrador" screen. NO
        token is minted and NO membership is auto-created (ADR 0047 —
        deny-by-default, access is granted only by an explicit admin
        assignment).
      * **1 membership** → ``state="single"``: a fresh TENANT-SCOPED token
        is minted and returned so the client enters that tenant directly.
      * **>1 memberships** → ``state="multiple"``: the client shows the
        tenant-picker and then POSTs ``/auth/session/select-tenant``.

    A System Admin is never locked out by membership. Their cross-tenant
    powers come from the ``X-Tenant-Id`` override + BYPASSRLS engine
    (``auth/deps.py``), not from a membership row, so a superadmin with NO
    membership must NOT hit ``no_access`` — that would be a chicken-and-egg
    lockout (they could not even reach ``/admin/users`` to grant themselves
    one). They resolve to ``state="admin"`` and enter the PORTFOLIO view
    (no active tenant) with the tenant-less identity token they already
    hold; the header tenant-picker switches tenant or bootstraps the first
    one. A superadmin who ALSO has explicit memberships still flows through
    ``single``/``multiple`` below (and can switch to "all tenants" from the
    header regardless).
    """
    memberships = await _load_active_memberships(principal.user_id)

    if principal.is_system_admin and not memberships:
        # Portfolio entry: no token minted — the identity token is already
        # tenant-less and carries ``is_system_admin`` (see login/SSO mint).
        return SessionResolutionResponse(
            state=RESOLUTION_STATE_ADMIN,
            memberships=[],
        )

    if not memberships:
        return SessionResolutionResponse(
            state=RESOLUTION_STATE_NO_ACCESS,
            memberships=[],
        )

    if len(memberships) == 1:
        only = memberships[0]
        minted = await _mint_tenant_session(
            sessions,
            user_id=principal.user_id,
            tenant_id=only.tenant_id,
            is_system_admin=principal.is_system_admin,
            is_system_owner=principal.is_system_owner,
            response=response,
        )
        return SessionResolutionResponse(
            state=RESOLUTION_STATE_SINGLE,
            memberships=memberships,
            access_token=minted.access_token,
            token_type=minted.token_type,
            expires_in=minted.expires_in,
        )

    return SessionResolutionResponse(
        state=RESOLUTION_STATE_MULTIPLE,
        memberships=memberships,
    )


# ---------------------------------------------------------------------------
# POST /auth/session/select-tenant  (ADR 0047, task_sso_03)
# ---------------------------------------------------------------------------
@router.post("/session/select-tenant", response_model=LoginResponse)
async def select_tenant(
    payload: SelectTenantRequest,
    response: Response,
    principal: AuthPrincipal = Depends(get_principal),
    sessions: SessionStore = Depends(get_session_store),
) -> LoginResponse:
    """Activate one of the user's tenants and mint a tenant-scoped token.

    The tenant-picker (``state="multiple"``) POSTs the chosen ``tenant_id``
    here. We re-assert an ACTIVE membership for ``(user_id, tenant_id)`` —
    the user can NEVER activate a tenant they don't belong to (a forged id
    returns 403) — then mint a fresh session + JWT carrying that tenant in
    its ``tid`` claim. This is the mechanism by which a REGULAR user (who
    cannot use the ``X-Tenant-Id`` superadmin override) acquires a
    tenant-scoped session.
    """
    if not await _is_active_member(principal.user_id, payload.tenant_id):
        # Same answer whether the tenant doesn't exist or the user simply
        # isn't a member — never reveal which.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="no active membership in the requested tenant",
        )
    return await _mint_tenant_session(
        sessions,
        user_id=principal.user_id,
        tenant_id=payload.tenant_id,
        is_system_admin=principal.is_system_admin,
        is_system_owner=principal.is_system_owner,
        response=response,
    )


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------
@router.get("/me", response_model=UserResponse)
async def me(
    principal: AuthPrincipal = Depends(get_principal),
) -> UserResponse:
    """Return the authenticated user's profile."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(select(User).where(User.id == principal.user_id))
        user = result.scalar_one_or_none()
        if user is None:
            # Token references a row that no longer exists. The Redis
            # session is still alive (otherwise get_principal would
            # have 401'd already), but the user got deleted — treat
            # the token as stale.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="user no longer exists",
            )
        return _to_user_response(user)
