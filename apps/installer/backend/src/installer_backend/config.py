"""Captured installer configuration — wizard steps 2-6 (Plan 15 task_15_03).

Steps 2-6 of the wizard capture everything the installer needs to provision the
stack:

    2. basics    — system config: domain + environment.
    3. resources — resource allocation + GPU enablement.
    4. storage   — data root + MinIO (object storage) config.
    5. providers — the four ADR-0021 LLM providers (Claude Agent SDK, GitHub
                   Copilot, Azure AI Foundry via APIM, Ollama). Credentials /
                   endpoints captured here.
    6. tenant    — the initial tenant: name + admin email.

This module defines the Pydantic models the wizard UI POSTs to the installer
backend, plus the *server-side* validation that backs the client-side checks.
The frontend validates first (fast feedback); the backend validates again
(never trust the client) and returns a structured per-field error list the UI
can surface.

Security — secrets are WRITE-ONLY
---------------------------------
Provider credentials (Claude SDK / Copilot tokens, Azure APIM key) and the MinIO
secret key are captured but NEVER echoed back. They are modelled as
:class:`pydantic.SecretStr` so they don't leak into ``repr``/logs/JSON dumps,
and the validation response carries only booleans (``*_set``) telling the UI
whether a secret was provided — never the value. The real secrets are written
to Vault by Phase B (tasks 15_08-15_09); this module only captures + validates
their shape. Nothing here is logged.

The captured config is held in the wizard state on the client and POSTed to
``/api/config/validate`` (and later, in Phase B, consumed by the generators).
No host access happens here — this is pure validation.
"""

from __future__ import annotations

import ipaddress
import re
from enum import Enum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

#: The three deployment modes for the in-stack Ollama (ADR 0056):
#:   * ``none`` — no Ollama service; embeddings use an external/cloud Ollama or
#:     stay on BM25 keyword search.
#:   * ``cpu``  — Ollama service without a GPU reservation (enough for embeddings
#:     and small local LLMs).
#:   * ``gpu``  — adds the NVIDIA device reservation for accelerated local LLMs.
OllamaMode = Literal["none", "cpu", "gpu"]

#: Deployment mode for the in-stack voice services (stt/tts) that power the
#: Assistant + córtex voice mode (ADR 0073):
#:   * ``none`` — no stt/tts services; the voice mode is unavailable in the
#:     deployment (the operator can still opt in later).
#:   * ``cpu``  — faster-whisper + Kokoro on CPU (the reference images); enough
#:     for the ES+EN small model. This is the default so voice works out of the
#:     box on a real install.
#:   * ``gpu``  — same services, reserved for a future CUDA overlay (the
#:     reference compose pins CPU images today; GPU is the documented overlay).
VoiceMode = Literal["none", "cpu", "gpu"]

#: How the single reverse proxy terminates TLS (ADR 0061):
#:   * ``internal`` — Caddy's local CA, self-signed (default; flagged pending).
#:   * ``provided`` — operator-supplied corporate cert (cert+key bind-mounted).
#:   * ``acme``     — Caddy obtains a public cert via ACME (needs email, non-IP).
TlsMode = Literal["internal", "provided", "acme"]

# ---------------------------------------------------------------------------
# Small shared validators / patterns.
# ---------------------------------------------------------------------------
# A hostname / FQDN label per RFC 1123 (also accepts bare "localhost"). We keep
# this permissive but reject obviously-invalid input (spaces, schemes, paths).
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$"
)

# Absolute POSIX path for the data root (the stack runs on Linux; the data root
# is an absolute path like /data/agent-platform). Reject relative / Windows
# paths to fail fast on a misconfiguration.
_POSIX_ABS_PATH_RE = re.compile(r"^/[^\0]*$")

# Object-storage bucket name (S3/MinIO): lowercase letters, digits, hyphens;
# 3-63 chars; must start/end alphanumeric.
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")

# Ollama / Azure endpoints must be http(s) URLs. Kept deliberately simple — a
# full URL validator is overkill for a one-shot installer field.
_HTTP_URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)


def _is_valid_host(value: str) -> bool:
    """True if *value* is a valid hostname/FQDN or an IP address."""

    if _HOSTNAME_RE.match(value):
        return True
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# Step 2 — system basics.
# ---------------------------------------------------------------------------
class Environment(str, Enum):
    """Deployment environment profile. Drives defaults + hardening posture."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"

    @property
    def runtime_value(self) -> str:
        """El nombre que entienden los servicios: ``{dev, staging, prod}``.

        Los valores de este enum son texto de UI del wizard (``production``), y
        el runtime valida contra un enum CERRADO distinto (``prod``) — ver
        ``api_server.config._KNOWN_ENVIRONMENTS``. Traducir es obligatorio en
        TODO sitio que emita configuración para un servicio.

        Vive aquí, en el enum, y no en un generador, porque tener el mapeo en uno
        solo de los dos generadores ya costó un bloqueo de arranque: el ``.env``
        traducía y el compose emitía ``production`` en crudo. Mientras el guard
        del runtime era fail-open el valor desconocido se trataba como dev y no
        se notaba; al volverlo fail-closed (prod-09 task_02), el api-server
        generado por el instalador dejó de arrancar.
        """
        return {
            Environment.DEVELOPMENT: "dev",
            Environment.STAGING: "staging",
            Environment.PRODUCTION: "prod",
        }[self]


class SystemConfig(BaseModel):
    """Step 2: domain the platform is served on + environment + how the single
    reverse proxy terminates TLS (ADR 0061).

    ``tls_mode`` defaults to ``internal`` (Caddy's local CA, self-signed) so a
    fresh install boots with HTTPS and zero external dependencies; the installer
    flags the self-signed cert as a pending action. ``provided`` consumes a
    corporate cert (``tls_cert_path``/``tls_key_path``); ``acme`` lets Caddy
    obtain a public cert (needs ``tls_acme_email`` and a non-IP domain). The new
    fields all default to ``None`` so configs that only set ``domain`` (and the
    test fixtures) keep loading unchanged.
    """

    model_config = ConfigDict(extra="forbid")

    domain: str = Field(..., min_length=1, max_length=253)
    environment: Environment = Environment.PRODUCTION
    tls_mode: TlsMode = "internal"
    tls_cert_path: str | None = None
    tls_key_path: str | None = None
    tls_acme_email: str | None = None
    tls_acme_ca: str | None = None

    @field_validator("domain")
    @classmethod
    def _validate_domain(cls, value: str) -> str:
        value = value.strip().lower()
        if not _is_valid_host(value):
            raise ValueError(
                "El dominio debe ser un nombre de host válido (p. ej. agentic.example.com) "
                "o una dirección IP, sin esquema (http://) ni ruta."
            )
        return value

    @model_validator(mode="after")
    def _validate_tls(self) -> SystemConfig:
        if self.tls_mode == "provided":
            if not self.tls_cert_path or not self.tls_key_path:
                raise ValueError(
                    "tls_mode='provided' requiere tls_cert_path y tls_key_path "
                    "(el certificado corporativo y su clave privada)."
                )
        elif self.tls_mode == "acme":
            if not self.tls_acme_email:
                raise ValueError(
                    "tls_mode='acme' requiere tls_acme_email (la CA ACME exige un "
                    "contacto para emitir y avisar de caducidades)."
                )
            try:
                ipaddress.ip_address(self.domain)
            except ValueError:
                pass
            else:
                raise ValueError(
                    "tls_mode='acme' no es válido con un dominio que es una IP: "
                    "las CA ACME no emiten certificados para IPs. Usa 'internal' "
                    "o 'provided'."
                )
            if self.tls_acme_ca and not _HTTP_URL_RE.match(self.tls_acme_ca):
                raise ValueError("tls_acme_ca debe ser la URL http(s) del directorio ACME.")
        return self


# ---------------------------------------------------------------------------
# Step 3 — resources / GPU.
# ---------------------------------------------------------------------------
class ResourceConfig(BaseModel):
    """Step 3: resource allocation for the stack + in-stack Ollama mode.

    ``worker_replicas`` and ``worker_memory_gib`` size the Celery workers; the
    real prereq probe (task 15_02) already told the operator whether the host
    can host them.

    ``ollama_mode`` (ADR 0056) selects how the in-stack Ollama is deployed —
    ``none`` / ``cpu`` / ``gpu``. ``gpu`` is only meaningful when an NVIDIA GPU +
    the Container Toolkit were detected. ``embedding_model`` is the model the
    bootstrap pulls and the api-server requests (the REAL Ollama registry name,
    default ``nomic-embed-text``; 768 dims).

    ``gpu_enabled`` is DEPRECATED (superseded by ``ollama_mode``) but still
    accepted so older saved configs load: when ``ollama_mode`` is omitted it is
    derived from ``gpu_enabled`` (True → ``gpu``, else ``none``), and the boolean
    is then kept in lockstep with ``ollama_mode == "gpu"``.
    """

    model_config = ConfigDict(extra="forbid")

    worker_replicas: int = Field(default=2, ge=1, le=64)
    worker_memory_gib: int = Field(default=4, ge=1, le=512)
    # Deprecated alias — see the model validator below + ollama_mode.
    gpu_enabled: bool = False
    ollama_mode: OllamaMode | None = None
    # Voice mode (ADR 0073): stt (faster-whisper) + tts (Kokoro) for the
    # Assistant + córtex voice mode. Defaults to ``cpu`` so a fresh install ships
    # a working voice stack; set ``none`` to skip the models' download/footprint.
    # When omitted on an OLDER saved config it is derived to ``cpu`` (back-compat
    # bridge below), so the bugfix applies even to configs persisted before this
    # field existed.
    voice_mode: VoiceMode | None = None
    embedding_model: str = Field(default="nomic-embed-text", min_length=1, max_length=120)

    @model_validator(mode="after")
    def _resolve_modes(self) -> ResourceConfig:
        """Back-compat bridges.

        Ollama: derive ``ollama_mode`` from the legacy ``gpu_enabled`` when it
        was not given, then keep the boolean in lockstep so any remaining
        ``gpu_enabled`` reader still sees the GPU truth.

        Voice: default ``voice_mode`` to ``cpu`` when omitted so older saved
        configs (and the wizard's defaults) produce a working voice stack —
        fixing the bug where the production installer never generated stt/tts.
        """
        if self.ollama_mode is None:
            self.ollama_mode = "gpu" if self.gpu_enabled else "none"
        self.gpu_enabled = self.ollama_mode == "gpu"
        if self.voice_mode is None:
            self.voice_mode = "cpu"
        return self


# ---------------------------------------------------------------------------
# Step 4 — storage (data root + MinIO).
# ---------------------------------------------------------------------------
class StorageConfig(BaseModel):
    """Step 4: where persistent data lives + the MinIO object store.

    ``minio_secret_key`` is write-only (:class:`SecretStr`) — it is captured but
    never echoed back. ``data_root`` is the absolute path under which the bare
    repos, pgdata and object storage live (default ``/data/agent-platform``).
    """

    model_config = ConfigDict(extra="forbid")

    data_root: str = Field(default="/data/agent-platform", min_length=1, max_length=4096)
    minio_bucket: str = Field(default="agentic-platform", min_length=3, max_length=63)
    minio_access_key: str = Field(..., min_length=3, max_length=128)
    minio_secret_key: SecretStr = Field(..., min_length=8, max_length=256)

    @field_validator("data_root")
    @classmethod
    def _validate_data_root(cls, value: str) -> str:
        value = value.strip()
        if not _POSIX_ABS_PATH_RE.match(value):
            raise ValueError(
                "La ruta de datos debe ser una ruta absoluta POSIX (p. ej. /data/agent-platform)."
            )
        return value.rstrip("/") or "/"

    @field_validator("minio_bucket")
    @classmethod
    def _validate_bucket(cls, value: str) -> str:
        value = value.strip().lower()
        if not _BUCKET_RE.match(value):
            raise ValueError(
                "El bucket de MinIO debe tener 3-63 caracteres, solo minúsculas, dígitos y "
                "guiones, y empezar/terminar con un carácter alfanumérico."
            )
        return value


# ---------------------------------------------------------------------------
# Step 5 — LLM providers (the four ADR-0021 paths).
# ---------------------------------------------------------------------------
class LLMProviderKind(str, Enum):
    """The four supported provider paths — closed catalogue (ADR 0021)."""

    CLAUDE_SDK = "claude_sdk"
    COPILOT = "copilot"
    AZURE_FOUNDRY = "azure_foundry"
    OLLAMA = "ollama"


class ClaudeSdkProvider(BaseModel):
    """Claude Agent SDK (Pro/Max subscription). OAuth token captured write-only."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # The Claude subscription OAuth token. Write-only.
    oauth_token: SecretStr | None = None


class CopilotProvider(BaseModel):
    """GitHub Copilot (OAuth Device Flow + minted JWT). Token captured write-only."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    oauth_token: SecretStr | None = None


class AzureFoundryProvider(BaseModel):
    """Azure AI Foundry via APIM (OpenAI-compatible gateway).

    Needs the APIM gateway endpoint (URL) + a subscription/API key (write-only).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    apim_endpoint: str | None = None
    api_key: SecretStr | None = None

    @field_validator("apim_endpoint")
    @classmethod
    def _validate_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if value and not _HTTP_URL_RE.match(value):
            raise ValueError("El endpoint de APIM debe ser una URL http(s) válida.")
        return value


class OllamaProvider(BaseModel):
    """Ollama (local or cloud). Endpoint URL only; no secret for local."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    endpoint: str | None = None

    @field_validator("endpoint")
    @classmethod
    def _validate_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if value and not _HTTP_URL_RE.match(value):
            raise ValueError("El endpoint de Ollama debe ser una URL http(s) válida.")
        return value


class ProvidersConfig(BaseModel):
    """Step 5: the four ADR-0021 providers. At least one must be enabled.

    Each enabled provider must carry the credential/endpoint it needs. Secrets
    are write-only across the board. Cross-field rules (enabled ⇒ creds present)
    are validated server-side here, mirrored client-side for fast feedback.
    """

    model_config = ConfigDict(extra="forbid")

    claude_sdk: ClaudeSdkProvider = Field(default_factory=ClaudeSdkProvider)
    copilot: CopilotProvider = Field(default_factory=CopilotProvider)
    azure_foundry: AzureFoundryProvider = Field(default_factory=AzureFoundryProvider)
    ollama: OllamaProvider = Field(default_factory=OllamaProvider)


# ---------------------------------------------------------------------------
# Host port mappings (parametrised by the wizard; consumed by the compose
# generator, task 15_07). Only services that publish a host port are listed;
# everything else stays inside the compose network.
# ---------------------------------------------------------------------------
class PortsConfig(BaseModel):
    """Host ports the generated compose publishes.

    Defaults match the canonical dev compose. The admin panel is the operator
    entry point; the others let the installer expose a service on the host when
    the operator overrides the default. All are validated to the IANA range.
    """

    model_config = ConfigDict(extra="forbid")

    admin_panel: int = Field(default=3000, ge=1, le=65535)
    api_server: int = Field(default=8000, ge=1, le=65535)
    minio_console: int = Field(default=9001, ge=1, le=65535)


# ---------------------------------------------------------------------------
# Step 6 — initial tenant.
# ---------------------------------------------------------------------------
class TenantConfig(BaseModel):
    """Step 6: the first tenant created at install time + its admin user."""

    model_config = ConfigDict(extra="forbid")

    tenant_name: str = Field(..., min_length=2, max_length=120)
    admin_email: EmailStr

    @field_validator("tenant_name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("El nombre del tenant no puede estar vacío.")
        return value


# ---------------------------------------------------------------------------
# The full captured config (steps 2-6) + the validation response.
# ---------------------------------------------------------------------------
class InstallerConfig(BaseModel):
    """The complete steps 2-6 capture, POSTed to ``/api/config/validate``."""

    model_config = ConfigDict(extra="forbid")

    system: SystemConfig
    resources: ResourceConfig = Field(default_factory=ResourceConfig)
    storage: StorageConfig
    providers: ProvidersConfig
    tenant: TenantConfig
    ports: PortsConfig = Field(default_factory=PortsConfig)


class FieldError(BaseModel):
    """One server-side validation error, addressed by a dotted field path."""

    field: str
    message: str


class ProvidersSummary(BaseModel):
    """Non-secret summary of which providers are enabled + whether creds were set.

    NEVER carries a secret value — only booleans. The UI uses this to confirm
    "credential provided" without ever seeing the credential again.
    """

    claude_sdk_enabled: bool
    claude_sdk_token_set: bool
    copilot_enabled: bool
    copilot_token_set: bool
    azure_foundry_enabled: bool
    azure_foundry_key_set: bool
    ollama_enabled: bool


class ConfigValidationResponse(BaseModel):
    """Result of validating a posted config. Carries NO secret values."""

    valid: bool
    errors: list[FieldError] = Field(default_factory=list)
    # Echo only the non-secret, normalised values + secret-presence booleans.
    normalized: dict[str, object] = Field(default_factory=dict)
    providers: ProvidersSummary | None = None


# ---------------------------------------------------------------------------
# Cross-field validation that Pydantic field validators can't express alone.
# Returns a flat list of FieldError; an empty list means "valid".
# ---------------------------------------------------------------------------
def validate_providers(providers: ProvidersConfig) -> list[FieldError]:
    """At least one provider enabled; each enabled one carries its creds."""

    errors: list[FieldError] = []

    enabled_any = (
        providers.claude_sdk.enabled
        or providers.copilot.enabled
        or providers.azure_foundry.enabled
        or providers.ollama.enabled
    )
    if not enabled_any:
        errors.append(
            FieldError(
                field="providers",
                message="Debes habilitar al menos un proveedor LLM (ADR-0021).",
            )
        )

    if providers.claude_sdk.enabled and providers.claude_sdk.oauth_token is None:
        errors.append(
            FieldError(
                field="providers.claude_sdk.oauth_token",
                message="Claude SDK requiere un token OAuth de la suscripción.",
            )
        )
    if providers.copilot.enabled and providers.copilot.oauth_token is None:
        errors.append(
            FieldError(
                field="providers.copilot.oauth_token",
                message="GitHub Copilot requiere un token OAuth (Device Flow).",
            )
        )
    if providers.azure_foundry.enabled:
        if not providers.azure_foundry.apim_endpoint:
            errors.append(
                FieldError(
                    field="providers.azure_foundry.apim_endpoint",
                    message="Azure AI Foundry requiere el endpoint del gateway APIM.",
                )
            )
        if providers.azure_foundry.api_key is None:
            errors.append(
                FieldError(
                    field="providers.azure_foundry.api_key",
                    message="Azure AI Foundry requiere la API key del gateway APIM.",
                )
            )
    if providers.ollama.enabled and not providers.ollama.endpoint:
        errors.append(
            FieldError(
                field="providers.ollama.endpoint",
                message="Ollama requiere el endpoint del servidor.",
            )
        )

    return errors


def providers_summary(providers: ProvidersConfig) -> ProvidersSummary:
    """Build the secret-free providers summary for the response."""

    return ProvidersSummary(
        claude_sdk_enabled=providers.claude_sdk.enabled,
        claude_sdk_token_set=providers.claude_sdk.oauth_token is not None,
        copilot_enabled=providers.copilot.enabled,
        copilot_token_set=providers.copilot.oauth_token is not None,
        azure_foundry_enabled=providers.azure_foundry.enabled,
        azure_foundry_key_set=providers.azure_foundry.api_key is not None,
        ollama_enabled=providers.ollama.enabled,
    )


def normalized_summary(config: InstallerConfig) -> dict[str, object]:
    """Non-secret normalised echo of the config (for the UI summary preview).

    Deliberately excludes EVERY secret field (MinIO secret key, provider
    tokens/keys). What it includes is safe to display and to log.
    """

    return {
        "system": {
            "domain": config.system.domain,
            "environment": config.system.environment.value,
        },
        "resources": {
            "worker_replicas": config.resources.worker_replicas,
            "worker_memory_gib": config.resources.worker_memory_gib,
            "ollama_mode": config.resources.ollama_mode,
            "voice_mode": config.resources.voice_mode,
            "embedding_model": config.resources.embedding_model,
            # Deprecated mirror, kept for any consumer still reading it.
            "gpu_enabled": config.resources.gpu_enabled,
        },
        "storage": {
            "data_root": config.storage.data_root,
            "minio_bucket": config.storage.minio_bucket,
            "minio_access_key": config.storage.minio_access_key,
        },
        "tenant": {
            "tenant_name": config.tenant.tenant_name,
            "admin_email": str(config.tenant.admin_email),
        },
    }


def validate_config(config: InstallerConfig) -> ConfigValidationResponse:
    """Full server-side validation. Field-level rules already ran via Pydantic.

    This layers the cross-field provider rules and builds a secret-free
    response. A config that reaches here already passed per-field validation
    (Pydantic raised otherwise); the only remaining failures are the provider
    cross-field rules.
    """

    errors = validate_providers(config.providers)
    return ConfigValidationResponse(
        valid=not errors,
        errors=errors,
        normalized=normalized_summary(config) if not errors else {},
        providers=providers_summary(config.providers),
    )
