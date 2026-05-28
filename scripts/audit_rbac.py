"""Audit every FastAPI route's RBAC posture (Plan 06.8 task_06_8_02).

Walks the api-server's routes and reports, per `(method, path)`:

  - which auth dependency is in effect (no auth / get_principal /
    require_system_admin / require_tenant_admin / require_tenant_member /
    require_tenant_role(...))
  - the response model name, if any
  - the router file the route was defined in

Output is a Markdown table on stdout. The plan keeps a curated version
in `docs/04-reference/rbac.md`; this script is the raw data the curated
matrix is reviewed against.

Usage::

    python scripts/audit_rbac.py
    python scripts/audit_rbac.py --csv > /tmp/audit.csv

The script imports `api_server.main.create_app()` and inspects the
FastAPI route tree — same source of truth FastAPI uses.
"""

from __future__ import annotations

import argparse
import csv
import sys

# We need the api-server importable. `pyproject` already adds `apps/*`
# to sys.path in the test setup; for the script we add the src dirs
# explicitly so it works from a fresh shell.
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
for p in (
    _ROOT / "apps" / "api-server" / "src",
    _ROOT / "packages" / "shared-domain" / "src",
    _ROOT / "packages" / "shared-llm" / "src",
):
    if str(p) not in sys.path and p.exists():
        sys.path.insert(0, str(p))


def _format_dep(dep: Any) -> str:
    """Map a FastAPI dependant to a short label.

    `dep` is the callable passed to `Depends(...)`. For closures from
    `require_tenant_role(role)` we return ``role:tenant_user`` etc.
    """
    if dep is None:
        return "—"
    name = getattr(dep, "__name__", repr(dep))
    return name


def _walk_deps(dependant: Any, depth: int = 0) -> list[str]:
    """Collect dep names from a FastAPI Dependant tree (depth-first).

    Mostly we care about the *first* role-related dep hit, but we keep
    the whole chain so the matrix can spot odd combos (e.g. an endpoint
    that mounts both `require_tenant_admin` AND `require_system_admin`).
    """
    out: list[str] = []
    for sub in getattr(dependant, "dependencies", []) or []:
        out.append(_format_dep(sub.call))
        out.extend(_walk_deps(sub, depth + 1))
    return out


def _classify(deps: list[str]) -> str:
    """Reduce the dep chain to one of:
    no-auth | principal | system_admin | tenant_admin | tenant_member |
    tenant_role:<role> | (mixed)"""
    role_marker_to_label = {
        "require_system_admin": "system_admin",
        "require_tenant_admin": "tenant_admin",
        "require_tenant_member": "tenant_member",
    }
    role_labels: list[str] = []
    has_principal = False
    has_session = False
    for d in deps:
        if d in role_marker_to_label:
            role_labels.append(role_marker_to_label[d])
        elif d == "_check":
            # The closure returned by require_tenant_role(role) — we
            # can't recover the role name from here without inspection.
            role_labels.append("tenant_role:?")
        elif d == "get_principal":
            has_principal = True
        elif d in {"get_tenant_session", "get_admin_session"}:
            has_session = True
    if not role_labels:
        if has_principal or has_session:
            # Authenticated but not role-gated. The endpoint is open to
            # any logged-in user with a session — appropriate for read
            # endpoints but a smell on POST/PUT/DELETE.
            return "principal-only"
        return "no-auth"
    if len(set(role_labels)) == 1:
        return role_labels[0]
    return "mixed:" + ",".join(sorted(set(role_labels)))


def _build_rows() -> list[dict[str, str]]:
    from api_server.main import create_app

    app = create_app()
    rows: list[dict[str, str]] = []
    for route in app.routes:
        # Skip the framework's /openapi.json /docs etc — they only have
        # `endpoint_name == route.unique_id` and no Dependant chain.
        methods = sorted(getattr(route, "methods", set()) or set())
        if not methods or methods == ["HEAD"]:
            continue
        # Some routes are WebSocket — they don't expose `dependant`.
        dependant = getattr(route, "dependant", None)
        deps = _walk_deps(dependant) if dependant is not None else []
        classification = _classify(deps)
        endpoint = getattr(route, "endpoint", None)
        endpoint_name = endpoint.__name__ if endpoint else "?"
        endpoint_module = getattr(endpoint, "__module__", "?") if endpoint else "?"
        for m in methods:
            if m == "HEAD":
                continue
            rows.append(
                {
                    "method": m,
                    "path": getattr(route, "path", "?"),
                    "endpoint": endpoint_name,
                    "module": endpoint_module,
                    "rbac": classification,
                    "deps": " > ".join(deps) or "—",
                }
            )
    rows.sort(key=lambda r: (r["module"], r["path"], r["method"]))
    return rows


def _print_markdown(rows: list[dict[str, str]]) -> None:
    headers = ["Method", "Path", "RBAC", "Endpoint", "Module"]
    widths = [
        max(len(r["method"]) for r in [*rows, {"method": headers[0]}]),
        max(len(r["path"]) for r in [*rows, {"path": headers[1]}]),
        max(len(r["rbac"]) for r in [*rows, {"rbac": headers[2]}]),
        max(len(r["endpoint"]) for r in [*rows, {"endpoint": headers[3]}]),
        max(len(r["module"]) for r in [*rows, {"module": headers[4]}]),
    ]

    def _fmt_row(cells: list[str]) -> str:
        return "| " + " | ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True)) + " |"

    print(_fmt_row(headers))
    print("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    for r in rows:
        print(_fmt_row([r["method"], r["path"], r["rbac"], r["endpoint"], r["module"]]))


def _print_csv(rows: list[dict[str, str]]) -> None:
    writer = csv.DictWriter(
        sys.stdout, fieldnames=["method", "path", "rbac", "endpoint", "module", "deps"]
    )
    writer.writeheader()
    writer.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", action="store_true", help="Emit CSV instead of Markdown.")
    args = ap.parse_args()
    rows = _build_rows()
    if args.csv:
        _print_csv(rows)
    else:
        _print_markdown(rows)
    # Print a summary to stderr so it's easy to spot smells.
    by_class: dict[str, int] = {}
    for r in rows:
        by_class[r["rbac"]] = by_class.get(r["rbac"], 0) + 1
    print(file=sys.stderr)
    print("RBAC summary:", file=sys.stderr)
    for k, v in sorted(by_class.items()):
        print(f"  {k:24} {v:4d}", file=sys.stderr)


if __name__ == "__main__":
    main()
