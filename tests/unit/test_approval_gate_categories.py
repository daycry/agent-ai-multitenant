"""g6 regression: the runtime approval gate must speak the preset vocabulary.

The gate mapped tools to categories (``code_execution``/``file_write``/...) that
intersected NONE of the 13 canonical preset categories, so ``requires_human``
always returned ``auto`` and no tool was ever gated — not even under the
``customer-external`` preset (audit 2026-07-03, g6, fail-open). These tests pin
(a) every gate category to the single-source ``APPROVAL_CATEGORIES`` and (b) the
end-to-end behaviour: a strict preset actually stops a sensitive tool.
"""

from __future__ import annotations

from agent_runtime.approval import DEFAULT_TOOL_CATEGORIES, ApprovalGate
from shared_domain.approval_categories import APPROVAL_CATEGORIES
from shared_domain.tool_names import CANONICAL_TOOL_NAMES, RUNTIME_WIRED_TOOL_NAMES


def _preset(decision: str) -> dict[str, dict[str, str]]:
    """A policy where every canonical category takes `decision` (mirrors the
    sandbox = all-auto / customer-external = all-human_required seeds)."""
    return {"categories": dict.fromkeys(APPROVAL_CATEGORIES, decision)}


def test_every_gate_category_is_canonical() -> None:
    """The fail-open bug: gate categories that are not in the preset vocabulary
    can never be `human_required`, so the tool silently runs."""
    canonical = set(APPROVAL_CATEGORIES)
    for tool, category in DEFAULT_TOOL_CATEGORIES.items():
        assert category in canonical, (
            f"{tool} maps to non-canonical category {category!r} → it would "
            f"fail-open (never gated) under any preset"
        )


def test_customer_external_preset_gates_sensitive_tools() -> None:
    gate = ApprovalGate(_preset("human_required"))
    # The dangerous tools are now actually parked for human approval.
    assert gate.review("http_post") == "external_http_post"
    assert gate.review("http_get") == "external_http_get"
    assert gate.review("shell_exec") == "code_changes"
    assert gate.review("stack_exec") == "code_changes"
    assert gate.review("write_file") == "code_changes"


def test_sandbox_preset_gates_nothing() -> None:
    gate = ApprovalGate(_preset("auto"))
    for tool in DEFAULT_TOOL_CATEGORIES:
        assert gate.review(tool) is None


def test_unmapped_tool_is_never_gated() -> None:
    gate = ApprovalGate(_preset("human_required"))
    assert gate.review("read_file") is None
    assert gate.review(None) is None


def test_old_broken_categories_are_not_canonical() -> None:
    """Documents the regression: the previous vocabulary had zero overlap."""
    old = {"code_execution", "file_write", "network_access", "agent_delegation"}
    assert old.isdisjoint(set(APPROVAL_CATEGORIES))


# --- prod-03 A8 (auditoría 2026-07-06): contrato INVERSO. El test forward
# (categoría ∈ CATEGORIES) no detectaba que tools sensibles wired NO tuvieran
# categoría y escapasen al gate incluso bajo customer-external. Estas tools
# DEBEN estar gateadas: destructivas, ejecutan código, o comunican al exterior.
_MUST_BE_GATED = {
    "delete_file",  # destructiva (write_file ya se gateaba, delete no)
    "stack_exec",  # ejecuta el toolchain del proyecto (la vía real, ADR 0093)
}

# Los cuatro `run_*` salieron de `RUNTIME_WIRED_TOOL_NAMES` con F5 (2026-07-28):
# son `docker_command` y fallan siempre dentro del sandbox. Como
# `send_notification`, CONSERVAN su categoría — ver el test de abajo.
_UNWIRED_BUT_KEEP_CATEGORY = {
    "send_notification": "external_communication",
    "run_pytest": "code_changes",
    "run_lint": "code_changes",
    "run_typecheck": "code_changes",
    "run_build": "code_changes",
}


def test_unwired_tools_keep_their_gate_category() -> None:
    """Las tools que salieron de `RUNTIME_WIRED_TOOL_NAMES` CONSERVAN su categoría.

    `send_notification` (B-04: su ejecutor devuelve «not wired») y los cuatro
    `run_*` (F5: son `docker_command` y fallan siempre dentro del sandbox) no se
    anuncian hoy. Pero si algún día se cablea su consumidor —o alguien las
    reintroduce— tienen que reaparecer YA gateadas, no colarse sin categoría y
    escapar al gate incluso bajo customer-external (prod-03 A8).

    Quitarles la categoría al retirarlas parecería limpieza y sería justo el
    agujero: el nombre volvería a estar disponible sin gate.
    """
    for tool, category in _UNWIRED_BUT_KEEP_CATEGORY.items():
        assert tool in DEFAULT_TOOL_CATEGORIES, f"{tool} perdió su categoría al retirarse"
        assert DEFAULT_TOOL_CATEGORIES[tool] == category, tool
        assert tool not in RUNTIME_WIRED_TOOL_NAMES, (
            f"{tool} volvió a estar wired — si es a propósito, muévela a _MUST_BE_GATED"
        )


def test_sensitive_wired_tools_are_gated() -> None:
    for tool in _MUST_BE_GATED:
        assert tool in RUNTIME_WIRED_TOOL_NAMES, f"{tool} ya no está wired — revisa la lista"
        assert tool in DEFAULT_TOOL_CATEGORIES, (
            f"{tool} es sensible y está wired pero NO tiene categoría → escapa al "
            f"gate incluso bajo customer-external (prod-03 A8)"
        )


def test_customer_external_gates_the_leaking_tools() -> None:
    gate = ApprovalGate(_preset("human_required"))
    assert gate.review("send_notification") == "external_communication"
    assert gate.review("delete_file") == "code_changes"
    assert gate.review("run_build") == "code_changes"
    assert gate.review("run_pytest") == "code_changes"


# ---------------------------------------------------------------------------
# La guarda INVERSA y EXHAUSTIVA (prod-03 task_prod03_02)
# ---------------------------------------------------------------------------
# `_MUST_BE_GATED` de arriba pinea DOS nombres a mano, así que una tool nueva
# podía aterrizar wired y SIN categoría en silencio — y así entraron
# `promote_to_kb` y `memory_store`, que escapaban al gate incluso bajo
# «Cliente Externo» (recon prod-03, 2026-07-29). La guarda de abajo recorre
# `RUNTIME_WIRED_TOOL_NAMES` ENTERA: toda tool wired está gateada o figura aquí
# con su motivo. Añadir una tool sin decidir su categoría rompe CI.
#
# Criterio de exención: la tool NO produce efecto persistente ni saliente. Una
# lectura (del worktree, de la KB, de la memoria) no lo tiene; una escritura que
# sobrevive al run, sí — la lee otro agente después, en otro proyecto, como
# contexto de confianza.
_WIRED_UNGATED_BY_DESIGN: dict[str, str] = {
    "read_file": "lectura del worktree, sin efecto persistente ni saliente",
    "list_files": "lectura del worktree",
    "rag_search": "lectura de la KB (recall, no escritura)",
    "memory_recall": "lectura de memoria (recall, no escritura)",
    "document_convert": (
        "devuelve los chunks de un documento YA ingerido por un humano "
        "(`/internal/agent/document-convert` es un SELECT); no escribe nada"
    ),
    "task_comment": (
        "escribe un comentario del plan — es el canal de reporte AL humano; "
        "gatearlo pediría permiso para pedir permiso"
    ),
}


def test_every_wired_tool_is_gated_or_exempt_with_a_reason() -> None:
    """La guarda que evita que se repita: ninguna tool wired sin categoría.

    Si esta se pone roja por una tool nueva, la decisión no es silenciarla: es
    elegirle categoría en `DEFAULT_TOOL_CATEGORIES` o justificar la exención.
    """
    # La guarda tiene que ENCONTRAR algo (un descubrimiento vacío pasaría
    # vacuamente y envejecería sin avisar).
    assert len(RUNTIME_WIRED_TOOL_NAMES) >= 10, (
        f"la guarda dejó de ver el conjunto de tools wired (vio {len(RUNTIME_WIRED_TOOL_NAMES)})"
    )
    ungated = sorted(
        RUNTIME_WIRED_TOOL_NAMES - set(DEFAULT_TOOL_CATEGORIES) - set(_WIRED_UNGATED_BY_DESIGN)
    )
    assert not ungated, (
        f"tools wired SIN categoría de aprobación: {ungated}. Escapan al gate "
        "incluso bajo el preset «Cliente Externo». Asígnales una de las 13 "
        "categorías canónicas o añádelas a _WIRED_UNGATED_BY_DESIGN con motivo."
    )


def test_every_wired_tool_is_a_canonical_platform_name() -> None:
    """El invariante que la omisión de `_SYSTEM_TOOL_NAMES` rompía.

    Una tool que el runtime cablea es un nombre DE PLATAFORMA. Si no está en
    `CANONICAL_TOOL_NAMES`, dos cosas se rompen a la vez: no se le puede dar
    categoría de aprobación (el contrato de `test_tool_catalog_contract` exige
    que toda clave del mapa sea canónica) y `routers/tools` deja que un tenant
    registre una tool con su nombre, que el registro por ToolSpec —posterior a
    las familias de sistema— sustituiría en silencio.
    """
    assert RUNTIME_WIRED_TOOL_NAMES, "la guarda dejó de ver el conjunto wired"
    huerfanos = sorted(RUNTIME_WIRED_TOOL_NAMES - CANONICAL_TOOL_NAMES)
    assert not huerfanos, (
        f"tools cableadas que no son nombres canónicos de plataforma: {huerfanos}. "
        "No se les puede asignar categoría de aprobación y un tenant puede "
        "apropiarse del nombre."
    )


def test_the_exemption_list_stays_honest() -> None:
    """La exención es de IGUALDAD, no de subconjunto (en las dos direcciones).

    Una entrada que ya no está wired es residuo que tapa el siguiente agujero; y
    una que ADEMÁS tiene categoría delata que la decisión cambió y nadie limpió.
    """
    for tool, reason in _WIRED_UNGATED_BY_DESIGN.items():
        assert tool in RUNTIME_WIRED_TOOL_NAMES, (
            f"{tool} ya no está wired — quítalo de _WIRED_UNGATED_BY_DESIGN "
            "para que la lista siga siendo honesta"
        )
        assert tool not in DEFAULT_TOOL_CATEGORIES, (
            f"{tool} está exento Y tiene categoría — contradicción; borra una de las dos"
        )
        assert reason.strip(), f"{tool} está exento sin motivo escrito"


def _development_preset() -> dict[str, dict[str, str]]:
    """El preset `development` — el que hereda un proyecto SIN política propia
    (`_resolve_effective_approval_policy` → `DEFAULT_APPROVAL_POLICY_PRESET`)."""
    return {
        "categories": {
            **dict.fromkeys(APPROVAL_CATEGORIES, "human_required"),
            "code_changes": "auto",
            "git_commit": "auto",
            "external_http_get": "auto",
        }
    }


def test_the_persistent_writes_are_gated_where_the_audit_pointed() -> None:
    """El agujero que cierra task_prod03_02: `promote_to_kb` y `memory_store`.

    Estaban wired (anunciadas al modelo) y SIN categoría, así que escapaban al
    gate incluso bajo «Cliente Externo» — el titular de la auditoría. Las dos
    escriben datos que SOBREVIVEN al run y que otro agente leerá después como
    contexto de confianza.
    """
    strict = ApprovalGate(_preset("human_required"))
    assert strict.review("promote_to_kb") == "data_migration"
    assert strict.review("memory_store") == "code_changes"
    # `production` también las gatea (13/13 human_required, igual que el strict).
    assert ApprovalGate(_preset("auto")).review("promote_to_kb") is None


def test_promote_to_kb_is_gated_from_development_upward() -> None:
    """Rara y de alto alcance: copia un Document + Chunks a una KB compartida.

    Gatearla en `development` cuesta una parada puntual (necesita un
    `document_id` y un `target_kb_id` que ya existan), no un bucle.
    """
    assert ApprovalGate(_development_preset()).review("promote_to_kb") == "data_migration"


def test_memory_store_stays_auto_in_development_to_avoid_the_repark_livelock() -> None:
    """Y esta es la razón de que `memory_store` NO comparta categoría con la otra.

    Es tool de familia de sistema (cableada para todo agente, se use o no
    `agent_tools`) y de uso rutinario. El gate no «pregunta y sigue»: aborta el
    run, y aprobar devuelve la tarea a `backlog` para re-ejecutarla desde cero
    (ADR 0020). Mientras el bucle aprobar→re-aparcar siga sin arreglar
    (guardrails-7 / task_prod03_06), gatearla en el preset POR DEFECTO
    convertiría cada run que guarda un aprendizaje en un livelock.

    Si alguien mueve `memory_store` a una categoría que `development` marca
    `human_required`, este test se pone rojo — y eso es lo que se quiere: la
    decisión va DESPUÉS de task_prod03_06, no antes.
    """
    development = ApprovalGate(_development_preset())
    assert development.review("memory_store") is None
    # El contraste que da valor a la aserción: en `development` una edición de
    # código también pasa sin humano, y un POST a internet NO.
    assert development.review("write_file") is None
    assert development.review("http_post") == "external_http_post"
