"""Córtex F4 — investigador con `claude_sdk` (ADR 0076 punto 3) y su degradación.

`research_topic` es el camino RECOMENDADO por el ADR 0076 cuando el owner tiene
`claude_sdk`: en vez de que el api-server salga a buscar con su propia tool web, se
le pide al SDK que use sus **WebSearch/WebFetch nativas** vía `allowed_tools`. La
salida es la del api-server (servicio confiable) y el anti-SSRF lo pone Anthropic,
así que no hace falta abrir egress en ningún runtime de agente.

El ADR cerró en `accepted` (2026-07-26) manteniendo la **divergencia deliberada**
3→4: el stack de desarrollo usa Ollama y por eso el bucle real usa la tool web
propia con anti-SSRF. Este módulo es el punto 3, que el propio ADR «sigue
recomendando cuando el owner tenga claude_sdk». De ahí que la mitad más importante
de estos tests sea la DEGRADACIÓN: un provider sin `run_agent` no puede hacer
egress y tiene que decirlo sin levantar y **sin tocar la red**.

Lo que estos tests fijan y antes no existía (auditoría 2026-07-27):

  * la contabilidad de `Usage.cost_usd` — hoy nadie la calculaba y por eso
    `cortex_curiosity_pursuits.cost_usd` era siempre 0 y el panel mostraba 0.00;
  * que las tools permitidas sean EXACTAMENTE las dos web (un `allowed_tools`
    generoso le daría a un bucle autónomo `Bash`/`Write` sobre el api-server);
  * que el `effort` viaje (era el fix bloqueante del punto 2 del ADR: `run_agent`
    lo ignoraba en silencio).

Doble de provider en memoria, sin red: mismo patrón que `ScriptedAssistantModel`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from api_server.cortex.researcher import ResearchResult, research_topic
from shared_llm.types import AgentRunEvent, Usage

pytestmark = pytest.mark.unit


class _ScriptedAgentProvider:
    """Provider-doble con `run_agent`: emite los eventos que se le programen.

    Registra los kwargs de la llamada para poder afirmar el contrato con el SDK
    (`allowed_tools`, `effort`, `model`, `system_prompt`), que es la mitad del
    valor de esta capa: si `allowed_tools` se ampliara, un bucle autónomo tendría
    herramientas de fichero sobre el api-server."""

    name = "scripted-claude-sdk"

    def __init__(self, events: list[AgentRunEvent]) -> None:
        self._events = events
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def run_agent(self, prompt: str, **kwargs: Any) -> AsyncIterator[AgentRunEvent]:
        self.calls.append({"prompt": prompt, **kwargs})

        async def _gen() -> AsyncIterator[AgentRunEvent]:
            for event in self._events:
                yield event

        return _gen()

    async def aclose(self) -> None:
        self.closed = True


class _NoSdkProvider:
    """Provider SIN `run_agent` (Ollama/Copilot/Azure): la rama degradada.

    `_boom` marca cualquier intento de trabajo: si `research_topic` cayera en
    `complete()` como "plan B", el test lo detecta — el MVP NO usa tool web propia
    por esta vía (Decisión #5 del plan maestro: el camino degradado exige su
    propio ADR con anti-SSRF)."""

    name = "ollama"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *_args: object, **_kwargs: object) -> None:
        self.calls += 1
        raise AssertionError("research_topic no debe llamar a complete() sin SDK")

    async def aclose(self) -> None:
        self.calls += 1


def _result_event(cost: float) -> AgentRunEvent:
    return AgentRunEvent(
        kind="result", usage=Usage(input_tokens=10, output_tokens=5, cost_usd=cost)
    )


def _tool_event(name: str) -> AgentRunEvent:
    return AgentRunEvent(kind="tool_use", tool_use={"name": name, "input": {"query": "rust"}})


# ---------------------------------------------------------------------------
# Camino con SDK: digest + nº de búsquedas + coste
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_acumula_texto_cuenta_busquedas_y_suma_coste() -> None:
    """La aceptación del plan: digest no vacío, `search_count>=1`, `cost_usd>0`.

    El coste es el punto entero de esta tarea: `cost_usd>0` era INALCANZABLE
    porque nadie lo leía del evento `result`, así que el budget de dólares no
    tenía nada que contar y el panel enseñaba 0.00 siempre."""
    provider = _ScriptedAgentProvider(
        [
            AgentRunEvent(kind="text", text="Rust usa ownership"),
            _tool_event("WebSearch"),
            AgentRunEvent(kind="text", text="para liberar memoria sin GC."),
            _result_event(0.0123),
        ]
    )

    result = await research_topic(provider, topic="rust", model="claude-sonnet", effort="high")

    assert result.skipped is False
    assert result.digest == "Rust usa ownership para liberar memoria sin GC."
    assert result.search_count == 1
    assert result.cost_usd == pytest.approx(0.0123)
    assert result.reason == "ok"


@pytest.mark.asyncio
async def test_pide_al_sdk_exactamente_las_dos_tools_web_y_le_pasa_el_effort() -> None:
    """`allowed_tools == ["WebSearch","WebFetch"]` y el `effort` llega al SDK.

    Dos invariantes de seguridad y una de calidad en el mismo assert:
      * la lista es CERRADA — el córtex investiga con las tools web nativas y con
        nada más; un `allowed_tools=None` le daría al bucle autónomo el juego
        completo del SDK (Bash, Write…) corriendo DENTRO del api-server confiable;
      * el `effort` viaja: era el fix bloqueante del punto 2 del ADR 0076
        (`run_agent` lo ignoraba en silencio, así que el "razonamiento profundo"
        no era profundo y nadie se enteraba);
      * el `model` viaja: la resolución vive en el caller (`cortex.default_model`),
        no aquí."""
    provider = _ScriptedAgentProvider([AgentRunEvent(kind="text", text="ok"), _result_event(0.01)])

    await research_topic(provider, topic="rust", model="claude-opus", effort="xhigh")

    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["allowed_tools"] == ["WebSearch", "WebFetch"]
    assert call["effort"] == "xhigh"
    assert call["model"] == "claude-opus"
    # El tema viaja en el prompt (no se pierde) y el system_prompt existe.
    assert "rust" in call["prompt"]
    assert call["system_prompt"]
    # Turnos ACOTADOS: un bucle autónomo sin techo de turnos es un gasto sin techo.
    assert call["max_turns"] >= 1


@pytest.mark.asyncio
async def test_cuenta_las_dos_tools_de_egress_y_no_las_demas() -> None:
    """Cada WebSearch/WebFetch es una unidad de egress; el resto no cuenta.

    El budget de búsquedas existe para topar el egress, así que lo que hay que
    contar es cada salida a Internet. Un `tool_use` de otra cosa (o un evento
    `other` del SDK) no es egress y no debe consumir budget — si contase, el cap
    se agotaría por trabajo interno y la curiosidad se apagaría sola."""
    provider = _ScriptedAgentProvider(
        [
            _tool_event("WebSearch"),
            _tool_event("WebFetch"),
            _tool_event("TodoWrite"),
            AgentRunEvent(kind="other", raw={"whatever": True}),
            AgentRunEvent(kind="text", text="aprendido"),
            _result_event(0.02),
        ]
    )

    result = await research_topic(provider, topic="rust", model=None, effort="high")

    assert result.search_count == 2
    assert result.digest == "aprendido"


@pytest.mark.asyncio
async def test_suma_el_coste_de_varios_eventos_result() -> None:
    """Con varios `result` (SDK multi-turno) el coste se SUMA, no se sobreescribe.

    Un `=` en vez de un `+=` dejaría el gasto contabilizado por debajo de lo real
    justo en el caso caro (el multi-turno), que es el que el cap de dólares tiene
    que frenar."""
    provider = _ScriptedAgentProvider(
        [
            AgentRunEvent(kind="text", text="a"),
            _result_event(0.01),
            AgentRunEvent(kind="text", text="b"),
            _result_event(0.02),
        ]
    )

    result = await research_topic(provider, topic="rust", model=None, effort="high")

    assert result.cost_usd == pytest.approx(0.03)
    assert result.digest == "a b"


@pytest.mark.asyncio
async def test_sin_texto_util_el_digest_queda_vacio_pero_no_es_un_fallo() -> None:
    """Una pasada que no produce texto no es un error: es un digest vacío.

    El caller distingue los dos casos (vacío ⇒ pursuit `skipped`, excepción ⇒
    `failed` + circuit-breaker). Devolver `skipped=True` aquí confundiría "el SDK
    no está" con "el SDK no encontró nada", y el segundo SÍ ha gastado dinero: el
    coste tiene que salir contabilizado igualmente."""
    provider = _ScriptedAgentProvider([_tool_event("WebSearch"), _result_event(0.005)])

    result = await research_topic(provider, topic="rust", model=None, effort="high")

    assert result.digest == ""
    assert result.skipped is False
    assert result.cost_usd == pytest.approx(0.005)  # gastó, aunque no aprendiese
    assert result.search_count == 1


# ---------------------------------------------------------------------------
# Degradación limpia: provider sin claude_sdk
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_provider_sin_run_agent_degrada_a_no_sdk_sin_tocar_la_red() -> None:
    """Sin `claude_sdk` (extra `claude` no instalado, ADR 0064) → `skipped no_sdk`.

    Y **cero llamadas**: ni `complete()` ni `aclose()` ni nada. El MVP no cae a la
    tool web propia por esta vía (eso exige su propio ADR con anti-SSRF
    obligatorio, punto 4 del ADR 0076): un fetch sin anti-SSRF desde el api-server
    confiable alcanza Vault y la red interna, o sea peor que desde el sandbox."""
    provider = _NoSdkProvider()

    result = await research_topic(provider, topic="rust", model=None, effort="high")

    assert result == ResearchResult(
        digest="", search_count=0, cost_usd=0.0, skipped=True, reason="no_sdk"
    )
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_un_run_agent_que_no_es_invocable_tambien_degrada() -> None:
    """Un atributo `run_agent` que no es callable no debe reventar el bucle.

    Guarda contra el `hasattr` ingenuo: un doble mal escrito, o un provider que
    exponga `run_agent` como dato, daría `TypeError: object is not callable` dentro
    de beat. La detección mira que sea invocable, no que el nombre exista."""

    class _Weird:
        run_agent = "no soy una función"

    result = await research_topic(_Weird(), topic="rust", model=None, effort="high")

    assert result.skipped is True
    assert result.reason == "no_sdk"


@pytest.mark.asyncio
async def test_un_fallo_del_sdk_se_propaga_al_caller() -> None:
    """El fallo NO se traga aquí: lo trata el caller (circuit-breaker + `failed`).

    Es deliberado y es la razón de que este módulo no tenga `try/except`: si
    devolviese `skipped` ante un error, el circuit-breaker del bucle nunca contaría
    fallos y una avería del SDK se reintentaría cada 30 minutos para siempre."""

    class _Boom:
        def run_agent(self, prompt: str, **kwargs: Any) -> AsyncIterator[AgentRunEvent]:
            async def _gen() -> AsyncIterator[AgentRunEvent]:
                yield AgentRunEvent(kind="text", text="empiezo")
                raise RuntimeError("sdk transport died")

            return _gen()

    with pytest.raises(RuntimeError, match="sdk transport died"):
        await research_topic(_Boom(), topic="rust", model=None, effort="high")
