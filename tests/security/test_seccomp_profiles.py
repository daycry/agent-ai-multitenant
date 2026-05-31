"""Seccomp profile validation — per-container hardening (task_15_15).

Real kernel seccomp enforcement cannot run in CI (no privileged Linux host,
no live container escape harness). So — exactly as the Plan 15 Fase C charter
requires — this suite delivers the PROFILES + wires them into compose/runtime
and then VALIDATES the profiles **structurally**:

  * every profile under ``docker/seccomp/`` is valid JSON with a default-deny
    ``defaultAction`` (``SCMP_ACT_ERRNO`` — any syscall off the allowlist is
    rejected, the opposite of Docker's permissive built-in default);
  * the dangerous syscall family (``mount``, ``ptrace``, ``kexec_load``,
    ``bpf`` where not needed, ``init_module``, ``setns``, ``unshare`` …) is NOT
    on any allowlist — so default-deny rejects it;
  * the untrusted agent-runtime profile is a STRICT subset of the shared
    default (it can only do *less*);
  * every long-lived platform service in the prod compose files references a
    seccomp profile via ``security_opt`` (the host-agent exemptions
    cAdvisor/node-exporter are documented);
  * the installer compose generator (task_15_07) EMITS the same reference;
  * the worker isolation seam forwards the profile *content* to the daemon.

Actual enforcement-by-the-kernel is a documented HUMAN test
(``docs/06-runbooks/internal-pentest-methodology.md`` §5) confirmed on a Linux
host with seccomp active. No live daemon / kernel is needed here — these are
deterministic static assertions that fail purely on a hardening regression.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.security

# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKER_DIR = REPO_ROOT / "docker"
SECCOMP_DIR = DOCKER_DIR / "seccomp"

DEFAULT_PROFILE = SECCOMP_DIR / "default.json"
AGENT_PROFILE = SECCOMP_DIR / "agent-runtime.json"

# Long-lived prod compose files whose services must each pin a seccomp profile.
PROD_COMPOSE_FILES: tuple[Path, ...] = (
    DOCKER_DIR / "docker-compose.yml",
    DOCKER_DIR / "docker-compose.monitoring.yml",
)

# Host-agent / privileged services that legitimately need the host syscall
# surface and are exempt from the seccomp pin (mirrors the pentest suite's
# HOST_AGENT_SERVICES). cAdvisor runs privileged (privileged disables seccomp
# at the daemon anyway); node-exporter mounts host /proc,/sys,/ ro and needs
# broad syscalls to read them. Both keep no-new-privileges + read-only mounts.
SECCOMP_EXEMPT_SERVICES = frozenset({"cadvisor", "node-exporter"})

# Default-deny actions the daemon treats as "reject unless allowlisted". The
# profiles use SCMP_ACT_ERRNO; SCMP_ACT_KILL(_PROCESS)/TRAP are also acceptable.
DEFAULT_DENY_ACTIONS = frozenset(
    {"SCMP_ACT_ERRNO", "SCMP_ACT_KILL", "SCMP_ACT_KILL_PROCESS", "SCMP_ACT_TRAP"}
)

# The dangerous syscall family that must NEVER be allowlisted by a default-deny
# profile (a container that can call these escapes / escalates / tampers with
# the kernel). If any appears with action SCMP_ACT_ALLOW the profile is unsafe.
DANGEROUS_SYSCALLS = frozenset(
    {
        "mount",
        "umount",
        "umount2",
        "mount_setattr",
        "pivot_root",
        "chroot",
        "ptrace",
        "process_vm_readv",
        "process_vm_writev",
        "kexec_load",
        "kexec_file_load",
        "bpf",
        "init_module",
        "finit_module",
        "delete_module",
        "reboot",
        "swapon",
        "swapoff",
        "setns",
        "unshare",
        "keyctl",
        "add_key",
        "request_key",
        "open_by_handle_at",
        "name_to_handle_at",
        "perf_event_open",
        "acct",
        "settimeofday",
        "clock_settime",
        "stime",
        "nfsservctl",
        "quotactl",
        "vhangup",
    }
)


# ---------------------------------------------------------------------------
# Profile parsing helpers
# ---------------------------------------------------------------------------


def _load_profile(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    assert isinstance(data, dict), f"{path.name} is not a JSON object"
    return data


def _allowed_syscalls(profile: dict[str, Any]) -> set[str]:
    """Every syscall name explicitly granted SCMP_ACT_ALLOW in the profile."""
    allowed: set[str] = set()
    for rule in profile.get("syscalls", []):
        if not isinstance(rule, dict):
            continue
        if rule.get("action") != "SCMP_ACT_ALLOW":
            continue
        for name in rule.get("names", []):
            allowed.add(str(name))
        single = rule.get("name")
        if single:
            allowed.add(str(single))
    return allowed


def _all_profiles() -> list[Path]:
    return sorted(SECCOMP_DIR.glob("*.json"))


# ---------------------------------------------------------------------------
# Compose helpers (reuse the same tolerant loader posture as the pentest suite)
# ---------------------------------------------------------------------------


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that tolerates Compose's custom merge tags."""


def _passthrough(loader: yaml.Loader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


for _tag in ("!reset", "!override"):
    _ComposeLoader.add_constructor(_tag, _passthrough)


def _rendered_services(path: Path) -> dict[str, dict[str, Any]]:
    """Service specs with YAML merge keys (``<<``) resolved.

    The base compose applies ``security_opt`` through a merged anchor
    (``*default-seccomp``), so we must expand ``<<`` to see the effective
    ``security_opt`` on each service — a raw parse would miss it.
    """
    with path.open(encoding="utf-8") as fh:
        data = yaml.load(fh, Loader=_ComposeLoader)
    assert isinstance(data, dict)
    raw = data.get("services", {})
    assert isinstance(raw, dict)
    out: dict[str, dict[str, Any]] = {}
    for name, raw_spec in raw.items():
        spec = raw_spec or {}
        merged: dict[str, Any] = {}
        merge = spec.get("<<")
        if isinstance(merge, dict):
            merged.update(merge)
        elif isinstance(merge, list):
            for part in merge:
                if isinstance(part, dict):
                    merged.update(part)
        merged.update({k: v for k, v in spec.items() if k != "<<"})
        out[name] = merged
    return out


def _security_opt(spec: dict[str, Any]) -> list[str]:
    opt = spec.get("security_opt") or []
    return [str(x) for x in opt]


def _references_seccomp(spec: dict[str, Any]) -> bool:
    return any(o.startswith("seccomp=") for o in _security_opt(spec))


# ---------------------------------------------------------------------------
# Profiles exist + are valid JSON
# ---------------------------------------------------------------------------


def test_seccomp_dir_ships_the_two_profiles() -> None:
    """The hardening ships exactly the shared default + the strict agent
    sandbox profile under docker/seccomp/."""
    assert SECCOMP_DIR.is_dir(), "docker/seccomp/ is missing"
    assert DEFAULT_PROFILE.is_file(), "docker/seccomp/default.json is missing"
    assert AGENT_PROFILE.is_file(), "docker/seccomp/agent-runtime.json is missing"


def test_every_profile_is_valid_json() -> None:
    """Each profile parses as JSON — a syntax slip (the kind that silently
    disables the profile or breaks `docker run`) fails here."""
    profiles = _all_profiles()
    assert profiles, "expected at least one seccomp profile under docker/seccomp/"
    for path in profiles:
        # _load_profile raises json.JSONDecodeError on a malformed file.
        _load_profile(path)


# ---------------------------------------------------------------------------
# default-deny posture
# ---------------------------------------------------------------------------


def test_every_profile_has_default_deny_action() -> None:
    """``defaultAction`` must be a deny action (SCMP_ACT_ERRNO) so any syscall
    NOT on the allowlist is rejected. A profile defaulting to SCMP_ACT_ALLOW
    would be a permissive allowlist-of-denials — the exact inversion the audit
    forbids."""
    for path in _all_profiles():
        profile = _load_profile(path)
        action = profile.get("defaultAction")
        assert action in DEFAULT_DENY_ACTIONS, (
            f"{path.name}: defaultAction={action!r} is not default-deny "
            f"(expected one of {sorted(DEFAULT_DENY_ACTIONS)})"
        )


def test_no_profile_allowlists_a_dangerous_syscall() -> None:
    """No default-deny profile may explicitly ALLOW a syscall from the
    escape/escalate/kernel-tamper family (mount, ptrace, kexec_load, bpf,
    setns, unshare, …). Those stay denied-by-default — if one is added to an
    allowlist this lists it."""
    offenders: list[str] = []
    for path in _all_profiles():
        allowed = _allowed_syscalls(_load_profile(path))
        leaked = sorted(allowed & DANGEROUS_SYSCALLS)
        if leaked:
            offenders.append(f"{path.name}: {', '.join(leaked)}")
    assert not offenders, "seccomp profiles allowlisting dangerous syscalls: " + "; ".join(
        offenders
    )


def test_profiles_allowlist_the_baseline_a_service_actually_needs() -> None:
    """Sanity that the allowlist is real (not empty / not a typo-ed action):
    the syscalls a network service genuinely needs ARE present, so the profile
    is usable, not just safe."""
    needed = {"read", "write", "openat", "close", "mmap", "futex", "socket", "connect"}
    for path in _all_profiles():
        allowed = _allowed_syscalls(_load_profile(path))
        missing = sorted(needed - allowed)
        assert not missing, f"{path.name}: missing baseline syscalls {missing}"


# ---------------------------------------------------------------------------
# the agent sandbox profile is STRICTER than the shared default
# ---------------------------------------------------------------------------


def test_agent_runtime_profile_is_a_strict_subset_of_default() -> None:
    """The untrusted agent/test sandbox can only do *less* than the shared
    services: its allowlist is a subset of the default profile's, and it is
    strictly smaller (it drops at least the xattr / module-adjacent extras).
    A regression that widened the sandbox above the shared baseline fails."""
    default_allowed = _allowed_syscalls(_load_profile(DEFAULT_PROFILE))
    agent_allowed = _allowed_syscalls(_load_profile(AGENT_PROFILE))
    extra = sorted(agent_allowed - default_allowed)
    assert not extra, (
        "the agent-runtime sandbox allowlists syscalls the shared default does "
        f"not — the untrusted profile must be a SUBSET: {extra}"
    )
    assert len(agent_allowed) < len(default_allowed), (
        "the agent-runtime profile must be strictly tighter than the shared "
        "default (it runs hostile code)"
    )


# ---------------------------------------------------------------------------
# compose wiring — every long-lived service pins a seccomp profile
# ---------------------------------------------------------------------------


def test_every_prod_service_references_a_seccomp_profile() -> None:
    """Each long-lived service in the base + monitoring compose pins a seccomp
    profile via ``security_opt: seccomp=…`` (the host-agent exemptions are
    documented). Drop the pin from any service and this lists it."""
    missing: list[str] = []
    for path in PROD_COMPOSE_FILES:
        if not path.exists():
            continue
        for name, spec in _rendered_services(path).items():
            if name in SECCOMP_EXEMPT_SERVICES:
                continue
            if not _references_seccomp(spec):
                missing.append(f"{path.name}:{name}")
    assert not missing, "services WITHOUT a seccomp profile pinned: " + ", ".join(missing)


def test_pinned_compose_profiles_point_at_files_that_exist() -> None:
    """The ``seccomp=<path>`` each service pins resolves to a profile file that
    actually ships (a dangling path silently disables enforcement)."""
    dangling: list[str] = []
    for path in PROD_COMPOSE_FILES:
        if not path.exists():
            continue
        for name, spec in _rendered_services(path).items():
            for opt in _security_opt(spec):
                if not opt.startswith("seccomp="):
                    continue
                rel = opt.split("=", 1)[1]
                if rel in ("unconfined", "runtime/default"):
                    dangling.append(f"{path.name}:{name} uses {rel}")
                    continue
                # Paths are relative to the compose directory (docker/).
                resolved = (DOCKER_DIR / rel).resolve()
                if not resolved.is_file():
                    dangling.append(f"{path.name}:{name} -> {rel} (missing)")
    assert not dangling, "compose seccomp pins that don't resolve: " + ", ".join(dangling)


def test_base_compose_never_sets_seccomp_unconfined() -> None:
    """No service may disable seccomp with ``seccomp=unconfined`` — that throws
    the whole profile away. Regression guard for an accidental opt-out."""
    offenders: list[str] = []
    for path in PROD_COMPOSE_FILES:
        if not path.exists():
            continue
        for name, spec in _rendered_services(path).items():
            if "seccomp=unconfined" in _security_opt(spec):
                offenders.append(f"{path.name}:{name}")
    assert not offenders, "services running seccomp=unconfined: " + ", ".join(offenders)


# ---------------------------------------------------------------------------
# installer compose generator (task_15_07) emits the seccomp pin
# ---------------------------------------------------------------------------


def test_compose_generator_emits_seccomp_on_every_service() -> None:
    """The installer's compose generator wires the same default-deny seccomp
    pin into every generated service (so an installed stack matches the
    committed compose's posture)."""
    from installer_backend.compose_generator import (
        SECCOMP_DEFAULT_PROFILE,
        generate_compose,
    )
    from installer_backend.config import (
        InstallerConfig,
        OllamaProvider,
        ProvidersConfig,
        ResourceConfig,
        StorageConfig,
        SystemConfig,
        TenantConfig,
    )

    cfg = InstallerConfig(
        system=SystemConfig(domain="agentic.example.com"),
        resources=ResourceConfig(gpu_enabled=True),
        storage=StorageConfig(
            data_root="/data/agent-platform",
            minio_access_key="throwaway-access",
            minio_secret_key="throwaway-secret-value-123",
        ),
        providers=ProvidersConfig(ollama=OllamaProvider(enabled=True, endpoint="http://o:11434")),
        tenant=TenantConfig(tenant_name="Acme", admin_email="admin@example.com"),
    )
    compose = generate_compose(cfg, monitoring=True)
    expected = f"seccomp={SECCOMP_DEFAULT_PROFILE}"
    missing: list[str] = []
    for name, svc in compose["services"].items():
        opts = [str(x) for x in svc.get("security_opt", [])]
        if expected not in opts:
            missing.append(name)
    assert not missing, "generated services WITHOUT the seccomp pin: " + ", ".join(missing)


# ---------------------------------------------------------------------------
# worker isolation forwards the profile CONTENT to the daemon
# ---------------------------------------------------------------------------


def test_worker_isolation_forwards_seccomp_profile_content(tmp_path: Path) -> None:
    """When a seccomp profile path is configured, the agent-runtime envelope
    forwards the profile *content* (``seccomp=<json>``), not the path — the
    daemon never needs the file. Pointing the worker at the shipped strict
    profile yields a default-deny envelope.

    This exercises the real isolation seam (no daemon needed): we feed it the
    committed agent-runtime.json and assert it lands as a content pin.
    """
    from workers.config import Settings
    from workers.isolation import build_hardened_run_kwargs

    settings = Settings(seccomp_profile_path=str(AGENT_PROFILE))
    kwargs = build_hardened_run_kwargs(settings)
    opts = kwargs["security_opt"]
    seccomp_opts = [o for o in opts if o.startswith("seccomp=")]
    assert len(seccomp_opts) == 1, f"expected exactly one seccomp pin, got {opts}"
    payload = seccomp_opts[0].split("=", 1)[1]
    # It is the JSON CONTENT, not the path.
    assert payload != str(AGENT_PROFILE)
    profile = json.loads(payload)
    assert profile["defaultAction"] in DEFAULT_DENY_ACTIONS
    # And the forwarded content has no dangerous syscall on its allowlist.
    assert not (_allowed_syscalls(profile) & DANGEROUS_SYSCALLS)


def test_empty_seccomp_setting_relies_on_docker_default() -> None:
    """With no profile configured (the default), the envelope sets ONLY
    no-new-privileges (Docker's built-in default-deny seccomp stays in force);
    it must not emit a broken ``seccomp=`` pin. Guards against the wiring
    over-reaching and breaking the default path."""
    from workers.config import Settings
    from workers.isolation import build_hardened_run_kwargs

    kwargs = build_hardened_run_kwargs(Settings(seccomp_profile_path=""))
    opts = kwargs["security_opt"]
    assert "no-new-privileges:true" in opts
    assert not any(o.startswith("seccomp=") for o in opts)
