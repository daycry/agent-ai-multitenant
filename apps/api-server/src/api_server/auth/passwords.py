"""Argon2id password hashing.

Conservative defaults (time_cost=3, memory_cost=64 MiB, parallelism=4)
that fit the OWASP 2023 recommendation. Tunable per-instance via a
PasswordHasher constructor if a host is too slow.
"""

from __future__ import annotations

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MiB
    parallelism=4,
    type=Type.ID,
)


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


def needs_rehash(hashed: str) -> bool:
    """True when the stored hash uses weaker parameters than the
    current `_hasher`. Call after a successful verify to seamlessly
    upgrade rows in the background."""
    result: bool = _hasher.check_needs_rehash(hashed)
    return result
