"""prod-03 task_prod03_06 / ADR 0135 — aprobar autoriza la acción, y el bucle acaba.

Antes de esto **aprobar no autorizaba nada**. El ADR 0020 lo dejó escrito:
aprobar → `backlog` → el dispatcher monta un spec nuevo que solo lleva
`approval_policy` → el gate, sin memoria, vuelve a aparcar la MISMA acción. Y el
bucle no estaba acotado por nada: `resolve_approval` no tocaba `retry_count` y
los presupuestos son POR EJECUCIÓN, así que cada re-despacho estrenaba techo de
tokens entero.

Este fichero fija la decisión del operador (G1+S1+T1+N3) por los ocho puntos de
verificación del ADR 0135. Los que no necesitan base de datos —consumo T1,
alias, args no serializables— viven en `tests/unit/test_approval_gate_authorized_
actions.py`; aquí está lo que solo se puede afirmar con filas reales: qué emite
el lector, qué NO emite (otra task, otro tenant), y qué hace el contador.

El criterio NEGATIVO que el ADR exige está en
`test_without_the_list_the_same_action_is_parked`: si borro la lista del spec y
el caso 1 sigue verde, el caso 1 no vale nada.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from agent_runtime.approval import ApprovalGate
from alembic import command
from api_server.db.approval_repo import (
    read_approved_actions,
    request_approval_if_needed,
    resolve_approval,
)
from api_server.db.domain import ApprovalRequest, Execution, Project, Task
from api_server.db.models import Organization, TaskAuditEvent, User
from shared_domain.approval_categories import APPROVAL_CATEGORIES
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 7, 31, 12, 0, 0, tzinfo=UTC)
_STRICT_POLICY: dict[str, Any] = {
    "categories": dict.fromkeys(APPROVAL_CATEGORIES, "human_required")
}
_WRITE_ARGS = {"path": "src/app.py", "content": "print('hola')\n"}


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(sm: async_sessionmaker) -> dict[str, UUID]:
    """Dos tenants, cada uno con proyecto; el tenant A con DOS tasks.

    El segundo tenant y la segunda task no son decorado: son los dos límites del
    alcance S1 («la misma task») y del Principio nº1 («ninguna query sin
    tenant_id»), y sin filas de verdad no se pueden afirmar.
    """
    ids = {
        "tenant": uuid4(),
        "other_tenant": uuid4(),
        "project": uuid4(),
        "other_project": uuid4(),
        "task": uuid4(),
        "other_task": uuid4(),
        "reviewer": uuid4(),
    }
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE approval_requests, task_audit_events, executions,"
                " task_dependencies, tasks, projects, organizations"
                " RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="Loop tenant", slug="loop-tenant"))
        s.add(Organization(id=ids["other_tenant"], name="Otro", slug="loop-otro"))
        s.add(
            User(id=ids["reviewer"], email=f"rev-{ids['reviewer']}@example.test", password_hash="x")
        )
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="Loop project",
                status="active",
                is_template=False,
                human_approval_policy=_STRICT_POLICY,
            )
        )
        s.add(
            Project(
                id=ids["other_project"],
                tenant_id=ids["other_tenant"],
                name="Otro project",
                status="active",
                is_template=False,
            )
        )
        await s.flush()
        for key, project in (("task", "project"), ("other_task", "project")):
            s.add(
                Task(
                    id=ids[key],
                    tenant_id=ids["tenant"],
                    project_id=ids[project],
                    title=f"Loop {key}",
                    status="awaiting_human_approval",
                    priority="medium",
                )
            )
    return ids


async def _park(
    sm: async_sessionmaker,
    ids: dict[str, UUID],
    *,
    task_key: str = "task",
    tool: str = "write_file",
    args: dict[str, Any] | None = None,
    category: str = "code_changes",
) -> UUID:
    """Aparca una acción como lo hace el worker: execution + `ApprovalRequest`."""
    execution_id = uuid4()
    async with sm() as s, s.begin():
        s.add(
            Execution(
                id=execution_id,
                tenant_id=ids["tenant"],
                task_id=ids[task_key],
                status="awaiting_human_approval",
            )
        )
        await s.flush()
        execution = await s.get(Execution, execution_id)
        project = await s.get(Project, ids["project"])
        assert execution is not None and project is not None
        request = await request_approval_if_needed(
            s,
            execution=execution,
            project=project,
            category=category,
            action={"tool": tool, "args": args if args is not None else dict(_WRITE_ARGS)},
        )
        assert request is not None, "la política estricta debe aparcar esta acción"
        return request.id


async def _approve(sm: async_sessionmaker, ids: dict[str, UUID], request_id: UUID) -> None:
    async with sm() as s, s.begin():
        request = await s.get(ApprovalRequest, request_id)
        assert request is not None
        resolved = await resolve_approval(
            s, request, approved=True, resolver_id=ids["reviewer"], reason="adelante"
        )
        assert resolved is not None


async def _read(sm: async_sessionmaker, ids: dict[str, UUID], task_key: str = "task") -> list[Any]:
    async with sm() as s:
        return await read_approved_actions(s, task_id=ids[task_key], tenant_id=ids["tenant"])


# ---------------------------------------------------------------------------
# Casos 1-3 del ADR: qué queda autorizado y qué no
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_approved_action_is_authorised_and_not_parked_again(
    _migrated: None, admin_database_url: str
) -> None:
    """Caso 1: aparcar → aprobar → re-ejecutar la MISMA acción → la tool corre."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)
        await _approve(sm, ids, await _park(sm, ids))

        approved = await _read(sm, ids)
        assert len(approved) == 1
        assert approved[0]["tool"] == "write_file"
        assert approved[0]["category"] == "code_changes"
        assert len(str(approved[0]["args_hash"])) == 64

        gate = ApprovalGate(_STRICT_POLICY, approved_actions=approved)
        assert gate.review("write_file", dict(_WRITE_ARGS)) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_without_the_list_the_same_action_is_parked(
    _migrated: None, admin_database_url: str
) -> None:
    """El criterio negativo del ADR: sin la lista, el caso 1 DEBE aparcar.

    Es el único test que puede quedar verde por accidente (basta que el doble no
    vuelva a proponer la acción), así que se prueba su contrario explícitamente.
    """
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)
        await _approve(sm, ids, await _park(sm, ids))
        assert await _read(sm, ids), "el lector tiene que estar emitiendo algo"

        gate = ApprovalGate(_STRICT_POLICY)  # ← la lista, borrada
        assert gate.review("write_file", dict(_WRITE_ARGS)) == "code_changes"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_same_tool_other_args_and_other_tool_same_category_are_parked(
    _migrated: None, admin_database_url: str
) -> None:
    """Casos 2 y 3: lo que separa G1 de G2 y de G4.

    Si el hash estuviera autorizando la tool entera, el primero pasaría; si
    estuviera autorizando la categoría, pasarían los dos.
    """
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)
        await _approve(sm, ids, await _park(sm, ids))
        gate = ApprovalGate(_STRICT_POLICY, approved_actions=await _read(sm, ids))

        assert gate.review("write_file", {**_WRITE_ARGS, "path": "otro.py"}) == "code_changes"
        assert gate.review("shell_exec", dict(_WRITE_ARGS)) == "code_changes"
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Caso 5: alcance S1 — la misma task, y solo ella
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_authorisation_does_not_cross_to_another_task(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)
        await _approve(sm, ids, await _park(sm, ids, task_key="task"))

        assert await _read(sm, ids, "task"), "la task A sí tiene su autorización"
        assert await _read(sm, ids, "other_task") == []
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Caso 7: cross-tenant — el predicado explícito es la ÚNICA defensa (BYPASSRLS)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_another_tenants_approval_never_appears(
    _migrated: None, admin_database_url: str
) -> None:
    """Una fila aprobada de OTRO tenant apuntando al mismo `task_id`.

    El lector corre con el rol BYPASSRLS del worker: RLS no acota nada ahí, así
    que el `tenant_id` explícito del WHERE es lo único que separa los datos.
    """
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)
        intruder_execution = uuid4()
        async with sm() as s, s.begin():
            s.add(
                Execution(
                    id=intruder_execution,
                    tenant_id=ids["other_tenant"],
                    task_id=ids["task"],
                    status="awaiting_human_approval",
                )
            )
            await s.flush()
            s.add(
                ApprovalRequest(
                    id=uuid4(),
                    tenant_id=ids["other_tenant"],
                    execution_id=intruder_execution,
                    task_id=ids["task"],
                    project_id=ids["other_project"],
                    category="code_changes",
                    action={"tool": "shell_exec", "args": {"command": "rm -rf /"}},
                    status="approved",
                    resolved_at=_NOW,
                )
            )

        assert await _read(sm, ids, "task") == []

        # Y la guarda no pasa vacía: con el tenant del intruso, la fila SÍ está.
        async with sm() as s:
            intruder = await read_approved_actions(
                s, task_id=ids["task"], tenant_id=ids["other_tenant"]
            )
        assert [entry["tool"] for entry in intruder] == ["shell_exec"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ask_human_answers_are_not_tool_authorisations(
    _migrated: None, admin_database_url: str
) -> None:
    """`human_question` (ADR 0114) viaja por `human_answers`, no por aquí.

    Su `action.args` es la pregunta, no los argumentos de una tool: emitirla
    como acción autorizada le daría al sandbox una capacidad que nadie concedió.
    """
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)
        request_id = await _park(
            sm,
            ids,
            tool="ask_human",
            args={"question": "¿REST o GraphQL?"},
            category="human_question",
        )
        await _approve(sm, ids, request_id)
        assert await _read(sm, ids) == []

        # Y tampoco paga reintento: el raíl de `ask_human` es non-terminal por
        # diseño (ADR 0114). Cobrarle un reintento bloquearía una tarea por
        # hacer tres preguntas legítimas.
        async with sm() as s:
            task = await s.get(Task, ids["task"])
        assert task is not None
        assert task.retry_count == 0
        assert task.status == "backlog"
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# N3: el «casi igual» vuelve a preguntar ENSEÑANDO qué cambió
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_near_miss_request_carries_the_delta_for_the_reviewer(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)
        await _approve(sm, ids, await _park(sm, ids))

        second = await _park(sm, ids, args={**_WRITE_ARGS, "content": "print('hola') \n"})
        async with sm() as s:
            request = await s.get(ApprovalRequest, second)
        assert request is not None
        prior = request.action.get("prior_approvals")
        assert prior is not None, "la segunda solicitud debe llevar el contexto (N3)"
        closest = prior["closest_prior"]
        assert closest["changed_args"]["content"]["before"] == "print('hola')\n"
        assert closest["changed_args"]["content"]["after"] == "print('hola') \n"
        assert "path" not in closest["changed_args"]
        assert prior["same_action_approved_times"] == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_identical_re_request_reports_how_many_times_it_was_approved(
    _migrated: None, admin_database_url: str
) -> None:
    """La otra mitad de la recomendación del ADR: un humano que ve «aprobada 2
    veces» deja de aprobar y llama a alguien."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)
        await _approve(sm, ids, await _park(sm, ids))
        await _approve(sm, ids, await _park(sm, ids))

        third = await _park(sm, ids)
        async with sm() as s:
            request = await s.get(ApprovalRequest, third)
        assert request is not None
        assert request.action["prior_approvals"]["same_action_approved_times"] == 2
        # La acción propuesta sigue intacta: lo que se hashea es lo que la UI
        # enseña, y la anotación NO puede cambiarlo.
        assert request.action["tool"] == "write_file"
        assert request.action["args"] == _WRITE_ARGS
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_first_request_of_a_task_carries_no_annotation(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)
        first = await _park(sm, ids)
        async with sm() as s:
            request = await s.get(ApprovalRequest, first)
        assert request is not None
        assert request.action == {"tool": "write_file", "args": _WRITE_ARGS}
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Caso 8: el bucle deja de ser infinito
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_each_approval_spends_one_retry(_migrated: None, admin_database_url: str) -> None:
    """`resolve_approval` no tocaba `retry_count`: el bucle no tenía techo."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)
        await _approve(sm, ids, await _park(sm, ids))
        async with sm() as s:
            task = await s.get(Task, ids["task"])
        assert task is not None
        assert task.retry_count == 1
        assert task.status == "backlog"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_repeated_approvals_end_blocked_with_a_legible_reason(
    _migrated: None, admin_database_url: str
) -> None:
    """N aprobaciones de la misma acción → `blocked`, no bucle (caso 8)."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)
        async with sm() as s:
            task = await s.get(Task, ids["task"])
            assert task is not None
            max_retries = task.max_retries
        assert max_retries == 3

        for _ in range(max_retries):
            await _approve(sm, ids, await _park(sm, ids))

        async with sm() as s:
            task = await s.get(Task, ids["task"])
            events = list(
                (
                    await s.execute(
                        select(TaskAuditEvent).where(TaskAuditEvent.task_id == ids["task"])
                    )
                ).scalars()
            )
        assert task is not None
        assert task.retry_count == max_retries
        assert task.status == "blocked"
        capped = [e for e in events if e.kind == "approval_retry_capped"]
        assert len(capped) == 1
        assert capped[0].payload["retry_count"] == max_retries
        assert capped[0].payload["same_action_approved_times"] == max_retries - 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_rejection_does_not_spend_a_retry(_migrated: None, admin_database_url: str) -> None:
    """Rechazar ya bloquea la task: cobrarle además un reintento sería contar
    dos veces el mismo final."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)
        request_id = await _park(sm, ids)
        async with sm() as s, s.begin():
            request = await s.get(ApprovalRequest, request_id)
            assert request is not None
            await resolve_approval(s, request, approved=False, resolver_id=ids["reviewer"])
        async with sm() as s:
            task = await s.get(Task, ids["task"])
        assert task is not None
        assert task.retry_count == 0
        assert task.status == "blocked"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_reader_is_bounded(_migrated: None, admin_database_url: str) -> None:
    """La lista es una capacidad que se entrega al sandbox: acotada, y por
    `resolved_at` descendente (las más recientes primero), como el lector
    hermano del ADR 0114."""
    from api_server.db.approval_repo import APPROVED_ACTIONS_MAX

    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)
        execution_id = uuid4()
        async with sm() as s, s.begin():
            s.add(
                Execution(
                    id=execution_id,
                    tenant_id=ids["tenant"],
                    task_id=ids["task"],
                    status="done",
                )
            )
            await s.flush()
            for index in range(APPROVED_ACTIONS_MAX + 3):
                s.add(
                    ApprovalRequest(
                        id=uuid4(),
                        tenant_id=ids["tenant"],
                        execution_id=execution_id,
                        task_id=ids["task"],
                        project_id=ids["project"],
                        category="code_changes",
                        action={"tool": "write_file", "args": {"path": f"f{index}.py"}},
                        status="approved",
                        resolved_at=_NOW + timedelta(minutes=index),
                    )
                )

        approved = await _read(sm, ids)
        assert len(approved) == APPROVED_ACTIONS_MAX
        newest = ApprovalGate(_STRICT_POLICY, approved_actions=approved)
        assert newest.review("write_file", {"path": f"f{APPROVED_ACTIONS_MAX + 2}.py"}) is None, (
            "la más reciente tiene que estar dentro del tope"
        )
    finally:
        await engine.dispose()
