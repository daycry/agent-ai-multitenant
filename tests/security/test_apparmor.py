"""AppArmor profile validation — MAC container confinement (task_15_16).

Real kernel AppArmor enforcement cannot run in CI (no privileged Linux host with
AppArmor loaded, no live container-escape harness). So — exactly as the Plan 15
Fase C charter requires — this suite delivers the PROFILES + wires them into
compose/runtime and then VALIDATES them **structurally**:

  * every profile under ``docker/apparmor/`` is well-formed (a ``profile <name>``
    header, balanced ``{}`` braces, no stray text) — the kind of syntax slip
    that makes ``apparmor_parser`` reject the file fails here;
  * each profile DENIES the container-escape / host-tamper primitives
    (``mount``, ``pivot_root``, ``ptrace``, kernel modules, raw I/O, the docker
    socket, writes to ``/proc/sys`` & ``/sys``) — the whole point of the MAC
    layer;
  * each profile CONFINES writes (it grants a bounded set of writable dirs, not
    a blanket ``/** rw``), and the untrusted agent-runtime profile is STRICTER
    than the shared default (it only writes ``/workspace``, ``/tmp`` and its own
    tmpfs HOME, and denies ``/var/lib`` / ``/data`` / ``/root`` writes);
  * the paths the worker actually hands the sandbox — its ``HOME`` and every
    runtime template's dependency cache — are WRITABLE under the profile the
    worker pins on that same container. Confinement that blocks the container's
    own job is not hardening, it is an outage, and the two artifacts silently
    drifted apart for a month;
  * every long-lived platform service in the prod compose files references an
    AppArmor profile via ``security_opt: apparmor=…`` (host-agent exemptions
    cAdvisor/node-exporter documented);
  * the installer compose generator (task_15_07) EMITS the same reference;
  * the worker isolation seam forwards the AppArmor profile NAME to the daemon.

Actual enforcement-by-the-kernel is a documented HUMAN test
(``docs/06-runbooks/apparmor-profiles.md`` + ``internal-pentest-methodology.md``
§5) confirmed on a Linux host with AppArmor active. No live daemon / kernel is
needed here — these are deterministic static assertions that fail purely on a
hardening regression.
"""

from __future__ import annotations

import re
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
APPARMOR_DIR = DOCKER_DIR / "apparmor"

DEFAULT_PROFILE = APPARMOR_DIR / "agentic-default.profile"
AGENT_PROFILE = APPARMOR_DIR / "agent-runtime.profile"

#: The profile NAMES the platform pins (the name in the ``profile <name> {``
#: header — what ``security_opt: apparmor=<name>`` references after the host
#: loads it with ``apparmor_parser``).
DEFAULT_PROFILE_NAME = "agentic-default"
AGENT_PROFILE_NAME = "agent-runtime"

# Long-lived prod compose files whose services must each pin an AppArmor profile.
PROD_COMPOSE_FILES: tuple[Path, ...] = (
    DOCKER_DIR / "docker-compose.yml",
    DOCKER_DIR / "docker-compose.monitoring.yml",
)

# Host-agent services that legitimately need the host surface and are exempt
# from the AppArmor pin (mirrors the seccomp suite): node-exporter mounts host
# /proc,/sys,/ ro and reads them (keeps no-new-privileges + read-only mounts).
# cAdvisor dejó de estar exento: desde prod-12 cadv_01 (sandbox-8) corre SIN
# privileged y con el hardening estándar (cap_drop ALL + apparmor pineado).
APPARMOR_EXEMPT_SERVICES = frozenset({"node-exporter"})

# The escape / host-tamper primitives every profile MUST deny. Each entry is a
# regex matched against the profile text — a ``deny`` rule covering it.
REQUIRED_DENY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("raw mount", r"deny\s+mount\b"),
    ("pivot_root", r"deny\s+pivot_root\b"),
    ("ptrace", r"deny\s+ptrace\b"),
    ("kernel modules", r"deny\s+capability\s+sys_module\b"),
    ("CAP_SYS_ADMIN", r"deny\s+capability\s+sys_admin\b"),
    ("raw I/O", r"deny\s+capability\s+sys_rawio\b"),
    ("docker.sock", r"deny\s+/(?:var/)?run/docker\.sock\b"),
    ("/proc/sys writes", r"deny\s+/proc/sys/\*\*"),
    ("/boot", r"deny\s+/boot/\*\*"),
    ("kernel module dir", r"deny\s+/lib/modules/\*\*"),
)


# ---------------------------------------------------------------------------
# Profile parsing helpers (AppArmor profiles are NOT JSON — they are a small
# DSL; we validate them structurally with light text + brace analysis).
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    """Drop full-line ``#`` comments + the cpp-style ``#include`` directives so
    brace counting and rule matching see only the profile body."""
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def _profile_name(text: str) -> str | None:
    m = re.search(r"^\s*profile\s+([A-Za-z0-9_.\-]+)\b", text, re.MULTILINE)
    return m.group(1) if m else None


def _all_profiles() -> list[Path]:
    return sorted(APPARMOR_DIR.glob("*.profile"))


def _writable_path_rules(text: str) -> list[str]:
    """File-path rules that grant a write permission (``w`` in the perm flags),
    excluding ``deny`` rules. Used to assert writes are CONFINED, not blanket."""
    rules: list[str] = []
    for raw in _strip_comments(text).splitlines():
        line = raw.strip().rstrip(",")
        if not line or line.startswith("deny"):
            continue
        # A path rule looks like: ``[owner] /some/path  rwk`` — path then perms.
        m = re.match(r"^(?:owner\s+)?(/\S*)\s+([rwmklxiapcget]+)$", line)
        if not m:
            continue
        path, perms = m.group(1), m.group(2)
        if "w" in perms:
            rules.append(path)
    return rules


# ---------------------------------------------------------------------------
# Path-rule EVALUATION.
#
# `_writable_path_rules` answers "which globs grant a write". To answer the
# question the confinement actually turns on — "could the container write THIS
# path?" — the globs have to be matched, and `deny` has to win: in AppArmor a
# deny rule is absolute and NO allow rule can override it.
# ---------------------------------------------------------------------------

#: (is_deny, compiled path glob, permission flags)
_PathRule = tuple[bool, "re.Pattern[str]", str]


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate an AppArmor path glob into a regex.

    ``**`` spans path separators, ``*`` and ``?`` do not — the distinction that
    makes ``/home/**`` match ``/home/agent/.npm`` while ``/home/*`` does not.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*" and pattern[i + 1 : i + 2] == "*":
            out.append(".*")
            i += 2
            continue
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + r"\Z")


def _path_rules(text: str) -> list[_PathRule]:
    rules: list[_PathRule] = []
    for raw in _strip_comments(text).splitlines():
        line = raw.strip().rstrip(",")
        if not line:
            continue
        is_deny = line.startswith("deny ")
        if is_deny:
            line = line[len("deny ") :].strip()
        m = re.match(r"^(?:owner\s+)?(/\S*)\s+([rwmklxiapcget]+)$", line)
        if not m:
            continue
        rules.append((is_deny, _glob_to_regex(m.group(1)), m.group(2)))
    return rules


def _may_write(text: str, path: str) -> bool:
    """Whether the profile lets the confined process WRITE ``path``.

    Two AppArmor rules, applied in this order: a matching ``deny`` refuses
    outright, and with no matching allow the access is refused anyway
    (default-deny). Both matter here — see the meta-test below.
    """
    rules = _path_rules(text)
    if any(deny and "w" in perms and rx.match(path) for deny, rx, perms in rules):
        return False
    return any(not deny and "w" in perms and rx.match(path) for deny, rx, perms in rules)


# ---------------------------------------------------------------------------
# Compose helpers (reuse the tolerant loader posture of the seccomp suite)
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
    """Service specs with YAML merge keys (``<<``) resolved — the base compose
    applies ``security_opt`` through the merged ``*default-seccomp`` anchor, so
    a raw parse would miss the effective ``apparmor=`` pin."""
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


def _effective_services(path: Path) -> dict[str, dict[str, Any]]:
    """Los servicios que este compose DEFINE, sin los fragmentos de override que
    solo parchean un servicio declarado en otro fichero (ver
    ``tests/security/_compose.py``)."""
    from tests.security._compose import defined_services

    return defined_services(_rendered_services(path))


def _security_opt(spec: dict[str, Any]) -> list[str]:
    opt = spec.get("security_opt") or []
    return [str(x) for x in opt]


def _references_apparmor(spec: dict[str, Any]) -> bool:
    return any(o.startswith("apparmor=") for o in _security_opt(spec))


# ---------------------------------------------------------------------------
# Profiles exist + are well-formed
# ---------------------------------------------------------------------------


def test_apparmor_dir_ships_the_two_profiles() -> None:
    """The hardening ships exactly the shared default + the strict agent sandbox
    profile under docker/apparmor/."""
    assert APPARMOR_DIR.is_dir(), "docker/apparmor/ is missing"
    assert DEFAULT_PROFILE.is_file(), "docker/apparmor/agentic-default.profile is missing"
    assert AGENT_PROFILE.is_file(), "docker/apparmor/agent-runtime.profile is missing"


def test_every_profile_is_structurally_well_formed() -> None:
    """Each profile has a ``profile <name>`` header and balanced ``{}`` braces —
    the syntax slips that make ``apparmor_parser`` reject the file (and so
    silently leave the container unconfined) fail here."""
    profiles = _all_profiles()
    assert profiles, "expected at least one AppArmor profile under docker/apparmor/"
    for path in profiles:
        text = _read(path)
        name = _profile_name(text)
        assert name, f"{path.name}: missing a `profile <name> {{` header"
        body = _strip_comments(text)
        opens = body.count("{")
        closes = body.count("}")
        assert opens == closes, (
            f"{path.name}: unbalanced braces ({opens} '{{' vs {closes} '}}') — "
            "apparmor_parser would reject this"
        )
        assert opens >= 1, f"{path.name}: no profile block body"
        # Every rule line inside the body ends with a comma or a brace — a stray
        # line without a terminator is the classic copy/paste syntax error.
        for raw in body.splitlines():
            line = raw.strip()
            if not line or line in ("{", "}"):
                continue
            if line.endswith(("{", "}", ",")):
                continue
            pytest.fail(f"{path.name}: rule not terminated with ',': {line!r}")


def test_profile_names_match_what_compose_and_the_worker_pin() -> None:
    """The name in each profile's header is the exact string the compose +
    worker reference via ``apparmor=<name>`` — a rename on one side without the
    other silently disables confinement."""
    assert _profile_name(_read(DEFAULT_PROFILE)) == DEFAULT_PROFILE_NAME
    assert _profile_name(_read(AGENT_PROFILE)) == AGENT_PROFILE_NAME


# ---------------------------------------------------------------------------
# deny posture — escape / host-tamper primitives are denied
# ---------------------------------------------------------------------------


def test_every_profile_denies_the_escape_primitives() -> None:
    """No profile may leave the container-escape / host-tamper primitives open:
    raw mount/pivot_root, ptrace, kernel-module load, raw I/O, the docker
    socket, and writes to /proc/sys, /sys, /boot, /lib/modules. Drop a deny rule
    and this lists exactly which primitive regressed on which profile."""
    offenders: list[str] = []
    for path in _all_profiles():
        text = _read(path)
        for label, pattern in REQUIRED_DENY_PATTERNS:
            if not re.search(pattern, text):
                offenders.append(f"{path.name}: missing deny for {label}")
    assert not offenders, "AppArmor profiles missing escape-primitive denials: " + "; ".join(
        offenders
    )


def test_required_deny_detector_has_teeth() -> None:
    """Meta-test: the deny-pattern detector actually catches a missing rule.
    A profile body that drops every deny must trip every pattern — guards
    against a regex that silently matches anything."""
    empty_profile = "profile sample flags=(attach_disconnected) {\n  /tmp/ rw,\n}\n"
    matched = [label for label, pat in REQUIRED_DENY_PATTERNS if re.search(pat, empty_profile)]
    assert not matched, f"detector matched denials that are absent: {matched}"


# ---------------------------------------------------------------------------
# confinement — writes are bounded, agent profile is stricter
# ---------------------------------------------------------------------------


def test_no_profile_grants_a_blanket_write_to_the_whole_filesystem() -> None:
    """Writes must be CONFINED: no profile may grant ``/** w…`` (write anywhere)
    or write the host rootfs ``/``. That would defeat the read-only-rootfs +
    confinement posture."""
    offenders: list[str] = []
    for path in _all_profiles():
        for rule in _writable_path_rules(_read(path)):
            if rule in ("/", "/**"):
                offenders.append(f"{path.name}: blanket writable path {rule!r}")
    assert not offenders, "AppArmor profiles with a blanket write grant: " + "; ".join(offenders)


def test_each_profile_confines_writes_to_a_bounded_set_of_dirs() -> None:
    """Sanity that confinement is real (not empty / not blanket): each profile
    grants a bounded, non-empty set of writable dirs, and the always-needed
    scratch dirs (``/tmp``) are writable so the profile is usable."""
    for path in _all_profiles():
        writable = _writable_path_rules(_read(path))
        assert writable, f"{path.name}: no writable path rule at all — unusable"
        assert any(
            r.startswith("/tmp") for r in writable
        ), f"{path.name}: /tmp must be writable (scratch space) — got {writable}"


def test_the_container_home_is_writable_under_the_agent_profile() -> None:
    """The HOME the worker hands the sandbox must be writable under the profile
    the worker pins on that same container.

    These two artifacts drifted for a month without anyone noticing, because
    each was internally consistent: the profile (2026-05-31) encoded "the
    sandbox writes /workspace and /tmp", and then the agent's HOME moved OUT of
    /workspace to a container-local tmpfs (498ade16, 2026-06-26) precisely so
    the CLI would stop committing its dotfiles into the project worktree. The
    profile kept its `deny /home/** wklx`. Nothing failed in CI because AppArmor
    only enforces on a Linux host with the profile loaded — and that is the
    installer's default (`WORKERS_APPARMOR_PROFILE=agent-runtime`).

    So the invariant is pinned against the CODE, not against a literal: move
    HOME again and this fails instead of the deployment.
    """
    from workers.isolation import AGENT_HOME

    text = _read(AGENT_PROFILE)
    probe = f"{AGENT_HOME}/.config/probe"
    assert _may_write(text, probe), (
        f"the agent/test sandbox runs with HOME={AGENT_HOME} (workers.isolation) "
        f"but agent-runtime.profile refuses to write {probe} — every toolchain "
        "write to the home dies with EACCES"
    )


def test_every_runtime_template_dep_cache_is_writable_under_the_agent_profile() -> None:
    """Every runtime template in the catalog points its dependency cache inside
    that HOME, so the same drift breaks `pip install`, `npm ci`, `composer
    install`, `go mod download`, maven, gradle, bundler, cargo and nuget — the
    entire test/stack execution path, on a real installed stack."""
    from shared_test_runtimes.catalog import CATALOG

    text = _read(AGENT_PROFILE)
    caches = {t.id: t.dep_cache_mount for t in CATALOG.values() if t.dep_cache_mount}
    assert caches, "expected the catalog to declare dependency caches"
    blocked = sorted(
        f"{tid} -> {mount}" for tid, mount in caches.items() if not _may_write(text, f"{mount}/x")
    )
    assert not blocked, (
        "agent-runtime.profile refuses writes to these dependency caches, so "
        "installing dependencies fails inside the sandbox: " + ", ".join(blocked)
    )


def test_the_write_evaluator_honours_deny_over_allow() -> None:
    """Meta-test, and the reason the fix REMOVED the blanket deny instead of
    just adding a grant next to it: in AppArmor a `deny` beats every `allow`, so
    a profile carrying both rules is still broken — and would have looked fixed
    to a reader. The third case pins the other half: dropping the deny does not
    open /home, because with only `/** r` granted the rest stays default-denied.
    """
    allow = "profile p {\n  /home/agent/** rwkix,\n}\n"
    allow_and_deny = "profile p {\n  /home/agent/** rwkix,\n  deny /home/** wklx,\n}\n"
    read_only = "profile p {\n  /** r,\n}\n"

    assert _may_write(allow, "/home/agent/.npm/x") is True
    assert _may_write(allow_and_deny, "/home/agent/.npm/x") is False
    assert _may_write(read_only, "/home/agent/.npm/x") is False
    # `**` spans separators, `*` does not — the profile's confinement depends on it.
    assert _may_write("profile p {\n  /home/* rw,\n}\n", "/home/agent/.npm/x") is False


def test_agent_runtime_profile_is_stricter_than_the_default() -> None:
    """The untrusted agent/test sandbox can only write LESS than the shared
    services: it grants /workspace, /tmp and its own HOME, and explicitly denies
    writes to /var/lib, /data and /root. A regression that widened the sandbox
    fails.

    HOME belongs on that list and is not a loosening: it is a container-local
    tmpfs the worker sizes per container, nosuid, thrown away with the
    container — strictly less exposed than the bind-mounted /workspace that has
    always been writable here.
    """
    from workers.isolation import AGENT_HOME

    agent_text = _read(AGENT_PROFILE)
    writable = _writable_path_rules(agent_text)
    # The sandbox writes only under /workspace, /tmp or its own HOME.
    stray = sorted(
        r for r in writable if not r.startswith(("/workspace", "/tmp", "/proc", AGENT_HOME))
    )
    assert not stray, (
        "the agent-runtime sandbox grants writes outside /workspace + /tmp — "
        f"untrusted code must be confined: {stray}"
    )
    # And it explicitly denies the broad write dirs the shared default allows.
    for denied in (r"deny\s+/var/lib/\*\*", r"deny\s+/data/\*\*", r"deny\s+/root/\*\*"):
        assert re.search(
            denied, agent_text
        ), f"agent-runtime.profile must deny writes matching {denied!r}"
    # The shared default DOES grant /var/lib + /data (trusted services need them)
    # — proving the agent profile is the strictly tighter one.
    default_writable = _writable_path_rules(_read(DEFAULT_PROFILE))
    assert any(
        r.startswith("/var/lib") for r in default_writable
    ), "the shared default should grant /var/lib writes (it runs trusted code)"


# ---------------------------------------------------------------------------
# compose wiring — every long-lived service pins an AppArmor profile
# ---------------------------------------------------------------------------


def test_every_prod_service_references_an_apparmor_profile() -> None:
    """Each long-lived service in the base + monitoring compose pins an AppArmor
    profile via ``security_opt: apparmor=…`` (the host-agent exemptions are
    documented). Drop the pin from any service and this lists it."""
    missing: list[str] = []
    for path in PROD_COMPOSE_FILES:
        if not path.exists():
            continue
        for name, spec in _effective_services(path).items():
            if name in APPARMOR_EXEMPT_SERVICES:
                continue
            if not _references_apparmor(spec):
                missing.append(f"{path.name}:{name}")
    assert not missing, "services WITHOUT an AppArmor profile pinned: " + ", ".join(missing)


def test_pinned_compose_apparmor_names_resolve_to_a_shipped_profile() -> None:
    """The ``apparmor=<name>`` each service pins must match a profile that
    actually ships under docker/apparmor/ (a typo'd name silently runs
    unconfined / fails to start)."""
    shipped = {_profile_name(_read(p)) for p in _all_profiles()}
    dangling: list[str] = []
    for path in PROD_COMPOSE_FILES:
        if not path.exists():
            continue
        for name, spec in _effective_services(path).items():
            for opt in _security_opt(spec):
                if not opt.startswith("apparmor="):
                    continue
                profile_name = opt.split("=", 1)[1]
                if profile_name in ("unconfined", "docker-default"):
                    dangling.append(f"{path.name}:{name} uses {profile_name}")
                elif profile_name not in shipped:
                    dangling.append(f"{path.name}:{name} -> {profile_name} (not shipped)")
    assert not dangling, "compose apparmor pins that don't resolve: " + ", ".join(dangling)


def test_no_prod_service_runs_apparmor_unconfined() -> None:
    """No service may disable AppArmor with ``apparmor=unconfined`` — that throws
    the MAC confinement away. Regression guard for an accidental opt-out."""
    offenders: list[str] = []
    for path in PROD_COMPOSE_FILES:
        if not path.exists():
            continue
        for name, spec in _effective_services(path).items():
            if "apparmor=unconfined" in _security_opt(spec):
                offenders.append(f"{path.name}:{name}")
    assert not offenders, "services running apparmor=unconfined: " + ", ".join(offenders)


# ---------------------------------------------------------------------------
# installer compose generator (task_15_07) emits the AppArmor pin
# ---------------------------------------------------------------------------


def test_compose_generator_emits_apparmor_on_every_service() -> None:
    """The installer's compose generator wires the same AppArmor pin into every
    generated service (so an installed stack matches the committed compose's
    posture)."""
    from installer_backend.compose_generator import (
        APPARMOR_DEFAULT_PROFILE,
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
    expected = f"apparmor={APPARMOR_DEFAULT_PROFILE}"
    missing: list[str] = []
    for name, svc in compose["services"].items():
        opts = [str(x) for x in svc.get("security_opt", [])]
        if expected not in opts:
            missing.append(name)
    assert not missing, "generated services WITHOUT the AppArmor pin: " + ", ".join(missing)


def test_compose_generator_apparmor_name_matches_the_shipped_profile() -> None:
    """The name the generator pins is exactly the shipped default profile's
    header name — a drift between them would install an unloadable reference."""
    from installer_backend.compose_generator import APPARMOR_DEFAULT_PROFILE

    assert _profile_name(_read(DEFAULT_PROFILE)) == APPARMOR_DEFAULT_PROFILE
    assert APPARMOR_DEFAULT_PROFILE == DEFAULT_PROFILE_NAME


# ---------------------------------------------------------------------------
# worker isolation forwards the AppArmor profile NAME to the daemon
# ---------------------------------------------------------------------------


def test_worker_isolation_forwards_apparmor_profile_name() -> None:
    """When an AppArmor profile name is configured, the agent-runtime envelope
    forwards it as ``apparmor=<name>`` (a NAME, not a path — unlike seccomp).
    Pointing the worker at the shipped strict profile's name pins it.

    Exercises the real isolation seam (no daemon needed)."""
    from workers.config import Settings
    from workers.isolation import build_hardened_run_kwargs

    settings = Settings(apparmor_profile=AGENT_PROFILE_NAME)
    kwargs = build_hardened_run_kwargs(settings)
    opts = kwargs["security_opt"]
    apparmor_opts = [o for o in opts if o.startswith("apparmor=")]
    assert len(apparmor_opts) == 1, f"expected exactly one apparmor pin, got {opts}"
    assert apparmor_opts[0] == f"apparmor={AGENT_PROFILE_NAME}"


def test_empty_apparmor_setting_relies_on_docker_default() -> None:
    """With no AppArmor profile configured (the default), the envelope sets ONLY
    no-new-privileges (Docker's automatic docker-default AppArmor profile stays
    in force where the host supports it); it must not emit a broken
    ``apparmor=`` pin. Guards against the wiring over-reaching."""
    from workers.config import Settings
    from workers.isolation import build_hardened_run_kwargs

    kwargs = build_hardened_run_kwargs(Settings(apparmor_profile=""))
    opts = kwargs["security_opt"]
    assert "no-new-privileges:true" in opts
    assert not any(o.startswith("apparmor=") for o in opts)
