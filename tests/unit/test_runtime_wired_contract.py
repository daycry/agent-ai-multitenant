"""Contrato: lo que se ANUNCIA al modelo tiene que poder EJECUTARSE (B-04).

`RUNTIME_WIRED_TOOL_NAMES` es el filtro que decide qué tools del catálogo
llegan al esquema que ve el LLM (`agent_tool_schemas._catalog_by_canonical`
descarta las que no están). Una tool anunciada sin ejecutor real es una
promesa falsa: el modelo la llama, quema un turno y recibe un error de
plataforma que no puede resolver.

Ya pasó con `kanban_update` / `agent_invoke` (AUD16-02): se las retiró del
ANUNCIO pero NO de esta lista, así que las dos fuentes quedaron divergentes y
`send_notification` —el tercer caso idéntico— siguió anunciándose durante
meses. Este test fija la lista de las que no tienen consumidor para que
añadir una a `RUNTIME_WIRED_TOOL_NAMES` rompa aquí en vez de en un run.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# Las tres tools de orquestación cuyo ejecutor devuelve `ok=False, "not wired"`
# (`agent_runtime.orchestration_tools`): validan sus argumentos y NO emiten
# efecto porque nadie lo drena worker-side. `task_comment`, la cuarta de esa
# familia, SÍ tiene consumidor real y por eso no está aquí.
_NO_DRAIN_TOOLS: frozenset[str] = frozenset({"kanban_update", "agent_invoke", "send_notification"})

# Los cuatro `run_*` (F5 de registry-egress-followups, ADR 0093 D3). Distinto
# motivo, MISMA consecuencia: son `docker_command`, y `DockerCommandTool` dentro
# del sandbox **falla siempre por diseño** —la imagen del agent-runtime no
# instala el paquete `docker` ni recibe socket (`test_docker_command_tool_retired`
# lo fija)—, así que anunciarlas es prometerle al modelo cuatro tools que no
# pueden ejecutarse. La vía real es `stack_exec`: el worker corre el toolchain en
# el runtime-template del proyecto.
#
# El ADR 0093 retiró el grant del equipo CI4 pero las dejó en el catálogo de
# plataforma «por compatibilidad», y así se quedaron: 62 grants vivos en 6 roles
# el 2026-07-28, cada invocación un turno quemado. Es el mismo fallo B-04 que
# `send_notification`, multiplicado por cuatro.
_SANDBOX_IMPOSSIBLE_TOOLS: frozenset[str] = frozenset(
    {"run_pytest", "run_lint", "run_typecheck", "run_build"}
)

#: Todo lo que NO puede anunciarse al modelo, por cualquiera de los dos motivos.
_NOT_WIRED_TOOLS: frozenset[str] = _NO_DRAIN_TOOLS | _SANDBOX_IMPOSSIBLE_TOOLS


def test_not_wired_tools_are_never_advertised_to_the_model() -> None:
    from shared_domain.tool_names import RUNTIME_WIRED_TOOL_NAMES

    offenders = sorted(_NOT_WIRED_TOOLS & RUNTIME_WIRED_TOOL_NAMES)
    assert not offenders, (
        f"{offenders} se anuncian al modelo y su ejecutor devuelve 'not wired'. "
        "O se cablea su consumidor worker-side, o se retiran de "
        "RUNTIME_WIRED_TOOL_NAMES — pero no se le prometen al agente."
    )


def test_is_runtime_wired_filters_them_out() -> None:
    """El filtro que usa `_catalog_by_canonical` las descarta de verdad."""
    from shared_domain.tool_names import is_runtime_wired

    for name in sorted(_NOT_WIRED_TOOLS):
        assert not is_runtime_wired(name), f"{name} pasaría el filtro del anuncio"


def test_task_comment_stays_wired() -> None:
    """Guarda en el sentido contrario: `task_comment` sí tiene drain real (el
    worker lo persiste como comentario del plan al finalizar el run), así que
    retirarla sería una regresión, no una limpieza."""
    from shared_domain.tool_names import RUNTIME_WIRED_TOOL_NAMES, is_runtime_wired

    assert "task_comment" in RUNTIME_WIRED_TOOL_NAMES
    assert is_runtime_wired("task_comment")


# ---------------------------------------------------------------------------
# La puerta de atrás (auditoría adversarial 2026-07-25): retirar una tool de
# `RUNTIME_WIRED_TOOL_NAMES` NO bastaba para dejar de anunciarla.
#
# El drop del catálogo (`_catalog_by_canonical` descarta las no cableadas) deja el
# nombre LIBRE en el índice de esquemas, y el bucle de `tool_specs` lo rellena:
# `serialize_agent_tool_specs` serializa TODAS las filas asignadas salvo
# `shell_exec`, y `_tool_to_spec` les adjunta `input_schema` + `description`. Así
# que a cualquier agente que tuviera `send_notification` concedida se le seguía
# anunciando, con su esquema, y la llamada moría igual.
#
# Las tools de TENANT (custom, MCP del proyecto) sí deben pasar por esa vía: el
# runtime las registra de verdad con `register_tool_specs`. Lo que no puede pasar
# es que un builtin de PLATAFORMA sin ejecutor entre por ahí.
# ---------------------------------------------------------------------------
def test_an_unwired_platform_builtin_cannot_sneak_in_through_a_spec() -> None:
    from workers.agent_tool_schemas import build_model_tool_schemas

    spec = [
        {
            "name": "send_notification",
            "implementation_type": "python_function",
            "config": {},
            "input_schema": {"type": "object"},
            "description": "su ejecutor devuelve 'not wired'",
        }
    ]
    advertised = {
        t["function"]["name"]
        for t in build_model_tool_schemas(
            ["send_notification", "read_file"], spec, include_system_tools=True
        )
    }
    assert "send_notification" not in advertised
    assert "read_file" in advertised, "la tool cableada del mismo allowlist sobrevive"


def test_a_tenant_tool_still_comes_through_its_spec() -> None:
    """La otra cara: las MCP del proyecto y las custom del tenant se anuncian por
    esta misma vía (task_wf_10) y el runtime SÍ las registra."""
    from workers.agent_tool_schemas import build_model_tool_schemas

    spec = [
        {
            "name": "context7.query_docs",
            "implementation_type": "mcp_tool",
            "config": {},
            "input_schema": {"type": "object"},
            "description": "Busca documentación.",
        }
    ]
    advertised = {
        t["function"]["name"] for t in build_model_tool_schemas(["context7.query_docs"], spec)
    }
    assert "context7.query_docs" in advertised


# ---------------------------------------------------------------------------
# El diagnóstico del api-server tiene que decir lo MISMO que el anuncio al modelo
# ---------------------------------------------------------------------------


def test_the_diagnostic_does_not_call_send_notification_executable() -> None:
    """La otra mitad del hallazgo B-04. Retirar la tool del anuncio al modelo
    arregla lo que ve el agente; el operador seguía viendo en el diagnóstico que
    `send_notification` es ejecutable, porque `tool_is_runtime_wired`
    cortocircuitaba por `implementation_type` y la semilla la declara
    `python_function`. Un diagnóstico que existe para decir qué funciona de
    verdad no puede enseñar un fantasma.
    """
    from api_server.schemas.catalog import tool_is_runtime_wired

    assert tool_is_runtime_wired("send_notification", "python_function") is False


def test_a_tenant_tool_is_still_wired_by_its_implementation_type() -> None:
    """La cara contraria, que es la que el atajo protegía: una tool de tenant con
    tipo tipado la cablea `register_tool_specs` se llame como se llame. Cerrar el
    caso de los builtins no puede llevarse por delante las tools personalizadas
    ni las MCP del proyecto."""
    from api_server.schemas.catalog import tool_is_runtime_wired

    assert tool_is_runtime_wired("acme_crm_lookup", "http_endpoint") is True
    assert tool_is_runtime_wired("atlassian-remote.search", "mcp_tool") is True


def test_a_wired_builtin_stays_wired() -> None:
    """Regresión: los builtins que SÍ tienen ejecutor no se ven afectados,
    incluido el alias `semantic_search` → `rag_search`."""
    from api_server.schemas.catalog import tool_is_runtime_wired

    assert tool_is_runtime_wired("read_file", "builtin") is True
    assert tool_is_runtime_wired("semantic_search", "builtin") is True
    assert tool_is_runtime_wired("stack_exec", "builtin") is True


# ---------------------------------------------------------------------------
# F5 — la puerta trasera que abre retirar un `run_*` MAL
# ---------------------------------------------------------------------------
def test_retiring_a_run_tool_does_not_reopen_the_implementation_type_shortcut() -> None:
    """El punto delicado de F5, y la razón de hacerlo en un orden concreto.

    `tool_is_runtime_wired` cortocircuita por `implementation_type` para las
    tools de tenant, y `docker_command` está en ese conjunto. Si al retirar los
    `run_*` se les quitara además el nombre de `_CATALOG_TOOL_NAMES`,
    `is_unwired_platform_builtin` dejaría de reconocerlos como builtins de
    plataforma, el atajo tomaría el mando y devolvería `True` para
    `docker_command`: una fila `run_pytest` superviviente en una BD sin migrar
    volvería a ser asignable Y anunciable. Es la puerta de atrás de B-04
    reabierta.

    Por eso siguen siendo nombres CANÓNICOS aunque ya no sean ejecutables.
    """
    from api_server.schemas.catalog import tool_is_runtime_wired
    from shared_domain.tool_names import CANONICAL_TOOL_NAMES, is_unwired_platform_builtin

    for name in sorted(_SANDBOX_IMPOSSIBLE_TOOLS):
        assert name in CANONICAL_TOOL_NAMES, f"{name} debe seguir siendo canónico"
        assert is_unwired_platform_builtin(name) is True, name
        # El tipo real de la fila sembrada, que es el que activaría el atajo.
        assert (
            tool_is_runtime_wired(name, "docker_command") is False
        ), f"{name}: el atajo por implementation_type volvió a declararla ejecutable"


def test_a_surviving_run_row_is_never_advertised() -> None:
    """El caso de la BD sin migrar, de punta a punta: aunque la fila siga viva y
    concedida, el modelo no la ve."""
    from workers.agent_tool_schemas import build_model_tool_schemas

    spec = [
        {
            "name": "run_pytest",
            "implementation_type": "docker_command",
            "config": {"runtime_template": "python-pytest"},
            "input_schema": {"type": "object"},
            "description": "falla siempre dentro del sandbox",
        }
    ]
    advertised = {
        t["function"]["name"] for t in build_model_tool_schemas(["run_pytest", "stack_exec"], spec)
    }
    assert "run_pytest" not in advertised
    assert "stack_exec" in advertised, "la vía real sí se anuncia"


def test_both_sides_read_the_same_rule_from_the_domain() -> None:
    """El worker (qué se anuncia) y el api-server (qué se declara ejecutable)
    tienen que salir del MISMO predicado: estas dos vistas ya divergieron una vez
    y por eso el fantasma sobrevivió a la primera corrección."""
    from shared_domain.tool_names import is_unwired_platform_builtin
    from workers.agent_tool_schemas import _unwired_platform_builtins

    assert "send_notification" in _unwired_platform_builtins()
    assert is_unwired_platform_builtin("send_notification") is True
    # Un nombre que no es de plataforma nunca cae aquí: es de tenant y su tipo manda.
    assert is_unwired_platform_builtin("acme_crm_lookup") is False
