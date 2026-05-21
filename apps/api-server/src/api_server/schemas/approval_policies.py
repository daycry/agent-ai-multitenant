"""Pydantic schemas for /approval-policies (task_01_23 substrate).

Read-only catalog endpoint: surfaces the four built-in presets
(Sandbox / Desarrollo / Producción / Cliente Externo) so the admin
panel can render its policy-configuration screen without a write path
through tid-claim issuance.

Tenants pick a preset and the categories JSON gets *copied* into
`projects.human_approval_policy`, so editing a project's policy never
mutates this catalog.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from api_server.db.domain import ApprovalPolicyTemplate

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


class ApprovalPolicyResponse(BaseModel):
    model_config = _BASE_CONFIG

    id: UUID
    name: str
    description: str | None
    is_builtin: bool
    categories: dict[str, Any]


def to_approval_policy_response(p: ApprovalPolicyTemplate) -> ApprovalPolicyResponse:
    return ApprovalPolicyResponse(
        id=p.id,
        name=p.name,
        description=p.description,
        is_builtin=p.is_builtin,
        categories=p.categories,
    )
