"""Canonical compose hardening contract (Plan prod-01 task_08, finding deploy-12).

The generated compose applies ``installer_backend.compose_generator._hardening``
to every service: ``cap_drop: [ALL]`` (Vault excepted — it keeps ``IPC_LOCK``)
plus a ``deploy.resources.limits`` cpu/memory cap. This test pins the SAME
criterion on the hand-maintained ``docker/docker-compose.yml`` so the two files
don't drift into different security postures.

``yaml.safe_load`` resolves the ``<<:`` merge anchors, so we assert the EFFECTIVE
per-service config.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_COMPOSE = Path(__file__).resolve().parents[2] / "docker" / "docker-compose.yml"

# One-shot init container (``restart: "no"``): like the generator's
# ollama-bootstrap it carries no resource cap / cap-drop — it pulls a model and
# exits.
_ONE_SHOT = {"ollama-bootstrap"}


def _services() -> dict:
    data = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    return {k: v for k, v in data["services"].items() if isinstance(v, dict)}


def test_every_long_lived_service_has_resource_limits() -> None:
    for name, svc in _services().items():
        if name in _ONE_SHOT:
            continue
        limits = ((svc.get("deploy") or {}).get("resources") or {}).get("limits") or {}
        assert limits.get("cpus") and limits.get("memory"), (
            f"{name} has no deploy.resources.limits — a runaway container could "
            "starve the single host (deploy-12)"
        )


def test_services_drop_all_caps_with_vault_keeping_ipc_lock() -> None:
    for name, svc in _services().items():
        if name in _ONE_SHOT:
            continue
        if name == "vault":
            # Vault mlocks its memory → needs IPC_LOCK; it opts out of the
            # blanket cap-drop exactly like compose_generator._vault_service.
            assert "IPC_LOCK" in (svc.get("cap_add") or []), "vault must keep IPC_LOCK"
        else:
            assert svc.get("cap_drop") == ["ALL"], f"{name} must cap_drop: [ALL]"


def test_official_infra_images_keep_self_init_caps() -> None:
    """Parity with ``compose_generator._INFRA_CAPS``: official images that
    self-init as root (postgres/redis/clamav/egress-proxy chown their data dir +
    drop to a service user via gosu/su-exec) add the self-init caps back on top
    of ``cap_drop:[ALL]``; Vault also keeps SETFCAP (it setcaps its binary).
    Without these the recreated containers crash-loop ("chmod/chown: Operation
    not permitted", "Unable to change to group") — the prod-01 hardening
    regression this guards against. Verified live: the dev stack containers are
    healthy with exactly these caps."""
    infra_caps = {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"}
    services = _services()
    for name in ("postgres", "redis", "clamav"):
        assert set(services[name].get("cap_add") or []) >= infra_caps, name
    if "egress-proxy" in services:  # tinyproxy setgid/setuid on start
        assert set(services["egress-proxy"].get("cap_add") or []) >= infra_caps
    vault_caps = set(services["vault"].get("cap_add") or [])
    assert vault_caps >= infra_caps | {"IPC_LOCK", "SETFCAP"}
