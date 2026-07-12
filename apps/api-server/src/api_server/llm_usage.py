"""Registro best-effort del consumo LLM no-run (ADR 0116).

El asistente, el córtex y el planning consumían LLM sin contabilizar. Este
helper vuelca los acumuladores del ``LLMAssistantModel`` del request a
``llm_usage_events``. Best-effort SIEMPRE: la contabilidad jamás rompe un
turno de chat (se loguea y se sigue). Un modelo sin acumuladores (scripted de
tests, seams) simplemente no registra nada.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

_log = structlog.get_logger("api_server.llm_usage")


async def record_llm_usage(
    session: AsyncSession,
    *,
    source: str,
    model_client: Any,
    tenant_id: UUID | None,
    user_id: UUID | None,
) -> None:
    """Persiste el consumo acumulado del request (si lo hay). Best-effort."""
    calls = int(getattr(model_client, "usage_calls", 0) or 0)
    if calls <= 0:
        return
    try:
        from api_server.db.llm_usage import LLMUsageEvent

        cost = float(getattr(model_client, "usage_cost_usd", 0.0) or 0.0)
        session.add(
            LLMUsageEvent(
                tenant_id=tenant_id,
                user_id=user_id,
                source=source,
                provider_kind=getattr(model_client, "provider_kind", None),
                model=getattr(model_client, "model", None),
                input_tokens=int(getattr(model_client, "usage_input_tokens", 0) or 0),
                output_tokens=int(getattr(model_client, "usage_output_tokens", 0) or 0),
                cost_usd=cost if cost > 0 else None,
                calls=calls,
            )
        )
        await session.flush()
    except Exception as exc:  # la contabilidad nunca rompe el turno
        _log.warning("llm_usage.record_failed", source=source, error=str(exc))
