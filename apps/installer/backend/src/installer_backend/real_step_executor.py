"""Real install StepExecutor — provisions the stack for real (Plan prod-01 task_16).

Implements the :class:`installer_backend.install.StepExecutor` Protocol with
injected seams (a :class:`CommandRunner` for ``docker compose``, the config
:class:`EnvFileWriter`/:class:`DataTreeProvisioner`, a Vault client factory), so
the orchestration is fully unit-testable without a Docker host. ``FakeStepExecutor``
stays for ``--dry-run`` and the existing suite.

Per step:
  * **GENERATE_CONFIG** — render + write ``docker-compose.yml`` (0640), ``.env``
    (0600, prod secret-guarded), ``config/global.yaml`` (0640) and the
    ``caddy/Caddyfile`` (0644, the compose bind-mounts it so it MUST exist before
    ``up``), then provision the ``/data`` tree.
  * **PULL_IMAGES** — ``docker compose pull``.
  * **START_STACK** — ``docker compose up -d --wait`` (Compose blocks until every
    service is healthy or fails — no hand-rolled polling).
  * **RUN_MIGRATIONS** — ``docker compose run --rm migrations`` (the one-shot).
  * **BOOTSTRAP_VAULT** — :func:`bootstrap_vault` orchestration ONLY (init → unseal
    → enable KV v2 → policies). Writing the secret VALUES into the KV + per-service
    token minting is prod-10's domain (ADR secrets-8) — deliberately NOT done here.
  * **SEED_TENANT** — ``init_tenant`` inside the api-server container; the admin
    password is generated with a CSPRNG and passed via an ``-e`` env PASS-THROUGH
    (never on the command line) and captured for the one-time reveal.
"""

from __future__ import annotations

import re
import secrets as _secrets
from collections.abc import Callable
from dataclasses import dataclass, field

from .command_runner import CommandRunner
from .compose_generator import PROJECT_NAME, generate_compose, render_compose_yaml
from .config import Environment, InstallerConfig
from .config_generators import (
    DataTreeProvisioner,
    EnvFileWriter,
    GeneratedSecrets,
    assert_env_passes_prod_secret_guard,
    build_data_tree_plan,
    generate_env_file,
    generate_global_config,
    render_global_yaml,
)
from .install import InstallStep, StepExecutionError
from .proxy_generator import generate_caddyfile
from .vault_bootstrap import (
    VaultBootstrapError,
    VaultBootstrapResult,
    VaultClient,
    bootstrap_vault,
)

#: ``Organization.slug`` is ``String(64)``; ``tenant_name`` allows up to 120
#: chars, so the slug MUST be capped or the SEED_TENANT INSERT fails late.
_MAX_SLUG_LEN = 64


def _slugify(name: str) -> str:
    """A conservative URL-safe slug for the tenant org (lowercase, dash-joined).

    Capped at 64 chars (the ``organizations.slug`` column width) so a long
    tenant name can't blow up the INSERT at the very last install step.
    """

    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return (slug[:_MAX_SLUG_LEN].rstrip("-")) or "tenant"


@dataclass
class RealStepExecutor:
    """The real :class:`StepExecutor` binding (see module docstring)."""

    compose_dir: str
    runner: CommandRunner
    env_writer: EnvFileWriter
    tree: DataTreeProvisioner
    vault_client_factory: Callable[[InstallerConfig], VaultClient]
    cfg: InstallerConfig
    secrets: GeneratedSecrets
    monitoring: bool = False

    #: Captured for the one-time credential reveal (read by RealCredentialBuilder).
    vault_bootstrap_result: VaultBootstrapResult | None = field(default=None, init=False)
    seeded_admin_password: str | None = field(default=None, init=False)

    @property
    def _compose_file(self) -> str:
        return f"{self.compose_dir}/docker-compose.yml"

    def _compose(self, *args: str) -> list[str]:
        return ["docker", "compose", "-p", PROJECT_NAME, "-f", self._compose_file, *args]

    def _run(
        self,
        args: list[str],
        lines: list[str],
        *,
        env: dict[str, str] | None = None,
    ) -> None:
        result = self.runner.run(args, cwd=self.compose_dir, env=env, on_line=lines.append)
        if result.returncode != 0:
            raise StepExecutionError(f"el comando falló (rc={result.returncode}): {' '.join(args)}")

    def execute(self, step: InstallStep, config: dict[str, object]) -> list[str]:  # noqa: ARG002
        lines: list[str] = []
        match step:
            case InstallStep.GENERATE_CONFIG:
                self._generate_config(lines)
            case InstallStep.PULL_IMAGES:
                self._run(self._compose("pull"), lines)
            case InstallStep.START_STACK:
                self._run(self._compose("up", "-d", "--wait"), lines)
            case InstallStep.RUN_MIGRATIONS:
                # The apps depend_on the one-shot `migrations` with
                # service_completed_successfully, so `up --wait` above already
                # applied them; this explicit step is the AUDITABLE migration
                # gate (matches the upgrade runbook) and is a safe idempotent
                # no-op here (env.py takes a pg_advisory_xact_lock).
                self._run(self._compose("run", "--rm", "migrations"), lines)
            case InstallStep.BOOTSTRAP_VAULT:
                self._bootstrap_vault(lines)
            case InstallStep.SEED_TENANT:
                self._seed_tenant(lines)
            case _:  # pragma: no cover - defensive; every step is handled above
                raise StepExecutionError(f"paso de instalación desconocido: {step}")
        return lines

    # -- steps --------------------------------------------------------------
    def _generate_config(self, lines: list[str]) -> None:
        prod = self.cfg.system.environment is Environment.PRODUCTION

        compose_yaml = render_compose_yaml(generate_compose(self.cfg, monitoring=self.monitoring))
        self.env_writer.write(self._compose_file, compose_yaml, mode=0o640)
        lines.append("Escrito docker-compose.yml")

        env_text = generate_env_file(self.cfg, self.secrets, monitoring=self.monitoring)
        if prod:
            try:
                assert_env_passes_prod_secret_guard(env_text)
            except ValueError as exc:
                raise StepExecutionError(str(exc)) from exc
        self.env_writer.write(f"{self.compose_dir}/.env", env_text, mode=0o600)
        lines.append("Escrito .env (0600)")

        global_yaml = render_global_yaml(
            generate_global_config(self.cfg, monitoring=self.monitoring)
        )
        self.env_writer.write(f"{self.compose_dir}/config/global.yaml", global_yaml, mode=0o640)
        lines.append("Escrito config/global.yaml")

        self.env_writer.write(
            f"{self.compose_dir}/caddy/Caddyfile", generate_caddyfile(self.cfg), mode=0o644
        )
        lines.append("Escrito caddy/Caddyfile")

        plan = build_data_tree_plan(self.cfg, monitoring=self.monitoring)
        self.tree.provision(plan)
        lines.append(f"Árbol de datos creado: {len(plan)} directorios")

    def _bootstrap_vault(self, lines: list[str]) -> None:
        client = self.vault_client_factory(self.cfg)
        try:
            result = bootstrap_vault(client)
        except VaultBootstrapError as exc:
            raise StepExecutionError(f"el bootstrap de Vault falló: {exc}") from exc
        self.vault_bootstrap_result = result
        lines.append("Vault inicializado" if result.init is not None else "Vault ya inicializado")
        lines.append(f"KV v2 habilitado: {result.kv_enabled}")
        lines.append(f"Políticas escritas: {len(result.policies_written)}")
        # NOTE: writing the secret VALUES into the KV (which secret → which path)
        # and minting per-service tokens is prod-10's domain (ADR secrets-8); here
        # we run ONLY the orchestration (init → unseal → enable KV → policies).

    def _seed_tenant(self, lines: list[str]) -> None:
        # 1. Platform tenant + the built-in catalog (agents, teams, tools, skills,
        #    KBs, project templates, marketplace). Without this a clean install
        #    boots an EMPTY system — no platform tenant, nothing to clone/use
        #    (review finding). Idempotent (every builtin seed upserts).
        self._run(
            self._compose("run", "--rm", "api-server", "python", "-m", "api_server.seeds"),
            lines,
        )
        lines.append("Catálogo built-in sembrado (platform tenant + agentes/equipos/tools)")

        # 2. The operator's initial tenant + admin user + membership.
        # CSPRNG admin password; passed by env PASS-THROUGH (never argv) to the
        # init_tenant entrypoint inside the api-server container.
        password = _secrets.token_urlsafe(18)
        slug = _slugify(self.cfg.tenant.tenant_name)
        args = self._compose(
            "run",
            "--rm",
            "-e",
            "INIT_ADMIN_PASSWORD",  # pass-through (value comes from the env below)
            "api-server",
            "python",
            "-m",
            "api_server.seeds.init_tenant",
            "--tenant-name",
            self.cfg.tenant.tenant_name,
            "--slug",
            slug,
            "--admin-email",
            str(self.cfg.tenant.admin_email),
        )
        self._run(args, lines, env={"INIT_ADMIN_PASSWORD": password})
        self.seeded_admin_password = password
        lines.append("Tenant inicial creado")
        lines.append("Usuario admin creado")
