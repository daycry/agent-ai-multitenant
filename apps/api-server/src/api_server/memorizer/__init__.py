"""Memorizer (Plan 04 task_04_03).

Distils a finished `Execution` into 0-N `MemoryEntry` rows. Three
pieces:

  - `policy`       — pure predicate: should this execution produce
    memories at all?
  - `distillation` — pure async function that calls the LLM to extract
    short candidates from the execution's steps_log.
  - `persistence`  — translates candidates into ORM rows and writes
    them under the right tenant + scope.

The Celery wiring lives in `apps/workers/src/workers/memorizer.py`
(`workers.memorize_execution`). It orchestrates these three pieces;
this package stays free of Celery and Redis so the unit tests run in
plain Python.
"""

from api_server.memorizer.distillation import (
    MemoryCandidate,
    distil_execution,
    distil_human_work_session,
)
from api_server.memorizer.persistence import (
    count_memories_for_source,
    persist_memory_candidates,
)
from api_server.memorizer.policy import (
    MemorizeDecision,
    MemorizeSkipReason,
    should_memorize,
    should_memorize_human_session,
)

__all__ = [
    "MemorizeDecision",
    "MemorizeSkipReason",
    "MemoryCandidate",
    "count_memories_for_source",
    "distil_execution",
    "distil_human_work_session",
    "persist_memory_candidates",
    "should_memorize",
    "should_memorize_human_session",
]
