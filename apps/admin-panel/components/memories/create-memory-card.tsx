"use client";

import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { ProjectCombobox } from "@/components/ui/project-combobox";
import { TeamCombobox } from "@/components/ui/team-combobox";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import { type MemoryResponse, type MemoryScope, type MemoryType } from "./types";

// Create form
export function CreateMemoryCard({ onCreated }: { onCreated: () => void }) {
  const t = useT("memories");
  const errorText = useErrorText();
  const [content, setContent] = useState("");
  const [scope, setScope] = useState<MemoryScope>("team_shared");
  const [type, setType] = useState<MemoryType>("semantic");
  const [teamId, setTeamId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [tagsRaw, setTagsRaw] = useState("");

  const parsedTags = useMemo(
    () =>
      tagsRaw
        .split(",")
        .map((t) => t.trim())
        .filter((t) => t.length > 0),
    [tagsRaw],
  );

  const mutation = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = {
        content,
        scope,
        type,
        tags: parsedTags,
      };
      if (scope === "team_shared") body.team_id = teamId;
      if (scope === "project_shared") body.project_id = projectId;
      return apiFetch<MemoryResponse>("/memories", { method: "POST", body });
    },
    onSuccess: () => {
      setContent("");
      setTagsRaw("");
      onCreated();
    },
  });

  const canSubmit =
    content.trim().length > 0 &&
    !mutation.isPending &&
    (scope !== "team_shared" || teamId.trim().length > 0) &&
    (scope !== "project_shared" || projectId.trim().length > 0);

  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle>{t("createTitle")}</CardTitle>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-3"
          data-testid="memory-create-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (canSubmit) mutation.mutate();
          }}
        >
          <div className="flex flex-col gap-1.5">
            <Label>{t("fieldContent")}</Label>
            <MarkdownTextarea
              value={content}
              onChange={setContent}
              rows={5}
              data-testid="memory-content-input"
            />
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="memory-scope-select">{t("fieldScope")}</Label>
              <select
                id="memory-scope-select"
                value={scope}
                onChange={(e) => setScope(e.target.value as MemoryScope)}
                data-testid="memory-scope-select"
                className="bg-background border-muted w-full rounded border px-2 py-1 text-sm"
              >
                <option value="private">{t("scopePrivate")}</option>
                <option value="team_shared">{t("scopeTeam")}</option>
                <option value="project_shared">{t("scopeProject")}</option>
                <option value="global">{t("scopeGlobal")}</option>
              </select>
            </div>
            <div>
              <Label htmlFor="memory-type-select">{t("fieldType")}</Label>
              <select
                id="memory-type-select"
                value={type}
                onChange={(e) => setType(e.target.value as MemoryType)}
                data-testid="memory-type-select"
                className="bg-background border-muted w-full rounded border px-2 py-1 text-sm"
              >
                <option value="semantic">{t("typeSemantic")}</option>
                <option value="episodic">{t("typeEpisodic")}</option>
              </select>
            </div>
          </div>

          {scope === "team_shared" ? (
            <div className="flex flex-col gap-1.5">
              <Label>{t("fieldTeam")}</Label>
              <TeamCombobox
                value={teamId || null}
                onChange={(id) => setTeamId(id ?? "")}
                data-testid="memory-team-id-input"
              />
            </div>
          ) : null}

          {scope === "project_shared" ? (
            <div className="flex flex-col gap-1.5">
              <Label>{t("fieldProject")}</Label>
              <ProjectCombobox
                value={projectId || null}
                onChange={(id) => setProjectId(id ?? "")}
                data-testid="memory-project-id-input"
              />
            </div>
          ) : null}

          <div>
            <Label htmlFor="memory-tags-input">{t("fieldTags")}</Label>
            <Input
              id="memory-tags-input"
              data-testid="memory-tags-input"
              value={tagsRaw}
              onChange={(e) => setTagsRaw(e.target.value)}
              placeholder={t("tagsPlaceholder")}
            />
          </div>

          {mutation.isError ? (
            <p className="text-destructive text-xs" data-testid="memory-create-error">
              {errorText(mutation.error)}
            </p>
          ) : null}

          <div className="flex justify-end">
            <Button type="submit" disabled={!canSubmit} data-testid="memory-create-submit">
              {t("submit")}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
