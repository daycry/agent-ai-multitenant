"use client";

/**
 * Settings index (Plan 06.7 task_06_7_06).
 *
 * Lee el registry desde GET /tenant-settings/_registry y renderiza
 * una card por categoría con su icono lucide-react. Click en una
 * card:
 *   - Si tiene `external_page`, lleva a esa URL (legacy hourly-rate).
 *   - Si no, lleva a /admin/settings/{category} (auto-generada).
 *
 * Categorías iniciales (registry):
 *   memories → Brain
 *   costs    → Coins (external_page → /admin/settings/hourly-rate)
 */

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  Brain,
  Coins,
  Cpu,
  FileText,
  Lock,
  Settings as SettingsIcon,
  Shield,
  Sparkles,
  type LucideIcon,
} from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";

// String → component mapping. Add more as the registry grows.
const ICONS: Record<string, LucideIcon> = {
  Brain,
  Coins,
  Cpu,
  FileText,
  Lock,
  Shield,
  Sparkles,
};

interface RegistrySettingDef {
  type: "float" | "int" | "string" | "bool";
  default: unknown;
  label_es: string;
  description_es: string;
  min_value: number | null;
  max_value: number | null;
}

interface RegistryCategoryDef {
  label_es: string;
  icon: string;
  description_es: string;
  external_page: string | null;
  settings: Record<string, RegistrySettingDef>;
}

interface RegistryResponse {
  categories: Record<string, RegistryCategoryDef>;
}

export default function SettingsIndexPage() {
  const { data, isLoading, isError, error } = useQuery<RegistryResponse, ApiError>({
    queryKey: ["tenant-settings", "_registry"],
    queryFn: () => apiFetch<RegistryResponse>("/tenant-settings/_registry"),
    refetchOnWindowFocus: false,
  });

  return (
    <div
      className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="settings-index"
    >
      <PageHeader
        icon={<SettingsIcon className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Settings"
        description="Configuración del tenant — agrupada por categoría."
      />

      {isLoading && (
        <p className="text-muted-foreground text-sm" data-testid="settings-loading">
          Cargando registry…
        </p>
      )}

      {isError && (
        <Card className="p-4" data-testid="settings-error">
          <p className="text-danger-soft-foreground text-sm">
            No se pudo cargar el registry: {error?.message ?? "error desconocido"}.
          </p>
        </Card>
      )}

      {data && (
        <div
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
          data-testid="settings-categories-grid"
        >
          {Object.entries(data.categories).map(([category, def]) => (
            <CategoryCard key={category} category={category} def={def} />
          ))}
        </div>
      )}
    </div>
  );
}

function CategoryCard({ category, def }: { category: string; def: RegistryCategoryDef }) {
  const Icon = ICONS[def.icon] ?? SettingsIcon;
  const href = def.external_page ?? `/admin/settings/${category}`;
  const settingCount = Object.keys(def.settings).length;

  return (
    <Link href={href} data-testid={`settings-category-link-${category}`} className="block">
      <Card
        data-testid={`settings-category-${category}`}
        className="hover:border-primary/40 h-full cursor-pointer transition-colors"
      >
        <CardHeader className="flex flex-row items-center gap-3 space-y-0 pb-2">
          <div className="bg-primary/10 text-primary flex h-10 w-10 items-center justify-center rounded-lg">
            <Icon className="h-5 w-5" />
          </div>
          <div>
            <CardTitle className="text-base">{def.label_es}</CardTitle>
            {def.external_page ? (
              <p
                className="text-muted-foreground text-[10px] uppercase tracking-wide"
                data-testid={`settings-category-${category}-external`}
              >
                página dedicada
              </p>
            ) : (
              <p
                className="text-muted-foreground text-[10px] uppercase tracking-wide"
                data-testid={`settings-category-${category}-count`}
              >
                {settingCount} ajuste{settingCount === 1 ? "" : "s"}
              </p>
            )}
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">{def.description_es}</p>
        </CardContent>
      </Card>
    </Link>
  );
}
