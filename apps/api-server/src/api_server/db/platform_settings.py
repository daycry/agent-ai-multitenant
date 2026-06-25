"""Platform-wide settings — read by anyone, written only by a System Admin
(task_02_13).

`max_review_retries` is the canonical example (spec §7.9): a hard
platform limit on how many times an agent may rework its output. A
tenant cannot loosen it — `set_platform_setting` raises unless the actor
is a System Admin. When a setting has never been written, the read
helpers fall back to the platform default.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.models import PlatformSetting, User

# Setting keys.
MAX_REVIEW_RETRIES_KEY = "max_review_retries"

# Platform default for max_review_retries when the setting is unset.
# Kept in lockstep with agent_runtime.safeguards.DEFAULT_MAX_REVIEW_RETRIES
# (the two packages deliberately do not import one another).
DEFAULT_MAX_REVIEW_RETRIES = 3


class PlatformSettingForbiddenError(PermissionError):
    """Raised when a non-System-Admin attempts to write a platform setting."""


async def get_platform_setting(session: AsyncSession, key: str, *, default: Any = None) -> Any:
    """Read a platform setting; return `default` when it has never been set."""
    row = await session.get(PlatformSetting, key)
    return row.value if row is not None else default


async def set_platform_setting(
    session: AsyncSession,
    key: str,
    value: Any,
    *,
    actor: User,
) -> PlatformSetting:
    """Write a platform setting. Only a System Admin may do so.

    Raises `PlatformSettingForbiddenError` for any other actor — a Tenant
    Admin included. The caller owns the transaction (this flushes).
    """
    if not actor.is_system_admin:
        raise PlatformSettingForbiddenError(
            f"only a System Admin may set the platform setting '{key}'"
        )

    row = await session.get(PlatformSetting, key)
    if row is None:
        row = PlatformSetting(key=key, value=value, updated_by=actor.id)
        session.add(row)
    else:
        row.value = value
        row.updated_by = actor.id
    await session.flush()
    return row


# ---------------------------------------------------------------------------
# Public application base URL (ADR 0047 — operator-configurable)
# ---------------------------------------------------------------------------
# The single PUBLIC base URL of the whole application — ONE clean origin (e.g.
# ``https://agentic-orchestrator.com``), port-abstract for end users. It is the
# canonical base for absolute links AND for building the SSO callback / SAML ACS
# / EntityID (those are PATHS appended to it). It must be registered in the IdP's
# allow-list, so it has to match the platform's real public hostname/gateway:
# a deploy concern that must still be operator-tunable (not hardcoded). A System
# Admin sets it from the SSO settings page; when unset, the router falls back to
# the API_SERVER_SSO_REDIRECT_BASE_URL env default (bootstrap — the api-server's
# own origin in dev, where /auth/* resolves without a proxy). Stored as a
# normalised absolute http(s) URL with no path / trailing slash.
APP_PUBLIC_BASE_URL_KEY = "app.public_base_url"


class InvalidPublicBaseUrlError(ValueError):
    """Raised when a proposed public base URL is not a valid absolute http(s)
    URL (bad scheme, missing host, or carrying a path/query/fragment)."""


def validate_public_base_url(value: str) -> str:
    """Validate + normalise the public application base URL.

    Must be an absolute ``http(s)://host[:port]`` origin with NO path, query or
    fragment (the callback / ACS paths are appended by the SSO router). Returns
    it normalised (scheme + netloc, no trailing slash). Raises
    :class:`InvalidPublicBaseUrlError` otherwise.
    """
    from urllib.parse import urlparse

    candidate = (value or "").strip()
    if not candidate:
        raise InvalidPublicBaseUrlError("base URL must not be empty")
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https"):
        raise InvalidPublicBaseUrlError("base URL must start with http:// or https://")
    if not parsed.netloc:
        raise InvalidPublicBaseUrlError("base URL must include a host")
    if parsed.path.strip("/") or parsed.query or parsed.fragment:
        raise InvalidPublicBaseUrlError(
            "base URL must be a bare scheme://host[:port] (no path or query)"
        )
    return f"{parsed.scheme}://{parsed.netloc}"


async def get_app_public_base_url_override(session: AsyncSession) -> str | None:
    """The System-Admin public-base-URL override, or ``None`` when unset.

    When unset the router falls back to the env default
    (``settings.sso_redirect_base_url`` — the bootstrap value)."""
    value = await get_platform_setting(session, APP_PUBLIC_BASE_URL_KEY, default=None)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


async def set_app_public_base_url(session: AsyncSession, value: str, *, actor: User) -> str:
    """Persist the public application base URL override (System Admin only).

    Validates FIRST (raising :class:`InvalidPublicBaseUrlError` before any
    write); ``set_platform_setting`` re-checks the actor is a System Admin.
    Returns the normalised URL stored."""
    normalised = validate_public_base_url(value)
    await set_platform_setting(session, APP_PUBLIC_BASE_URL_KEY, normalised, actor=actor)
    return normalised


# ---------------------------------------------------------------------------
# API path prefix (ADR 0069). The PATH segment under which the api-server is
# published behind a single-origin reverse proxy (Caddy/nginx): it sits BETWEEN
# the public origin (`app.public_base_url`) and the SSO/SCIM paths the routers
# append. Option C keeps the origin and the prefix as SEPARATE settings so the
# same origin serves the SPA at `/` and the API under e.g. `/api`. Empty = no
# prefix (api-server at the origin root — the dev/default, backward-compatible).
# ---------------------------------------------------------------------------
APP_API_PATH_PREFIX_KEY = "app.api_path_prefix"


class InvalidApiPathPrefixError(ValueError):
    """Raised when a proposed API path prefix is not a bare path beginning with
    ``/`` (carries a host/scheme/query/fragment, or omits the leading slash)."""


def validate_api_path_prefix(value: str) -> str:
    """Validate + normalise the API path prefix.

    Empty / whitespace / a bare ``/`` normalise to ``""`` (no prefix). A
    non-empty value must be a bare absolute path (leading ``/``, no
    scheme/host/query/fragment); it is returned with a leading slash and NO
    trailing slash (``/api``, ``/api/v1``). Raises
    :class:`InvalidApiPathPrefixError` otherwise.
    """
    from urllib.parse import urlparse

    candidate = (value or "").strip()
    if not candidate or candidate == "/":
        return ""
    if "://" in candidate or candidate.startswith("//"):
        raise InvalidApiPathPrefixError("the API path prefix must be a path, not a URL with host")
    if not candidate.startswith("/"):
        raise InvalidApiPathPrefixError("the API path prefix must start with '/'")
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise InvalidApiPathPrefixError(
            "the API path prefix must be a bare path (no host, query or fragment)"
        )
    inner = parsed.path.strip("/")
    return f"/{inner}" if inner else ""


async def get_api_path_prefix_override(session: AsyncSession) -> str | None:
    """The System-Admin API-path-prefix override, or ``None`` when unset/invalid.

    When ``None`` the router falls back to the env bootstrap
    (``settings.api_path_prefix``). A stored value that fails validation is
    treated as unset (never crashes the URL builders)."""
    value = await get_platform_setting(session, APP_API_PATH_PREFIX_KEY, default=None)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return validate_api_path_prefix(value)
    except InvalidApiPathPrefixError:
        return None


async def set_api_path_prefix(session: AsyncSession, value: str, *, actor: User) -> str:
    """Persist the API path prefix override (System Admin only). Validates first
    (raising :class:`InvalidApiPathPrefixError`); returns the normalised prefix
    stored (may be ``""``)."""
    normalised = validate_api_path_prefix(value)
    await set_platform_setting(session, APP_API_PATH_PREFIX_KEY, normalised, actor=actor)
    return normalised


# ---------------------------------------------------------------------------
# Web del córtex (ADR 0067) — habilitar egress web para las host tools
# ---------------------------------------------------------------------------
# Gate ON/OFF de las host tools provider-agnósticas ``web_search`` / ``web_fetch``
# del córtex. Default OFF (deny-by-default, Principio 2: abrir egress web es una
# decisión de seguridad, no el estado por defecto). Cuando el owner lo enciende desde
# el panel, el router del córtex pone ``CortexToolContext.web_enabled=True``, las web
# tools aparecen en los schemas y el modelo puede invocarlas (siempre por el
# egress-proxy + anti-SSRF). Sólo un System Admin lo escribe — el owner del despliegue
# lo es (ADR 0074). El proveedor de búsqueda concreto (searxng/brave) es un setting
# aparte (``cortex.web_search_provider``, leído por la tool desde Settings).
CORTEX_WEB_ENABLED_KEY = "cortex.web_enabled"
DEFAULT_CORTEX_WEB_ENABLED = False


async def get_cortex_web_enabled(session: AsyncSession) -> bool:
    """Si la web del córtex (host tools ``web_search`` / ``web_fetch``) está habilitada.

    Lo lee el router del córtex en cada turno para decidir ``web_enabled``. Default OFF
    (ADR 0067, deny-by-default): abrir egress web es una decisión explícita del owner.
    Sólo un System Admin lo escribe (``set_platform_setting``)."""
    value = await get_platform_setting(
        session, CORTEX_WEB_ENABLED_KEY, default=DEFAULT_CORTEX_WEB_ENABLED
    )
    return bool(value)


# ---------------------------------------------------------------------------
# Autonomía del córtex (Córtex F4, ADR 0078) — bucles cognitivos de fondo
# ---------------------------------------------------------------------------
# KILL-SWITCH GLOBAL de los bucles autónomos del córtex (curiosidad + reflexión
# programada + mantenimiento). Default OFF (deny-by-default, ADR 0078): el
# comportamiento autónomo consume LLM/egress sin que nadie lo dispare, así que
# arranca APAGADO; el owner lo enciende explícitamente desde el panel. Con OFF, el
# beat puede tickear pero CADA tarea lee este flag al inicio de la pasada y sale
# no-op (ni curiosidad ni reflexión programada ni mantenimiento tocan BD/red). Solo
# un System Admin lo escribe — el owner del despliegue lo es (ADR 0074).
CORTEX_AUTONOMY_ENABLED_KEY = "cortex.autonomy_enabled"
DEFAULT_CORTEX_AUTONOMY_ENABLED = False

# Budget caps de la curiosidad por ventana DIARIA (ADR 0078: caps + circuit-breaker
# son parte del MVP del bucle, no un fast-follow). Se aplican en Redis
# (``cortex:budget:{owner}:curiosity:{yyyymmdd}`` con INCR + cap). Cuando se superan
# → la pasada de curiosidad es un no-op (no busca). Solo un System Admin los escribe.
CORTEX_CURIOSITY_DAILY_SEARCHES_CAP_KEY = "cortex.curiosity_daily_searches_cap"
DEFAULT_CORTEX_CURIOSITY_DAILY_SEARCHES_CAP = 5

# Umbral del drive: la curiosidad SOLO se dispara si ``curiosity < threshold``
# (hambre de aprender). Por encima del umbral, el drive está saciado → no-op.
CORTEX_CURIOSITY_DRIVE_THRESHOLD_KEY = "cortex.curiosity_drive_threshold"
DEFAULT_CORTEX_CURIOSITY_DRIVE_THRESHOLD = 0.35

# Circuit-breaker: tras N fallos CONSECUTIVOS de una pasada de curiosidad, el
# breaker se ABRE durante ``cb_cooldown_s`` (worker config) y el bucle deja de
# intentar (protege coste/egress ante un fallo sistémico). Un éxito lo resetea.
CORTEX_CURIOSITY_CB_FAILS_KEY = "cortex.curiosity_cb_fails"
DEFAULT_CORTEX_CURIOSITY_CB_FAILS = 3


async def get_cortex_autonomy_enabled(session: AsyncSession) -> bool:
    """El KILL-SWITCH global de los bucles autónomos del córtex (ADR 0078).

    Lo leen las tres tareas de fondo (curiosidad / reflexión programada /
    mantenimiento) al inicio de CADA pasada; cuando es False la pasada es un no-op
    total (no toca BD ni red ni LLM). Default OFF (deny-by-default): el
    comportamiento autónomo consume coste/egress, así que arranca apagado y el owner
    lo enciende explícitamente. Solo un System Admin lo escribe (``set_platform_setting``)."""
    value = await get_platform_setting(
        session, CORTEX_AUTONOMY_ENABLED_KEY, default=DEFAULT_CORTEX_AUTONOMY_ENABLED
    )
    return bool(value)


async def get_cortex_curiosity_daily_searches_cap(session: AsyncSession) -> int:
    """Tope de búsquedas web/día de la curiosidad (default 5).

    Lo lee el budget gate en vivo; al alcanzarse, la siguiente pasada es un no-op
    (no busca). Un valor < 0 se sanea a 0 (cap a 0 = curiosidad apagada de facto)."""
    value = await get_platform_setting(
        session,
        CORTEX_CURIOSITY_DAILY_SEARCHES_CAP_KEY,
        default=DEFAULT_CORTEX_CURIOSITY_DAILY_SEARCHES_CAP,
    )
    try:
        cap = int(value)
    except (TypeError, ValueError):
        return DEFAULT_CORTEX_CURIOSITY_DAILY_SEARCHES_CAP
    return max(0, cap)


async def get_cortex_curiosity_drive_threshold(session: AsyncSession) -> float:
    """Umbral del drive ``curiosity`` por debajo del cual se dispara la curiosidad.

    Lo lee la tarea de curiosidad en vivo. Un valor fuera de ``[0,1]`` se recorta al
    rango; un valor no numérico cae al default 0.35."""
    value = await get_platform_setting(
        session,
        CORTEX_CURIOSITY_DRIVE_THRESHOLD_KEY,
        default=DEFAULT_CORTEX_CURIOSITY_DRIVE_THRESHOLD,
    )
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        return DEFAULT_CORTEX_CURIOSITY_DRIVE_THRESHOLD
    return max(0.0, min(1.0, threshold))


async def get_cortex_curiosity_cb_fails(session: AsyncSession) -> int:
    """Nº de fallos consecutivos que ABRE el circuit-breaker de la curiosidad (default 3).

    Lo lee la tarea de curiosidad en vivo. Un valor < 1 se sanea a 1 (al menos un
    fallo abre el breaker); un valor no numérico cae al default 3."""
    value = await get_platform_setting(
        session, CORTEX_CURIOSITY_CB_FAILS_KEY, default=DEFAULT_CORTEX_CURIOSITY_CB_FAILS
    )
    try:
        fails = int(value)
    except (TypeError, ValueError):
        return DEFAULT_CORTEX_CURIOSITY_CB_FAILS
    return max(1, fails)


async def get_max_review_retries(session: AsyncSession) -> int:
    """The effective max_review_retries — the platform override, or the
    default. This is what an execution's review-retry budget is built from."""
    value = await get_platform_setting(
        session, MAX_REVIEW_RETRIES_KEY, default=DEFAULT_MAX_REVIEW_RETRIES
    )
    return int(value)


# ---------------------------------------------------------------------------
# RAG reranker activation (Plan 06.17 task_06_17_02)
# ---------------------------------------------------------------------------
# Live enable/disable lever for the cross-encoder reranker applied on top of
# the hybrid (BM25 + vector + RRF) recall in /internal/agent/rag-search. When
# OFF (the default) the endpoint keeps the RRF order — the recall is already
# useful and the real reranker (BGEReranker) pulls in torch + transformers,
# a cost a deployment should opt INTO, not pay by default. When a System Admin
# flips it ON from the admin panel the endpoint applies the configured reranker
# on the next request (no restart). The flag is read live per request, so a
# change takes effect immediately. Only a System Admin may write it
# (``set_platform_setting``).
RAG_RERANKER_ENABLED_KEY = "rag.reranker_enabled"
DEFAULT_RAG_RERANKER_ENABLED = False


async def get_rag_reranker_enabled(session: AsyncSession) -> bool:
    """Whether the RAG reranker is currently enabled for /rag-search.

    Read live by the rag-search endpoint before it decides to apply a reranker.
    Default OFF: the hybrid RRF recall is already useful and the real reranker
    is a heavy opt-in dependency. A System Admin flips this from the admin panel
    — only a System Admin may write a platform setting (``set_platform_setting``).
    """
    value = await get_platform_setting(
        session, RAG_RERANKER_ENABLED_KEY, default=DEFAULT_RAG_RERANKER_ENABLED
    )
    return bool(value)


# ---------------------------------------------------------------------------
# Back-fill de embeddings de memoria (Plan 06.17 task_06_17_03)
# ---------------------------------------------------------------------------
# El worker dedicado ``workers.backfill_memory_embeddings`` rellena la columna
# ``memory_entries.embedding`` que nace NULL (persistence.py): un trabajo
# IDEMPOTENTE (solo toca filas con ``embedding IS NULL``), por LOTES y
# THROTTLED, NUNCA parte del flujo de run (sin auto-retry — el run no espera al
# embedder). Tres palancas operator-configurable, leídas en vivo al inicio de
# cada ejecución del worker (un System Admin las cambia desde el panel y surten
# efecto en la siguiente pasada sin reiniciar Celery):
#
#   * ``memory.backfill_enabled``    — lever ON/OFF (default ON: una plataforma
#       desatendida debe ir rellenando los embeddings que faltan).
#   * ``memory.backfill_batch_size`` — cuántas filas se embeben por lote (acota
#       la memoria y el tamaño de la petición al embedder).
#   * ``memory.backfill_throttle_ms``— pausa entre lotes en milisegundos (evita
#       saturar Ollama / la DB; 0 = sin pausa).
#
# El worker recorre TODOS los tenants (corre con un rol BYPASSRLS, como el
# Memorizer), de modo que no necesita ``app.tenant_id``; la columna
# ``tenant_id`` sigue presente en cada UPDATE como defensa en profundidad.
MEMORY_BACKFILL_ENABLED_KEY = "memory.backfill_enabled"
DEFAULT_MEMORY_BACKFILL_ENABLED = True

MEMORY_BACKFILL_BATCH_SIZE_KEY = "memory.backfill_batch_size"
DEFAULT_MEMORY_BACKFILL_BATCH_SIZE = 50
# Cotas de cordura: al menos 1 fila por lote; un techo generoso evita que un
# typo cargue miles de contenidos en una sola petición al embedder.
MEMORY_BACKFILL_BATCH_SIZE_MIN = 1
MEMORY_BACKFILL_BATCH_SIZE_MAX = 500

MEMORY_BACKFILL_THROTTLE_MS_KEY = "memory.backfill_throttle_ms"
DEFAULT_MEMORY_BACKFILL_THROTTLE_MS = 0
MEMORY_BACKFILL_THROTTLE_MS_MIN = 0
MEMORY_BACKFILL_THROTTLE_MS_MAX = 60_000


async def get_memory_backfill_enabled(session: AsyncSession) -> bool:
    """Si el back-fill de embeddings de memoria está habilitado.

    Lo lee el worker ``workers.backfill_memory_embeddings`` al inicio de cada
    ejecución; cuando es False la pasada es un no-op (no lee ni escribe filas).
    Default ON: una plataforma desatendida debe rellenar los embeddings que
    faltan. Solo un System Admin puede escribir el flag (``set_platform_setting``).
    """
    value = await get_platform_setting(
        session, MEMORY_BACKFILL_ENABLED_KEY, default=DEFAULT_MEMORY_BACKFILL_ENABLED
    )
    return bool(value)


async def get_memory_backfill_batch_size(session: AsyncSession) -> int:
    """Tamaño de lote del back-fill (acotado a [MIN, MAX]).

    Lo lee el worker en vivo; un valor fuera de rango se recorta a la cota más
    cercana en vez de romper la pasada."""
    value = await get_platform_setting(
        session, MEMORY_BACKFILL_BATCH_SIZE_KEY, default=DEFAULT_MEMORY_BACKFILL_BATCH_SIZE
    )
    try:
        size = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MEMORY_BACKFILL_BATCH_SIZE
    return max(MEMORY_BACKFILL_BATCH_SIZE_MIN, min(MEMORY_BACKFILL_BATCH_SIZE_MAX, size))


async def get_memory_backfill_throttle_ms(session: AsyncSession) -> int:
    """Pausa (ms) entre lotes del back-fill (acotada a [MIN, MAX]).

    Lo lee el worker en vivo; 0 = sin pausa. Un valor fuera de rango se recorta
    a la cota más cercana."""
    value = await get_platform_setting(
        session, MEMORY_BACKFILL_THROTTLE_MS_KEY, default=DEFAULT_MEMORY_BACKFILL_THROTTLE_MS
    )
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MEMORY_BACKFILL_THROTTLE_MS
    return max(MEMORY_BACKFILL_THROTTLE_MS_MIN, min(MEMORY_BACKFILL_THROTTLE_MS_MAX, ms))


# ---------------------------------------------------------------------------
# Default de memory_scope para agentes nuevos (Plan 06.17 task_06_17_04)
# ---------------------------------------------------------------------------
# Hasta esta tarea ``AgentCreateRequest`` defaulteaba SIEMPRE a ``private``
# (``domain.py`` ``server_default 'private'`` + el schema), de modo que un agente
# IA creado por UI sin elegir scope nacía ``private`` y el Memorizer no
# memorizaba nada en silencio. Este setting hace el default OPERATOR-CONFIGURABLE:
# el endpoint ``POST /agents`` lo lee cuando el body NO trae ``memory_scope``. El
# default de plataforma sigue siendo ``private`` (backward-compat: no cambia el
# comportamiento de los agentes ya creados ni el previo). Solo un System Admin lo
# escribe (``set_platform_setting``). Un valor no canónico cae a ``private``.
MEMORY_DEFAULT_SCOPE_KEY = "memory.default_scope"
DEFAULT_MEMORY_DEFAULT_SCOPE = "private"


async def get_default_memory_scope(session: AsyncSession) -> str:
    """El ``memory_scope`` por defecto para un agente creado sin scope explícito.

    Lo lee ``POST /agents`` cuando el body no envía ``memory_scope``. Falla a
    ``private`` (el default histórico, backward-compat). Un valor almacenado
    fuera de las cuatro :class:`~api_server.db.domain.MemoryScope` canónicas se
    sanea a ``private`` en vez de romper la creación."""
    from api_server.db.domain import MemoryScope

    value = await get_platform_setting(
        session, MEMORY_DEFAULT_SCOPE_KEY, default=DEFAULT_MEMORY_DEFAULT_SCOPE
    )
    scope = str(value).strip()
    canonical = {s.value for s in MemoryScope}
    return scope if scope in canonical else DEFAULT_MEMORY_DEFAULT_SCOPE


# ---------------------------------------------------------------------------
# Agente global usa el project_id de la tarea (Plan 06.17 task_06_17_13 / ADR 0054)
# ---------------------------------------------------------------------------
# Un agente GLOBAL (``project_id IS NULL``: built-in o tenant-template) que
# ejecuta una tarea DE UN PROYECTO sufría una asimetría real entre escritura y
# lectura: el Memorizer ESCRIBÍA memoria bajo ``task.project_id`` pero los
# endpoints internos LEÍAN por ``agent.project_id`` (None), de modo que el agente
# global no veía NUNCA los chunks ni la memoria ``project_shared`` del proyecto de
# la tarea (``internal_agent.py`` rag-search ``hits=[]``; memory-recall sin
# candidatos). El ADR 0054 (opción B) resuelve el ``project_id`` de LECTURA como
# el de la tarea en curso (``task.project_id``) cuando el agente es global,
# **estrictamente tenant-safe** (el ``task_id`` lo porta el token del runtime; el
# proyecto se resuelve server-side validando ``task.tenant_id == principal.
# tenant_id``) y acotado a ESE ÚNICO proyecto — jamás cross-tenant ni
# cross-project.
#
# Esta clave operator-configurable gobierna el comportamiento. Default ON: arregla
# la asimetría real (read = write = ``task.project_id``). Un operador que prefiera
# el aislamiento estricto antiguo (agente global = sin contexto de proyecto) lo
# apaga sin tocar código. Se lee EN VIVO por petición, así que un cambio surte
# efecto de inmediato. Solo un System Admin lo escribe (``set_platform_setting``).
GLOBAL_AGENT_USES_TASK_PROJECT_KEY = "memory.global_agent_uses_task_project"
DEFAULT_GLOBAL_AGENT_USES_TASK_PROJECT = True


async def get_global_agent_uses_task_project(session: AsyncSession) -> bool:
    """Si un agente global lee RAG/memoria con el ``project_id`` de la tarea en curso.

    Lo leen ``/internal/agent/rag-search`` y ``/internal/agent/memory-recall``
    antes de resolver el ``project_id`` efectivo de un agente global. Default ON
    (ADR 0054): arregla la asimetría write≠read. Cuando es False el agente global
    conserva el comportamiento estricto antiguo (sin contexto de proyecto). Solo
    un System Admin puede escribir el flag (``set_platform_setting``).
    """
    value = await get_platform_setting(
        session,
        GLOBAL_AGENT_USES_TASK_PROJECT_KEY,
        default=DEFAULT_GLOBAL_AGENT_USES_TASK_PROJECT,
    )
    return bool(value)


# ---------------------------------------------------------------------------
# Default seguro de model_config para agentes (Plan 06.17 task_06_17_10 / ADR 0055)
# ---------------------------------------------------------------------------
# El ``model_config`` de un agente (la pata SER: proveedor/modelo/temperatura)
# nacía a menudo ``{}`` (ningún diálogo de la UI lo enviaba), de modo que en
# dispatch ese ``{}`` se traducía a un spec de modelo vacío que podía hacer
# fallar el arranque del run — un fallo tardío y opaco. El ADR 0055 (opción M-B)
# decide:
#
#   * ``POST /agents`` rellena un DEFAULT EXPLÍCITO cuando el body no envía
#     ``model_config`` (ningún agente nuevo nace ``{}``);
#   * el dispatch aplica este MISMO default seguro a cualquier spec legacy ``{}``
#     (sin fallo de arranque, SIN auto-retry — solo rellena);
#   * una migración (0081) sanea las filas ``agents`` con ``model_config = {}``.
#
# El default es OPERATOR-CONFIGURABLE vía esta clave de ``platform_settings``: un
# System Admin lo cambia desde el panel (proveedor/modelo/temperatura). Si nunca
# se configuró, cae al fallback de CÓDIGO ``DEFAULT_MODEL_CONFIG`` (también del
# catálogo cerrado), nunca a un fallo. El default mismo se VALIDA contra el
# catálogo cerrado del ADR 0021; un valor almacenado inválido cae al fallback.
MODEL_DEFAULT_CONFIG_KEY = "model.default_config"

# Fallback de código anclado al catálogo cerrado del ADR 0021. Claude SDK es el
# camino primario de la plataforma (suscripción Pro/Max). ``temperature`` baja
# por defecto (salida más determinista para tareas de agente); 0.1 alinea con el
# valor que envía por defecto el plugin de GitHub Copilot en VS Code.
DEFAULT_MODEL_CONFIG: dict[str, Any] = {
    "provider": "claude_sdk",
    "model": "claude-sonnet-4",
    "temperature": 0.1,
}

# Rango válido de temperatura (mismo rango que valida el schema de agente).
MODEL_TEMPERATURE_MIN = 0.0
MODEL_TEMPERATURE_MAX = 2.0


class InvalidModelConfigError(ValueError):
    """Lanzada cuando un ``model_config`` propuesto no valida contra el catálogo
    cerrado (ADR 0021): proveedor fuera de catálogo, ``model`` vacío o
    ``temperature`` fuera de rango."""


def validate_model_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Valida un ``model_config`` contra el catálogo cerrado (ADR 0021 / 0055).

    Reglas (un fallo levanta :class:`InvalidModelConfigError`, que el router
    traduce a ``422``):

      * ``provider`` ∈ ``{claude_sdk, copilot, azure_foundry, ollama}`` (los
        cuatro proveedores del catálogo cerrado del ADR 0021);
      * ``model`` presente y no vacío;
      * ``temperature``, si está presente, dentro de ``[MIN, MAX]``.

    Devuelve el dict (intacto) en éxito. La fuente única de los proveedores
    válidos es ``LLM_PROVIDER_KINDS`` (no una lista paralela que se desincronice
    del enum ``LLMProviderKind``).
    """
    from api_server.db.llm_providers import LLM_PROVIDER_KINDS

    # ADR 0082: a config pinned by a CONCRETE provider row carries `provider_id`.
    # The row governs the kind at build time (its kind is guaranteed ∈ catálogo by
    # the DB CHECK), so we don't re-check the kind against the closed catalogue —
    # only that `provider_id` is a UUID. The `provider` (kind) may be stored
    # alongside (for inheritance/display/back-compat); if present it must be valid.
    pinned_by_id = bool(cfg.get("provider_id"))
    provider = cfg.get("provider")
    if pinned_by_id:
        from uuid import UUID

        try:
            UUID(str(cfg["provider_id"]))
        except (ValueError, TypeError) as exc:
            raise InvalidModelConfigError("provider_id must be a UUID") from exc
        if provider is not None and provider not in LLM_PROVIDER_KINDS:
            raise InvalidModelConfigError(
                f"provider {provider!r} is not in the closed catalogue "
                f"{LLM_PROVIDER_KINDS} (ADR 0021)"
            )
    elif provider not in LLM_PROVIDER_KINDS:
        raise InvalidModelConfigError(
            f"provider {provider!r} is not in the closed catalogue "
            f"{LLM_PROVIDER_KINDS} (ADR 0021)"
        )
    model = cfg.get("model")
    if not isinstance(model, str) or not model.strip():
        raise InvalidModelConfigError("model must be a non-empty string")
    temperature = cfg.get("temperature")
    if temperature is not None:
        try:
            temp = float(temperature)
        except (TypeError, ValueError) as exc:
            raise InvalidModelConfigError("temperature must be a number") from exc
        if temp < MODEL_TEMPERATURE_MIN or temp > MODEL_TEMPERATURE_MAX:
            raise InvalidModelConfigError(
                f"temperature {temp} must be between "
                f"{MODEL_TEMPERATURE_MIN} and {MODEL_TEMPERATURE_MAX}"
            )
    # reasoning_effort (ADR 0070): opcional; si está, debe ser una opción válida
    # del proveedor (incluye "off"). Cada proveedor tiene su set; no hay uno común.
    reasoning = cfg.get("reasoning_effort")
    if isinstance(reasoning, str) and reasoning.strip() and provider is not None:
        # Validate against the kind when known. With a provider_id pin and no kind
        # alongside, the row's kind governs the allowed set at build time, so we
        # skip the kind-check here rather than reject every reasoning value.
        from api_server.db.llm_providers import REASONING_OPTIONS_BY_KIND

        allowed = REASONING_OPTIONS_BY_KIND.get(provider, ())
        if reasoning not in allowed:
            raise InvalidModelConfigError(
                f"reasoning_effort {reasoning!r} is not valid for provider "
                f"{provider!r} (ADR 0070); allowed: {allowed}"
            )
    return cfg


def validate_chat_model_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate a CHAT model override (Feature B). Two accepted shapes:

      * concrete provider pinned: ``{provider_id: <uuid>, model: <non-empty>}`` —
        the provider's kind governs at build time, so the closed-catalogue kind
        check does not apply here (only provider_id well-formed + model present);
      * kind-based: same rules as :func:`validate_model_config` (ADR 0021).

    Raises :class:`InvalidModelConfigError` (→ 422) on a bad value."""
    if cfg.get("provider_id"):
        from uuid import UUID

        try:
            UUID(str(cfg["provider_id"]))
        except (ValueError, TypeError) as exc:
            raise InvalidModelConfigError("provider_id must be a UUID") from exc
        model = cfg.get("model")
        if not isinstance(model, str) or not model.strip():
            raise InvalidModelConfigError("model must be a non-empty string")
        return cfg
    return validate_model_config(cfg)


def is_model_config_empty(cfg: dict[str, Any] | None) -> bool:
    """Si un ``model_config`` cuenta como "vacío" (legacy ``{}``).

    Vacío = ``None`` o ``{}`` (un dict sin ninguna clave). Es el predicado que el
    endpoint y el dispatch usan para decidir si aplicar el default seguro. Un dict
    NO vacío se trata como un spec INTENCIONAL y se respeta tal cual — incluso si
    no trae ``provider``/``model`` canónicos (p. ej. el spec scripted del runtime
    de test usa ``kind`` en vez de ``provider``); el ADR 0055 acota el saneo al
    caso ``{}`` legacy, no a cualquier spec "incompleto". La validación de catálogo
    (``validate_model_config``, ``422`` en create/update) ya garantiza que un spec
    nuevo no vacío trae ``provider``/``model`` válidos."""
    return not cfg


def config_needs_default_model(cfg: dict[str, Any] | None) -> bool:
    """Si un ``model_config`` carece de spec de modelo y debe heredar el default.

    Extensión del ADR 0055: el caso ``{}`` legacy NO es el único que hereda. Un
    agente SEMBRADO puede traer solo personalidad (``system_prompts``) sin
    ``provider``/``model`` — p. ej. el equipo built-in de CodeIgniter 4: debe
    heredar el modelo default conservando sus prompts. Devuelve True cuando el
    config NO fija modelo: ni el par ``provider``+``model``, ni un marcador
    ``kind`` (el spec ``scripted`` del runtime de test, que se respeta intacto).
    ``{}``/``None`` también devuelven True (mismo caso que is_model_config_empty).
    El dispatch hace ``{**default, **cfg}``: rellena el modelo y preserva el resto.
    """
    if not cfg:
        return True
    if cfg.get("kind"):
        return False
    return not (cfg.get("provider") and cfg.get("model"))


def resolve_model_config_chain(
    agent_cfg: dict[str, Any] | None,
    team_cfg: dict[str, Any] | None,
    project_cfg: dict[str, Any] | None,
    platform_default: dict[str, Any],
) -> dict[str, Any]:
    """Resuelve el ``model_config`` por la cadena de herencia (Ola A / ADR 0055):
    **plataforma → proyecto → equipo → agente**, gana el más específico que
    PINEE ``provider``+``model``.

    Si el agente pinea, se devuelve verbatim. Si no, se baja a equipo, luego a
    proyecto, luego a plataforma, tomando el PRIMER nivel que pinee; ese nivel
    rellena ``provider``/``model``/``temperature`` y las claves no-modelo del
    agente (p.ej. ``system_prompts``) se preservan (mismo merge ``{**nivel,
    **agente}`` que usaba el dispatch con el default de plataforma). Un nivel que
    solo pinea parcialmente (provider sin model) NO cuenta — se ignora y se baja.
    """
    agent = dict(agent_cfg or {})
    if not config_needs_default_model(agent):
        return agent
    for level in (team_cfg, project_cfg, platform_default):
        level_d = dict(level or {})
        if not config_needs_default_model(level_d):
            return _merge_inherited_model(level_d, agent)
    return _merge_inherited_model(dict(platform_default or {}), agent)


def _merge_inherited_model(level_d: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    """Merge de herencia cuando el agente NO pinea: el nivel aporta
    ``provider``/``model``/``temperature`` y el agente preserva sus claves
    no-modelo (p.ej. ``system_prompts``). ``reasoning_effort`` es
    provider-específico (ADR 0070): como el provider efectivo lo aporta el NIVEL,
    el reasoning del agente (que sería de OTRO provider) NO aplica — manda el del
    nivel o, si el nivel no fija ninguno, se descarta para no dejar un valor
    cruzado/inválido colgado en un provider incompatible."""
    merged = {**level_d, **agent}
    level_reasoning = level_d.get("reasoning_effort")
    if level_reasoning:
        merged["reasoning_effort"] = level_reasoning
    else:
        merged.pop("reasoning_effort", None)
    return merged


def resolve_model_config_origin(
    agent_cfg: dict[str, Any] | None,
    team_cfg: dict[str, Any] | None,
    project_cfg: dict[str, Any] | None,
) -> str:
    """Nivel de la cadena que PINEA el modelo efectivo (Ola D / ADR 0065).

    Devuelve ``"agent" | "team" | "project" | "platform"`` — el más específico que
    fije ``provider``+``model`` (misma precedencia que ``resolve_model_config_chain``;
    ``"platform"`` si ninguno de los niveles superiores pinea, por eso no necesita
    el default de plataforma). Sirve para mostrar el ORIGEN del modelo efectivo en
    el Hub de Capacidad, no para resolverlo.
    """
    if not config_needs_default_model(dict(agent_cfg or {})):
        return "agent"
    if team_cfg and not config_needs_default_model(dict(team_cfg)):
        return "team"
    if project_cfg and not config_needs_default_model(dict(project_cfg)):
        return "project"
    return "platform"


async def get_default_model_config(session: AsyncSession) -> dict[str, Any]:
    """El ``model_config`` por defecto seguro para un agente sin spec explícito.

    Lo lee ``POST /agents`` cuando el body no envía ``model_config`` y el
    dispatch cuando un agente legacy tiene ``{}``. Devuelve el override del
    System Admin si está configurado Y valida contra el catálogo; si nunca se
    configuró o el valor almacenado es inválido, cae al fallback de código
    ``DEFAULT_MODEL_CONFIG`` (también del catálogo). NUNCA devuelve ``{}`` ni
    levanta — el dispatch no debe fallar el arranque por un default mal puesto."""
    value = await get_platform_setting(session, MODEL_DEFAULT_CONFIG_KEY, default=None)
    if isinstance(value, dict) and value:
        try:
            return dict(validate_model_config(value))
        except InvalidModelConfigError:
            # Un default mal configurado no debe romper la creación ni el
            # dispatch; cae al fallback de código del catálogo.
            return dict(DEFAULT_MODEL_CONFIG)
    return dict(DEFAULT_MODEL_CONFIG)


# ---------------------------------------------------------------------------
# Estados de ejecución elegibles para memorización (Plan 06.17 task_06_17_04)
# ---------------------------------------------------------------------------
# El Memorizer solo destila ejecuciones "exitosas" — históricamente solo
# ``done`` (``policy.py``). Este setting hace ese conjunto OPERATOR-CONFIGURABLE
# (p.ej. añadir ``aborted`` para aprender de fallos): el worker lo lee en vivo y
# se lo pasa a ``should_memorize``. Default ``["done"]`` (backward-compat). Solo
# un System Admin lo escribe. Un valor que no sea una lista de
# :class:`~api_server.db.domain.ExecutionStatus` válidos cae al default.
MEMORY_MEMORIZABLE_STATUSES_KEY = "memory.memorizable_statuses"
DEFAULT_MEMORY_MEMORIZABLE_STATUSES: tuple[str, ...] = ("done",)


async def get_memorizable_statuses(session: AsyncSession) -> frozenset[str]:
    """Estados de ejecución que disparan memorización (default ``{"done"}``).

    Lo lee el worker del Memorizer en vivo y se lo pasa a ``should_memorize`` como
    conjunto elegible. Normaliza a un ``frozenset`` de estados
    :class:`~api_server.db.domain.ExecutionStatus` válidos; descarta entradas no
    reconocidas y, si la lista queda vacía o no es lista, cae al default
    ``{"done"}`` (nunca deja al Memorizer sin ningún estado elegible)."""
    from api_server.db.domain import ExecutionStatus

    value = await get_platform_setting(
        session,
        MEMORY_MEMORIZABLE_STATUSES_KEY,
        default=list(DEFAULT_MEMORY_MEMORIZABLE_STATUSES),
    )
    valid = {s.value for s in ExecutionStatus}
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list | tuple | set | frozenset):
        return frozenset(DEFAULT_MEMORY_MEMORIZABLE_STATUSES)
    statuses = {str(item).strip() for item in value if str(item).strip() in valid}
    return frozenset(statuses) if statuses else frozenset(DEFAULT_MEMORY_MEMORIZABLE_STATUSES)


# ---------------------------------------------------------------------------
# Plan approval — double-signature threshold (Plan 03 task_03_25)
# ---------------------------------------------------------------------------
PLAN_DOUBLE_SIGNATURE_THRESHOLD_KEY = "plan_approval_double_signature_threshold"
# Default 0 = single firma always. The operator raises this from the
# admin panel to force a second signer on expensive plans. Value is
# read as a Decimal in the currency of the plan's AI estimate.
DEFAULT_DOUBLE_SIGNATURE_THRESHOLD = "0"


# ---------------------------------------------------------------------------
# Execution time-limit backstop (Plan 06.14 task_06_14_04 / workers-orchestrator-10)
# ---------------------------------------------------------------------------
# Operator-tunable backstop applied per `run_execution` at enqueue time, so a
# change takes effect for new runs without restarting the workers. Generous on
# purpose — the agent-runtime enforces its own, tighter container_run_timeout_s;
# these only catch a truly wedged task. Soft → SoftTimeLimitExceeded the task
# can catch and finalise; hard → SIGKILL of the worker child.
EXECUTION_SOFT_TIME_LIMIT_KEY = "execution_soft_time_limit_s"
EXECUTION_HARD_TIME_LIMIT_KEY = "execution_hard_time_limit_s"
DEFAULT_EXECUTION_SOFT_TIME_LIMIT_S = 1800
DEFAULT_EXECUTION_HARD_TIME_LIMIT_S = 2100


async def get_execution_time_limits(session: AsyncSession) -> tuple[int, int]:
    """Return ``(soft_s, hard_s)`` for a dispatched ``run_execution``.

    Reads the operator overrides from platform settings, falling back to the
    defaults. Guarantees ``soft < hard`` (bumps hard if misconfigured) so
    Celery never rejects the limits."""
    soft = int(
        await get_platform_setting(
            session, EXECUTION_SOFT_TIME_LIMIT_KEY, default=DEFAULT_EXECUTION_SOFT_TIME_LIMIT_S
        )
    )
    hard = int(
        await get_platform_setting(
            session, EXECUTION_HARD_TIME_LIMIT_KEY, default=DEFAULT_EXECUTION_HARD_TIME_LIMIT_S
        )
    )
    if hard <= soft:
        hard = soft + 300
    return soft, hard


async def get_double_signature_threshold(session: AsyncSession) -> str:
    """Threshold (string-decimal) above which an AI cost estimate
    triggers the double-signature path. Returned as a string so the
    caller picks the right Decimal precision for its currency."""
    value = await get_platform_setting(
        session,
        PLAN_DOUBLE_SIGNATURE_THRESHOLD_KEY,
        default=DEFAULT_DOUBLE_SIGNATURE_THRESHOLD,
    )
    return str(value)


# ---------------------------------------------------------------------------
# Scheduled price-catalog sync (Plan 11 task_11_18)
# ---------------------------------------------------------------------------
# The price-sync beat job (workers.sync_model_prices) checks this flag at the
# top of every run, so a System Admin can turn the scheduled sync OFF (or back
# ON) from the admin panel and it takes effect on the next fire without
# restarting Celery beat. The CADENCE is a separate, operator-tunable knob
# (WORKERS_PRICE_SYNC_CRON read by the beat process at boot) — this flag is the
# live enable/disable lever. Default ON: keeping prices fresh is the desired
# behaviour, and an unconfirmed >10% spike is held for manual confirm anyway.
PRICE_SYNC_ENABLED_KEY = "price_sync_enabled"
DEFAULT_PRICE_SYNC_ENABLED = True


async def get_price_sync_enabled(session: AsyncSession) -> bool:
    """Whether the scheduled price-catalog sync is currently enabled.

    Read by the ``workers.sync_model_prices`` beat task before it does any
    work; when False the run is a no-op (it never fetches the feed or writes
    the catalog). A System Admin flips this from the admin panel — only a
    System Admin may write a platform setting (``set_platform_setting``).
    """
    value = await get_platform_setting(
        session, PRICE_SYNC_ENABLED_KEY, default=DEFAULT_PRICE_SYNC_ENABLED
    )
    return bool(value)


# ---------------------------------------------------------------------------
# Price-sync family allowlist override (plan price-sync-active-providers,
# task_psa_01)
# ---------------------------------------------------------------------------
# The price sync normally derives the LiteLLM families it imports from the
# ACTIVE ``llm_providers`` rows (ADR 0028 kind→family map). This OPTIONAL
# System-Admin override pins an explicit allowlist instead: when set (a list of
# family strings), it WINS over the derived set; when unset (the default), the
# resolver falls back to the families of the active providers. The value is read
# live by the resolver so a change takes effect on the next sync without a
# restart. An empty list ``[]`` is a meaningful override — it pins the allowlist
# to EMPTY (sync nothing), distinct from "unset" (derive from active providers).
PRICE_SYNC_ALLOWED_FAMILIES_KEY = "price_sync.allowed_families"


async def get_price_sync_allowed_families_override(
    session: AsyncSession,
) -> frozenset[str] | None:
    """The System-Admin family-allowlist override, or ``None`` when unset.

    Returns ``None`` when the setting has never been written (the resolver then
    derives the allowlist from the active ``llm_providers``). When written it is
    a list of family strings; this normalises it to a ``frozenset`` of trimmed,
    non-empty, lower-cased family names. A stored value that is not a list (or
    is empty after cleaning) still returns a frozenset — an empty override is
    deliberately meaningful (pin the allowlist to EMPTY)."""
    value = await get_platform_setting(session, PRICE_SYNC_ALLOWED_FAMILIES_KEY, default=None)
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list | tuple | set | frozenset):
        return None
    families = {
        str(item).strip().lower() for item in value if isinstance(item, str) and str(item).strip()
    }
    return frozenset(families)


# ---------------------------------------------------------------------------
# Scheduled exchange-rates fetch (Plan 11.1 task_11_1_02)
# ---------------------------------------------------------------------------
# Live enable/disable lever for the daily FX-fetcher job
# (workers.fetch_exchange_rates). The beat task reads it at the top of every
# run; OFF makes the run a no-op (no feed fetch, no catalog write) without
# restarting Celery beat — mirroring the price-sync / backup enable levers. The
# CADENCE (cron) is a separate operator-tunable knob (WORKERS_FX_FETCH_CRON read
# by the beat process at boot), NOT this flag. Default ON: an unattended
# platform should keep its display-currency rates fresh (USD stays canonical).
FX_FETCH_ENABLED_KEY = "fx_fetch_enabled"
DEFAULT_FX_FETCH_ENABLED = True

# The FX rate SOURCE the fetcher uses, selectable by a System Admin from the
# admin panel (Plan 11.1 decision: "fuente por defecto ECB, configurable por
# System Admin"). Read live by the FX-fetcher beat task so a change takes effect
# on the next fire without a restart. Only ECB is wired today; the key is a
# free-form string so a future source (e.g. a paid feed) needs no schema change,
# and an unknown value falls back to ECB rather than crashing the run.
FX_SOURCE_KEY = "fx_source"
DEFAULT_FX_SOURCE = "ecb"
# The FX sources the fetcher knows how to fetch. Kept in lockstep with
# workers.fx_fetcher.FX_FETCHER_SOURCES (the two packages deliberately do not
# import one another at module load).
FX_SOURCES = ("ecb",)


async def get_fx_fetch_enabled(session: AsyncSession) -> bool:
    """Whether the scheduled exchange-rates fetch is currently enabled.

    Read by the ``workers.fetch_exchange_rates`` beat task before it does any
    work; when False the run is a no-op (it never fetches the feed or writes the
    catalog). A System Admin flips this from the admin panel — only a System
    Admin may write a platform setting (``set_platform_setting``).
    """
    value = await get_platform_setting(
        session, FX_FETCH_ENABLED_KEY, default=DEFAULT_FX_FETCH_ENABLED
    )
    return bool(value)


async def get_fx_source(session: AsyncSession) -> str:
    """The configured FX rate source (default ECB).

    Read live by the FX-fetcher beat task so a System-Admin source change from
    the admin panel takes effect on the next run. An unknown / unset value
    falls back to the default ECB source (the fetcher never crashes on a typo).
    """
    value = await get_platform_setting(session, FX_SOURCE_KEY, default=DEFAULT_FX_SOURCE)
    source = str(value).strip().lower()
    return source if source in FX_SOURCES else DEFAULT_FX_SOURCE


# ---------------------------------------------------------------------------
# Budget alert thresholds (Plan 11.1 task_11_1_04)
# ---------------------------------------------------------------------------
# The percentages-of-budget at which the consumption evaluator (task_11_1_05)
# fires an alert via the Plan 10 notifier. PLATFORM-GLOBAL + configurable by a
# System Admin (Plan 11.1 decision): the same thresholds apply to every tenant
# and project, and a tenant cannot loosen them. Default [80, 90, 100]: warn at
# 80% and 90%, then 100% is the auto-pause trigger (task_11_1_06). Stored as a
# JSON array of ints. The 100% entry is what arms the auto-pause, so it is
# always present in the effective list even if a System Admin drops it.
BUDGET_ALERT_THRESHOLDS_KEY = "budget_alert_thresholds"
DEFAULT_BUDGET_ALERT_THRESHOLDS: tuple[int, ...] = (80, 90, 100)

# A threshold is a percentage of the budget. Below 1% is meaningless noise; we
# allow above 100% (an over-budget escalation alert is legitimate).
_BUDGET_THRESHOLD_MIN = 1
_BUDGET_THRESHOLD_MAX = 1000
# The 100% mark always stays in the effective list — it is the auto-pause arm.
_BUDGET_PAUSE_THRESHOLD = 100


class InvalidBudgetThresholdsError(ValueError):
    """Raised when a proposed budget-alert-threshold list fails validation
    (empty, a non-int entry, or a value outside the [1, 1000] range)."""


def validate_budget_alert_thresholds(values: list[int]) -> list[int]:
    """Validate + normalise a budget-alert-threshold list.

    Returns the de-duplicated, ascending list with the mandatory 100% pause
    arm guaranteed present. Raises :class:`InvalidBudgetThresholdsError` for an
    empty list, a non-int (``bool`` is rejected too), or an out-of-range value.
    """
    if not values:
        raise InvalidBudgetThresholdsError("at least one alert threshold is required")
    cleaned: set[int] = set()
    for v in values:
        # bool is an int subclass — reject it so True/False can't sneak in.
        if isinstance(v, bool) or not isinstance(v, int):
            raise InvalidBudgetThresholdsError(f"threshold {v!r} must be an integer")
        if v < _BUDGET_THRESHOLD_MIN or v > _BUDGET_THRESHOLD_MAX:
            raise InvalidBudgetThresholdsError(
                f"threshold {v} must be between "
                f"{_BUDGET_THRESHOLD_MIN} and {_BUDGET_THRESHOLD_MAX}"
            )
        cleaned.add(v)
    cleaned.add(_BUDGET_PAUSE_THRESHOLD)  # auto-pause arm always present
    return sorted(cleaned)


async def get_budget_alert_thresholds(session: AsyncSession) -> list[int]:
    """The effective budget-alert thresholds (ascending ints).

    Reads the System-Admin override, falling back to the platform default
    ``[80, 90, 100]``. Always normalised + guaranteed to include the 100% pause
    arm; a stored value that somehow fails validation falls back to the default
    rather than crashing the evaluator."""
    value = await get_platform_setting(
        session,
        BUDGET_ALERT_THRESHOLDS_KEY,
        default=list(DEFAULT_BUDGET_ALERT_THRESHOLDS),
    )
    try:
        return validate_budget_alert_thresholds(list(value))
    except (InvalidBudgetThresholdsError, TypeError):
        return list(DEFAULT_BUDGET_ALERT_THRESHOLDS)


async def set_budget_alert_thresholds(
    session: AsyncSession,
    values: list[int],
    *,
    actor: User,
) -> list[int]:
    """Persist the budget-alert thresholds (System Admin only).

    Validates the list FIRST (raising :class:`InvalidBudgetThresholdsError`
    before any write); ``set_platform_setting`` re-checks the actor is a System
    Admin. Returns the normalised list actually stored."""
    normalised = validate_budget_alert_thresholds(values)
    await set_platform_setting(session, BUDGET_ALERT_THRESHOLDS_KEY, normalised, actor=actor)
    return normalised


# ---------------------------------------------------------------------------
# Scheduled credential rotation (Plan 15 task_15_17)
# ---------------------------------------------------------------------------
# Live enable/disable lever for the periodic Vault credential-rotation job
# (workers.rotate_credentials). The beat task reads it at the top of every run;
# OFF makes the run a no-op (no Vault writes, no lease churn) without restarting
# Celery beat — mirroring the price-sync / backup enable levers. The CADENCE
# (cron) is a separate operator-tunable knob (WORKERS_CRED_ROTATION_CRON read by
# the beat process at boot), NOT this flag. Default ON: an unattended production
# platform should keep its static secrets fresh + its dynamic leases short-lived.
CRED_ROTATION_ENABLED_KEY = "cred_rotation_enabled"
DEFAULT_CRED_ROTATION_ENABLED = True


async def get_cred_rotation_enabled(session: AsyncSession) -> bool:
    """Whether the scheduled credential-rotation job is currently enabled.

    Read by the ``workers.rotate_credentials`` beat task before it does any
    work; when False the run is a no-op (no Vault writes, no lease renewal). A
    System Admin flips this from the admin panel — only a System Admin may write
    a platform setting (``set_platform_setting``).
    """
    value = await get_platform_setting(
        session, CRED_ROTATION_ENABLED_KEY, default=DEFAULT_CRED_ROTATION_ENABLED
    )
    return bool(value)


# ---------------------------------------------------------------------------
# Acceptance-timeout escalation sweep (Plan 16 task_16_06)
# ---------------------------------------------------------------------------
# Live enable/disable lever for the acceptance-timeout escalation beat job
# (workers.escalate_human_assignments). The beat task reads it at the top of
# every run; OFF makes the run a no-op (no reassignment, no block) without
# restarting Celery beat — mirroring the price-sync / fx-fetch / backup enable
# levers. The CADENCE (every 10 min, default) is a separate operator-tunable
# knob (WORKERS_HUMAN_ESCALATION_CRON read by the beat process at boot), NOT
# this flag. Default ON: an unattended platform should keep human tasks moving
# (escalate a forgotten assignment, block when escalation is exhausted).
HUMAN_ESCALATION_ENABLED_KEY = "human_escalation_enabled"
DEFAULT_HUMAN_ESCALATION_ENABLED = True


async def get_human_escalation_enabled(session: AsyncSession) -> bool:
    """Whether the acceptance-timeout escalation sweep is currently enabled.

    Read by the ``workers.escalate_human_assignments`` beat task before it does
    any work; when False the run is a no-op (no pending-acceptance assignment is
    reassigned or blocked). A System Admin flips this from the admin panel — only
    a System Admin may write a platform setting (``set_platform_setting``).
    """
    value = await get_platform_setting(
        session, HUMAN_ESCALATION_ENABLED_KEY, default=DEFAULT_HUMAN_ESCALATION_ENABLED
    )
    return bool(value)


# ---------------------------------------------------------------------------
# Scheduled backup (Plan 12 task_12_01 / task_12_04)
# ---------------------------------------------------------------------------
# Live enable/disable lever for the daily backup job, flipped by a System Admin
# from the admin panel (task_12_04). The beat task reads it at the top of every
# run; OFF makes the run a no-op without restarting Celery beat. Default ON —
# an unattended platform should be backing itself up. The CADENCE (cron) and
# the time WINDOW are separate operator-tunable knobs (`backup_cron` setting /
# WORKERS_* envs), NOT this flag.
BACKUP_ENABLED_KEY = "backup_enabled"
DEFAULT_BACKUP_ENABLED = True

# Operator-tunable cron for the daily backup, read by the beat process at boot
# (mirrors price_sync) AND re-read live by the backup beat task. Default daily
# at 03:00 (Plan 12: "Backup automático diario 03:00"). Stored as a 5-field
# cron string.
BACKUP_CRON_KEY = "backup_cron"
DEFAULT_BACKUP_CRON = "0 3 * * *"

# Local retention window in days (Plan 12: "Retención local 7 días"). Stored
# as a platform setting so a System Admin tunes it from the panel (task_12_04)
# rather than only via the WORKERS_BACKUP_RETENTION_DAYS env. The backup beat
# task reads it live so a change takes effect on the next run without a restart.
# Kept in lockstep with workers.config.Settings.backup_retention_days's default.
BACKUP_RETENTION_DAYS_KEY = "backup_retention_days"
DEFAULT_BACKUP_RETENTION_DAYS = 7

# Validation bounds for the retention window — never a magic literal scattered
# across the codebase. At least one day (a 0-day window would prune the bundle
# we just wrote); a generous upper bound keeps a typo from filling the disk.
BACKUP_RETENTION_DAYS_MIN = 1
BACKUP_RETENTION_DAYS_MAX = 3650


class InvalidBackupScheduleError(ValueError):
    """Raised when a proposed backup schedule fails validation (bad cron
    expression or an out-of-range retention window)."""


def validate_backup_cron(expr: str) -> str:
    """Validate a 5-field cron expression for the backup schedule.

    Returns the normalised (whitespace-collapsed) expression on success.
    Raises :class:`InvalidBackupScheduleError` for a non-5-field string or a
    field Celery's ``crontab`` parser rejects. We delegate the field-syntax
    check to ``celery.schedules.crontab`` — the SAME parser the beat process
    uses (``workers.beat_schedule._parse_cron``) — so a value the API accepts
    is one beat can actually schedule, never a "valid here / rejected there"
    mismatch.
    """
    parts = expr.split()
    if len(parts) != 5:
        raise InvalidBackupScheduleError(
            "cron must have exactly 5 fields: 'minute hour day-of-month month day-of-week'"
        )
    minute, hour, dom, month, dow = parts
    try:
        # Importing here keeps celery out of the api-server import graph for
        # callers that never touch the backup schedule.
        from celery.schedules import crontab

        crontab(
            minute=minute,
            hour=hour,
            day_of_month=dom,
            month_of_year=month,
            day_of_week=dow,
        )
    except Exception as exc:  # celery raises ValueError/KeyError on bad fields
        raise InvalidBackupScheduleError(f"invalid cron expression: {expr!r}") from exc
    return " ".join(parts)


def validate_backup_retention_days(value: int) -> int:
    """Validate the retention window is within [MIN, MAX]. Returns it on
    success; raises :class:`InvalidBackupScheduleError` otherwise."""
    if value < BACKUP_RETENTION_DAYS_MIN or value > BACKUP_RETENTION_DAYS_MAX:
        raise InvalidBackupScheduleError(
            f"retention_days must be between {BACKUP_RETENTION_DAYS_MIN} "
            f"and {BACKUP_RETENTION_DAYS_MAX}"
        )
    return value


async def get_backup_enabled(session: AsyncSession) -> bool:
    """Whether the scheduled daily backup is currently enabled.

    Read by the backup beat task before it does any work; when False the run
    is a no-op (no pg_dump, no tar, no disk writes). Only a System Admin may
    flip it (``set_platform_setting``).
    """
    value = await get_platform_setting(session, BACKUP_ENABLED_KEY, default=DEFAULT_BACKUP_ENABLED)
    return bool(value)


async def get_backup_cron(session: AsyncSession) -> str:
    """The configured backup cron (5-field string). Falls back to the default
    daily-03:00 schedule when unset. The beat process reads this at boot to
    build its schedule; the beat TASK also re-reads it live for its log/summary."""
    value = await get_platform_setting(session, BACKUP_CRON_KEY, default=DEFAULT_BACKUP_CRON)
    return str(value)


async def get_backup_retention_days(session: AsyncSession) -> int:
    """The configured local retention window in days. Falls back to the
    platform default when unset. The backup beat task reads this live so a
    change a System Admin makes from the panel takes effect on the next run
    (it overrides the WORKERS_BACKUP_RETENTION_DAYS env default)."""
    value = await get_platform_setting(
        session, BACKUP_RETENTION_DAYS_KEY, default=DEFAULT_BACKUP_RETENTION_DAYS
    )
    return int(value)


# ---------------------------------------------------------------------------
# Remote backup destinations (Plan 12 Phase B — task_12_09)
# ---------------------------------------------------------------------------
# After a successful, verified backup the bundle is uploaded to each configured
# + enabled remote destination (Plan 12: "destinos remotos opcionales (S3, B2,
# SFTP/NAS, rclone)"). A System Admin manages the list from the admin panel.
#
# What is stored here is the NON-secret config ONLY: a list of
#   {"type": "s3"|"b2"|"sftp"|"rclone", "name", "enabled", "config": {<knobs>}}
# dicts under one platform_settings key. The CREDENTIALS (S3 access key/secret,
# B2 keyId/key, SFTP password/private key, the rclone config blob) are NEVER
# stored here — they live in the workers' secret seam (Vault/env), keyed by each
# adapter's well-known field names. We reject any secret-looking field landing in
# the config so a credential can never be persisted (or echoed back) by accident.
BACKUP_DESTINATIONS_KEY = "backup_destinations"

# The destination types the platform supports. Kept in lockstep with
# workers.backup_destinations.DESTINATION_TYPES (the two packages deliberately
# do not import one another at module load).
BACKUP_DESTINATION_TYPES = ("s3", "b2", "sftp", "rclone")

# The NON-secret config field each destination type requires + the optional ones
# it accepts. Anything outside (required + optional) for a type is rejected — a
# guardrail that also blocks a secret field (access_key, password, ...) from ever
# being stored, because none of them appear in these allow-lists.
_DEST_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "s3": ("bucket",),
    "b2": ("bucket", "region"),
    "sftp": ("host", "username"),
    "rclone": ("remote",),
}
_DEST_OPTIONAL_FIELDS: dict[str, tuple[str, ...]] = {
    "s3": ("prefix", "endpoint_url", "region"),
    "b2": ("prefix",),
    "sftp": ("port", "remote_path", "host_key_policy", "known_hosts_path"),
    "rclone": ("path",),
}

# A destination name must be a short, stable identifier (logs/manifest key).
_DEST_NAME_MAX_LEN = 64
# Cap the number of configured destinations so a runaway client cannot bloat the
# single JSONB row.
_DEST_MAX_COUNT = 25


class InvalidBackupDestinationError(ValueError):
    """Raised when a proposed backup-destination config fails validation
    (unknown type, missing required field, an unexpected/secret-looking field,
    or a duplicate name)."""


def validate_backup_destinations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate + normalise a list of NON-secret destination configs.

    Each item must be ``{"type", "name", "enabled", "config": {...}}`` where
    ``config`` carries ONLY the type's known non-secret knobs. Returns the
    normalised list (stable key order, names trimmed) on success; raises
    :class:`InvalidBackupDestinationError` on any problem WITHOUT persisting.

    The unknown-field rejection is the credential guardrail: every adapter's
    secret field name (``backup_s3_access_key_id``, ``backup_sftp_password``,
    the rclone config blob, …) is outside the per-type allow-list, so a client
    that tries to smuggle a credential into ``config`` is a clean 422 — a secret
    can never reach this table.
    """
    if len(items) > _DEST_MAX_COUNT:
        raise InvalidBackupDestinationError(f"too many destinations (max {_DEST_MAX_COUNT})")
    normalised: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise InvalidBackupDestinationError(f"destination #{idx} must be an object")
        dest_type = str(item.get("type", "")).strip().lower()
        if dest_type not in BACKUP_DESTINATION_TYPES:
            raise InvalidBackupDestinationError(
                f"destination #{idx} has unknown type {dest_type!r}; "
                f"must be one of {BACKUP_DESTINATION_TYPES}"
            )
        name = str(item.get("name", "")).strip()
        if not name or len(name) > _DEST_NAME_MAX_LEN:
            raise InvalidBackupDestinationError(
                f"destination #{idx} requires a name (1..{_DEST_NAME_MAX_LEN} chars)"
            )
        if name in seen_names:
            raise InvalidBackupDestinationError(f"duplicate destination name {name!r}")
        seen_names.add(name)

        raw_config = item.get("config", {})
        if not isinstance(raw_config, dict):
            raise InvalidBackupDestinationError(f"destination {name!r} config must be an object")
        allowed = set(_DEST_REQUIRED_FIELDS[dest_type]) | set(_DEST_OPTIONAL_FIELDS[dest_type])
        config: dict[str, Any] = {}
        for key, value in raw_config.items():
            if key not in allowed:
                # Outside the non-secret allow-list — either a typo or an
                # attempt to store a credential. Reject either way.
                raise InvalidBackupDestinationError(
                    f"destination {name!r}: field {key!r} is not allowed for "
                    f"type {dest_type!r} (secrets are never stored here)"
                )
            config[key] = value
        for required in _DEST_REQUIRED_FIELDS[dest_type]:
            rv = config.get(required)
            if rv is None or (isinstance(rv, str) and not rv.strip()):
                raise InvalidBackupDestinationError(
                    f"destination {name!r} of type {dest_type!r} is missing "
                    f"required field {required!r}"
                )
        normalised.append(
            {
                "type": dest_type,
                "name": name,
                "enabled": bool(item.get("enabled", True)),
                "config": config,
            }
        )
    return normalised


async def get_backup_destinations(session: AsyncSession) -> list[dict[str, Any]]:
    """The configured remote backup destinations (NON-secret config only).

    Returns the stored list, or ``[]`` when none have been configured. Each item
    is ``{"type", "name", "enabled", "config": {...}}`` — never a credential."""
    value = await get_platform_setting(session, BACKUP_DESTINATIONS_KEY, default=[])
    return list(value) if isinstance(value, list) else []


async def set_backup_destinations(
    session: AsyncSession,
    items: list[dict[str, Any]],
    *,
    actor: User,
) -> list[dict[str, Any]]:
    """Persist the full destination list (System Admin only).

    Validates EVERY item first (raising :class:`InvalidBackupDestinationError`
    before any write); ``set_platform_setting`` re-checks the actor is a System
    Admin. Returns the normalised list. CREDENTIALS are never part of the stored
    config (validation rejects any secret-looking field), so this write — and the
    read-back — can never echo a secret."""
    normalised = validate_backup_destinations(items)
    await set_platform_setting(session, BACKUP_DESTINATIONS_KEY, normalised, actor=actor)
    return normalised


async def get_backup_schedule(session: AsyncSession) -> tuple[bool, str, int]:
    """Return the full backup schedule as ``(enabled, cron, retention_days)``.

    The single read the get-schedule endpoint and the beat task both use, so
    the API surface and the scheduled run agree on the same stored config."""
    return (
        await get_backup_enabled(session),
        await get_backup_cron(session),
        await get_backup_retention_days(session),
    )


async def set_backup_schedule(
    session: AsyncSession,
    *,
    enabled: bool,
    cron: str,
    retention_days: int,
    actor: User,
) -> tuple[bool, str, int]:
    """Persist the full backup schedule (System Admin only).

    Validates the cron + retention window FIRST (raising
    :class:`InvalidBackupScheduleError` on a bad value, before any write), then
    writes all three settings on the actor's session. ``set_platform_setting``
    re-checks the actor is a System Admin, so a non-admin never reaches the
    write. Returns the normalised ``(enabled, cron, retention_days)``."""
    normalised_cron = validate_backup_cron(cron)
    validated_retention = validate_backup_retention_days(retention_days)

    await set_platform_setting(session, BACKUP_ENABLED_KEY, bool(enabled), actor=actor)
    await set_platform_setting(session, BACKUP_CRON_KEY, normalised_cron, actor=actor)
    await set_platform_setting(session, BACKUP_RETENTION_DAYS_KEY, validated_retention, actor=actor)
    return bool(enabled), normalised_cron, validated_retention
