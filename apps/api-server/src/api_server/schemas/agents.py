"""Pydantic schemas for /agents endpoints.

Field-name note: the DB column is `model_config` (spec §3.1.3) but
Pydantic v2 reserves that name for the per-model BaseModel.model_config
class attribute. We expose it under the Python attribute `llm_config`
with an explicit alias so the JSON contract stays `model_config`.
Callers using Python objects pass `llm_config=...`; HTTP clients see
`model_config` in JSON, both incoming and outgoing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api_server.db.domain import (
    Agent,
    AgentRole,
    AgentScope,
    AgentType,
    MemoryScope,
)
from api_server.evals.prompt_edit_gate import OVERRIDE_MIN_REASON_CHARS

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


class ProviderOption(BaseModel):
    """Un proveedor LLM ACTIVO concreto (fila) seleccionable para el chat del
    proyecto. Sin secretos (la credencial vive en Vault)."""

    model_config = _BASE_CONFIG

    id: UUID
    kind: str
    display_name: str
    slug: str | None = None
    models: list[str] = Field(default_factory=list)
    reasoning_options: list[str] = Field(default_factory=list)


class AgentProviderOptionsResponse(BaseModel):
    """Proveedores ACTIVOS concretos (por nombre) + sus modelos, para el selector
    del «Modelo del chat» (Feature B): a diferencia de ``by_kind``, distingue filas
    del mismo kind (p.ej. Ollama local vs Ollama cloud)."""

    model_config = _BASE_CONFIG

    providers: list[ProviderOption] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
class AgentCreateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    avatar_url: str | None = Field(default=None, max_length=500)
    agent_type: AgentType = AgentType.AI
    role: AgentRole
    system_prompt: str = Field(min_length=1)
    llm_config: dict[str, Any] = Field(default_factory=dict, alias="model_config")
    # None = "no especificado": el endpoint resuelve el default operator-configurable
    # (``memory.default_scope`` en platform_settings) en vez de hardcodear `private`
    # (Plan 06.17 task_06_17_04). Un valor explícito gana sobre el default.
    memory_scope: MemoryScope | None = None
    review_capability: bool = False
    max_concurrent_tasks: int = Field(default=1, ge=1, le=64)
    is_template: bool = False

    # Linked-vs-forked (task_01_03) -- tenant users may only create
    # `project_local` or `global_tenant_template`. `global_builtin` is
    # rejected at the router layer (System-Admin-only path).
    scope: AgentScope = AgentScope.PROJECT_LOCAL
    project_id: UUID | None = None

    @model_validator(mode="after")
    def _scope_project_consistency(self) -> AgentCreateRequest:
        """Mirror the DB CHECK constraint so the API returns 422 instead
        of letting Postgres raise a constraint violation at INSERT time."""
        if self.scope == AgentScope.PROJECT_LOCAL and self.project_id is None:
            raise ValueError("project_id is required when scope='project_local'")
        if self.scope != AgentScope.PROJECT_LOCAL and self.project_id is not None:
            raise ValueError("project_id must be null for non-project_local scopes")
        return self

    @model_validator(mode="after")
    def _validate_model_config(self) -> AgentCreateRequest:
        """Valida ``model_config`` contra el catálogo cerrado (ADR 0055 / 0021).

        Solo valida cuando el body envía un ``model_config`` NO vacío: un ``{}``
        (o "no enviado") pasa porque el endpoint le aplica el default explícito
        operator-configurable. Un proveedor fuera de catálogo, un ``model`` vacío
        o una ``temperature`` fuera de rango → ``422``.
        """
        if self.llm_config:
            from api_server.db.platform_settings import (
                InvalidModelConfigError,
                validate_model_config,
            )

            try:
                validate_model_config(self.llm_config)
            except InvalidModelConfigError as exc:
                raise ValueError(str(exc)) from exc
        return self


# ---------------------------------------------------------------------------
# Update — all fields optional; only sent values are touched
# ---------------------------------------------------------------------------
class EvalGateOverrideRequest(BaseModel):
    """La válvula de escape del gate de evals (`task_gov_05`).

    Es una DIRECTIVA de la petición, no un campo del agente: no se persiste en
    ninguna columna y no tiene efecto sobre nada que no sea el resultado
    ``inconclusive`` de esta misma edición. Sobre una regresión MEDIDA no hace
    nada — si la abriera, el gate sería opcional justo cuando funciona.

    El motivo es obligatorio y largo a propósito: es el mismo listón que
    `CLAUDE.md` §«La excepción al gate» le pone al ``gate_override`` del roadmap,
    y por la misma razón — una excepción sin justificación auditable es la forma
    barata de saltarse la regla. Queda **verbatim** en `audit_log`.
    """

    model_config = _BASE_CONFIG

    # SIN `min_length` en el `Field`, a propósito: con `str_strip_whitespace` ya
    # puesto en `_BASE_CONFIG`, esa restricción cubriría exactamente el mismo caso
    # que el validador de abajo y le quitaría el mensaje — dejándolo como código
    # muerto que parece una guarda. Una sola comprobación, y la que explica.
    reason: str

    @model_validator(mode="after")
    def _explain_the_bar(self) -> EvalGateOverrideRequest:
        if len(self.reason.strip()) < OVERRIDE_MIN_REASON_CHARS:
            raise ValueError(
                "el motivo del override debe tener al menos "
                f"{OVERRIDE_MIN_REASON_CHARS} caracteres con contenido: queda "
                "auditado a tu nombre y alguien tiene que poder entenderlo dentro "
                "de seis meses"
            )
        return self


class AgentUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    avatar_url: str | None = Field(default=None, max_length=500)
    agent_type: AgentType | None = None
    role: AgentRole | None = None
    system_prompt: str | None = Field(default=None, min_length=1)
    llm_config: dict[str, Any] | None = Field(default=None, alias="model_config")
    memory_scope: MemoryScope | None = None
    review_capability: bool | None = None
    max_concurrent_tasks: int | None = Field(default=None, ge=1, le=64)
    is_template: bool | None = None
    # scope + project_id stay set-once. Re-scoping an agent would break
    # the linked-vs-forked invariants; do it via a separate "fork" endpoint
    # (task_01_15).
    anchored_version: str | None = Field(default=None, max_length=32)
    #: Directiva de petición, NO un campo del agente. `update_agent` la saca
    #: antes de aplicar el resto (`apply_partial_update(..., exclude=...)`):
    #: escribirla sobre la fila dejaría un atributo fantasma en el ORM.
    eval_gate_override: EvalGateOverrideRequest | None = None

    @model_validator(mode="after")
    def _validate_model_config(self) -> AgentUpdateRequest:
        """Valida ``model_config`` contra el catálogo cerrado (ADR 0055 / 0021).

        Solo valida cuando el ``PUT`` envía un ``model_config`` NO vacío (un
        update parcial que no toca el modelo lo deja a ``None`` y no se valida).
        Mismas reglas que el create: proveedor fuera de catálogo, ``model`` vacío
        o ``temperature`` fuera de rango → ``422``.
        """
        if self.llm_config:
            from api_server.db.platform_settings import (
                InvalidModelConfigError,
                validate_model_config,
            )

            try:
                validate_model_config(self.llm_config)
            except InvalidModelConfigError as exc:
                raise ValueError(str(exc)) from exc
        return self


# ---------------------------------------------------------------------------
# Fork
# ---------------------------------------------------------------------------
class AgentForkRequest(BaseModel):
    """Clone a visible agent into a project_local copy (spec §5.7).

    `project_id` is mandatory -- a fork always lands in a specific
    project of the calling tenant. Optional `name` and `system_prompt`
    let the caller customize the fork at creation; all other fields
    can be tweaked via PUT afterwards.
    """

    model_config = _BASE_CONFIG

    project_id: UUID
    name: str | None = Field(default=None, min_length=1, max_length=120)
    system_prompt: str | None = Field(default=None, min_length=1)


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------
class AgentTeamRef(BaseModel):
    """Equipo al que pertenece un agente (para badge/filtros/disable — ADR 0071)."""

    model_config = _BASE_CONFIG

    id: UUID
    name: str


class EvalGateNoticeResponse(BaseModel):
    """Lo que el gate de evals tiene que decir sobre una edición (`task_gov_05`).

    Sólo viaja en la respuesta del `PUT` que tocó el prompt; es ``None`` en el
    resto (GET, listado, POST), porque describe una decisión sobre UNA edición y
    no un atributo del agente.

    Existe porque el enunciado dice «en desarrollo y sandbox **se guarda y se
    avisa**»: un aviso que no llega a ninguna pantalla no avisa. Es el modo de
    fallo dominante de esta base (`verificar-antes-de-implementar.md` §5) —
    mecanismo entregado, cero consumidores.
    """

    model_config = _BASE_CONFIG

    outcome: str
    preset: str
    blocking: bool
    message: str
    scenarios: list[str] = Field(default_factory=list)
    dataset_id: UUID | None = None
    baseline_run_id: UUID | None = None
    overridden: bool = False


class AgentResponse(BaseModel):
    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID
    name: str
    description: str | None
    avatar_url: str | None
    agent_type: str
    role: str
    system_prompt: str
    llm_config: dict[str, Any] = Field(alias="model_config")
    memory_scope: str
    # ADR 0071: equipos a los que pertenece (vacío = sin equipo). Para el badge/
    # filtros de la pantalla de Agentes y el disable del memory_scope por-agente.
    teams: list[AgentTeamRef] = Field(default_factory=list)
    review_capability: bool
    max_concurrent_tasks: int
    is_template: bool
    scope: str
    project_id: UUID | None
    forked_from_agent_id: UUID | None
    forked_from_version: str | None
    anchored_version: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    #: Sólo lo rellena el `PUT` que tocó el prompt (`task_gov_05`). Ver
    #: :class:`EvalGateNoticeResponse`.
    eval_gate: EvalGateNoticeResponse | None = None


# ---------------------------------------------------------------------------
# Fork diff
# ---------------------------------------------------------------------------
class AgentFieldDiff(BaseModel):
    model_config = _BASE_CONFIG

    fork: Any
    source: Any


class AgentMergeRequest(BaseModel):
    """Pull selected fields from the source into the fork (spec §5.7).

    `fields` is the list of column names the caller wants to absorb.
    Any field not listed stays untouched on the fork. After a successful
    merge, `anchored_version` is bumped to the source's current
    `updated_at` so subsequent diffs reflect the new baseline.
    """

    model_config = _BASE_CONFIG

    fields: list[str] = Field(default_factory=list)
    #: `task_cv_33` (auditoría 2026-09-01, F-03): además de campos escalares, el
    #: fork puede absorber las CAPACIDADES actuales del origen — sus tools y
    #: skills asignadas —, que es lo que una migración cambia sin tocar texto.
    capabilities: list[Literal["tools", "skills"]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _something_to_merge(self) -> AgentMergeRequest:
        if not self.fields and not self.capabilities:
            raise ValueError("nothing to merge: give `fields` and/or `capabilities`")
        return self


class AgentCapabilitiesDiff(BaseModel):
    """Sets de capacidad de un lado del diff (fork o source).

    Plan 06.17 task_06_17_12: el diff/merge no solo compara los campos escalares
    (``_DIFFABLE_FIELDS``) sino también las CAPACIDADES asignadas, para que la UI
    pueda mostrar qué KBs/tools/skills tiene cada lado. Los ids van como cadenas
    para no acoplar el contrato JSON a la serialización de UUID.
    """

    model_config = _BASE_CONFIG

    kb_ids: list[str] = Field(default_factory=list)
    tool_ids: list[str] = Field(default_factory=list)
    skill_ids: list[str] = Field(default_factory=list)


class AgentDiffResponse(BaseModel):
    """Field-by-field diff between a fork and its source agent.

    `source_moved` is true when the source has been updated since the
    fork point (captured in `forked_from_version`). UI can use this to
    decide whether to offer the "absorb upstream improvements" action.

    `capabilities` expone los sets de KBs/tools/skills de cada lado
    (Plan 06.17 task_06_17_12) — sólo informativo; el merge de campos sigue
    operando sobre ``_DIFFABLE_FIELDS``.
    """

    model_config = _BASE_CONFIG

    fork_id: UUID
    source_id: UUID
    forked_from_version: str | None
    source_current_version: str | None
    source_moved: bool
    source_deleted: bool
    fields: dict[str, AgentFieldDiff]
    capabilities: dict[str, AgentCapabilitiesDiff] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Grant a KB to an agent
# ---------------------------------------------------------------------------
class GrantKBRequest(BaseModel):
    """Payload for POST /agents/{agent_id}/knowledge-bases.

    Replaces the previous raw ``dict[str, str]`` body (api-routers-validation-2):
    a typed schema gives automatic UUID coercion, a standard 422 on a bad
    payload, rejection of unexpected fields (no mass-assignment), and OpenAPI
    docs consistent with the rest of the API.
    """

    model_config = ConfigDict(extra="forbid")

    kb_id: UUID


# ---------------------------------------------------------------------------
# Assign tools to an agent (Plan 06.15 task_06_15_01)
# ---------------------------------------------------------------------------
class AgentToolAssignment(BaseModel):
    """One entry in the declarative `PUT /agents/{id}/tools` payload.

    `config_override` is an optional per-agent JSON blob layered on top of
    the catalog Tool's defaults (mirrors `AgentTool.config_override`).
    """

    model_config = ConfigDict(extra="forbid")

    tool_id: UUID
    config_override: dict[str, Any] | None = None


class SetAgentToolsRequest(BaseModel):
    """Payload for `PUT /agents/{id}/tools` — the full desired set.

    The endpoint is declarative: the agent's `agent_tools` rows are
    replaced wholesale with this list (an empty list clears all
    assignments, restoring the backward-compatible "no per-agent
    restriction" behaviour at enforcement time).
    """

    model_config = ConfigDict(extra="forbid")

    tools: list[AgentToolAssignment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_duplicate_tool_ids(self) -> SetAgentToolsRequest:
        seen: set[UUID] = set()
        for entry in self.tools:
            if entry.tool_id in seen:
                raise ValueError(f"duplicate tool_id in payload: {entry.tool_id}")
            seen.add(entry.tool_id)
        return self


class AgentToolResponse(BaseModel):
    """One assigned Tool, projected to what the assignment UI needs.

    `is_builtin` + `implementation_type` carry the derived "básica vs
    avanzada" taxonomy (Plan 06.15 decision: no new column). The read
    shape mirrors the read-only `agent-tools-diagnostic` panel.
    """

    model_config = ConfigDict(populate_by_name=True)

    tool_id: UUID
    name: str
    description: str | None = None
    category: str
    implementation_type: str
    security_level: str
    is_builtin: bool
    config_override: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Assign skills to an agent (Plan 06.18 task_06_18_13, ADR 0050 Opción A)
# ---------------------------------------------------------------------------
class AgentSkillAssignment(BaseModel):
    """Una entrada del payload declarativo de ``PUT /agents/{id}/skills``."""

    model_config = ConfigDict(extra="forbid")

    skill_id: UUID


class SetAgentSkillsRequest(BaseModel):
    """Payload de ``PUT /agents/{id}/skills`` — el conjunto deseado completo.

    El endpoint es declarativo: las filas ``agent_skills`` del agente se
    reemplazan en bloque con esta lista (una lista vacía limpia todas las
    asignaciones → sin inyección de prompt, comportamiento previo intacto).
    """

    model_config = ConfigDict(extra="forbid")

    skills: list[AgentSkillAssignment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_duplicate_skill_ids(self) -> SetAgentSkillsRequest:
        seen: set[UUID] = set()
        for entry in self.skills:
            if entry.skill_id in seen:
                raise ValueError(f"duplicate skill_id in payload: {entry.skill_id}")
            seen.add(entry.skill_id)
        return self


class AgentSkillResponse(BaseModel):
    """Una Skill asignada, proyectada a lo que la UI de asignación necesita.

    ``prompt_fragment`` se incluye para que la ficha del agente pueda mostrar el
    efecto real (qué se inyectará en el prompt) sin una segunda llamada.
    """

    model_config = ConfigDict(populate_by_name=True)

    skill_id: UUID
    name: str
    category: str
    description: str | None = None
    prompt_fragment: str
    is_builtin: bool


def to_agent_response(a: Agent, teams: list[tuple[UUID, str]] | None = None) -> AgentResponse:
    """ORM -> DTO with the `model_config` rename baked in.

    We go through `model_validate` with a dict because the field is
    aliased (`llm_config` Python name <-> `model_config` JSON key) and
    Pydantic's mypy plugin doesn't expose the field-name kwarg on the
    constructor when an alias is present. The alias is what the API
    contract uses, so the dict key matches the wire format.

    ``teams``: pertenencias del agente (id, nombre) — ADR 0071. El caller las
    resuelve (en batch para el listado); ``None`` = sin dato (lista vacía).
    """
    payload: dict[str, Any] = {
        "id": a.id,
        "tenant_id": a.tenant_id,
        "name": a.name,
        "description": a.description,
        "avatar_url": a.avatar_url,
        "agent_type": a.agent_type,
        "role": a.role,
        "system_prompt": a.system_prompt,
        "model_config": a.model_config,
        "memory_scope": a.memory_scope,
        "teams": [{"id": tid, "name": tname} for tid, tname in (teams or [])],
        "review_capability": a.review_capability,
        "max_concurrent_tasks": a.max_concurrent_tasks,
        "is_template": a.is_template,
        "scope": a.scope,
        "project_id": a.project_id,
        "forked_from_agent_id": a.forked_from_agent_id,
        "forked_from_version": a.forked_from_version,
        "anchored_version": a.anchored_version,
        "created_at": a.created_at,
        "updated_at": a.updated_at,
        "deleted_at": a.deleted_at,
    }
    return AgentResponse.model_validate(payload)


# ---------------------------------------------------------------------------
# Historial del prompt (`task_gov_02`)
# ---------------------------------------------------------------------------
class AgentPromptVersionEntry(BaseModel):
    """Una versión del prompt del agente, con su diff contra la anterior.

    ``changed_by`` a ``None`` no es un dato que falte por descuido: es el autor de
    la fila de BASE, la que registra el prompt que ya existía antes de que hubiera
    historial. Atribuírselo a quien hizo la primera edición sería inventar.

    ``diff`` viene vacío en la fila más antigua de la cadena, que no tiene contra
    qué compararse — el prompt entero ya viaja en ``system_prompt`` / ``persona``,
    así que devolverlo también como diff de adición sería duplicarlo.
    """

    model_config = _BASE_CONFIG

    id: UUID
    agent_id: UUID
    version: int
    system_prompt: str
    persona: dict[str, Any] = Field(default_factory=dict)
    prompt_hash: str
    changed_by: UUID | None = None
    parent_version_id: UUID | None = None
    created_at: datetime
    diff: str = ""


class AgentPromptVersionsResponse(BaseModel):
    """El historial completo de un agente, MÁS RECIENTE PRIMERO."""

    model_config = _BASE_CONFIG

    agent_id: UUID
    #: Sello del texto EFECTIVO del prompt vigente del agente, calculado sobre la
    #: fila viva y no sobre el historial. Es el mismo número que el dispatch manda
    #: al runtime (`task_gov_03`), y estar aquí permite ver de un vistazo si el
    #: agente lleva un prompt que NO está registrado — el caso de un agente que
    #: nunca se editó desde que existe esta tabla.
    current_prompt_hash: str
    versions: list[AgentPromptVersionEntry] = Field(default_factory=list)
