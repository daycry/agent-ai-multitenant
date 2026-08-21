"use client";

// ADR 0099: visor read-only del DIFF DE CÓDIGO de la rama del plan — qué
// cambió respecto a su merge-base con la rama por defecto. Carga perezosa
// (solo al desplegar) contra GET /projects/{pid}/plans/{planId}/code-diff;
// reutiliza el renderer del diff de docs (mismas líneas clasificadas).

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, GitBranch } from "lucide-react";

import { DocDiffRenderer } from "@/app/admin/docs/doc-diff-renderer";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface CodeDiffFile {
  path: string;
  additions: number | null;
  deletions: number | null;
}

interface CodeDiffResponse {
  plan_branch: string;
  default_branch: string;
  unchanged: boolean;
  truncated: boolean;
  files: CodeDiffFile[];
  lines: { kind: "added" | "removed" | "hunk" | "context"; content: string }[];
}

export function PlanCodeDiffSection({ projectId, planId }: { projectId: string; planId: string }) {
  const t = useT("planDetail");
  const [open, setOpen] = useState(false);
  const diffQuery = useQuery<CodeDiffResponse, ApiError>({
    queryKey: ["plan-code-diff", planId],
    queryFn: () => apiFetch(`/projects/${projectId}/plans/${planId}/code-diff`),
    enabled: open,
    refetchOnWindowFocus: false,
    retry: false,
  });

  return (
    <Card className="mt-6" data-testid="plan-code-diff-section">
      <CardHeader>
        <button
          type="button"
          className="flex w-full items-center gap-2 text-left"
          onClick={() => setOpen((v) => !v)}
          data-testid="plan-code-diff-toggle"
        >
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          <GitBranch className="h-4 w-4" />
          <CardTitle className="flex-1">{t("codeDiffTitle")}</CardTitle>
        </button>
      </CardHeader>
      {open && (
        <CardContent className="space-y-3">
          {diffQuery.isLoading && (
            <p className="text-muted-foreground flex items-center gap-2 text-sm">
              <Spinner /> {t("codeDiffCalculating")}
            </p>
          )}
          {diffQuery.isError && (
            <p className="text-muted-foreground text-sm" data-testid="plan-code-diff-empty">
              {diffQuery.error.status === 404
                ? t("codeDiffNoBranch")
                : String(diffQuery.error.body ?? diffQuery.error)}
            </p>
          )}
          {diffQuery.data && diffQuery.data.unchanged && (
            <p className="text-muted-foreground text-sm" data-testid="plan-code-diff-unchanged">
              {t("codeDiffUnchangedBefore")} <code>{diffQuery.data.plan_branch}</code>{" "}
              {t("codeDiffUnchangedMiddle")} <code>{diffQuery.data.default_branch}</code>.
            </p>
          )}
          {diffQuery.data && !diffQuery.data.unchanged && (
            <>
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <Badge variant="muted">
                  {diffQuery.data.plan_branch} → {diffQuery.data.default_branch}
                </Badge>
                <Badge variant="info">
                  {t("codeDiffFiles", { count: diffQuery.data.files.length })}
                </Badge>
                {diffQuery.data.truncated && (
                  <Badge variant="warning" data-testid="plan-code-diff-truncated">
                    {t("codeDiffTruncated")}
                  </Badge>
                )}
              </div>
              <ul
                className="text-muted-foreground max-h-40 space-y-0.5 overflow-y-auto font-mono text-xs"
                data-testid="plan-code-diff-files"
              >
                {diffQuery.data.files.map((f) => (
                  <li key={f.path}>
                    <span className="text-success-soft-foreground">+{f.additions ?? "·"}</span>{" "}
                    <span className="text-danger-soft-foreground">−{f.deletions ?? "·"}</span>{" "}
                    {f.path}
                  </li>
                ))}
              </ul>
              <DocDiffRenderer
                lines={diffQuery.data.lines.map((ln) => ({
                  kind: ln.kind,
                  content: ln.content,
                }))}
              />
            </>
          )}
        </CardContent>
      )}
    </Card>
  );
}
