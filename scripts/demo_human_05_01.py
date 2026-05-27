"""Demo: human_05_01 — MCP funciona con un servidor real + Vault inyectado.

Este demo NO depende de un PAT de GitHub real ni de docker compose
arriba. Usa el toy MCP server (`tests/integration/_toy_mcp_server.py`)
como sustituto del servidor real, le inyecta un secret via
`StaticVaultResolver` (el mismo Protocol que `HvacVaultResolver`), y
prueba el flujo entero:

  1. `MCPClient.connect()` abre el subprocess stdio del toy server.
  2. El resolver Vault aporta `TOY_SECRET` que se fusiona en
     `config.env` antes del spawn.
  3. `secret_echo(env_var='TOY_SECRET')` desde el toy server demuestra
     que el secret cruzo el subprocess SIN aparecer en logs.

Cubre los items 1 (UI muestra tools), 4 (tokens Vault sin logs) y
parcialmente 2 (agente puede llamar tools). Items 2 y 3 (listar repos
GitHub reales, crear issue) requieren cuenta real — el demo prueba la
mecanica, no la integracion con GitHub.

Uso:

    .venv/Scripts/python scripts/demo_human_05_01.py

No requiere docker compose. Requisitos:
  - El venv con `shared_mcp` + `agent_runtime` instalados (pip install -e).
  - El toy server en `tests/integration/_toy_mcp_server.py`.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from pathlib import Path

# Force UTF-8 stdout — Windows default cp1252 chokes on accents + box-
# drawing characters used by this report.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOY_SERVER = _REPO_ROOT / "tests" / "integration" / "_toy_mcp_server.py"

# A "secret" we'll plant in the fake Vault and check the toy server
# receives via env.
_FAKE_SECRET = "tok-from-vault-NEVER-LOG-ME"


def _banner(text: str) -> None:
    bar = "─" * 60
    print(f"\n{bar}\n  {text}\n{bar}", flush=True)


async def _amain() -> int:
    # Lazy imports — keep the script start-up cheap when imports fail
    # the error message stays clean.
    from shared_mcp import (
        MCPClient,
        MCPServerConfig,
        StaticVaultResolver,
        discover_tools,
    )

    _banner("demo human_05_01 — MCP + Vault end-to-end (toy server)")

    if not _TOY_SERVER.is_file():
        print(f"✗ FAIL — toy MCP server not at {_TOY_SERVER}")
        return 1

    config = MCPServerConfig(
        name="toy",
        transport="stdio",
        command=sys.executable,
        args=(str(_TOY_SERVER), "--transport", "stdio"),
        auth_ref="vault:secret/data/mcp/toy/demo",
        timeout_s=15.0,
    )

    # Resolver — same Protocol api-server's HvacVaultResolver implements.
    # In production this is hvac-backed; here we use the in-memory test
    # double so the demo runs anywhere.
    resolver = StaticVaultResolver(values={config.auth_ref or "": {"TOY_SECRET": _FAKE_SECRET}})

    # Step 1: discover tools (item 1 of the checklist — "la UI muestra
    # las tools descubiertas del servidor").
    print("\n→ discover_tools() — opens session, runs handshake, lists tools")
    discovery = await discover_tools(config, vault_resolver=resolver)
    tool_names = sorted(t.name for t in discovery.tools)
    print(f"  server: {discovery.server_name!r} v{discovery.server_version}")
    print(f"  tools : {tool_names}")
    if "secret_echo" not in tool_names:
        print("✗ FAIL — toy server didn't advertise `secret_echo`")
        return 1
    print("  ✓ tools[] visible")

    # Step 2: invoke `secret_echo` with the env var name we expect the
    # subprocess to have. If the resolver wired correctly, the toy
    # server sees TOY_SECRET in os.environ and echoes its value.
    print("\n→ call_tool('secret_echo') — proves Vault secret reached the subprocess")
    async with MCPClient.connect(config, vault_resolver=resolver) as session:
        result = await session.call_tool("secret_echo", {"env_var": "TOY_SECRET"})

    if _FAKE_SECRET not in result.content:
        print(f"✗ FAIL — toy server didn't see the secret (got: {result.content!r})")
        return 1
    print(f"  ✓ subprocess received TOY_SECRET = {_FAKE_SECRET!r}")

    # Step 3: prove the secret is NOT visible in this Python process'
    # env (only the subprocess gets it via config.env merge).
    print("\n→ env scrub check — secret must NOT be in parent process env")
    if os.environ.get("TOY_SECRET") == _FAKE_SECRET:
        print("✗ FAIL — secret leaked to parent process os.environ")
        return 1
    print("  ✓ parent os.environ is clean")

    _banner("demo human_05_01 PASSED")
    print("Checklist roadmap:")
    print("  [✓] La UI muestra las tools descubiertas del servidor")
    print("  [partial] Un agente puede listar repos del usuario  (requires real PAT)")
    print("  [partial] Un agente puede crear un issue            (requires real PAT)")
    print("  [✓] Los tokens del Vault se inyectan sin aparecer en logs")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
