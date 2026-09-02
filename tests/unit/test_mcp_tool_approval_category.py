"""Las tools MCP/custom son GATEABLES por el gate de validación humana.

T2 del plan `tools-y-cierre-plan-fixes`, residuo del hallazgo **g6** de la
auditoría de plataforma 2026-07-03 (coordinado con `prod-03 task_prod03_02`).

T1 cerró la mitad grande de g6: el runtime emitía 4 categorías que no
intersectaban ninguna de las 13 canónicas, así que `requires_human` caía siempre
en `auto` y el preset «Cliente Externo» (13/13 `human_required`) no detenía nada.
Se remapearon los builtins y el gate volvió a morder.

Pero `DEFAULT_TOOL_CATEGORIES` está **keyed por nombre canónico de builtin**, y
una tool MCP se llama `<server>.<tool>` — un nombre que ese mapa no puede
contener porque depende del servidor que el proyecto declare. Resultado: la
superficie que MÁS sale de la plataforma (una integración externa que hace lo que
le dé la gana en un servidor de terceros) era justo la que **ningún preset podía
parar**. Ni «Cliente Externo».

La vía que cierra T2: el `ToolSpec` que el api-server serializa lleva su
`approval_category` derivada del `security_level` que el operador ya fija al
importar la tool (`sandboxed` por defecto, ver `routers/mcp.py`), el worker lo
forwardea sin tocarlo y el runtime lo mezcla con el mapa de builtins antes de
construir el `ApprovalGate`.

Que el opt-out sea `security_level='safe'` no es un atajo: es el criterio de
aceptación del propio plan («una tool MCP **marcada sensible** se aparca»). Sin
él, la única palanca del operador sería apagar la categoría entera del proyecto,
que es más grosera y afecta también a los builtins.
"""

from __future__ import annotations

import pytest
from agent_runtime.approval import (
    DEFAULT_TOOL_CATEGORIES,
    ApprovalGate,
    tool_categories_from_specs,
)
from api_server.seeds.builtin_approval_policies import preset_decisions
from shared_domain.approval_categories import APPROVAL_CATEGORIES, spec_approval_category

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# (a) La derivación pura: implementation_type + security_level → categoría.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("impl", "level", "expected"),
    [
        # Una integración MCP es una llamada saliente a un servidor que la
        # plataforma no controla y con efectos que no puede prever: la categoría
        # honesta de las 13 es la de POST externo.
        ("mcp_tool", "sandboxed", "external_http_post"),
        ("mcp_tool", "privileged", "external_http_post"),
        # Una tool HTTP custom es lo mismo sin el protocolo MCP delante.
        ("http_endpoint", "sandboxed", "external_http_post"),
        ("http_endpoint", "privileged", "external_http_post"),
        # Las que EJECUTAN algo (código o un contenedor) tocan el trabajo.
        ("python_function", "sandboxed", "code_changes"),
        ("docker_command", "privileged", "code_changes"),
        # `safe` es el opt-out explícito del operador, para cualquier tipo.
        ("mcp_tool", "safe", None),
        ("http_endpoint", "safe", None),
        ("python_function", "safe", None),
        # Los builtins NO se derivan aquí: su mapa canónico manda (T1).
        ("builtin", "privileged", None),
        ("builtin", "sandboxed", None),
    ],
)
def test_spec_approval_category_mapping(impl: str, level: str, expected: str | None) -> None:
    assert spec_approval_category(implementation_type=impl, security_level=level) == expected


def test_every_derived_category_is_canonical() -> None:
    """Un valor fuera de las 13 reeditaría g6 en pequeño: el gate emitiría una
    categoría que ningún preset conoce y `requires_human` caería en `auto`."""
    for impl in ("mcp_tool", "http_endpoint", "python_function", "docker_command"):
        for level in ("safe", "sandboxed", "privileged"):
            category = spec_approval_category(implementation_type=impl, security_level=level)
            assert category is None or category in APPROVAL_CATEGORIES, (impl, level, category)


def test_unknown_security_level_is_treated_as_sensitive() -> None:
    """Fail-CLOSED ante un valor que no reconocemos.

    `security_level` es una CHECK cerrada en BD, así que esto no debería pasar;
    pero si un día se añade un nivel nuevo, la opción segura es gatear, no
    dejar pasar en silencio (que es exactamente cómo se vive un fail-open)."""
    assert (
        spec_approval_category(implementation_type="mcp_tool", security_level="whatever")
        == "external_http_post"
    )
    assert spec_approval_category(implementation_type="mcp_tool", security_level=None) == (
        "external_http_post"
    )


# ---------------------------------------------------------------------------
# (b) El api-server lo emite en el ToolSpec serializado.
# ---------------------------------------------------------------------------
class _FakeTool:
    """Lo mínimo que `_tool_to_spec` lee de una fila `Tool`."""

    def __init__(self, name: str, impl: str, level: str) -> None:
        self.name = name
        self.implementation_type = impl
        self.security_level = level
        self.implementation_ref = "docling.convert" if impl == "mcp_tool" else None
        self.input_schema = {"type": "object"}
        self.description = "d"


def test_tool_to_spec_carries_the_approval_category() -> None:
    from api_server.agent_tools_enforcement import _tool_to_spec

    spec = _tool_to_spec(_FakeTool("docling.convert", "mcp_tool", "sandboxed"))
    assert spec["approval_category"] == "external_http_post"


def test_tool_to_spec_omits_the_key_when_not_gated() -> None:
    """Ausente, no `None`: el runtime distingue «sin categoría» de «categoría
    nula» sin tener que filtrar valores falsy al mezclar los mapas."""
    from api_server.agent_tools_enforcement import _tool_to_spec

    spec = _tool_to_spec(_FakeTool("acme.ping", "mcp_tool", "safe"))
    assert "approval_category" not in spec


def test_builtin_specs_do_not_carry_a_derived_category() -> None:
    """Un builtin NO puede traer categoría derivada: pisaría el mapa canónico de
    T1 con una adivinada a partir del `security_level` de la fila sembrada."""
    from api_server.agent_tools_enforcement import _tool_to_spec

    spec = _tool_to_spec(_FakeTool("write_file", "builtin", "privileged"))
    assert "approval_category" not in spec


# ---------------------------------------------------------------------------
# (c) El runtime mezcla spec + builtins sin dejar que uno pise al otro.
# ---------------------------------------------------------------------------
def test_specs_extend_the_builtin_map() -> None:
    merged = tool_categories_from_specs(
        [
            {"name": "docling.convert", "approval_category": "external_http_post"},
            {"name": "acme.deploy", "approval_category": "code_changes"},
        ]
    )
    assert merged["docling.convert"] == "external_http_post"
    assert merged["acme.deploy"] == "code_changes"
    # Y no pierde los builtins.
    assert merged["shell_exec"] == DEFAULT_TOOL_CATEGORIES["shell_exec"]


def test_a_spec_cannot_downgrade_a_builtin() -> None:
    """El mapa canónico gana la colisión.

    Si una fila `Tool` sembrada mal (o manipulada) declarara `write_file` con una
    categoría más laxa, el spec NO puede rebajar el gate de un builtin."""
    merged = tool_categories_from_specs(
        [{"name": "write_file", "approval_category": "external_http_get"}]
    )
    assert merged["write_file"] == DEFAULT_TOOL_CATEGORIES["write_file"]


def test_specs_without_category_are_recorded_as_the_operators_opt_out() -> None:
    """`task_cv_23`: un spec listado SIN categoría ya no «no cambia nada»: se
    anota como opt-out explícito (`UNGATED_TOOL`) para distinguirlo de una tool
    que nadie catalogó, que es la que cae al fallback del gate."""
    from agent_runtime.approval import UNGATED_TOOL

    merged = tool_categories_from_specs([{"name": "x.y"}])
    assert merged["x.y"] == UNGATED_TOOL
    assert {k: v for k, v in merged.items() if k != "x.y"} == dict(DEFAULT_TOOL_CATEGORIES)
    assert tool_categories_from_specs(None) == dict(DEFAULT_TOOL_CATEGORIES)
    assert tool_categories_from_specs([]) == dict(DEFAULT_TOOL_CATEGORIES)


def test_a_spec_category_outside_the_13_is_dropped() -> None:
    """Defensa en profundidad contra el modo de fallo de g6: una categoría
    desconocida no se propaga al gate (haría creer que la tool está cubierta
    cuando `requires_human` caería en `auto`)."""
    merged = tool_categories_from_specs(
        [{"name": "evil.tool", "approval_category": "network_access"}]
    )
    assert "evil.tool" not in merged


# ---------------------------------------------------------------------------
# (d) El comportamiento que pide el criterio de aceptación del plan.
# ---------------------------------------------------------------------------
def _gate(preset: str, specs: list[dict[str, object]]) -> ApprovalGate:
    return ApprovalGate(
        {"categories": preset_decisions(preset)},
        tool_categories_from_specs(specs),
    )


_MCP_SPECS: list[dict[str, object]] = [
    {"name": "docling.convert", "approval_category": "external_http_post"}
]


def test_customer_external_stops_an_mcp_tool() -> None:
    assert _gate("customer-external", _MCP_SPECS).review("docling.convert") == "external_http_post"


def test_production_stops_an_mcp_tool() -> None:
    assert _gate("production", _MCP_SPECS).review("docling.convert") == "external_http_post"


def test_development_lets_an_mcp_tool_through() -> None:
    """En `development` una tool MCP NO se para. Es un cambio con historia.

    Este test decía lo contrario, y su versión anterior era coherente con el
    hallazgo **g6**: las tools MCP se enrutaron a `external_http_post`
    precisamente porque la integración externa —la superficie con más alcance de
    todas— era la única que **ningún preset podía detener**. Gatearlas bajo
    `development` era parte de cerrar ese agujero.

    El operador lo revirtió para `development` el 2026-08-02, sabiendo esto, y
    la razón es de dosis y no de principio: `import_mcp_tools` da de alta las
    tools con `security_level="sandboxed"`, así que gatear la categoría hace que
    **cada** integración del proyecto pida aprobación desde el primer día. Una
    cola así se despacha aprobando sin leer, y entonces el gate no protege: solo
    entrena el reflejo de aceptar.

    Lo que NO se revirtió, y es lo que mantiene cerrado el g6:

      * `production` y `customer-external` siguen deteniendo la tool MCP (los
        dos tests de arriba), así que la superficie no vuelve a ser
        indetenible por ningún preset — que era el hallazgo literal;
      * el opt-out sigue siendo por-herramienta y del operador
        (`security_level="safe"`), o sea que quien quiera gatear sus
        integraciones en desarrollo tiene la palanca fina: marcar `safe` las de
        confianza y dejar el resto gateadas bajo el preset que elija.

    Si se vuelve a cambiar, que sea pesando esas dos cosas y no por simetría con
    los otros presets.
    """
    assert _gate("development", _MCP_SPECS).review("docling.convert") is None


def test_sandbox_lets_an_mcp_tool_through() -> None:
    assert _gate("sandbox", _MCP_SPECS).review("docling.convert") is None


def test_a_safe_mcp_tool_is_not_gated_even_under_customer_external() -> None:
    """El opt-out del operador es real y por-tool (criterio del plan).

    Se pinea a propósito: si algún día se decide que ni `safe` escapa del preset
    máximo, este test es el que hay que cambiar — y con él, la conversación.
    """
    assert _gate("customer-external", [{"name": "acme.ping"}]).review("acme.ping") is None


def test_the_regression_this_closes() -> None:
    """Sin categorías de spec, «Cliente Externo» no detenía la tool MCP.

    Es el estado exacto anterior a T2: el gate con SOLO el mapa de builtins.
    `task_cv_23` (auditoría 2026-09-01, D-04) cierra también ESTE agujero por
    el otro lado: una tool con namespace que ningún spec catalogó cae al
    criterio de `spec_approval_category` para `mcp_tool` y se para. El opt-out
    del operador sigue siendo el spec listado sin categoría (test de arriba).
    """
    blind = ApprovalGate({"categories": preset_decisions("customer-external")})
    assert blind.review("docling.convert") == "external_http_post"
