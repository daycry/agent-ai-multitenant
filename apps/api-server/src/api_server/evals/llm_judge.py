"""El juez y el sujeto REALES de los evals, sobre la capa de proveedores (`task_wf_52b`).

El subsistema de evals estaba construido entero —7 módulos, 7 tablas, 18
endpoints, dashboard— y sus tablas vacías, porque **no había ninguna vía de
producirlas**. El motor aceptaba `JudgeModel` y `SubjectModel` y la única
implementación era `ScriptedJudgeModel`, el doble de test. Sin un juez real,
todo lo demás era andamiaje.

Esto lo cierra con la capa que ya existe (`shared_llm`, ADR 0021): mismo
catálogo de proveedores, mismas credenciales, misma resolución de modelo que el
resto del sistema. No hay un segundo camino de LLM que mantener.

Dos reglas que el motor ya exigía y aquí se respetan:

  * el juez NO puede ser el mismo modelo que el sujeto (`SameModelJudgeError`
    lo verifica en `run_eval`) — un modelo juzgándose a sí mismo se aprueba;
  * el juez devuelve TEXTO y el motor lo parsea. Este módulo no interpreta el
    veredicto: si lo hiciera, habría dos parsers del mismo contrato.

Por qué el sujeto también vive aquí
-----------------------------------
El item dorado trae `expected_output`: la **referencia**. Pasarla como si fuera
la salida del sujeto haría que el juez la comparase consigo misma — 100 % de
aciertos, siempre, midiendo nada. Un eval que siempre pasa es peor que no tener
eval, porque da confianza. El sujeto tiene que producir de verdad.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from shared_llm.types import Message

from api_server.evals.judge import JudgeCallResult, SubjectOutput

# Temperatura baja: juzgar contra una referencia es una tarea de comparación,
# no de creación. Un juez creativo puntúa distinto el mismo par dos veces y
# hace inservible la comparación entre releases, que es el punto de medir.
_JUDGE_TEMPERATURE = 0.0
_JUDGE_MAX_TOKENS = 2048

# El SUJETO, en cambio, corre a la temperatura de trabajo real: se le mide cómo
# se comporta cuando produce, no cómo se comporta si lo congelamos.
_SUBJECT_TEMPERATURE = 0.3
_SUBJECT_MAX_TOKENS = 4096


def _usage_of(response: Any) -> tuple[int, Decimal]:
    """Tokens y coste de una respuesta, a prueba de proveedores parcos.

    `cost_usd` es best-effort por contrato de `Usage` (Ollama local siempre
    da 0). Que un proveedor no informe no puede romper el run: el veredicto
    del juez vale igual sin la factura.
    """
    usage = getattr(response, "usage", None)
    tokens = int(getattr(usage, "input_tokens", 0) or 0) + int(
        getattr(usage, "output_tokens", 0) or 0
    )
    return tokens, Decimal(str(getattr(usage, "cost_usd", 0) or 0))


@dataclass
class LLMJudgeModel:
    """Adaptador de un `LLMProvider` al seam `JudgeModel` del motor."""

    provider: Any
    model: str

    async def judge(self, prompt: str) -> JudgeCallResult:
        started = time.monotonic()
        response = await self.provider.complete(
            [Message(role="user", content=prompt)],
            model=self.model,
            max_tokens=_JUDGE_MAX_TOKENS,
            temperature=_JUDGE_TEMPERATURE,
        )
        tokens, cost = _usage_of(response)
        return JudgeCallResult(
            text=response.content or "",
            tokens=tokens,
            cost_usd=cost,
            latency_ms=int((time.monotonic() - started) * 1000),
        )


@dataclass
class LLMSubjectModel:
    """Adaptador al seam `SubjectModel`: produce lo que el juez comparará.

    ``system_prompt`` es **el prompt bajo evaluación** — el del agente, o el
    candidato cuando el gate de `task_gov_05` está midiendo una edición. Antes
    de esa tarea este adaptador no mandaba ningún mensaje ``system``, y la
    consecuencia era silenciosa y grave: dos corridas del MISMO dataset con
    prompts distintos salían estadísticamente iguales, porque el sujeto nunca
    veía el prompt. Es decir, todo el subsistema medía el modelo, no al agente,
    y «esta edición del prompt empeora la calidad» era una pregunta que no se
    podía contestar por construcción.

    ``None`` (o vacío) mantiene el comportamiento anterior — un agente sin
    persona no tiene nada que prepender — en vez de inventarse un system vacío,
    que algunos proveedores rechazan y otros cuentan como turno.
    """

    provider: Any
    model: str
    system_prompt: str | None = None

    async def produce(self, item_input: dict[str, Any]) -> SubjectOutput:
        started = time.monotonic()
        body = json.dumps(item_input, ensure_ascii=False, indent=2, default=str)
        prompt = (
            "Resuelve la siguiente tarea y devuelve ÚNICAMENTE el entregable, "
            "sin preámbulo ni explicación:" + chr(10) + chr(10) + body
        )
        messages: list[Message] = []
        if self.system_prompt and self.system_prompt.strip():
            messages.append(Message(role="system", content=self.system_prompt))
        messages.append(Message(role="user", content=prompt))
        response = await self.provider.complete(
            messages,
            model=self.model,
            max_tokens=_SUBJECT_MAX_TOKENS,
            temperature=_SUBJECT_TEMPERATURE,
        )
        tokens, cost = _usage_of(response)
        return SubjectOutput(
            output=response.content or "",
            tokens=tokens,
            cost_usd=cost,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
