"""Security invariant suite (Plan 15 Fase C — internal pentest, task_15_14).

These tests assert the platform's hardening posture holds at the *source*
level so a regression in compose / migrations / the worker isolation
envelope fails CI BEFORE it ships. They are the automated half of the
internal pentest; deep manual exploitation + the external professional
audit (task_15_27) are documented as HUMAN tests in
``docs/06-runbooks/internal-pentest-methodology.md``.
"""
