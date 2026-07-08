"""Runtime configuration for the Celery workers service.

Env-driven via pydantic-settings, prefix `WORKERS_`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Substrings flagging a known dev-only default — forbidden in staging/prod
# (Plan 06.14 task_06_14_03 / secrets-config-5).
_DEV_SECRET_MARKERS = ("changeme", "dev-only")


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
        default="postgresql+asyncpg://migrations_user:changeme-migrations-dev-only"
        "@localhost:5432/agentic_platform",
        description="PostgreSQL URL the worker persists `executions` rows to. "
        "A BYPASSRLS role — the worker writes execution records across tenants.",
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
        description="Max process count inside an agent container — caps " "fork bombs.",
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
    dind_proxy_mem_limit: str = Field(
        default="128m",
        description="Hard memory cap for the testcontainers DinD socket-proxy sidecar.",
    )
    dind_proxy_pids_limit: int = Field(
        default=64,
        description="Max process count inside the DinD socket-proxy sidecar.",
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
        default="docker/docker-compose.yml",
        description="Path to the compose file the restore stack-control commands "
        "use (`docker compose --file <this>`).",
    )
    restore_app_services: list[str] = Field(
        default_factory=lambda: [
            "api-server",
            "orchestrator",
            "workers",
            "web-app",
            "admin-panel",
        ],
        description="The APP services stopped (and brought back up) around a full "
        "restore. PostgreSQL is deliberately ABSENT — it must stay reachable for "
        "pg_restore. The volume-backing services are stopped separately around the "
        "volume restore (`restore_volume_services`).",
    )
    restore_volume_services: list[str] = Field(
        default_factory=lambda: ["minio", "redis", "vault"],
        description="The services backing the data volumes restored from the tar "
        "archives. Stopped while each volume's _data tree is wiped + re-extracted, "
        "then started again with the rest of the stack.",
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
        default="dev", description="Tag emitted in logs: dev | staging | prod."
    )

    @model_validator(mode="after")
    def _forbid_dev_secrets_outside_dev(self) -> Settings:
        """Reject the dev-default `database_url` (BYPASSRLS credentials) in
        staging/prod (secrets-config-5)."""
        if self.environment not in {"staging", "prod"}:
            return self
        if any(marker in self.database_url.lower() for marker in _DEV_SECRET_MARKERS):
            raise ValueError(
                f"environment={self.environment!r} but WORKERS_DATABASE_URL still uses "
                "dev-default credentials. Set it to a real secret (Vault-backed in production)."
            )
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
