"""Host-only seam bindings the real installer wires (Plan prod-01 task_19).

These touch the host (write files, ``mkdir``/``chmod``, talk to Vault via
``hvac``) so they are ``# pragma: no cover`` — never exercised by the unit suite
(which drives the orchestration via the in-memory fakes) and validated only by
the e2e / Tests Humanos on a real Linux host with Docker.

``build_hvac_vault_client`` is intentionally thin: it maps the
:class:`installer_backend.vault_bootstrap.VaultClient` Protocol onto ``hvac``.
The exact reachability of Vault from the host (a published port vs ``docker
compose exec``) is finalised with prod-10; the method mapping here is stable.
"""

from __future__ import annotations

import os
from pathlib import Path

from .config import InstallerConfig
from .config_generators import DataDir
from .vault_bootstrap import VaultClient, VaultInitResult


class RealEnvFileWriter:
    """Writes a generated file to disk with its POSIX mode (creating parents)."""

    def write(self, path: str, content: str, *, mode: int) -> None:  # pragma: no cover
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        target.chmod(mode)


class RealDataTreeProvisioner:
    """Creates each data-tree directory with its declared POSIX mode."""

    def provision(self, plan: list[DataDir]) -> None:  # pragma: no cover
        for entry in plan:
            path = Path(entry.path)
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(entry.mode)


class _HvacVaultClient:  # pragma: no cover - host-only adapter over hvac
    """Adapter mapping the :class:`VaultClient` Protocol onto an ``hvac.Client``."""

    def __init__(self, addr: str) -> None:
        import hvac

        self._hvac = hvac
        self._client = hvac.Client(url=addr)

    def is_initialized(self) -> bool:
        return bool(self._client.sys.is_initialized())

    def is_sealed(self) -> bool:
        return bool(self._client.sys.is_sealed())

    def initialize(self, *, secret_shares: int, secret_threshold: int) -> VaultInitResult:
        result = self._client.sys.initialize(
            secret_shares=secret_shares, secret_threshold=secret_threshold
        )
        keys = tuple(result.get("keys_base64") or result.get("keys") or ())
        root_token = str(result["root_token"])
        # Authenticate the client as root for the subsequent KV/policy writes.
        self._client.token = root_token
        return VaultInitResult(
            unseal_keys=keys, root_token=root_token, key_threshold=secret_threshold
        )

    def submit_unseal_key(self, key: str) -> bool:
        status = self._client.sys.submit_unseal_key(key)
        return not bool(status["sealed"])

    def list_mounts(self) -> dict[str, str]:
        mounts = self._client.sys.list_mounted_secrets_engines()
        data = mounts.get("data", mounts)
        return {name: str(info.get("type", "")) for name, info in data.items()}

    def enable_kv_v2(self, *, mount_point: str) -> None:
        self._client.sys.enable_secrets_engine(
            backend_type="kv", path=mount_point, options={"version": "2"}
        )

    def write_policy(self, *, name: str, policy_hcl: str) -> None:
        self._client.sys.create_or_update_policy(name=name, policy=policy_hcl)


def build_hvac_vault_client(cfg: InstallerConfig) -> VaultClient:  # pragma: no cover
    """Construct the real Vault client for the BOOTSTRAP_VAULT step.

    ``VAULT_ADDR`` overrides the address; defaults to the local listener. Imports
    ``hvac`` lazily so the installer package imports cleanly without it (the unit
    suite never builds a real client — it injects ``FakeVaultClient``).
    """

    _ = cfg  # reserved: per-deployment addressing is finalised with prod-10
    addr = os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200")
    return _HvacVaultClient(addr)
