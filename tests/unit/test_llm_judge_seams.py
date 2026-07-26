"""El juez y el sujeto REALES de los evals (`task_wf_52b`).

El subsistema de evals estaba entero —módulos, tablas, endpoints, dashboard— y
sus tablas vacías: el motor pedía un `JudgeModel` y un `SubjectModel` y la
única implementación era el doble de test. Estos son los adaptadores que lo
conectan a la capa de proveedores del sistema.

El test que más importa aquí es el de la temperatura del juez: si un juez
puntúa distinto el mismo par dos veces, comparar dos releases deja de medir
nada.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from api_server.evals.judge import JudgeCallResult, JudgeModel, SubjectModel, SubjectOutput
from api_server.evals.llm_judge import LLMJudgeModel, LLMSubjectModel


class _Provider:
    """Proveedor de mentira que registra cómo se le llamó."""

    def __init__(self, content: str = "{}", usage: Any = None) -> None:
        self.content = content
        self.usage = usage
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages: list[Any], **kwargs: Any) -> Any:
        self.calls.append({"messages": messages, **kwargs})
        return SimpleNamespace(content=self.content, usage=self.usage)


def _usage(inp: int = 10, out: int = 5, cost: float = 0.002) -> Any:
    return SimpleNamespace(input_tokens=inp, output_tokens=out, cost_usd=cost)


# ---------------------------------------------------------------------------
# Conformidad con los seams del motor
# ---------------------------------------------------------------------------
def test_the_adapters_satisfy_the_engine_protocols() -> None:
    # Si no encajan con el Protocol, `run_eval` los rechaza en runtime y el
    # fallo aparece en producción, no aquí.
    judge = LLMJudgeModel(provider=_Provider(), model="juez")
    subject = LLMSubjectModel(provider=_Provider(), model="sujeto")
    assert isinstance(judge, JudgeModel)
    assert isinstance(subject, SubjectModel)


@pytest.mark.asyncio
async def test_the_judge_returns_the_engines_result_shape() -> None:
    provider = _Provider(content='{"score": 0.8}', usage=_usage())
    out = await LLMJudgeModel(provider=provider, model="juez").judge("prompt")
    assert isinstance(out, JudgeCallResult)
    assert out.text == '{"score": 0.8}'
    assert out.tokens == 15
    assert out.cost_usd == Decimal("0.002")


@pytest.mark.asyncio
async def test_the_subject_returns_the_engines_output_shape() -> None:
    provider = _Provider(content="el entregable", usage=_usage())
    out = await LLMSubjectModel(provider=provider, model="sujeto").produce({"pide": "algo"})
    assert isinstance(out, SubjectOutput)
    assert out.output == "el entregable"
    assert out.tokens == 15


# ---------------------------------------------------------------------------
# Las decisiones que hacen que la medida signifique algo
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_judge_runs_deterministic() -> None:
    # Un juez creativo puntúa distinto el mismo par dos veces y hace inservible
    # la comparación entre releases, que es justo el punto de medir.
    provider = _Provider()
    await LLMJudgeModel(provider=provider, model="juez").judge("p")
    assert provider.calls[0]["temperature"] == 0.0


@pytest.mark.asyncio
async def test_the_subject_does_not_run_frozen() -> None:
    # Al sujeto se le mide cómo se comporta cuando produce de verdad. Medirlo
    # congelado diría poco de cómo se comporta en un run real.
    provider = _Provider()
    await LLMSubjectModel(provider=provider, model="sujeto").produce({"x": 1})
    assert provider.calls[0]["temperature"] > 0


@pytest.mark.asyncio
async def test_each_seam_uses_its_own_model_name() -> None:
    # El motor compara los dos nombres para impedir que un modelo se juzgue a
    # sí mismo; si el adaptador ignorase el nombre, la guarda no valdría nada.
    jp, sp = _Provider(), _Provider()
    await LLMJudgeModel(provider=jp, model="el-juez").judge("p")
    await LLMSubjectModel(provider=sp, model="el-sujeto").produce({})
    assert jp.calls[0]["model"] == "el-juez"
    assert sp.calls[0]["model"] == "el-sujeto"


@pytest.mark.asyncio
async def test_the_subject_prompt_carries_the_item_input() -> None:
    provider = _Provider()
    await LLMSubjectModel(provider=provider, model="s").produce({"titulo": "añadir login"})
    prompt = provider.calls[0]["messages"][0].content
    assert "añadir login" in prompt


# ---------------------------------------------------------------------------
# Robustez: la contabilidad nunca puede tumbar una medición
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_provider_that_reports_no_usage_still_yields_a_verdict() -> None:
    # `cost_usd` es best-effort por contrato de `Usage` (Ollama local da 0
    # siempre). El veredicto del juez vale igual sin la factura.
    provider = _Provider(content='{"score": 1.0}', usage=None)
    out = await LLMJudgeModel(provider=provider, model="j").judge("p")
    assert out.text == '{"score": 1.0}'
    assert out.tokens == 0
    assert out.cost_usd == Decimal("0")


@pytest.mark.asyncio
async def test_an_empty_answer_is_empty_text_not_none() -> None:
    # El motor parsea texto; un `None` reventaría el parser en vez de dar un
    # veredicto no parseable, que es un fallo mucho más difícil de leer.
    out = await LLMJudgeModel(provider=_Provider(content=None), model="j").judge("p")  # type: ignore[arg-type]
    assert out.text == ""
