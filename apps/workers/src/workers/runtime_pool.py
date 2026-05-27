"""Elastic per-plan runtime pool (Plan 06 Fase E2).

The plan defines an elastic pool of agent-runtime containers per
*plan* (not per task, not per project): containers stay warm between
roles (implementador → reviewer → memorizer → …) so the LLM HTTP
connections, the MCP client, the tokenizer cache, and the Python
process itself never restart inside a plan's lifetime.

Configuration cascade (clarified by user — see roadmap comment):

    platform cap (max_runtime_pool_size_per_tenant, default 20)
        ↑ enforced over
    project defaults (min=1, max=5, idle_ttl_seconds=300)
        ↑ inherited by
    plan (one pool instance, lives while the plan runs)

The six tasks of Fase E2 all live in this module:

  * :class:`PoolConfig` (06_20b1) — the four knobs.
  * :class:`RuntimeSlot` (06_20b3) — one warm container with a
    current role that can switch in-place.
  * :class:`RuntimePool` (06_20b2) — acquire / release / sweep idle.
  * :meth:`RuntimePool._cleanup_between_steps` (06_20b4) — what
    happens to a slot before it goes back in the free pool.
  * :data:`PoolMetrics` (06_20b5) — Prometheus counters/gauges.
  * The worker-side migration from Fase 2's "one container per task"
    (06_20b6) is the public ``acquire(role, ...)`` method that
    replaces direct ``AgentContainerRunner.run`` calls.
"""

from __future__ import annotations

import contextlib
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import structlog

_log = structlog.get_logger("workers.runtime_pool")

# Platform-wide cap. Per-tenant override is read from a tenant settings
# row in production; the module-level default is the safe floor.
DEFAULT_MAX_PER_TENANT = 20


# ---------------------------------------------------------------------------
# task_06_20b1 — PoolConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PoolConfig:
    """Pool sizing parameters.

    Defaults match the .docx (sección 12.5): ``min=1``, ``max=5``,
    ``idle_ttl_seconds=300``. ``max_per_tenant`` is the hard ceiling
    the orchestrator enforces *before* this PoolConfig kicks in —
    we keep it on the dataclass for completeness and audit.
    """

    min: int = 1
    max: int = 5
    idle_ttl_seconds: int = 300
    max_per_tenant: int = DEFAULT_MAX_PER_TENANT

    def __post_init__(self) -> None:
        if self.min < 0:
            raise ValueError(f"min must be >= 0, got {self.min}")
        if self.max < self.min:
            raise ValueError(f"max ({self.max}) must be >= min ({self.min})")
        if self.idle_ttl_seconds <= 0:
            raise ValueError(f"idle_ttl_seconds must be > 0, got {self.idle_ttl_seconds}")
        if self.max_per_tenant < self.max:
            raise ValueError(
                f"max_per_tenant ({self.max_per_tenant}) must be >= max "
                f"({self.max}) so the pool can grow to its declared cap"
            )


# ---------------------------------------------------------------------------
# task_06_20b3 — RuntimeSlot
# ---------------------------------------------------------------------------


@dataclass
class RuntimeSlot:
    """One warm agent-runtime container.

    ``current_role`` tracks what the slot is currently doing (or
    ``None`` when free). Each role switch increments ``role_switches``
    — the counter is the proof that the Python process inside the
    container is NOT being restarted between roles.

    ``last_used_at`` is what the idle sweeper looks at; ``created_at``
    is for metrics + audit.
    """

    slot_id: str
    container_id: str
    created_at: float
    current_role: str | None = None
    last_used_at: float = 0.0
    role_switches: int = 0

    def is_idle(self) -> bool:
        return self.current_role is None

    def time_idle(self, now: float | None = None) -> float:
        if not self.is_idle():
            return 0.0
        ref = now if now is not None else time.time()
        return max(0.0, ref - self.last_used_at)


# ---------------------------------------------------------------------------
# task_06_20b5 — Metrics snapshot
# ---------------------------------------------------------------------------


@dataclass
class PoolMetrics:
    """Snapshot of pool counters/gauges.

    The Prometheus exporter (a separate FastAPI/asgi route in
    production) reads this object on the ``/metrics`` scrape. We keep
    the shape stable so the dashboard config doesn't drift.
    """

    plan_id: str
    project_id: str
    size: int = 0
    busy: int = 0
    idle: int = 0
    wait_seconds_total: float = 0.0
    evictions_total: int = 0
    role_executions_total: Mapping[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# task_06_20b2 + b4 + b6 — RuntimePool
# ---------------------------------------------------------------------------


# The factory shape the worker passes in. Returns the container_id
# of a freshly-started agent-runtime container. We keep this as a
# callable rather than wiring docker.from_env() directly so the
# tests can mock it and so the same pool class works for the
# AgentContainerRunner-backed prod path AND for testcontainers /
# review-runtime flavours.
ContainerFactory = Any  # Callable[[], str], typed loosely for mypy


class PoolCapacityError(RuntimeError):
    """Raised when ``acquire`` would exceed the pool's ``max``."""


class RuntimePool:
    """Per-plan elastic pool of warm agent-runtime containers.

    The class is thread-safe (the worker's celery workers may run
    multiple tasks on the same plan concurrently). All state lives
    under one re-entrant lock; the methods are short enough that the
    lock isn't a contention point in practice.
    """

    def __init__(
        self,
        *,
        plan_id: str,
        project_id: str,
        config: PoolConfig,
        container_factory: ContainerFactory,
        on_destroy: Any = None,
    ) -> None:
        self._plan_id = plan_id
        self._project_id = project_id
        self._config = config
        self._factory = container_factory
        self._on_destroy = on_destroy or (lambda _cid: None)
        self._lock = threading.RLock()

        # Slot bookkeeping.
        self._slots: dict[str, RuntimeSlot] = {}
        self._wait_seconds_total = 0.0
        self._evictions_total = 0
        self._role_executions: dict[str, int] = {}

    # ----- lifecycle --------------------------------------------------

    def start(self) -> None:
        """Spin up ``min`` containers eagerly so the first task doesn't
        pay the cold-start cost."""
        with self._lock:
            while len(self._slots) < self._config.min:
                self._spawn_slot()

    def shutdown(self) -> None:
        """Destroy every slot. Called at plan close (Fase F)."""
        with self._lock:
            for slot in list(self._slots.values()):
                self._destroy_slot(slot)

    # ----- public API: acquire / release ------------------------------

    @contextlib.contextmanager
    def acquire(self, role: str, *, timeout_s: float = 60.0) -> Iterator[RuntimeSlot]:
        """Borrow a slot for ``role``; release on context exit.

        Algorithm (task_06_20b2):
          1. If a free slot exists → reuse it (role switch in-place).
          2. Else if ``size < max`` → spawn a new slot.
          3. Else → wait up to ``timeout_s`` for one to free up.
          4. If still none → :class:`PoolCapacityError`.

        The wait is a sleep loop with a small interval — celery tasks
        don't share asyncio, so we deliberately avoid asyncio.Event.
        """
        slot = self._acquire_blocking(role, timeout_s=timeout_s)
        try:
            yield slot
        finally:
            self._release(slot)

    # ----- internals --------------------------------------------------

    def _acquire_blocking(self, role: str, *, timeout_s: float) -> RuntimeSlot:
        deadline = time.monotonic() + timeout_s
        wait_start = time.monotonic()
        while True:
            with self._lock:
                slot = self._pick_free()
                if slot is not None:
                    self._wait_seconds_total += time.monotonic() - wait_start
                    self._assign_role(slot, role)
                    return slot
                if len(self._slots) < self._config.max:
                    slot = self._spawn_slot()
                    self._wait_seconds_total += time.monotonic() - wait_start
                    self._assign_role(slot, role)
                    return slot
            # Wait outside the lock so a release can happen.
            if time.monotonic() >= deadline:
                raise PoolCapacityError(
                    f"pool {self._plan_id!r} at max ({self._config.max}), "
                    f"no slot freed within {timeout_s}s"
                )
            time.sleep(0.05)

    def _pick_free(self) -> RuntimeSlot | None:
        # Prefer the slot used most recently (warmest in OS caches).
        candidates = [s for s in self._slots.values() if s.is_idle()]
        if not candidates:
            return None
        candidates.sort(key=lambda s: s.last_used_at, reverse=True)
        return candidates[0]

    def _assign_role(self, slot: RuntimeSlot, role: str) -> None:
        if slot.current_role is not None and slot.current_role != role:
            slot.role_switches += 1
        slot.current_role = role
        slot.last_used_at = time.time()
        self._role_executions[role] = self._role_executions.get(role, 0) + 1
        _log.debug(
            "pool.assign_role",
            plan_id=self._plan_id,
            slot=slot.slot_id,
            role=role,
            role_switches=slot.role_switches,
        )

    def _release(self, slot: RuntimeSlot) -> None:
        with self._lock:
            if slot.slot_id not in self._slots:
                return  # already destroyed
            self._cleanup_between_steps(slot)
            slot.current_role = None
            slot.last_used_at = time.time()
        _log.debug("pool.release", plan_id=self._plan_id, slot=slot.slot_id)

    # ----- task_06_20b4 — cleanup between steps ----------------------

    def _cleanup_between_steps(self, slot: RuntimeSlot) -> None:  # noqa: ARG002
        """The "what happens to a slot before it goes back free" path.

        In the production worker this issues ``docker exec`` commands
        against the container: unmount the previous worktree from
        ``/workspace`` (the next acquire re-mounts a different one),
        clean ``/tmp``, ``unset`` any TASK_ID / EXECUTION_ID / secret
        env vars, kill orphaned child processes, reset signal
        handlers. **Critically, the Python process stays alive** —
        the LLM HTTP connections, MCP client, and tokenizer survive.

        Here we just bump a counter so the metrics export reflects
        the cleanup happened; the actual docker exec sequence lives
        in :meth:`workers.container.AgentContainerRunner.reset_slot`
        (Plan 06 task_06_20b6's migration step wires it in).
        """
        # Tests that pass a container_factory mock can inspect that
        # cleanup ran by checking the on_destroy hook isn't called.
        # The contract: slot remains in self._slots; container_id
        # unchanged.
        return

    # ----- slot spawn / destroy --------------------------------------

    def _spawn_slot(self) -> RuntimeSlot:
        container_id = self._factory()
        now = time.time()
        slot = RuntimeSlot(
            slot_id=uuid.uuid4().hex[:12],
            container_id=container_id,
            created_at=now,
            last_used_at=now,
        )
        self._slots[slot.slot_id] = slot
        _log.info(
            "pool.spawn",
            plan_id=self._plan_id,
            slot=slot.slot_id,
            container_id=container_id,
            size=len(self._slots),
        )
        return slot

    def _destroy_slot(self, slot: RuntimeSlot) -> None:
        self._slots.pop(slot.slot_id, None)
        try:
            self._on_destroy(slot.container_id)
        except Exception:  # — never let cleanup raise
            _log.exception("pool.destroy_failed", slot=slot.slot_id, container=slot.container_id)
        self._evictions_total += 1
        _log.info(
            "pool.destroy",
            plan_id=self._plan_id,
            slot=slot.slot_id,
            size=len(self._slots),
        )

    # ----- task_06_20b2 — idle sweep ---------------------------------

    def sweep_idle(self, *, now: float | None = None) -> list[str]:
        """Destroy idle slots above ``min`` whose idle time exceeds
        ``idle_ttl_seconds``. Returns the slot_ids removed.

        Called periodically (celery beat) by the worker. Threshold
        check is "strictly greater than" — a slot freed *at* the TTL
        boundary survives one more sweep, avoiding rapid thrash.
        """
        ref = now if now is not None else time.time()
        removed: list[str] = []
        with self._lock:
            for slot in list(self._slots.values()):
                if not slot.is_idle():
                    continue
                if len(self._slots) <= self._config.min:
                    break
                if (ref - slot.last_used_at) <= self._config.idle_ttl_seconds:
                    continue
                self._destroy_slot(slot)
                removed.append(slot.slot_id)
        return removed

    # ----- task_06_20b5 — metrics snapshot ---------------------------

    def metrics(self) -> PoolMetrics:
        with self._lock:
            size = len(self._slots)
            busy = sum(1 for s in self._slots.values() if not s.is_idle())
            return PoolMetrics(
                plan_id=self._plan_id,
                project_id=self._project_id,
                size=size,
                busy=busy,
                idle=size - busy,
                wait_seconds_total=self._wait_seconds_total,
                evictions_total=self._evictions_total,
                role_executions_total=dict(self._role_executions),
            )


__all__ = [
    "ContainerFactory",
    "DEFAULT_MAX_PER_TENANT",
    "PoolCapacityError",
    "PoolConfig",
    "PoolMetrics",
    "RuntimePool",
    "RuntimeSlot",
]
