"""Demo: human_05_03 — allowlist de http_endpoint se respeta.

Lee el state de `setup_demo_05.py` (un Tool de tipo `http_endpoint`
wired al agente "HTTP Lookup Bot") y:

  1. Construye un `HttpEndpointTool` con una URL FUERA del allowlist
     y comprueba que falla con un error explícito ANTES de hacer la
     llamada HTTP.
  2. Si hay internet, construye otra apuntando a httpbin.org (en el
     allowlist) para demostrar el round-trip exitoso.
  3. Te dice qué URL del admin-panel abrir para ver la Tool wired.

Uso:

    .venv/Scripts/python scripts/demo_human_05_03.py

No requiere docker. Step 2 se skipea sin internet.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

_REPO_ROOT = Path(__file__).resolve().parent.parent
_STATE_FILE = _REPO_ROOT / "scripts" / ".demo_state_05.json"
_ADMIN_URL = os.environ.get("DEMO_ADMIN_URL", "http://localhost:3000")


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


def _has_internet(host: str = "httpbin.org", port: int = 443) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return True
    except OSError:
        return False


def main() -> int:
    from agent_runtime.http_endpoint_tool import HttpEndpointTool

    _banner("demo human_05_03 - http_endpoint allowlist")

    state = _load_state()
    if state is None:
        print("FAIL — no state file. Lanza primero:")
        print("       .venv/Scripts/python scripts/setup_demo_05.py")
        return 1
    project_id = state["project_id"]
    agent_http_id = state["agent_http_id"]
    tool_http_id = state["tool_http_id"]

    print("\n=> Tool wired al agente HTTP Lookup Bot")
    print(f"     project_id : {project_id}")
    print(f"     agent_id   : {agent_http_id}")
    print(f"     tool_id    : {tool_http_id}")

    # Step 1: off-allowlist URL must be REJECTED before any HTTP call.
    print("\n=> 1) URL fuera del allowlist debe fallar SIN llamar a la red")
    off = HttpEndpointTool(
        name="lookup-blocked",
        url_template="https://forbidden.example.com/?q={q}",
        allowed_domains=frozenset({"api.allowed.example.com"}),
    )
    off_result = off({"q": "test"})
    err = (off_result.error or "").lower()
    ok_rejected = _check(off_result.ok is False, "ToolResult.ok = False")
    ok_msg = _check(
        "not allowed" in err and "forbidden.example.com" in err,
        "error explicito sobre el dominio bloqueado",
        off_result.error or "",
    )
    allowed = (off_result.output or {}).get("allowed", [])
    ok_list = _check(
        bool(allowed),
        "output.allowed lista los dominios permitidos",
        str(allowed),
    )

    # Step 2: on-allowlist round-trip (opcional con internet).
    print("\n=> 2) URL en el allowlist hace round-trip HTTP real")
    if not _has_internet():
        print("     [SKIP] sin internet - step 1 cubre el camino security-critical")
        ok_roundtrip = True
    else:
        on = HttpEndpointTool(
            name="lookup-allowed",
            url_template="https://httpbin.org/anything/{path}",
            allowed_domains=frozenset({"httpbin.org"}),
            timeout_s=10.0,
        )
        on_result = on({"path": "demo"})
        ok_roundtrip = _check(
            on_result.ok is True,
            "round-trip OK",
            (
                f"status={on_result.output.get('status_code')}"
                if on_result.ok
                else (on_result.error or "")
            ),
        )

    _banner("Que abrir en el admin-panel ahora")
    print("\n  Panel diagnostico (read-only) del proyecto:")
    print(f"     {_ADMIN_URL}/admin/projects/{project_id}/agent-tools-diagnostic")
    print("\n  -> Card del agente 'HTTP Lookup Bot' debe listar el tool")
    print("     'example-weather' con:")
    print("        - badge implementation_type = http_endpoint  (azul)")
    print("        - badge security_level = safe                (verde)")
    print("        - timeout 10s, category 'data'")
    print("\n  -> El allowlist concreto vive en el agent-runtime al boot")
    print("     (no es un campo del Tool row); el operador lo configura")
    print("     en el proyecto. Cuando un agente real intente llamar la")
    print("     Tool con URL off-allowlist, vera el error en el step_log")
    print("     de la execution (/admin/executions/<id>) - parte que")
    print("     entrara cuando el agent loop wire el ToolRegistry desde")
    print("     Tool rows en la BD.")

    all_ok = all([ok_rejected, ok_msg, ok_list, ok_roundtrip])
    if all_ok:
        _banner("demo human_05_03 PASSED")
        return 0
    _banner("demo human_05_03 - revisa items FAIL arriba")
    return 1


if __name__ == "__main__":
    sys.exit(main())
