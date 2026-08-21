"use client";

/**
 * `GET /runtime-templates` consumer (Plan 06.18 task_06_18_11).
 *
 * The runtime-template catalog is served by the backend
 * (`api_server.routers.runtimes`, task_06_18_08) so the admin-panel never
 * hardcodes it again. Before 06.18 the project Commands screen and the
 * Dep-cache screen each kept their OWN array (14 ids vs 12 ids, with
 * invented labels), and they diverged. This hook is the single client-side
 * source: both screens read the same list, in the catalog's declared
 * insertion order, and render the bilingual `label` the endpoint serves —
 * never a raw slug.
 *
 * The response mirrors `api_server.routers.runtimes.RuntimeTemplateDto`.
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { apiFetch, type ApiError } from "@/lib/api";
import { pickLang } from "@/lib/i18n";
import type { Lang } from "@/lib/lang-context";

/** ES + EN display names for a runtime template (served, never invented). */
export interface RuntimeLabel {
  es: string;
  en: string;
}

/** JSON projection of `shared_test_runtimes.types.RuntimeTemplate`. */
export interface RuntimeTemplateDto {
  /** The value persisted to `Project.default_runtime_template`. */
  id: string;
  label: RuntimeLabel;
  /** Container path the shared dep-cache mounts at; `null` = no cache. */
  dep_cache_mount: string | null;
  /** Default container network policy: none | restricted | open. */
  network_policy: string;
}

/** The bilingual label for a runtime template in the active language. */
export function runtimeLabel(template: RuntimeTemplateDto, lang: Lang): string {
  return pickLang(lang, template.label);
}

/**
 * Fetch the platform runtime-template catalog. Tenant-agnostic and stable
 * (changes only when an ADR adds/removes a template), so we cache it for
 * the session and never refetch on focus.
 */
export function useRuntimeTemplates(): UseQueryResult<RuntimeTemplateDto[], ApiError> {
  return useQuery<RuntimeTemplateDto[], ApiError>({
    queryKey: ["runtime-templates"],
    queryFn: () => apiFetch<RuntimeTemplateDto[]>("/runtime-templates"),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
}
