"use client";

/**
 * «Integraciones» del proyecto (`task_mk_20`, MK-05): las anclas de Jira y
 * Confluence —clave de proyecto + epic padre, espacio + página raíz— que el run
 * recibe en su preámbulo (`task_mk_21`) para trabajar bajo ellas sin que nadie
 * las repita en cada plan.
 *
 * Sin secretos: aquí sólo van identificadores públicos. Las credenciales viven en
 * el servidor MCP del proyecto (Vault/OAuth), que se configura en «MCP servers».
 * La lógica de forma ↔ payload y los problemas de formato viven en el módulo puro
 * `lib/project-integrations.ts`, espejo del esquema del backend.
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plug } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLangOptional } from "@/lib/lang-context";
import {
  integrationsProblems,
  toForm,
  toPayload,
  type IntegrationsForm,
  type IntegrationsValue,
} from "@/lib/project-integrations";
import { useErrorText } from "@/lib/use-error-text";

export function ProjectIntegrationsSection({
  projectId,
  value,
}: {
  projectId: string;
  value: IntegrationsValue | null | undefined;
}) {
  const queryClient = useQueryClient();
  const t = useT("projectIntegrations");
  const lang = useLangOptional();
  const errorText = useErrorText();
  const [form, setForm] = useState<IntegrationsForm>(() => toForm(value));
  const [saved, setSaved] = useState(false);

  const save = useMutation({
    mutationFn: () => apiFetch(`/projects/${projectId}`, { method: "PUT", body: toPayload(form) }),
    onSuccess: () => {
      setSaved(true);
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });

  function patch(change: Partial<IntegrationsForm>) {
    setSaved(false);
    save.reset();
    setForm((prev) => ({ ...prev, ...change }));
  }

  const problems = integrationsProblems(form, lang);

  return (
    <Card data-testid="project-integrations-section">
      <CardHeader className="flex flex-row items-center gap-2">
        <Plug className="text-muted-foreground h-5 w-5" />
        <CardTitle>{t("title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <p className="text-muted-foreground text-sm">{t("description")}</p>

        <section className="space-y-2">
          <h3 className="text-sm font-semibold">{t("jiraHeading")}</h3>
          <p className="text-muted-foreground text-sm">{t("jiraDescription")}</p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="integrations-jira-project-key">{t("jiraProjectKeyLabel")}</Label>
              <Input
                id="integrations-jira-project-key"
                data-testid="integrations-jira-project-key"
                placeholder="PLAT"
                value={form.jiraProjectKey}
                onChange={(e) => patch({ jiraProjectKey: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="integrations-jira-parent">{t("jiraParentIssueKeyLabel")}</Label>
              <Input
                id="integrations-jira-parent"
                data-testid="integrations-jira-parent-issue-key"
                placeholder="PLAT-120"
                value={form.jiraParentIssueKey}
                onChange={(e) => patch({ jiraParentIssueKey: e.target.value })}
              />
              <p className="text-muted-foreground text-xs">{t("jiraParentIssueKeyHelp")}</p>
            </div>
          </div>
        </section>

        <section className="space-y-2">
          <h3 className="text-sm font-semibold">{t("confluenceHeading")}</h3>
          <p className="text-muted-foreground text-sm">{t("confluenceDescription")}</p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="integrations-confluence-space">{t("confluenceSpaceKeyLabel")}</Label>
              <Input
                id="integrations-confluence-space"
                data-testid="integrations-confluence-space-key"
                placeholder="ENG"
                value={form.confluenceSpaceKey}
                onChange={(e) => patch({ confluenceSpaceKey: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="integrations-confluence-root">{t("confluenceRootPageIdLabel")}</Label>
              <Input
                id="integrations-confluence-root"
                data-testid="integrations-confluence-root-page-id"
                inputMode="numeric"
                placeholder="123456"
                value={form.confluenceRootPageId}
                onChange={(e) => patch({ confluenceRootPageId: e.target.value })}
              />
              <p className="text-muted-foreground text-xs">{t("confluenceRootPageIdHelp")}</p>
            </div>
          </div>
        </section>

        <p className="text-muted-foreground text-xs">{t("noSecretsNote")}</p>

        {problems.length > 0 ? (
          <ul
            className="bg-warning-soft text-warning-soft-foreground list-disc space-y-0.5 rounded p-3 pl-7 text-xs"
            data-testid="integrations-problems"
          >
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        ) : null}

        <div className="flex items-center gap-3">
          <Button
            onClick={() => save.mutate()}
            disabled={problems.length > 0 || save.isPending}
            data-testid="integrations-save"
          >
            {save.isPending ? t("saving") : t("save")}
          </Button>
          {saved ? <p className="text-success text-xs">{t("saved")}</p> : null}
          {save.isError ? (
            <p className="text-destructive text-xs" data-testid="integrations-error">
              {errorText(save.error)}
            </p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
