"use client";

/**
 * DocDiffView — the docs visor's "compare versions" pane (Plan 07 task_07_16).
 *
 * Given the open doc (`projectId` + `path`), lets the user pick two git
 * commit-ish refs (a *base* and a *head*) and renders the unified diff of that
 * `.md` between them via `GET /projects/{id}/docs/diff?path=&base=&head=`. The
 * endpoint is RBAC/RLS-gated server-side (a project the user can't see is a 404
 * surfaced here as an error), classifies each line, and returns add/remove
 * counts; we hand the classified lines to {@link DocDiffRenderer}.
 *
 * Refs are free text (the backend accepts any commit-ish — tag, branch, SHA,
 * `HEAD~1`, …) and validated/injection-checked server-side, so we only block
 * empty submits client-side. Defaults `HEAD~1`→`HEAD` cover the common "what
 * changed last" case.
 *
 * States handled: nothing selected (empty), idle (refs entered, not run yet),
 * loading, error (with the API message), unchanged (identical across refs), and
 * the rendered diff. The query is only enabled after an explicit submit so we
 * never fire a request for half-typed refs.
 */

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, GitCompare } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { ApiError } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";
import { fetchDocDiff, type DocDiff } from "@/lib/docs-api";

import { DocDiffRenderer } from "./doc-diff-renderer";

const DEFAULT_BASE = "HEAD~1";
const DEFAULT_HEAD = "HEAD";

interface DocDiffViewProps {
  projectId: string | null;
  path: string | null;
}

export function DocDiffView({ projectId, path }: DocDiffViewProps) {
  const t = useT("docs");
  const [base, setBase] = useState(DEFAULT_BASE);
  const [head, setHead] = useState(DEFAULT_HEAD);
  // The refs we last *submitted* — the query keys off these, not the live
  // inputs, so typing never fires a request until the user compares.
  const [submitted, setSubmitted] = useState<{ base: string; head: string } | null>(null);

  // Reset the submitted comparison when the open doc changes: a diff for the
  // previous file must not linger over a different document.
  useEffect(() => {
    setSubmitted(null);
  }, [projectId, path]);

  const docSelected = Boolean(projectId && path);
  const enabled = docSelected && submitted !== null;

  const diffQuery = useQuery<DocDiff>({
    queryKey: ["docs-diff", projectId, path, submitted?.base, submitted?.head],
    queryFn: ({ signal }) =>
      // All four are non-null whenever `enabled`.
      fetchDocDiff(
        projectId as string,
        path as string,
        (submitted as { base: string; head: string }).base,
        (submitted as { base: string; head: string }).head,
        signal,
      ),
    enabled,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
    retry: false,
  });

  if (!docSelected) {
    return (
      <div
        className="flex h-full flex-col items-center justify-center text-center"
        data-testid="docs-diff-empty"
      >
        <GitCompare className="text-muted-foreground/50 mb-3 h-10 w-10" aria-hidden="true" />
        <p className="text-muted-foreground text-sm">{t("diffEmpty")}</p>
      </div>
    );
  }

  const onSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmedBase = base.trim();
    const trimmedHead = head.trim();
    if (trimmedBase.length === 0 || trimmedHead.length === 0) return;
    setSubmitted({ base: trimmedBase, head: trimmedHead });
  };

  return (
    <div data-testid="docs-diff-view">
      <form
        onSubmit={onSubmit}
        className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end"
        data-testid="docs-diff-form"
      >
        <div className="flex flex-1 flex-col gap-1">
          <Label htmlFor="docs-diff-base">{t("diffBaseLabel")}</Label>
          <Input
            id="docs-diff-base"
            value={base}
            onChange={(event) => setBase(event.target.value)}
            placeholder={DEFAULT_BASE}
            className="font-mono text-xs"
            aria-label={t("diffBaseAria")}
            data-testid="docs-diff-base-input"
          />
        </div>
        <ArrowRight
          className="text-muted-foreground hidden h-4 w-4 shrink-0 sm:mb-3 sm:block"
          aria-hidden="true"
        />
        <div className="flex flex-1 flex-col gap-1">
          <Label htmlFor="docs-diff-head">{t("diffHeadLabel")}</Label>
          <Input
            id="docs-diff-head"
            value={head}
            onChange={(event) => setHead(event.target.value)}
            placeholder={DEFAULT_HEAD}
            className="font-mono text-xs"
            aria-label={t("diffHeadAria")}
            data-testid="docs-diff-head-input"
          />
        </div>
        <Button
          type="submit"
          disabled={base.trim().length === 0 || head.trim().length === 0}
          data-testid="docs-diff-submit"
        >
          <GitCompare className="mr-1.5 h-4 w-4" aria-hidden="true" />
          {t("diffSubmit")}
        </Button>
      </form>

      <DiffResult
        enabled={enabled}
        isFetching={diffQuery.isFetching}
        isError={diffQuery.isError}
        error={diffQuery.error}
        data={diffQuery.data}
      />
    </div>
  );
}

function DiffResult({
  enabled,
  isFetching,
  isError,
  error,
  data,
}: {
  enabled: boolean;
  isFetching: boolean;
  isError: boolean;
  error: unknown;
  data: DocDiff | undefined;
}) {
  const t = useT("docs");
  const errorText = useErrorText();
  if (!enabled) {
    return (
      <p
        className="text-muted-foreground rounded-lg border border-dashed px-4 py-6 text-center text-sm"
        data-testid="docs-diff-idle"
      >
        {t("diffIdle")}
      </p>
    );
  }

  if (isFetching) {
    return (
      <div
        className="text-muted-foreground flex items-center gap-2 py-8 text-sm"
        data-testid="docs-diff-loading"
      >
        <Spinner className="h-4 w-4" />
        {t("diffLoading")}
      </div>
    );
  }

  if (isError) {
    return (
      <div
        className="border-destructive/40 bg-destructive/5 text-destructive rounded-lg border p-4 text-sm"
        data-testid="docs-diff-error"
      >
        {error instanceof ApiError && error.status === 404 ? t("docNotFound") : errorText(error)}
      </div>
    );
  }

  if (!data) return null;

  if (data.unchanged || data.lines.length === 0) {
    return (
      <p
        className="text-muted-foreground rounded-lg border px-4 py-6 text-center text-sm"
        data-testid="docs-diff-unchanged"
      >
        {t("diffUnchanged")} <code className="font-mono text-xs">{data.base_ref}</code>
        {" \u2192 "}
        <code className="font-mono text-xs">{data.head_ref}</code>.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3" data-testid="docs-diff-result">
      <div className="flex items-center gap-2 text-xs">
        <Badge variant="success" data-testid="docs-diff-added-count">
          +{data.added}
        </Badge>
        <Badge variant="danger" data-testid="docs-diff-removed-count">
          -{data.removed}
        </Badge>
        <span className="text-muted-foreground font-mono">
          {data.base_ref} → {data.head_ref}
        </span>
      </div>
      <DocDiffRenderer lines={data.lines} />
    </div>
  );
}
