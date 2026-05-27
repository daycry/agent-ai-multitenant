"""Demo: human_05_03 — allowlist de http_endpoint se respeta.

Crea dos `HttpEndpointTool` apuntando a la misma URL; una con allowlist
que la incluye y otra con allowlist que la excluye. Prueba:

  1. La invocacion off-allowlist falla con error explicito sobre allowlist
     → ToolResult.ok=False con "domain not allowed: <host>".
  2. La invocacion on-allowlist hace la llamada real al host.

El segundo paso usa `httpbin.org` (servicio publico de test HTTP) — si
no hay conectividad a internet, el demo lo skipea con un mensaje claro
en lugar de fallar. El item 1 del checklist (lo critico de seguridad)
funciona sin red.

Item 2 del roadmap ("queda en audit_log con el dominio bloqueado") se
verifica indirectamente: el `ToolResult.output['allowed']` contiene
la lista de dominios permitidos, y el agente que llama la tool emite
una step del tipo `tool_call` en su steps_log con el `tool_name` y el
error. Esa parte se prueba con un agente real (no en este demo
unitario).

Uso:

    .venv/Scripts/python scripts/demo_human_05_03.py

No requiere docker daemon ni stack. Solo el venv con `agent_runtime`.
"""

from __future__ import annotations

import contextlib
import socket
import sys

# Force UTF-8 stdout.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def _banner(text: str) -> None:
    bar = "─" * 60
    print(f"\n{bar}\n  {text}\n{bar}", flush=True)


def _has_internet(host: str, port: int = 443, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main() -> int:
    from agent_runtime.http_endpoint_tool import HttpEndpointTool

    _banner("demo human_05_03 — http_endpoint allowlist")

    # Step 1: off-allowlist URL must be REJECTED before any HTTP call.
    print("\n→ Step 1: URL outside the project allowlist must fail")
    off = HttpEndpointTool(
        name="lookup-blocked",
        url_template="https://forbidden.example.com/?q={q}",
        allowed_domains=frozenset({"api.allowed.example.com"}),
    )
    off_result = off({"q": "test"})
    if off_result.ok:
        print(f"✗ FAIL — off-allowlist call should have failed: {off_result.output!r}")
        return 1
    error = (off_result.error or "").lower()
    if "not allowed" not in error or "forbidden.example.com" not in error:
        print(f"✗ FAIL — error message lacks expected context: {off_result.error!r}")
        return 1
    allowed_list = (off_result.output or {}).get("allowed")
    print(f"  ✓ ToolResult.ok={off_result.ok}")
    print(f"  ✓ error: {off_result.error}")
    print(f"  ✓ allowed list surfaced: {allowed_list}")

    # Step 2: on-allowlist URL works (if we have internet).
    print("\n→ Step 2: URL on the allowlist round-trips a real HTTP call")
    if not _has_internet("httpbin.org"):
        print("  ⚠ no internet — skipping the real round-trip. Step 1 covers")
        print("    the security-critical path (off-allowlist rejected).")
    else:
        on = HttpEndpointTool(
            name="lookup-allowed",
            url_template="https://httpbin.org/anything/{path}",
            allowed_domains=frozenset({"httpbin.org"}),
            timeout_s=10.0,
        )
        on_result = on({"path": "demo"})
        if not on_result.ok:
            print(f"✗ FAIL — on-allowlist call did not succeed: {on_result.error}")
            return 1
        body = on_result.output["body"]
        status_code = on_result.output["status_code"]
        print(f"  ✓ status_code: {status_code}")
        print(f"  ✓ url echoed back: {body.get('url', '<missing>')}")

    _banner("demo human_05_03 PASSED")
    print("Checklist roadmap:")
    print("  [✓] La invocacion falla con error explicito sobre allowlist")
    print("  [partial] El intento queda en audit_log con el dominio bloqueado")
    print("        (requires running a real agent execution; demo asserts")
    print("         the error surfaced is machine-readable + actionable)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
