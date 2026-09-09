"""Las tools de un servidor MCP llegan al catálogo sin un paso manual (`task_mk_01`, ADR 0166).

Extiende `test_mcp_tool_import_and_threading.py` (cuyos fixtures reutiliza) con lo
que el ADR 0166 cambia en el contrato del import:

- D2: `tool_names` opcional ⇒ todas las anunciadas, en un viaje.
- R2: un re-import sin `security_level` NO pisa la elección del operador.
- R3: lo que el servidor deja de anunciar se retira (soft) — sólo sin selección
  explícita y con auditoría.
- R4: un servidor que sale del proyecto se lleva sus filas, salvo que otro
  proyecto vivo del tenant declare el mismo nombre.
- L1: por encima de 200 tools el automatismo se abstiene con `TOO_MANY_TOOLS`.
- D8: discovery caído ⇒ cero filas (fail-closed, sin cambios).
"""

# ruff: noqa: F811 - los fixtures importados se re-vinculan como parámetros, que es como pytest los inyecta
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.test_mcp_tool_import_and_threading import (  # noqa: F401 - fixtures
    _mint_token,
    _seed,
    configured_app,
)

pytestmark = [pytest.mark.integration]

_URL = "/projects/{pid}/mcp/servers/filesystem/import-tools"


def _patch(monkeypatch: pytest.MonkeyPatch, names: list[str]) -> None:
    from shared_mcp.discovery import DiscoveryResult
    from shared_mcp.types import MCPTool

    async def _discover(config, *, vault_resolver=None, **_kw):
        return DiscoveryResult(
            tools=[
                MCPTool(name=n, description=f"{n} desc", input_schema={"type": "object"})
                for n in names
            ],
            server_name="filesystem",
        )

    monkeypatch.setattr("api_server.mcp.import_tools.discover_tools", _discover)
    monkeypatch.setattr("api_server.routers.mcp.discover_tools", _discover)


async def _client(app, seeded):
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_sin_tool_names_entran_todas_las_anunciadas(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    _patch(monkeypatch, ["read_file", "write_file", "list_dir"])
    async with await _client(configured_app, seeded) as client:
        resp = await client.post(_URL.format(pid=seeded["project_a"]), json={})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert {t["name"] for t in body["tools"]} == {
            "filesystem.read_file",
            "filesystem.write_file",
            "filesystem.list_dir",
        }
        assert body["retired"] == [] and body["omitted"] == []
        assert all(t["security_level"] == "sandboxed" for t in body["tools"])


@pytest.mark.asyncio
async def test_un_reimport_sin_security_level_respeta_la_eleccion_del_operador(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    _patch(monkeypatch, ["read_file"])
    url = _URL.format(pid=seeded["project_a"])
    async with await _client(configured_app, seeded) as client:
        manual = await client.post(
            url, json={"tool_names": ["read_file"], "security_level": "privileged"}
        )
        assert manual.status_code == 200, manual.text
        assert manual.json()["tools"][0]["security_level"] == "privileged"

        auto = await client.post(url, json={})
        assert auto.status_code == 200, auto.text
        assert auto.json()["tools"][0]["security_level"] == "privileged"


@pytest.mark.asyncio
async def test_r3_lo_que_el_servidor_deja_de_anunciar_se_retira_solo_sin_seleccion(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    url = _URL.format(pid=seeded["project_a"])
    async with await _client(configured_app, seeded) as client:
        _patch(monkeypatch, ["read_file", "write_file"])
        assert (await client.post(url, json={})).status_code == 200

        # El servidor deja de anunciar `write_file`. Con selección explícita NO se
        # retira nada (un import parcial es una elección, no un inventario)…
        _patch(monkeypatch, ["read_file"])
        explicit = await client.post(url, json={"tool_names": ["read_file"]})
        assert explicit.status_code == 200
        assert explicit.json()["retired"] == []
        names = {t["name"] for t in (await client.get("/tools?category=mcp")).json()}
        assert "filesystem.write_file" in names

        # …y sin selección sí, con auditoría.
        auto = await client.post(url, json={})
        assert auto.status_code == 200
        assert auto.json()["retired"] == ["filesystem.write_file"]
        names = {t["name"] for t in (await client.get("/tools?category=mcp")).json()}
        assert "filesystem.write_file" not in names

        # Y un re-anuncio la resucita (el índice único es parcial sobre filas vivas).
        _patch(monkeypatch, ["read_file", "write_file"])
        back = await client.post(url, json={})
        assert back.status_code == 200
        assert "filesystem.write_file" in {t["name"] for t in back.json()["tools"]}


@pytest.mark.asyncio
async def test_l1_por_encima_del_tope_se_abstiene(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    _patch(monkeypatch, [f"t{i}" for i in range(201)])
    async with await _client(configured_app, seeded) as client:
        resp = await client.post(_URL.format(pid=seeded["project_a"]), json={})
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["error_code"] == "TOO_MANY_TOOLS"
        assert "201" in resp.json()["detail"]["message"]
        # Cero filas: el automatismo se apaga, no trunca.
        assert (await client.get("/tools?category=mcp")).json() == []


@pytest.mark.asyncio
async def test_r4_retirar_el_servidor_del_proyecto_se_lleva_sus_filas_salvo_si_otro_lo_declara(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    _patch(monkeypatch, ["read_file"])
    pid = seeded["project_a"]
    async with await _client(configured_app, seeded) as client:
        assert (await client.post(_URL.format(pid=pid), json={})).status_code == 200
        server = (await client.get(f"/projects/{pid}")).json()["mcp_servers"][0]

        # Un segundo proyecto del MISMO tenant declara un servidor homónimo.
        other = await client.post("/projects", json={"name": "Project A2", "mcp_servers": [server]})
        assert other.status_code == 201, other.text

        # Quitarlo de A no retira nada: B lo sigue declarando.
        put = await client.put(f"/projects/{pid}", json={"mcp_servers": []})
        assert put.status_code == 200, put.text
        names = {t["name"] for t in (await client.get("/tools?category=mcp")).json()}
        assert "filesystem.read_file" in names

        # Quitarlo también de B sí lo retira.
        put_b = await client.put(f"/projects/{other.json()['id']}", json={"mcp_servers": []})
        assert put_b.status_code == 200, put_b.text
        names = {t["name"] for t in (await client.get("/tools?category=mcp")).json()}
        assert "filesystem.read_file" not in names
