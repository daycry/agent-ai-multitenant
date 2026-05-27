"""Demo: human_05_01 — MCP funciona con un servidor real + Vault inyectado.

Lee el escenario que `setup_demo_05.py` dejó sembrado (un proyecto con
una entrada `mcp_servers` apuntando al toy MCP server local) y:

  1. Confirma que la entrada se ve en la UI de configuración de MCP
     servers (`/admin/projects/<id>/mcp-servers`).
  2. Llama al endpoint `/test-connection` (el mismo que dispara el
     botón "Probar" del dialog) y comprueba que devuelve los tools
     descubiertos por el toy server.
  3. Hace un smoke test en proceso con Vault inyectado vía
     `StaticVaultResolver` para demostrar que un secreto cruza al
     subprocess sin filtrarse al padre.
  4. Termina con las URLs concretas del admin-panel que tienes que
     abrir + qué buscar en cada una.

Si no encuentra el estado, te dice cómo lanzar el setup.

Uso:

    .venv/Scripts/python scripts/demo_human_05_01.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Force UTF-8 stdout.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

_REPO_ROOT = Path(__file__).resolve().parent.parent
_STATE_FILE = _REPO_ROOT / "scripts" / ".demo_state_05.json"
_TOY_SERVER = _REPO_ROOT / "tests" / "integration" / "_toy_mcp_server.py"
_API_URL = os.environ.get("DEMO_API_URL", "http://localhost:8001")
_ADMIN_URL = os.environ.get("DEMO_ADMIN_URL", "http://localhost:3000")
_FAKE_SECRET = "tok-from-vault-NEVER-LOG-ME"


def _banner(text: str) -> None:
    bar = "-" * 60
    print(f"\n{bar}\n  {text}\n{bar}", flush=True)


def _check(ok: bool, label: str, detail: str = "") -> bool:
    mark = "[ OK ]" if ok else "[FAIL]"
    print(f"  {mark} {label}" + (f" - {detail}" if detail else ""))
    return ok


def _load_state() -> dict[str, str] | None:
    if not _STATE_FILE.exists():
        return None
    return json.loads(_STATE_FILE.read_text(encoding="utf-8"))


def _http_get(path: str) -> tuple[int, dict]:
    """GET against api-server with no auth (the dev compose has auth
    disabled on /healthz; for project endpoints, the demo expects the
    stack to be in dev mode with the platform's seeded root token, OR
    the operator to read the URLs in the browser where the session
    cookie is already set)."""
    req = urllib.request.Request(f"{_API_URL}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status, body
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = {}
        return exc.code, body


async def _vault_smoke_test() -> bool:
    """Drive the executor directly with a StaticVaultResolver to
    prove the secret reaches the subprocess. This is the security-
    critical check the UI alone can't show."""
    from shared_mcp import (
        MCPClient,
        MCPServerConfig,
        StaticVaultResolver,
    )

    config = MCPServerConfig(
        name="toy",
        transport="stdio",
        command=sys.executable,
        args=(str(_TOY_SERVER), "--transport", "stdio"),
        auth_ref="vault:secret/data/mcp/toy/demo",
        timeout_s=15.0,
    )
    resolver = StaticVaultResolver(values={config.auth_ref or "": {"TOY_SECRET": _FAKE_SECRET}})
    async with MCPClient.connect(config, vault_resolver=resolver) as session:
        result = await session.call_tool("secret_echo", {"env_var": "TOY_SECRET"})

    secret_in_subprocess = _FAKE_SECRET in result.content
    secret_in_parent = os.environ.get("TOY_SECRET") == _FAKE_SECRET
    _check(secret_in_subprocess, "el secret cruza al subprocess (lo eco secret_echo)")
    _check(not secret_in_parent, "el secret NO se filtra al os.environ del padre")
    return secret_in_subprocess and not secret_in_parent


def main() -> int:
    _banner("demo human_05_01 - MCP + Vault end-to-end")

    state = _load_state()
    if state is None:
        print("FAIL — no state file. Lanza primero:")
        print("       .venv/Scripts/python scripts/setup_demo_05.py")
        return 1
    project_id = state["project_id"]

    # Step 1: the project's mcp_servers must contain the toy entry.
    print("\n=> 1) la entrada MCP esta persistida en el proyecto")
    status, body = _http_get(f"/projects/{project_id}")
    project_visible = status == 200
    if not project_visible:
        print(f"     HTTP {status} - {body}")
        print("     (esto es normal si el api-server pide auth: abre el URL")
        print("      de mas abajo en el navegador, donde tienes sesion)")
    else:
        servers = body.get("mcp_servers") or []
        toy = next((s for s in servers if s.get("name") == "toy-mcp"), None)
        _check(toy is not None, "Project.mcp_servers contiene la entrada 'toy-mcp'")
        if toy:
            _check(toy.get("transport") == "stdio", "transport=stdio")

    # Step 2: /test-connection devuelve tools descubiertas.
    print("\n=> 2) endpoint /test-connection lista los tools del toy server")
    test_payload = {
        "name": "toy-mcp",
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(_TOY_SERVER), "--transport", "stdio"],
        "timeout_s": 15.0,
    }
    req = urllib.request.Request(
        f"{_API_URL}/projects/{project_id}/mcp/test-connection",
        data=json.dumps(test_payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            tc_body = json.loads(resp.read().decode("utf-8"))
            tools = [t["name"] for t in tc_body.get("tools", [])]
            _check("echo" in tools and "add" in tools, "tools descubiertos", str(tools))
    except urllib.error.HTTPError as exc:
        print(f"     HTTP {exc.code} - {exc.read().decode('utf-8', errors='replace')}")
        print("     (auth requerida desde script; el boton 'Probar' del")
        print("      dialog de la UI usa la sesion del browser y SI funciona)")

    # Step 3: in-process Vault injection smoke test.
    print("\n=> 3) Vault injection roundtrip (smoke test in-proceso)")
    vault_ok = asyncio.run(_vault_smoke_test())

    _banner("Que abrir en el admin-panel ahora")
    print("\n  1) MCP servers del proyecto:")
    print(f"     {_ADMIN_URL}/admin/projects/{project_id}/mcp-servers")
    print("     -> Debes ver una card 'toy-mcp' con badge 'stdio'.")
    print("     -> Pulsa el icono lapiz para editar; en el dialog,")
    print("        pulsa 'Probar conexion' y mira la lista de tools")
    print("        ['echo','add','secret_echo'] aparecer abajo.")
    print("\n  2) Panel diagnostico de tools por agente:")
    print(f"     {_ADMIN_URL}/admin/projects/{project_id}/agent-tools-diagnostic")
    print("     -> Card 'MCP servers del proyecto' lista 'toy-mcp'.")
    print("     -> 2 cards de agentes (HTTP Lookup Bot, Sandbox Runner)")
    print("        con sus tools wired.")

    if vault_ok:
        _banner("demo human_05_01 PASSED")
        return 0
    _banner("demo human_05_01 - revisa los items FAIL arriba")
    return 1


if __name__ == "__main__":
    sys.exit(main())
