"use client";

/**
 * Documentos de una base dentro de su tarjeta (`task_ui_21`): fila con estado,
 * enlace de progreso, reindexado y borrado, y el diálogo de subida directa a una
 * base concreta. Salió de la página del proyecto para que ésta sea sólo la
 * pantalla; el texto sigue en el namespace `projectKbs`.
 */

import { useState } from "react";
import Link from "next/link";
import { useMutation } from "@tanstack/react-query";
import { RefreshCw, Trash2 } from "lucide-react";

import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiFetch } from "@/lib/api";
import { CSRF_HEADER, getCsrfToken } from "@/lib/auth";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import type { DocumentStatus, KbDocument as KBDocument } from "./kb-types";

const STATUS_VARIANT: Record<DocumentStatus, BadgeVariant> = {
  pending: "muted",
  processing: "warning",
  indexed: "success",
  failed: "danger",
};

const STATUS_LABEL_KEY: Record<
  DocumentStatus,
  "statusPending" | "statusProcessing" | "statusIndexed" | "statusFailed"
> = {
  pending: "statusPending",
  processing: "statusProcessing",
  indexed: "statusIndexed",
  failed: "statusFailed",
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// --------------------------------------------------------------------------
// Per-document row with status, link to ingestion page, delete
// --------------------------------------------------------------------------
export function DocumentList({
  kbId,
  documents,
  onChanged,
}: {
  kbId: string;
  documents: KBDocument[];
  onChanged: () => void;
}) {
  return (
    <ul className="space-y-1.5" data-testid={`kb-docs-list-${kbId}`}>
      {documents.map((doc) => (
        <DocumentRow key={doc.id} kbId={kbId} doc={doc} onChanged={onChanged} />
      ))}
    </ul>
  );
}

function DocumentRow({
  kbId,
  doc,
  onChanged,
}: {
  kbId: string;
  doc: KBDocument;
  onChanged: () => void;
}) {
  const t = useT("projectKbs");
  const deleteMutation = useMutation({
    mutationFn: () =>
      apiFetch<void>(`/knowledge-bases/${kbId}/documents/${doc.id}`, {
        method: "DELETE",
      }),
    onSuccess: onChanged,
  });

  const reindexMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/knowledge-bases/${kbId}/documents/${doc.id}/reindex`, { method: "POST" }),
    onSuccess: onChanged,
  });

  const variant = STATUS_VARIANT[doc.status];
  const canReindex = doc.status === "failed" || doc.status === "indexed";

  return (
    <li
      className="border-muted flex items-center justify-between gap-3 rounded border px-3 py-2 text-sm"
      data-testid={`kb-doc-${doc.id}`}
      data-status={doc.status}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-wide">
          <Badge variant={variant} data-testid={`kb-doc-status-${doc.id}`}>
            {t(STATUS_LABEL_KEY[doc.status])}
          </Badge>
          <span className="text-muted-foreground">
            {doc.source_mime_type} · {formatBytes(doc.source_size_bytes)}
            {doc.page_count > 0 ? ` · ${doc.page_count} pp.` : ""}
          </span>
        </div>
        <p className="mt-0.5 truncate">{doc.title}</p>
        {doc.error_message ? (
          <p className="text-destructive mt-0.5 text-xs">{doc.error_message}</p>
        ) : null}
      </div>
      <div className="flex items-center gap-1">
        <Link
          href={`/admin/documents/${doc.id}/ingestion`}
          data-testid={`kb-doc-ingestion-link-${doc.id}`}
          className="text-muted-foreground hover:text-foreground text-xs underline"
        >
          {t("progress")}
        </Link>
        {canReindex ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() => reindexMutation.mutate()}
            disabled={reindexMutation.isPending}
            data-testid={`kb-doc-reindex-${doc.id}`}
            title={t("reindexTitle")}
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
        ) : null}
        <Button
          variant="outline"
          size="sm"
          onClick={() => deleteMutation.mutate()}
          disabled={deleteMutation.isPending}
          data-testid={`kb-doc-delete-${doc.id}`}
          aria-label={t("delete")}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>
    </li>
  );
}

// --------------------------------------------------------------------------
// Upload dialog
// --------------------------------------------------------------------------
export function UploadDialog({
  open,
  onOpenChange,
  kbId,
  onUploaded,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  kbId: string;
  onUploaded: () => void;
}) {
  const errorText = useErrorText();
  const t = useT("projectKbs");
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("file is required");
      const formData = new FormData();
      formData.append("file", file);
      if (title.trim()) formData.append("title", title.trim());
      const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";
      // Multipart upload, so it cannot go through `apiFetch` (which JSON-encodes
      // the body). It repeats the same session contract by hand: cookie via
      // `credentials` + the CSRF proof, since this is a POST (ADR 0133).
      const csrf = getCsrfToken();
      const response = await fetch(`${apiUrl}/knowledge-bases/${kbId}/documents`, {
        method: "POST",
        credentials: "include",
        headers: csrf ? { [CSRF_HEADER]: csrf } : {},
        body: formData,
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `HTTP ${response.status}`);
      }
      return response.json();
    },
    onSuccess: () => {
      setFile(null);
      setTitle("");
      setErrorMsg(null);
      onUploaded();
      onOpenChange(false);
    },
    onError: (err) => {
      setErrorMsg(errorText(err));
    },
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!mutation.isPending) onOpenChange(next);
      }}
    >
      <DialogContent data-testid="kb-upload-dialog">
        <DialogHeader>
          <DialogTitle>{t("dialogTitle")}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <div>
            <Label htmlFor="kb-upload-file">{t("fileLabel")}</Label>
            <Input
              id="kb-upload-file"
              data-testid="kb-upload-file"
              type="file"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              accept=".pdf,.docx,.md,.txt,.html,.wav,.mp3"
            />
          </div>
          <div>
            <Label htmlFor="kb-upload-title">{t("titleLabel")}</Label>
            <Input
              id="kb-upload-title"
              data-testid="kb-upload-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={t("titlePlaceholder")}
            />
          </div>
          {errorMsg ? (
            <p className="text-destructive text-xs" data-testid="kb-upload-error">
              {errorMsg}
            </p>
          ) : null}
        </DialogBody>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={mutation.isPending}
            data-testid="kb-upload-cancel"
          >
            {t("cancel")}
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={!file || mutation.isPending}
            data-testid="kb-upload-submit"
          >
            {mutation.isPending ? t("uploading") : t("upload")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
