"""La credencial de un proveedor claude_sdk no puede vivir en `os.environ`.

Prerequisito de seguridad que el **ADR 0076** dejó anotado y que seguía abierto:
el constructor escribía `ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN` en el
entorno **global del proceso**. Tres consecuencias, y la tercera es la cara:

  1. la credencial queda en `/proc/self/environ` y la hereda cualquier hijo;
  2. no se limpia nunca;
  3. **el catálogo de proveedores admite varias filas del mismo kind** (la
     columna `slug` de la migración 0083 existe justo para eso). Con la
     escritura global, la clave del proveedor A quedaba puesta para siempre:
     un proveedor B configurado con suscripción OAuth arrancaba con la
     `ANTHROPIC_API_KEY` de A todavía en el entorno y el CLI podía preferirla
     — facturando a la cuenta de A las llamadas de B, en silencio.

El arreglo pasa la credencial **por llamada**, vía `ClaudeAgentOptions.env`,
que el transporte fusiona sobre el entorno heredado (y gana). Como es una
fusión y no un reemplazo, no basta con poner la propia: hay que **neutralizar
la del otro modo**, o una clave rancia heredada seguiría ganando.
"""

from __future__ import annotations

import os
import sys
import types
from typing import Any

import pytest
from shared_llm.providers import ClaudeAgentProvider

_API = "ANTHROPIC_API_KEY"
_OAUTH = "CLAUDE_CODE_OAUTH_TOKEN"


def _fake_sdk(captured: dict[str, Any]) -> types.ModuleType:
    fake = types.ModuleType("claude_agent_sdk")

    class _Options:
        def __init__(self, **kw: Any) -> None:
            captured.update(kw)

    fake.ClaudeAgentOptions = _Options  # type: ignore[attr-defined]
    return fake


@pytest.fixture()
def sdk(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _fake_sdk(captured))
    # Entorno limpio: cada test declara lo que quiere que haya.
    monkeypatch.delenv(_API, raising=False)
    monkeypatch.delenv(_OAUTH, raising=False)
    return captured


def _options(provider: ClaudeAgentProvider, captured: dict[str, Any]) -> dict[str, str]:
    provider._build_options(model="m", system=None, allowed_tools=None, max_turns=1)
    return dict(captured.get("env") or {})


# ---------------------------------------------------------------------------
# La fuga
# ---------------------------------------------------------------------------
def test_the_api_key_never_lands_in_the_process_environment(sdk: dict[str, Any]) -> None:
    ClaudeAgentProvider(api_key="sk-de-A")
    assert os.environ.get(_API) is None


def test_the_oauth_token_never_lands_in_the_process_environment(sdk: dict[str, Any]) -> None:
    ClaudeAgentProvider(oauth_token="tok-de-A")
    assert os.environ.get(_OAUTH) is None


def test_one_providers_credential_does_not_reach_another(sdk: dict[str, Any]) -> None:
    # El caso que costaba dinero: A con API key, B con suscripción. Antes, B
    # heredaba la clave de A y el CLI podía facturarle a A.
    a = ClaudeAgentProvider(api_key="sk-de-A")
    b = ClaudeAgentProvider(oauth_token="tok-de-B")

    env_a = _options(a, sdk)
    assert env_a[_API] == "sk-de-A"

    env_b = _options(b, sdk)
    assert env_b[_OAUTH] == "tok-de-B"
    # No basta con que B lleve lo suyo: `env` se FUSIONA sobre el entorno
    # heredado, así que B tiene que anular explícitamente la vía de A.
    assert env_b.get(_API) == ""


def test_a_stale_inherited_key_is_neutralised(
    sdk: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Una clave puesta en el despliegue (o por código antiguo) no puede ganarle
    # a la credencial que el operador configuró en ESTE proveedor.
    monkeypatch.setenv(_API, "sk-rancia-del-despliegue")
    provider = ClaudeAgentProvider(oauth_token="tok-del-proveedor")
    env = _options(provider, sdk)
    assert env[_OAUTH] == "tok-del-proveedor"
    assert env[_API] == ""


# ---------------------------------------------------------------------------
# Lo que NO debe cambiar
# ---------------------------------------------------------------------------
def test_ambient_auth_still_works_when_the_provider_has_no_credential(
    sdk: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Un usuario Pro/Max puede tener el token ya en el entorno o en las
    # credenciales del propio SDK. Sin credencial configurada NO tocamos nada:
    # anular ahí rompería ese arranque.
    monkeypatch.setenv(_API, "sk-ambiental")
    provider = ClaudeAgentProvider()
    env = _options(provider, sdk)
    assert _API not in env
    assert _OAUTH not in env


def test_the_api_key_mode_neutralises_the_oauth_variable(sdk: dict[str, Any]) -> None:
    provider = ClaudeAgentProvider(api_key="sk-1")
    env = _options(provider, sdk)
    assert env[_API] == "sk-1"
    assert env.get(_OAUTH) == ""


def test_both_credentials_configured_keeps_both(sdk: dict[str, Any]) -> None:
    # Configuración rara pero legítima; no nos toca elegir por el operador.
    provider = ClaudeAgentProvider(api_key="sk-1", oauth_token="tok-1")
    env = _options(provider, sdk)
    assert env[_API] == "sk-1"
    assert env[_OAUTH] == "tok-1"


def test_the_credential_is_not_logged_in_the_repr(sdk: dict[str, Any]) -> None:
    # Un `repr` con la clave acaba en un traceback y de ahí en Loki.
    provider = ClaudeAgentProvider(api_key="sk-secretisima")
    assert "sk-secretisima" not in repr(provider)


# ---------------------------------------------------------------------------
# Que no vuelva a pasar
# ---------------------------------------------------------------------------
def test_every_options_builder_passes_the_credential() -> None:
    """Ningún sitio que construya `ClaudeAgentOptions` puede olvidar `env`.

    El fallo real durante este arreglo: `ClaudeAgentSessionProvider` (ADR 0097)
    construye sus PROPIAS opciones en vez de reutilizar `_build_options`, así
    que al dejar de escribir `os.environ` los runs con hilo persistente se
    habrían quedado **sin credencial** — y en silencio, porque el error de auth
    sale del CLI en tiempo de llamada.

    Guarda estática en vez de un test por camino: lo que hay que impedir es que
    aparezca un CUARTO constructor sin `env`, y un test de comportamiento no
    cubre el que aún no existe.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "shared_llm" / "providers"
    offenders: list[str] = []
    seen = 0
    for path in root.glob("claude_agent*.py"):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"return ClaudeAgentOptions\(", source):
            seen += 1
            line_no = source[: match.start()].count("\n") + 1
            # El bloque de 25 líneas previas es donde se prepara `extra`.
            window = "\n".join(source.splitlines()[max(0, line_no - 26) : line_no])
            if "_auth_env()" not in window:
                offenders.append(f"{path.name}:{line_no}")
    # Sin esto la guarda pasaría VACÍAMENTE el día que alguien renombre la
    # clase o cambie el `return`: cero coincidencias, cero infractores, verde.
    assert seen >= 3, f"la guarda dejó de encontrar los constructores (vio {seen})"
    assert not offenders, (
        "estos constructores de ClaudeAgentOptions no pasan la credencial por "
        f"`env` y dejarían el run sin auth: {offenders}"
    )
