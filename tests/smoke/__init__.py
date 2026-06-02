"""Post-deploy smoke suite (Plan 15 Fase D — task_15_26).

This suite validates a *deployed* stack end-to-end against a configurable
base URL (env ``SMOKE_BASE_URL``): the health/readiness of each service, an
auth/login probe, a minimal API v1 call with a token, admin-panel
reachability and the monitoring endpoints (Grafana / Prometheus). It is the
automated half of the post-deploy checklist documented in
``docs/06-runbooks/01-installation-from-scratch.md`` and
``docs/06-runbooks/02-troubleshooting.md``.

Two run modes, ONE codebase:

  * **No live stack (CI / this dev env)** — every live test SKIP-GUARDS
    cleanly (``pytest.skip`` with a clear reason) when ``SMOKE_BASE_URL`` is
    unset or the target is unreachable, so ``pytest tests/smoke/`` exits 0
    everywhere and never reddens CI.
  * **Live stack (human / post-deploy)** — point ``SMOKE_BASE_URL`` at the
    deployed api-server (e.g. ``https://platform.example.com``) and the
    tests run for real. Optional env knobs let the operator supply
    credentials and the monitoring URLs so the probes hit the live surface.

The probe *logic* itself (status-code/JSON interpretation, URL joining,
result shaping) lives in :mod:`tests.smoke.probes` as pure, transport-agnostic
functions and is unit-tested against an ``httpx.MockTransport`` in
``test_probes_unit.py`` — so the assertions are covered even when no stack is
deployed.
"""
