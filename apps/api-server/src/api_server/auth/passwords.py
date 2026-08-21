"""Argon2id password hashing.

Conservative defaults (time_cost=3, memory_cost=64 MiB, parallelism=4)
that fit the OWASP 2023 recommendation. Tunable per-instance via a
PasswordHasher constructor if a host is too slow.
"""

from __future__ import annotations

from contextlib import suppress
from functools import lru_cache

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MiB
    parallelism=4,
    type=Type.ID,
)

# Texto fijo del que se deriva el hash de relleno. No es un secreto y no
# autentica a nadie: ninguna fila de `users` lo lleva, y
# `burn_password_verification` devuelve None aunque el llamante lo acierte.
_DUMMY_PASSWORD = "argon2-dummy-verification-target"


def hash_password(plain: str) -> str:
    """Return the encoded argon2id hash of `plain`."""
    encoded: str = _hasher.hash(plain)
    return encoded


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time verification. Returns False on mismatch; raises
    ValueError only when `hashed` is not a valid argon2 encoding."""
    try:
        _hasher.verify(hashed, plain)
    except VerifyMismatchError:
        return False
    except InvalidHashError as exc:
        raise ValueError("stored password is not a valid argon2 hash") from exc
    return True


@lru_cache(maxsize=1)
def dummy_password_hash() -> str:
    """Hash argon2id de relleno, derivado con el hasher VIVO.

    Se calcula perezosamente (no en el import: son decenas de milisegundos en
    el arranque de cada proceso) y se memoiza. Que salga de `_hasher` y no de
    una constante escrita a mano es deliberado: si alguien sube el
    `memory_cost`, el relleno sube con él y sigue costando lo mismo que una
    verificación de verdad. Un hash fijo se quedaría atrás y reabriría el
    canal lateral en silencio.
    """
    encoded: str = _hasher.hash(_DUMMY_PASSWORD)
    return encoded


def burn_password_verification(plain: str) -> None:
    """Gasta el mismo trabajo argon2 que una verificación real y no devuelve nada.

    Es la rama de relleno del login: cuando el email no existe, el usuario
    está inactivo o la identidad es de SSO no hay hash contra el que
    comparar, y salir por ahí sin quemar el tiempo convierte la latencia de
    `/auth/login` en un oráculo de enumeración de usuarios.

    Devuelve `None` a propósito —no `False`— para que ningún llamante pueda
    tratarla como una verificación cuyo resultado se pueda invertir por
    error. No autentica nunca, ni siquiera si `plain` coincide con el texto
    del relleno.
    """
    with suppress(VerifyMismatchError):
        _hasher.verify(dummy_password_hash(), plain)


def needs_rehash(hashed: str) -> bool:
    """True when the stored hash uses weaker parameters than the
    current `_hasher`. Call after a successful verify to seamlessly
    upgrade rows in the background."""
    result: bool = _hasher.check_needs_rehash(hashed)
    return result
