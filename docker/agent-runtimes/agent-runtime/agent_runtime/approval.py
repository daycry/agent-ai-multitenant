"""The human-approval gate inside the agent loop (task_02_33).

The agent loop runs sandboxed — it has no DB and cannot reach the
api-server's approval engine. So the gate works on data alone: the
worker passes the project's `human_approval_policy` into the task spec,
and the loop checks each tool call against it *before* the tool runs.

When a tool's category is marked `human_required`, the `plan` node
stops the loop with status `awaiting_human_approval` instead of acting.
The worker (task_02_30) turns that into a real `ApprovalRequest` row.

`requires_human` mirrors `api_server.db.approval_repo.requires_human_
approval` — the policy contract, not importable across the sandbox.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from shared_domain.approval_action import action_fingerprint
from shared_domain.approval_categories import APPROVAL_CATEGORIES
from shared_domain.tool_names import to_canonical

# Builtin tool → sensitive-action category. Keyed on CANONICAL tool names
# (ADR 0048); a tool absent from this map is not sensitive and is never gated.
#
# The VALUES are the canonical categories of shared_domain.approval_categories
# (the same vocabulary the policy presets are seeded with). They used to be
# code_execution/file_write/network_access/agent_delegation, which intersected
# NONE of the 13 preset categories, so requires_human always returned auto and
# nothing was ever gated (audit 2026-07-03, g6, fail-open). test_approval_gate_
# categories pins every value here to APPROVAL_CATEGORIES. NOTE: agent_invoke is
# gated as code_changes for now; a dedicated `agent_delegation` canonical
# category (with its UI/preset support) is deferred to prod-03.
DEFAULT_TOOL_CATEGORIES: dict[str, str] = {
    "shell_exec": "code_changes",
    "stack_exec": "code_changes",
    "write_file": "code_changes",
    # prod-03 A8 (auditoría 2026-07-06): estas tools estaban wired pero SIN
    # categoría, así que escapaban al gate incluso bajo customer-external.
    "delete_file": "code_changes",  # destructiva sobre el worktree (como write_file)
    # `move_file` (2026-08-31): mueve o renombra ficheros y árboles del worktree,
    # y con `overwrite=true` reemplaza lo que había. Sin categoría escaparía al
    # gate incluso bajo «Cliente Externo» — que es el fail-open de prod-03 A8—, y
    # además desmontaría media razón de haberla construido en vez de abrir `mv`
    # en el allowlist de `shell_exec`: que por aquí SÍ pasa por la política de
    # aprobación del proyecto.
    "move_file": "code_changes",
    "run_pytest": "code_changes",  # ejecutan código arbitrario del repo
    "run_lint": "code_changes",
    "run_typecheck": "code_changes",
    "run_build": "code_changes",
    "send_notification": "external_communication",  # el preset promete gatear comunicación
    "http_get": "external_http_get",
    "http_post": "external_http_post",
    "agent_invoke": "code_changes",
    # prod-03 task_prod03_02 (recon 2026-07-29): las DOS escrituras persistentes
    # que quedaban sin categoría — wired y por tanto anunciadas al modelo, y sin
    # embargo invisibles al gate incluso bajo el preset «Cliente Externo». Un
    # agente escribía en la KB del tenant y en la memoria compartida sin que
    # ningún humano lo viera nunca.
    #
    # Ninguna de las 13 se llama «escritura persistente en KB/memoria» — el
    # nombre honesto sería una 14ª (`knowledge_write`), y eso toca presets y UI,
    # o sea decisión de producto. De las 13 disponibles se reparten así, y la
    # diferencia NO es cosmética:
    #
    # `promote_to_kb` → `data_migration`. Copia un Document y TODOS sus Chunks a
    #   otra KB del tenant (`/internal/agent/promote-to-kb`), de donde lo leerá
    #   por RAG cualquier proyecto con grant. Es, en sentido literal, datos
    #   moviéndose entre almacenes persistidos: `data_migration` es la única de
    #   las 13 cuyo sujeto son los datos de la plataforma y no el producto del
    #   trabajo, la red, los secretos, la infra, los despliegues, la comunicación
    #   saliente, la exportación de PII o los usuarios. Requiere `document_id` +
    #   `target_kb_id` ya existentes, así que es RARA y deliberada: gatearla
    #   desde `development` cuesta una parada puntual, no un bucle.
    #
    # `memory_store` → `code_changes`, y aquí manda la FRECUENCIA. Es tool de
    #   familia de sistema: `register_system_families` la cablea para TODO
    #   agente, esté o no en sus `agent_tools`, y se usa de rutina. El gate no
    #   «pide permiso y sigue»: aborta el run entero
    #   (`graph.py` → `STATUS_AWAITING_APPROVAL`) y, por el ADR 0020, aprobar
    #   devuelve la tarea a `backlog` para que se re-ejecute DESDE CERO. Con el
    #   bucle aprobar→re-aparcar todavía sin arreglar (guardrails-7 /
    #   task_prod03_06, que este carril NO implementa), darle una categoría que
    #   el preset por defecto `development` marca `human_required` convertiría
    #   cada run que guarda un aprendizaje en un livelock: aparcar → aprobar →
    #   re-ejecutar → aparcar. `code_changes` la deja `auto` en
    #   `sandbox`/`development` y la GATEA en `production` y
    #   `customer-external`, que es exactamente el agujero que la auditoría
    #   señaló («ni siquiera Cliente Externo detiene una sola tool»).
    #
    # Pendiente para producto, anotado aquí para que no se pierda: con la 14ª
    # categoría, `memory_store` debería gatearse desde `development` — pero
    # DESPUÉS de task_prod03_06, no antes.
    "promote_to_kb": "data_migration",
    "memory_store": "code_changes",
    # `kanban_update` tiene el MISMO agujero latente (mueve tareas del tablero) y
    # se queda sin categoría a propósito: hoy no está en
    # `RUNTIME_WIRED_TOOL_NAMES` (su drain worker-side nunca aterrizó, devuelve
    # `ok=False, "not wired"`), y ninguna de las 13 categorías canónicas cubre
    # «gestión de tareas/plan» — inventar la 14ª toca presets y UI, o sea que es
    # decisión de producto, no técnica. Si alguien la vuelve a cablear,
    # `test_every_wired_tool_is_gated_or_exempt_with_a_reason` se pone rojo y
    # fuerza la decisión ahí mismo. Eso es intencional.
}


def tool_categories_from_specs(
    raw_specs: Iterable[Mapping[str, Any]] | None,
    base: Mapping[str, str] = DEFAULT_TOOL_CATEGORIES,
) -> dict[str, str]:
    """El mapa tool→categoría del run: builtins + lo que traiga cada ToolSpec.

    T2 de `tools-y-cierre-plan-fixes` (residuo de g6). :data:`DEFAULT_TOOL_CATEGORIES`
    está keyed por nombre canónico de builtin y una tool MCP se llama
    ``<server>.<tool>``, un nombre que depende del servidor que declare el
    proyecto: no cabe en un mapa estático. El api-server deriva su categoría del
    ``security_level`` de la fila (``shared_domain.approval_categories.
    spec_approval_category``) y la serializa en el spec; aquí se une al mapa
    builtin justo antes de construir el gate.

    Dos reglas que no son adorno:

      * **el builtin gana la colisión** — un spec no puede rebajar el gate de
        ``write_file`` declarándolo con una categoría más laxa;
      * **una categoría fuera de las 13 se descarta** — propagarla haría creer
        que la tool está cubierta cuando ``requires_human`` caería en ``auto``,
        que es exactamente el fail-open de g6 reeditado en pequeño. Desde el ADR
        0153 ya no caería en ``auto`` sino en lo que diga ``unlisted_category``,
        pero descartarla sigue siendo lo correcto por otra razón: una categoría
        que no existe no se puede decidir en la UI de la política, así que el
        operador no tendría forma de ajustarla ni de saber que está ahí.
    """
    merged = dict(base)
    for spec in raw_specs or ():
        name = spec.get("name")
        category = spec.get("approval_category")
        if not name or not category or name in base:
            continue
        if category not in APPROVAL_CATEGORIES:
            continue
        merged[str(name)] = str(category)
    return merged


# ---------------------------------------------------------------------------
# Categoría que la política NO lista — ADR 0153 (C)
# ---------------------------------------------------------------------------
# ESPEJO EXACTO de `api_server.db.approval_repo`. Los dos procesos no se
# importan entre sí (esto corre dentro del sandbox, sin BD y sin api-server),
# así que la única defensa contra la deriva es el test que compara las dos
# implementaciones caso a caso (`tests/unit/test_unlisted_approval_category.py`).
# Si tocas una, toca la otra: arreglar solo la del api-server deja el agujero
# abierto justo donde corre el código NO confiable.

#: Clave HERMANA de `categories` que dice qué pasa con una categoría que el mapa
#: no nombra. Vocabulario: `auto` | `human_required`.
UNLISTED_CATEGORY_KEY = "unlisted_category"

_AUTO = "auto"
_HUMAN_REQUIRED = "human_required"
_DECISIONS = frozenset({_AUTO, _HUMAN_REQUIRED})

#: Default de :data:`UNLISTED_CATEGORY_KEY` cuando la política no la trae, según
#: su `preset`. Es el MISMO criterio con el que se siembran los cuatro presets:
#: estricto donde una acción sensible sin revisar cuesta caro, laxo donde gatear
#: lo no listado pararía los runs autónomos constantemente (y una cola de
#: aprobaciones que nadie atiende enseña a aprobar sin leer, que es peor que no
#: tener gate).
UNLISTED_DEFAULT_BY_PRESET: dict[str, str] = {
    "sandbox": _AUTO,
    "development": _AUTO,
    "production": _HUMAN_REQUIRED,
    "customer-external": _HUMAN_REQUIRED,
}

#: Sin clave y sin preset reconocible: se PARA. Ante una política que no se sabe
#: interpretar, preguntar es recuperable; dejar correr una acción sensible, no.
UNLISTED_FALLBACK_DECISION = _HUMAN_REQUIRED


def _policy_categories(policy: dict[str, Any]) -> dict[str, Any]:
    """El mapa `categories`, aceptando también la forma «mapa desnudo».

    Un `categories` que no es un mapa no se puede leer: se trata como vacío, o
    sea que TODA categoría cae al camino de lo no listado (fail-closed si la
    política tampoco declara preset), en vez de dejar pasar todo en silencio.
    """
    categories = policy.get("categories", policy)
    return categories if isinstance(categories, dict) else {}


def _unlisted_decision(policy: dict[str, Any]) -> tuple[str, str]:
    """``(decisión, por qué)`` para una categoría que la política no lista.

    El «por qué» viaja hasta el humano que recibe la aprobación: una solicitud
    sin motivo se aprueba sin leer, y esta es justo la que necesita leerse (para
    de más porque la política está incompleta, no porque la acción sea rara).
    """
    raw = policy.get(UNLISTED_CATEGORY_KEY)
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in _DECISIONS:
            return value, f"su clave `{UNLISTED_CATEGORY_KEY}` dice «{value}»"
        # Un valor ilegible (un typo de `human_required`, p. ej.) NO se resuelve
        # cayendo al preset: el autor pedía algo y no sabemos qué. Se para, y el
        # motivo lo dice para que se corrija.
        return (
            UNLISTED_FALLBACK_DECISION,
            f"su clave `{UNLISTED_CATEGORY_KEY}` tiene un valor que no se "
            f"entiende («{raw}»), así que se para por seguridad (fail-closed)",
        )
    preset = policy.get("preset")
    if isinstance(preset, str):
        slug = preset.strip().lower()
        derived = UNLISTED_DEFAULT_BY_PRESET.get(slug)
        if derived is not None:
            return derived, f"su preset es «{slug}»"
        return (
            UNLISTED_FALLBACK_DECISION,
            f"su preset («{preset}») no es reconocible, así que se para por "
            f"seguridad (fail-closed)",
        )
    return (
        UNLISTED_FALLBACK_DECISION,
        "no declara preset ni "
        f"`{UNLISTED_CATEGORY_KEY}`, así que se para por seguridad (fail-closed)",
    )


def requires_human(policy: dict[str, Any] | None, category: str) -> bool:
    """True if `category` needs a human under this project's policy.

    The policy JSONB is `{"categories": {<category>: "auto" |
    "human_required"}}` (a bare `{<category>: ...}` map is also accepted).

    ADR 0153: una categoría que el mapa NO lista ya no cae a un ``"auto"`` fijo
    —fail-open—; la decide la política (``unlisted_category``), en su defecto el
    ``preset``, y si no hay nada legible se falla CERRADO.

    Una política ausente/vacía es otra cosa y NO es de este ADR: la resuelve el
    ADR 0104 heredando el preset por defecto de plataforma (en el worker,
    ``_resolve_effective_approval_policy``), así que aquí sigue devolviendo
    False — fallar cerrado aquí gatearía todo run de un proyecto recién creado
    antes de que ese preset llegue a aplicarse.
    """
    if not policy:
        return False
    categories = _policy_categories(policy)
    if category in categories:
        return str(categories[category]).strip().lower() == _HUMAN_REQUIRED
    return _unlisted_decision(policy)[0] == _HUMAN_REQUIRED


def unlisted_category_reason(policy: dict[str, Any] | None, category: str) -> str | None:
    """Por qué el gate paró en una categoría que la política NO lista.

    ``None`` cuando no aplica: la política lista la categoría (se explica sola —
    la política la nombra y la decide) o no para. La cadena solo aparece en el
    caso nuevo, el que un humano no puede deducir mirando la solicitud.
    """
    if not policy:
        return None
    if category in _policy_categories(policy):
        return None
    decision, why = _unlisted_decision(policy)
    if decision != _HUMAN_REQUIRED:
        return None
    return (
        f"La política del proyecto no lista la categoría «{category}» y {why}: "
        f"se exige revisión humana (ADR 0153)."
    )


class ApprovalGate:
    """Decides whether a tool call must pause for human approval."""

    def __init__(
        self,
        policy: dict[str, Any] | None,
        tool_categories: dict[str, str] | None = None,
        approved_actions: Iterable[Mapping[str, Any]] | None = None,
    ) -> None:
        self._policy = policy
        self._tool_categories = tool_categories or DEFAULT_TOOL_CATEGORIES
        # ADR 0135: las acciones que un humano YA aprobó en ESTA task, por
        # huella canónica. Es un multiset: dos aprobaciones de la misma acción
        # dan dos canjes, ni uno más (T1). Una entrada sin `args_hash` —fila
        # corrupta, spec de una versión vieja— no autoriza nada: la lista es
        # una capacidad que se entrega al sandbox, así que se construye
        # cerrada por omisión.
        self._authorized: Counter[str] = Counter(
            digest
            for entry in (approved_actions or ())
            if (digest := str(entry.get("args_hash") or ""))
        )

    def review(self, tool: str | None, args: Any = None) -> str | None:
        """Return the sensitive category gating `tool`, or None if the
        tool may run without approval.

        ADR 0135 — ``args`` es lo que convierte esto en una autorización y no en
        un permiso por tool: cuando la categoría exige humano, se compara la
        acción EXACTA (``tool`` canónico + ``args`` verbatim) contra las que el
        humano aprobó en esta task. Si coincide, la llamada pasa y **la
        autorización se consume** (un canje, T1). Si no coincide —otra tool de
        la misma categoría, un espacio de más en el ``content``, o una llamada
        que ni siquiera trae ``args``— se aparca como siempre y el humano vuelve
        a decidir viendo el delta (N3, que arma el api-server).
        """
        if not tool:
            return None
        # Resolve legacy aliases (file_write → write_file, http_request →
        # http_get/http_post) to canonical names (ADR 0048) before lookup, so a
        # sensitive call cannot slip past the gate by mere name mismatch.
        for canonical in to_canonical(tool):
            category = self._tool_categories.get(canonical)
            if category is not None and requires_human(self._policy, category):
                if self._redeem(tool, args):
                    return None
                return category
        return None

    def gate_reason(self, category: str) -> str | None:
        """Por qué esta categoría para, cuando la política ni siquiera la lista.

        ``None`` para el caso corriente (la política nombra la categoría): ahí
        el motivo es la propia categoría. El api-server recalcula esto con la
        MISMA política al persistir la ``ApprovalRequest``, así que el humano lo
        ve aunque este runtime no lo propague.
        """
        return unlisted_category_reason(self._policy, category)

    def _redeem(self, tool: str, args: Any) -> bool:
        """Canjea la autorización de esta acción exacta, si la hay.

        ``action_fingerprint`` devuelve ``None`` cuando la acción no admite
        representación canónica (args no serializables, ``NaN``): sin huella no
        hay canje posible y se aparca, que es la dirección segura.
        """
        if not self._authorized:
            return False
        digest = action_fingerprint(tool, args)
        if digest is None or self._authorized[digest] <= 0:
            return False
        self._authorized[digest] -= 1
        return True
