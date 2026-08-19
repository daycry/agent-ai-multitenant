"""La FORMA persistida del veredicto y su agregado (`task_gov_10`).

El test unitario hermano (`tests/unit/test_reject_taxonomy.py`) prueba el
vocabulario y el parseo. Aquí se prueba lo que la casilla pide de verdad y que
ningún test en memoria puede demostrar: que el par `target` x `class` **queda
escrito** donde vive el veredicto, y que desde ahí **se puede agregar** — que era
justamente lo que la prosa no permitía.

Cuatro cosas, y la cuarta es la que evita que el dato mienta:

1. El rechazo persiste los dos ejes en el `payload` del evento `review_comment`.
2. Un rechazo sin clasificar deja las claves presentes y VACÍAS, no ausentes: un
   consumidor no tiene que distinguir «no lo emitió» de «esta versión no lo
   escribía».
3. `reject_breakdown` contesta «¿qué se rechaza más?» y «¿qué clase domina?».
4. Contesta también **cuántos rechazos no se pudieron clasificar**, y no cruza
   tenants ni proyectos.

Se prueba llamando a `apply_reviewer_verdict` con una sesión real y leyendo la
tabla con otra conexión: el par tiene que estar en la BD, no en un objeto.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.db.domain import TaskStatus
from api_server.db.reject_taxonomy_repo import reject_breakdown
from api_server.reviewer_bridge import CriterionOutcome, ReviewerVerdict, apply_reviewer_verdict
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(dsn: str, *, tasks: int = 1, tenants: int = 1) -> dict[str, Any]:
    """`tenants` organizaciones, cada una con un proyecto y `tasks` tareas `in_review`.

    Devuelve `{"tenants": [...], "projects": [[...]], "tasks": [[...]]}` — listas
    paralelas, para que un test pueda pedir dos tenants y comprobar que el
    agregado de uno no ve al otro.
    """
    out: dict[str, Any] = {"tenants": [], "projects": [], "tasks": []}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE task_audit_events, executions, task_dependencies, tasks, plans,"
            " agents, projects, organizations RESTART IDENTITY CASCADE"
        )
        for t in range(tenants):
            tenant, project = uuid4(), uuid4()
            await conn.execute(
                "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                tenant,
                f"T{t}",
                f"t-shape-{t}",
            )
            await conn.execute(
                "INSERT INTO projects (id, tenant_id, name, status, worker_config)"
                " VALUES ($1, $2, $3, 'active', '{}'::jsonb)",
                project,
                tenant,
                f"P{t}",
            )
            # Un segundo proyecto del MISMO tenant: sin él no se puede comprobar
            # que el filtro por proyecto acota de verdad.
            project_b = uuid4()
            await conn.execute(
                "INSERT INTO projects (id, tenant_id, name, status, worker_config)"
                " VALUES ($1, $2, $3, 'active', '{}'::jsonb)",
                project_b,
                tenant,
                f"P{t}b",
            )
            task_ids = []
            for i in range(tasks):
                task = uuid4()
                await conn.execute(
                    "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority,"
                    " retry_count, max_retries)"
                    " VALUES ($1, $2, $3, $4, $5, 'medium', 0, 9)",
                    task,
                    tenant,
                    project,
                    f"task {i}",
                    TaskStatus.IN_REVIEW.value,
                )
                task_ids.append(task)
            out["tenants"].append(tenant)
            out["projects"].append([project, project_b])
            out["tasks"].append(task_ids)
        return out
    finally:
        await conn.close()


async def _move_task(dsn: str, task_id: UUID, *, project_id: UUID) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("UPDATE tasks SET project_id = $2 WHERE id = $1", task_id, project_id)
    finally:
        await conn.close()


async def _reset_to_in_review(dsn: str, task_id: UUID) -> None:
    """`apply_reviewer_verdict` sólo actúa sobre `in_review` (guarda de idempotencia)."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "UPDATE tasks SET status = $2 WHERE id = $1", task_id, TaskStatus.IN_REVIEW.value
        )
    finally:
        await conn.close()


async def _payloads(dsn: str, task_id: UUID) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT payload FROM task_audit_events"
            " WHERE task_id = $1 AND kind = 'review_comment' ORDER BY at",
            task_id,
        )
        return [json.loads(r["payload"]) for r in rows]
    finally:
        await conn.close()


async def _apply(url: str, *, task_id: UUID, tenant_id: UUID, verdict: ReviewerVerdict) -> Any:
    engine = create_async_engine(url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            return await apply_reviewer_verdict(
                session, task_id=task_id, tenant_id=tenant_id, verdict=verdict
            )
    finally:
        await engine.dispose()


async def _breakdown(url: str, **kwargs: Any) -> Any:
    engine = create_async_engine(url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session:
            return await reject_breakdown(session, **kwargs)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# 1-2. La forma persistida
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_rejection_persists_the_two_axes(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """El par viaja al MISMO evento que la prosa, y sobrevive al viaje a la BD."""
    ids = await _seed(migrations_pg_dsn)
    tenant, task = ids["tenants"][0], ids["tasks"][0][0]

    await _apply(
        admin_database_url,
        task_id=task,
        tenant_id=tenant,
        verdict=ReviewerVerdict(
            label="reject",
            failed_criterion="retries are covered",
            what_to_fix="add the regression test",
            reject_targets=("tests", "code"),
            reject_classes=("unproven",),
        ),
    )

    payload = (await _payloads(migrations_pg_dsn, task))[0]
    assert payload["reject_targets"] == ["tests", "code"]
    assert payload["reject_classes"] == ["unproven"]
    # La prosa NO se ha ido: el par es aditivo, no un reemplazo. Sin esta
    # aserción, un cambio que se llevara `what_to_fix` por delante pasaría en
    # verde y el implementador se quedaría sin saber qué arreglar.
    assert payload["what_to_fix"] == "add the regression test"
    assert payload["failed_criterion"] == "retries are covered"


@pytest.mark.asyncio
async def test_an_unclassified_rejection_writes_the_keys_empty(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Vacías, no ausentes.

    Si faltaran, un lector tendría que distinguir «el reviewer no clasificó» de
    «esta fila la escribió una versión anterior», y el agregado tendría que
    adivinar. Presentes y vacías es la única forma en que `unlabelled` significa
    algo.
    """
    ids = await _seed(migrations_pg_dsn)
    tenant, task = ids["tenants"][0], ids["tasks"][0][0]

    await _apply(
        admin_database_url,
        task_id=task,
        tenant_id=tenant,
        verdict=ReviewerVerdict(label="reject", what_to_fix="algo"),
    )

    payload = (await _payloads(migrations_pg_dsn, task))[0]
    assert payload["reject_targets"] == []
    assert payload["reject_classes"] == []


@pytest.mark.asyncio
async def test_an_approve_payload_is_untouched(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Un APPROVE con desglose sigue escribiendo lo de siempre y nada más.

    No hay `target` de un rechazo que no hubo, y la constancia de aprobación
    (`task_wf_61`) no se contamina con claves de rechazo — es lo que permite al
    agregado distinguir aprobaciones de rechazos sin heurística.
    """
    ids = await _seed(migrations_pg_dsn)
    tenant, task = ids["tenants"][0], ids["tasks"][0][0]

    await _apply(
        admin_database_url,
        task_id=task,
        tenant_id=tenant,
        verdict=ReviewerVerdict(
            label="approve",
            criteria=(CriterionOutcome(text="c1", passed=True, evidence="tests green"),),
        ),
    )

    payload = (await _payloads(migrations_pg_dsn, task))[0]
    assert payload["approved"] is True
    assert "reject_targets" not in payload
    assert "reject_classes" not in payload


# ---------------------------------------------------------------------------
# 3-4. El agregado: la pregunta que antes no se podía hacer
# ---------------------------------------------------------------------------


async def _reject(
    url: str,
    dsn: str,
    *,
    task: UUID,
    tenant: UUID,
    targets: tuple[str, ...] = (),
    classes: tuple[str, ...] = (),
) -> None:
    await _reset_to_in_review(dsn, task)
    await _apply(
        url,
        task_id=task,
        tenant_id=tenant,
        verdict=ReviewerVerdict(
            label="reject",
            what_to_fix="x",
            reject_targets=targets,
            reject_classes=classes,
        ),
    )


@pytest.mark.asyncio
async def test_the_breakdown_answers_what_gets_rejected_most(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    ids = await _seed(migrations_pg_dsn, tasks=4)
    tenant = ids["tenants"][0]
    t = ids["tasks"][0]

    await _reject(
        admin_database_url,
        migrations_pg_dsn,
        task=t[0],
        tenant=tenant,
        targets=("code",),
        classes=("incorrect",),
    )
    await _reject(
        admin_database_url,
        migrations_pg_dsn,
        task=t[1],
        tenant=tenant,
        targets=("code", "tests"),
        classes=("unproven",),
    )
    await _reject(
        admin_database_url,
        migrations_pg_dsn,
        task=t[2],
        tenant=tenant,
        targets=("tests",),
        classes=("unproven",),
    )
    # El cuarto NO se clasifica: es el caso real (el reviewer no emitió los tags,
    # o el worker sintetizó un rechazo defensivo porque no había veredicto).
    await _reject(admin_database_url, migrations_pg_dsn, task=t[3], tenant=tenant)

    got = await _breakdown(admin_database_url, tenant_id=tenant)

    assert got.rejections == 4
    assert got.labelled == 3
    assert got.unlabelled == 1
    assert got.targets == {"code": 2, "tests": 2}
    assert got.classes == {"unproven": 2, "incorrect": 1}
    # La respuesta literal a las dos preguntas de la casilla.
    assert got.top_target in {"code", "tests"}
    assert got.top_class == "unproven"


@pytest.mark.asyncio
async def test_the_breakdown_does_not_cross_tenants(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Dos tenants, dos clases distintas: cada uno ve la suya y sólo la suya."""
    ids = await _seed(migrations_pg_dsn, tasks=1, tenants=2)
    a, b = ids["tenants"]

    await _reject(
        admin_database_url,
        migrations_pg_dsn,
        task=ids["tasks"][0][0],
        tenant=a,
        targets=("code",),
        classes=("incorrect",),
    )
    await _reject(
        admin_database_url,
        migrations_pg_dsn,
        task=ids["tasks"][1][0],
        tenant=b,
        targets=("deliverable",),
        classes=("contract_drift",),
    )

    got_a = await _breakdown(admin_database_url, tenant_id=a)
    got_b = await _breakdown(admin_database_url, tenant_id=b)

    assert got_a.rejections == 1
    assert got_a.targets == {"code": 1}
    assert got_a.classes == {"incorrect": 1}
    assert got_b.targets == {"deliverable": 1}
    assert got_b.classes == {"contract_drift": 1}


@pytest.mark.asyncio
async def test_the_breakdown_can_be_scoped_to_one_project(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """«¿Por qué se rechaza más en ESTE proyecto?» es la pregunta del enunciado."""
    ids = await _seed(migrations_pg_dsn, tasks=2)
    tenant = ids["tenants"][0]
    project_a, project_b = ids["projects"][0]
    t = ids["tasks"][0]
    await _move_task(migrations_pg_dsn, t[1], project_id=project_b)

    await _reject(
        admin_database_url,
        migrations_pg_dsn,
        task=t[0],
        tenant=tenant,
        targets=("code",),
        classes=("incorrect",),
    )
    await _reject(
        admin_database_url,
        migrations_pg_dsn,
        task=t[1],
        tenant=tenant,
        targets=("scope",),
        classes=("overreach",),
    )

    whole = await _breakdown(admin_database_url, tenant_id=tenant)
    only_a = await _breakdown(admin_database_url, tenant_id=tenant, project_id=project_a)

    assert whole.targets == {"code": 1, "scope": 1}
    assert only_a.targets == {"code": 1}
    assert only_a.classes == {"incorrect": 1}
    assert only_a.rejections == 1


@pytest.mark.asyncio
async def test_a_label_outside_the_vocabulary_is_not_counted(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """La mitad de LECTURA del cierre del value-set.

    El par vive en un `payload` JSONB, así que no hay CHECK que impida escribir
    `"frontend"` a mano o desde una versión futura del escritor. Si el agregado
    lo contase, el vocabulario se ensancharía por la puerta de atrás — que es
    exactamente lo que `ck_skills_category` impide cuando el valor sí es una
    columna. Y el rechazo cuenta como NO clasificado, no como clasificado con
    basura.
    """
    ids = await _seed(migrations_pg_dsn)
    tenant, task = ids["tenants"][0], ids["tasks"][0][0]
    await _reject(
        admin_database_url, migrations_pg_dsn, task=task, tenant=tenant, targets=("code",)
    )

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE task_audit_events SET payload = jsonb_set(payload, '{reject_targets}',"
            ' \'["frontend", "otros"]\'::jsonb) WHERE task_id = $1',
            task,
        )
    finally:
        await conn.close()

    got = await _breakdown(admin_database_url, tenant_id=tenant)
    assert got.targets == {}
    assert got.rejections == 1
    assert got.unlabelled == 1, "un rechazo etiquetado con basura no está clasificado"


@pytest.mark.asyncio
async def test_an_old_payload_without_the_keys_does_not_break_the_report(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Las filas escritas ANTES de esta casilla no llevan las claves.

    `jsonb_array_elements_text(NULL)` no devuelve filas, pero un `payload` con la
    clave presente y NO-array reventaría la query: el informe entero se caería
    por una fila. Se prueban las dos formas del pasado.
    """
    ids = await _seed(migrations_pg_dsn, tasks=2)
    tenant = ids["tenants"][0]
    t = ids["tasks"][0]
    await _reject(
        admin_database_url, migrations_pg_dsn, task=t[0], tenant=tenant, targets=("code",)
    )
    await _reject(admin_database_url, migrations_pg_dsn, task=t[1], tenant=tenant)

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # (a) fila vieja: sin las claves.
        await conn.execute(
            "UPDATE task_audit_events SET payload = payload - 'reject_targets'"
            " - 'reject_classes' WHERE task_id = $1",
            t[1],
        )
        # (b) fila corrupta: la clave existe y no es un array.
        await conn.execute(
            "INSERT INTO task_audit_events (id, tenant_id, task_id, kind, actor, payload)"
            " VALUES ($1, $2, $3, 'review_comment', 'agent:reviewer',"
            ' \'{"reject_targets": "code"}\'::jsonb)',
            uuid4(),
            tenant,
            t[1],
        )
    finally:
        await conn.close()

    got = await _breakdown(admin_database_url, tenant_id=tenant)
    assert got.targets == {"code": 1}
    assert got.rejections == 3
    assert got.unlabelled == 2


@pytest.mark.asyncio
async def test_no_rejections_is_an_empty_report_not_a_crash(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    got = await _breakdown(admin_database_url, tenant_id=ids["tenants"][0])
    assert (got.rejections, got.labelled, got.unlabelled) == (0, 0, 0)
    assert got.targets == {} and got.classes == {}
    assert got.top_target is None and got.top_class is None
