"use client";

/**
 * KbDocumentsPanel — documentos de una KB con subida, estado, progreso,
 * re-index y borrado (Plan 06.11 task_06_11_05).
 *
 * Reutilizable: lo monta la página general `/admin/knowledge-bases`
 * (expander por KB) para que subir documentos NO requiera pasar por la
 * sub-página de un proyecto. La sub-página del proyecto mantiene su
 * propio render (con sus testids del e2e) — este componente es la
 * superficie tenant-wide.
 *
 * La subida va por `fetch` multipart (FormData) con el token de
 * localStorage, igual que el flujo del proyecto — `apiFetch` no maneja
 * FormData. El resto (list / delete / reindex) sí usa `apiFetch`.
 */

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Trash2, UploadCloud } from "lucide-react";

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
import { RoleGuard } from "@/components/ui/role-guard";
import { apiFetch } from "@/lib/api";

type DocumentStatus = "pending" | "processing" | "indexed" | "indexed_empty" | "failed";

interface KBDocument {
  id: string;
  kb_id: string;
  title: string;
  source_filename: string;
  source_mime_type: string;
  source_size_bytes: number;
  status: DocumentStatus;
  error_message: string | null;
  page_count: number;
  indexed_at: string | null;
  created_at: string;
  updated_at: string;
}

const STATUS_VARIANT: Record<DocumentStatus, BadgeVariant> = {
  pending: "muted",
  processing: "warning",
  indexed: "success",
  // task_06_17_05: "indexado vacío" (0 chunks) NO es verde — el agente no
  // puede recuperar nada del documento. Warning honesto, no success.
  indexed_empty: "warning",
  failed: "danger",
};

// KB Q6 (propuesta simplificación 2026-07-12): lenguaje de persona, no jerga
// del pipeline. El estado técnico sigue disponible en data-status/tooltip.
const STATUS_LABEL: Record<DocumentStatus, string> = {
  pending: "Procesando…",
  processing: "Procesando…",
  indexed: "Listo",
  indexed_empty: "Sin contenido aprovechable",
  failed: "Error",
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function KbDocumentsPanel({ kbId }: { kbId: string }) {
  const queryClient = useQueryClient();
  const [uploadOpen, setUploadOpen] = useState(false);

  const docsQuery = useQuery({
    queryKey: ["kb-documents", kbId],
    queryFn: () => apiFetch<KBDocument[]>(`/knowledge-bases/${kbId}/documents`),
    refetchOnWindowFocus: false,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["kb-documents", kbId] });

  const docs = docsQuery.data ?? [];

  return (
    <div className="mt-2" data-testid={`kb-docs-panel-${kbId}`}>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
          Documentos ({docs.length})
        </span>
        <RoleGuard min="tenant_admin">
          <Button
            size="sm"
            variant="outline"
            onClick={() => setUploadOpen(true)}
            data-testid={`kb-docs-upload-open-${kbId}`}
          >
            <UploadCloud className="mr-1 h-3.5 w-3.5" />
            Subir documento
          </Button>
        </RoleGuard>
      </div>

      {docsQuery.isLoading ? (
        <p className="text-muted-foreground text-sm">Cargando documentos…</p>
      ) : docs.length === 0 ? (
        <p
          className="text-muted-foreground rounded border border-dashed px-3 py-4 text-center text-sm italic"
          data-testid={`kb-docs-empty-${kbId}`}
        >
          Esta KB aún no tiene documentos. Sube el primero para indexarlo.
        </p>
      ) : (
        <ul className="space-y-1.5" data-testid={`kb-docs-list-${kbId}`}>
          {docs.map((doc) => (
            <DocumentRow key={doc.id} kbId={kbId} doc={doc} onChanged={invalidate} />
          ))}
        </ul>
      )}

      <UploadDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        kbId={kbId}
        onUploaded={invalidate}
      />
    </div>
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
  const deleteMutation = useMutation({
    mutationFn: () =>
      apiFetch<void>(`/knowledge-bases/${kbId}/documents/${doc.id}`, { method: "DELETE" }),
    onSuccess: onChanged,
  });

  const reindexMutation = useMutation({
    mutationFn: () =>
      apiFetch(`/knowledge-bases/${kbId}/documents/${doc.id}/reindex`, { method: "POST" }),
    onSuccess: onChanged,
  });

  // Re-index makes sense once the run finished (indexed → re-parse;
  // failed → retry; indexed_empty → re-parse, quizá tras subir un
  // original con texto). Hidden mid-flight (pending/processing).
  const canReindex =
    doc.status === "failed" || doc.status === "indexed" || doc.status === "indexed_empty";

  return (
    <li
      className="border-muted flex items-center justify-between gap-3 rounded border px-3 py-2 text-sm"
      data-testid={`kb-docs-row-${doc.id}`}
      data-status={doc.status}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-wide">
          <Badge variant={STATUS_VARIANT[doc.status]} data-testid={`kb-docs-status-${doc.id}`}>
            {STATUS_LABEL[doc.status]}
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
        {doc.status === "indexed_empty" ? (
          <p
            className="text-warning-soft-foreground mt-0.5 text-xs"
            data-testid={`kb-docs-empty-hint-${doc.id}`}
          >
            Procesado pero sin fragmentos (0 chunks): el agente no puede recuperar nada de este
            documento. Sube un original con texto seleccionable o reindexa.
          </p>
        ) : null}
      </div>
      <div className="flex items-center gap-1">
        <Link
          href={`/admin/documents/${doc.id}/ingestion`}
          className="text-muted-foreground hover:text-foreground text-xs underline"
          data-testid={`kb-docs-progress-${doc.id}`}
        >
          Progreso
        </Link>
        <RoleGuard min="tenant_admin">
          {canReindex ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => reindexMutation.mutate()}
              disabled={reindexMutation.isPending}
              data-testid={`kb-docs-reindex-${doc.id}`}
              title="Reindexar (vuelve a procesar el documento)"
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </Button>
          ) : null}
          <Button
            variant="outline"
            size="sm"
            onClick={() => deleteMutation.mutate()}
            disabled={deleteMutation.isPending}
            data-testid={`kb-docs-delete-${doc.id}`}
            aria-label="Eliminar"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </RoleGuard>
      </div>
    </li>
  );
}

function UploadDialog({
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
      const token = localStorage.getItem("agentic.token");
      const response = await fetch(`${apiUrl}/knowledge-bases/${kbId}/documents`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
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
    onError: (err) => setErrorMsg(err instanceof Error ? err.message : String(err)),
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!mutation.isPending) onOpenChange(next);
      }}
    >
      <DialogContent data-testid="kb-docs-upload-dialog">
        <DialogHeader>
          <DialogTitle>Subir documento a la KB</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <div>
            <Label htmlFor="kb-docs-upload-file">Archivo</Label>
            <Input
              id="kb-docs-upload-file"
              data-testid="kb-docs-upload-file"
              type="file"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              accept=".pdf,.docx,.md,.txt,.html,.wav,.mp3"
            />
          </div>
          <div>
            <Label htmlFor="kb-docs-upload-title">Título (opcional)</Label>
            <Input
              id="kb-docs-upload-title"
              data-testid="kb-docs-upload-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Por defecto: nombre del archivo"
            />
          </div>
          {errorMsg ? (
            <p className="text-destructive text-xs" data-testid="kb-docs-upload-error">
              {errorMsg}
            </p>
          ) : null}
        </DialogBody>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={mutation.isPending}
          >
            Cancelar
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={!file || mutation.isPending}
            data-testid="kb-docs-upload-submit"
          >
            {mutation.isPending ? "Subiendo…" : "Subir"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
