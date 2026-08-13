"""Host + container alert-rule tests (Plan 12 — Backup/Restore, task_12_14).

The plan's automated check for task_12_14 (auto_12_14_a) is "load + parse the
rules file; assert each required alert exists with the right metric / threshold
/ duration; the YAML is structurally valid (promtool-style structure)".

A live ``curl prometheus`` / ``amtool`` check needs a RUNNING stack and cannot
run in CI without the monitoring overlay up — that is a human / CI-with-stack
check (see the test plan note). Here we VALIDATE THE CONFIG statically instead:

  * the Prometheus alert RULES file
    (``docker/monitoring/prometheus/rules/host_alerts.yml``) parses as YAML and
    has the promtool-style ``groups -> rules -> {alert, expr, ...}`` shape;
  * EACH of the five alerts the plan requires exists with the right metric, the
    right comparison/threshold, and (where the plan says "sustained" / "for a
    duration") a ``for:`` window:
      1. disk usage > 80%            → HostDiskUsageHigh
      2. RAM > 90% SUSTAINED         → HostMemoryUsageHigh (for: window)
      3. swap active                 → HostSwapActive
      4. OOM kills (host + cont.)    → HostOOMKills / ContainerOOMKilled
      5. last backup failed          → BackupLastRunFailed / BackupTooOld
  * Prometheus is WIRED to load the rules + push to Alertmanager
    (``prometheus.yml`` has the ``rule_files`` glob + an ``alerting`` block);
  * Alertmanager is WIRED to a receiver that reuses the platform notifier
    (``alertmanager.yml`` routes to a webhook receiver);
  * the backup engine's metric emitter (``workers.backup_metrics``) renders the
    EXACT metric names the "last backup failed" rules reference, with 1/0 on
    success/failure.

Pure file parsing + a pure-function render assertion — no DB, no Redis, no live
network. Marked ``integration`` so it rides the integration suite, but it has no
external dependency.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.integration

# Repo root: tests/integration/<this file> -> parents[2] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_MONITORING = _REPO_ROOT / "docker" / "monitoring"
_RULES_FILE = _MONITORING / "prometheus" / "rules" / "host_alerts.yml"
_PROMETHEUS_FILE = _MONITORING / "prometheus" / "prometheus.yml"
_ALERTMANAGER_FILE = _MONITORING / "alertmanager" / "alertmanager.yml"


# ---------------------------------------------------------------------------
# Helpers — parse the rules file once, index alerts by name.
# ---------------------------------------------------------------------------
def _load_rules() -> dict[str, Any]:
    assert _RULES_FILE.is_file(), f"alert rules file missing: {_RULES_FILE}"
    with _RULES_FILE.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    assert isinstance(doc, dict), "rules file must be a YAML mapping"
    return doc


def _index_alerts(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return {alert_name: rule_dict} across every group (promtool shape)."""
    alerts: dict[str, dict[str, Any]] = {}
    for group in doc["groups"]:
        for rule in group["rules"]:
            if "alert" in rule:
                alerts[rule["alert"]] = rule
    return alerts


@pytest.fixture(scope="module")
def alerts() -> dict[str, dict[str, Any]]:
    return _index_alerts(_load_rules())


# ---------------------------------------------------------------------------
# Structural validity (promtool-style).
# ---------------------------------------------------------------------------
def test_rules_file_is_structurally_valid() -> None:
    doc = _load_rules()
    # Top level: groups is a non-empty list.
    assert isinstance(doc.get("groups"), list) and doc["groups"], "groups missing/empty"
    for group in doc["groups"]:
        assert isinstance(group.get("name"), str) and group["name"], "group needs a name"
        assert isinstance(group.get("rules"), list) and group["rules"], "group needs rules"
        for rule in group["rules"]:
            # Every rule in these groups is an ALERTING rule (not a recording
            # rule), so each must carry `alert` + `expr` (promtool requires it).
            assert "alert" in rule, f"rule missing `alert`: {rule}"
            assert isinstance(rule["alert"], str) and rule["alert"]
            assert isinstance(rule.get("expr"), str) and rule["expr"].strip(), (
                f"alert {rule['alert']} missing a non-empty expr"
            )
            # labels + annotations are mappings when present.
            if "labels" in rule:
                assert isinstance(rule["labels"], dict)
            if "annotations" in rule:
                assert isinstance(rule["annotations"], dict)


def test_no_duplicate_alert_names(alerts: dict[str, dict[str, Any]]) -> None:
    # _index_alerts collapses dupes; compare against the raw count.
    doc = _load_rules()
    raw = [r["alert"] for g in doc["groups"] for r in g["rules"] if "alert" in r]
    assert len(raw) == len(set(raw)), f"duplicate alert names: {raw}"
    # And every indexed alert is reachable.
    assert set(alerts) == set(raw)


# ---------------------------------------------------------------------------
# Each required alert: metric + threshold + duration.
# ---------------------------------------------------------------------------
def test_disk_usage_alert(alerts: dict[str, dict[str, Any]]) -> None:
    rule = alerts["HostDiskUsageHigh"]
    expr = rule["expr"]
    # Disk usage built from the node-exporter filesystem gauges.
    assert "node_filesystem_avail_bytes" in expr
    assert "node_filesystem_size_bytes" in expr
    # > 80% free-space-below-20% threshold.
    assert "0.80" in expr
    assert "for" in rule  # sustained, not instantaneous
    assert rule["labels"]["severity"] in {"warning", "critical"}


def test_ram_sustained_alert(alerts: dict[str, dict[str, Any]]) -> None:
    rule = alerts["HostMemoryUsageHigh"]
    expr = rule["expr"]
    assert "node_memory_MemAvailable_bytes" in expr
    assert "node_memory_MemTotal_bytes" in expr
    assert "0.90" in expr  # > 90%
    # "SUSTAINED >90% for a duration" — the plan explicitly wants a window.
    assert rule.get("for") == "5m", f"RAM alert must be sustained, got for={rule.get('for')}"


def test_swap_active_alert(alerts: dict[str, dict[str, Any]]) -> None:
    rule = alerts["HostSwapActive"]
    expr = rule["expr"]
    assert "node_memory_SwapTotal_bytes" in expr
    assert "node_memory_SwapFree_bytes" in expr
    assert "> 0" in expr  # any swap in use


def test_host_oom_kill_alert(alerts: dict[str, dict[str, Any]]) -> None:
    rule = alerts["HostOOMKills"]
    expr = rule["expr"]
    # The plan names node_vmstat_oom_kill specifically.
    assert "node_vmstat_oom_kill" in expr
    assert "increase(" in expr and "> 0" in expr
    assert rule["labels"]["severity"] == "critical"


def test_container_oom_alert(alerts: dict[str, dict[str, Any]]) -> None:
    rule = alerts["ContainerOOMKilled"]
    expr = rule["expr"]
    # cAdvisor's per-container OOM counter (the plan says "container OOM").
    assert "container_oom_events_total" in expr
    assert "> 0" in expr


def test_last_backup_failed_alert(alerts: dict[str, dict[str, Any]]) -> None:
    # Primary: last run reported failure/invalid.
    failed = alerts["BackupLastRunFailed"]
    assert "agentic_backup_last_success" in failed["expr"]
    assert "== 0" in failed["expr"]
    assert failed["labels"]["severity"] == "critical"
    # Defence in depth: backup too old (engine never ran).
    too_old = alerts["BackupTooOld"]
    assert "agentic_backup_last_success_timestamp_seconds" in too_old["expr"]
    # 26h threshold = 93600 seconds.
    assert "93600" in too_old["expr"]


def test_all_required_alerts_present(alerts: dict[str, dict[str, Any]]) -> None:
    required = {
        "HostDiskUsageHigh",
        "HostMemoryUsageHigh",
        "HostSwapActive",
        "HostOOMKills",
        "ContainerOOMKilled",
        "BackupLastRunFailed",
        "BackupTooOld",
    }
    missing = required - set(alerts)
    assert not missing, f"required alerts missing from rules file: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Prometheus is wired to LOAD the rules + push to Alertmanager.
# ---------------------------------------------------------------------------
def test_prometheus_loads_rules_and_alerts() -> None:
    assert _PROMETHEUS_FILE.is_file()
    with _PROMETHEUS_FILE.open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    # rule_files glob picks up the rules dir.
    rule_files = cfg.get("rule_files")
    assert rule_files and any("rules" in rf for rf in rule_files), rule_files
    # alerting block points at the alertmanager service (not commented out).
    alerting = cfg.get("alerting")
    assert isinstance(alerting, dict), "prometheus.yml `alerting` block must be active"
    targets = alerting["alertmanagers"][0]["static_configs"][0]["targets"]
    assert any("alertmanager" in t for t in targets), targets


# ---------------------------------------------------------------------------
# Alertmanager routes to a receiver that reuses the platform notifier.
# ---------------------------------------------------------------------------
def test_alertmanager_routes_to_platform_notifier() -> None:
    assert _ALERTMANAGER_FILE.is_file()
    with _ALERTMANAGER_FILE.open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    # A default receiver exists and the route funnels to it.
    receiver = cfg["route"]["receiver"]
    names = {r["name"] for r in cfg["receivers"]}
    assert receiver in names, f"route receiver {receiver!r} not in {names}"
    # The receiver reuses the platform notifier via a webhook (not a divergent
    # SMTP/Slack path) — assert at least one webhook receiver is configured.
    assert any("webhook_configs" in r for r in cfg["receivers"]), (
        "no webhook receiver — task_12_14 requires reusing the platform notifier"
    )


# ---------------------------------------------------------------------------
# The backup metric the "last backup failed" rules reference is REAL: the
# engine's emitter renders those exact metric names with 1/0 on success/fail.
# ---------------------------------------------------------------------------
def test_backup_metrics_emitter_renders_referenced_metrics() -> None:
    from workers.backup_metrics import render_backup_metrics

    ok = render_backup_metrics(success=True, now=1_700_000_000.0, last_success_ts=1_700_000_000.0)
    assert "agentic_backup_last_success 1" in ok
    assert "agentic_backup_last_success_timestamp_seconds 1700000000" in ok

    bad = render_backup_metrics(success=False, now=1_700_000_100.0, last_success_ts=1_700_000_000.0)
    assert "agentic_backup_last_success 0" in bad
    # A failure preserves the LAST successful timestamp (does not reset the age
    # clock BackupTooOld measures from).
    assert "agentic_backup_last_success_timestamp_seconds 1700000000" in bad


def test_backup_metrics_written_atomically(tmp_path: Path) -> None:
    from workers.backup_metrics import write_backup_metrics

    target = tmp_path / "sub" / "agentic_backup.prom"
    assert write_backup_metrics(target, success=True) is True
    body = target.read_text(encoding="utf-8")
    assert "agentic_backup_last_success 1" in body
    # No temp files left behind.
    leftovers = list(target.parent.glob("*.tmp"))
    assert not leftovers, leftovers


# ---------------------------------------------------------------------------
# AUD16-19: la copia OFFSITE era invisible — uploaded=[] en todos los bundles
# y ninguna métrica/alerta lo decía. El emitter publica cuántos artefactos
# subieron en el último run y cuándo fue el último upload BUENO (preservado en
# fallos, como el success_ts); la regla BackupOffsiteStale solo arma cuando
# ALGUNA vez hubo offsite (ts > 0) — un host sin destino configurado no alerta
# (decisión gated del operador), lo dice el runbook.
# ---------------------------------------------------------------------------
def test_backup_offsite_metrics_are_rendered() -> None:
    from workers.backup_metrics import render_backup_metrics

    ok = render_backup_metrics(
        success=True,
        now=1_700_000_000.0,
        last_success_ts=1_700_000_000.0,
        offsite_uploaded=2,
        offsite_last_success_ts=1_700_000_000.0,
    )
    assert "agentic_backup_offsite_uploaded 2" in ok
    assert "agentic_backup_offsite_last_success_timestamp_seconds 1700000000" in ok

    none_up = render_backup_metrics(
        success=True,
        now=1_700_000_100.0,
        last_success_ts=1_700_000_100.0,
        offsite_uploaded=0,
        offsite_last_success_ts=1_700_000_000.0,
    )
    assert "agentic_backup_offsite_uploaded 0" in none_up
    # Sin upload en este run, el reloj del último upload BUENO se preserva.
    assert "agentic_backup_offsite_last_success_timestamp_seconds 1700000000" in none_up


def test_backup_offsite_stale_alert(alerts: dict[str, dict[str, Any]]) -> None:
    rule = alerts["BackupOffsiteStale"]
    assert "agentic_backup_offsite_last_success_timestamp_seconds" in rule["expr"]
    # Gated: sin offsite jamás configurado (ts == 0) la regla no arma.
    assert "> 0" in rule["expr"]
    assert rule["labels"]["severity"] in {"warning", "critical"}
