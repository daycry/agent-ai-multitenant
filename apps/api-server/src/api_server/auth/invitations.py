"""Invitation-token minting + hashing + verification (ADR 0134, opción C).

Con el registro público cerrado, el alta de un usuario nuevo pasa por un token
de invitación que emite un admin y que el invitado presenta en
``POST /auth/register``. Ese token es, por definición, una credencial que viaja
en una petición **no autenticada**: es lo único que distingue a un invitado de
un desconocido.

Por eso sigue exactamente el patrón que ya usan las otras dos credenciales de
ese tipo en el repo —el bearer de SCIM (:mod:`api_server.auth.scim.tokens`) y
el ``X-API-Token`` público (:mod:`api_server.auth.api_tokens`)—:

  * el valor crudo se enseña al admin **una sola vez** y jamás se persiste;
  * en la BD solo vive su digest SHA-256 (``token_hash``) más un ``token_prefix``
    en claro para que el listado distinga invitaciones sin revelarlas;
  * la verificación usa :func:`secrets.compare_digest`.

Por qué SHA-256 y no un argon2 salteado (como en las contraseñas): el canje
llega sin sesión, así que hay que **buscar** la invitación por el valor
presentado (``WHERE token_hash = :digest``), y un hash salteado por fila no se
puede buscar. El digest determinista es seguro aquí precisamente porque el token
es aleatorio de alta entropía (``INVITATION_TOKEN_SECRET_BYTES`` bytes): no hay
fuerza bruta ni rainbow table que valgan.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

# Marca en claro que encabeza todo token de invitación. Permite reconocer el
# valor (y grepearlo de logs o de una filtración) igual que ``ghp_`` / ``sk-``
# en otros sistemas.
INVITATION_TOKEN_PREFIX_MARKER = "aainv"  # agent-ai invitation
# Longitud del id aleatorio en claro que se añade a la marca para formar el
# ``token_prefix`` que se guarda para los listados (p. ej. ``aainv_3f9a2c7b``).
INVITATION_TOKEN_PREFIX_ID_LEN = 8
# Bytes de entropía CSPRNG de la cola secreta, en base64 urlsafe (~43 chars).
# Muy por encima de lo forzable, que es lo que permite que el digest SHA-256
# plano sea la forma at-rest (ver docstring del módulo).
INVITATION_TOKEN_SECRET_BYTES = 32
# Separador entre el prefijo en claro y la cola secreta.
_INVITATION_TOKEN_SEP = "_"

# Vigencia por defecto de una invitación: 7 días. Una invitación SIEMPRE caduca
# (ADR 0134) — sin caducidad el token sería una credencial permanente por
# descuido, y el admin no tendría forma de saber cuántas puertas ha dejado
# abiertas. El emisor puede acortarla; el endpoint acota el máximo.
DEFAULT_INVITATION_TTL_HOURS = 7 * 24
# Techo duro de la vigencia que un admin puede pedir (30 días).
MAX_INVITATION_TTL_HOURS = 30 * 24


@dataclass(frozen=True, slots=True)
class GeneratedInvitationToken:
    """Una invitación recién emitida, en sus tres formas.

    - ``token`` es el valor crudo en claro que se enseña al admin una sola vez
      (para que se lo pase al invitado) y que NUNCA se persiste. Con forma
      ``<prefix>_<secret>``.
    - ``prefix`` es el id inicial en claro (``<marca>_<id>``) que sí se guarda
      para los listados; no es secreto.
    - ``token_hash`` es el digest SHA-256 de ``token`` — la única forma que
      llega a la BD.
    """

    token: str
    prefix: str
    token_hash: str


def generate_invitation_token() -> GeneratedInvitationToken:
    """Emite un token de invitación fresco y sus formas at-rest."""
    prefix_id = secrets.token_hex(INVITATION_TOKEN_PREFIX_ID_LEN // 2)
    prefix = f"{INVITATION_TOKEN_PREFIX_MARKER}{_INVITATION_TOKEN_SEP}{prefix_id}"
    secret = secrets.token_urlsafe(INVITATION_TOKEN_SECRET_BYTES)
    token = f"{prefix}{_INVITATION_TOKEN_SEP}{secret}"
    return GeneratedInvitationToken(
        token=token,
        prefix=prefix,
        token_hash=hash_invitation_token(token),
    )


def hash_invitation_token(token: str) -> str:
    """Digest SHA-256 hex de ``token`` (la forma at-rest).

    Determinista para que el canje —que llega sin sesión— se resuelva con una
    búsqueda por igualdad sobre ``user_invitations.token_hash``.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def invitation_prefix_of(token: str) -> str:
    """Recupera el ``prefix`` en claro (``<marca>_<id>``) de un token crudo.

    Cae de vuelta al valor entero si el token no tiene la forma esperada, para
    que un valor basura no reviente el listado.
    """
    parts = token.split(_INVITATION_TOKEN_SEP)
    if len(parts) >= 3 and parts[0] == INVITATION_TOKEN_PREFIX_MARKER:
        return f"{parts[0]}{_INVITATION_TOKEN_SEP}{parts[1]}"
    return token


def verify_invitation_token(presented: str, token_hash: str) -> bool:
    """Comprobación de tiempo constante de que ``presented`` hashea a
    ``token_hash``.

    Usa :func:`secrets.compare_digest` para que el tiempo no filtre cuántos
    caracteres iniciales del digest coincidían.
    """
    return secrets.compare_digest(hash_invitation_token(presented), token_hash)


__all__ = [
    "DEFAULT_INVITATION_TTL_HOURS",
    "INVITATION_TOKEN_PREFIX_ID_LEN",
    "INVITATION_TOKEN_PREFIX_MARKER",
    "INVITATION_TOKEN_SECRET_BYTES",
    "MAX_INVITATION_TTL_HOURS",
    "GeneratedInvitationToken",
    "generate_invitation_token",
    "hash_invitation_token",
    "invitation_prefix_of",
    "verify_invitation_token",
]
