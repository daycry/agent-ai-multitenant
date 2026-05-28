"use client";

/**
 * Knowledge Bases — admin general del tenant (Plan 06.6 follow-up).
 *
 * Hasta ahora los endpoints backend `POST /knowledge-bases`,
 * `PUT /knowledge-bases/{id}`, `DELETE /knowledge-bases/{id}` y
 * `POST /knowledge-bases/{id}/projects` existían sin UI — los KBs
 * había que crearlos por API. Esta página cubre el gap:
 *
 *   - Listar las KBs del tenant
 *   - Crear nueva KB (nombre, descripción markdown, embedding model)
 *   - Editar / borrar
 *   - "Grant" a un proyecto (con el `<ProjectCombobox>`)
 *
 * El listado de "qué proyectos tienen acceso a esta KB" no es scope
 * de esta iteración — el backend no expone el endpoint inverso, y
 * desde el proyecto se ve el conjunto de KBs disponibles. Añadirlo
 * requiere un GET extra en el router, queda para un follow-up.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Home, Library, Pencil, Plus, Share2, Trash2 } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Breadcrumb } from "@/components/layout/breadcrumb";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { ProjectCombobox } from "@/components/ui/project-combobox";
import { ApiError, apiFetch } from "@/lib/api";
import { renderPlanDraft } from "@/lib/plan-draft-md";

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

const DEFAULT_EMBEDDING_MODEL = "nomic-embed-text-v1.5";

export default function KnowledgeBasesPage() {
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<KnowledgeBase | null>(null);
  const [deleting, setDeleting] = useState<KnowledgeBase | null>(null);
  const [granting, setGranting] = useState<KnowledgeBase | null>(null);

  const kbsQuery = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: () => apiFetch<KnowledgeBase[]>("/knowledge-bases"),
    refetchOnWindowFocus: false,
  });

  const kbs = kbsQuery.data ?? [];

  function refetch() {
    void queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] });
  }

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8" data-testid="kbs-page">
      <Breadcrumb
        items={[
          { label: "Inicio", href: "/admin", icon: <Home className="h-3.5 w-3.5" /> },
          { label: "Knowledge Bases" },
        ]}
      />
      <PageHeader
        icon={<Library className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Knowledge Bases"
        description="Bases de conocimiento del tenant. Cada KB agrupa documentos indexados y se asigna (grant) a uno o más proyectos."
        actions={
          <Button onClick={() => setCreateOpen(true)} data-testid="kbs-create-button">
            <Plus className="mr-1 h-4 w-4" />
            Crear KB
          </Button>
        }
      />

      <div className="mt-6">
        {kbsQuery.isLoading ? (
          <p className="text-muted-foreground text-sm">Cargando KBs…</p>
        ) : kbsQuery.isError ? (
          <Card>
            <CardHeader>
              <CardTitle>Error</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-destructive text-sm" data-testid="kbs-error">
                {kbsQuery.error instanceof ApiError ? kbsQuery.error.body : String(kbsQuery.error)}
              </p>
            </CardContent>
          </Card>
        ) : kbs.length === 0 ? (
          <Card>
            <CardContent className="py-10 text-center">
              <p className="text-muted-foreground text-sm" data-testid="kbs-empty">
                Aún no hay KBs en este tenant. Crea la primera para empezar a indexar documentos.
              </p>
            </CardContent>
          </Card>
        ) : (
          <ul className="space-y-3" data-testid="kbs-list">
            {kbs.map((kb) => (
              <li key={kb.id}>
                <KbRow
                  kb={kb}
                  onEdit={() => setEditing(kb)}
                  onDelete={() => setDeleting(kb)}
                  onGrant={() => setGranting(kb)}
                />
              </li>
            ))}
          </ul>
        )}
      </div>

      <KbCreateDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={() => {
          refetch();
          setCreateOpen(false);
        }}
      />

      {editing && (
        <KbEditDialog
          kb={editing}
          onOpenChange={(v) => !v && setEditing(null)}
          onSaved={() => {
            refetch();
            setEditing(null);
          }}
        />
      )}

      {deleting && (
        <KbDeleteDialog
          kb={deleting}
          onOpenChange={(v) => !v && setDeleting(null)}
          onDeleted={() => {
            refetch();
            setDeleting(null);
          }}
        />
      )}

      {granting && (
        <KbGrantDialog
          kb={granting}
          onOpenChange={(v) => !v && setGranting(null)}
          onGranted={() => setGranting(null)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

function KbRow({
  kb,
  onEdit,
  onDelete,
  onGrant,
}: {
  kb: KnowledgeBase;
  onEdit: () => void;
  onDelete: () => void;
  onGrant: () => void;
}) {
  return (
    <Card data-testid={`kb-${kb.id}`}>
      <CardHeader className="flex flex-row items-start justify-between gap-3 pb-2">
        <div className="min-w-0 flex-1">
          <CardTitle className="text-base">{kb.name}</CardTitle>
          <p className="text-muted-foreground mt-1 font-mono text-xs">
            embedding: {kb.embedding_model_id}
          </p>
        </div>
        <div className="flex flex-row items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            onClick={onGrant}
            data-testid={`kb-grant-${kb.id}`}
            title="Dar acceso a un proyecto"
          >
            <Share2 className="mr-1 h-3.5 w-3.5" />
            Grant
          </Button>
          <Button variant="outline" size="sm" onClick={onEdit} data-testid={`kb-edit-${kb.id}`}>
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button variant="outline" size="sm" onClick={onDelete} data-testid={`kb-delete-${kb.id}`}>
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </CardHeader>
      {kb.description && (
        <CardContent>
          <div className="text-sm">{renderPlanDraft(kb.description)}</div>
        </CardContent>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Create
// ---------------------------------------------------------------------------

interface KbForm {
  name: string;
  description: string | null;
  embedding_model_id: string;
}

function KbCreateDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [embedding, setEmbedding] = useState(DEFAULT_EMBEDDING_MODEL);

  const mutation = useMutation<KnowledgeBase, ApiError, KbForm>({
    mutationFn: (payload) =>
      apiFetch<KnowledgeBase>("/knowledge-bases", {
        method: "POST",
        body: payload,
      }),
    onSuccess: () => {
      setName("");
      setDescription("");
      setEmbedding(DEFAULT_EMBEDDING_MODEL);
      onCreated();
    },
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) {
          setName("");
          setDescription("");
          setEmbedding(DEFAULT_EMBEDDING_MODEL);
        }
        onOpenChange(v);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Crear Knowledge Base</DialogTitle>
          <DialogDescription>
            Una KB es un contenedor de documentos indexados. Tras crearla, dale acceso (grant) a los
            proyectos que la consumirán y sube documentos desde su sub-sección dentro del proyecto.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="kb-name">Nombre</Label>
            <Input
              id="kb-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={120}
              data-testid="kb-create-name"
              required
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Descripción</Label>
            <MarkdownTextarea
              value={description}
              onChange={setDescription}
              rows={4}
              data-testid="kb-create-description"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="kb-embedding">Modelo de embedding</Label>
            <Input
              id="kb-embedding"
              value={embedding}
              onChange={(e) => setEmbedding(e.target.value)}
              data-testid="kb-create-embedding"
            />
            <p className="text-muted-foreground text-xs">
              Por defecto <code>{DEFAULT_EMBEDDING_MODEL}</code> (local, Ollama, 768d). Cámbialo
              solo si tu deployment expone otro modelo compatible.
            </p>
          </div>

          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="kb-create-error"
            >
              {mutation.error?.message ?? "Error al crear"}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button
            disabled={!name.trim() || mutation.isPending}
            onClick={() =>
              mutation.mutate({
                name: name.trim(),
                description: description.trim(),
                embedding_model_id: embedding.trim() || DEFAULT_EMBEDDING_MODEL,
              })
            }
            data-testid="kb-create-submit"
          >
            {mutation.isPending ? "Creando…" : "Crear KB"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Edit
// ---------------------------------------------------------------------------

function KbEditDialog({
  kb,
  onOpenChange,
  onSaved,
}: {
  kb: KnowledgeBase;
  onOpenChange: (v: boolean) => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(kb.name);
  const [description, setDescription] = useState(kb.description ?? "");
  const [embedding, setEmbedding] = useState(kb.embedding_model_id);

  const mutation = useMutation<KnowledgeBase, ApiError, Partial<KbForm>>({
    mutationFn: (payload) =>
      apiFetch<KnowledgeBase>(`/knowledge-bases/${kb.id}`, {
        method: "PUT",
        body: payload,
      }),
    onSuccess: onSaved,
  });

  return (
    <Dialog open={true} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Editar Knowledge Base</DialogTitle>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="kb-edit-name">Nombre</Label>
            <Input
              id="kb-edit-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={120}
              data-testid="kb-edit-name"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Descripción</Label>
            <MarkdownTextarea
              value={description}
              onChange={setDescription}
              rows={4}
              data-testid="kb-edit-description"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="kb-edit-embedding">Modelo de embedding</Label>
            <Input
              id="kb-edit-embedding"
              value={embedding}
              onChange={(e) => setEmbedding(e.target.value)}
              data-testid="kb-edit-embedding"
            />
            <p className="text-muted-foreground text-xs">
              ⚠️ Cambiar el modelo no re-indexa documentos existentes. Los nuevos uploads usarán el
              modelo actual.
            </p>
          </div>

          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="kb-edit-error"
            >
              {mutation.error?.message ?? "Error al guardar"}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button
            disabled={!name.trim() || mutation.isPending}
            onClick={() =>
              mutation.mutate({
                name: name.trim(),
                description: description.trim() || null,
                embedding_model_id: embedding.trim(),
              })
            }
            data-testid="kb-edit-submit"
          >
            {mutation.isPending ? "Guardando…" : "Guardar"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Delete with confirm-by-name
// ---------------------------------------------------------------------------

function KbDeleteDialog({
  kb,
  onOpenChange,
  onDeleted,
}: {
  kb: KnowledgeBase;
  onOpenChange: (v: boolean) => void;
  onDeleted: () => void;
}) {
  const [typed, setTyped] = useState("");
  const matches = typed === kb.name;

  const mutation = useMutation<void, ApiError, void>({
    mutationFn: async () => {
      await apiFetch(`/knowledge-bases/${kb.id}`, { method: "DELETE" });
    },
    onSuccess: onDeleted,
  });

  return (
    <Dialog
      open={true}
      onOpenChange={(v) => {
        if (!v) setTyped("");
        onOpenChange(v);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Borrar Knowledge Base</DialogTitle>
          <DialogDescription>
            Borra la KB, todos sus documentos indexados y los grants a proyectos. La acción es{" "}
            <strong>irreversible</strong>. Los documentos en MinIO no se tocan.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <p className="text-sm">
            Para confirmar, teclea el nombre de la KB:{" "}
            <code className="bg-muted rounded px-1 py-0.5 text-xs">{kb.name}</code>
          </p>
          <Input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={kb.name}
            data-testid="kb-delete-confirm-input"
          />
          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="kb-delete-error"
            >
              {mutation.error?.message ?? "Error al borrar"}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button
            variant="destructive"
            disabled={!matches || mutation.isPending}
            onClick={() => mutation.mutate()}
            data-testid="kb-delete-confirm"
          >
            {mutation.isPending ? "Borrando…" : "Borrar definitivamente"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Grant to project
// ---------------------------------------------------------------------------

function KbGrantDialog({
  kb,
  onOpenChange,
  onGranted,
}: {
  kb: KnowledgeBase;
  onOpenChange: (v: boolean) => void;
  onGranted: () => void;
}) {
  const [projectId, setProjectId] = useState<string | null>(null);
  const [projectName, setProjectName] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const mutation = useMutation<unknown, ApiError, { project_id: string }>({
    mutationFn: (payload) =>
      apiFetch(`/knowledge-bases/${kb.id}/projects`, { method: "POST", body: payload }),
    onSuccess: () => {
      setSuccessMsg(
        projectName ? `Acceso otorgado a "${projectName}".` : "Acceso otorgado al proyecto.",
      );
      setProjectId(null);
      setProjectName(null);
    },
  });

  return (
    <Dialog
      open={true}
      onOpenChange={(v) => {
        if (!v) {
          setProjectId(null);
          setProjectName(null);
          setSuccessMsg(null);
          onGranted();
        }
        onOpenChange(v);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Dar acceso a un proyecto</DialogTitle>
          <DialogDescription>
            Después del grant, el proyecto verá esta KB en su sub-sección &quot;Knowledge
            Bases&quot; y podrá subir documentos. Puedes hacer grant a varios proyectos repitiendo
            esta acción.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <p className="text-sm">
            KB: <strong>{kb.name}</strong>
          </p>

          <div className="flex flex-col gap-1.5">
            <Label>Proyecto destino</Label>
            <ProjectCombobox
              value={projectId}
              onChange={(id, name) => {
                setProjectId(id);
                setProjectName(name ?? null);
                setSuccessMsg(null);
              }}
              data-testid="kb-grant-project"
            />
          </div>

          {successMsg && (
            <p
              className="bg-success-soft text-success-soft-foreground rounded p-2 text-xs"
              data-testid="kb-grant-success"
            >
              {successMsg}
            </p>
          )}

          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="kb-grant-error"
            >
              {mutation.error?.message ?? "Error al otorgar acceso"}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cerrar
          </Button>
          <Button
            disabled={!projectId || mutation.isPending}
            onClick={() => {
              if (projectId) mutation.mutate({ project_id: projectId });
            }}
            data-testid="kb-grant-submit"
          >
            {mutation.isPending ? "Otorgando…" : "Otorgar acceso"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
