"use client";

/**
 * task_04_24 — Knowledge Bases del proyecto.
 *
 * Lista las KBs granted al proyecto (vía /projects/{id}/knowledge-bases)
 * con sus documentos: estado, progreso (link al WebSocket de ingestión
 * de task_04_15), upload nuevo, delete.
 *
 * La creación de KBs en sí + grant a otros projects vive en el panel
 * admin general (`/admin/knowledge-bases`); aquí sólo administramos
 * lo que el proyecto ve.
 */

import { useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Library, RefreshCw, Trash2, UploadCloud } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { ProjectBreadcrumb } from "@/components/layout/breadcrumb";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { ApiError, apiFetch } from "@/lib/api";

// --------------------------------------------------------------------------
// Types (mirror the backend responses)
// --------------------------------------------------------------------------
interface KnowledgeBase {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  embedding_model_id: string;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

type DocumentStatus = "pending" | "processing" | "indexed" | "failed";

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
  failed: "danger",
};

const STATUS_LABEL: Record<DocumentStatus, string> = {
  pending: "Pendiente",
  processing: "Procesando",
  indexed: "Indexado",
  failed: "Fallido",
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function ProjectKnowledgeBasesPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;

  // KB Q3 (propuesta simplificación 2026-07-12): el catálogo completo del
  // tenant (incluye built-ins) para activar/desactivar con un clic — antes
  // había que descubrirlas y grantearlas desde la pantalla global.
  const catalogQuery = useQuery({
    queryKey: ["kb-catalog"],
    queryFn: () => apiFetch<KnowledgeBase[]>("/knowledge-bases"),
    refetchOnWindowFocus: false,
  });
  const kbsQuery = useQuery({
    queryKey: ["project-kbs", projectId],
    queryFn: () => apiFetch<KnowledgeBase[]>(`/projects/${projectId}/knowledge-bases`),
    refetchOnWindowFocus: false,
    enabled: Boolean(projectId),
  });

  return (
    <div
      className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="project-kbs-page"
    >
      <ProjectBreadcrumb projectId={projectId} current="Knowledge Bases" />
      <PageHeader
        icon={<Library className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Knowledge Bases del proyecto"
        description="Las KBs granted al proyecto, sus documentos y el progreso de la ingestión."
        data-testid="project-kbs-header"
      />

      {/* KB Q2 (propuesta 2026-07-12): «Añadir conocimiento» como flujo
          primario — un solo paso: elige ficheros → se crea (lazy) la KB
          implícita «Documentos de {proyecto}» ya granteada (Q1 project_id
          auto-grant) → ingesta. La gestión avanzada queda intacta debajo. */}
      <AddKnowledgeSection
        projectId={projectId}
        grantedKbs={kbsQuery.data ?? []}
        onDone={() => {
          void kbsQuery.refetch();
        }}
      />

      {kbsQuery.isLoading ? (
        <p className="text-muted-foreground mt-6 text-sm">Cargando…</p>
      ) : kbsQuery.isError ? (
        <p className="text-destructive mt-6 text-sm" data-testid="project-kbs-error">
          {kbsQuery.error instanceof ApiError ? kbsQuery.error.body : String(kbsQuery.error)}
        </p>
      ) : (kbsQuery.data ?? []).length === 0 ? (
        <Card className="mt-6">
          <CardContent className="py-10 text-center">
            <p className="text-muted-foreground text-sm italic" data-testid="project-kbs-empty">
              Ninguna KB está granted a este proyecto todavía. Concede una desde el{" "}
              <Link href="/admin/knowledge-bases" className="text-foreground underline not-italic">
                panel de Knowledge Bases
              </Link>{" "}
              (botón <span className="not-italic">Grant</span>) — o súbele documentos directamente
              allí desplegando la KB.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="mt-6 space-y-4">
          {(kbsQuery.data ?? []).map((kb) => (
            <KnowledgeBaseCard key={kb.id} kb={kb} />
          ))}
        </div>
      )}

      {/* KB Q3: catálogo con toggle — activa cualquier KB del tenant (built-ins
          incluidas) sin ir a la pantalla global. El toggle crea/borra un grant
          NORMAL de kb_projects (auditable y revocable). */}
      <KbCatalogSection
        projectId={projectId}
        catalog={catalogQuery.data ?? []}
        granted={new Set((kbsQuery.data ?? []).map((kb) => kb.id))}
      />
    </div>
  );
}

// --------------------------------------------------------------------------
// KB Q2 — «Añadir conocimiento»: ficheros → KB implícita lazy → ingesta
// --------------------------------------------------------------------------
function AddKnowledgeSection({
  projectId,
  grantedKbs,
  onDone,
}: {
  projectId: string;
  grantedKbs: KnowledgeBase[];
  onDone: () => void;
}) {
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => apiFetch<{ id: string; name: string }>(`/projects/${projectId}`),
    enabled: Boolean(projectId),
  });

  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      const projectName = projectQuery.data?.name ?? "proyecto";
      const implicitName = `Documentos de ${projectName}`;
      // find-or-create idempotente: si la KB implícita ya existe (por nombre,
      // entre las granteadas al proyecto), se reutiliza; si no, se crea con
      // project_id → auto-grant (Q1).
      let kb = grantedKbs.find((k) => k.name === implicitName);
      if (!kb) {
        kb = await apiFetch<KnowledgeBase>("/knowledge-bases", {
          method: "POST",
          body: {
            name: implicitName,
            description: `Documentos añadidos desde el proyecto ${projectName}`,
            project_id: projectId,
          },
        });
      }
      const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";
      const token = localStorage.getItem("agentic.token");
      for (const [index, file] of files.entries()) {
        setStatus(`Subiendo ${index + 1}/${files.length}: ${file.name}`);
        const formData = new FormData();
        formData.append("file", file);
        const response = await fetch(`${apiUrl}/knowledge-bases/${kb.id}/documents`, {
          method: "POST",
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          body: formData,
        });
        if (!response.ok) {
          const text = await response.text();
          throw new Error(`${file.name}: ${text || `HTTP ${response.status}`}`);
        }
      }
      return files.length;
    },
    onSuccess: (count) => {
      setStatus(`${count} documento(s) en ingesta.`);
      setError(null);
      onDone();
    },
    onError: (err) => {
      setStatus(null);
      setError(err instanceof ApiError ? err.body : String(err));
    },
  });

  return (
    <Card className="mt-6" data-testid="add-knowledge-section">
      <CardContent className="flex flex-wrap items-center gap-3 py-4">
        <label
          className="border-input hover:bg-muted/50 inline-flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm font-medium"
          data-testid="add-knowledge-button"
        >
          <Library className="h-4 w-4" />
          Añadir conocimiento
          <input
            type="file"
            multiple
            className="hidden"
            data-testid="add-knowledge-input"
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              if (files.length > 0) upload.mutate(files);
              e.target.value = "";
            }}
            disabled={upload.isPending || projectQuery.isLoading}
          />
        </label>
        <span className="text-muted-foreground text-xs">
          Sube documentos en un paso: van a la KB «Documentos de {projectQuery.data?.name ?? "…"}»
          (se crea sola la primera vez, ya activada para este proyecto).
        </span>
        {status && (
          <span className="text-xs" data-testid="add-knowledge-status">
            {status}
          </span>
        )}
        {error && (
          <span className="text-destructive text-xs" data-testid="add-knowledge-error">
            {error}
          </span>
        )}
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// KB Q3 — catálogo del tenant con toggle de activación por proyecto
// --------------------------------------------------------------------------
function KbCatalogSection({
  projectId,
  catalog,
  granted,
}: {
  projectId: string;
  catalog: KnowledgeBase[];
  granted: Set<string>;
}) {
  const queryClient = useQueryClient();
  const toggle = useMutation({
    mutationFn: async ({ kbId, enable }: { kbId: string; enable: boolean }) => {
      if (enable) {
        await apiFetch(`/knowledge-bases/${kbId}/projects`, {
          method: "POST",
          body: { project_id: projectId },
        });
      } else {
        await apiFetch<void>(`/knowledge-bases/${kbId}/projects/${projectId}`, {
          method: "DELETE",
        });
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["project-kbs", projectId] });
      queryClient.invalidateQueries({ queryKey: ["kb-catalog"] });
    },
  });

  if (catalog.length === 0) return null;
  return (
    <Card className="mt-6" data-testid="kb-catalog-section">
      <CardContent className="pt-5">
        <h2 className="text-base font-semibold">Catálogo de conocimiento</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          Activa una KB para que este proyecto (y sus agentes) puedan leerla; desactívala para
          revocar el acceso.
        </p>
        {toggle.isError ? (
          <p className="text-destructive mt-2 text-sm" data-testid="kb-catalog-error">
            {toggle.error instanceof ApiError ? toggle.error.body : String(toggle.error)}
          </p>
        ) : null}
        <ul className="mt-3 divide-y">
          {catalog.map((kb) => {
            const isGranted = granted.has(kb.id);
            return (
              <li key={kb.id} className="flex items-center justify-between gap-3 py-2">
                <div className="min-w-0">
                  <span className="block truncate text-sm">{kb.name}</span>
                  {kb.description ? (
                    <span className="text-muted-foreground block truncate text-xs">
                      {kb.description}
                    </span>
                  ) : null}
                </div>
                <Button
                  size="sm"
                  variant={isGranted ? "outline" : "default"}
                  disabled={toggle.isPending}
                  onClick={() => toggle.mutate({ kbId: kb.id, enable: !isGranted })}
                  data-testid={`kb-catalog-toggle-${kb.id}`}
                >
                  {isGranted ? "Desactivar" : "Activar"}
                </Button>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Per-KB card with its documents
// --------------------------------------------------------------------------
function KnowledgeBaseCard({ kb }: { kb: KnowledgeBase }) {
  const queryClient = useQueryClient();
  const [uploadOpen, setUploadOpen] = useState(false);

  const docsQuery = useQuery({
    queryKey: ["kb-documents", kb.id],
    queryFn: () => apiFetch<KBDocument[]>(`/knowledge-bases/${kb.id}/documents`),
    refetchOnWindowFocus: false,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["kb-documents", kb.id] });

  return (
    <Card data-testid={`kb-card-${kb.id}`}>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>{kb.name}</CardTitle>
          {kb.description ? (
            <p className="text-muted-foreground mt-1 text-xs">{kb.description}</p>
          ) : null}
          <p className="text-muted-foreground mt-1 text-[10px] uppercase tracking-wide">
            Embedding: <span className="font-mono">{kb.embedding_model_id}</span>
          </p>
        </div>
        <Button onClick={() => setUploadOpen(true)} data-testid={`kb-upload-open-${kb.id}`}>
          <UploadCloud className="mr-1 h-3.5 w-3.5" />
          Subir documento
        </Button>
      </CardHeader>
      <CardContent>
        {docsQuery.isLoading ? (
          <p className="text-muted-foreground text-sm">Cargando documentos…</p>
        ) : (docsQuery.data ?? []).length === 0 ? (
          <p
            className="text-muted-foreground text-sm italic"
            data-testid={`kb-docs-empty-${kb.id}`}
          >
            Esta KB aún no tiene documentos.
          </p>
        ) : (
          <DocumentList kbId={kb.id} documents={docsQuery.data ?? []} onChanged={invalidate} />
        )}
      </CardContent>

      <UploadDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        kbId={kb.id}
        onUploaded={invalidate}
      />
    </Card>
  );
}

// --------------------------------------------------------------------------
// Per-document row with status, link to ingestion page, delete
// --------------------------------------------------------------------------
function DocumentList({
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
      </div>
      <div className="flex items-center gap-1">
        <Link
          href={`/admin/documents/${doc.id}/ingestion`}
          data-testid={`kb-doc-ingestion-link-${doc.id}`}
          className="text-muted-foreground hover:text-foreground text-xs underline"
        >
          Progreso
        </Link>
        {canReindex ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() => reindexMutation.mutate()}
            disabled={reindexMutation.isPending}
            data-testid={`kb-doc-reindex-${doc.id}`}
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
          data-testid={`kb-doc-delete-${doc.id}`}
          aria-label="Eliminar"
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
    onError: (err) => {
      setErrorMsg(err instanceof Error ? err.message : String(err));
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
          <DialogTitle>Subir documento a la KB</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <div>
            <Label htmlFor="kb-upload-file">Archivo</Label>
            <Input
              id="kb-upload-file"
              data-testid="kb-upload-file"
              type="file"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              accept=".pdf,.docx,.md,.txt,.html,.wav,.mp3"
            />
          </div>
          <div>
            <Label htmlFor="kb-upload-title">Título (opcional)</Label>
            <Input
              id="kb-upload-title"
              data-testid="kb-upload-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Por defecto: nombre del archivo"
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
            Cancelar
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={!file || mutation.isPending}
            data-testid="kb-upload-submit"
          >
            {mutation.isPending ? "Subiendo…" : "Subir"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
