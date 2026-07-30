"""El índice inverso usuario→sesiones de :class:`SessionStore` (prod-09, authz-4).

## El defecto

``SessionStore.create`` solo indexaba la sesión cuando ``tenant_id is not None``:

```python
if tenant_id is not None:
    await sadd(f"user-sessions:{tenant_id}:{user_id}", str(sid))
```

Y **toda** sesión nace SIN tenant. No es un caso raro de un System Admin: es el
camino único. ``POST /auth/login`` (y el callback SSO) mintean lo que su propio
docstring llama «tenant-less IDENTITY session» — ``routers/auth.py`` llama a
``sessions.create(..., tenant_id=None, ...)`` — y solo DESPUÉS
``/auth/session/resolve`` o ``/auth/session/select-tenant`` mintean una segunda
sesión, esta sí con tenant, **sin revocar la primera**. Resultado: cada login
deja una sesión viva que no aparece en NINGÚN índice y a la que
``revoke_user_sessions`` no puede llegar de ninguna manera.

Para un usuario de tenant eso es un token de identidad huérfano durante 24 h. Para
un **System Admin es la credencial más privilegiada del sistema**: su sesión es
tenant-less POR DISEÑO (``resolve_session`` devuelve ``state="admin"`` y entra en
la vista de cartera con el token de identidad), y con ella la cabecera
``X-Tenant-Id`` le da acceso cross-tenant a cualquier tenant. Justo la sesión que
una baja tiene que poder cortar, y justo la que no se podía cortar.

## Por qué estos tests y no otros

- El índice per-tenant se mantiene INTACTO: SCIM revoca «las sesiones de este
  usuario EN ESTE tenant» (`routers/scim.py`), no las de un usuario que sigue
  siendo miembro legítimo de otros. Hay un test que fija ese límite, porque
  «arreglar» esto revocando de más sería un fallo peor y silencioso.
- El caso que faltaba se cubre con un índice GLOBAL por ``user_id`` y un método
  explícito (``revoke_all_user_sessions``), no sobrecargando el existente con un
  ``tenant_id=None`` implícito: quien lee la llamada tiene que ver el alcance.

Contra Redis de verdad, no un doble: el defecto ERA la disposición de las claves.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from uuid6 import uuid7

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture()
async def store(test_redis_url: str) -> AsyncIterator[Any]:
    """A :class:`SessionStore` on the test Redis DB, flushed before and after."""
    from api_server.auth.sessions import SessionStore

    client: Redis = Redis.from_url(test_redis_url, decode_responses=True)
    await client.flushdb()
    try:
        yield SessionStore(client)
    finally:
        await client.flushdb()
        await client.aclose()


# ---------------------------------------------------------------------------
# El defecto: la sesión SIN tenant era inalcanzable
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_tenantless_session_can_be_revoked(store: Any) -> None:
    """La sesión de identidad (la que crea TODO login) muere al revocar el usuario.

    Antes del arreglo esto era imposible: la sesión no estaba en ningún índice, así
    que no había forma de encontrarla a partir del ``user_id``.
    """
    user_id = uuid4()
    sid = uuid7()
    await store.create(sid, user_id=user_id, tenant_id=None, ttl_seconds=600)
    assert await store.get(sid) is not None, "la sesión no se creó; el test no probaría nada"

    revoked = await store.revoke_all_user_sessions(user_id)

    assert revoked == 1, f"se esperaba 1 sesión revocada, se revocaron {revoked}"
    assert await store.get(sid) is None, "la sesión sin tenant sobrevivió a la revocación"


@pytest.mark.asyncio
async def test_revoke_all_covers_tenantless_and_tenant_scoped_together(store: Any) -> None:
    """El caso REAL de un login: identidad tenant-less + sesión con tenant.

    Las dos son del mismo usuario y las dos tienen que morir en una baja. Con solo
    el índice per-tenant moría una de las dos, que es exactamente el agujero.
    """
    user_id = uuid4()
    tenant_id = uuid4()
    identity_sid = uuid7()
    tenant_sid = uuid7()
    await store.create(identity_sid, user_id=user_id, tenant_id=None, ttl_seconds=600)
    await store.create(tenant_sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=600)

    revoked = await store.revoke_all_user_sessions(user_id)

    assert revoked == 2, f"se esperaban 2 sesiones revocadas, {revoked}"
    assert await store.get(identity_sid) is None
    assert await store.get(tenant_sid) is None


@pytest.mark.asyncio
async def test_revoke_all_does_not_touch_another_user(store: Any) -> None:
    """Contra-prueba de que el índice global está keyed por usuario y no es un
    ``flushdb`` disfrazado: la sesión de otro usuario sobrevive."""
    victim, bystander = uuid4(), uuid4()
    victim_sid, bystander_sid = uuid7(), uuid7()
    await store.create(victim_sid, user_id=victim, tenant_id=None, ttl_seconds=600)
    await store.create(bystander_sid, user_id=bystander, tenant_id=None, ttl_seconds=600)

    revoked = await store.revoke_all_user_sessions(victim)

    assert revoked == 1
    assert await store.get(victim_sid) is None
    assert await store.get(bystander_sid) is not None, "se revocó la sesión de otro usuario"


# ---------------------------------------------------------------------------
# El límite que NO se debe cruzar: SCIM sigue siendo per-tenant
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tenant_scoped_revocation_stays_scoped_to_its_tenant(store: Any) -> None:
    """``revoke_user_sessions(user, tenant_a)`` no toca la sesión del MISMO usuario
    en el tenant B ni su sesión de identidad.

    SCIM ``active=false`` significa «este usuario deja de tener acceso a ESTE
    tenant»; el usuario puede seguir siendo miembro legítimo de otro. Este test
    existe para que el arreglo del hueco no se convierta en una revocación de más.
    """
    user_id = uuid4()
    tenant_a, tenant_b = uuid4(), uuid4()
    sid_a, sid_b, sid_identity = uuid7(), uuid7(), uuid7()
    await store.create(sid_a, user_id=user_id, tenant_id=tenant_a, ttl_seconds=600)
    await store.create(sid_b, user_id=user_id, tenant_id=tenant_b, ttl_seconds=600)
    await store.create(sid_identity, user_id=user_id, tenant_id=None, ttl_seconds=600)

    revoked = await store.revoke_user_sessions(user_id, tenant_a)

    assert revoked == 1, f"la revocación per-tenant alcanzó {revoked} sesiones, no 1"
    assert await store.get(sid_a) is None
    assert await store.get(sid_b) is not None, "se revocó la sesión del otro tenant"
    assert await store.get(sid_identity) is not None, "se revocó la sesión de identidad"


# ---------------------------------------------------------------------------
# Higiene de los índices
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_revoking_one_session_removes_it_from_the_global_index(store: Any) -> None:
    """Un logout (``revoke``) saca el sid del índice global, no solo del per-tenant.

    Sin esto el índice global acumularía sids muertos y ``revoke_all_user_sessions``
    devolvería un recuento inflado — el número que un auditor leería como
    «sesiones que estaban vivas».
    """
    user_id = uuid4()
    tenant_id = uuid4()
    sid = uuid7()
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=600)

    await store.revoke(sid)

    assert await store.get(sid) is None
    assert (
        await store.revoke_all_user_sessions(user_id) == 0
    ), "el sid revocado seguía en el índice global y se contó como sesión viva"


@pytest.mark.asyncio
async def test_tenant_revocation_clears_the_global_index_too(store: Any) -> None:
    """Tras una revocación per-tenant, el índice global no conserva esos sids."""
    user_id = uuid4()
    tenant_id = uuid4()
    sid = uuid7()
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=600)

    assert await store.revoke_user_sessions(user_id, tenant_id) == 1
    assert (
        await store.revoke_all_user_sessions(user_id) == 0
    ), "el índice global quedó con el sid que la revocación per-tenant ya mató"


@pytest.mark.asyncio
async def test_the_global_index_expires_with_the_sessions_it_tracks(store: Any) -> None:
    """El índice lleva TTL: no puede quedarse en Redis para siempre apuntando a
    sesiones caducadas (fuga de memoria + recuentos mentirosos)."""
    from api_server.auth.sessions import _user_all_index_key

    user_id = uuid4()
    await store.create(uuid7(), user_id=user_id, tenant_id=None, ttl_seconds=600)

    ttl = await store._redis.ttl(_user_all_index_key(user_id))

    assert 0 < ttl <= 600, f"el índice global no tiene TTL acotado (ttl={ttl})"


@pytest.mark.asyncio
async def test_a_stale_sid_in_the_index_is_not_counted_as_revoked(store: Any) -> None:
    """Un sid que ya caducó por TTL cuenta 0, no 1.

    El recuento que devuelve el método es lo que un llamante audita («se cortaron N
    sesiones»); contar sids fantasma lo volvería inútil.
    """
    from api_server.auth.sessions import _key, _user_all_index_key

    user_id = uuid4()
    live_sid, ghost_sid = uuid7(), uuid7()
    await store.create(live_sid, user_id=user_id, tenant_id=None, ttl_seconds=600)
    await store.create(ghost_sid, user_id=user_id, tenant_id=None, ttl_seconds=600)
    # Simula la caducidad por TTL de una sola sesión: la clave desaparece pero el
    # sid sigue en el índice (el índice es metadato best-effort, no la verdad).
    await store._redis.delete(_key(ghost_sid))

    revoked = await store.revoke_all_user_sessions(user_id)

    assert revoked == 1, f"se contaron {revoked} sesiones revocadas contando el fantasma"
    assert await store._redis.exists(_user_all_index_key(user_id)) == 0


@pytest.mark.asyncio
async def test_revoke_all_on_a_user_with_no_sessions_is_zero(store: Any) -> None:
    """Idempotente y sin excepciones: revocar a quien no tiene sesiones da 0."""
    assert await store.revoke_all_user_sessions(uuid4()) == 0


# ---------------------------------------------------------------------------
# Guarda de que el índice global existe de verdad (y no pasa vacío)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_every_created_session_lands_in_the_global_index(store: Any) -> None:
    """El invariante del arreglo: TODA sesión se indexa, con tenant o sin él.

    Escrito sobre las claves de Redis a propósito. Los tests de comportamiento de
    arriba pasarían igual con una implementación que escaneara ``session:*`` — que
    sería O(claves) y afectaría a todos los usuarios. Esto fija que existe el
    índice inverso y que lo alimenta ``create``.
    """
    from api_server.auth.sessions import _user_all_index_key

    user_id = uuid4()
    expected = set()
    for tenant in (None, uuid4()):
        sid = uuid7()
        expected.add(str(sid))
        await store.create(sid, user_id=user_id, tenant_id=tenant, ttl_seconds=600)

    indexed = await store._redis.smembers(_user_all_index_key(user_id))

    assert len(expected) == 2, "la guarda dejó de crear las dos formas de sesión"
    assert set(indexed) == expected, f"el índice global no contiene {expected}: {indexed}"


@pytest.mark.asyncio
async def test_uuid_of_a_revoked_session_survives_a_missing_payload(store: Any) -> None:
    """``revoke`` sobre un sid inexistente no explota (logout doble, sid caducado)."""
    await store.revoke(uuid7())  # no debe lanzar


@pytest.mark.asyncio
async def test_payload_still_carries_user_tenant_and_created_at(store: Any) -> None:
    """Regresión: el arreglo no cambia el payload que leen ``get_principal`` y la
    puerta de admin-hardening (``created_at`` acota la edad de sesión en /admin)."""
    user_id, tenant_id = uuid4(), uuid4()
    sid = uuid7()
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=600)

    payload = await store.get(sid)

    assert payload is not None
    assert payload["user_id"] == str(user_id)
    assert payload["tenant_id"] == str(tenant_id)
    assert isinstance(payload["created_at"], int)
    assert UUID(str(payload["user_id"])) == user_id
