"""La eval bloquea al editar un prompt, según el preset (`task_gov_05`).

`task_gov_04` cerró la mitad de CI —un workflow que vigila **dos ficheros del
repo**—. Ésta es la otra mitad, y es la que un tenant usa de verdad: la pantalla
de Agentes, `PUT /agents/{id}`, donde el prompt cambia sin pasar por ningún
fichero versionado.

Los tres nodos irrenunciables que declara el plan están abajo con ese nombre:

  1. **bloquea en `production`** — y la escritura no ocurre: ni el prompt, ni la
     versión del historial de `task_gov_02`;
  2. **NO bloquea en `development`** — se guarda y se avisa, y el aviso llega a
     la respuesta (un aviso que no llega a ninguna pantalla no avisa de nada);
  3. **el mensaje NOMBRA los escenarios que empeoraron** — no «la eval falló».

Y tres más que no son opcionales aunque el plan no las enumere:

  * la **válvula de escape** abre lo inconcluso y **no** una regresión medida;
  * una **plantilla de tenant** hereda el preset más estricto de los proyectos de
    sus equipos — si no, la puerta trasera es obvia: editar la plantilla en vez
    del agente del proyecto de producción;
  * la **sonda viva** corre el golden set de verdad con el prompt candidato. Sin
    ese test, el gate sería el patrón dominante de esta base
    (`verificar-antes-de-implementar.md` §5): mecanismo entregado, cero
    llamantes reales.

Pre-condición: postgres (15432) + redis de docker-compose sanos.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from api_server.evals.diff import DiffVerdict, ItemChange, MetricDelta, RunDiff
from api_server.evals.prompt_edit_gate import (
    EvalUnavailableError,
    PromptEvalRequest,
)
from httpx import ASGITransport, AsyncClient

from tests.integration.test_eval_endpoints import (
    _auth,
    _seed_tenant,
    _seed_user_with_jwt,
    configured_app,  # noqa: F401 - fixture reutilizada a propósito
)

pytestmark = pytest.mark.integration

#: Un motivo de override que pasa el listón de 80 caracteres de `CLAUDE.md`.
_REASON = (
    "El proveedor del modelo juez lleva caído desde las 09:00 (incidencia INC-4412) "
    "y este cambio corrige una fuga de datos que está en producción ahora mismo."
)

#: Los dos escenarios que empeoran. Sus TÍTULOS son lo que el mensaje de rechazo
#: tiene que nombrar — el nodo (3) del plan.
_SCENARIO_A = "Alta de usuario con SSO"
_SCENARIO_B = "Rechazo de tarjeta caducada"


# ---------------------------------------------------------------------------
# La sonda de test: ni LLM ni corrida real
# ---------------------------------------------------------------------------
@dataclass
class _ScriptedProbe:
    """Devuelve un diff enlatado, o se cae como se cae la infraestructura.

    Mismo seam y mismo motivo que ``DiffProvider`` en el gate de CI: el camino
    decisión→código de respuesta tiene que poder recorrerse sin proveedor.
    """

    diff: RunDiff | None = None
    error: Exception | None = None
    calls: list[PromptEvalRequest] = field(default_factory=list)

    async def measure(self, request: PromptEvalRequest) -> RunDiff:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        assert self.diff is not None, "la sonda no tiene ni diff ni error que devolver"
        return self.diff


def _regression_diff(item_a: UUID, item_b: UUID) -> RunDiff:
    """Dos items que pasaban y ahora fallan; la tasa cae de 1.0 a 0.5."""
    return RunDiff(
        verdict=DiffVerdict.REGRESSED,
        pass_rate=MetricDelta(base=Decimal("1.0"), candidate=Decimal("0.5"), delta=Decimal("-0.5")),
        mean_latency_ms=MetricDelta(None, None, None),
        mean_cost_usd=MetricDelta(None, None, None),
        mean_tokens=MetricDelta(None, None, None),
        regressions=(
            ItemChange(item_id=item_a, base_verdict="pass", candidate_verdict="fail"),
            ItemChange(item_id=item_b, base_verdict="pass", candidate_verdict="fail"),
        ),
        improvements=(),
        pass_rate_regression_threshold=Decimal("0"),
    )


def _improvement_diff() -> RunDiff:
    return RunDiff(
        verdict=DiffVerdict.IMPROVED,
        pass_rate=MetricDelta(base=Decimal("0.5"), candidate=Decimal("1.0"), delta=Decimal("0.5")),
        mean_latency_ms=MetricDelta(None, None, None),
        mean_cost_usd=MetricDelta(None, None, None),
        mean_tokens=MetricDelta(None, None, None),
        regressions=(),
        improvements=(),
        pass_rate_regression_threshold=Decimal("0"),
    )


# ---------------------------------------------------------------------------
# Semilla
# ---------------------------------------------------------------------------
def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _wipe(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE eval_results, eval_runs, eval_criteria, eval_dataset_items, "
            "eval_datasets, agent_prompt_versions, team_members, agents, teams, "
            "projects, audit_log, user_org_memberships, organizations, users "
            "RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


async def _seed(dsn: str, redis_url: str) -> dict[str, Any]:
    """Dos proyectos —uno `production`, otro `development`— con su agente y su
    golden set, más una plantilla de tenant enganchada al equipo del estricto.

    Las políticas se escriben con `preset_policy(...)` del seed, no a mano: un
    mapa copiado en el test seguiría verde mientras el preset real se relaja.
    """
    from api_server.seeds.builtin_approval_policies import preset_policy

    await _wipe(dsn)
    tenant = await _seed_tenant(dsn, slug="gov05")
    _uid, jwt = await _seed_user_with_jwt(
        dsn, redis_url, tenant_id=tenant, email="admin@gov05.example.com", role="tenant_admin"
    )

    ids: dict[str, Any] = {
        "tenant": tenant,
        "jwt": jwt,
        "team_prod": uuid4(),
        "team_dev": uuid4(),
        "project_prod": uuid4(),
        "project_dev": uuid4(),
        "agent_prod": uuid4(),
        "agent_dev": uuid4(),
        "agent_sin_dataset": uuid4(),
        "agent_template": uuid4(),
        "dataset_prod": uuid4(),
        "dataset_dev": uuid4(),
        "dataset_template": uuid4(),
        "item_a": uuid4(),
        "item_b": uuid4(),
        "item_dev_a": uuid4(),
        "item_dev_b": uuid4(),
        "item_tpl_a": uuid4(),
        "baseline_prod": uuid4(),
        "baseline_dev": uuid4(),
        "baseline_tpl": uuid4(),
    }

    conn = await asyncpg.connect(dsn)
    try:
        for key, name in (("team_prod", "Equipo prod"), ("team_dev", "Equipo dev")):
            await conn.execute(
                "INSERT INTO teams (id, tenant_id, name) VALUES ($1,$2,$3)",
                ids[key],
                tenant,
                name,
            )
        for pkey, tkey, name, preset in (
            ("project_prod", "team_prod", "Banca", "production"),
            ("project_dev", "team_dev", "Laboratorio", "development"),
        ):
            await conn.execute(
                "INSERT INTO projects (id, tenant_id, name, team_id, human_approval_policy)"
                " VALUES ($1,$2,$3,$4,$5::jsonb)",
                ids[pkey],
                tenant,
                name,
                ids[tkey],
                json.dumps(preset_policy(preset)),
            )
        for akey, pkey, name in (
            ("agent_prod", "project_prod", "Backend banca"),
            ("agent_dev", "project_dev", "Backend lab"),
            ("agent_sin_dataset", "project_prod", "Sin medir"),
        ):
            await conn.execute(
                "INSERT INTO agents (id, tenant_id, name, role, system_prompt, model_config,"
                " scope, project_id)"
                " VALUES ($1,$2,$3,'backend_dev','PROMPT ORIGINAL',$4::jsonb,"
                " 'project_local',$5)",
                ids[akey],
                tenant,
                name,
                '{"provider": "ollama", "model": "modelo-sujeto"}',
                ids[pkey],
            )
        # La plantilla de tenant: sin `project_id`, pero miembro del equipo cuyo
        # proyecto es el estricto. El gate tiene que juzgarla como estricta.
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, model_config, scope)"
            " VALUES ($1,$2,'Plantilla','backend_dev','PROMPT ORIGINAL',$3::jsonb,"
            " 'global_tenant_template')",
            ids["agent_template"],
            tenant,
            '{"provider": "ollama", "model": "modelo-sujeto"}',
        )
        await conn.execute(
            "INSERT INTO team_members (team_id, agent_id, tenant_id) VALUES ($1,$2,$3)",
            ids["team_prod"],
            ids["agent_template"],
            tenant,
        )

        for dkey, akey, name in (
            ("dataset_prod", "agent_prod", "Dorado banca"),
            ("dataset_dev", "agent_dev", "Dorado lab"),
            ("dataset_template", "agent_template", "Dorado plantilla"),
        ):
            await conn.execute(
                "INSERT INTO eval_datasets (id, tenant_id, name, kind, target_agent_id)"
                " VALUES ($1,$2,$3,'golden',$4)",
                ids[dkey],
                tenant,
                name,
                ids[akey],
            )
        for ikey, dkey, title in (
            ("item_a", "dataset_prod", _SCENARIO_A),
            ("item_b", "dataset_prod", _SCENARIO_B),
            ("item_dev_a", "dataset_dev", _SCENARIO_A),
            ("item_dev_b", "dataset_dev", _SCENARIO_B),
            ("item_tpl_a", "dataset_template", _SCENARIO_A),
        ):
            await conn.execute(
                "INSERT INTO eval_dataset_items (id, tenant_id, dataset_id, input,"
                " expected_output) VALUES ($1,$2,$3,$4::jsonb,'LA REFERENCIA')",
                ids[ikey],
                tenant,
                ids[dkey],
                json.dumps({"title": title, "description": "lo de siempre"}),
            )
        for rkey, dkey in (
            ("baseline_prod", "dataset_prod"),
            ("baseline_dev", "dataset_dev"),
            ("baseline_tpl", "dataset_template"),
        ):
            await conn.execute(
                "INSERT INTO eval_runs (id, tenant_id, dataset_id, status, judge_model,"
                " total_items, passed_items, pass_rate)"
                " VALUES ($1,$2,$3,'completed','modelo-juez',2,2,1.0)",
                ids[rkey],
                tenant,
                ids[dkey],
            )
    finally:
        await conn.close()
    return ids


def _install_probe(app: Any, probe: Any) -> None:
    from api_server.evals.prompt_edit_enforce import get_prompt_eval_probe

    app.dependency_overrides[get_prompt_eval_probe] = lambda: probe


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _prompt_of(dsn: str, agent_id: UUID) -> str:
    conn = await asyncpg.connect(dsn)
    try:
        return str(await conn.fetchval("SELECT system_prompt FROM agents WHERE id = $1", agent_id))
    finally:
        await conn.close()


async def _version_count(dsn: str, agent_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return int(
            await conn.fetchval(
                "SELECT count(*) FROM agent_prompt_versions WHERE agent_id = $1", agent_id
            )
        )
    finally:
        await conn.close()


async def _audit_rows(dsn: str, agent_id: UUID) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT user_id, action, changes FROM audit_log"
            " WHERE resource_id = $1 AND action = 'prompt_eval_gate'"
            " ORDER BY created_at",
            agent_id,
        )
        out = []
        for row in rows:
            changes = row["changes"]
            out.append(
                {
                    "user_id": row["user_id"],
                    "changes": json.loads(changes) if isinstance(changes, str) else changes,
                }
            )
        return out
    finally:
        await conn.close()


# ===========================================================================
# Nodo 1 — bloquea en `production`
# ===========================================================================
def test_a_regression_blocks_the_write_under_the_production_preset(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
) -> None:
    """409, y **nada** se ha escrito: ni el prompt ni la fila del historial.

    Lo segundo es la mitad que se olvida: un gate que responde 409 pero deja el
    `UPDATE` commitado sería peor que no tenerlo, porque el operador se iría
    creyendo que su cambio no entró.
    """

    async def _run() -> None:
        dsn = _plain(migrations_pg_dsn)
        ids = await _seed(dsn, test_redis_url)
        probe = _ScriptedProbe(diff=_regression_diff(ids["item_a"], ids["item_b"]))
        _install_probe(configured_app, probe)

        async with _client(configured_app) as client:
            resp = await client.put(
                f"/agents/{ids['agent_prod']}",
                json={"system_prompt": "PROMPT NUEVO Y PEOR"},
                headers=_auth(ids["jwt"]),
            )

        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "prompt_eval_regression"
        assert detail["preset"] == "production"
        assert await _prompt_of(dsn, ids["agent_prod"]) == "PROMPT ORIGINAL"
        assert await _version_count(dsn, ids["agent_prod"]) == 0, (
            "el rechazo dejó una versión del historial: la transacción no se deshizo"
        )
        # Y el rechazo SOBREVIVE a la transacción deshecha: si la auditoría
        # viajara en ella, un 409 no dejaría rastro ninguno.
        rows = await _audit_rows(dsn, ids["agent_prod"])
        assert len(rows) == 1, rows
        assert rows[0]["changes"]["rejected"] is True
        assert rows[0]["changes"]["outcome"] == "blocked"

    asyncio.run(_run())


# ===========================================================================
# Nodo 3 — el mensaje nombra los escenarios que empeoraron
# ===========================================================================
def test_the_rejection_names_the_scenarios_that_got_worse(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
) -> None:
    """Los TÍTULOS de los items, resueltos de la base a partir del diff.

    Un rechazo mudo («la eval falló») no se puede accionar, y lo que no se puede
    accionar se desactiva — con lo que la feature deja de existir. Los nombres
    van en el TEXTO, no sólo en un campo aparte que un cliente puede no pintar.
    """

    async def _run() -> None:
        dsn = _plain(migrations_pg_dsn)
        ids = await _seed(dsn, test_redis_url)
        _install_probe(
            configured_app, _ScriptedProbe(diff=_regression_diff(ids["item_a"], ids["item_b"]))
        )

        async with _client(configured_app) as client:
            resp = await client.put(
                f"/agents/{ids['agent_prod']}",
                json={"system_prompt": "PROMPT NUEVO Y PEOR"},
                headers=_auth(ids["jwt"]),
            )

        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert _SCENARIO_A in detail["message"]
        assert _SCENARIO_B in detail["message"]
        assert detail["scenarios"] == [_SCENARIO_A, _SCENARIO_B]
        # Y dice que la válvula NO abre esto: si no lo dijera, el paso siguiente
        # del usuario sería bajar el preset del proyecto, que es el agujero
        # grande, permanente y sin auditar.
        assert "NO aplica" in detail["message"]

    asyncio.run(_run())


# ===========================================================================
# Nodo 2 — NO bloquea en `development`
# ===========================================================================
def test_the_same_regression_only_warns_under_the_development_preset(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
) -> None:
    """Se guarda **y se avisa** — y el aviso llega a la respuesta.

    La mitad «se avisa» es la que esta base se suele dejar sin hacer: medir,
    escribirlo en ningún sitio y llamarlo hecho. El aviso nombra los mismos
    escenarios y dice explícitamente que bajo un preset estricto esto se habría
    rechazado, para que el que lo lea sepa qué le espera al promocionar.
    """

    async def _run() -> None:
        dsn = _plain(migrations_pg_dsn)
        ids = await _seed(dsn, test_redis_url)
        _install_probe(
            configured_app,
            _ScriptedProbe(diff=_regression_diff(ids["item_dev_a"], ids["item_dev_b"])),
        )

        async with _client(configured_app) as client:
            resp = await client.put(
                f"/agents/{ids['agent_dev']}",
                json={"system_prompt": "PROMPT NUEVO Y PEOR"},
                headers=_auth(ids["jwt"]),
            )

        assert resp.status_code == 200, resp.text
        notice = resp.json()["eval_gate"]
        assert notice["outcome"] == "blocked", (
            "el hallazgo es el mismo; lo que cambia es qué se hace"
        )
        assert notice["blocking"] is False
        assert notice["preset"] == "development"
        assert notice["scenarios"] == [_SCENARIO_A, _SCENARIO_B]
        assert _SCENARIO_A in notice["message"]
        assert "production" in notice["message"]
        # Y se guardó de verdad, con su versión en el historial de `task_gov_02`.
        assert await _prompt_of(dsn, ids["agent_dev"]) == "PROMPT NUEVO Y PEOR"
        assert await _version_count(dsn, ids["agent_dev"]) == 2

    asyncio.run(_run())


# ===========================================================================
# El camino que deja pasar
# ===========================================================================
def test_an_improvement_is_saved_under_production(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
) -> None:
    async def _run() -> None:
        dsn = _plain(migrations_pg_dsn)
        ids = await _seed(dsn, test_redis_url)
        _install_probe(configured_app, _ScriptedProbe(diff=_improvement_diff()))

        async with _client(configured_app) as client:
            resp = await client.put(
                f"/agents/{ids['agent_prod']}",
                json={"system_prompt": "PROMPT NUEVO Y MEJOR"},
                headers=_auth(ids["jwt"]),
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["eval_gate"]["outcome"] == "passed"
        assert await _prompt_of(dsn, ids["agent_prod"]) == "PROMPT NUEVO Y MEJOR"
        # Un `passed` limpio NO escribe auditoría: una fila por edición correcta
        # es ruido que entierra las que importan.
        assert await _audit_rows(dsn, ids["agent_prod"]) == []

    asyncio.run(_run())


def test_an_update_that_does_not_touch_the_prompt_never_runs_the_eval(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
) -> None:
    """Subir `max_concurrent_tasks` no puede costar una corrida de evals.

    Es el mismo criterio que usa `task_gov_02` para no abrir versión: lo que
    dispara el gate es que el PROMPT cambie, no que el `PUT` se ejecute.
    """

    async def _run() -> None:
        dsn = _plain(migrations_pg_dsn)
        ids = await _seed(dsn, test_redis_url)
        probe = _ScriptedProbe(diff=_regression_diff(ids["item_a"], ids["item_b"]))
        _install_probe(configured_app, probe)

        async with _client(configured_app) as client:
            resp = await client.put(
                f"/agents/{ids['agent_prod']}",
                json={"max_concurrent_tasks": 4},
                headers=_auth(ids["jwt"]),
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["eval_gate"] is None
            # Reenviar el MISMO prompt tampoco dispara nada.
            resp = await client.put(
                f"/agents/{ids['agent_prod']}",
                json={"system_prompt": "PROMPT ORIGINAL"},
                headers=_auth(ids["jwt"]),
            )
            assert resp.status_code == 200, resp.text

        assert probe.calls == []

    asyncio.run(_run())


def test_an_agent_without_a_golden_set_is_not_gated(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
) -> None:
    """Sin dataset no hay nada que medir — y eso NO es un aprobado ni un bloqueo.

    Bloquear aquí congelaría todos los prompts de todo tenant que aún no haya
    sembrado un golden set, que es la forma más rápida de que alguien apague el
    gate entero. Devolver `passed` sería el verde no ganado que `task_gov_04`
    acaba de quitar del gate de CI.
    """

    async def _run() -> None:
        dsn = _plain(migrations_pg_dsn)
        ids = await _seed(dsn, test_redis_url)
        probe = _ScriptedProbe(diff=_regression_diff(ids["item_a"], ids["item_b"]))
        _install_probe(configured_app, probe)

        async with _client(configured_app) as client:
            resp = await client.put(
                f"/agents/{ids['agent_sin_dataset']}",
                json={"system_prompt": "OTRO PROMPT"},
                headers=_auth(ids["jwt"]),
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["eval_gate"]["outcome"] == "not_gated"
        assert probe.calls == [], "se llamó a la sonda sin dataset contra el que medir"
        assert await _prompt_of(dsn, ids["agent_sin_dataset"]) == "OTRO PROMPT"

    asyncio.run(_run())


# ===========================================================================
# La válvula de escape
# ===========================================================================
def test_an_eval_that_cannot_measure_blocks_production_and_points_at_the_valve(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
) -> None:
    """Fail-closed, pero con la salida ESCRITA en el propio mensaje.

    Un bloqueo sin salida visible es una llamada de soporte y un incentivo a
    apagar el gate; por eso el texto nombra `eval_gate_override` y avisa de que
    queda auditado.
    """

    async def _run() -> None:
        dsn = _plain(migrations_pg_dsn)
        ids = await _seed(dsn, test_redis_url)
        _install_probe(
            configured_app,
            _ScriptedProbe(error=EvalUnavailableError("no hay proveedor LLM activo")),
        )

        async with _client(configured_app) as client:
            resp = await client.put(
                f"/agents/{ids['agent_prod']}",
                json={"system_prompt": "PROMPT NUEVO"},
                headers=_auth(ids["jwt"]),
            )

        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "prompt_eval_inconclusive"
        assert "no hay proveedor LLM activo" in detail["message"]
        assert "eval_gate_override" in detail["message"]
        assert await _prompt_of(dsn, ids["agent_prod"]) == "PROMPT ORIGINAL"

    asyncio.run(_run())


def test_the_valve_opens_the_inconclusive_case_and_leaves_the_reason_verbatim(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
) -> None:
    """Quién: el mismo tenant_admin. Qué queda: una fila con su nombre y su motivo.

    El motivo se guarda **verbatim**: resumirlo dejaría la auditoría diciendo lo
    que el sistema entendió en vez de lo que la persona escribió.
    """

    async def _run() -> None:
        dsn = _plain(migrations_pg_dsn)
        ids = await _seed(dsn, test_redis_url)
        _install_probe(configured_app, _ScriptedProbe(error=EvalUnavailableError("eval caída")))

        async with _client(configured_app) as client:
            resp = await client.put(
                f"/agents/{ids['agent_prod']}",
                json={
                    "system_prompt": "PROMPT NUEVO",
                    "eval_gate_override": {"reason": _REASON},
                },
                headers=_auth(ids["jwt"]),
            )

        assert resp.status_code == 200, resp.text
        notice = resp.json()["eval_gate"]
        assert notice["outcome"] == "inconclusive"
        assert notice["overridden"] is True
        assert await _prompt_of(dsn, ids["agent_prod"]) == "PROMPT NUEVO"

        rows = await _audit_rows(dsn, ids["agent_prod"])
        assert len(rows) == 1, rows
        assert rows[0]["changes"]["override_used"] is True
        assert rows[0]["changes"]["override_reason"] == _REASON
        assert rows[0]["user_id"] is not None, "el override sin autor no es auditoría"

    asyncio.run(_run())


def test_the_valve_does_not_open_a_measured_regression(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
) -> None:
    """La línea que hace que la válvula no sea un agujero.

    Si abriera un `BLOCKED`, el gate sería opcional exactamente cuando funciona
    — y todo lo demás (el motivo escrito, la auditoría) sería teatro.
    """

    async def _run() -> None:
        dsn = _plain(migrations_pg_dsn)
        ids = await _seed(dsn, test_redis_url)
        _install_probe(
            configured_app, _ScriptedProbe(diff=_regression_diff(ids["item_a"], ids["item_b"]))
        )

        async with _client(configured_app) as client:
            resp = await client.put(
                f"/agents/{ids['agent_prod']}",
                json={
                    "system_prompt": "PROMPT NUEVO Y PEOR",
                    "eval_gate_override": {"reason": _REASON},
                },
                headers=_auth(ids["jwt"]),
            )

        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["error"] == "prompt_eval_regression"
        assert await _prompt_of(dsn, ids["agent_prod"]) == "PROMPT ORIGINAL"
        # El intento queda registrado: «adjuntar siempre el override» tiene que
        # ser un patrón visible en la auditoría, no una costumbre invisible.
        rows = await _audit_rows(dsn, ids["agent_prod"])
        assert rows[0]["changes"]["override_used"] is False
        assert rows[0]["changes"]["override_reason"] == _REASON

    asyncio.run(_run())


def test_a_short_override_reason_is_refused_before_anything_runs(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
) -> None:
    """El listón de 80 caracteres es el de `CLAUDE.md`, y se aplica al parsear.

    Sin él, la válvula sería un botón de «sí» y la auditoría diría «porque sí».
    """

    async def _run() -> None:
        dsn = _plain(migrations_pg_dsn)
        ids = await _seed(dsn, test_redis_url)
        probe = _ScriptedProbe(error=EvalUnavailableError("eval caída"))
        _install_probe(configured_app, probe)

        async with _client(configured_app) as client:
            resp = await client.put(
                f"/agents/{ids['agent_prod']}",
                json={"system_prompt": "PROMPT NUEVO", "eval_gate_override": {"reason": "urge"}},
                headers=_auth(ids["jwt"]),
            )

        assert resp.status_code == 422, resp.text
        assert probe.calls == []
        assert await _prompt_of(dsn, ids["agent_prod"]) == "PROMPT ORIGINAL"

    asyncio.run(_run())


def test_a_duplicate_name_still_gets_the_sanitised_conflict_not_a_raw_500(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
) -> None:
    """El orden `flush` → gate, que no es indiferente.

    El gate hace `SELECT`s sobre la sesión del request, y el autoflush de
    SQLAlchemy escribiría el `UPDATE` pendiente desde dentro de ellos. Con el
    gate por delante, un `PUT` que renombra a un nombre ya usado **y** toca el
    prompt sacaría la `IntegrityError` por un camino que no pasa por
    `flush_or_conflict` — o sea un 500 con el mensaje crudo de PostgreSQL, que
    nombra la constraint y trae el `tenant_id` en el `DETAIL:`. La misma fuga que
    `routers/_integrity.py` existe para evitar.
    """

    async def _run() -> None:
        dsn = _plain(migrations_pg_dsn)
        ids = await _seed(dsn, test_redis_url)
        _install_probe(configured_app, _ScriptedProbe(diff=_improvement_diff()))

        async with _client(configured_app) as client:
            resp = await client.put(
                f"/agents/{ids['agent_prod']}",
                # «Sin medir» ya existe en el MISMO proyecto: choca con
                # `uq_agents_tenant_project_name_live`.
                json={"name": "Sin medir", "system_prompt": "PROMPT NUEVO"},
                headers=_auth(ids["jwt"]),
            )

        assert resp.status_code == 409, resp.text
        body = resp.text
        assert "uq_agents" not in body, body
        assert str(ids["tenant"]) not in body, "el 500 crudo filtra el tenant_id"

    asyncio.run(_run())


# ===========================================================================
# La puerta trasera: editar la plantilla en vez del agente del proyecto
# ===========================================================================
def test_a_tenant_template_inherits_the_strictest_preset_of_its_teams_projects(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
) -> None:
    """Una plantilla no tiene proyecto, pero **se ejecuta** en los de sus equipos.

    Tomar el camino cómodo («sin `project_id` ⇒ sólo avisa») dejaría la puerta
    trasera obvia: editar la plantilla en vez del agente del proyecto de
    producción y saltarse el gate entero.
    """

    async def _run() -> None:
        dsn = _plain(migrations_pg_dsn)
        ids = await _seed(dsn, test_redis_url)
        _install_probe(
            configured_app,
            _ScriptedProbe(diff=_regression_diff(ids["item_tpl_a"], ids["item_tpl_a"])),
        )

        async with _client(configured_app) as client:
            resp = await client.put(
                f"/agents/{ids['agent_template']}",
                json={"system_prompt": "PROMPT NUEVO Y PEOR"},
                headers=_auth(ids["jwt"]),
            )

        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["preset"] == "production"
        assert await _prompt_of(dsn, ids["agent_template"]) == "PROMPT ORIGINAL"

    asyncio.run(_run())


# ===========================================================================
# La sonda VIVA: que el mecanismo no sea decorativo
# ===========================================================================
def test_the_live_probe_runs_the_golden_set_with_the_candidate_prompt(
    configured_app,  # noqa: F811
    migrations_pg_dsn: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La corrida candidata existe, se persiste, y el sujeto VE el prompt nuevo.

    Este es el test que impide que `task_gov_05` sea el patrón dominante de esta
    base (mecanismo entregado, cero llamantes). Y comprueba de paso el defecto
    que hacía imposible la tarea entera: hasta ahora `LLMSubjectModel` no mandaba
    ningún mensaje ``system``, así que dos corridas con prompts distintos salían
    iguales y «este cambio de prompt empeora» era una pregunta incontestable.

    La corrida candidata vive en una sesión PROPIA, así que sobrevive al rechazo
    del `PUT` — el operador puede abrirla en el dashboard y ver por qué se le
    dijo que no.
    """

    async def _run() -> None:
        import api_server.routers.evals as evals_router
        from api_server.auth.deps import AuthPrincipal
        from api_server.evals.judge import JudgeCallResult, SubjectOutput
        from api_server.evals.prompt_edit_enforce import LiveEvalProbe

        dsn = _plain(migrations_pg_dsn)
        ids = await _seed(dsn, test_redis_url)

        # Un criterio y resultados de base: sin ellos no hay contra qué diffear.
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "INSERT INTO eval_criteria (id, tenant_id, dataset_id, name, judge_instruction)"
                " VALUES ($1,$2,$3,'correccion','¿resuelve la tarea?')",
                uuid4(),
                ids["tenant"],
                ids["dataset_prod"],
            )
            for item in ("item_a", "item_b"):
                await conn.execute(
                    "INSERT INTO eval_results (id, tenant_id, run_id, item_id, verdict,"
                    " overall_score) VALUES ($1,$2,$3,$4,'pass',1.0)",
                    uuid4(),
                    ids["tenant"],
                    ids["baseline_prod"],
                    ids[item],
                )
        finally:
            await conn.close()

        seen: dict[str, Any] = {}

        class _Judge:
            model = "modelo-juez"

            async def judge(self, prompt: str) -> Any:
                return JudgeCallResult(text='{"score": 0.0, "rationale": "peor"}')

        class _Subject:
            model = "modelo-sujeto"

            async def produce(self, item_input: dict[str, Any]) -> Any:
                return SubjectOutput(output="algo")

        async def _fake_seams(
            _session: Any,
            judge_model: str,
            subject_model: str,
            *,
            subject_system_prompt: str | None = None,
        ) -> tuple[Any, Any]:
            seen["judge_model"] = judge_model
            seen["subject_model"] = subject_model
            seen["subject_system_prompt"] = subject_system_prompt
            return _Judge(), _Subject()

        monkeypatch.setattr(evals_router, "_build_eval_seams", _fake_seams)

        principal = AuthPrincipal(user_id=uuid4(), session_id=uuid4(), tenant_id=ids["tenant"])
        probe = LiveEvalProbe(principal=principal)
        diff = await probe.measure(
            PromptEvalRequest(
                tenant_id=ids["tenant"],
                agent_id=ids["agent_prod"],
                agent_name="Backend banca",
                dataset_id=ids["dataset_prod"],
                baseline_run_id=ids["baseline_prod"],
                candidate_prompt="EL PROMPT CANDIDATO",
                subject_model="modelo-sujeto",
                judge_model="modelo-juez",
                regression_threshold=Decimal("0"),
            )
        )

        # 1. El sujeto corrió CON el prompt candidato, y el juez es el de la base.
        assert seen["subject_system_prompt"] == "EL PROMPT CANDIDATO"
        assert seen["judge_model"] == "modelo-juez"
        # 2. El diff detecta la caída de 2/2 a 0/2 y nombra los dos items.
        assert diff.verdict is DiffVerdict.REGRESSED
        assert {c.item_id for c in diff.regressions} == {ids["item_a"], ids["item_b"]}
        # 3. La corrida candidata quedó PERSISTIDA en su propia transacción.
        conn = await asyncpg.connect(dsn)
        try:
            candidatas = await conn.fetch(
                "SELECT id, status FROM eval_runs WHERE dataset_id = $1 AND id <> $2",
                ids["dataset_prod"],
                ids["baseline_prod"],
            )
        finally:
            await conn.close()
        assert len(candidatas) == 1, candidatas
        assert candidatas[0]["status"] == "completed"

    asyncio.run(_run())
