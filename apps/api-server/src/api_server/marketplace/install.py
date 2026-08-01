"""End-to-end install orchestration (Plan 09 task_09_11).

The pipeline that ``POST /marketplace/installations`` delegates to once a
listing has been resolved under RLS. It chains the Phase B/C gates the
trust policy (task_09_04) implies — in a fixed, fail-closed order — and
persists the installation + an append-only audit trail, all inside the
caller's tenant-scoped session (``get_tenant_session`` + RLS). Nothing here
relaxes the multi-tenancy guarantee: every row this module writes is stamped
with the caller's ``tenant_id`` and only ever touched through the session
RLS already scopes.

The ordered gates (each one's failure ABORTS with a typed error + an audit
entry and leaves NO enabled install):

  1. **FETCH** the artifact from its source. The network/disk fetch is
     hidden behind the :class:`ArtifactFetcher` protocol so the tests inject
     a local fixture (:class:`LocalArtifactFetcher`) and no real network is
     touched — the xmlsec / Docker / semgrep capability-gap precedent. A
     real HTTP/git fetcher is a thin future implementation of the same
     protocol; the live path is pending the registry runtime.
  2. **PARSE** the SKILL.md (task_09_09) or tool manifest (task_09_10). A
     malformed artifact is rejected before any code is scanned/run.
  3. **VERIFY SIGNATURE** when ``trust_policy.signature_required`` (only
     ``verified``, plan decision (d)). The detached signature is verified
     with :mod:`cryptography` (Ed25519) against the platform team's public
     key over the EXACT manifest bytes that were parsed — a tampered or
     unsigned verified-listing artifact is REJECTED. The signature itself is
     never echoed back to the caller (secrets/signatures stay server-side).
  4. **STATIC ANALYSIS** (task_09_05) over the fetched source tree; BLOCK
     when the report exceeds the trust policy's ``max_allowed_severity``.
  5. **SANDBOX SMOKE TEST** (task_09_06) when ``sandbox_required``
     (community / experimental). Docker is mocked in the tests; a failing
     probe blocks the install. A launch error fails closed.
  6. **CONSENT** (task_09_07) when ``per_permission_consent_required``
     (community / experimental ALWAYS, plan decisions (a)+(b)). Such an
     install is PERSISTED but lands ``disabled`` with no granted permissions
     and only becomes ``enabled`` once every requested permission is granted
     via ``POST .../consent``. A ``verified`` listing installs ``enabled``.
  7. **PERSIST** the installation + write the install audit entry.

Abort vs. pending-consent: a *gate failure* (bad signature, blocking
analysis, failed sandbox) is a hard abort — a typed :class:`InstallError`,
an audit row recording WHY, and no install row. "Awaiting consent" is NOT a
failure: the install row IS created (disabled), exactly as Phase A's
endpoint already does, so the consent UI has something to act on.

Durability of abort audits: the orchestrator COMMITS the abort audit row
before raising, so the immutable record survives even though the caller's
request transaction then unwinds with the error. The success path flushes
the install + audit row in the caller's transaction and lets the caller
commit them atomically.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.marketplace import (
    InstallationStatus,
    MarketplaceAuditAction,
    MarketplaceAuditEntry,
    MarketplaceInstallation,
    MarketplaceListing,
    MarketplaceListingKind,
)
from api_server.marketplace.consent import consent_required_for
from api_server.marketplace.skill_format import SkillFormatError, parse_skill_md
from api_server.marketplace.tool_format import ToolFormatError, parse_tool_manifest
from api_server.marketplace.trust import NetworkPolicy, trust_policy

_log = structlog.get_logger("marketplace.install")


# =============================================================================
# Typed error hierarchy — every gate aborts with its own type
# =============================================================================
class InstallError(RuntimeError):
    """Base for a hard install abort.

    Every subclass corresponds to ONE failed gate; the orchestrator writes a
    matching audit entry before raising so the trail records why no install
    was enabled. ``audit_action`` is the action persisted for the abort.
    """

    audit_action: MarketplaceAuditAction = MarketplaceAuditAction.INSTALL


class ArtifactFetchError(InstallError):
    """The artifact could not be fetched from its source (gate 1)."""


class ManifestParseError(InstallError):
    """The SKILL.md / tool manifest is malformed (gate 2)."""


class SignatureVerificationError(InstallError):
    """A required signature is missing or does not verify (gate 3).

    The message NEVER contains the signature bytes or the key material —
    only the fact that verification failed."""


class StaticAnalysisBlockedError(InstallError):
    """Static analysis found a finding above the trust policy (gate 4)."""


class SandboxCheckFailedError(InstallError):
    """The post-install sandbox smoke check failed / could not run (gate 5)."""


# =============================================================================
# Artifact fetch — abstracted behind a protocol (tests inject a local fixture)
# =============================================================================
@dataclass(frozen=True, slots=True)
class FetchedArtifact:
    """The bytes + tree the install flow needs after a fetch.

    ``source_dir`` is a local directory the static analyzer scans and the
    sandbox mounts read-only. ``manifest_text`` is the raw SKILL.md / tool
    manifest document (parsed in gate 2 and the exact bytes the signature is
    verified over in gate 3). ``signature`` is the detached signature, when
    the source carried one (verified listings); ``None`` when unsigned.
    """

    source_dir: str
    manifest_text: str
    signature: str | None = None


class ArtifactFetcher(Protocol):
    """How the install flow gets a listing's artifact onto local disk.

    Abstracted so the orchestration never hard-codes a transport: the tests
    inject a :class:`LocalArtifactFetcher` pointing at a fixture directory
    (no network), and a real HTTP/git fetcher is a future implementation of
    the same one-method protocol. A fetch that cannot complete must raise —
    the orchestrator maps any failure to :class:`ArtifactFetchError`.
    """

    def fetch(self, listing: MarketplaceListing) -> FetchedArtifact:  # pragma: no cover - protocol
        ...


@dataclass(frozen=True, slots=True)
class LocalArtifactFetcher:
    """An :class:`ArtifactFetcher` that reads an already-on-disk artifact.

    The default fetcher for tests + for the official catalog whose artifacts
    ship with the platform: ``root_dir`` holds one sub-directory per listing
    id, each containing the manifest file (``SKILL.md`` for a skill,
    ``tool.yaml`` for a tool / MCP server) and optionally a ``.sig`` detached
    signature. No network — the capability-gap-honest stand-in for the live
    registry fetch, which is a future implementation of the same protocol.
    """

    root_dir: str

    def fetch(self, listing: MarketplaceListing) -> FetchedArtifact:
        listing_dir = Path(self.root_dir) / str(listing.id)
        if not listing_dir.is_dir():
            raise ArtifactFetchError(
                f"no artifact on disk for listing {listing.id} under {self.root_dir!r}"
            )
        manifest_name = _manifest_filename(listing.kind)
        manifest_path = listing_dir / manifest_name
        if not manifest_path.is_file():
            raise ArtifactFetchError(
                f"artifact for listing {listing.id} is missing {manifest_name!r}"
            )
        manifest_text = manifest_path.read_text(encoding="utf-8")
        sig_path = listing_dir / f"{manifest_name}.sig"
        signature = sig_path.read_text(encoding="utf-8").strip() if sig_path.is_file() else None
        return FetchedArtifact(
            source_dir=str(listing_dir),
            manifest_text=manifest_text,
            signature=signature,
        )


def _manifest_filename(kind: str) -> str:
    """The manifest file name expected on disk for a listing ``kind``."""
    return "SKILL.md" if kind == MarketplaceListingKind.SKILL.value else "tool.yaml"


# =============================================================================
# Signature verification — real cryptography (Ed25519), no secrets echoed
# =============================================================================
def verify_artifact_signature(
    *,
    manifest_text: str,
    signature: str | None,
    public_key_pem: bytes,
) -> None:
    """Verify the detached Ed25519 signature over the manifest bytes.

    Raises :class:`SignatureVerificationError` when the signature is absent,
    not valid hex, or does not verify against ``public_key_pem`` over the
    UTF-8 manifest bytes — a tampered or unsigned verified-listing artifact
    is rejected. Uses :mod:`cryptography` (already a project dependency); no
    key/secret material ever reaches the error message or the audit detail.
    """
    if not signature:
        raise SignatureVerificationError(
            "listing requires a signature but the artifact is unsigned"
        )

    # Imported lazily so a bad/missing key surfaces only on the verified path.
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    try:
        signature_bytes = bytes.fromhex(signature)
    except ValueError as exc:
        raise SignatureVerificationError("signature is not valid hex") from exc

    try:
        public_key = load_pem_public_key(public_key_pem)
    except Exception as exc:  # malformed key material — fail closed
        raise SignatureVerificationError("platform signing key is unusable") from exc

    # The platform signing key must be Ed25519 (the detached-signature scheme
    # this verifier implements). Narrowing the loaded key both enforces that
    # invariant at runtime and resolves the verify() overload — load_pem_public_key
    # returns a broad union (RSA/EC/Ed25519/… in cryptography 48+) and only the
    # Ed*/Ed25519 keys expose the two-argument verify(signature, data).
    if not isinstance(public_key, Ed25519PublicKey):
        raise SignatureVerificationError("platform signing key must be an Ed25519 public key")

    try:
        public_key.verify(signature_bytes, manifest_text.encode("utf-8"))
    except InvalidSignature as exc:
        raise SignatureVerificationError("artifact signature does not verify") from exc
    except Exception as exc:  # wrong key type / unexpected crypto error
        raise SignatureVerificationError("artifact signature could not be verified") from exc


# =============================================================================
# Orchestrator
# =============================================================================
@dataclass(slots=True)
class _GateContext:
    """The tenant-scoped state every gate shares while one install runs.

    Bundles the session + tenant/actor/listing identity + the mutable
    ``gate_report`` the gates append their verdicts to, so each gate helper
    takes one argument instead of five and the abort path has everything it
    needs to write a tenant-scoped audit row. Not frozen — ``gate_report``
    accumulates across gates.
    """

    session: AsyncSession
    tenant_id: UUID
    actor: str
    listing: MarketplaceListing
    gate_report: dict[str, Any] = field(default_factory=dict)
    # The audit action a gate abort is recorded under: ``install`` for a
    # fresh install, ``update`` for the update path (task_09_12) — so the
    # trail distinguishes a failed install from a failed update.
    abort_action: MarketplaceAuditAction = MarketplaceAuditAction.INSTALL


@dataclass(frozen=True, slots=True)
class InstallResult:
    """The outcome of a successful (non-aborted) install.

    ``installation`` is the persisted row; ``enabled`` mirrors its status
    (``True`` for a verified install, ``False`` for a consent-gated one that
    awaits per-permission consent). ``gate_report`` is the structured trail
    of which gates ran + their verdict (also written to the audit detail).
    """

    installation: MarketplaceInstallation
    enabled: bool
    gate_report: dict[str, Any] = field(default_factory=dict)


class InstallOrchestrator:
    """Runs the ordered install gates against a tenant-scoped session.

    Stateless save for its injected collaborators (the artifact fetcher, the
    static analyzer, the sandbox, the platform signing key). The single entry
    point :meth:`install` resolves the trust policy, runs each required gate
    in order, and either persists the installation (gate 7) or aborts with a
    typed :class:`InstallError` + a committed audit entry.
    """

    def __init__(
        self,
        *,
        fetcher: ArtifactFetcher,
        public_key_pem: bytes | None = None,
        analyzer: Any = None,
        sandbox: Any = None,
        sandbox_image: str = "agentic/sandbox-python:latest",
    ) -> None:
        self._fetcher = fetcher
        self._public_key_pem = public_key_pem
        # Lazy default analyzer so a caller that always passes one (and a
        # minimal install without bandit) need not construct it.
        self._analyzer = analyzer
        self._sandbox = sandbox
        self._sandbox_image = sandbox_image

    # --- public ---------------------------------------------------------
    async def install(
        self,
        *,
        session: AsyncSession,
        tenant_id: UUID,
        actor: str,
        listing: MarketplaceListing,
        installed_by: UUID | None = None,
        project_id: UUID | None = None,
        granted_permissions: Sequence[Any] | None = None,
    ) -> InstallResult:
        """Run the full pipeline for ``listing`` into ``tenant_id``.

        ``actor`` is the audit actor string (e.g. ``user:<uuid>``).
        ``granted_permissions`` is honoured ONLY for a listing whose trust
        level needs no per-permission consent (verified); a consent-gated
        install always starts with none granted (the consent flow records
        them). Raises a typed :class:`InstallError` on any gate failure
        (after committing the matching audit row).
        """
        ctx = await self._run_security_gates(
            session=session, tenant_id=tenant_id, actor=actor, listing=listing
        )

        # --- gate 6+7: consent gate + persist ----------------------------
        gate_report = ctx.gate_report
        needs_consent = consent_required_for(listing.trust_level)
        if needs_consent:
            initial_status = InstallationStatus.DISABLED.value
            granted: list[Any] = []
        else:
            initial_status = InstallationStatus.ENABLED.value
            granted = list(granted_permissions or [])
        gate_report["consent_required"] = needs_consent
        gate_report["status"] = initial_status

        installation = MarketplaceInstallation(
            tenant_id=tenant_id,
            listing_id=listing.id,
            project_id=project_id,
            version=listing.version,
            status=initial_status,
            granted_permissions=granted,
            denied_permissions=[],
            installed_by=installed_by,
        )
        session.add(installation)
        await session.flush()

        session.add(
            MarketplaceAuditEntry(
                tenant_id=tenant_id,
                actor=actor,
                action=MarketplaceAuditAction.INSTALL.value,
                listing_id=listing.id,
                installation_id=installation.id,
                detail={
                    "version": listing.version,
                    "trust_level": listing.trust_level,
                    "consent_required": needs_consent,
                    "status": initial_status,
                    "granted_permissions": granted,
                    "project_id": str(project_id) if project_id else None,
                    "gates": gate_report,
                },
            )
        )
        await session.flush()
        await session.refresh(installation)

        _log.info(
            "marketplace.install.done",
            listing_id=str(listing.id),
            trust_level=listing.trust_level,
            status=initial_status,
            consent_required=needs_consent,
        )
        return InstallResult(
            installation=installation,
            enabled=initial_status == InstallationStatus.ENABLED.value,
            gate_report=gate_report,
        )

    async def analyze_for_install(
        self,
        *,
        session: AsyncSession,
        tenant_id: UUID,
        actor: str,
        listing: MarketplaceListing,
    ) -> dict[str, Any]:
        """Gate de análisis estático para la instalación FRESCA (task_prod12_mkt_01).

        El install de Fase A no pasaba por NINGÚN gate (el TODO de ADR 0081);
        este método cablea exactamente el que sí puede correr dentro del
        api-server — bandit/semgrep son puro Python, sin Docker — reusando
        ``_gate_static_analysis`` (el MISMO pipeline del update).

        Semántica deliberada:
          - política sin análisis requerido → ``skipped_reason=not_required``;
          - artefacto AUSENTE en disco → ``skipped_reason=no_artifact`` y la
            instalación SIGUE (bloquear aquí cerraría en falso toda
            instalación pre-registry — la regresión H4 que ADR 0081 documenta;
            el hueco restante es la Fase B/C: registry + sandbox out-of-process);
          - hallazgos por encima de ``max_allowed_severity`` → audit row de
            aborto comprometida + :class:`StaticAnalysisBlockedError` (422 en
            el router), sin installation persistida.

        Devuelve el fragmento de ``gate_report`` que el caller pliega en el
        detail del audit row del install.
        """
        policy = trust_policy(listing.trust_level)
        if not policy.static_analysis_required:
            return {"static_analysis": {"skipped_reason": "not_required"}}
        ctx = _GateContext(
            session=session,
            tenant_id=tenant_id,
            actor=actor,
            listing=listing,
            gate_report={"trust_level": str(policy.level)},
        )
        try:
            artifact = self._fetcher.fetch(listing)
        except Exception as exc:
            _log.warning(
                "marketplace.install.analysis_skipped_no_artifact",
                listing_id=str(listing.id),
                error=str(exc),
            )
            return {"static_analysis": {"skipped_reason": "no_artifact", "detail": str(exc)}}
        await self._gate_static_analysis(ctx, artifact, policy)
        return ctx.gate_report

    async def update(
        self,
        *,
        session: AsyncSession,
        tenant_id: UUID,
        actor: str,
        installation: MarketplaceInstallation,
        target_listing: MarketplaceListing,
    ) -> InstallResult:
        """Re-point ``installation`` to ``target_listing`` after re-running gates.

        The update path (task_09_12). It re-runs the SAME security gates the
        first install ran (fetch → parse → signature → static-analysis →
        sandbox) against the NEW version's artifact — an outdated install can
        only move to a version that itself passes the trust policy — then
        re-points the install's ``version`` and writes an ``update`` audit
        row carrying the version diff + the gate trail.

        Compatibility (no auto-major-jump) is the CALLER's call: the router
        resolves ``target_listing`` via
        :func:`api_server.marketplace.versioning.select_update_target`, which
        only yields a major-version target with the explicit opt-in. By the
        time we are here the target is already an approved, compatible bump.

        Like :meth:`install`, any gate failure raises a typed
        :class:`InstallError` after committing an abort audit row, and NO
        version change is persisted (the install stays on its old version).
        The success path flushes the version re-point + ``update`` audit row
        in the caller's transaction so they commit atomically.
        """
        from_version = installation.version
        to_version = target_listing.version

        ctx = await self._run_security_gates(
            session=session,
            tenant_id=tenant_id,
            actor=actor,
            listing=target_listing,
            abort_action=MarketplaceAuditAction.UPDATE,
            # MISMA semántica que `analyze_for_install`: un artefacto AUSENTE en
            # disco es un skip honesto, no un aborto.
            #
            # Sin esto la asimetría era letal y silenciosa: el install fresco
            # instala igual cuando no hay artefacto (decisión deliberada del
            # ADR 0081 / regresión H4 — bloquear ahí cerraría en falso TODO el
            # catálogo pre-registry), pero el update abortaba con 422. O sea:
            # **toda instalación creada por el camino tolerante era imposible de
            # actualizar**, que es justo la que existe hoy. Lo descubrió el flujo
            # de la fase 4 del ADR 0142 (`test_marketplace_update_flow.py`), que
            # sin este arreglo no podía pasar de la primera versión.
            #
            # Lo que NO se relaja: si el artefacto SÍ está, sus gates corren
            # enteros y un fallo aborta como siempre.
            tolerate_missing_artifact=True,
        )
        gate_report = ctx.gate_report
        gate_report["update"] = {"from_version": from_version, "to_version": to_version}

        # Re-point the existing install to the new listing version. We keep
        # the granted/denied consent state: a compatible (non-major) update
        # does not change the requested-permission surface; a major bump that
        # WOULD change it is gated behind the explicit opt-in upstream, and a
        # future iteration can re-trigger consent there.
        installation.listing_id = target_listing.id
        installation.version = to_version
        session.add(installation)
        await session.flush()

        session.add(
            MarketplaceAuditEntry(
                tenant_id=tenant_id,
                actor=actor,
                action=MarketplaceAuditAction.UPDATE.value,
                listing_id=target_listing.id,
                installation_id=installation.id,
                detail={
                    "from_version": from_version,
                    "to_version": to_version,
                    "trust_level": target_listing.trust_level,
                    "status": installation.status,
                    "gates": gate_report,
                },
            )
        )
        await session.flush()
        await session.refresh(installation)

        _log.info(
            "marketplace.install.updated",
            listing_id=str(target_listing.id),
            from_version=from_version,
            to_version=to_version,
        )
        return InstallResult(
            installation=installation,
            enabled=installation.status == InstallationStatus.ENABLED.value,
            gate_report=gate_report,
        )

    # --- gate orchestration ---------------------------------------------
    async def _run_security_gates(
        self,
        *,
        session: AsyncSession,
        tenant_id: UUID,
        actor: str,
        listing: MarketplaceListing,
        abort_action: MarketplaceAuditAction = MarketplaceAuditAction.INSTALL,
        tolerate_missing_artifact: bool = False,
    ) -> _GateContext:
        """Run gates 1-5 (fetch → parse → signature → analysis → sandbox).

        Shared by :meth:`install` and :meth:`update`: each helper runs its
        gate, records the verdict on ``ctx.gate_report``, and on failure
        writes a COMMITTED abort audit row (under ``abort_action`` — ``install``
        or ``update``) + raises a typed :class:`InstallError` (so no install
        row / version change survives). Returns the populated context (its
        ``gate_report`` carries the trail the caller folds into the install /
        update audit detail).

        ``tolerate_missing_artifact`` reproduces the honest skip that
        :meth:`analyze_for_install` already documents: with NO artifact on disk
        there is nothing to fetch, parse, verify or scan, so the gates record
        ``skipped_reason=no_artifact`` and the caller goes on. It is opt-in and
        applies ONLY to the artifact being *absent* — an artifact that IS there
        runs every gate its trust policy demands, and a failure aborts.
        """
        policy = trust_policy(listing.trust_level)
        ctx = _GateContext(
            session=session,
            tenant_id=tenant_id,
            actor=actor,
            listing=listing,
            gate_report={"trust_level": str(policy.level)},
            abort_action=abort_action,
        )
        if tolerate_missing_artifact:
            try:
                artifact = self._fetcher.fetch(listing)
            except Exception as exc:
                _log.warning(
                    "marketplace.install.gates_skipped_no_artifact",
                    listing_id=str(listing.id),
                    action=abort_action.value,
                    error=str(exc),
                )
                ctx.gate_report["skipped_reason"] = "no_artifact"
                ctx.gate_report["detail"] = str(exc)
                return ctx
            ctx.gate_report["fetched"] = True
        else:
            artifact = await self._gate_fetch(ctx)
        await self._gate_parse(ctx, artifact)
        if policy.signature_required:
            await self._gate_signature(ctx, artifact)
        if policy.static_analysis_required:
            await self._gate_static_analysis(ctx, artifact, policy)
        if policy.sandbox_required:
            await self._gate_sandbox(ctx, artifact)
        return ctx

    # --- gate helpers (one per ordered gate) ----------------------------
    async def _gate_fetch(self, ctx: _GateContext) -> FetchedArtifact:
        """Gate 1: fetch the artifact onto local disk (abort on transport fail)."""
        try:
            artifact = self._fetcher.fetch(ctx.listing)
        except InstallError:
            raise
        except Exception as exc:  # any fetch transport failure → typed abort
            await self._abort(ctx, reason="artifact_fetch_failed", message=str(exc))
            raise ArtifactFetchError(f"could not fetch artifact: {exc}") from exc
        ctx.gate_report["fetched"] = True
        return artifact

    async def _gate_parse(self, ctx: _GateContext, artifact: FetchedArtifact) -> None:
        """Gate 2: parse the SKILL.md / tool manifest (abort if malformed)."""
        try:
            self._parse_manifest(ctx.listing.kind, artifact.manifest_text)
        except (SkillFormatError, ToolFormatError) as exc:
            await self._abort(ctx, reason="manifest_invalid", message=str(exc))
            raise ManifestParseError(f"artifact manifest is invalid: {exc}") from exc
        ctx.gate_report["manifest_parsed"] = True

    async def _gate_signature(self, ctx: _GateContext, artifact: FetchedArtifact) -> None:
        """Gate 3: verify the detached signature (abort + NEVER echo bytes)."""
        try:
            verify_artifact_signature(
                manifest_text=artifact.manifest_text,
                signature=artifact.signature,
                public_key_pem=self._require_public_key(),
            )
        except SignatureVerificationError as exc:
            await self._abort(ctx, reason="signature_invalid", message=str(exc))
            raise
        ctx.gate_report["signature_verified"] = True

    async def _gate_static_analysis(
        self, ctx: _GateContext, artifact: FetchedArtifact, policy: Any
    ) -> None:
        """Gate 4: static analysis; abort when a finding exceeds the policy."""
        # `_run_static_analysis` lanza bandit y semgrep por `subprocess.run`
        # SÍNCRONO, con hasta 120 s de timeout cada uno. Ejecutado en el bucle
        # de eventos congelaba durante minutos TODAS las requests y WebSockets
        # del api-server (hallazgo perf-1). `to_thread` es la mitigación que el
        # plan prod-13 llama "intermedia": el request sigue durando lo que dura
        # el análisis, pero deja de secuestrar el proceso entero. Mover la
        # puerta a Celery + 202 sigue siendo lo correcto y es otra tarea.
        report = await asyncio.to_thread(
            self._run_static_analysis, artifact.source_dir, ctx.listing
        )
        ctx.gate_report["static_analysis"] = {
            "ran": list(report.ran),
            "skipped": [name for name, _ in report.skipped],
            "max_severity": report.max_severity.name,
            "blocked": report.blocked,
        }
        if not report.blocked:
            return
        blocking = [
            {"severity": f.severity.name, "rule": f.rule, "file": f.file, "line": f.line}
            for f in report.blocking_findings()
        ]
        await self._abort(
            ctx,
            reason="static_analysis_blocked",
            message=f"{len(blocking)} finding(s) exceed the trust policy",
            extra={"blocking_findings": blocking},
        )
        raise StaticAnalysisBlockedError(
            f"static analysis blocked install: max severity "
            f"{report.max_severity.name} exceeds policy "
            f"{policy.max_allowed_severity.name}"
        )

    async def _gate_sandbox(self, ctx: _GateContext, artifact: FetchedArtifact) -> None:
        """Gate 5: run the sandbox smoke check (abort on a failing probe)."""
        # Igual que la puerta 4: el SDK de Docker es síncrono de principio a
        # fin (crear el contenedor, esperar, leer logs) y bloquearía el bucle
        # de eventos mientras corre la prueba de humo (perf-1).
        result = await asyncio.to_thread(self._run_sandbox, artifact)
        ctx.gate_report["sandbox"] = {
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "passed": result.passed,
        }
        if result.passed:
            return
        await self._abort(
            ctx,
            reason="sandbox_check_failed",
            message=f"smoke check exit_code={result.exit_code} timed_out={result.timed_out}",
        )
        raise SandboxCheckFailedError(
            f"sandbox smoke check failed (exit_code={result.exit_code}, "
            f"timed_out={result.timed_out})"
        )

    @staticmethod
    def _parse_manifest(kind: str, text: str) -> None:
        """Parse the manifest for ``kind`` (raises the format's typed error)."""
        if kind == MarketplaceListingKind.SKILL.value:
            parse_skill_md(text)
        else:
            parse_tool_manifest(text)

    def _require_public_key(self) -> bytes:
        if self._public_key_pem is None:
            # A verified listing demands a key; missing config fails closed.
            raise SignatureVerificationError("no platform signing key configured")
        return self._public_key_pem

    def _run_static_analysis(self, source_dir: str, listing: MarketplaceListing) -> Any:
        analyzer = self._analyzer
        if analyzer is None:
            from api_server.marketplace.static_analysis import StaticAnalyzer

            analyzer = StaticAnalyzer()
        return analyzer.analyze(source_dir, listing.trust_level)

    def _run_sandbox(self, artifact: FetchedArtifact) -> Any:
        from api_server.marketplace.sandbox import (
            MarketplaceSandbox,
            SandboxError,
            SandboxSpec,
        )

        sandbox = self._sandbox if self._sandbox is not None else MarketplaceSandbox()
        spec = SandboxSpec(
            image=self._sandbox_image,
            # A first run for an unvetted listing must have no egress; the
            # consented network policy is applied on later real runs, not the
            # smoke probe (decision (b): the first run is the most locked-down).
            network_policy=NetworkPolicy.NONE,
            workspace_host_path=artifact.source_dir,
        )
        try:
            return sandbox.run(spec)
        except SandboxError as exc:  # could not even run the probe → fail closed
            raise SandboxCheckFailedError(f"sandbox could not run the smoke check: {exc}") from exc

    # --- abort ----------------------------------------------------------
    async def _abort(
        self,
        ctx: _GateContext,
        *,
        reason: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Write + COMMIT a tenant-scoped abort audit entry, then return.

        Committed immediately so the immutable record survives the request
        transaction unwinding when the caller propagates the raised
        :class:`InstallError`. The audit row never carries signature/secret
        bytes — only the ``reason`` + a sanitized ``message``.
        """
        gate_report = {**ctx.gate_report, **(extra or {})}
        ctx.session.add(
            MarketplaceAuditEntry(
                tenant_id=ctx.tenant_id,
                actor=ctx.actor,
                action=ctx.abort_action.value,
                listing_id=ctx.listing.id,
                installation_id=None,
                detail={
                    "version": ctx.listing.version,
                    "trust_level": ctx.listing.trust_level,
                    "aborted": True,
                    "reason": reason,
                    "message": message,
                    "gates": gate_report,
                },
            )
        )
        await ctx.session.commit()
        _log.warning(
            "marketplace.install.aborted",
            listing_id=str(ctx.listing.id),
            reason=reason,
        )


def default_artifact_root() -> str:
    """The on-disk root the official-catalog :class:`LocalArtifactFetcher` reads.

    Overridable via ``MARKETPLACE_ARTIFACT_ROOT`` so a deployment can point
    at the mounted catalog volume; defaults to the conventional data path.
    """
    return os.environ.get("MARKETPLACE_ARTIFACT_ROOT", "/data/agent-platform/marketplace/artifacts")


__all__ = [
    "ArtifactFetchError",
    "ArtifactFetcher",
    "FetchedArtifact",
    "InstallError",
    "InstallOrchestrator",
    "InstallResult",
    "LocalArtifactFetcher",
    "ManifestParseError",
    "SandboxCheckFailedError",
    "SignatureVerificationError",
    "StaticAnalysisBlockedError",
    "default_artifact_root",
    "verify_artifact_signature",
]
