// Tipos, constantes y helpers puros del catálogo Modelos & Precios (tramo #9,
// extracción verbatim del monolito page.tsx — auditoría 2026-07-10). Espejan
// api_server.schemas.model_prices + price_sync; USD-canónico (sin knob de
// moneda). Sin JSX ni hooks: módulo .ts importable desde page y dialogs.

import { type BadgeVariant } from "@/components/ui/badge";

// ---------------------------------------------------------------------------
// Types — mirror api_server.schemas.model_prices + db.model_prices enums.
// USD-canonical: `currency` is always "USD"; there is no currency knob.
// ---------------------------------------------------------------------------
export type Modality = "text" | "vision" | "audio" | "embedding" | "image" | "rerank";
export type Unit = "per_1m_tokens" | "per_1k_tokens";
export type Source = "litellm" | "provider_api" | "manual";

export interface ModelPrice {
  id: string;
  provider: string;
  model_id: string;
  modality: string;
  input_price: string;
  output_price: string;
  cached_input_price: string | null;
  unit: string;
  currency: string;
  context_window: number | null;
  source: string;
  // task_11_2_06 — association to a configured platform provider
  // (llm_providers.id). NULL when the price is not associated.
  provider_id: string | null;
  effective_from: string;
  effective_to: string | null;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
}

// task_11_2_06 — the platform-global LLM providers (ADR 0028). Read from
// the System-Admin /admin/llm-providers surface only to map provider_id ->
// a human label + populate the "filter by provider" dropdown. Mirrors
// api_server.schemas.llm_providers.LLMProviderResponse (secret-free).
export interface LlmProvider {
  id: string;
  kind: string;
  display_name: string;
  is_active: boolean;
}

// plan price-sync-active-providers (task_psa_02) — map a configured provider
// `kind` (ADR 0021 closed catalogue) to the LiteLLM `litellm_provider` families
// its models appear under (ADR 0028). MUST stay in lockstep with the backend
// `KIND_TO_LITELLM_FAMILIES` (api_server.pricing.litellm_sync): the price sync
// derives the families it imports from the union of the ACTIVE providers' kinds.
// This is only a UI hint of the scope — the backend is the source of truth (and
// a System-Admin `price_sync.allowed_families` override can pin a different set).
const KIND_TO_LITELLM_FAMILIES: Record<string, string[]> = {
  claude_sdk: ["anthropic"],
  azure_foundry: ["azure", "azure_ai", "openai"],
  copilot: ["openai", "anthropic"],
  ollama: ["ollama"],
};

/** Union the LiteLLM families of the ACTIVE providers (sorted, de-duplicated). */
export function activeFamilies(providers: LlmProvider[]): string[] {
  const families = new Set<string>();
  for (const p of providers) {
    if (!p.is_active) continue;
    for (const fam of KIND_TO_LITELLM_FAMILIES[p.kind] ?? []) families.add(fam);
  }
  return [...families].sort();
}

// The typed skip reason the backend stamps on a feed entry whose family is not
// an active provider (api_server.pricing.litellm_sync.SKIP_FAMILY_NOT_ACTIVE).
export const SKIP_FAMILY_NOT_ACTIVE = "family_not_active";

// task_11_16 — dry-run diff + mandatory-confirmation apply.
// Mirror api_server.schemas.price_sync.{PriceSyncDiffResponse,PriceDiffRowResponse}.
type DiffStatus = "added" | "updated" | "unchanged" | "increased" | "removed";

interface PriceDiffRow {
  provider: string;
  model_id: string;
  modality: string;
  status: DiffStatus;
  source: string;
  old_input: string | null;
  new_input: string | null;
  old_output: string | null;
  new_output: string | null;
  old_cached_input: string | null;
  new_cached_input: string | null;
  input_pct: number | null;
  output_pct: number | null;
  manual_skipped: boolean;
}

export interface PriceSyncDiff {
  fetched: number;
  added: number;
  updated: number;
  unchanged: number;
  increased: number;
  removed: number;
  has_large_increase: boolean;
  rows: PriceDiffRow[];
  skipped: { model_key: string; reason: string }[];
}

export interface PriceSyncResult {
  fetched: number;
  created: number;
  updated: number;
  unchanged: number;
  changed: number;
}

export const MODALITIES: Modality[] = ["text", "vision", "audio", "embedding", "image", "rerank"];
export const UNITS: Unit[] = ["per_1m_tokens", "per_1k_tokens"];
export const SOURCES: Source[] = ["manual", "litellm", "provider_api"];

export const DIFF_STATUS_BADGE: Record<DiffStatus, BadgeVariant> = {
  added: "info",
  updated: "primary",
  unchanged: "muted",
  increased: "danger",
  removed: "warning",
};

export const DIFF_STATUS_LABEL: Record<DiffStatus, string> = {
  added: "nuevo",
  updated: "actualizado",
  unchanged: "sin cambios",
  increased: "subida >10%",
  removed: "descontinuado",
};

/** Format a fractional change (0.067 -> "+6.7%"); null -> "—". */
export function fmtPct(value: number | null): string {
  if (value === null) return "—";
  const pct = value * 100;
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toLocaleString("en-US", { maximumFractionDigits: 1 })}%`;
}

export const UNIT_LABEL: Record<string, string> = {
  per_1m_tokens: "por 1M tokens",
  per_1k_tokens: "por 1K tokens",
};

export const SOURCE_BADGE: Record<string, BadgeVariant> = {
  manual: "primary",
  litellm: "info",
  provider_api: "success",
};

/** Canonical USD price formatting; the catalog stores `Numeric(18,10)` strings. */
export function fmtUsd(value: string | null): string {
  if (value === null) return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return value;
  // Up to 6 significant decimals; trim trailing zeros for readability.
  return `$${n.toLocaleString("en-US", { maximumFractionDigits: 6 })}`;
}

export function fmtDate(iso: string | null): string {
  if (iso === null) return "—";
  return new Date(iso).toLocaleDateString("es-ES", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}
