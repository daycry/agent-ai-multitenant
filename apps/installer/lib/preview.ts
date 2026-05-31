/**
 * Resource preview for the summary / confirmation step (Plan 15 task_15_04).
 *
 * Pure, typed derivation of "what will be provisioned" from the captured wizard
 * config (steps 2-6). It powers the review screen's preview of the stack:
 *
 *   - the services that Docker Compose will bring up,
 *   - each service's internal default port,
 *   - the named volumes / host paths created under the data root,
 *   - an *estimated* RAM and disk footprint (an upper-ish guide, NOT a probe).
 *
 * These are ESTIMATES for operator review only. No host access happens here —
 * the real provisioning (docker compose up, file writes, Vault bootstrap) lives
 * behind the backend's injectable seams (Phase B). The actual published-port
 * layout is fronted by the egress/reverse proxy in the runtime compose; the
 * ports shown here are the canonical in-container service ports so the operator
 * can reason about the stack.
 *
 * Mirrors the canonical service set of `docker/docker-compose.yml` plus the
 * app-tier services from CLAUDE.md (api-server, orchestrator, workers, …).
 */

import type { InstallerConfig } from "./config";

/** A single service that will be brought up by the generated compose. */
export interface PreviewService {
  /** Stable compose service name (used as the row key + data-testid suffix). */
  readonly name: string;
  /** Human-facing role, ES (per docs_language). */
  readonly role: string;
  /** Canonical in-container port, or null for portless services (workers). */
  readonly port: number | null;
  /** Rough steady-state RAM reservation in MiB, used for the estimate. */
  readonly ramMib: number;
}

/** A persistent volume / host path created under the data root. */
export interface PreviewVolume {
  /** Compose volume name or host bind path. */
  readonly name: string;
  /** What it stores, ES. */
  readonly purpose: string;
  /** Rough disk reservation in GiB, used for the estimate. */
  readonly diskGib: number;
}

/** The full provisioning preview rendered on the summary step. */
export interface ResourcePreview {
  readonly services: readonly PreviewService[];
  readonly volumes: readonly PreviewVolume[];
  /** Estimated total RAM footprint in GiB (rounded up to one decimal). */
  readonly estimatedRamGib: number;
  /** Estimated total disk footprint in GiB. */
  readonly estimatedDiskGib: number;
  /** True when the GPU runtime add-on is included. */
  readonly gpuEnabled: boolean;
}

// ---------------------------------------------------------------------------
// Canonical infra + app service set. Infra mirrors docker/docker-compose.yml;
// the app tier mirrors CLAUDE.md's apps/ layout. RAM figures are conservative
// steady-state reservations used only to build the operator-facing estimate.
// ---------------------------------------------------------------------------
const INFRA_SERVICES: readonly PreviewService[] = [
  { name: "postgres", role: "PostgreSQL 16 + pgvector", port: 5432, ramMib: 1024 },
  { name: "redis", role: "Redis 7 (cache + broker)", port: 6379, ramMib: 256 },
  { name: "minio", role: "MinIO (object storage S3)", port: 9000, ramMib: 512 },
  { name: "vault", role: "HashiCorp Vault (secretos)", port: 8200, ramMib: 256 },
  { name: "clamav", role: "ClamAV (antivirus)", port: 3310, ramMib: 1024 },
  { name: "docling-serve", role: "Docling (ingestión documental)", port: 5001, ramMib: 768 },
  { name: "egress-proxy", role: "Proxy de egress (red restringida)", port: 3128, ramMib: 128 },
] as const;

const APP_SERVICES: readonly PreviewService[] = [
  { name: "api-server", role: "API FastAPI (REST + WebSocket)", port: 8000, ramMib: 512 },
  { name: "orchestrator", role: "Orquestador de tareas", port: null, ramMib: 384 },
  { name: "memorizer", role: "Indexación de memoria", port: null, ramMib: 384 },
  { name: "admin-panel", role: "Panel del System Admin (Next.js)", port: 3000, ramMib: 256 },
  { name: "web-app", role: "Aplicación web de tenants (Next.js)", port: 3001, ramMib: 256 },
] as const;

/** Per-worker RAM is bounded by the operator's per-worker memory choice. */
const WORKER_RAM_OVERHEAD_MIB = 256;

/** Base disk volumes that always exist (mirrors compose named volumes). */
function baseVolumes(config: InstallerConfig): readonly PreviewVolume[] {
  const root = config.storage.dataRoot;
  return [
    { name: `${root}/repos`, purpose: "Repos bare + worktrees de proyectos", diskGib: 20 },
    { name: "postgres_data", purpose: "Datos de PostgreSQL + pgvector", diskGib: 10 },
    { name: "redis_data", purpose: "Persistencia de Redis", diskGib: 1 },
    {
      name: "minio_data",
      purpose: `Objetos MinIO (bucket «${config.storage.minioBucket}»)`,
      diskGib: 20,
    },
    { name: "vault_data", purpose: "Almacén de Vault", diskGib: 1 },
    { name: "clamav_data", purpose: "Firmas de ClamAV", diskGib: 2 },
  ];
}

/** Round up to one decimal place to keep estimates readable. */
function roundUp1(value: number): number {
  return Math.ceil(value * 10) / 10;
}

/**
 * Build the provisioning preview from the captured config. Pure: depends only
 * on the config, never on the host. The worker tier expands to
 * `worker_replicas` rows so the operator sees the real fan-out, and the RAM
 * estimate uses the per-worker memory they chose.
 */
export function buildPreview(config: InstallerConfig): ResourcePreview {
  const { workerReplicas, workerMemoryGib, gpuEnabled } = config.resources;

  const workers: PreviewService[] = Array.from({ length: workerReplicas }, (_, i) => ({
    name: `worker-${i + 1}`,
    role: "Celery worker (ejecución de tareas)",
    port: null,
    ramMib: workerMemoryGib * 1024 + WORKER_RAM_OVERHEAD_MIB,
  }));

  const services: PreviewService[] = [...INFRA_SERVICES, ...APP_SERVICES, ...workers];

  const volumes = baseVolumes(config);

  const totalRamMib = services.reduce((sum, s) => sum + s.ramMib, 0);
  const totalDiskGib = volumes.reduce((sum, v) => sum + v.diskGib, 0);

  return {
    services,
    volumes,
    estimatedRamGib: roundUp1(totalRamMib / 1024),
    estimatedDiskGib: totalDiskGib,
    gpuEnabled,
  };
}

// ---------------------------------------------------------------------------
// Secret masking for the config review. Secrets are NEVER shown in plaintext on
// the summary screen — we render a fixed mask if a value was provided, or an
// "(sin definir)" marker if not. The mask never reveals length.
// ---------------------------------------------------------------------------
const MASK = "••••••••";

/** Mask a secret for display: a fixed dot mask if set, a marker if empty. */
export function maskSecret(value: string): string {
  return value.trim().length > 0 ? MASK : "(sin definir)";
}

/** Whether a secret-bearing field has a value (drives the "definida" badge). */
export function isSecretSet(value: string): boolean {
  return value.trim().length > 0;
}

/** A single config row for the review table (label + safe display value). */
export interface ConfigRow {
  readonly label: string;
  readonly value: string;
  /** True when this row holds a (masked) secret. */
  readonly secret?: boolean;
}

/** A labelled group of config rows for the review screen. */
export interface ConfigGroup {
  readonly id: string;
  readonly title: string;
  readonly rows: readonly ConfigRow[];
}

const ENVIRONMENT_LABEL: Readonly<Record<InstallerConfig["system"]["environment"], string>> = {
  development: "Desarrollo",
  staging: "Staging",
  production: "Producción",
};

/**
 * Flatten the captured config into labelled groups for the review screen, with
 * every secret masked. This is the ONLY place the summary reads config values,
 * so masking is centralised here and cannot be bypassed.
 */
export function buildConfigGroups(config: InstallerConfig): readonly ConfigGroup[] {
  const { system, resources, storage, providers, tenant } = config;

  const providerRows: ConfigRow[] = [];
  if (providers.claudeSdk.enabled) {
    providerRows.push({
      label: "Claude Agent SDK · token OAuth",
      value: maskSecret(providers.claudeSdk.oauthToken),
      secret: true,
    });
  }
  if (providers.copilot.enabled) {
    providerRows.push({
      label: "GitHub Copilot · token OAuth",
      value: maskSecret(providers.copilot.oauthToken),
      secret: true,
    });
  }
  if (providers.azureFoundry.enabled) {
    providerRows.push({
      label: "Azure AI Foundry · endpoint APIM",
      value: providers.azureFoundry.apimEndpoint.trim() || "(sin definir)",
    });
    providerRows.push({
      label: "Azure AI Foundry · API key",
      value: maskSecret(providers.azureFoundry.apiKey),
      secret: true,
    });
  }
  if (providers.ollama.enabled) {
    providerRows.push({
      label: "Ollama · endpoint",
      value: providers.ollama.endpoint.trim() || "(sin definir)",
    });
  }
  if (providerRows.length === 0) {
    providerRows.push({ label: "Proveedores", value: "(ninguno habilitado)" });
  }

  return [
    {
      id: "system",
      title: "Sistema",
      rows: [
        { label: "Dominio", value: system.domain.trim() || "(sin definir)" },
        { label: "Entorno", value: ENVIRONMENT_LABEL[system.environment] },
      ],
    },
    {
      id: "resources",
      title: "Recursos",
      rows: [
        { label: "Réplicas de worker", value: String(resources.workerReplicas) },
        { label: "Memoria por worker", value: `${resources.workerMemoryGib} GiB` },
        { label: "GPU", value: resources.gpuEnabled ? "Habilitada" : "Deshabilitada" },
      ],
    },
    {
      id: "storage",
      title: "Almacenamiento",
      rows: [
        { label: "Ruta de datos", value: storage.dataRoot.trim() || "(sin definir)" },
        { label: "Bucket de MinIO", value: storage.minioBucket.trim() || "(sin definir)" },
        { label: "Access key de MinIO", value: storage.minioAccessKey.trim() || "(sin definir)" },
        { label: "Secret key de MinIO", value: maskSecret(storage.minioSecretKey), secret: true },
      ],
    },
    {
      id: "providers",
      title: "Proveedores LLM",
      rows: providerRows,
    },
    {
      id: "tenant",
      title: "Tenant inicial",
      rows: [
        { label: "Nombre del tenant", value: tenant.tenantName.trim() || "(sin definir)" },
        { label: "Email del administrador", value: tenant.adminEmail.trim() || "(sin definir)" },
      ],
    },
  ];
}
