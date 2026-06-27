"""Pydantic schemas for the /executions endpoints (Plan 02 Fase E).

The Timeline UI (task_02_22) reads an execution and renders its
`steps_log`. `total_cost_usd` is exposed as a float so the browser
need not parse a decimal string.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from api_server.db.domain import Execution


class ExecutionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    tenant_id: UUID
    task_id: UUID
    agent_id: UUID | None

    status: str
    abort_code: str | None
    output: str | None
    # ADR 0087: the agent's structured finish status (success|failed|partial) or
    # None — a hint rendered as a badge in the Runs detail.
    finish_status: str | None = None

    steps_log: list[Any]
    iterations: int
    total_tokens: int
    total_cost_usd: float
    tool_call_count: int
    model_call_count: int

    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


def to_execution_response(execution: Execution) -> ExecutionResponse:
    return ExecutionResponse(
        id=execution.id,
        tenant_id=execution.tenant_id,
        task_id=execution.task_id,
        agent_id=execution.agent_id,
        status=execution.status,
        abort_code=execution.abort_code,
        output=execution.output,
        finish_status=execution.finish_status,
        steps_log=execution.steps_log,
        iterations=execution.iterations,
        total_tokens=execution.total_tokens,
        total_cost_usd=float(execution.total_cost_usd),
        tool_call_count=execution.tool_call_count,
        model_call_count=execution.model_call_count,
        started_at=execution.started_at,
        completed_at=execution.completed_at,
        created_at=execution.created_at,
        updated_at=execution.updated_at,
    )
