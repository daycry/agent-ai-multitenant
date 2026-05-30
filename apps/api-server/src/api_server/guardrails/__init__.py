"""api-server guardrails host integration (Plan 11 Fase E).

The shared guardrails *engine* lives in ``packages/shared-guardrails`` and
is pure (no DB, no I/O): it evaluates the declarative pipeline and emits a
:class:`shared_guardrails.types.PipelineDecision`. This package is the
api-server *host* side that PERSISTS + OBSERVES those decisions:

  - :mod:`api_server.guardrails.events` — the ``record_guardrail_event``
    service + the ``record_pipeline_decision`` hook the pipeline host calls
    when guardrails fire, writing one tenant-scoped ``guardrail_events`` row
    per triggered guardrail with a **masked** detail (never the raw
    secret / PII).
"""

from __future__ import annotations
