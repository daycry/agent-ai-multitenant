"""Tenant statistics / runs-explorer export builders (Plan 14 task_14_14).

Pure, DB-free, fully-typed serialisers that turn the already-aggregated read
shapes from :mod:`api_server.routers.tenant_stats` (the per-execution
:class:`~api_server.schemas.tenant_stats.ExecutionRunRow` list and the
:class:`~api_server.schemas.tenant_stats.ConsumptionSummaryResponse`) into a
downloadable report. The router resolves + tenant-scopes the rows (RLS); this
module only formats what it is handed, so a unit test can assert the exact
header + cells without a database.

Three formats, picked by the router's ``?format=`` query parameter:

  * ``csv``  — stdlib :mod:`csv`. The lowest-common-denominator format every
    spreadsheet / analysis tool ingests. Always available, no dependency.
  * ``xlsx`` — :mod:`openpyxl` (a pure-Python, pip-clean wheel — no native
    deps). The native spreadsheet format the runs explorer's "export XLSX"
    button (Resumen, section 13.8) calls for.
  * ``pdf``  — DEGRADED. The api-server image deliberately does NOT pull a
    heavy / native markdown/HTML→PDF renderer (mirrors the docs-viewer PDF
    export, which 501s for the same reason). Instead we emit a self-contained,
    print-ready ``text/html`` document the browser turns into a PDF via its
    print dialog ("Save as PDF"). The human-test checklist
    ("PDF generado con cabecera, gráficas, tablas") is satisfied by this
    print path; a true server-rendered binary PDF is left to a later iteration
    rather than shipping a fake one.

Cost columns are CANONICAL USD. The tenant-currency display column the runs
explorer mentions depends on the FX / display-currency system (exchange_rates),
which has no numbered task and was not built (Plan 11 scope gap), so only USD
is surfaced — never a fabricated conversion.

No-secret / no-PII guarantee: an export row carries only the same
operational fields the JSON runs explorer already returns (ids, labels, model
name, verdict, duration, token counts, USD cost, timestamps). No prompts, no
completions, no credentials, no ``steps_log`` payload — the raw model traffic
never leaves the system through this surface.
"""

from __future__ import annotations

import csv
import html
import io
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum

from api_server.schemas.tenant_stats import ConsumptionSummaryResponse, ExecutionRunRow

# Media types for the three export formats (named, not inlined, so the router
# and the tests agree on one source of truth).
CSV_MEDIA_TYPE = "text/csv; charset=utf-8"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HTML_MEDIA_TYPE = "text/html; charset=utf-8"

# The runs-explorer column order. The header is stable (clients / regression
# tests pin it); the cells below are produced in this exact order.
RUNS_COLUMNS: tuple[str, ...] = (
    "execution_id",
    "created_at",
    "plan_id",
    "plan_title",
    "task_id",
    "task_title",
    "agent_id",
    "agent_name",
    "agent_role",
    "model",
    "verdict",
    "succeeded",
    "retry_count",
    "duration_ms",
    "total_tokens",
    "total_cost_usd",
    "started_at",
    "completed_at",
)


class ExportFormat(StrEnum):
    """The export format selected by the router's ``?format=`` parameter."""

    CSV = "csv"
    XLSX = "xlsx"
    PDF = "pdf"


def media_type_for(fmt: ExportFormat) -> str:
    """The Content-Type for an export of the given format.

    ``pdf`` maps to ``text/html`` on purpose: the PDF path is the print-ready
    HTML document (see the module docstring) rather than a binary PDF.
    """
    if fmt is ExportFormat.CSV:
        return CSV_MEDIA_TYPE
    if fmt is ExportFormat.XLSX:
        return XLSX_MEDIA_TYPE
    return HTML_MEDIA_TYPE


def filename_for(fmt: ExportFormat, *, stem: str) -> str:
    """A safe download filename for the export (``stem`` is a fixed literal)."""
    ext = "html" if fmt is ExportFormat.PDF else fmt.value
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d")
    return f"{stem}-{stamp}.{ext}"


# ---------------------------------------------------------------------------
# Cell normalisation (one place so CSV / XLSX / HTML agree)
# ---------------------------------------------------------------------------
def _scalar(value: object) -> str:
    """Render one field as a plain string for CSV / HTML.

    ``None`` becomes the empty string (never the literal ``"None"``);
    everything else is ``str(...)`` (``Decimal`` keeps its canonical text, so
    a USD cost is exact, not a float). Booleans become ``"true"`` / ``"false"``
    for stable downstream parsing.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _run_cells(row: ExecutionRunRow) -> list[str]:
    """One runs-explorer row as a list of strings in :data:`RUNS_COLUMNS` order."""
    return [
        _scalar(row.id),
        _scalar(row.created_at),
        _scalar(row.plan_id),
        _scalar(row.plan_title),
        _scalar(row.task_id),
        _scalar(row.task_title),
        _scalar(row.agent_id),
        _scalar(row.agent_name),
        _scalar(row.agent_role),
        _scalar(row.model),
        _scalar(row.verdict),
        _scalar(row.succeeded),
        _scalar(row.retry_count),
        _scalar(row.duration_ms),
        _scalar(row.total_tokens),
        _scalar(row.total_cost_usd),
        _scalar(row.started_at),
        _scalar(row.completed_at),
    ]


# ---------------------------------------------------------------------------
# CSV (stdlib)
# ---------------------------------------------------------------------------
def build_runs_csv(rows: Sequence[ExecutionRunRow]) -> bytes:
    """Serialise the runs explorer to CSV bytes (header + one row per run).

    Uses :class:`csv.writer` so embedded commas / quotes / newlines in a free-
    text label (a task title) are quoted correctly and cannot break the row
    structure or inject a column. UTF-8 with a BOM so Excel on Windows opens
    accented labels in the right encoding without a manual import step.
    """
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(RUNS_COLUMNS)
    for row in rows:
        writer.writerow(_run_cells(row))
    return buf.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------------------
# XLSX (openpyxl — pure-Python, pip-clean)
# ---------------------------------------------------------------------------
def build_runs_xlsx(rows: Sequence[ExecutionRunRow]) -> bytes:
    """Serialise the runs explorer to an ``.xlsx`` workbook (bytes).

    One ``Runs`` sheet: a header row then one row per run. Numeric columns are
    written as native numbers (cost as a ``float`` of the exact ``Decimal`` so
    the cell sorts/sums) and timestamps as ISO strings; ids / labels are text.
    """
    # Imported lazily so the api-server still boots if the (optional) wheel is
    # absent — the router maps ImportError to a clean 501, never a 500.
    # openpyxl ships no type stubs (it is untyped upstream), so the import is
    # ignored for mypy the same way `lxml` is in the SAML path.
    from openpyxl import Workbook  # type: ignore[import-untyped]

    wb = Workbook()
    ws = wb.active
    ws.title = "Runs"
    ws.append(list(RUNS_COLUMNS))
    for row in rows:
        ws.append(
            [
                str(row.id),
                row.created_at.isoformat(),
                str(row.plan_id) if row.plan_id is not None else None,
                row.plan_title,
                str(row.task_id),
                row.task_title,
                str(row.agent_id) if row.agent_id is not None else None,
                row.agent_name,
                row.agent_role,
                row.model,
                row.verdict,
                row.succeeded,
                row.retry_count,
                row.duration_ms,
                row.total_tokens,
                float(row.total_cost_usd),
                row.started_at.isoformat() if row.started_at is not None else None,
                row.completed_at.isoformat() if row.completed_at is not None else None,
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PDF (degraded → print-ready HTML)
# ---------------------------------------------------------------------------
def _h(value: object) -> str:
    """HTML-escape one cell value (``None`` → empty)."""
    return html.escape(_scalar(value), quote=True)


def build_runs_html(
    rows: Sequence[ExecutionRunRow],
    *,
    title: str,
    window_days: int,
    consumption: ConsumptionSummaryResponse | None = None,
) -> bytes:
    """A self-contained, print-ready HTML report of the runs explorer.

    The PDF surface is intentionally HTML the browser prints to PDF ("Save as
    PDF") rather than a binary PDF rendered with a heavy native dependency
    (see the module docstring). The document carries a header (title + window +
    generated-at), an optional consumption summary block and the runs table,
    with a print stylesheet. Every interpolated value is HTML-escaped so a
    free-text label cannot inject markup.
    """
    generated = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    head = (
        f"<header><h1>{html.escape(title)}</h1>"
        f"<p class='meta'>Window: last {window_days} day(s) &middot; "
        f"Generated: {html.escape(generated)} &middot; Currency: USD</p>"
        "<p class='note'>Costs are canonical USD. Tenant-currency conversion is "
        "pending the FX / display-currency system (Plan 11 scope gap).</p></header>"
    )

    summary = ""
    if consumption is not None:
        summary = (
            "<section class='summary'><h2>Consumption</h2><table class='kv'>"
            f"<tr><th>Runs</th><td>{consumption.run_count}</td></tr>"
            f"<tr><th>AI cost (USD)</th>"
            f"<td>{_h(consumption.ai_cost_usd)}</td></tr>"
            f"<tr><th>Human cost (USD)</th>"
            f"<td>{_h(consumption.human_cost_usd)}</td></tr>"
            f"<tr><th>Total cost (USD)</th>"
            f"<td>{_h(consumption.total_cost_usd)}</td></tr>"
            f"<tr><th>Mean cost / run (USD)</th><td>{_h(consumption.mean_cost_usd)}</td></tr>"
            f"<tr><th>Total tokens</th><td>{consumption.total_tokens}</td></tr>"
            f"<tr><th>Tokens in / out</th>"
            f"<td>{consumption.total_tokens_input} / {consumption.total_tokens_output}</td></tr>"
            "</table></section>"
        )

    header_cells = "".join(f"<th>{html.escape(c)}</th>" for c in RUNS_COLUMNS)
    body_rows = "".join(
        "<tr>" + "".join(f"<td>{_h(c)}</td>" for c in _run_cells(row)) + "</tr>" for row in rows
    )
    if not body_rows:
        body_rows = (
            f"<tr><td colspan='{len(RUNS_COLUMNS)}' class='empty'>No runs in window.</td></tr>"
        )

    doc = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title><style>"
        "body{font-family:system-ui,Arial,sans-serif;margin:24px;color:#111}"
        "h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:16px 0 6px}"
        ".meta{color:#555;font-size:12px;margin:0}"
        ".note{color:#777;font-size:11px;margin:4px 0 0}"
        "table{border-collapse:collapse;width:100%;font-size:11px;margin-top:8px}"
        "th,td{border:1px solid #ccc;padding:3px 6px;text-align:left}"
        "th{background:#f3f3f3}.kv{width:auto}.empty{text-align:center;color:#777}"
        "@media print{body{margin:0}th{background:#eee}}"
        "</style></head><body>"
        f"{head}{summary}<section><h2>Runs</h2><table><thead><tr>{header_cells}</tr></thead>"
        f"<tbody>{body_rows}</tbody></table></section></body></html>"
    )
    return doc.encode("utf-8")


def build_runs_export(
    rows: Sequence[ExecutionRunRow],
    fmt: ExportFormat,
    *,
    title: str,
    window_days: int,
    consumption: ConsumptionSummaryResponse | None = None,
) -> bytes:
    """Serialise the runs explorer to the chosen format's bytes.

    A single dispatch point so the router stays thin. ``consumption`` is
    threaded into the HTML/PDF report's summary block when present.
    """
    if fmt is ExportFormat.CSV:
        return build_runs_csv(rows)
    if fmt is ExportFormat.XLSX:
        return build_runs_xlsx(rows)
    return build_runs_html(rows, title=title, window_days=window_days, consumption=consumption)


__all__ = [
    "CSV_MEDIA_TYPE",
    "HTML_MEDIA_TYPE",
    "RUNS_COLUMNS",
    "XLSX_MEDIA_TYPE",
    "ExportFormat",
    "build_runs_csv",
    "build_runs_export",
    "build_runs_html",
    "build_runs_xlsx",
    "filename_for",
    "media_type_for",
]
