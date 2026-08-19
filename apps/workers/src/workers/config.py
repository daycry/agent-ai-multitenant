"""Runtime configuration for the Celery workers service.

Env-driven via pydantic-settings, prefix `WORKERS_`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Substrings flagging a known dev-only default — forbidden in staging/prod
# (Plan 06.14 task_06_14_03 / secrets-config-5).
_DEV_SECRET_MARKERS = ("changeme", "dev-only")

# The CLOSED set of deployment environments, and the fail-CLOSED predicate — the
# same posture `api_server.config` adopted in prod-09 task_prod09_02 (authz-2) and
# that this service was left without. Written as "everything except dev" rather
# than "in {staging, prod}" on purpose: the old shape meant any UNRECOGNISED value
# (`production`, an empty var, `prod ` with a trailing space) silently meant dev
# and skipped the guard below. A typo downgraded the posture with no log line —
# and the installer's own enum says `production`, so it was not hypothetical.
_KNOWN_ENVIRONMENTS = frozenset({"dev", "staging", "prod"})
_DEV_ENVIRONMENT = "dev"


class Settings(BaseSettings):
    """All env-driven knobs for the workers / Celery app."""

    # ----- Broker + result backend (both Redis) -----
    broker_url: str = Field(
        default="redis://localhost:6379/1",
        description="Celery broker URL. Redis DB 1 — kept off DB 0 "
        "(sessions / rate-limit / event bus) so a FLUSHDB on one "
        "doesn't nuke the other.",
    )
    result_backend: str = Field(
        default="redis://localhost:6379/2",
        description="Celery result backend URL. Redis DB 2.",
    )

    # ----- Execution persistence + live stream (Plan 02 Fase G) -----
    database_url: str = Field(
        default="postgresql+asyncpg://service_user:changeme-service-dev-only"
        "@localhost:5432/agentic_platform",
        description="PostgreSQL URL the worker persists `executions` rows to. "
        "`service_user`: BYPASSRLS but NO DDL (prod-14 task_05 / tenancy-2). "
        "BYPASSRLS is required — the worker writes execution records across "
        "tenants with no request-scoped `app.tenant_id` to bind to. What it does "
        "NOT need, and used to have as `migrations_user` (schema owner, GRANT "
        "ALL), is the ability to run `ALTER TABLE ... DISABLE ROW LEVEL "
        "SECURITY` and switch off multi-tenant isolation platform-wide.",
    )
    events_redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL hosting the per-execution event streams "
        "(`exec:{id}`). DB 0 — the same instance the api-server WebSocket "
        "tails, kept off the broker (DB 1) and result backend (DB 2).",
    )

    # ----- Ingesta: modo de fallo del antivirus (prod-12 av_01 / ADR 0105) ---
    # fail_closed (default): con ClamAV inalcanzable el documento queda en
    # `pending_scan` (NO se indexa) y el sweep lo reintenta. fail_open: se
    # indexa con warning — SOLO aceptable en dev/sandbox.
    av_failure_mode: Literal["fail_closed", "fail_open"] = Field(
        default="fail_closed",
        description="Qué hacer si el backend antivirus no responde durante la "
        "ingesta: fail_closed = pending_scan + reintento (default producción); "
        "fail_open = indexar con warning (solo dev/sandbox).",
    )
    # Cola del notification-dispatcher (misma que usa el orchestrator) para el
    # aviso de antivirus inalcanzable > N minutos.
    notifications_event_queue: str = Field(
        default="notifications",
        description="Celery queue the notification-dispatcher drains.",
    )

    # ----- Agent-runtime containers (Plan 02 Fase B) -----
    agent_runtime_image: str = Field(
        default="agent-runtime:v1",
        description="Image the worker launches for each agent task.",
    )
    browser_runtime_image: str = Field(
        default="browser-runtime:v1",
        description=(
            "Imagen del navegador sandboxeado (ADR 0080). Se lanza EFÍMERA, una por "
            "sesión de navegación, y solo tras la aprobación del owner."
        ),
    )
    browse_session_timeout_s: int = Field(
        default=240,
        description=(
            "Techo de reloj de una sesión de navegación cuando no pide el suyo "
            "(el runtime acota además páginas y bytes)."
        ),
    )
    agent_network: str = Field(
        default="agentic-agents",
        description="Dedicated Docker network for agent containers — kept "
        "off agentic-net so agents cannot reach Postgres/Redis/Vault or "
        "the platform services.",
    )
    agent_internal_api_url: str = Field(
        default="http://api-server:8000",
        description="URL INTERNA del api-server que el contenedor agent-runtime "
        "alcanza para la API interna del agente (``/internal/agent/*``: "
        "rag-search, memory-recall/store, document-convert, promote-to-kb). El "
        "worker la inyecta como ``AGENTIC_API_URL`` junto al "
        "``AGENTIC_INTERNAL_TOKEN`` minteado (ADR 0012, Plan 04.5). Debe ser "
        "alcanzable desde la red del sandbox (la red del compose), no la URL "
        "pública. Operator-tunable; default = el hostname del servicio en el "
        "compose. El token se firma con el ``jwt_secret`` del api-server, así que "
        "el worker necesita ``API_SERVER_JWT_SECRET`` (mismo secreto que "
        "api-server) en su entorno para que el token valide.",
    )
    agent_network_internal: bool = Field(
        default=True,
        description="Create the agent network as `internal` (no egress to "
        "the host or the internet). El egress controlado a proveedores LLM "
        "y a la allowlist de `http_request` va por `egress_proxy_url` "
        "(ADR 0019 / task_02_35), no abriendo esta red.",
    )
    egress_proxy_url: str = Field(
        default="",
        description="URL del proxy de egress allowlisted que el sandbox "
        "agent-runtime usa para alcanzar a los proveedores LLM y a los "
        "dominios de `http_request`. Cuando está vacío NO se inyecta "
        "HTTP_PROXY en el contenedor — los ModelClient reales no podrán "
        "salir desde dentro del sandbox y sólo funcionará el "
        "ScriptedModelClient (ADR 0019). En producción: "
        "`http://egress-proxy:8888` (el servicio del compose, task_02_35).",
    )
    registry_proxy_url: str = Field(
        default="http://registry-proxy:8888",
        description="URL del registry-proxy (allowlist de registries de "
        "paquetes, ADR 0094) que el worker inyecta como HTTP(S)_PROXY en los "
        "runtime-templates cuando un launch pide egress (dep_egress). Apunta al "
        "alias con el que el worker conecta el proxy al bridge per-task. Vacío "
        "= sin egress (los installs en frío fallan offline). El host debe "
        "coincidir con `registry_proxy_alias`.",
    )
    registry_proxy_container: str = Field(
        default="agentic-registry-proxy",
        description="Nombre del contenedor del registry-proxy que el worker "
        "resuelve por la API Docker (`containers.get`) para conectarlo al "
        "bridge efímero de cada tarea (ADR 0094).",
    )
    registry_proxy_alias: str = Field(
        default="registry-proxy",
        description="Alias de red con el que el worker conecta el registry-proxy "
        "al bridge per-task; el runtime lo resuelve por DNS embebido de docker "
        "para alcanzar el proxy. Debe ser el host de `registry_proxy_url`.",
    )
    container_mem_limit: str = Field(
        default="512m",
        description="Hard memory cap for an agent container (a leak or a "
        "runaway model can't take the host down).",
    )
    container_pids_limit: int = Field(
        default=256,
        description="Max process count inside an agent container — caps fork bombs.",
    )
    test_runtime_pids_limit: int = Field(
        default=1024,
        description="Max process count inside a TEST/STACK runtime container "
        "(task_wf_21, C-02). Deliberately higher than container_pids_limit: a test "
        "container legitimately spawns far more processes than the agent loop "
        "(parallel compilers, watchers, test servers), so inheriting the agent's "
        "256 would trade a real risk for a false test failure. Still a hard cap: "
        "without one, a runaway `make -j` or a fork bomb in the repo under test "
        "had nothing stopping it. Override with WORKERS_TEST_RUNTIME_PIDS_LIMIT.",
    )
    container_tmp_size: str = Field(
        default="64m", description="Size of the /tmp tmpfs in an agent container."
    )
    container_workspace_size: str = Field(
        default="256m",
        description="Size of the /workspace tmpfs when no host workspace is "
        "bind-mounted (real worktree mounts arrive in Plan 06).",
    )
    container_run_timeout_s: int = Field(
        default=600,
        description="Default wall-clock budget for one container run before "
        "the worker kills it. Per-task overrides land with the Fase C "
        "safeguards (task_02_13). Applies to the fast HTTP providers "
        "(ollama/azure_foundry/copilot); claude_sdk uses the longer budget below.",
    )
    container_run_timeout_claude_sdk_s: int = Field(
        default=7200,
        description="Wall-clock budget for a `claude_sdk` agent container (2h). Much "
        "longer than the base timeout: the Claude Agent SDK spawns the Claude Code "
        "CLI (Node) and its high-effort / xhigh-reasoning model calls are slow "
        "(~1-2 min each), whereas the HTTP providers finish well within "
        "container_run_timeout_s. This value caps BOTH the container and the agent "
        "loop's internal wall-clock safeguard (execution.py aligns them). Override "
        "with WORKERS_CONTAINER_RUN_TIMEOUT_CLAUDE_SDK_S.",
    )
    container_grace_s: int = Field(
        default=120,
        description="Grace margin (seconds) the worker adds ON TOP of the "
        "per-provider wall-clock budget to compute the container's HARD kill "
        "timeout. The internal agent-loop wall-clock (the per-kind budget) must "
        "fire FIRST so a budget exhaustion aborts cleanly inside the loop "
        "(`max_wall_clock_exceeded`, keeping partials + finish_status) instead of "
        "the container's hard kill always winning and mislabelling every "
        "exhaustion as 'container timed out' (F19). Override with "
        "WORKERS_CONTAINER_GRACE_S.",
    )
    container_home_size: str = Field(
        default="64m",
        description="Size of the agent container's HOME tmpfs (/home/agent). The "
        "Claude Code CLI writes its config (.claude.json, .claude/) into HOME; "
        "keeping HOME on its own tmpfs OUTSIDE /workspace stops that config from "
        "polluting the project worktree (and the agent's model context).",
    )
    test_runtime_tmp_size: str = Field(
        default="256m",
        description="Size of the TEST/STACK runtime container's /tmp tmpfs. F3 de "
        "registry-egress-followups: estaba escrito como literal de 64m y por ahí pasan "
        "`composer install` y `npm ci`, que descargan y EXTRAEN en /tmp — composer ya "
        "avisaba 'less than 100MiB of free space'. 256m deja holgura para un árbol de "
        "deps normal sin acercarse al mem_limit del contenedor: las páginas del tmpfs "
        "cuentan contra su cgroup de memoria, así que un /tmp desproporcionado cambia un "
        "ENOSPC legible por un OOM-kill mudo (exit 137 sin mensaje). Un monorepo grande "
        "puede necesitar más: WORKERS_TEST_RUNTIME_TMP_SIZE. El invariante 'nunca más de "
        "la mitad del mem_limit' lo fija tests/unit/test_test_runtime_tmp_size.py.",
    )
    test_runtime_home_size: str = Field(
        default="512m",
        description="Size of the TEST/STACK runtime container's HOME tmpfs "
        "(/home/agent). Deliberately much larger than container_home_size: the "
        "agent container's HOME holds a CLI config (tens of KB), whereas a "
        "toolchain's home holds dependency metadata and, when the project has no "
        "warm dep-cache bind, the download cache itself (composer/npm/pip/maven). "
        "Capping it at 64m would swap 'the worktree gets polluted' for "
        "'a cold install fails with ENOSPC' (task_wf_20, C-01). The heavy path is "
        "still the dep_cache bind mounted ON TOP of this tmpfs. Override with "
        "WORKERS_TEST_RUNTIME_HOME_SIZE.",
    )

    agent_max_iterations_claude_sdk: int = Field(
        default=50,
        description="Agent-loop iteration cap for `claude_sdk` runs. The runtime "
        "default (25) cut off multi-file tasks right as they finished writing — "
        "the agent produced all deliverables but couldn't reach the final FINISH "
        "turn, leaving the run `aborted` (max_iterations_exceeded) instead of "
        "`done`. claude_sdk writes one file per iteration and is slow, so it needs "
        "more headroom; the nudge + loop-detector keep the extra iterations "
        "productive. Override with WORKERS_AGENT_MAX_ITERATIONS_CLAUDE_SDK.",
    )
    agent_max_iterations_review: int = Field(
        default=25,
        description="Agent-loop iteration cap para runs de REVIEW (F2b.5, "
        "auditoría 2026-07-02). El reviewer corría con el presupuesto del "
        "implementador (50 iter) cuando la evidencia post-ADR-0095 muestra "
        "reviews convergiendo en 13-22 steps — la mitad basta y acota el coste "
        "de un reviewer atascado. Override con "
        "WORKERS_AGENT_MAX_ITERATIONS_REVIEW.",
    )
    agent_max_tokens_claude_sdk: int = Field(
        default=500_000,
        description="Presupuesto de tokens (in+out acumulados) para un run "
        "implementador claude_sdk. El default del runtime (100k) se calibró "
        "cuando la contabilidad de usage medía 0 (bug F1.4); con tokens REALES "
        "un run sano de ~23 iteraciones ya cruza 100k (observado en el e2e del "
        "2026-07-02: 102.957 tok) — 500k da margen a las 50 iteraciones; el "
        "guardarraíl de coste (max_cost_usd) sigue acotando el gasto. Override "
        "con WORKERS_AGENT_MAX_TOKENS_CLAUDE_SDK.",
    )
    agent_max_tokens_review: int = Field(
        default=250_000,
        description="Presupuesto de tokens para un run de REVIEW claude_sdk "
        "(la mitad del de implementador; las reviews convergen en 13-22 "
        "steps). Override con WORKERS_AGENT_MAX_TOKENS_REVIEW.",
    )
    container_run_timeout_review_claude_sdk_s: int = Field(
        default=3600,
        description="Wall-clock budget para un run de REVIEW claude_sdk (1h, "
        "F2b.5): la mitad del budget de implementador (2h) — un review lee y "
        "juzga, no escribe N ficheros. Override con "
        "WORKERS_CONTAINER_RUN_TIMEOUT_REVIEW_CLAUDE_SDK_S.",
    )

    def container_timeout_for_kind(self, kind: str | None, *, is_review: bool = False) -> int:
        """Per-provider container wall-clock budget. ``claude_sdk`` gets the
        longer SDK timeout (Node CLI + slow high-effort/xhigh calls); every other
        kind uses the base ``container_run_timeout_s``. Un run de REVIEW
        claude_sdk usa su budget propio, más corto (F2b.5)."""
        if kind == "claude_sdk":
            if is_review:
                return self.container_run_timeout_review_claude_sdk_s
            return self.container_run_timeout_claude_sdk_s
        return self.container_run_timeout_s

    def container_timeout_with_grace_for_kind(
        self, kind: str | None, *, is_review: bool = False
    ) -> int:
        """The container's HARD wall-clock kill timeout for ``kind``: the
        per-provider budget (:meth:`container_timeout_for_kind`) PLUS
        ``container_grace_s``. The internal agent-loop wall-clock uses the bare
        budget, so it aborts cleanly (``max_wall_clock_exceeded``, with partials /
        finish_status) BEFORE the container's hard kill fires — otherwise the kill
        always wins and every exhaustion is mislabelled 'container timed out' (F19)."""
        return self.container_timeout_for_kind(kind, is_review=is_review) + self.container_grace_s

    def agent_max_iterations_for_kind(
        self, kind: str | None, *, is_review: bool = False
    ) -> int | None:
        """Per-provider agent-loop iteration cap. ``claude_sdk`` gets a higher cap
        so multi-file tasks write every deliverable AND reach the final FINISH
        turn; other kinds return ``None`` (the runtime keeps its built-in default).
        Un run de REVIEW usa el cap de review, más bajo (F2b.5)."""
        if kind == "claude_sdk":
            if is_review:
                return self.agent_max_iterations_review
            return self.agent_max_iterations_claude_sdk
        return None

    def agent_max_tokens_for_kind(self, kind: str | None, *, is_review: bool = False) -> int | None:
        """Presupuesto de tokens por-provider (auditoría 2026-07-02). Solo
        ``claude_sdk`` necesita un override: con la contabilidad de usage
        arreglada (F1.4), su volumen real de tokens por iteración desborda el
        default de 100k del runtime a mitad de un run sano. Otros kinds →
        ``None`` (default del runtime)."""
        if kind == "claude_sdk":
            if is_review:
                return self.agent_max_tokens_review
            return self.agent_max_tokens_claude_sdk
        return None

    seccomp_profile_path: str = Field(
        default="",
        description="Path to a custom seccomp JSON profile for the untrusted "
        "agent/test runtime. Empty = rely on Docker's built-in default-deny "
        "(SCMP_ACT_ERRNO) profile. The platform ships a STRICTER hand-tightened "
        "profile at docker/seccomp/agent-runtime.json (Plan 15 task_15_15); "
        "point WORKERS_SECCOMP_PROFILE at its in-container path to pin it. The "
        "worker forwards the file CONTENT to the daemon (isolation.py).",
    )
    model_credential_file: bool = Field(
        default=True,
        description="prod-07 task_prod07_10: entregar la credencial del proveedor "
        "LLM en un fichero read-only bajo /run/secrets en vez de dentro de "
        "AGENT_TASK_SPEC (donde la ve cualquier `docker inspect`). El env sólo "
        "lleva el puntero. VÁLVULA DE ESCAPE: ponlo a false si el stack corre una "
        "imagen agent-runtime ANTERIOR a este cambio — esa imagen ignora el "
        "puntero y el run arranca sin credencial (401 dentro del sandbox). El "
        "orden de despliegue seguro es imagen primero, worker después: la imagen "
        "nueva entiende los DOS formatos.",
    )
    apparmor_profile: str = Field(
        default="",
        description="AppArmor profile NAME to pin for the untrusted agent/test "
        "runtime (forwarded as security_opt apparmor=<name> by isolation.py). "
        "Empty = Docker's automatic docker-default profile where the host kernel "
        "supports AppArmor. The platform ships a STRICTER hand-written profile at "
        "docker/apparmor/agent-runtime.profile (Plan 15 task_15_16); load it on "
        "the host with apparmor_parser and set WORKERS_APPARMOR_PROFILE="
        "agent-runtime to pin it.",
    )

    # ----- Test-runtime aux services + DinD proxy hardening (Plan 06.14
    # task_06_14_11 / container-isolation-1/2). These sidecars are transient
    # and live only on the task's private bridge, but they still get the
    # cap-drop + no-new-privileges + mem/pids envelope so a runaway or
    # malicious test cannot exhaust the host. Tunable by the operator; the
    # per-service AuxServiceSpec may still override the limits per service. -----
    aux_postgres_mem_limit: str = Field(
        default="256m",
        description="Hard memory cap for the postgres-test aux sidecar.",
    )
    aux_redis_mem_limit: str = Field(
        default="128m",
        description="Hard memory cap for the redis-test aux sidecar.",
    )
    aux_default_pids_limit: int = Field(
        default=128,
        description="Max process count inside an aux-service container — caps fork bombs.",
    )

    # ----- Memorizer (Plan 04.5 task_04_5_02) -----
    memorizer_llm_base_url: str = Field(
        default="http://localhost:11434/v1",
        description="OpenAI-compatible base URL the Memorizer distillation "
        "step calls. Defaults to local Ollama (`ollama serve`). Override in "
        "envs without a local Ollama by pointing at a managed endpoint.",
    )
    memorizer_llm_model: str = Field(
        default="llama3.1",
        description="Model id the Memorizer's FALLBACK distiller asks for. "
        "Auditoría 2026-07-02 (F2.1): el camino primario es el provider del "
        "AGENTE de la execution (memorizer_use_agent_provider); este modelo "
        "local solo se usa cuando aquel no está disponible.",
    )
    memorizer_use_agent_provider: bool = Field(
        default=True,
        description="F2.1 (auditoría 2026-07-02): destilar memorias con el LLM "
        "del AGENTE de la execution (resolución por provider_id, ADR 0082) en "
        "vez del modelo local fijo — el 1b local producía ~50% ruido "
        "(tautologías, URLs fabricadas) que contaminaba el recall. Desactívalo "
        "para volver al modelo local (sin cuota, sin egress). Override con "
        "WORKERS_MEMORIZER_USE_AGENT_PROVIDER.",
    )

    # ----- Córtex F2: distilador afectivo (ADR 0075) -----
    # El distilador afectivo (``workers.cortex_distill_affect``) puntúa cada turno
    # del córtex contra los drives/identidad y emite un ``delta PAD + razón`` que
    # el motor determinista aplica. Corre POST-turno, FAIL-OPEN (Ollama caído ⇒
    # delta=0) y SIN egress: usa Ollama LOCAL (ya en el catálogo cerrado, ADR
    # 0021), un modelo pequeño y barato (el razonamiento profundo sigue saliendo
    # de claude_sdk en F1). Estos envs son sólo el cableado del host hacia Ollama;
    # default local sin egress.
    cortex_affect_llm_base_url: str = Field(
        default="http://localhost:11434/v1",
        description="OpenAI-compatible base URL que el distilador afectivo del "
        "córtex (ADR 0075) llama. Default Ollama LOCAL (`ollama serve`) — sin "
        "egress. Apúntalo a un endpoint gestionado en entornos sin Ollama local.",
    )
    cortex_affect_llm_model: str = Field(
        default="llama3.1",
        description="Modelo que el distilador afectivo pide. El appraisal es "
        "barato; un modelo local pequeño es la decisión correcta (sin cuota, sin "
        "egress). El catálogo LLM cerrado (ADR 0021) queda intacto.",
    )

    # ----- Córtex F4: bucles cognitivos de fondo (ADR 0078) -----
    # Las tres tareas autónomas (curiosidad / reflexión programada / mantenimiento)
    # corren por Celery beat a una cadencia operator-tunable; el ENABLE real es el
    # kill-switch `cortex.autonomy_enabled` (platform setting, default OFF) leído EN
    # VIVO dentro de cada tarea — la entrada del beat siempre existe (como price-sync),
    # pero con el kill-switch OFF cada pasada sale no-op. Estos crons los lee el beat
    # PROCESS al boot. La curiosidad consume coste/egress, así que su cadencia es
    # conservadora por defecto.
    cortex_curiosity_cron: str = Field(
        default="*/30 * * * *",
        description="Cron (minute hour day-of-month month day-of-week) del bucle de "
        "curiosidad autónoma del córtex. Default cada 30 minutos. Operator-tunable; "
        "el enable real es el kill-switch `cortex.autonomy_enabled` (platform setting).",
    )
    cortex_reflection_cron: str = Field(
        default="17 */6 * * *",
        description="Cron de la reflexión periódica de identidad del córtex. Default "
        "cada 6 horas (minuto 17 para descolgarlo de otros jobs). Operator-tunable.",
    )
    cortex_maintenance_cron: str = Field(
        default="42 4 * * *",
        description="Cron del mantenimiento de fondo del córtex (decay snapshot, "
        "olvido/consolidación, poda). Default diario 04:42 UTC. Operator-tunable.",
    )
    cortex_curiosity_cb_cooldown_s: int = Field(
        default=3600,
        description="Cooldown (segundos) que el circuit-breaker de la curiosidad "
        "permanece ABIERTO tras N fallos consecutivos. Default 1h. Operator-tunable.",
    )

    # ----- Back-fill de embeddings de memoria (Plan 06.17 task_06_17_03) -----
    # El worker dedicado ``workers.backfill_memory_embeddings`` rellena los
    # ``memory_entries.embedding`` NULL embebiendo el contenido con Ollama
    # (mismo embedder que la ingesta de KBs: ``OllamaEmbedder`` → ``/api/embed``,
    # default ``nomic-embed-text-v1.5``, 768 dims). Esta es la BASE URL de Ollama
    # (sin ``/v1``, distinta del endpoint de chat del Memorizer). El flag ON/OFF,
    # el batch y el throttle son PLATFORM settings que un System Admin posee
    # (``memory.backfill_*``); este env es solo el cableado del host hacia Ollama.
    memory_embedder_base_url: str = Field(
        default="http://localhost:11434",
        description="Base URL de Ollama que el back-fill de embeddings de memoria "
        "usa (endpoint ``/api/embed``). Apunta a un Ollama local por defecto.",
    )

    # ----- Plan 06 / 06.5: shared data root for worktrees + dep-cache -----
    data_root: str = Field(
        default="/data/agent-platform",
        description=(
            "Host filesystem root for platform-managed state: bare repos, "
            "worktrees, dep-cache. The maintenance beat tasks "
            "(prune_worktrees, purge_dep_cache) resolve their working "
            "directories under this root."
        ),
    )

    # ----- ADR 0072: Vault para resolver la credencial git del proyecto -----
    # El worker lee el secreto git (PAT/SSH) de Vault al clonar/fetch. Sin estos
    # (None) la task de clone no puede autenticar repos privados (sí públicos).
    vault_url: str | None = Field(
        default=None,
        description="URL de Vault para resolver la credencial git del proyecto (ADR 0072).",
    )
    vault_token: str | None = Field(
        default=None,
        description="Token de Vault (dev/install). Secreto — no loguear.",
    )

    # ----- Scheduled price-catalog sync (Plan 11 task_11_18) -----
    # The price-sync beat job runs the LiteLLM-feed sync (ADR 0021: data feed
    # only, NOT a provider runtime) on a CONFIGURABLE cadence. The cron string
    # is read by the beat process at boot — change it (and restart beat) to
    # alter the cadence. The live enable/disable lever is the `price_sync_enabled`
    # PLATFORM setting (a System Admin flips it from the admin panel and it takes
    # effect on the next fire without a restart) — NOT this env. A scheduled run
    # applies non-spiking changes automatically but DEFERS a >10% rise for manual
    # confirmation (the task_11_16 gate), even when scheduled.
    price_sync_cron: str = Field(
        default="0 4 * * *",
        description="Cron (minute hour day-of-month month day-of-week) for the "
        "scheduled price-catalog sync. Default daily at 04:00 UTC. Operator-tunable; "
        "the beat process reads it at boot.",
    )
    litellm_price_feed_url: str = Field(
        default=(
            "https://raw.githubusercontent.com/BerriAI/litellm/main/"
            "model_prices_and_context_window.json"
        ),
        description="URL of the community LiteLLM price JSON consumed strictly as a "
        "DATA FEED (ADR 0021) by the scheduled sync — never a provider runtime. "
        "Point at an internal mirror to avoid egress.",
    )

    # ----- Scheduled exchange-rates fetch (Plan 11.1 task_11_1_02) -----
    # The FX-fetcher beat job downloads the daily reference rates from the
    # configured source (ECB by default) and upserts `exchange_rates` (a global
    # catalog; ADR USD-canonical). The CRON cadence is read by the beat process
    # at boot — change it (and restart beat) to alter the cadence. The live
    # enable/disable lever + the SOURCE selection are PLATFORM settings a System
    # Admin owns (`fx_fetch_enabled` / `fx_source`); these envs are only the
    # boot-time defaults + the per-source feed URL. Best-effort: a fetch failure
    # logs + alerts (a platform-scoped ops signal) but never crashes beat.
    fx_fetch_cron: str = Field(
        default="0 6 * * *",
        description="Cron (minute hour day-of-month month day-of-week) for the "
        "scheduled exchange-rates fetch. Default daily at 06:00 UTC (Plan 11.1). "
        "Operator-tunable; the beat process reads it at boot.",
    )
    # ADR 0098 (eje 3): cadencia del barrido periodico de fetch de remotos git
    # (`workers.sweep_project_git_remotes`). El interruptor vivo es el platform
    # setting `git_fetch_sweep_enabled` (default OFF) que un System Admin flipa
    # desde el panel; este cron solo fija CUANDO se evalua. Conservador (cada
    # 30 min): el coste crece con el numero de proyectos con remoto.
    # ADR 0110 (mitad HTTP, EXPERIMENTAL): hilo conversacional en memoria por
    # run en los providers HTTP (azure_foundry/copilot/ollama). OFF por defecto
    # — encenderlo cambia el shape del prompt por turno (KV-cache reutilizable,
    # historial real) y debe validarse con runs e2e antes de generalizarlo.
    runtime_conversation_thread: bool = Field(
        default=False,
        description="EXPERIMENTAL (ADR 0110): per-run in-memory conversation "
        "thread for HTTP providers in the agent runtime. OFF by default.",
    )
    # ADR 0112 fase 2 (EXPERIMENTAL, OFF): mini-turno dedicado de reflexion en
    # los providers HTTP cada 10 iteraciones + escalado determinista tras 2
    # veredictos "stuck" consecutivos (abort_code reflection_stalled).
    runtime_reflection_assess: bool = Field(
        default=False,
        description="EXPERIMENTAL (ADR 0112 fase 2): dedicated progress "
        "self-assessment mini-turn for HTTP providers. OFF by default.",
    )
    git_fetch_cron: str = Field(
        default="*/30 * * * *",
        description="Cron (minute hour day-of-month month day-of-week) for the "
        "periodic git-remote fetch sweep (ADR 0098). Default every 30 minutes; "
        "the live ON/OFF lever is the `git_fetch_sweep_enabled` platform setting "
        "(default OFF). The beat process reads this at boot.",
    )
    ecb_fx_feed_url: str = Field(
        default="https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml",
        description="URL of the ECB daily reference-rates XML feed (the default "
        "FX source). ECB publishes rates vs EUR; the fetcher converts them to "
        "vs-USD via the USD rate. Point at an internal mirror to avoid egress.",
    )

    # ----- Backup engine (Plan 12 task_12_01) -----
    # The full-backup routine (pg_dump LOGICAL + tar of the data volumes +
    # a checksummed manifest) is driven by these operator-tunable knobs —
    # never hardcoded magic numbers. The live enable/disable + the cron
    # cadence are PLATFORM settings a System Admin owns (task_12_04); these
    # envs are the host-side wiring the backup process reads at runtime.
    backup_root: str = Field(
        default="/data/agent-platform/backups",
        description="Host filesystem root where backup bundles are written, "
        "one timestamped subdirectory per run. Defaults under data_root so a "
        "single bind-mount covers all platform-managed state.",
    )
    backup_database_url: str = Field(
        default="postgresql://migrations_user:changeme-migrations-dev-only"
        "@localhost:15432/agentic_platform",
        description="LIBPQ-style URL pg_dump connects with for the FULL logical "
        "dump. A BYPASSRLS / admin-grade role so the dump captures every "
        "tenant's rows. NOTE: a plain libpq URL (postgresql://), NOT the "
        "SQLAlchemy +asyncpg form — pg_dump speaks libpq.",
    )
    backup_retention_days: int = Field(
        default=7,
        description="Local retention window in days (Plan 12: 'Retención local "
        "7 días'). Bundles whose timestamp is older than now-this are pruned "
        "after a successful run. Operator-tunable.",
    )
    knowledge_gc_retention_days: int = Field(
        default=30,
        description="G-03: gracia antes de que el GC de conocimiento hard-borre "
        "un documento soft-borrado (chunks + blob + fila). Operator-tunable.",
    )
    backup_volumes: list[str] = Field(
        default_factory=lambda: ["minio_data", "redis_data", "vault_data"],
        description="Docker named volumes captured in the tar+gzip step: MinIO "
        "objects, the Redis RDB/AOF, and the Vault file backend (snapshots). "
        "Names match docker/docker-compose.yml.",
    )
    backup_volumes_mount_root: str = Field(
        default="/var/lib/docker/volumes",
        description="Host directory under which the named docker volumes are "
        "materialised (`<root>/<volume>/_data`). The backup tars each volume's "
        "_data tree from here. Override when volumes live elsewhere (e.g. a "
        "bind-mounted /data root).",
    )
    backup_bind_paths: list[str] = Field(
        default_factory=lambda: ["/data/agent-platform"],
        description="Bind mounts (rutas absolutas, NO named volumes) que también "
        "entran en el bundle de backup. Por defecto los bare repos + worktrees "
        "de los agentes (/data/agent-platform), que quedaban fuera de todo "
        "backup y se perdieron en el wipe del bind del 2026-07-02 (auditoría "
        "F0.4). Vacíala para excluirlos.",
    )
    # prod-04 task_prod_04_05 — los bare repos son EL PRODUCTO de la plataforma
    # (principios rectores 4 y 5): cada proyecto tiene su repo en
    # `{data_root}/projects/{tenant}/{project}/repos/{repo}.git` y cada plan una
    # rama `plan/{id}-{slug}` dentro. Hasta prod-04 solo entraban de rebote en el
    # tar del bind `/data/agent-platform` — con los worktrees dentro (transitorios,
    # enormes, y en escritura activa) y, peor, sin que el RESTORE los extrajese
    # nunca (`_restore_volumes` filtraba `kind == "volume_tar"`). Ahora son un
    # artefacto propio, verificado y restaurado.
    backup_projects_root: str = Field(
        default="/data/agent-platform/projects",
        description="Raíz de los bare repos por tenant/proyecto que entra en el "
        "bundle como artefacto `projects_tar` (verificado y restaurado). Debe "
        "coincidir con `{data_root}/projects` (workers.git_repos.RepoLayout). "
        "Vacía = no capturar los repos (no recomendado: se pierde el código).",
    )
    backup_transient_excludes: list[str] = Field(
        default_factory=lambda: ["worktrees", "dep-cache"],
        description="Nombres de directorio EXCLUIDOS de los tar de `projects_tar` "
        "y de los bind paths porque son regenerables: los worktrees por tarea "
        "(`git worktree add` los recrea desde el bare) y la cache de dependencias. "
        "Además evitan el rc≠0 de tar por «file changed as we read it» sobre un "
        "worktree que un agente está escribiendo. Vacía = capturarlo todo.",
    )
    # ----- Coherencia de la captura (prod-04 task_prod_04_06) -----
    # Redis alojaba su estado en el bundle DE REBOTE, dentro del tar del bind del
    # data-root: un `appendonlydir` en escritura activa, acumulado durante días,
    # copiado mientras el servidor le escribía. Ahora es un artefacto propio
    # precedido de un `BGREWRITEAOF` completado.
    # OJO con la trampa que esto esquiva, medida contra redis:7-alpine el
    # 2026-07-31: capturar SOLO el `dump.rdb` (que era la letra del plan) restaura
    # una base VACÍA, porque con `--appendonly yes` un Redis que encuentra un RDB y
    # ningún `appendonlydir` no lee el RDB — crea un AOF nuevo vacío y sirve
    # DBSIZE 0, sin un solo error. Se captura el directorio (AOF + RDB).
    backup_redis_dir: str = Field(
        default="",
        description="Ruta del host con el directorio de datos de Redis "
        "(`appendonlydir/` + `dump.rdb`), capturada como artefacto `redis_tar` tras "
        "un BGREWRITEAOF completado. Vacía = no respaldar Redis, que es una opción "
        "legítima si se declara recreable (sesiones caídas, colas re-encoladas) "
        "pero es una DECISIÓN, no un descuido: el ADR de consistencia del bundle la "
        "plantea explícitamente.",
    )
    backup_redis_url: str = Field(
        default="",
        description="URL con la que el backup le pide a Redis el BGREWRITEAOF. "
        "Vacía = usar `broker_url`, que el worker ya tiene y apunta al MISMO "
        "servidor (el rewrite es global, no por-db).",
    )
    backup_stable_snapshot_paths: list[str] = Field(
        default_factory=list,
        description="Bind paths cuya captura se VERIFICA estable: huella del árbol "
        "(ruta, tamaño, mtime) antes y después del tar; si cambió, se reintenta "
        "`backup_snapshot_retries` veces y, si no converge, el backup falla. Para el "
        "file backend de Vault, cuya copia rota no da ninguna señal hasta que "
        "alguien intenta desellar el Vault restaurado en pleno DR. "
        "Deliberadamente NO para MinIO: se escribe todo el rato por diseño y "
        "exigirle estabilidad convertiría el backup nocturno en un fallo nocturno.",
    )
    backup_snapshot_retries: int = Field(
        default=2,
        description="Reintentos de una captura verificada antes de fallar el run. "
        "Una escritura suelta no debe tirar el backup; un árbol que no se queda "
        "quieto sí, porque la copia no sería coherente.",
    )
    # ----- Salvaguarda de secretos de columna (ADR 0146) -----
    # El ADR 0146 bendice que tres familias de secretos vivan cifradas con Fernet
    # en columnas de Postgres en vez de en Vault, PERO con una condición que
    # llama no opcional: un dump robado no puede bastar. Hoy quien tiene el
    # bundle y la variable `API_SERVER_*_ENCRYPTION_KEY` tiene los secretos, y el
    # bundle viaja a MinIO y a destinos externos. Se excluyen los DATOS de esas
    # tablas del dump (su DEFINICIÓN sí viaja: el restore las recrea vacías).
    # El precio, documentado en 06-runbooks/04-disaster-recovery.md: tras un DR
    # hay que reconfigurar SSO, canales de notificación y webhooks entrantes.
    backup_column_secret_tables: list[str] = Field(
        default_factory=lambda: [
            "sso_configurations",
            "notification_channels",
            "incoming_webhook_configs",
        ],
        description="Tablas cuyos DATOS quedan fuera del `pg_dump` porque llevan "
        "secretos que un TENANT configura para terceros, cifrados con Fernet y una "
        "clave que vive en una variable de entorno (ADR 0146). Vacía = viajan, o "
        "sea el comportamiento anterior al ADR: sólo tiene sentido si el bundle se "
        "cifra con una clave que NO es la de esas columnas y está en custodia. La "
        "frontera del ADR es estricta: aquí sólo entra el secreto tenant→tercero; "
        "las credenciales de PLATAFORMA siguen en Vault sin excepción.",
    )
    # ----- Quiesce de escritores durante la captura (ADR 0149, opción A) -----
    # El ADR se firmó el 2026-08-01: se paran los escritores mientras dura la
    # captura, PERO con un plazo que degrada. Si no paran a tiempo el backup
    # sigue adelante con los que queden en pie y el acta lo registra
    # (`quiesce: partial`), porque un quiesce que se cuelga convierte el backup
    # nocturno en una caída y a las 03:00 no hay nadie mirando.
    backup_quiesce_services: list[str] = Field(
        # Los mismos escritores de PostgreSQL que para el restore
        # (`restore_app_services`, vetada en task_prod_04_03), MENOS la lane que
        # corre el propio backup — que además queda bloqueada por
        # `backup_quiesce_never_stop`, porque una lista de servicios la escribe
        # un operador y la guarda no puede ser «no lo pongas».
        # `workers-marketplace` entra por la misma regla que en
        # `restore_app_services`: escribe en PostgreSQL (estado de instalación,
        # auditoría, materialización), así que dejarla en pie durante la captura
        # es justo el skew que el ADR 0149 vino a cerrar. Su task es idempotente
        # (`task_acks_late` + guarda de `status != ANALYZING`), así que la pausa
        # no pierde la instalación: el mensaje se re-entrega al volver.
        default_factory=lambda: [
            "api-server",
            "orchestrator",
            "workers",
            "workers-marketplace",
            "cortex-beat",
            "notification-dispatcher",
            "admin-panel",
        ],
        description="Servicios de aplicación que se PARAN mientras dura la captura "
        "del bundle (ADR 0149, opción A), para que ningún artefacto retrate un "
        "fichero a medio escribir. PostgreSQL, MinIO, Redis y Vault NO se paran: "
        "son los que se leen. Vacía = no parar nada (el comportamiento anterior al "
        "ADR, o sea aceptar el skew de la opción C). Cada nombre tiene que estar "
        "declarado en `restore_compose_file`.",
    )
    backup_quiesce_never_stop: list[str] = Field(
        default_factory=lambda: ["workers-privileged"],
        description="Servicios que NUNCA se paran aunque estén en "
        "`backup_quiesce_services`: la lane `workers-privileged` drena la cola "
        "`privileged`, o sea que ahí corre ESTE backup. Pararla lo mata a mitad de "
        "la captura y deja el resto del stack parado hasta que alguien lo note.",
    )
    backup_quiesce_timeout_seconds: int = Field(
        default=180,
        description="Plazo máximo (segundos) que el backup espera a que los "
        "escritores paren (ADR 0149, punto 1). Vencido, el backup SIGUE ADELANTE "
        "con los que queden en pie y el manifest registra `quiesce: partial` con "
        "quién no paró. Un backup con skew registrado es mucho mejor que un backup "
        "que no existe.",
    )
    backup_cron: str = Field(
        default="0 3 * * *",
        description="Cron (minute hour day-of-month month day-of-week) for the "
        "scheduled daily backup. Default 03:00 (Plan 12). Operator-tunable; the "
        "beat process reads it at boot. The live enable/disable lever is the "
        "`backup_enabled` PLATFORM setting (a System Admin flips it from the "
        "admin panel and it takes effect on the next fire without a restart).",
    )
    backup_metrics_textfile_path: str = Field(
        default="/host/textfile/agentic_backup.prom",
        description="Path to the node-exporter TEXTFILE-COLLECTOR file the daily "
        "backup task writes after every run (task_12_14). node-exporter "
        "(docker-compose.monitoring.yml, --collector.textfile.directory) "
        "re-exports the `agentic_backup_last_success` + "
        "`agentic_backup_last_success_timestamp_seconds` samples written here, "
        "which feed the BackupLastRunFailed / BackupTooOld alert rules. Written "
        "atomically (temp + rename) so node-exporter never reads a half-written "
        "file.",
    )
    # prod-06 task_prod06_dag_03 (parte B): the node-exporter TEXTFILE-COLLECTOR
    # file the `workers.sample_queue_metrics` beat writes — `agentic_celery_queue_depth`
    # (Redis LLEN per Celery queue) + `agentic_tasks_by_status` (task count per
    # lifecycle status). Same atomic-write + node-exporter re-export pattern as the
    # backup metric above; prod-08 adds the scrape job + CeleryQueueGrowing alert.
    queue_metrics_textfile_path: str = Field(
        default="/host/textfile/agentic_queue_depth.prom",
        description="Path to the node-exporter textfile the queue-depth + "
        "task-state metrics sampler writes (task_prod06_dag_03). Written atomically "
        "(temp + rename) so node-exporter never reads a half-written file.",
    )

    # ----- Optional at-rest encryption (Plan 12 task_12_02) -----
    # AES-256 (Decisiones Clave). OFF by default: encryption is OPTIONAL and
    # adds a Vault dependency, so an operator opts in explicitly. When ON, the
    # assembled bundle is wrapped into a single AES-256-GCM blob keyed by a
    # Vault-resolved secret (`backup_encryption_vault_key`); when OFF the
    # plaintext bundle is left unchanged. Never a magic number — both knobs are
    # operator-tunable env.
    backup_encryption_enabled: bool = Field(
        default=False,
        description="Whether to AES-256 encrypt the backup bundle at rest "
        "(Plan 12 Decisiones Clave). OFF by default — encryption is optional and "
        "requires a Vault key. When ON the bundle is wrapped into a single "
        "encrypted blob and the manifest records `encrypted: true`.",
    )
    backup_encryption_vault_key: str = Field(
        default="backup_encryption_key",
        description="Name of the secret the workers' Vault/secret provider "
        "resolves for the AES-256 backup key (never plaintext, never logged). "
        "Only consulted when `backup_encryption_enabled` is true.",
    )
    # ----- Custodia offsite de la clave (prod-04 task_prod_04_07) -----
    # LA CIRCULARIDAD (hallazgo gap1-1). La clave que descifra el bundle vive en
    # `WORKERS_BACKUP_ENCRYPTION_KEY`, o sea en el entorno de LA MISMA MÁQUINA que
    # se está respaldando — y el Vault viaja DENTRO del blob cifrado. Ante pérdida
    # total del host, el backup es matemáticamente irrecuperable: las unseal keys
    # custodiadas NO descifran AES-GCM, solo abren un Vault que está dentro del
    # blob que no se puede abrir.
    # El control técnico no puede garantizar la custodia humana; solo puede
    # verificar que la clave ACTIVA es la que alguien declaró haber depositado.
    # Eso es lo que hace este fingerprint, y por eso el backup falla en cuanto
    # deja de coincidir: una rotación de clave sin actualizar la custodia deja
    # bundles nuevos que nadie podrá abrir, y es preferible enterarse esa noche.
    backup_key_custody_fingerprint: str = Field(
        default="",
        description="Huella SHA-256 (hex) de la clave de cifrado del backup que "
        "está DEPOSITADA EN CUSTODIA OFFSITE (gestor corporativo / sobre sellado, "
        "junto a las unseal keys pero diferenciada). Si "
        "`backup_encryption_enabled` es true y esta huella no coincide con la de "
        "la clave activa, el backup FALLA. Vacía: fuera de dev también falla — un "
        "bundle cifrado cuya clave no está custodiada es un bundle irrecuperable. "
        "Obtén la huella del log del primer backup o del manifest "
        "(`key_fingerprint`). NUNCA pongas aquí la clave.",
    )

    # ----- Remote backup destinations — S3 (Plan 12 task_12_05) -----
    # After a successful, verified backup the bundle is uploaded to every
    # configured + enabled remote destination (Plan 12: "destinos remotos
    # opcionales (S3, B2, SFTP/NAS, rclone)"). These are the NON-secret S3
    # tunables (bucket, prefix, endpoint, region) — the access key + secret are
    # SECRETS resolved through the workers' secret seam (Vault/env), NEVER here.
    # OFF by default: a destination is opt-in. `endpoint_url` is the lever that
    # makes ANY S3-compatible provider work (MinIO, Backblaze B2, Wasabi, R2);
    # leave it empty for AWS.
    backup_s3_enabled: bool = Field(
        default=False,
        description="Whether to upload each successful backup bundle to the S3 "
        "destination. OFF by default — remote destinations are opt-in.",
    )
    backup_s3_bucket: str = Field(
        default="",
        description="S3 bucket the backup bundle is uploaded to. Required when "
        "`backup_s3_enabled` is true.",
    )
    backup_s3_prefix: str = Field(
        default="",
        description="Key prefix ('folder') under which bundles are stored in the "
        "bucket. Empty = bucket root.",
    )
    backup_s3_endpoint_url: str = Field(
        default="",
        description="S3 endpoint URL for a NON-AWS S3-compatible provider (MinIO, "
        "Backblaze B2, Wasabi, Cloudflare R2). Empty = real AWS S3.",
    )
    backup_s3_region: str = Field(
        default="",
        description="S3 region name. Empty = let the SDK/endpoint decide.",
    )

    # ----- Remote backup destinations — Backblaze B2 (Plan 12 task_12_06) -----
    # B2 is S3-COMPATIBLE but with quirks (Plan 12): the endpoint is derived from
    # the region as `s3.<region>.backblazeb2.com`, multipart wants a larger part
    # size than AWS's 5 MiB default, and auth is an application keyId + key. These
    # are the NON-secret B2 tunables; the application key id + key are SECRETS
    # resolved through the workers' secret seam (Vault/env), NEVER here. OFF by
    # default — a destination is opt-in. The B2 adapter reuses the S3 adapter via
    # the S3-compatible endpoint, so no endpoint_url knob is needed: it is built
    # from the region.
    backup_b2_enabled: bool = Field(
        default=False,
        description="Whether to upload each successful backup bundle to the "
        "Backblaze B2 destination. OFF by default — remote destinations are opt-in.",
    )
    backup_b2_bucket: str = Field(
        default="",
        description="B2 bucket the backup bundle is uploaded to. Required when "
        "`backup_b2_enabled` is true.",
    )
    backup_b2_prefix: str = Field(
        default="",
        description="Key prefix ('folder') under which bundles are stored in the "
        "B2 bucket. Empty = bucket root.",
    )
    backup_b2_region: str = Field(
        default="",
        description="B2 region (e.g. `us-west-002`, `eu-central-003`). The "
        "S3-compatible endpoint is derived from it as "
        "`https://s3.<region>.backblazeb2.com`. Required when "
        "`backup_b2_enabled` is true.",
    )

    # ----- Remote backup destinations — SFTP / NAS (Plan 12 task_12_07) -----
    # Any SSH-reachable host (a NAS, an offsite box) is a remote destination.
    # These are the NON-secret SFTP tunables (host, port, remote path, username,
    # host-key policy); the password / private key are SECRETS resolved through
    # the workers' secret seam (Vault/env), NEVER here. OFF by default — a
    # destination is opt-in. `host_key_policy` defaults to "reject" (the host
    # must be in a known_hosts file) — never silently disable host-key checking.
    backup_sftp_enabled: bool = Field(
        default=False,
        description="Whether to upload each successful backup bundle to the SFTP/"
        "NAS destination. OFF by default — remote destinations are opt-in.",
    )
    backup_sftp_host: str = Field(
        default="",
        description="SFTP/NAS hostname or IP the backup bundle is uploaded to. "
        "Required when `backup_sftp_enabled` is true.",
    )
    backup_sftp_port: int = Field(
        default=22,
        description="SFTP (SSH) port. Default 22.",
    )
    backup_sftp_username: str = Field(
        default="",
        description="SFTP username. Required when `backup_sftp_enabled` is true. "
        "The password / private key are SECRETS (secret seam), never here.",
    )
    backup_sftp_path: str = Field(
        default="",
        description="Remote directory under which bundles are stored. Empty = the "
        "session's default directory (the user's home).",
    )
    backup_sftp_host_key_policy: str = Field(
        default="reject",
        description="How an unknown server host key is handled: `reject` (default, "
        "safest — host must be in known_hosts), `auto_add` (trust-on-first-use), "
        "or `warn`. Never silently disable host-key checking.",
    )
    backup_sftp_known_hosts_path: str = Field(
        default="",
        description="Path to a known_hosts file loaded before connecting (for the "
        "`reject`/`warn` policies). Empty = paramiko's system host keys only.",
    )

    # ----- Remote backup destinations — generic rclone (Plan 12 task_12_08) -----
    # rclone speaks ~70 storage backends (Google Drive, Dropbox, OneDrive, Azure
    # Blob, WebDAV, …) through one CLI, so this destination makes the catalogue
    # open-ended without a bespoke adapter per provider. These are the NON-secret
    # rclone tunables (remote name, path); the rclone CONFIG BLOB (an `rclone.conf`
    # section body with OBSCURED creds) is a SECRET resolved through the workers'
    # secret seam (Vault/env), NEVER here — it is written to a temp `rclone.conf`
    # (0600) for the duration of each op and removed afterwards. OFF by default —
    # a destination is opt-in.
    backup_rclone_enabled: bool = Field(
        default=False,
        description="Whether to upload each successful backup bundle to the generic "
        "rclone destination. OFF by default — remote destinations are opt-in.",
    )
    backup_rclone_remote: str = Field(
        default="",
        description="The rclone remote name (the `[section]` header inside the config "
        "blob, e.g. `gdrive`, `b2-offsite`). Required when `backup_rclone_enabled` is "
        "true. The config blob (obscured creds) is a SECRET (secret seam), never here.",
    )
    backup_rclone_path: str = Field(
        default="",
        description="Path under the rclone remote where bundles are stored. Empty = "
        "the remote's root.",
    )

    # ----- Restore engine (Plan 12 task_12_10) -----
    # The full restore (decrypt + verify-before-restore, then stop app stack →
    # pg_restore the LOGICAL dump → restore volume tars → restart stack) drives
    # `docker compose` against THIS project + compose file, never an implicit one
    # (a host may run several compose stacks). The DB service is deliberately NOT
    # in `restore_app_services` — Postgres must stay reachable for pg_restore.
    # Operator-tunable; never hardcoded.
    restore_compose_project: str = Field(
        default="agentic-platform",
        description="docker compose project name the restore stack-control "
        "commands target (`docker compose --project-name <this>`). Must match the "
        "running stack so a restore never drives the wrong project.",
    )
    restore_compose_file: str = Field(
        # prod-04 task_prod_04_03: el default era `docker/docker-compose.yml`, el
        # compose VERSIONADO — que a propósito NO declara los servicios de
        # aplicación («The app services … are not yet declared in this compose
        # file», cabecera del fichero). Con ese default, `docker compose stop
        # api-server` devuelve != 0 y el restore aborta en el primer paso
        # destructivo. El compose que corre en producción lo escribe el
        # instalador en `{data_root}/docker-compose.yml` (compose_dir = data_root,
        # cli.py:793), y ese es el único que declara api-server/workers/…
        # Además era una ruta RELATIVA: dependía del cwd del proceso.
        default="/data/agent-platform/docker-compose.yml",
        description="Path to the compose file the restore stack-control commands "
        "use (`docker compose --file <this>`). Debe ser el compose que corre de "
        "verdad — el que genera el instalador en `{data_root}/docker-compose.yml`, "
        "no el `docker/docker-compose.yml` versionado (que no declara los "
        "servicios de aplicación). `scripts/restore.sh` lo pasa explícito. El "
        "motor comprueba en preflight que cada servicio a parar está declarado "
        "aquí y aborta ANTES de tocar nada si falta alguno.",
    )
    restore_app_services: list[str] = Field(
        # ADR 0117 (c): `web-app` estuvo aquí y **no existe en ningún compose**
        # — ni el versionado ni el que genera el instalador. `docker compose stop`
        # con un servicio inexistente devuelve != 0, y `_stop_app_stack` eleva en
        # ese caso: la restauración completa abortaba en el paso 3, ANTES de
        # restaurar nada. Un fantasma en esta lista no es cosmética, es el
        # simulacro de recuperación roto.
        # prod-04 task_prod_04_03: faltaban TRES escritores de la base de datos.
        # `workers-privileged` (la lane de backups/rotación), `cortex-beat` (el
        # beat del córtex) y `notification-dispatcher` siguen conectados a
        # PostgreSQL, que a propósito NO se para. Con ellos vivos, un
        # `pg_restore --clean` compite contra escrituras concurrentes: el DROP de
        # una tabla que otro proceso está usando falla o deja filas nuevas encima
        # del dump restaurado. Detener «los servicios de aplicación» tiene que
        # significar TODOS los que escriben, no los cuatro más visibles.
        # `caddy` queda fuera a propósito: es el proxy y no escribe; pararlo solo
        # añadiría un corte de red innecesario. `migrations` es one-shot.
        # prod-13 task_prod13_01: `workers-marketplace` es el CUARTO escritor que
        # se cuela sin entrar aquí. Drena la cola `marketplace`, y sus
        # puertas de seguridad escriben de verdad: mueven
        # `marketplace_installations.status` fuera de `analyzing`, insertan
        # entradas de auditoría (`_block`) y materializan agentes/equipos al
        # aprobar. Con la lane viva durante un `pg_restore --clean`, una puerta
        # en vuelo escribe encima del dump recién restaurado o hace fallar el
        # DROP. Pararla es seguro además de necesario: la task es idempotente por
        # diseño (`task_acks_late` + la guarda de `status != ANALYZING`), así que
        # el mensaje se re-entrega y la instalación se reanuda al levantar.
        default_factory=lambda: [
            "api-server",
            "orchestrator",
            "workers",
            "workers-privileged",
            "workers-marketplace",
            "cortex-beat",
            "notification-dispatcher",
            "admin-panel",
        ],
        description="The APP services stopped (and brought back up) around a full "
        "restore — every service that WRITES to PostgreSQL, since Postgres itself "
        "is deliberately ABSENT (it must stay reachable for pg_restore). The "
        "volume-backing services are stopped separately around the volume restore "
        "(`restore_volume_services`). Every name here MUST be declared in the "
        "compose file `restore_compose_file` points at: `docker compose stop` "
        "exits != 0 on an unknown service and the restore aborts before restoring "
        "anything (ADR 0117 c). El motor lo comprueba en preflight.",
    )
    restore_volume_services: list[str] = Field(
        default_factory=lambda: ["minio", "redis", "vault"],
        description="The services backing the data volumes restored from the tar "
        "archives. Stopped while each volume's _data tree is wiped + re-extracted, "
        "then started again with the rest of the stack.",
    )
    # prod-04 task_prod_04_04 — el motor tenía un `finally: docker compose up -d`
    # que arrancaba la aplicación incluso tras fallar a mitad, sobre datos
    # inconsistentes. Los dos runbooks de DR ordenan lo contrario. Ahora el
    # arranque tras un fallo es OPT-IN y por defecto el stack queda parado (solo
    # PostgreSQL sigue alcanzable, porque nunca se para).
    restore_autostart_on_failure: bool = Field(
        default=False,
        description="Si un paso de la fase destructiva del restore falla, ¿arrancar "
        "el stack igualmente? FALSE por defecto (fail-stopped): un stack sirviendo "
        "datos a medio restaurar es peor que un stack parado. Ponlo a true solo en "
        "un laboratorio donde la disponibilidad importe más que la corrección.",
    )
    # prod-04 task_prod_04_08 — `pg_dump`/`pg_restore` corren con
    # `--no-owner --no-privileges`, así que el restore recrea los objetos SIN
    # ownership ni ACLs: `app_user` (el rol NOBYPASSRLS del runtime) se queda sin
    # GRANTs y la aplicación arranca para fallar con «permission denied for table».
    restore_required_db_role: str = Field(
        default="migrations_user",
        description="Rol con el que el DSN de restore DEBE conectar (el dueño del "
        "DDL). `pg_restore --clean` recrea todos los objetos y el ownership queda "
        "en el rol que conecta: hacerlo como app_user deja el esquema inservible "
        "para las migraciones. Vacío = no comprobarlo (no recomendado).",
    )
    restore_grant_app_role: str = Field(
        default="app_user",
        description="Rol de runtime al que el restore re-concede permisos "
        "(idempotente) al terminar el pg_restore: USAGE en el esquema, "
        "SELECT/INSERT/UPDATE/DELETE en todas las tablas, USAGE/SELECT en las "
        "secuencias y los DEFAULT PRIVILEGES equivalentes. Vacío = no re-conceder "
        "(la aplicación no podrá leer sus propias tablas tras un DR).",
    )

    # ----- Selective per-tenant restore (Plan 12 task_12_11) -----
    # Restore ONE tenant's data from a full bundle without clobbering others. The
    # logical dump is pg_restore'd into a throwaway STAGING db, then ONLY the
    # target tenant's rows are copied into the live tables (filtered by tenant_id
    # on both sides, in FK order). These knobs are the tenant-scoped table set (in
    # FK parent→child order) + which captured volume holds the object store. The
    # admin DB URL the cross-tenant copy runs as is `backup_database_url` (a
    # BYPASSRLS role) — reused, never a second credential. Operator-tunable so a
    # schema change is config, not a worker code change. An empty list falls back
    # to the built-in DEFAULT_TENANT_SCOPED_TABLES.
    restore_tenant_scoped_tables: list[str] = Field(
        default_factory=list,
        description="The tenant-scoped tables a per-tenant restore copies, in FK "
        "(parent→child) order: inserts go in this order, deletes in reverse. Empty "
        "= the built-in default set (every tenant_id-bearing domain table). Each "
        "name must be a plain SQL identifier (validated before use).",
    )
    restore_object_store_volume: str = Field(
        default="minio_data",
        description="The captured docker volume that holds object storage (MinIO). "
        "A per-tenant restore re-extracts ONLY the tenant's `<tenant_id>/` key "
        "prefix from this volume's tar, never the whole volume.",
    )

    # ----- Vault dynamic-secret credential rotation (Plan 15 task_15_17) -----
    # Automatic credential rotation has two halves (Plan 15 Fase C):
    #   1. SHORT-TTL DYNAMIC DB CREDS — the Vault database secrets engine mints a
    #      throwaway Postgres role per lease; a service holds creds only for
    #      `cred_rotation_db_ttl_s`, after which the lease (and the role) expires.
    #   2. PERIODIC ROTATION JOB — a Celery beat task (CONFIGURABLE cadence) that
    #      rotates the STATIC secrets (MinIO/JWT/…) and renews/revokes leases.
    # Like price-sync / backup, the cron is read by the beat PROCESS at boot and
    # the live enable lever is a PLATFORM setting a System Admin owns — NOT this
    # env. The Vault client sits behind a seam (mocked in tests); nothing here is
    # a secret (the Vault token + minted creds never live in config).
    cred_rotation_cron: str = Field(
        default="0 2 * * 0",
        description="Cron (minute hour day-of-month month day-of-week) for the "
        "scheduled credential-rotation job. Default weekly Sunday 02:00 UTC — "
        "rotation is heavier than a price sync, so it runs less often. "
        "Operator-tunable; the beat process reads it at boot. The live "
        "enable/disable lever is the `cred_rotation_enabled` PLATFORM setting.",
    )
    cred_rotation_db_role: str = Field(
        default="platform-app",
        description="Name of the Vault database-secrets-engine ROLE that mints "
        "short-TTL dynamic Postgres credentials. The role's creation statements "
        "grant exactly the platform app privileges; each lease is a throwaway DB "
        "role Vault revokes on expiry. NOT a secret.",
    )
    cred_rotation_db_mount: str = Field(
        default="database",
        description="Mount point of the Vault database secrets engine (the "
        "`vault secrets enable database` path). NOT a secret.",
    )
    cred_rotation_db_connection: str = Field(
        default="platform-postgres",
        description="Name of the Vault database-engine CONNECTION the role is "
        "bound to (the configured Postgres connection Vault dials to create/drop "
        "the dynamic roles). NOT a secret — the connection's admin DSN is a Vault "
        "secret resolved server-side, never here.",
    )
    cred_rotation_db_ttl_s: int = Field(
        default=3600,
        description="Default TTL (seconds) of a minted dynamic DB credential "
        "lease. SHORT by design (default 1h) so a leaked credential self-expires. "
        "Operator-tunable.",
    )
    cred_rotation_db_max_ttl_s: int = Field(
        default=86400,
        description="Maximum TTL (seconds) a dynamic DB credential lease can be "
        "renewed up to before Vault forces a fresh issue. Default 24h.",
    )
    cred_rotation_static_secrets: list[str] = Field(
        default_factory=lambda: ["minio", "jwt"],
        description="Logical names of the STATIC secrets the rotation job rotates "
        "in place each cycle (the MinIO access/secret key + the JWT signing key, "
        "per Plan 15). Each maps to a KV v2 path under the platform mount; the "
        "rotated VALUES are high-entropy material generated + written by Vault, "
        "NEVER logged and NEVER in this config.",
    )
    # ----- MinIO admin credential for the rotation (prod-05 task_prod05_07) -----
    # Rotating `minio` used to mean "write a new value into KV v2", which rotates
    # NOTHING: MinIO keeps accepting the old credential and the services keep
    # using theirs (gap2-2). The cycle now mints a MinIO SERVICE ACCOUNT through
    # the admin API before it writes KV, and that needs an admin credential.
    #
    # Service accounts (not a new root user) so a botched rotation can never lock
    # the platform out of its own object storage: the root credential below is
    # never changed by the rotation, only used to mint and revoke children.
    #
    # Unset (the default) = the MinIO step FAILS LOUDLY rather than writing a KV
    # entry naming a credential MinIO never issued.
    cred_rotation_minio_url: str = Field(
        default="http://minio:9000",
        description="MinIO endpoint the rotation's admin client dials. A scheme is "
        "accepted and stripped (MinioAdmin takes host:port). NOT a secret.",
    )
    cred_rotation_minio_root_user: str = Field(
        default="",
        description="MinIO admin user used ONLY to create/delete rotation service "
        "accounts. Empty = the MinIO rotation step is not wired and fails loudly.",
    )
    cred_rotation_minio_root_password: SecretStr = Field(
        default=SecretStr(""),
        description="Password of `cred_rotation_minio_root_user`. Secreto — nunca "
        "se loguea ni aparece en una auditoría de rotación.",
    )

    # ----- Acceptance-timeout escalation sweep (Plan 16 task_16_06) -----
    # The escalation beat job (workers.escalate_human_assignments) sweeps the
    # pending_acceptance HumanTaskAssignment rows whose age exceeds their Human
    # Agent's acceptance_timeout_hours and reassigns/blocks them. Like the other
    # beat jobs the cron is read by the beat PROCESS at boot and the live
    # enable lever is a PLATFORM setting (`human_escalation_enabled`) a System
    # Admin owns — NOT this env. Default every 10 minutes (Plan 16 task_16_06):
    # frequent enough that a 24h acceptance window is enforced promptly, cheap
    # enough (a partial-index scan of the open pending_acceptance rows) to run
    # often. NOTE: a 5-field cron's finest granularity is per-minute; "*/10 * * *
    # *" fires at minute 0,10,20,…,50 of every hour — the 10-minute cadence.
    human_escalation_cron: str = Field(
        default="*/10 * * * *",
        description="Cron (minute hour day-of-month month day-of-week) for the "
        "scheduled acceptance-timeout escalation sweep. Default every 10 minutes "
        "(Plan 16 task_16_06). Operator-tunable; the beat process reads it at boot. "
        "The live enable/disable lever is the `human_escalation_enabled` PLATFORM "
        "setting (a System Admin flips it from the admin panel; it takes effect on "
        "the next fire without a restart).",
    )

    # ----- Misc -----
    environment: str = Field(
        default="dev",
        description=(
            "Deployment environment — a CLOSED set: dev | staging | prod. Any "
            "other value fails startup: an unrecognised tag used to be treated as "
            "`dev`, silently disabling the dev-credential guard below."
        ),
    )

    @field_validator("environment")
    @classmethod
    def _validate_environment(cls, value: str) -> str:
        """Reject any environment tag outside ``{dev, staging, prod}``.

        A FIELD validator (not a model one) so it runs BEFORE
        :meth:`_forbid_dev_secrets_outside_dev`, which branches on this value: the
        credential guard must never decide anything from an unvalidated tag.

        Whitespace and case are normalised (``" PROD "`` -> ``"prod"``) because a
        trailing newline in a compose/`.env` file is a configuration accident, not
        an intent to run unguarded. Anything else is a hard failure naming the
        accepted values, so the operator fixes it in seconds instead of running
        publicly-known BYPASSRLS credentials in production for months.
        """
        normalised = value.strip().lower()
        if normalised not in _KNOWN_ENVIRONMENTS:
            raise ValueError(
                f"WORKERS_ENVIRONMENT={value!r} is not a known environment. "
                f"Accepted values: {', '.join(sorted(_KNOWN_ENVIRONMENTS))}. "
                "An unrecognised value used to be treated as 'dev', which "
                "disabled the dev-credential guard."
            )
        return normalised

    @model_validator(mode="after")
    def _forbid_dev_secrets_outside_dev(self) -> Settings:
        """Reject the dev-default `database_url` (BYPASSRLS credentials) in
        anything that is not dev (secrets-config-5).

        FAIL-CLOSED: the predicate is ``environment == "dev"`` (skip), never
        ``environment in {staging, prod}`` (enforce). The enum above already closes
        today's hole; writing the guard as "everything except dev" is what keeps a
        FUTURE fourth environment guarded by default instead of by remembering to
        update a set literal.
        """
        if self.environment == _DEV_ENVIRONMENT:
            return self
        if any(marker in self.database_url.lower() for marker in _DEV_SECRET_MARKERS):
            raise ValueError(
                f"environment={self.environment!r} but WORKERS_DATABASE_URL still uses "
                "dev-default credentials. Set it to a real secret (Vault-backed in production)."
            )
        # El DSN del backup (`backup_database_url`) es una SEGUNDA credencial con
        # su propio default de dev, y también hay que rechazarlo fuera de dev —
        # pero NO aquí: eso convertiría «el backup nocturno no correría» en «la
        # flota de workers entera no arranca», que es un radio de explosión mayor
        # que el problema. Se comprueba en `workers.backup.BackupConfig.from_settings`,
        # que falla el run del backup con un mensaje accionable antes de gastar una
        # hora en el pg_dump (prod-04 task_prod_04_09).
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="WORKERS_",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor — read once per process."""
    return Settings()


def reset_settings_cache() -> None:
    """Drop the cached Settings so the next get_settings() re-reads env."""
    get_settings.cache_clear()
