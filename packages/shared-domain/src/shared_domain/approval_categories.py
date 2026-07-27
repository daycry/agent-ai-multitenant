"""Single source of the sensitive-action categories (spec §7.7-7.8).

The human-approval gate has TWO consumers that must agree on the category
vocabulary: the api-server seed of the four policy presets
(``api_server.seeds.builtin_approval_policies``) and the sandboxed runtime gate
(``agent_runtime.approval``). They diverged — the runtime emitted 4 categories
(``code_execution``/``file_write``/``network_access``/``agent_delegation``) that
did NOT intersect these 13, so ``requires_human`` always fell through to ``auto``
and NOTHING was gated, not even under the ``customer-external`` preset (audit
2026-07-03, g6, fail-open). This module is the one list both import; a contract
test pins the runtime tool→category map to it so they cannot drift again.

Lives in ``shared-domain`` because the runtime is sandboxed (no DB, no
api-server) but already imports ``shared_domain`` (e.g. ``tool_names``).
"""

from __future__ import annotations

#: The 13 canonical categories of sensitive actions. Order is stable for JSON
#: serialization of the preset ``categories`` maps. The admin-panel UI mirrors
#: this list with labels (``approval-policy/page.tsx``); keep them in sync.
APPROVAL_CATEGORIES: tuple[str, ...] = (
    "code_changes",
    "git_commit",
    "git_push",
    "external_http_get",
    "external_http_post",
    "secrets_access",
    "data_migration",
    "production_deploy",
    "infra_provision",
    "secret_rotation",
    "external_communication",
    "data_export_pii",
    "user_management",
)


# ---------------------------------------------------------------------------
# Categoría de una tool NO builtin (T2 de `tools-y-cierre-plan-fixes`, g6)
# ---------------------------------------------------------------------------
#
# El mapa del runtime (``agent_runtime.approval.DEFAULT_TOOL_CATEGORIES``) está
# keyed por nombre canónico de builtin. Una tool MCP se llama ``<server>.<tool>``
# —un nombre que depende del servidor que declare cada proyecto—, así que ese
# mapa NO puede contenerla: la integración externa, que es la superficie con más
# alcance de todas, era la única que ningún preset podía detener.
#
# Aquí se deriva su categoría de lo que el operador YA declara al importarla: el
# ``security_level`` de la fila (``sandboxed`` por defecto, ver
# ``api_server.routers.mcp.import_mcp_tools``). La api-server la serializa en el
# ToolSpec, el worker la forwardea y el runtime la mezcla con el mapa builtin.
#
# Por qué estas categorías y no otras:
#
#   * una tool MCP / ``http_endpoint`` es una llamada SALIENTE a un servidor que
#     la plataforma no controla, con efectos que no puede prever → la honesta de
#     las 13 es ``external_http_post`` (``auto`` solo en el preset ``sandbox``);
#   * ``python_function`` / ``docker_command`` EJECUTAN algo sobre el trabajo →
#     ``code_changes``, la misma que ``shell_exec`` / ``write_file``.

#: Categoría por ``implementation_type`` para una tool no builtin que el operador
#: no ha marcado ``safe``.
_SPEC_CATEGORY_BY_IMPL: dict[str, str] = {
    "mcp_tool": "external_http_post",
    "http_endpoint": "external_http_post",
    "python_function": "code_changes",
    "docker_command": "code_changes",
}

#: El ÚNICO nivel que exime del gate. Es el opt-out explícito y por-tool del
#: operador; sin él, su única palanca sería apagar la categoría entera del
#: proyecto, que también dejaría escapar a los builtins que la comparten.
_UNGATED_SECURITY_LEVEL = "safe"


def spec_approval_category(*, implementation_type: str, security_level: str | None) -> str | None:
    """Categoría canónica que gatea una tool NO builtin, o ``None``.

    Devuelve ``None`` para los builtins: su categoría la fija el mapa canónico
    del runtime y derivarla aquí del ``security_level`` de la fila sembrada la
    pisaría con una adivinada.

    Fail-CLOSED ante un ``security_level`` desconocido: se gatea. Es una CHECK
    cerrada en BD, así que no debería ocurrir; pero si mañana se añade un nivel,
    el modo de fallo seguro es pedir humano, no dejar pasar en silencio —que es
    literalmente cómo se vivió g6.
    """
    category = _SPEC_CATEGORY_BY_IMPL.get(implementation_type)
    if category is None:
        return None
    if security_level == _UNGATED_SECURITY_LEVEL:
        return None
    return category


__all__ = [
    "APPROVAL_CATEGORIES",
    "spec_approval_category",
]
