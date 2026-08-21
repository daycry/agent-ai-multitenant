"""``user_invitations`` — el alta por invitación (ADR 0134, opción C).

Con ``POST /auth/register`` cerrado al público, una fila de esta tabla es la
ÚNICA forma de que un usuario nuevo entre en la plataforma (salvo el arranque de
la primera instalación, donde la tabla ``users`` vacía abre el registro, y las
vías de IdP: SSO JIT y SCIM).

Multi-tenancy: la invitación es tenant-scoped (:class:`TenantScopedMixin` + RLS).
No es solo higiene del principio 1 de CLAUDE.md — el tenant es lo que el canje
convierte en la ``UserOrganizationMembership`` que da acceso de verdad. Una
invitación sin tenant dejaría al invitado en la pantalla ``no_access``, es decir,
habríamos construido el mecanismo entero sin que nadie viera el resultado.

Manejo del secreto (CLAUDE.md: ningún secreto en claro en la BD). El token es un
valor aleatorio largo que se enseña al admin EXACTAMENTE UNA VEZ al emitirlo y
nunca se persiste en claro: solo su ``token_hash`` SHA-256. Se usa SHA-256 y no
un argon2 salteado a propósito — el canje llega sin autenticar, así que hay que
localizar la fila por el valor presentado, y un hash salteado por fila no se
puede buscar; el token es de alta entropía, luego el digest determinista es
seguro. Mismo razonamiento que :class:`~api_server.db.models.ScimToken` y
:class:`~api_server.db.models.ApiToken`. Ver
:mod:`api_server.auth.invitations` para los helpers de emisión/hash/verificación.

Ciclo de vida (los tres motivos por los que una invitación deja de valer):

  * ``expires_at``  — vigencia obligatoria; una invitación caducada no canjea
    nada. NUNCA es NULL: un token sin caducidad es una puerta abierta que nadie
    recuerda haber dejado.
  * ``redeemed_at`` — UN SOLO USO. En cuanto se canjea queda inservible, para
    que un token filtrado (correo reenviado, historial de chat) no sirva de alta
    repetida.
  * ``revoked_at``  — revocación explícita por el admin; se conserva la fila
    para el rastro.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import (
    Base,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class UserInvitation(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """Una invitación de alta emitida por un admin (ADR 0134)."""

    __tablename__ = "user_invitations"
    __table_args__ = (
        # El digest es globalmente único: identifica la invitación en una
        # petición NO autenticada, y el UNIQUE convierte la búsqueda por hash en
        # un sondeo de índice.
        UniqueConstraint("token_hash", name="uq_user_invitation_token_hash"),
        # Índice parcial de las PENDIENTES — es la única consulta caliente
        # (listado del admin y comprobación de duplicados por email).
        Index(
            "ix_user_invitations_tenant_pending",
            "tenant_id",
            "email",
            postgresql_where=text("redeemed_at IS NULL AND revoked_at IS NULL"),
        ),
        # La FK de `tenant_id` que creó la migración 0127; `TenantScopedMixin` no
        # declara ninguna, así que sin esto un autogenerate propone BORRARLA — y
        # con ella el borrado en cascada de las invitaciones al eliminar un
        # tenant, que es lo que impide que un token emitido siga canjeándose
        # contra un tenant que ya no existe.
        #
        # SIN `name=` a propósito: la 0127 la creó sin nombrarla dentro del
        # `create_table`, así que la lleva el default de PostgreSQL
        # (`user_invitations_tenant_id_fkey`). El modelo declara lo que declaró la
        # migración; escribir aquí ese nombre a mano sería copiar un default.
        ForeignKeyConstraint(["tenant_id"], ["organizations.id"], ondelete="CASCADE"),
    )

    # `TenantScopedMixin` declara `index=True` en `tenant_id` para todas sus
    # tablas por igual; aquí ese índice PLANO no existe en la base de datos,
    # porque la migración 0127 creó en su lugar el parcial de arriba
    # (`ix_user_invitations_tenant_pending`), cuya columna guía ya es
    # `tenant_id`.
    #
    # A diferencia de `knowledge_bases` y `memory_entries` —donde ninguna
    # consulta mira fuera del predicado—, aquí SÍ hay una que se queda fuera:
    # `GET /admin/invitations?tenant_id=…` (`routers/invitations.py`) lista
    # TAMBIÉN las canjeadas, revocadas y caducadas, para el rastro. Aun así el
    # índice plano no paga su coste: es una pantalla de administración, la tabla
    # crece a ritmo de altas de personas (0 filas en el stack de referencia) y
    # ese listado devuelve el tenant ENTERO ordenado por `created_at DESC`, así
    # que un índice por `tenant_id` no le ahorraría ni el sort ni la lectura de
    # casi todas las filas — mientras que sí cobraría escritura en cada emisión,
    # canje y revocación.
    #
    # Si algún día esa tabla crece de verdad, el índice que hace falta NO es el
    # plano sino `(tenant_id, created_at DESC)`, y eso es una migración, no un
    # cambio de modelo.
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=False)

    # Email al que se emitió. El canje EXIGE que coincida con el email que se
    # registra: si no, una invitación filtrada dejaría entrar a cualquiera con
    # el correo que eligiera.
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    # Digest SHA-256 hex (64 chars) del token. Jamás el token.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Prefijo en claro (``<marca>_<id>``) para que el listado distinga
    # invitaciones sin revelarlas.
    token_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    # Rol de la membresía que se creará al canjear (valores de
    # :class:`~api_server.db.models.UserRole`).
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    # Vigencia. NOT NULL a propósito: toda invitación caduca.
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    # Sellada al canjear — a partir de ahí la invitación es inservible.
    redeemed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True, default=None
    )
    # Usuario que resultó del canje (rastro: quién entró con esta invitación).
    redeemed_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    # Revocación explícita del admin; la fila se conserva para auditoría.
    revoked_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True, default=None
    )
    # Admin que la emitió. SET NULL: la invitación sobrevive a su emisor.
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )

    def is_redeemable(self, *, now: datetime | None = None) -> bool:
        """True sii la invitación sigue sirviendo para dar de alta a alguien.

        Los tres motivos de invalidez juntos y en un solo sitio, para que el
        endpoint de canje no pueda olvidarse de uno: ya canjeada (un solo uso),
        revocada, o caducada. La frontera de la caducidad es inclusiva
        (``expires_at <= now`` ya es tarde), igual que en
        :meth:`~api_server.db.models.ApiToken.is_active`.
        """
        moment = now or datetime.now(UTC)
        if self.redeemed_at is not None or self.revoked_at is not None:
            return False
        return self.expires_at > moment

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"UserInvitation(id={self.id!r}, tenant={self.tenant_id!r}, "
            f"email={self.email!r}, prefix={self.token_prefix!r})"
        )


__all__ = ["UserInvitation"]
