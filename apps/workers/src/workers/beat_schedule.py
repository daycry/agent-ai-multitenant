"""Celery beat schedule (Plan 06.5 Fase D — task_06_5_13).

Wires the four maintenance tasks of `workers.maintenance` to Celery
beat with the cadences mandated by Plan 06.5:

    idle_sweep_pools         every 30 seconds
    expire_review_runtimes   every 5 minutes
    purge_dep_cache          daily at 03:00 UTC
    prune_worktrees          daily at 03:30 UTC

Plan 11 task_11_18 adds a CONFIGURABLE scheduled price-catalog sync
(`workers.sync_model_prices`); its cadence comes from
`Settings.price_sync_cron` (default daily 04:00 UTC) rather than a
hardcoded magic schedule, and its live enable/disable is the
`price_sync_enabled` platform setting a System Admin owns.

Activated by `apps/workers/__main__.py` (or `celery -A workers.celery_app
beat`) — `build_celery_app` reads this schedule when running with the
``beat`` role. The constants are exported so tests can introspect them.
"""

from __future__ import annotations

import logging

from celery.schedules import crontab, schedule

from workers.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Environments where a malformed operator cron must REJECT beat boot rather than
# silently fall back — a typo that turns a 10-minute sweep into a daily one is a
# production incident, not a warning.
_STRICT_ENVIRONMENTS = frozenset({"staging", "prod"})

# Each schedule entry is the standard Celery shape:
# `{task: <name>, schedule: <celery.schedules.*>, options: {queue: <name>}}`.
#
# We pin queues explicitly:
#   - idle_sweep_pools     → default  (no infra side-effects beyond logging)
#   - expire_review_runtimes → review  (touches review_sessions rows)
#   - purge_dep_cache      → test    (touches the dep-cache directory the
#                                      test queue manages)
#   - prune_worktrees      → default (cross-cutting filesystem walk)
BEAT_SCHEDULE: dict[str, dict[str, object]] = {
    "idle-sweep-pools-every-30s": {
        "task": "workers.idle_sweep_pools",
        "schedule": schedule(run_every=30.0),
        "options": {"queue": "default"},
    },
    # prod-12 task_prod12_reaper_01 (sandbox-5): reap de contenedores managed
    # sin asociacion VIVA (execution running / review activa) + redes bridge
    # per-task de test-runtime vacias. Criterio de vida compartido con el
    # sweeper de zombis de prod-06 (nunca doble-kill). Cada 10 min.
    "reap-orphans-every-10m": {
        "task": "workers.reap_orphans",
        "schedule": schedule(run_every=600.0),
        "options": {"queue": "default"},
    },
    # prod-06 task_prod06_dag_02 — safety-net DAG promotion: across in_progress
    # plans, promote eligible backlog tasks to ready and re-announce undispatched
    # ready tasks (the DB trigger flips status without publishing an event). Cheap
    # query; every 30s. Roots get the instant path in start-execution.
    "promote-ready-plans-every-30s": {
        "task": "workers.promote_ready_plans",
        "schedule": schedule(run_every=30.0),
        "options": {"queue": "default"},
    },
    # prod-06 task_prod06_zombi_01 — close zombie executions (running rows whose
    # Celery child was SIGKILLed by OOM/hard-limit) and reap their orphan
    # containers. Every 5 min; only touches rows older than the stale threshold.
    "sweep-stale-executions-every-5m": {
        "task": "workers.sweep_stale_executions",
        "schedule": schedule(run_every=300.0),
        "options": {"queue": "default"},
    },
    "expire-review-runtimes-every-5m": {
        "task": "workers.expire_review_runtimes",
        "schedule": schedule(run_every=300.0),
        "options": {"queue": "review"},
    },
    # prod-06 task_prod06_budget_01 — per-tenant budget sweep: re-derive the
    # auto-pause flags + fire threshold alerts. The post-execution hook keeps a
    # single run immediate; this is the safety net (period rollover auto-clear,
    # missed hook, manual spend correction). Cheap per tenant; every 5 min.
    "refresh-budgets-every-5m": {
        "task": "workers.refresh_budgets",
        "schedule": schedule(run_every=300.0),
        "options": {"queue": "default"},
    },
    # prod-06 task_prod06_dag_03 (parte B) — sample Celery queue depth (Redis LLEN)
    # + task counts per status into the node-exporter textfile. prod-08 scrapes it
    # (CeleryQueueGrowing alert + dashboard). Cheap; every 30s so a piling-up queue
    # or a stuck state (e.g. growing in_review) shows up promptly.
    "sample-queue-metrics-every-30s": {
        "task": "workers.sample_queue_metrics",
        "schedule": schedule(run_every=30.0),
        "options": {"queue": "default"},
    },
    # Audit C3 / P0.6 — convergence safety net: reconcile DERIVED pipeline state the
    # live event path can miss (a lost task/plan event, a worker SIGKILLed between the
    # finalize txn and the publish). Three idempotent best-effort passes: (a) a task
    # stuck `in_progress` whose last execution is terminal -> transition it off
    # (reusing the dag_01 policy + re-emit the event); (b) an `in_review` task with an
    # AI reviewer but no live/recent review run -> re-announce `in_review` so the
    # orchestrator re-dispatches; (c) an `in_progress` plan whose tasks are all
    # terminal -> `pending_human_validation`. Every 90s; only touches rows settled
    # past the age thresholds, so it never races a worker mid post-processing.
    "reconcile-pipeline-state-every-90s": {
        "task": "workers.reconcile_pipeline_state",
        "schedule": schedule(run_every=90.0),
        "options": {"queue": "default"},
    },
    "purge-dep-cache-daily": {
        "task": "workers.purge_dep_cache",
        "schedule": crontab(hour="3", minute="0"),
        "options": {"queue": "test"},
    },
    # ADR 0122: vigía de credenciales LLM — sondea los proveedores activos
    # y avisa ANTES de que un run muera por credencial caducada.
    "provider-watchdog-every-30m": {
        "task": "workers.provider_watchdog",
        "schedule": schedule(run_every=1800.0),
        "options": {"queue": "default"},
    },
    # ADR 0120: el standup del PM corre CADA HORA a los :05 — la task decide
    # qué tenants reciben el parte (los que tienen `standup.hour` == hora
    # actual UTC); la cadencia horaria hace de gate de idempotencia diaria.
    "daily-standup-hourly-gate": {
        "task": "workers.daily_standup",
        "schedule": crontab(minute="5"),
        "options": {"queue": "default"},
    },
    # ADR 0124: retro de planes cerrados → memoria project_shared (cada 15
    # min; idempotente por marker en Redis, ventana 48h).
    # ADR 0125: asesor de configuración — lunes 07:00 UTC, solo PROPONE.
    "config-advisor-weekly": {
        "task": "workers.config_advisor",
        "schedule": crontab(day_of_week="1", hour="7", minute="0"),
        "options": {"queue": "default"},
    },
    "plan-retro-every-15m": {
        "task": "workers.plan_retro",
        "schedule": schedule(run_every=900.0),
        "options": {"queue": "default"},
    },
    "prune-worktrees-daily": {
        "task": "workers.prune_worktrees",
        "schedule": crontab(hour="3", minute="30"),
        "options": {"queue": "default"},
    },
    # G-08 (auditoría proyecto 2026-07-17) — higiene mensual de los bare repos:
    # worktree prune + gc ligero + locks huérfanos + poda de ramas plan/* de
    # planes cerrados con PR abierto (con refs/rescue como red).
    "git-housekeeping-monthly": {
        "task": "workers.git_housekeeping",
        "schedule": crontab(day_of_month="1", hour="3", minute="50"),
        "options": {"queue": "default"},
    },
    # Plan 06.11 — safety net: re-enqueue documents stuck in `pending`
    # (a missed enqueue, a worker crash mid-flight, or an upload while
    # the broker was down). Cheap query; runs every 2 minutes.
    "sweep-pending-documents-every-2m": {
        "task": "workers.sweep_pending_documents",
        "schedule": schedule(run_every=120.0),
        "options": {"queue": "ingestion"},
    },
    # Plan 06.17 task_06_17_03 — back-fill IDEMPOTENTE de embeddings de memoria:
    # rellena los memory_entries.embedding NULL por lotes/throttled. Worker
    # DEDICADO (nunca parte del flujo de un run, sin auto-retry). Cada 5 min;
    # una pasada solo toca filas NULL, así que con todo rellenado es un no-op
    # barato. Su enable/batch/throttle son platform settings (memory.backfill_*)
    # que un System Admin posee. Pinned a `ingestion` (donde vive el embedder).
    # P1-11b (investigación 2026-07-11): el espejo para chunks de KB — la
    # ingesta deja embedding=NULL si Ollama falla y el re-embed nunca existió.
    "backfill-chunk-embeddings-every-5m": {
        "task": "workers.backfill_chunk_embeddings",
        "schedule": schedule(run_every=300.0),
        "options": {"queue": "default"},
    },
    "backfill-memory-embeddings-every-5m": {
        "task": "workers.backfill_memory_embeddings",
        "schedule": schedule(run_every=300.0),
        "options": {"queue": "ingestion"},
    },
    # G-03 (auditoría proyecto 2026-07-17): GC físico del conocimiento —
    # hard-purga documentos soft-borrados vencidos (chunks+blob+fila) y barre
    # blobs kb/** sin fila documents. Diario 04:00. Pinned a `ingestion` (donde
    # vive el cliente MinIO). Idempotente; una pasada sin basura es no-op barato.
    "collect-knowledge-garbage-daily": {
        "task": "workers.collect_knowledge_garbage",
        "schedule": crontab(hour="4", minute="0"),
        "options": {"queue": "ingestion"},
    },
}

# Plan 11 task_11_18: the scheduled price-catalog sync entry name. Kept as a
# constant so tests + ops can reference it without hardcoding the string.
PRICE_SYNC_BEAT_ENTRY = "sync-model-prices"

# Plan 12 task_12_01/12_04: the scheduled daily-backup entry name. Same
# constant-not-hardcoded-string discipline as the price-sync entry.
BACKUP_BEAT_ENTRY = "run-daily-backup"

# Plan 15 task_15_17: the scheduled credential-rotation entry name. Same
# constant-not-hardcoded-string discipline as the price-sync / backup entries.
CRED_ROTATION_BEAT_ENTRY = "rotate-credentials"

# Plan 11.1 task_11_1_02: the scheduled exchange-rates-fetcher entry name. Same
# constant-not-hardcoded-string discipline as the price-sync / backup entries.
FX_FETCH_BEAT_ENTRY = "fetch-exchange-rates"
GIT_FETCH_BEAT_ENTRY = "sweep-project-git-remotes"

# Plan 16 task_16_06: the scheduled acceptance-timeout escalation sweep entry
# name. Same constant-not-hardcoded-string discipline as the entries above.
HUMAN_ESCALATION_BEAT_ENTRY = "escalate-human-assignments"


def _try_crontab(expr: str) -> crontab | None:
    """Return a crontab for a 5-field expr, or None if it is malformed.

    Malformed means either the wrong field count OR a field celery rejects
    (out-of-range value, bad weekday literal, …) — both surface as None so the
    caller decides whether to fall back or reject.
    """
    parts = expr.split()
    if len(parts) != 5:
        return None
    minute, hour, dom, month, dow = parts
    try:
        return crontab(
            minute=minute,
            hour=hour,
            day_of_month=dom,
            month_of_year=month,
            day_of_week=dow,
        )
    except (ValueError, KeyError) as exc:  # celery raises ValueError on bad fields
        logger.debug("crontab rejected %r: %s", expr, exc)
        return None


def _parse_cron(
    expr: str,
    *,
    env_var: str,
    default: str,
    environment: str = "dev",
) -> crontab:
    """Parse a 5-field cron string (minute hour dom month dow) to a crontab, LOUDLY.

    On a malformed expression we never silently degrade to a global daily 04:00
    (a typo in ``WORKERS_HUMAN_ESCALATION_CRON`` used to turn a 10-minute sweep
    into a daily one with no warning):

      - ``staging``/``prod``: RAISE to reject beat boot — a bad cadence in
        production is an incident, not a warning;
      - ``dev``/anything else: log an ERROR naming the offending env var and fall
        back to THIS entry's documented ``default`` (not a global 04:00).
    """
    parsed = _try_crontab(expr)
    if parsed is not None:
        return parsed
    if environment in _STRICT_ENVIRONMENTS:
        raise ValueError(
            f"Refusing to start beat: {env_var}={expr!r} is not a valid 5-field "
            "cron 'minute hour day-of-month month day-of-week'."
        )
    logger.error(
        "Malformed cron in %s=%r (expected 5 fields 'minute hour dom month dow'); "
        "falling back to this entry's documented default %r.",
        env_var,
        expr,
        default,
    )
    fallback = _try_crontab(default)
    if fallback is None:  # our own built-in default is broken — a programming error
        raise ValueError(f"Built-in default cron {default!r} for {env_var} is itself invalid")
    return fallback


def _cron_default(field: str) -> str:
    """The documented default of a ``Settings`` cron field (single source of truth).

    Reading it from the model keeps the per-entry fallback in lock-step with the
    configured default — no drifting literals duplicated at the call sites.
    """
    return str(Settings.model_fields[field].default)


def build_beat_schedule(settings: Settings | None = None) -> dict[str, dict[str, object]]:
    """The full beat schedule, including the CONFIGURABLE price-sync entry.

    `build_celery_app` calls this so the scheduled price-catalog sync
    (task_11_18) picks up its cadence from ``Settings.price_sync_cron`` rather
    than a hardcoded schedule. The static maintenance entries are unchanged.
    The price-sync run is pinned to the `default` queue (a cheap HTTP fetch +
    a handful of catalog writes — no infra side-effects).
    """
    cfg = settings or get_settings()
    sched: dict[str, dict[str, object]] = dict(BEAT_SCHEDULE)
    sched[PRICE_SYNC_BEAT_ENTRY] = {
        "task": "workers.sync_model_prices",
        "schedule": _parse_cron(
            cfg.price_sync_cron,
            env_var="WORKERS_PRICE_SYNC_CRON",
            default=_cron_default("price_sync_cron"),
            environment=cfg.environment,
        ),
        "options": {"queue": "default"},
    }
    # Plan 12 task_12_01/12_04: daily full backup on a CONFIGURABLE cadence
    # (WORKERS_BACKUP_CRON, default 03:00). Pinned to the `privileged` queue —
    # it touches infra (the DB dump + the data volumes), the lane drained by a
    # worker with host-level access. Its live enable/disable is the
    # `backup_enabled` platform setting a System Admin owns.
    sched[BACKUP_BEAT_ENTRY] = {
        "task": "workers.run_daily_backup",
        "schedule": _parse_cron(
            cfg.backup_cron,
            env_var="WORKERS_BACKUP_CRON",
            default=_cron_default("backup_cron"),
            environment=cfg.environment,
        ),
        "options": {"queue": "privileged"},
    }
    # Plan 15 task_15_17: Vault credential rotation on a CONFIGURABLE cadence
    # (WORKERS_CRED_ROTATION_CRON, default weekly Sunday 02:00). Pinned to the
    # `privileged` queue — it touches the platform's secrets/Vault, the lane
    # drained by a worker with the tighter security profile. Its live
    # enable/disable is the `cred_rotation_enabled` platform setting a System
    # Admin owns.
    sched[CRED_ROTATION_BEAT_ENTRY] = {
        "task": "workers.rotate_credentials",
        "schedule": _parse_cron(
            cfg.cred_rotation_cron,
            env_var="WORKERS_CRED_ROTATION_CRON",
            default=_cron_default("cred_rotation_cron"),
            environment=cfg.environment,
        ),
        "options": {"queue": "privileged"},
    }
    # Plan 11.1 task_11_1_02: daily exchange-rates fetch on a CONFIGURABLE
    # cadence (WORKERS_FX_FETCH_CRON, default 06:00 UTC). Pinned to the `default`
    # queue — a cheap HTTP fetch + a handful of global-catalog upserts, no infra
    # side-effects. Its live enable/disable + the SOURCE selection are the
    # `fx_fetch_enabled` / `fx_source` platform settings a System Admin owns.
    sched[FX_FETCH_BEAT_ENTRY] = {
        "task": "workers.fetch_exchange_rates",
        "schedule": _parse_cron(
            cfg.fx_fetch_cron,
            env_var="WORKERS_FX_FETCH_CRON",
            default=_cron_default("fx_fetch_cron"),
            environment=cfg.environment,
        ),
        "options": {"queue": "default"},
    }
    # ADR 0098 (eje 3): barrido periodico de fetch de remotos git en cadencia
    # CONFIGURABLE (WORKERS_GIT_FETCH_CRON, default cada 30 min). Cola `default`
    # (fetch autenticado best-effort por proyecto, sin side-effects de infra).
    # El interruptor vivo es el platform setting `git_fetch_sweep_enabled`
    # (default OFF) que el task consulta antes de tocar ningun remoto.
    sched[GIT_FETCH_BEAT_ENTRY] = {
        "task": "workers.sweep_project_git_remotes",
        "schedule": _parse_cron(
            cfg.git_fetch_cron,
            env_var="WORKERS_GIT_FETCH_CRON",
            default=_cron_default("git_fetch_cron"),
            environment=cfg.environment,
        ),
        "options": {"queue": "default"},
    }
    # Plan 16 task_16_06: acceptance-timeout escalation sweep on a CONFIGURABLE
    # cadence (WORKERS_HUMAN_ESCALATION_CRON, default every 10 minutes). Pinned
    # to the `default` queue — a cheap partial-index scan of the open
    # pending_acceptance rows + a handful of reassign/block writes, no infra
    # side-effects. Its live enable/disable is the `human_escalation_enabled`
    # platform setting a System Admin owns.
    sched[HUMAN_ESCALATION_BEAT_ENTRY] = {
        "task": "workers.escalate_human_assignments",
        "schedule": _parse_cron(
            cfg.human_escalation_cron,
            env_var="WORKERS_HUMAN_ESCALATION_CRON",
            default=_cron_default("human_escalation_cron"),
            environment=cfg.environment,
        ),
        "options": {"queue": "default"},
    }
    # Córtex F4 (ADR 0078) — bucles cognitivos de fondo del system_owner. Cada tarea
    # comprueba el KILL-SWITCH `cortex.autonomy_enabled` (default OFF ⇒ no-op), así que
    # estas entradas pueden tickear siempre sin coste: encolan barato y la tarea sale
    # enseguida si la autonomía está apagada. Queue `default` (Ollama local + web
    # acotada, sin infra). Activación = encender el switch desde la UI del owner.
    sched["cortex-curiosity"] = {
        "task": "workers.cortex_curiosity_loop",
        "schedule": schedule(run_every=900.0),  # cada 15 min
        "options": {"queue": "default"},
    }
    sched["cortex-reflection"] = {
        "task": "workers.cortex_reflect_scheduled",
        "schedule": crontab(hour="4", minute="15"),  # reflexión diaria (madrugada)
        "options": {"queue": "default"},
    }
    sched["cortex-maintenance"] = {
        "task": "workers.cortex_maintenance",
        "schedule": crontab(hour="4", minute="45"),  # mantenimiento/olvido diario
        "options": {"queue": "default"},
    }
    # C2 (investigación 2026-07-11): el pulso de plataforma — el córtex siente
    # lo que le pasa al sistema (runs/planes) sin LLM (mapeo determinista).
    sched["cortex-platform-pulse"] = {
        "task": "workers.cortex_platform_pulse",
        "schedule": schedule(run_every=900.0),  # cada 15 min
        "options": {"queue": "default"},
    }
    # C1 (investigación 2026-07-11): iniciativa proactiva — el córtex escribe
    # primero cuando hay aprendizajes pendientes + silencio largo (lógica pura
    # anti-acoso en api_server.cortex.initiative).
    sched["cortex-initiative"] = {
        "task": "workers.cortex_initiative",
        "schedule": schedule(run_every=1800.0),  # cada 30 min
        "options": {"queue": "default"},
    }
    return sched
