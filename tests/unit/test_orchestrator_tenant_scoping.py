"""c5 guard: BYPASSRLS orchestrator loads must carry a tenant_id predicate.

The orchestrator dispatch runs with a BYPASSRLS DB role, so PostgreSQL RLS does
NOT backstop a missing ``tenant_id`` filter (regla dura #1 de CLAUDE.md). The
audit (2026-07-03, c5) found two by-PK ``select(Task)`` loads without it
(``_dispatch`` / ``_revert_to_ready``). This static guard fails CI if a by-id
``select(Task|Plan)`` reappears without a ``tenant_id`` predicate — cheap
defence-in-depth against a future cross-tenant regression.
"""

from __future__ import annotations

import re
from pathlib import Path

_DISPATCH = (
    Path(__file__).resolve().parents[2]
    / "apps"
    / "orchestrator"
    / "src"
    / "orchestrator"
    / "dispatch.py"
)


def test_no_by_id_select_without_tenant_predicate_in_dispatch() -> None:
    src = _DISPATCH.read_text(encoding="utf-8")
    # Whitespace-normalise so black's line-wrapping doesn't hide the pattern.
    normalized = re.sub(r"\s+", " ", src)
    # A ``select(Task|Plan).where(<Model>.id == <name>)`` whose predicate list
    # closes right after the id equality (no trailing comma) has NO tenant
    # filter. The fixed form is ``...where(Task.id == task_id, Task.tenant_id
    # == ...)`` — a comma before the close, so it does not match.
    offenders = re.findall(
        r"select\((?:Task|Plan)\)\.where\((?:Task|Plan)\.id == \w+\)",
        normalized,
    )
    assert not offenders, (
        "BYPASSRLS by-id select without a tenant_id predicate in the "
        f"orchestrator dispatch (regla dura #1): {offenders}"
    )
