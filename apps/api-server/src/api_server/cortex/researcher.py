"""Córtex F4 — investigación autónoma con `claude_sdk` agéntico (ADR 0076, punto 3).

Cuando el owner tiene `claude_sdk`, la forma **recomendada** de que el córtex
investigue un tema no es que el api-server salga a buscar con su propia tool web,
sino pedirle al SDK que use sus **WebSearch/WebFetch nativas** vía
``ClaudeAgentOptions.allowed_tools``: la salida es la del api-server (servicio
confiable, internet directo por ``agentic-net``), el fetch lo gestiona Anthropic y
por tanto el **anti-SSRF sale gratis** — sin abrir egress en ningún runtime de
agente y sin depender del ADR 0067.

Estado del ADR 0076 (cerrado `accepted` el 2026-07-26): mantiene la **divergencia
deliberada 3→4**. El bucle del stack de desarrollo usa Ollama, que no tiene SDK, y
por eso investiga con la tool web propia + anti-SSRF obligatorio (punto 4). Este
módulo es el punto 3, que el ADR «sigue recomendando cuando el owner tenga
claude_sdk». Los dos caminos conviven: el bucle elige según el provider que
resuelva, y la degradación de aquí es lo que hace que esa elección sea segura.

Dos decisiones que parecen detalles y no lo son:

  * **``allowed_tools`` es una lista CERRADA de dos elementos.** No es una
    preferencia: un ``allowed_tools=None`` le daría al bucle autónomo el juego
    completo del SDK (``Bash``, ``Write``, ``Read``…) ejecutándose DENTRO del
    api-server confiable, sin que nadie lo pida ni lo vea. La curiosidad necesita
    leer la web y nada más.
  * **Nada de ``try/except`` aquí.** Un fallo del SDK se PROPAGA al caller, que es
    quien tiene el circuit-breaker (:mod:`api_server.cortex.autonomy`). Si este
    módulo devolviese ``skipped`` ante un error, el breaker no contaría fallos y una
    avería del transporte se reintentaría cada 30 minutos indefinidamente.

Y la contabilidad: se suma ``Usage.cost_usd`` de **cada** evento ``result`` (el SDK
multi-turno emite varios). Ese número es lo que alimenta el cap de dólares del
budget y la columna ``cortex_curiosity_pursuits.cost_usd`` — que antes de esto era
siempre 0, así que el "coste real de la pasada" del panel mentía por omisión.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Las ÚNICAS tools que el investigador autoriza. Cerrada a propósito (ver módulo).
WEB_TOOLS: tuple[str, ...] = ("WebSearch", "WebFetch")

#: Techo de turnos del run agéntico. Un bucle autónomo sin techo de turnos es un
#: gasto sin techo: el cap de dólares lo frena a posteriori, esto lo acota a priori.
MAX_RESEARCH_TURNS = 6

#: Effort por defecto (ADR 0070/0076: el razonamiento profundo es la razón de usar
#: el SDK aquí). El caller puede bajarlo; el parámetro llega a ``_build_options``
#: desde el fix del punto 2 del ADR 0076 — antes se ignoraba en silencio.
DEFAULT_RESEARCH_EFFORT = "high"

RESEARCH_SYSTEM_PROMPT = (
    "Eres el proceso de CURIOSIDAD de un córtex (modelo COMPUTACIONAL, NO "
    "consciencia). Recibes un TEMA y puedes usar la búsqueda web para informarte. "
    "Destila lo aprendido en 1-3 frases claras y útiles, en PRIMERA persona, en el "
    "idioma del tema. NO inventes: cíñete a lo que encuentres; si no encuentras "
    "nada útil, dilo en una frase. Responde SOLO con el texto del aprendizaje, sin "
    "prosa adicional ni markdown."
)


@dataclass(frozen=True)
class ResearchResult:
    """El resultado de investigar un tema: qué se aprendió y qué costó.

    ``skipped=True`` con ``reason='no_sdk'`` es la **degradación limpia**: el
    provider efectivo no es ``claude_sdk``, así que no hubo investigación y —esto es
    lo importante— no hubo ni una llamada de red. Un digest vacío con
    ``skipped=False`` es otra cosa: sí se investigó, sí pudo costar dinero, y no
    salió nada útil (el caller lo trata como skip, no como fallo)."""

    digest: str = ""
    search_count: int = 0
    cost_usd: float = 0.0
    skipped: bool = False
    reason: str = "ok"


def supports_agentic_research(provider: Any) -> bool:
    """¿Este provider puede investigar con las web tools nativas del SDK?

    Duck-typing sobre ``run_agent`` INVOCABLE, no sobre el nombre: un provider (o
    un doble mal escrito) que exponga ``run_agent`` como dato daría
    ``TypeError: object is not callable`` dentro de beat, sin nadie mirando."""
    return callable(getattr(provider, "run_agent", None))


def _is_web_tool(tool_use: dict[str, Any] | None) -> bool:
    """Si un evento ``tool_use`` es una salida a Internet (unidad de budget).

    Solo WebSearch/WebFetch cuentan: el budget de "búsquedas" existe para topar el
    EGRESS, así que contar trabajo interno del SDK agotaría el cap sin que el córtex
    hubiese salido a la web ni una vez."""
    name = str((tool_use or {}).get("name", ""))
    return name in WEB_TOOLS


async def research_topic(
    provider: Any,
    *,
    topic: str,
    model: str | None = None,
    effort: str = DEFAULT_RESEARCH_EFFORT,
) -> ResearchResult:
    """Investiga ``topic`` con el SDK agéntico; degrada limpio si no hay SDK.

    Acumula los eventos ``text`` en el digest, cuenta los ``tool_use`` de las dos
    web tools como búsquedas (unidades de egress del budget) y **suma** el
    ``cost_usd`` de cada evento ``result``.

    La resolución del modelo NO vive aquí: el caller pasa ``model`` (de
    ``cortex.default_model``, F1) y ``effort``. Sin SDK ⇒
    ``ResearchResult(skipped=True, reason='no_sdk')`` sin tocar la red. Un fallo del
    SDK se propaga (el caller tiene el circuit-breaker)."""
    if not supports_agentic_research(provider):
        return ResearchResult(skipped=True, reason="no_sdk")

    chunks: list[str] = []
    search_count = 0
    cost_usd = 0.0

    prompt = (
        f"TEMA: {topic}\n\n"
        "Busca en la web lo más relevante y reciente sobre el tema y destila lo "
        "aprendido."
    )
    async for event in provider.run_agent(
        prompt,
        model=model,
        system_prompt=RESEARCH_SYSTEM_PROMPT,
        allowed_tools=list(WEB_TOOLS),
        max_turns=MAX_RESEARCH_TURNS,
        effort=effort,
    ):
        if event.kind == "text" and event.text:
            chunks.append(event.text.strip())
        elif event.kind == "tool_use" and _is_web_tool(event.tool_use):
            search_count += 1
        elif event.kind == "result" and event.usage is not None:
            # Varios `result` en un run multi-turno: se SUMAN. Un `=` dejaría el
            # gasto contabilizado por debajo justo en el caso caro.
            cost_usd += float(event.usage.cost_usd or 0.0)

    digest = " ".join(c for c in chunks if c).strip()
    return ResearchResult(
        digest=digest,
        search_count=search_count,
        cost_usd=cost_usd,
        skipped=False,
        reason="ok",
    )


__all__ = [
    "DEFAULT_RESEARCH_EFFORT",
    "MAX_RESEARCH_TURNS",
    "RESEARCH_SYSTEM_PROMPT",
    "WEB_TOOLS",
    "ResearchResult",
    "research_topic",
    "supports_agentic_research",
]
