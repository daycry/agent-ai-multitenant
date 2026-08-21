"""Vault bootstrap orchestration — install step ``bootstrap_vault`` (task_15_09).

Phase B fills the real generators the install orchestration (task 15_05's
``bootstrap_vault`` step) calls. This module owns the **Vault bootstrap**: the
ordered, idempotent sequence that turns a freshly-started, *sealed and
uninitialised* Vault into a usable secret store for the platform:

    1. init        — ``vault operator init`` → capture the Shamir *unseal keys*
                     + the initial *root token*. These are shown to the operator
                     EXACTLY ONCE (Plan 15 Decisiones Clave: no recovery) and are
                     NEVER persisted in plaintext nor logged.
    2. unseal      — apply ``key_threshold`` unseal-key shares to unseal the
                     vault (a sealed vault answers nothing).
    3. enable KV v2 — mount the versioned KV secrets engine at the platform
                     mount (``secret/``) so the services read/write versioned
                     secrets.
    4. write policies — the initial per-service READ policies, each scoped to
                     exactly the secret paths that service resolves at runtime
                     (mirroring how the app resolves secrets). No service gets a
                     capability it does not need.

Re-bootstrap detection
----------------------
Bootstrapping is idempotent at the *init* boundary: an already-initialised Vault
is detected (``is_initialized()``) and :func:`bootstrap_vault` refuses to
re-``init`` it — a second ``vault operator init`` would fail and, worse, could be
read as "rotate the root/unseal material", which is a destructive, no-recovery
action. On an already-initialised vault the bootstrap either no-ops (when not
sealed) or, given the operator's existing unseal keys, just unseals + reconciles
the KV mount/policies — it never re-inits.

The Vault client behind a seam
-----------------------------
The installer links NO ``hvac`` dependency. Every Vault call is expressed through
the :class:`VaultClient` Protocol (an ``hvac``-like surface: ``sys`` init/unseal/
seal-status + mount + policy writes). Tests inject :class:`FakeVaultClient`, an
in-memory fake that models init/unseal/mount/policy state, so the whole
orchestration is asserted with NO real Vault. The real binding (an ``hvac``
adapter) lands at install time and is exercised only by the plan's Tests Humanos.

Security
--------
The unseal keys + root token are high-entropy material returned ONLY inside the
:class:`VaultInitResult` / :class:`VaultBootstrapResult`, whose ``__repr__`` /
``__str__`` are redacted so a stray log line or traceback frame cannot leak them.
Nothing in this module writes them to disk or logs them; the one-time hand-off to
the operator is the finalize step's reveal (task 15_06). The policy *documents*
(HCL) carry NO secret — only path + capability grants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Platform KV layout. The platform mounts a single KV v2 engine and stores each
# service's secrets under a stable, per-domain path. The per-service read
# policies below grant read on exactly the paths a service resolves at runtime.
# ---------------------------------------------------------------------------
#: The KV v2 mount point for all platform secrets. KV v2 nests the actual data
#: under ``<mount>/data/<path>`` and the metadata under ``<mount>/metadata/
#: <path>``; the policies grant on the ``data`` (and ``metadata`` for list)
#: sub-paths accordingly.
PLATFORM_KV_MOUNT = "secret"

#: KV v2 (``options.version = 2``) — versioned secrets so a rotation keeps the
#: prior version and the services can pin/roll back.
_KV_VERSION = "2"

#: Logical secret domains under the mount. Each is a KV v2 path holding the
#: secrets that one concern owns; the per-service policies grant read on the
#: subset a service actually consumes.
SECRET_PATH_DATABASE = "platform/database"  # DB DSNs / role passwords
SECRET_PATH_MINIO = "platform/minio"  # object-store access/secret key
SECRET_PATH_JWT = "platform/jwt"  # JWT signing + review-url signing
SECRET_PATH_ENCRYPTION = "platform/encryption"  # SSO / notification / webhook keys
SECRET_PATH_LLM = "platform/llm-providers"  # ADR-0021 provider credentials


@dataclass(frozen=True)
class PolicyRule:
    """A single ``path "<…>" { capabilities = [...] }`` grant in a Vault policy.

    ``path`` is a KV v2 *logical* secret path (e.g. ``platform/database``); the
    renderer expands it to the engine's ``<mount>/data/<path>`` (and, for a list
    capability, ``<mount>/metadata/<path>``). ``capabilities`` is the Vault
    capability set — for a read-only service that is ``("read",)``.
    """

    path: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class VaultPolicy:
    """A named Vault ACL policy: a service identity + its path/capability rules.

    ``name`` is the policy/role name (e.g. ``api-server``). ``rules`` are the
    per-path grants. :meth:`render_hcl` serialises it to the HCL Vault stores.
    A policy document carries NO secret — only path + capability grants.
    """

    name: str
    rules: tuple[PolicyRule, ...]

    def render_hcl(self, *, mount: str = PLATFORM_KV_MOUNT) -> str:
        """Render this policy to Vault HCL against the KV v2 *mount*.

        Each rule grants on the engine's ``<mount>/data/<path>``; a rule that
        includes the ``list`` capability also grants on ``<mount>/metadata/
        <path>`` (KV v2 keeps listings under ``metadata``). The output is
        deterministic so the generated document is assertable.
        """

        blocks: list[str] = []
        for rule in self.rules:
            caps = ", ".join(f'"{c}"' for c in rule.capabilities)
            blocks.append(f'path "{mount}/data/{rule.path}" {{\n  capabilities = [{caps}]\n}}')
            if "list" in rule.capabilities:
                blocks.append(
                    f'path "{mount}/metadata/{rule.path}" {{\n  capabilities = ["list", "read"]\n}}'
                )
        return "\n\n".join(blocks) + "\n"


#: The initial per-service READ policies. Each service's grants mirror exactly
#: the secrets it resolves at runtime — least privilege, no service reads a path
#: it does not consume:
#:
#:   * api-server            — DB DSNs, MinIO, JWT/review-url signing, the SSO /
#:                             notification / incoming-webhook encryption keys,
#:                             and the LLM provider credentials.
#:   * workers               — DB (admin DSN, runs DDL via migrations role),
#:                             MinIO (backup bundles), LLM provider creds.
#:   * orchestrator          — DB only (it schedules; no secret-bearing I/O).
#:   * notification-dispatcher — DB + the notification encryption key (read side
#:                             of the write/read pair shared with api-server).
#:
#: All grants are ``read``-only: services consume secrets, they never write them
#: (writes happen here at bootstrap + during rotation, task 15_17).
_INITIAL_POLICIES: tuple[VaultPolicy, ...] = (
    VaultPolicy(
        name="api-server",
        rules=(
            PolicyRule(path=SECRET_PATH_DATABASE, capabilities=("read",)),
            PolicyRule(path=SECRET_PATH_MINIO, capabilities=("read",)),
            PolicyRule(path=SECRET_PATH_JWT, capabilities=("read",)),
            PolicyRule(path=SECRET_PATH_ENCRYPTION, capabilities=("read",)),
            PolicyRule(path=SECRET_PATH_LLM, capabilities=("read",)),
        ),
    ),
    VaultPolicy(
        name="workers",
        rules=(
            PolicyRule(path=SECRET_PATH_DATABASE, capabilities=("read",)),
            PolicyRule(path=SECRET_PATH_MINIO, capabilities=("read",)),
            PolicyRule(path=SECRET_PATH_LLM, capabilities=("read",)),
        ),
    ),
    VaultPolicy(
        name="orchestrator",
        rules=(PolicyRule(path=SECRET_PATH_DATABASE, capabilities=("read",)),),
    ),
    VaultPolicy(
        name="notification-dispatcher",
        rules=(
            PolicyRule(path=SECRET_PATH_DATABASE, capabilities=("read",)),
            PolicyRule(path=SECRET_PATH_ENCRYPTION, capabilities=("read",)),
        ),
    ),
)


def initial_policies() -> tuple[VaultPolicy, ...]:
    """The per-service read policies written at bootstrap (least-privilege)."""

    return _INITIAL_POLICIES


@dataclass(frozen=True)
class VaultInitResult:
    """The one-shot output of ``vault operator init`` — high-value, no recovery.

    ``unseal_keys`` are the Shamir key shares (``key_shares`` of them); a
    ``key_threshold`` subset unseals the vault. ``root_token`` is the initial
    root token. Both are shown to the operator EXACTLY ONCE and there is NO
    recovery — ``__repr__``/``__str__`` are redacted so they cannot leak into a
    log line or traceback.
    """

    unseal_keys: tuple[str, ...]
    root_token: str
    key_threshold: int

    def __repr__(self) -> str:  # pragma: no cover - trivial, security-load-bearing
        return "VaultInitResult(<redacted: shown once, no recovery, never logged>)"

    __str__ = __repr__


@dataclass(frozen=True)
class VaultBootstrapResult:
    """Outcome of :func:`bootstrap_vault`.

    ``init`` carries the unseal keys + root token when *this* run initialised the
    vault; it is ``None`` when the vault was already initialised (a re-bootstrap),
    because the init material was handed out on the first bootstrap and there is
    no recovery. ``already_initialized`` records the re-bootstrap detection.
    ``kv_enabled`` / ``policies_written`` record the reconciliation that ran
    regardless. ``__repr__`` is redacted so a stray log can't leak ``init``.
    """

    init: VaultInitResult | None
    already_initialized: bool
    kv_mount: str
    kv_enabled: bool
    policies_written: tuple[str, ...]

    def __repr__(self) -> str:  # pragma: no cover - trivial, security-load-bearing
        return (
            "VaultBootstrapResult(already_initialized="
            f"{self.already_initialized}, kv_mount={self.kv_mount!r}, "
            f"kv_enabled={self.kv_enabled}, policies_written={self.policies_written!r}, "
            "init=<redacted>)"
        )

    __str__ = __repr__


class VaultBootstrapError(Exception):
    """Raised when the Vault bootstrap cannot complete.

    The message is surfaced to the operator (and may become a
    :class:`installer_backend.install.StepExecutionError`), so it MUST NOT carry
    any secret — no unseal key, no root token.
    """


@runtime_checkable
class VaultClient(Protocol):
    """An ``hvac``-like surface for the bootstrap — the single injectable seam.

    Models only what the bootstrap needs: the seal/init status, ``operator
    init``, ``unseal``, enabling a secrets engine (KV v2) and writing ACL
    policies. The real binding is an ``hvac.Client`` adapter (Tests Humanos);
    tests inject :class:`FakeVaultClient`. Method names/shapes mirror ``hvac`` so
    the adapter is a thin pass-through.
    """

    def is_initialized(self) -> bool:
        """True iff the vault has already been ``operator init``-ed."""
        ...

    def is_sealed(self) -> bool:
        """True iff the vault is currently sealed (answers nothing useful)."""
        ...

    def initialize(self, *, secret_shares: int, secret_threshold: int) -> VaultInitResult:
        """``vault operator init`` — return the unseal keys + root token ONCE."""
        ...

    def submit_unseal_key(self, key: str) -> bool:
        """Submit one unseal-key share; return True once the vault is unsealed."""
        ...

    def list_mounts(self) -> dict[str, str]:
        """Map of ``<mount>/`` → engine type for the enabled secrets engines."""
        ...

    def enable_kv_v2(self, *, mount_point: str) -> None:
        """Enable a KV v2 secrets engine at *mount_point* (idempotent)."""
        ...

    def write_policy(self, *, name: str, policy_hcl: str) -> None:
        """Create/update the named ACL policy from its HCL document."""
        ...


def _ensure_kv_v2(client: VaultClient, *, mount: str) -> bool:
    """Enable the KV v2 engine at *mount* if absent. Return True iff enabled now.

    Idempotent: if the mount already exists as a KV v2 engine this is a no-op and
    returns False (nothing was newly enabled), so a re-bootstrap doesn't fail on
    an existing mount.
    """

    mounts = client.list_mounts()
    existing = mounts.get(f"{mount}/") or mounts.get(mount)
    if existing is not None:
        if existing != "kv":
            raise VaultBootstrapError(
                f"El mount '{mount}/' ya existe pero no es un motor KV "
                f"(es '{existing}'); revisa la instalación de Vault."
            )
        return False
    client.enable_kv_v2(mount_point=mount)
    return True


def _write_initial_policies(client: VaultClient, *, mount: str) -> tuple[str, ...]:
    """Write every per-service read policy. Return the names written, in order."""

    written: list[str] = []
    for policy in initial_policies():
        client.write_policy(name=policy.name, policy_hcl=policy.render_hcl(mount=mount))
        written.append(policy.name)
    return tuple(written)


def bootstrap_vault(
    client: VaultClient,
    *,
    key_shares: int = 5,
    key_threshold: int = 3,
    mount: str = PLATFORM_KV_MOUNT,
    existing_unseal_keys: tuple[str, ...] | None = None,
) -> VaultBootstrapResult:
    """Bootstrap Vault: init (once) + unseal + KV v2 + initial policies.

    Idempotent and safe to re-run:

    * **Fresh vault** (not initialised) — ``operator init`` captures the unseal
      keys + root token (returned in :class:`VaultInitResult` for the one-time
      reveal), then the *threshold* of those keys unseals it.
    * **Already-initialised vault** (a re-bootstrap) — :func:`bootstrap_vault`
      does NOT re-``init`` (no double-init; the original material is gone and
      there is no recovery). If the vault is sealed it unseals it using
      *existing_unseal_keys* (the operator's stored keys); if those are missing
      it raises :class:`VaultBootstrapError` rather than silently failing.

    In BOTH cases the KV v2 mount and the per-service policies are reconciled
    (enabled/written) idempotently, so a re-bootstrap converges the config.

    The unseal keys / root token are NEVER logged or persisted here — they live
    only in the returned result (redacted ``repr``) for the finalize reveal.
    """

    if key_threshold < 1 or key_threshold > key_shares:
        raise VaultBootstrapError(
            "El umbral de unseal debe estar entre 1 y el número de shares "
            f"({key_threshold} no es válido para {key_shares} shares)."
        )

    already = client.is_initialized()

    init: VaultInitResult | None = None
    if not already:
        init = client.initialize(secret_shares=key_shares, secret_threshold=key_threshold)
        _unseal_with(client, init.unseal_keys, threshold=init.key_threshold)
    elif client.is_sealed():
        if not existing_unseal_keys:
            raise VaultBootstrapError(
                "Vault ya está inicializado y sellado, pero no se han aportado "
                "las unseal keys existentes para desellarlo (no hay recuperación "
                "de las claves originales)."
            )
        _unseal_with(client, existing_unseal_keys, threshold=key_threshold)

    kv_enabled = _ensure_kv_v2(client, mount=mount)
    policies = _write_initial_policies(client, mount=mount)

    return VaultBootstrapResult(
        init=init,
        already_initialized=already,
        kv_mount=mount,
        kv_enabled=kv_enabled,
        policies_written=policies,
    )


def _unseal_with(client: VaultClient, keys: tuple[str, ...], *, threshold: int) -> None:
    """Apply unseal-key shares until the vault reports unsealed.

    Submits up to *threshold* shares (a vault unseals once the threshold is
    reached). Raises :class:`VaultBootstrapError` if the keys run out before the
    vault unseals. The keys are never logged.
    """

    if len(keys) < threshold:
        raise VaultBootstrapError(
            f"No hay suficientes unseal keys para alcanzar el umbral ({len(keys)} < {threshold})."
        )
    for key in keys[:threshold]:
        if client.submit_unseal_key(key):
            return
    if client.is_sealed():
        raise VaultBootstrapError("Vault sigue sellado tras aplicar el umbral de unseal keys.")


# ---------------------------------------------------------------------------
# In-memory fake Vault client — the test default. Models init/unseal/mount/
# policy state with NO real Vault. The real hvac adapter lands at install time
# and is exercised only by the plan's Tests Humanos.
# ---------------------------------------------------------------------------
@dataclass
class FakeVaultClient:
    """A deterministic in-memory :class:`VaultClient`.

    Starts uninitialised + sealed (a fresh vault). :meth:`initialize` mints a
    scripted set of unseal keys + a root token and records that init happened
    EXACTLY ONCE (a second init raises, mirroring real Vault — this backs the
    no-double-init assertion). :meth:`submit_unseal_key` decrements the sealed
    counter; the vault unseals once ``key_threshold`` valid shares are applied.
    Mounts + policies are recorded so tests assert the KV v2 mount and the
    written policy documents.

    ``initialized`` / ``sealed`` can be preset to model an already-initialised
    vault (re-bootstrap) without calling :meth:`initialize`.
    """

    initialized: bool = False
    sealed: bool = True
    key_shares: int = 5
    key_threshold: int = 3
    #: Scripted material handed out by initialize() (high-entropy in real life;
    #: scripted + obviously-fake here so a test can assert on it).
    scripted_unseal_keys: tuple[str, ...] = (
        "fake-unseal-key-1",
        "fake-unseal-key-2",
        "fake-unseal-key-3",
        "fake-unseal-key-4",
        "fake-unseal-key-5",
    )
    scripted_root_token: str = "fake-root-token"

    #: Recorded state for assertions.
    mounts: dict[str, str] = field(default_factory=dict)
    #: KV version recorded per mount on enable (asserts "v2", not v1).
    mount_kv_versions: dict[str, str] = field(default_factory=dict)
    policies: dict[str, str] = field(default_factory=dict)
    init_calls: int = 0
    _accepted_keys: set[str] = field(default_factory=set)
    #: The unseal keys this fake will accept (set at init; presettable for a
    #: re-bootstrap scenario via :meth:`preset_initialized`).
    valid_unseal_keys: tuple[str, ...] = ()

    def preset_initialized(self, *, sealed: bool, unseal_keys: tuple[str, ...]) -> None:
        """Model an already-initialised vault (for a re-bootstrap test)."""

        self.initialized = True
        self.sealed = sealed
        self.valid_unseal_keys = unseal_keys

    # -- VaultClient surface ------------------------------------------------
    def is_initialized(self) -> bool:
        return self.initialized

    def is_sealed(self) -> bool:
        return self.sealed

    def initialize(self, *, secret_shares: int, secret_threshold: int) -> VaultInitResult:
        if self.initialized:
            # Real Vault refuses a second init; mirror that (no double-init).
            raise VaultBootstrapError("Vault ya está inicializado; no se puede re-init.")
        self.init_calls += 1
        self.initialized = True
        self.key_shares = secret_shares
        self.key_threshold = secret_threshold
        keys = self.scripted_unseal_keys[:secret_shares]
        self.valid_unseal_keys = keys
        return VaultInitResult(
            unseal_keys=keys,
            root_token=self.scripted_root_token,
            key_threshold=secret_threshold,
        )

    def submit_unseal_key(self, key: str) -> bool:
        if not self.sealed:
            return True
        if key in self.valid_unseal_keys:
            self._accepted_keys.add(key)
        if len(self._accepted_keys) >= self.key_threshold:
            self.sealed = False
            self._accepted_keys.clear()
        return not self.sealed

    def list_mounts(self) -> dict[str, str]:
        return dict(self.mounts)

    def enable_kv_v2(self, *, mount_point: str) -> None:
        self.mounts[f"{mount_point}/"] = "kv"
        self.mount_kv_versions[f"{mount_point}/"] = _KV_VERSION

    def write_policy(self, *, name: str, policy_hcl: str) -> None:
        self.policies[name] = policy_hcl
