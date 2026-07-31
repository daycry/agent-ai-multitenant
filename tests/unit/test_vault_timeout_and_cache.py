"""prod-13 · task_prod13_03 — Vault fuera del event loop, con timeout y caché.

El hallazgo perf-7 nombraba DOS caminos, no uno:

  * el del panel (`routers/llm_providers.py`), que ya pasa `timeout` al
    `hvac.Client` y envuelve las cuatro llamadas al store en `to_thread`;
  * y el del **chat del asistente**, que entra por
    `llm_providers/factory.build_llm_provider` y seguía llamando a
    `vault.read_secret()` SÍNCRONO dentro del handler async — un Vault lento
    congelaba el bucle de eventos entero — y sin caché, así que iba a Vault en
    CADA mensaje.

Los tests de aquí miden lo que de verdad importa en cada mitad:

  * que la lectura NO ocurre en el hilo del event loop (thread ident distinto):
    es la única aserción que no se puede falsear desde fuera;
  * que la segunda construcción del mismo `provider_id` no vuelve a Vault;
  * que la invalidación explícita (el gancho que prod-05 usará al rotar) y el
    vencimiento del TTL sí vuelven;
  * que dos proveedores distintos NO comparten credencial — una caché de
    secretos que se cruza es mucho peor que no tener caché.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from uuid import uuid4

import pytest


class _Row:
    """Fila mínima de `llm_providers` que `build_llm_provider` necesita."""

    def __init__(self, *, provider_id: Any, kind: str = "ollama") -> None:
        self.id = provider_id
        self.kind = kind
        self.base_url = "http://ollama.invalid:11434"
        self.secret_vault_path = f"platform/llm/{provider_id}"
        self.is_active = True


class _RecordingStore:
    """Doble del store de Vault que registra CUÁNTAS lecturas y EN QUÉ HILO."""

    def __init__(self, secret: dict[str, str]) -> None:
        self.secret = dict(secret)
        self.reads = 0
        self.threads: list[int] = []
        self.paths: list[str] = []

    def write_secret(self, path: str, secret: dict[str, str]) -> None:  # pragma: no cover
        self.secret = dict(secret)

    def read_secret(self, path: str) -> dict[str, str]:
        self.reads += 1
        self.threads.append(threading.get_ident())
        self.paths.append(path)
        return dict(self.secret)

    def delete_secret(self, path: str) -> None:  # pragma: no cover
        self.secret = {}


@pytest.fixture()
def factory_mod(monkeypatch):
    """El módulo de la factoría con la caché limpia y sin BD detrás."""
    from api_server.llm_providers import factory

    rows: dict[Any, _Row] = {}

    async def _get_llm_provider(_session: Any, provider_id: Any) -> _Row | None:
        return rows.get(provider_id)

    monkeypatch.setattr(factory, "get_llm_provider", _get_llm_provider)
    # Los cuatro builders reales importan SDK opcionales; aquí solo interesa
    # QUÉ secreto les llega, no qué cliente sale.
    seen: list[dict[str, str]] = []

    def _build(kind: str, *, base_url: Any, secret: dict[str, str], model: str) -> object:
        seen.append(dict(secret))
        return object()

    monkeypatch.setattr(factory, "build_provider_from_kind", _build)
    factory.clear_provider_secret_cache()
    try:
        yield factory, rows, seen
    finally:
        factory.clear_provider_secret_cache()


@pytest.mark.asyncio
async def test_vault_read_happens_off_the_event_loop_thread(factory_mod) -> None:
    """La lectura de Vault es una llamada HTTP SÍNCRONA (hvac va sobre requests).
    Hacerla en el hilo del loop congela el api-server entero mientras Vault
    tarde. Comparar el thread ident es la comprobación que no admite trampa."""
    factory, rows, _seen = factory_mod
    provider_id = uuid4()
    rows[provider_id] = _Row(provider_id=provider_id)
    store = _RecordingStore({"bearer_token": "t0"})

    loop_thread = threading.get_ident()
    built = await factory.build_llm_provider(
        object(), provider_id=provider_id, model="m", vault=store
    )

    assert built is not None
    assert store.reads == 1
    assert store.threads[0] != loop_thread, (
        "vault.read_secret corrió en el hilo del event loop: un Vault lento "
        "vuelve a congelar el api-server (perf-7)"
    )


@pytest.mark.asyncio
async def test_second_build_of_the_same_provider_does_not_hit_vault_again(factory_mod) -> None:
    """El chat del asistente construye el proveedor en CADA mensaje. Sin caché,
    cada mensaje era un round-trip a Vault."""
    factory, rows, seen = factory_mod
    provider_id = uuid4()
    rows[provider_id] = _Row(provider_id=provider_id)
    store = _RecordingStore({"bearer_token": "t0"})

    for _ in range(3):
        await factory.build_llm_provider(object(), provider_id=provider_id, model="m", vault=store)

    assert store.reads == 1, f"se fue a Vault {store.reads} veces; la caché no sirvió"
    assert [s["bearer_token"] for s in seen] == ["t0", "t0", "t0"]


@pytest.mark.asyncio
async def test_two_providers_never_share_a_cached_credential(factory_mod) -> None:
    """Una caché de secretos cruzada es peor que no tener caché: mandaría la
    credencial de un proveedor al endpoint de otro."""
    factory, rows, seen = factory_mod
    first, second = uuid4(), uuid4()
    rows[first] = _Row(provider_id=first)
    rows[second] = _Row(provider_id=second)
    store_a = _RecordingStore({"bearer_token": "secreto-A"})
    store_b = _RecordingStore({"bearer_token": "secreto-B"})

    await factory.build_llm_provider(object(), provider_id=first, model="m", vault=store_a)
    await factory.build_llm_provider(object(), provider_id=second, model="m", vault=store_b)

    assert [s["bearer_token"] for s in seen] == ["secreto-A", "secreto-B"]
    assert store_a.reads == 1 and store_b.reads == 1


@pytest.mark.asyncio
async def test_explicit_invalidation_forces_a_fresh_read(factory_mod) -> None:
    """El gancho que prod-05 (rotación de credenciales) necesita: tras rotar, la
    siguiente construcción tiene que traer la credencial NUEVA, no la vieja."""
    factory, rows, seen = factory_mod
    provider_id = uuid4()
    rows[provider_id] = _Row(provider_id=provider_id)
    store = _RecordingStore({"bearer_token": "vieja"})

    await factory.build_llm_provider(object(), provider_id=provider_id, model="m", vault=store)
    store.secret = {"bearer_token": "rotada"}
    factory.invalidate_provider_secret_cache(provider_id)
    await factory.build_llm_provider(object(), provider_id=provider_id, model="m", vault=store)

    assert store.reads == 2
    assert [s["bearer_token"] for s in seen] == ["vieja", "rotada"]


@pytest.mark.asyncio
async def test_the_entry_expires_so_a_missed_invalidation_is_bounded(factory_mod, monkeypatch):
    """El TTL es el techo de lo rancia que puede quedarse una credencial si
    alguien olvida invalidar. Tiene que estar dentro de los 30-60 s del plan."""
    factory, rows, seen = factory_mod
    provider_id = uuid4()
    rows[provider_id] = _Row(provider_id=provider_id)
    store = _RecordingStore({"bearer_token": "vieja"})

    ttl = factory.PROVIDER_SECRET_CACHE_TTL_SECONDS
    assert 30 <= ttl <= 60, f"TTL fuera del rango que pide el plan: {ttl}"

    clock = {"now": 1000.0}
    monkeypatch.setattr(factory, "_monotonic", lambda: clock["now"])

    await factory.build_llm_provider(object(), provider_id=provider_id, model="m", vault=store)
    clock["now"] += ttl - 1
    await factory.build_llm_provider(object(), provider_id=provider_id, model="m", vault=store)
    assert store.reads == 1, "la entrada venció antes de tiempo"

    store.secret = {"bearer_token": "rotada"}
    clock["now"] += 2
    await factory.build_llm_provider(object(), provider_id=provider_id, model="m", vault=store)
    assert store.reads == 2, "la entrada NUNCA vence: una credencial rotada no entraría jamás"
    assert seen[-1]["bearer_token"] == "rotada"


@pytest.mark.asyncio
async def test_a_vault_error_is_not_cached_as_an_empty_credential(factory_mod) -> None:
    """Degradar a "sin credencial" es correcto para NO tumbar el chat, pero
    cachear ese fallo convertiría un parpadeo de Vault en 30 s de proveedor sin
    autenticar."""
    from api_server.llm_providers.vault import LLMProviderVaultError

    factory, rows, seen = factory_mod
    provider_id = uuid4()
    rows[provider_id] = _Row(provider_id=provider_id)

    class _FlakyStore(_RecordingStore):
        def read_secret(self, path: str) -> dict[str, str]:
            self.reads += 1
            self.threads.append(threading.get_ident())
            if self.reads == 1:
                raise LLMProviderVaultError("vault down")
            return dict(self.secret)

    store = _FlakyStore({"bearer_token": "buena"})

    await factory.build_llm_provider(object(), provider_id=provider_id, model="m", vault=store)
    assert seen[0] == {}
    await factory.build_llm_provider(object(), provider_id=provider_id, model="m", vault=store)
    assert store.reads == 2, "el fallo de Vault quedó cacheado como credencial vacía"
    assert seen[1] == {"bearer_token": "buena"}


@pytest.mark.asyncio
async def test_a_provider_without_vault_pointer_never_touches_the_cache(factory_mod) -> None:
    """Sin `secret_vault_path` no hay nada que leer ni que cachear."""
    factory, rows, seen = factory_mod
    provider_id = uuid4()
    row = _Row(provider_id=provider_id)
    row.secret_vault_path = None
    rows[provider_id] = row

    await factory.build_llm_provider(object(), provider_id=provider_id, model="m", vault=None)
    assert seen == [{}]


# ---------------------------------------------------------------------------
# La otra mitad de perf-7: el `timeout` del cliente hvac del panel
# ---------------------------------------------------------------------------
def test_the_hvac_client_is_built_with_an_explicit_timeout(monkeypatch) -> None:
    """`hvac` va sobre `requests`, que SIN `timeout` espera indefinidamente. Se
    inyecta un módulo `hvac` falso para observar los kwargs reales del
    constructor en vez de leer el fuente."""
    import sys
    import types

    calls: list[dict[str, Any]] = []

    fake_hvac = types.ModuleType("hvac")

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            calls.append(kwargs)

    fake_hvac.Client = _Client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hvac", fake_hvac)
    monkeypatch.setenv("API_SERVER_VAULT_TOKEN", "s.testtoken")

    from api_server.config import get_settings
    from api_server.routers import llm_providers as router_mod

    get_settings.cache_clear()
    router_mod.reset_provider_vault_store_cache()
    try:
        store = router_mod.get_provider_vault_store()
    finally:
        router_mod.reset_provider_vault_store_cache()
        get_settings.cache_clear()

    assert store is not None
    assert len(calls) == 1, "no se construyó el cliente hvac; el test no probó nada"
    timeout = calls[0].get("timeout")
    assert timeout is not None, "hvac.Client sin timeout: un Vault colgado cuelga el api-server"
    assert 0 < float(timeout) <= 15


def test_the_router_reads_and_writes_vault_off_the_event_loop() -> None:
    """Guarda de no-regresión de la mitad ya hecha: las cuatro llamadas al store
    en el router van por `asyncio.to_thread`. Se cuenta para que la guarda no
    pase vacía el día que alguien renombre los métodos."""
    from pathlib import Path

    source = Path("apps/api-server/src/api_server/routers/llm_providers.py").read_text(
        encoding="utf-8"
    )
    wrapped = source.count("asyncio.to_thread(store.")
    assert wrapped >= 4, f"solo {wrapped} llamadas al store de Vault van por to_thread"
    for method in ("write_secret", "read_secret", "delete_secret"):
        bare = f"store.{method}("
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith(bare):
                raise AssertionError(f"llamada síncrona a {bare} en el router: {stripped}")


def test_asyncio_is_imported_where_the_factory_uses_it() -> None:
    """Aserción tonta a propósito: `asyncio.to_thread` con `asyncio` sin importar
    es un NameError que solo aparece con Vault configurado."""
    from api_server.llm_providers import factory

    assert factory.asyncio is asyncio
