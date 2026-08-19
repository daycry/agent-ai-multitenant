"""Una casilla marcada no puede declarar un test que no existe.

Cada tarea del roadmap declara sus ``command:`` en un bloque yaml. Cuando la
casilla esta ``[x]``, ese comando es la prueba de que la tarea se verifico. Si el
fichero que nombra no existe, el comando **no puede haber pasado nunca**: pytest
y playwright con un fichero que no casa salen con codigo distinto de cero
(«No tests found», comprobado). O sea que el checkbox afirma una verificacion
imposible.

Medido el 2026-08-19 sobre los 824 comandos declarados del roadmap: **76**
caminos inexistentes en casillas ya marcadas. De ellos, unos 59 tienen un fichero
de nombre parecido —renombrados y consolidaciones, o sea enunciados desfasados— y
17 no tienen ninguno. Esa distincion NO la decide este test: la decide quien
audite cada caso, y hasta entonces las dos poblaciones cuentan como deuda.

Este fichero **no arregla las 76**: las congela, e impide que aparezca la
siguiente. Es el patron de inventario congelado que ya usan
``_GATE_DEBT_2026_07_29`` y ``_DELIVERED_BUT_UNSTARTED_2026_08_12`` en
``test_roadmap_frontmatter.py``. Vigila las dos direcciones: una entrada que deja
de faltar tiene que salir del inventario, o la lista describe un mundo que ya no
existe.

Como se retira una entrada: o se escribe el test, o se corrige el comando para
que nombre el que de verdad cubre esa tarea, con una nota que diga por que. El
caso que destapo esto: ``task_prod16_02`` declaraba ``e2e/lang-toggle.spec.ts``,
que nunca existio; el equivalente real, ``e2e/lang-switcher.spec.ts``, cubre lo
mismo y llevaba meses al lado.
"""

from __future__ import annotations

import re
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
_ROADMAP = _RAIZ / "docs" / "roadmap"

_TOKEN = re.compile(r"[\w./\-\[\]]+\.(?:py|ts|tsx|mjs|js|sh|ps1)\b")
_CMD = re.compile(r"^\s*command:\s*[\"']?(.+?)[\"']?\s*$")
_TAREA = re.compile(r"^#{2,5}\s+`?(task_[\w.]+)`?")
_CASILLA = re.compile(r"^\s*-\s+\[([ xX])\]")

#: Inventario CONGELADO el 2026-08-19: (fichero del plan, tarea, camino que falta).
#: No crece. Al arreglar una entrada, borrala de aqui: hay un test que se pone
#: rojo si sobra.
_DECLARED_TEST_DEBT_2026_08_19: frozenset[tuple[str, str, str]] = frozenset(
    [
        (
            "06-testing-revision-git.md",
            "task_06_07",
            "tests/integration/test_testcontainers_mode.py",
        ),
        (
            "06-testing-revision-git.md",
            "task_06_20b1",
            "tests/integration/test_runtime_pool_model.py",
        ),
        ("06-testing-revision-git.md", "task_06_20b2", "tests/integration/test_pool_assign.py"),
        (
            "06-testing-revision-git.md",
            "task_06_20b2",
            "tests/integration/test_pool_idle_eviction.py",
        ),
        ("06-testing-revision-git.md", "task_06_20b2", "tests/integration/test_pool_queue.py"),
        (
            "06-testing-revision-git.md",
            "task_06_20b3",
            "tests/integration/test_pool_role_switch.py",
        ),
        ("06-testing-revision-git.md", "task_06_20b4", "tests/integration/test_pool_cleanup.py"),
        ("06-testing-revision-git.md", "task_06_20b5", "tests/integration/test_pool_metrics.py"),
        (
            "06-testing-revision-git.md",
            "task_06_20b6",
            "tests/integration/test_worker_uses_pool.py",
        ),
        ("06-testing-revision-git.md", "task_06_34", "tests/integration/test_review_cap.py"),
        ("06.17-capacitacion-agentes.md", "task_06_17_11", "agent-persona.spec.ts"),
        ("06.17-capacitacion-agentes.md", "task_06_17_16", "capability-hub.spec.ts"),
        ("06.18-tools-overhaul.md", "task_06_18_09", "tools-affordance.spec.ts"),
        ("06.18-tools-overhaul.md", "task_06_18_11", "tools-catalog.spec.ts"),
        (
            "06.5-orchestrator-wiring.md",
            "task_06_5_01",
            "tests/integration/test_migration_review_sessions.py",
        ),
        ("07-documentacion-visor.md", "task_07_18", "tests/integration/test_docs_rbac.py"),
        ("08-sso-empresarial.md", "task_08_01", "tests/integration/test_oidc_generic.py"),
        ("08-sso-empresarial.md", "task_08_12", "tests/integration/test_login_discovery.py"),
        ("09-marketplace.md", "task_09_10", "tests/unit/test_tool_format.py"),
        ("09-marketplace.md", "task_09_13", "e2e/playwright-tool-config.spec.ts"),
        (
            "prod-01-despliegue-ejecutable.md",
            "task_prod01_01",
            "tests/smoke/test_app_images_build.py",
        ),
        ("prod-02-ci-en-verde.md", "task_prod_02_12", "tests/integration/test_pool_queue.py"),
        (
            "prod-03-guardrails-validacion-humana.md",
            "task_prod03_01",
            "tests/integration/test_approval_gate_presets.py",
        ),
        (
            "prod-03-guardrails-validacion-humana.md",
            "task_prod03_01",
            "tests/unit/test_approval_categories_contract.py",
        ),
        (
            "prod-03-guardrails-validacion-humana.md",
            "task_prod03_02",
            "tests/integration/test_mcp_tool_gating.py",
        ),
        (
            "prod-03-guardrails-validacion-humana.md",
            "task_prod03_02",
            "tests/unit/test_tool_category_coverage.py",
        ),
        (
            "prod-03-guardrails-validacion-humana.md",
            "task_prod03_03",
            "tests/integration/test_approval_default_policy.py",
        ),
        (
            "prod-03-guardrails-validacion-humana.md",
            "task_prod03_05",
            "tests/unit/test_beat_schedule.py",
        ),
        (
            "prod-03-guardrails-validacion-humana.md",
            "task_prod03_12",
            "tests/integration/test_agent_loop_guardrail_hooks.py",
        ),
        (
            "prod-03-guardrails-validacion-humana.md",
            "task_prod03_12",
            "tests/integration/test_indirect_prompt_injection.py",
        ),
        (
            "prod-03-guardrails-validacion-humana.md",
            "task_prod03_13",
            "tests/integration/test_guardrail_events_from_worker.py",
        ),
        (
            "prod-03-guardrails-validacion-humana.md",
            "task_prod03_14",
            "tests/e2e/test_planning_guardrails_route.py",
        ),
        (
            "prod-03-guardrails-validacion-humana.md",
            "task_prod03_15",
            "tests/e2e/test_customer_external_preset_gates.py",
        ),
        (
            "prod-04-backup-dr-restaurable.md",
            "task_prod_04_08",
            "tests/integration/test_restore_grants.py",
        ),
        (
            "prod-05-rotacion-claves.md",
            "task_prod05_01",
            "tests/integration/test_fernet_rotation_two_keys.py",
        ),
        (
            "prod-05-rotacion-claves.md",
            "task_prod05_03",
            "tests/integration/test_mfa_key_rotation_story.py",
        ),
        (
            "prod-05-rotacion-claves.md",
            "task_prod05_04",
            "tests/integration/test_agent_token_survives_rotation.py",
        ),
        (
            "prod-05-rotacion-claves.md",
            "task_prod05_05",
            "tests/integration/test_rotation_never_succeeds_on_fake.py",
        ),
        (
            "prod-05-rotacion-claves.md",
            "task_prod05_06",
            "tests/integration/test_rotation_propagation_cycle.py",
        ),
        (
            "prod-05-rotacion-claves.md",
            "task_prod05_08",
            "tests/integration/test_restore_v1_blob_after_rotation.py",
        ),
        ("prod-05-rotacion-claves.md", "task_prod05_10", "tests/e2e/test_key_rotation_drill.py"),
        (
            "prod-06-ciclo-vida-ejecucion.md",
            "task_prod06_dag_02",
            "tests/e2e/test_plan_autonomous_lifecycle.py",
        ),
        (
            "prod-06-ciclo-vida-ejecucion.md",
            "task_prod06_evento_01",
            "tests/integration/test_ready_task_resweep.py",
        ),
        (
            "prod-06-ciclo-vida-ejecucion.md",
            "task_prod06_zombi_02",
            "tests/integration/test_worker_lost_redelivery.py",
        ),
        (
            "prod-06-ciclo-vida-ejecucion.md",
            "task_prod06_zombi_03",
            "tests/unit/test_celery_broker_options.py",
        ),
        (
            "prod-08-observabilidad-alertas.md",
            "task_prod08_celery_logging_09",
            "apps/notification-dispatcher/tests/test_logging_pipeline.py",
        ),
        (
            "prod-08-observabilidad-alertas.md",
            "task_prod08_celery_logging_09",
            "apps/workers/tests/test_logging_pipeline.py",
        ),
        (
            "prod-08-observabilidad-alertas.md",
            "task_prod08_dashboards_07",
            "tests/unit/test_grafana_dashboards_valid_json.py",
        ),
        (
            "prod-08-observabilidad-alertas.md",
            "task_prod08_loki_deploy_12",
            "tests/integration/test_monitoring_compose_loki.py",
        ),
        (
            "prod-08-observabilidad-alertas.md",
            "task_prod08_metrics_api_04",
            "tests/integration/test_metrics_endpoint.py",
        ),
        (
            "prod-08-observabilidad-alertas.md",
            "task_prod08_metrics_workers_05",
            "apps/workers/tests/test_metrics_exporter.py",
        ),
        (
            "prod-08-observabilidad-alertas.md",
            "task_prod08_request_id_10",
            "tests/integration/test_request_id_propagation.py",
        ),
        (
            "prod-08-observabilidad-alertas.md",
            "task_prod08_scrape_rules_06",
            "tests/integration/test_prometheus_rules_lint.py",
        ),
        (
            "prod-09-sesiones-autorizacion-frontend.md",
            "task_prod09_12",
            "tests/integration/test_ws_ticket_auth.py",
        ),
        (
            "prod-10-vault-secretos-operables.md",
            "task_prod10_02",
            "tests/integration/test_init_vault_script.py",
        ),
        (
            "prod-10-vault-secretos-operables.md",
            "task_prod10_06",
            "tests/integration/test_redis_requires_password.py",
        ),
        (
            "prod-10-vault-secretos-operables.md",
            "task_prod10_07",
            "tests/integration/test_vault_token_renewal.py",
        ),
        (
            "prod-10-vault-secretos-operables.md",
            "task_prod10_08",
            "tests/integration/test_vault_service_tokens.py",
        ),
        (
            "prod-10-vault-secretos-operables.md",
            "task_prod10_09",
            "tests/integration/test_system_health_vault_sealed.py",
        ),
        (
            "prod-10-vault-secretos-operables.md",
            "task_prod10_11",
            "tests/integration/test_sso_notification_webhook_secrets_vault.py",
        ),
        (
            "prod-12-hardening-tools-agentes.md",
            "task_prod12_allow_01",
            "tests/integration/test_execution_request_allowed_domains.py",
        ),
        (
            "prod-12-hardening-tools-agentes.md",
            "task_prod12_docker_01",
            "tests/unit/test_docker_command_tool_retired.py",
        ),
        (
            "prod-12-hardening-tools-agentes.md",
            "task_prod12_net_01",
            "tests/integration/test_network_policy_open_egress.py",
        ),
        (
            "prod-12-hardening-tools-agentes.md",
            "task_prod12_ssrf_01",
            "tests/unit/test_http_tools_destination_validation.py",
        ),
        (
            "prod-12-hardening-tools-agentes.md",
            "task_prod12_ssrf_01",
            "tests/unit/test_ssrf_guard.py",
        ),
        (
            "prod-12-hardening-tools-agentes.md",
            "task_prod12_ssrf_02",
            "tests/unit/test_http_tools_dns_pinning_redirects.py",
        ),
        (
            "prod-13-rendimiento-y-datos.md",
            "task_prod13_10",
            "tests/integration/test_chunks_fts_index_es_unaccent.py",
        ),
        (
            "prod-13-rendimiento-y-datos.md",
            "task_prod13_15",
            "tests/integration/test_append_only_retention.py",
        ),
        (
            "prod-13-rendimiento-y-datos.md",
            "task_prod13_17",
            "tests/integration/test_pagination_conversations_docs_citations.py",
        ),
        (
            "prod-13-rendimiento-y-datos.md",
            "task_prod13_20",
            "tests/integration/test_assistant_chat_rate_limit.py",
        ),
        (
            "prod-13-rendimiento-y-datos.md",
            "task_prod13_23",
            "tests/integration/test_integrity_error_sanitized.py",
        ),
        (
            "prod-17-bucle-ai-reviewer.md",
            "task_prod17_loop_02",
            "tests/unit/test_review_spec_builder.py",
        ),
        (
            "prod-17-bucle-ai-reviewer.md",
            "task_prod17_test_01",
            "tests/integration/test_test_runtime_wiring.py",
        ),
        (
            "prod-18-worktree-en-ejecucion.md",
            "task_prod18_commit_01",
            "tests/integration/test_execution_commits_to_worktree.py",
        ),
        (
            "prod-18-worktree-en-ejecucion.md",
            "task_prod18_test_01",
            "tests/integration/test_test_runtime_wiring.py",
        ),
        (
            "remediacion-auditoria-integral-2026-07-14.md",
            "task_audit14_06",
            "tests/docs/test_worker_db_factory_contract.py",
        ),
    ]
)


def _existe(token: str) -> bool:
    base = token.split("::", maxsplit=1)[0]
    for pref in ("", "apps/admin-panel/", "apps/api-server/", "apps/installer/"):
        for t in (token, base):
            if (_RAIZ / (pref + t)).exists():
                return True
    return False


def _declarados_que_faltan() -> set[tuple[str, str, str]]:
    """(plan, tarea, camino) de cada comando de una casilla MARCADA cuyo fichero
    no existe."""
    faltan: set[tuple[str, str, str]] = set()
    for md in sorted(_ROADMAP.glob("*.md")):
        tarea, estado = "?", None
        for linea in md.read_text(encoding="utf-8").split("\n"):
            m_t = _TAREA.match(linea)
            if m_t:
                tarea, estado = m_t.group(1), None
                continue
            m_c = _CASILLA.match(linea)
            if m_c and estado is None:
                estado = "x" if m_c.group(1).lower() == "x" else " "
                continue
            m = _CMD.match(linea)
            if m is None or estado != "x":
                continue
            for token in _TOKEN.findall(m.group(1)):
                if token.startswith(("npx", "node_modules")):
                    continue
                if not _existe(token):
                    faltan.add((md.name, tarea, token))
    return faltan


def test_the_discovery_actually_finds_the_declared_commands() -> None:
    """No-vacuidad: si el parseo se rompe, los dos tests de abajo pasan solos.

    Se afirma sobre el UNIVERSO (comandos declarados), no sobre los que faltan:
    el dia que las 76 se arreglen el inventario quedara vacio y eso esta bien,
    pero que no haya NI UN comando declarado solo puede ser un parser roto.
    """
    total = 0
    for md in _ROADMAP.glob("*.md"):
        for linea in md.read_text(encoding="utf-8").split("\n"):
            if _CMD.match(linea):
                total += 1
    assert total >= 500, f"esperaba cientos de comandos declarados, encontre {total}"


def test_no_new_marked_task_declares_a_test_that_does_not_exist() -> None:
    nuevas = _declarados_que_faltan() - _DECLARED_TEST_DEBT_2026_08_19
    assert not nuevas, (
        "casillas MARCADAS que declaran un test cuyo fichero no existe y que NO"
        " estaban en el inventario del 2026-08-19:\n"
        + "\n".join(f"  {plan} :: {tarea} -> {token}" for plan, tarea, token in sorted(nuevas))
        + "\n\nUn comando que nombra un fichero inexistente no puede haber pasado:"
        " pytest y playwright salen != 0 con «No tests found». O escribes el test,"
        " o corriges el comando para que nombre el que de verdad cubre la tarea."
    )


def test_the_inventory_has_no_dead_entries() -> None:
    """Una entrada que ya no falta describe un mundo que no existe."""
    vivas = _declarados_que_faltan()
    muertas = _DECLARED_TEST_DEBT_2026_08_19 - vivas
    assert not muertas, (
        "estas entradas del inventario YA no faltan (se escribio el fichero, se"
        " corrigio el comando, o se desmarco la casilla). Borralas del"
        " inventario:\n"
        + "\n".join(f"  {plan} :: {tarea} -> {token}" for plan, tarea, token in sorted(muertas))
    )
