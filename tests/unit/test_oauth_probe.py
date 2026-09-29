"""La sonda «¿conectado el OAuth?» del Resumen y las Capacidades.

Tres estados (conectado / no conectado / desconocido), fuera del event loop, cacheada 30 s
y sin que ninguna excepción de Vault tumbe el endpoint (revisión adversarial, intento 1).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from uuid import uuid4

import pytest
from api_server.mcp import oauth_probe
from shared_mcp import MCPAuthError, StaticVaultResolver, oauth_vault_path

pytestmark = pytest.mark.asyncio

TENANT, PROJECT = uuid4(), uuid4()


def _path(name: str) -> str:
    return oauth_vault_path(tenant_id=str(TENANT), project_id=str(PROJECT), server_name=name)


def _token(access: str = "abc") -> dict[str, str]:
    return {"oauth_token": json.dumps({"access_token": access, "token_type": "Bearer"})}


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    oauth_probe.clear_oauth_probe_cache()


class _Down:
    """Vault sellado: hvac lanza y el resolver lo envuelve en MCPAuthError."""

    def resolve(self, auth_ref: str) -> dict[str, str]:
        raise MCPAuthError("Vault read failed") from RuntimeError("Vault is sealed")

    def write(self, auth_ref: str, values: dict[str, str]) -> None: ...


async def test_connected_not_connected_and_unknown() -> None:
    ok = StaticVaultResolver({_path("a"): _token()})
    assert await oauth_probe.oauth_connected_probe(ok, TENANT, PROJECT)("a") is True
    assert await oauth_probe.oauth_connected_probe(ok, TENANT, PROJECT)("missing") is False
    assert await oauth_probe.oauth_connected_probe(_Down(), TENANT, PROJECT)("z") is None


async def test_no_resolver_is_not_connected() -> None:
    assert await oauth_probe.oauth_connected_probe(None, TENANT, PROJECT)("a") is False


async def test_corrupt_entry_is_unknown_and_does_not_raise() -> None:
    bad = StaticVaultResolver({_path("a"): {"oauth_token": "{not json"}})
    assert await oauth_probe.oauth_connected_probe(bad, TENANT, PROJECT)("a") is None
    shape = StaticVaultResolver({_path("b"): {"oauth_token": json.dumps({"scope": "x"})}})
    assert await oauth_probe.oauth_connected_probe(shape, TENANT, PROJECT)("b") is None


async def test_vault_read_leaves_the_event_loop_and_is_cached() -> None:
    seen: list[int] = []

    class Spy(StaticVaultResolver):
        def resolve(self, auth_ref: str) -> dict[str, str]:
            seen.append(threading.get_ident())
            return super().resolve(auth_ref)

    spy = Spy({_path("a"): _token()})
    probe = oauth_probe.oauth_connected_probe(spy, TENANT, PROJECT)
    assert await probe("a") is True
    assert await probe("a") is True
    assert len(seen) == 1, "la segunda lectura sale de la caché de 30 s"
    assert seen[0] != threading.get_ident(), "hvac es síncrono: fuera del hilo del loop"


async def test_unknown_is_not_cached() -> None:
    calls = 0

    class Flaky(StaticVaultResolver):
        def resolve(self, auth_ref: str) -> dict[str, str]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise MCPAuthError("boom") from RuntimeError("down")
            return super().resolve(auth_ref)

    probe = oauth_probe.oauth_connected_probe(Flaky({_path("a"): _token()}), TENANT, PROJECT)
    assert await probe("a") is None
    assert await probe("a") is True


async def test_probe_many_runs_in_parallel() -> None:
    class Slow(StaticVaultResolver):
        def resolve(self, auth_ref: str) -> dict[str, str]:
            time.sleep(0.3)
            return super().resolve(auth_ref)

    names = ["a", "b", "c", "d"]
    probe = oauth_probe.oauth_connected_probe(
        Slow({_path(n): _token() for n in names}), TENANT, PROJECT
    )
    started = time.monotonic()
    result = await oauth_probe.probe_many(probe, names)
    assert result == dict.fromkeys(names, True)
    assert time.monotonic() - started < 0.9, "en serie serían 1.2 s"
    assert asyncio.get_running_loop() is not None


async def test_invalidate_drops_only_that_server_from_the_cache() -> None:
    """«Conectar»/desconectar cambian el estado: la caché de 30 s no puede seguir
    diciendo lo de antes (revisión adversarial, intento 2)."""
    data = {_path("a"): _token(), _path("b"): _token()}
    resolver = StaticVaultResolver(data)
    probe = oauth_probe.oauth_connected_probe(resolver, TENANT, PROJECT)
    assert await probe("a") is True
    assert await probe("b") is True
    del data[_path("a")]
    del data[_path("b")]
    assert await probe("a") is True, "sin invalidar, la caché sigue"
    oauth_probe.invalidate_oauth_probe(TENANT, PROJECT, "a")
    assert await probe("a") is False
    assert await probe("b") is True, "otro servidor: su entrada no se toca"
    # Otro proyecto u otro tenant tampoco.
    oauth_probe.invalidate_oauth_probe(uuid4(), PROJECT, "b")
    oauth_probe.invalidate_oauth_probe(TENANT, uuid4(), "b")
    assert await probe("b") is True
