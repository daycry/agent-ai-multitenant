"""`POST /eval-runs` — la vía que faltaba para PRODUCIR evals (`task_wf_52b`).

El subsistema estaba construido entero —7 módulos, 7 tablas, 18 endpoints,
dashboard— y sus tablas vacías: el router tenía CRUD de las ENTRADAS (datasets,
criterios, items) y solo lectura de las SALIDAS. No había forma de lanzar una
corrida, así que el dashboard de calidad pintaba un vacío permanente.

El test que más importa de este fichero es
``test_the_judge_never_sees_the_reference_as_the_produced_output``: si el
sujeto no produce y se le pasa al juez el `expected_output` del item como si
fuera la salida, el juez compara la referencia consigo misma y el `pass_rate`
sale 100 % siempre. Un eval que siempre pasa es peor que no tener eval, porque
da confianza.

Pre-condición: postgres (15432) + redis (6379) de docker-compose sanos.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
from uuid import uuid4

import api_server.routers.evals as evals_router
import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.test_eval_endpoints import (
    _auth,
    _seed_tenant,
    _seed_user_with_jwt,
    _truncate_all,
    configured_app,  # noqa: F401 - fixture reutilizada a propósito
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Dobles: ni el juez ni el sujeto llaman a un LLM real en un test
# ---------------------------------------------------------------------------
class _ScriptedJudge:
    """Juez que registra cada prompt y siempre aprueba."""

    def __init__(self, model: str = "modelo-juez") -> None:
        self.model = model
        self.prompts: list[str] = []

    async def judge(self, prompt: str) -> Any:
        from api_server.evals.judge import JudgeCallResult

        self.prompts.append(prompt)
        return JudgeCallResult(
            text='{"score": 1.0, "rationale": "ok"}', tokens=3, cost_usd=Decimal("0")
        )


class _GarbageJudge:
    """Juez que contesta prosa: lo que hace un modelo pequeño de verdad."""

    def __init__(self, model: str = "modelo-juez") -> None:
        self.model = model

    async def judge(self, prompt: str) -> Any:
        from api_server.evals.judge import JudgeCallResult

        return JudgeCallResult(text="Pues me parece que está bastante bien, la verdad.")


class _ScriptedSubject:
    """Sujeto que produce una salida RECONOCIBLE y distinta de la referencia."""

    def __init__(self, model: str = "modelo-sujeto") -> None:
        self.model = model
        self.inputs: list[dict[str, Any]] = []

    async def produce(self, item_input: dict[str, Any]) -> Any:
        from api_server.evals.judge import SubjectOutput

        self.inputs.append(item_input)
        return SubjectOutput(output="LO-QUE-PRODUJO-EL-SUJETO", tokens=4)


def _install_seams(mp: pytest.MonkeyPatch, judge: Any, subject: Any) -> None:
    """Sustituye la resolución de proveedor por los dobles.

    Vía `monkeypatch` y no asignando el atributo a pelo: una mutación de módulo
    sin restaurar se filtra al resto de la sesión de tests y produce fallos
    fantasma en ficheros que no la pidieron.

    ``subject_system_prompt`` es el prompt del agente que la corrida evalúa
    (`task_gov_05`): el doble lo acepta y lo ignora, pero la firma tiene que
    seguir a la real — un doble con una firma vieja hace que el test pase
    probando una llamada que ya nadie hace.
    """

    async def _fake(
        _session: Any,
        _judge_model: str,
        _subject_model: str,
        *,
        subject_system_prompt: str | None = None,
    ) -> tuple[Any, Any]:
        return judge, subject

    mp.setattr(evals_router, "_build_eval_seams", _fake)


def _client(app: object) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _seed_dataset_with_item(
    client: AsyncClient, jwt: str, *, expected: str = "LA-REFERENCIA-DORADA"
) -> str:
    resp = await client.post("/eval-datasets", json={"name": "dorado"}, headers=_auth(jwt))
    assert resp.status_code == 201, resp.text
    dataset_id = resp.json()["id"]
    resp = await client.post(
        f"/eval-datasets/{dataset_id}/criteria",
        json={"name": "correccion", "judge_instruction": "¿resuelve la tarea?"},
        headers=_auth(jwt),
    )
    assert resp.status_code == 201, resp.text
    resp = await client.post(
        f"/eval-datasets/{dataset_id}/items",
        json={"input": {"titulo": "añadir login"}, "expected_output": expected},
        headers=_auth(jwt),
    )
    assert resp.status_code == 201, resp.text
    return dataset_id


def _body(dataset_id: str, **over: Any) -> dict[str, Any]:
    return {
        "dataset_id": dataset_id,
        "subject_model": "modelo-sujeto",
        "judge_model": "modelo-juez",
        **over,
    }


# ---------------------------------------------------------------------------
# El camino feliz
# ---------------------------------------------------------------------------
def test_a_run_judges_the_dataset_and_persists_its_results(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run() -> None:
        await _truncate_all(migrations_pg_dsn)
        tenant = await _seed_tenant(migrations_pg_dsn, slug=f"t{uuid4().hex[:8]}")
        _uid, jwt = await _seed_user_with_jwt(
            migrations_pg_dsn,
            test_redis_url,
            tenant_id=tenant,
            email=f"{uuid4().hex[:8]}@x.io",
            role="tenant_admin",
        )
        judge, subject = _ScriptedJudge(), _ScriptedSubject()
        _install_seams(monkeypatch, judge, subject)

        async with _client(configured_app) as client:
            dataset_id = await _seed_dataset_with_item(client, jwt)
            resp = await client.post("/eval-runs", json=_body(dataset_id), headers=_auth(jwt))
            assert resp.status_code == 201, resp.text
            run = resp.json()
            assert run["status"] == "completed"
            assert run["judge_model"] == "modelo-juez"

            # Las SALIDAS quedan legibles por el dashboard, que era el punto.
            results = await client.get(f"/eval-runs/{run['id']}/results", headers=_auth(jwt))
            assert results.status_code == 200, results.text
            rows = results.json()
            assert len(rows) == 1
            # El desglose tiene que decir QUÉ criterio se juzgó. La fila
            # persiste `criterion_id`, no el nombre: sin resolverlo aquí, la
            # pantalla mostraría filas idénticas tituladas con un UUID.
            assert [c["name"] for c in rows[0]["criterion_scores"]] == ["correccion"]

    asyncio.run(_run())


def test_the_judge_never_sees_the_reference_as_the_produced_output(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La trampa silenciosa que este endpoint tenía que evitar.

    Si la salida juzgada fuese el `expected_output` del item, el juez estaría
    comparando la referencia consigo misma: 100 % de aciertos, siempre,
    midiendo nada.
    """

    async def _run() -> None:
        await _truncate_all(migrations_pg_dsn)
        tenant = await _seed_tenant(migrations_pg_dsn, slug=f"t{uuid4().hex[:8]}")
        _uid, jwt = await _seed_user_with_jwt(
            migrations_pg_dsn,
            test_redis_url,
            tenant_id=tenant,
            email=f"{uuid4().hex[:8]}@x.io",
            role="tenant_admin",
        )
        judge, subject = _ScriptedJudge(), _ScriptedSubject()
        _install_seams(monkeypatch, judge, subject)

        async with _client(configured_app) as client:
            dataset_id = await _seed_dataset_with_item(client, jwt)
            resp = await client.post("/eval-runs", json=_body(dataset_id), headers=_auth(jwt))
            assert resp.status_code == 201, resp.text
            run_id = resp.json()["id"]

            # El sujeto fue invocado con la ENTRADA del item…
            assert subject.inputs == [{"titulo": "añadir login"}]
            # …y lo que el juez puntuó es lo que el sujeto produjo.
            assert judge.prompts, "el juez no llegó a ser invocado"
            joined = "\n".join(judge.prompts)
            assert "LO-QUE-PRODUJO-EL-SUJETO" in joined
            # La referencia entra en el prompt como referencia — pero jamás
            # como la salida evaluada.
            assert "LA-REFERENCIA-DORADA" in joined

            results = await client.get(f"/eval-runs/{run_id}/results", headers=_auth(jwt))
            produced = results.json()[0]["produced_output"]
            assert produced == "LO-QUE-PRODUJO-EL-SUJETO"
            assert produced != "LA-REFERENCIA-DORADA"

    asyncio.run(_run())


def test_the_prompt_version_travels_into_the_run(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `EvalRun.subject_prompt_version` existía desde el Plan 14 y nadie lo
    # poblaba: los rollups agrupaban todo bajo «(sin versión)» y la calidad no
    # se podía atribuir a un cambio de prompt.
    async def _run() -> None:
        await _truncate_all(migrations_pg_dsn)
        tenant = await _seed_tenant(migrations_pg_dsn, slug=f"t{uuid4().hex[:8]}")
        _uid, jwt = await _seed_user_with_jwt(
            migrations_pg_dsn,
            test_redis_url,
            tenant_id=tenant,
            email=f"{uuid4().hex[:8]}@x.io",
            role="tenant_admin",
        )
        _install_seams(monkeypatch, _ScriptedJudge(), _ScriptedSubject())

        async with _client(configured_app) as client:
            dataset_id = await _seed_dataset_with_item(client, jwt)
            resp = await client.post(
                "/eval-runs",
                json=_body(dataset_id, subject_prompt_version="abc123def456"),
                headers=_auth(jwt),
            )
            assert resp.status_code == 201, resp.text
            assert resp.json()["subject_prompt_version"] == "abc123def456"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Los rechazos
# ---------------------------------------------------------------------------
def test_an_empty_dataset_is_rejected_instead_of_scoring_a_perfect_run(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Cero items juzgados daría un `pass_rate` de 100 %: el peor dato posible,
    # porque parece perfecto.
    async def _run() -> None:
        await _truncate_all(migrations_pg_dsn)
        tenant = await _seed_tenant(migrations_pg_dsn, slug=f"t{uuid4().hex[:8]}")
        _uid, jwt = await _seed_user_with_jwt(
            migrations_pg_dsn,
            test_redis_url,
            tenant_id=tenant,
            email=f"{uuid4().hex[:8]}@x.io",
            role="tenant_admin",
        )
        _install_seams(monkeypatch, _ScriptedJudge(), _ScriptedSubject())

        async with _client(configured_app) as client:
            resp = await client.post("/eval-datasets", json={"name": "vacío"}, headers=_auth(jwt))
            dataset_id = resp.json()["id"]
            resp = await client.post("/eval-runs", json=_body(dataset_id), headers=_auth(jwt))
            assert resp.status_code == 422, resp.text
            assert resp.json()["detail"]["error"] == "empty_dataset"

    asyncio.run(_run())


def test_a_model_may_not_judge_itself(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run() -> None:
        await _truncate_all(migrations_pg_dsn)
        tenant = await _seed_tenant(migrations_pg_dsn, slug=f"t{uuid4().hex[:8]}")
        _uid, jwt = await _seed_user_with_jwt(
            migrations_pg_dsn,
            test_redis_url,
            tenant_id=tenant,
            email=f"{uuid4().hex[:8]}@x.io",
            role="tenant_admin",
        )
        _install_seams(monkeypatch, _ScriptedJudge(), _ScriptedSubject())

        async with _client(configured_app) as client:
            dataset_id = await _seed_dataset_with_item(client, jwt)
            resp = await client.post(
                "/eval-runs",
                json=_body(dataset_id, judge_model="modelo-sujeto"),
                headers=_auth(jwt),
            )
            assert resp.status_code == 409, resp.text
            assert resp.json()["detail"]["error"] == "same_model_judge"

    asyncio.run(_run())


def test_a_plain_member_may_not_launch_a_run(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Una corrida gasta llamadas de juez: es gasto, y el gasto es del admin.
    async def _run() -> None:
        await _truncate_all(migrations_pg_dsn)
        tenant = await _seed_tenant(migrations_pg_dsn, slug=f"t{uuid4().hex[:8]}")
        _uid, admin_jwt = await _seed_user_with_jwt(
            migrations_pg_dsn,
            test_redis_url,
            tenant_id=tenant,
            email=f"a{uuid4().hex[:8]}@x.io",
            role="tenant_admin",
        )
        _uid2, member_jwt = await _seed_user_with_jwt(
            migrations_pg_dsn,
            test_redis_url,
            tenant_id=tenant,
            email=f"m{uuid4().hex[:8]}@x.io",
            role="tenant_user",
        )
        _install_seams(monkeypatch, _ScriptedJudge(), _ScriptedSubject())

        async with _client(configured_app) as client:
            dataset_id = await _seed_dataset_with_item(client, admin_jwt)
            resp = await client.post(
                "/eval-runs", json=_body(dataset_id), headers=_auth(member_jwt)
            )
            assert resp.status_code == 403, resp.text

    asyncio.run(_run())


@pytest.mark.cross_tenant
def test_a_tenant_cannot_launch_a_run_against_another_tenants_dataset(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run() -> None:
        await _truncate_all(migrations_pg_dsn)
        tenant_a = await _seed_tenant(migrations_pg_dsn, slug=f"a{uuid4().hex[:8]}")
        tenant_b = await _seed_tenant(migrations_pg_dsn, slug=f"b{uuid4().hex[:8]}")
        _u1, jwt_a = await _seed_user_with_jwt(
            migrations_pg_dsn,
            test_redis_url,
            tenant_id=tenant_a,
            email=f"a{uuid4().hex[:8]}@x.io",
            role="tenant_admin",
        )
        _u2, jwt_b = await _seed_user_with_jwt(
            migrations_pg_dsn,
            test_redis_url,
            tenant_id=tenant_b,
            email=f"b{uuid4().hex[:8]}@x.io",
            role="tenant_admin",
        )
        _install_seams(monkeypatch, _ScriptedJudge(), _ScriptedSubject())

        async with _client(configured_app) as client:
            dataset_of_a = await _seed_dataset_with_item(client, jwt_a)
            resp = await client.post("/eval-runs", json=_body(dataset_of_a), headers=_auth(jwt_b))
            assert resp.status_code == 404, resp.text

        # Y no ha quedado ninguna corrida escrita por el intento.
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            assert await conn.fetchval("SELECT count(*) FROM eval_runs") == 0
        finally:
            await conn.close()

    asyncio.run(_run())


@pytest.mark.cross_tenant
def test_the_results_of_a_run_are_invisible_to_another_tenant(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El desglose por item es nuevo (`task_wf_52b`) y es una ruta de LECTURA.

    Que la corrida esté protegida no protege sus resultados: son otra tabla y
    otra consulta. Sin resolver antes el run, un `WHERE run_id = ...` devolvería
    una lista vacía en vez de un 404 — y una lista vacía confirma que el run
    existe, que es justo lo que no debe filtrarse.
    """

    async def _run() -> None:
        await _truncate_all(migrations_pg_dsn)
        tenant_a = await _seed_tenant(migrations_pg_dsn, slug=f"a{uuid4().hex[:8]}")
        tenant_b = await _seed_tenant(migrations_pg_dsn, slug=f"b{uuid4().hex[:8]}")
        _u1, jwt_a = await _seed_user_with_jwt(
            migrations_pg_dsn,
            test_redis_url,
            tenant_id=tenant_a,
            email=f"a{uuid4().hex[:8]}@x.io",
            role="tenant_admin",
        )
        _u2, jwt_b = await _seed_user_with_jwt(
            migrations_pg_dsn,
            test_redis_url,
            tenant_id=tenant_b,
            email=f"b{uuid4().hex[:8]}@x.io",
            role="tenant_admin",
        )
        _install_seams(monkeypatch, _ScriptedJudge(), _ScriptedSubject())

        async with _client(configured_app) as client:
            dataset_id = await _seed_dataset_with_item(client, jwt_a)
            resp = await client.post("/eval-runs", json=_body(dataset_id), headers=_auth(jwt_a))
            assert resp.status_code == 201, resp.text
            run_id = resp.json()["id"]

            # El dueño lo ve…
            mine = await client.get(f"/eval-runs/{run_id}/results", headers=_auth(jwt_a))
            assert mine.status_code == 200
            assert len(mine.json()) == 1

            # …y el otro tenant recibe 404, no una lista vacía.
            theirs = await client.get(f"/eval-runs/{run_id}/results", headers=_auth(jwt_b))
            assert theirs.status_code == 404, theirs.text

    asyncio.run(_run())


def test_a_dataset_too_big_for_one_request_is_refused_up_front(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La corrida se ejecuta DENTRO de la petición.

    Son `items x (1 sujeto + N criterios)` llamadas a modelo. Pasado cierto
    tamaño el request muere por timeout a mitad: el operador ve un 504, la
    transacción se deshace y no queda ni el run ni una explicación. Se dice por
    adelantado, con el número concreto.
    """

    async def _run() -> None:
        await _truncate_all(migrations_pg_dsn)
        tenant = await _seed_tenant(migrations_pg_dsn, slug=f"t{uuid4().hex[:8]}")
        _uid, jwt = await _seed_user_with_jwt(
            migrations_pg_dsn,
            test_redis_url,
            tenant_id=tenant,
            email=f"{uuid4().hex[:8]}@x.io",
            role="tenant_admin",
        )
        _install_seams(monkeypatch, _ScriptedJudge(), _ScriptedSubject())
        monkeypatch.setattr(evals_router, "MAX_SYNC_EVAL_CALLS", 3)

        async with _client(configured_app) as client:
            # 2 items x (1 sujeto + 1 criterio) = 4 llamadas > 3.
            dataset_id = await _seed_dataset_with_item(client, jwt)
            resp = await client.post(
                f"/eval-datasets/{dataset_id}/items",
                json={"input": {"titulo": "otra"}, "expected_output": "ref"},
                headers=_auth(jwt),
            )
            assert resp.status_code == 201, resp.text

            resp = await client.post("/eval-runs", json=_body(dataset_id), headers=_auth(jwt))
            assert resp.status_code == 422, resp.text
            detail = resp.json()["detail"]
            assert detail["error"] == "dataset_too_large"
            # El número tiene que estar: «demasiado grande» sin cifra no dice
            # cuánto hay que recortar.
            assert detail["planned_calls"] == 4
            assert "4" in detail["message"]

        # Y no ha quedado un run a medias en la tabla.
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            assert await conn.fetchval("SELECT count(*) FROM eval_runs") == 0
        finally:
            await conn.close()

    asyncio.run(_run())


def test_a_judge_that_answers_prose_says_so_instead_of_a_mute_500(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un modelo pequeño como juez contesta en prosa, no en el JSON del contrato.

    Sin traducirlo, el operador ve un 500 y no sabe que el problema es SU
    elección de juez. Y lo que importa además: la transacción se deshace, así
    que no queda un run colgado en `running` engordando el dashboard.
    """

    async def _run() -> None:
        await _truncate_all(migrations_pg_dsn)
        tenant = await _seed_tenant(migrations_pg_dsn, slug=f"t{uuid4().hex[:8]}")
        _uid, jwt = await _seed_user_with_jwt(
            migrations_pg_dsn,
            test_redis_url,
            tenant_id=tenant,
            email=f"{uuid4().hex[:8]}@x.io",
            role="tenant_admin",
        )
        _install_seams(monkeypatch, _GarbageJudge(), _ScriptedSubject())

        async with _client(configured_app) as client:
            dataset_id = await _seed_dataset_with_item(client, jwt)
            resp = await client.post("/eval-runs", json=_body(dataset_id), headers=_auth(jwt))
            assert resp.status_code == 502, resp.text
            detail = resp.json()["detail"]
            assert detail["error"] == "judge_unparseable"
            assert "modelo-juez" in detail["message"]

        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            assert await conn.fetchval("SELECT count(*) FROM eval_runs") == 0
        finally:
            await conn.close()

    asyncio.run(_run())
