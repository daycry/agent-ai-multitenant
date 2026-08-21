"""Sembrar usuarios cuando el registro está cerrado (ADR 0134).

Hasta el 2026-07-31 los tests de integración creaban cuantos usuarios querían
llamando a ``POST /auth/register``. Ya no: ese endpoint solo da de alta al
PRIMER usuario (la puerta de arranque de una instalación) o a quien presente una
invitación válida.

Los tests que solo necesitan «un usuario más» para ejercitar otra cosa —RBAC,
membresías, el singleton del System Owner— no deberían tener que montar el
circuito entero de invitaciones para conseguirlo. Este helper los siembra
directamente en la tabla, con el MISMO hash argon2id que produciría el endpoint,
de modo que el ``POST /auth/login`` posterior funciona exactamente igual.

Se conecta con el DSN de ``migrations_user`` (BYPASSRLS), que es el que los
tests ya usan para preparar su estado.
"""

from __future__ import annotations

import asyncpg
from api_server.auth.passwords import hash_password
from uuid6 import uuid7

DEFAULT_TEST_PASSWORD = "longenoughpw"


async def seed_user(
    dsn: str,
    email: str,
    password: str = DEFAULT_TEST_PASSWORD,
    *,
    full_name: str | None = None,
    is_system_admin: bool = False,
) -> str:
    """Inserta un usuario y devuelve su id como string.

    No toca ``is_system_owner``: es un singleton con índice único parcial
    (ADR 0074) y sembrarlo a ciegas rompería cualquier test que ya tenga uno.
    """
    conn = await asyncpg.connect(dsn)
    try:
        user_id = str(uuid7())
        # IDEMPOTENTE a propósito. La BD de integración es SESSION-SCOPED: la
        # comparten todos los tests de la corrida, así que dos que siembren el
        # mismo email (`root@example.com` lo usan seis de un mismo fichero)
        # chocarían con `users_email_key` — y el fallo aparece en el SEGUNDO test,
        # no en el que tiene el problema, que es la peor forma de descubrirlo.
        #
        # `DO UPDATE` y no `DO NOTHING`: el llamante pide un usuario CON estas
        # credenciales y este flag. Con `DO NOTHING` una segunda siembra
        # devolvería la fila vieja y el login del test fallaría con una
        # contraseña que él mismo acaba de fijar.
        row = await conn.fetchrow(
            "INSERT INTO users (id, email, password_hash, full_name, is_system_admin)"
            " VALUES ($1::uuid, $2, $3, $4, $5)"
            " ON CONFLICT (email) DO UPDATE SET"
            "   password_hash = EXCLUDED.password_hash,"
            "   full_name = EXCLUDED.full_name,"
            "   is_system_admin = EXCLUDED.is_system_admin"
            " RETURNING id",
            user_id,
            email.lower(),
            hash_password(password),
            full_name,
            is_system_admin,
        )
        assert row is not None  # el RETURNING siempre trae fila en INSERT/UPDATE
        return str(row["id"])
    finally:
        await conn.close()


__all__ = ["DEFAULT_TEST_PASSWORD", "seed_user"]
