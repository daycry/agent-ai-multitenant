"use client";

/**
 * Hub del agente (Plan 06.6 task_06_6_05 + 06_6_07).
 *
 * Vista detalle/edit de un agente. Lee GET /agents/{id} y permite:
 *   - Editar (campos básicos: name, description, role, system_prompt,
 *     memory_scope, review_capability, max_concurrent_tasks).
 *   - Borrar con confirm-by-name.
 *
 * Los campos del scope (project_id, scope, forked_from_agent_id)
 * son set-once en el backend — esta UI no los expone como editables.
 * Para "fork" hay una API separada (task_01_15) que vivirá en su
 * propia acción "Hacer copia".
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Home, Pencil, Trash2 } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Breadcrumb } from "@/components/layout/breadcrumb";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
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
import { Spinner } from "@/components/ui/spinner";
import { ApiError, apiFetch } from "@/lib/api";

import { AgentKbsSection } from "./agent-kbs-section";

interface Agent {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  agent_type: string;
  role: string;
  system_prompt: string;
  memory_scope: string;
  review_capability: boolean;
  max_concurrent_tasks: number;
  is_template: boolean;
  scope: string;
  project_id: string | null;
  forked_from_agent_id: string | null;
}

interface AgentUpdate {
  name?: string;
  description?: string | null;
  role?: string;
  system_prompt?: string;
  memory_scope?: string;
  review_capability?: boolean;
  max_concurrent_tasks?: number;
}

const ROLE_OPTIONS = [
  "project_manager",
  "architect",
  "backend_dev",
  "frontend_dev",
  "qa",
  "reviewer",
  "leader",
  "worker",
  "specialist",
  "researcher",
  "devops",
  "security",
  "technical_writer",
];

const MEMORY_SCOPE_OPTIONS = [
  { value: "private", label: "Privada" },
  { value: "team_shared", label: "Compartida con equipo" },
  { value: "project_shared", label: "Compartida con proyecto" },
  { value: "global", label: "Global del tenant" },
];

const SCOPE_BADGE: Record<string, BadgeVariant> = {
  global_builtin: "muted",
  global_tenant_template: "info",
  project_local: "primary",
};

export default function AgentHubPage() {
  const params = useParams<{ id: string }>();
  const agentId = params?.id ?? "";
  const router = useRouter();
  const queryClient = useQueryClient();

  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const {
    data: agent,
    isLoading,
    isError,
    error,
  } = useQuery<Agent, ApiError>({
    queryKey: ["agent", agentId],
    queryFn: () => apiFetch<Agent>(`/agents/${agentId}`),
    enabled: !!agentId,
    refetchOnWindowFocus: false,
  });

  // Built-in agents cannot be edited/deleted by tenant users — the
  // backend rejects with 403 / 405. We hide the buttons to avoid a
  // misleading affordance.
  const isReadOnly = agent?.scope === "global_builtin";

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8" data-testid="agent-hub">
      <Breadcrumb
        items={[
          { label: "Inicio", href: "/admin", icon: <Home className="h-3.5 w-3.5" /> },
          { label: "Agentes", href: "/admin/agents", icon: <Bot className="h-3.5 w-3.5" /> },
          { label: agent?.name ?? "Agente" },
        ]}
      />
      <PageHeader
        icon={<Bot className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={agent?.name ?? "Agente"}
        description={agent?.description ?? "Cargando…"}
        actions={
          agent && !isReadOnly ? (
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setEditOpen(true)}
                data-testid="agent-edit-button"
              >
                <Pencil className="mr-1 h-4 w-4" />
                Editar
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setDeleteOpen(true)}
                data-testid="agent-delete-button"
              >
                <Trash2 className="mr-1 h-4 w-4" />
                Borrar
              </Button>
            </div>
          ) : agent && isReadOnly ? (
            <Badge variant="muted">read-only (built-in)</Badge>
          ) : null
        }
      />

      {isLoading && (
        <div className="flex justify-center p-8" data-testid="agent-loading">
          <Spinner />
        </div>
      )}

      {isError && (
        <Card className="p-6" data-testid="agent-error">
          <p className="text-danger-soft-foreground text-sm">
            No se pudo cargar el agente: {error?.message ?? "error desconocido"}.
          </p>
          <Button asChild variant="outline" size="sm" className="mt-3">
            <Link href="/admin/agents">Volver al catálogo</Link>
          </Button>
        </Card>
      )}

      {agent && (
        <Card className="space-y-4 p-6" data-testid="agent-fields">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={SCOPE_BADGE[agent.scope] ?? "muted"}>{agent.scope}</Badge>
            <Badge variant="info">{agent.role}</Badge>
            <Badge variant="muted">{agent.agent_type}</Badge>
            {agent.review_capability && <Badge variant="success">puede revisar</Badge>}
            {agent.is_template && <Badge variant="info">plantilla</Badge>}
            {agent.forked_from_agent_id && <Badge variant="warning">forked</Badge>}
          </div>

          <div>
            <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
              System prompt
            </p>
            <pre className="bg-muted/40 mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded p-3 text-xs">
              {agent.system_prompt}
            </pre>
          </div>

          <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
            <Field label="Memory scope" value={agent.memory_scope} />
            <Field label="Max concurrent tasks" value={String(agent.max_concurrent_tasks)} />
            <Field label="Project" value={agent.project_id ? agent.project_id.slice(0, 8) : "—"} />
          </div>
        </Card>
      )}

      {/* Plan 06.9: knowledge bases granted to this agent template */}
      {agent && (
        <div className="mt-4">
          <AgentKbsSection agentId={agent.id} isReadOnly={isReadOnly} />
        </div>
      )}

      {agent && (
        <AgentEditDialog
          agent={agent}
          open={editOpen}
          onOpenChange={setEditOpen}
          onSaved={() => {
            void queryClient.invalidateQueries({ queryKey: ["agent", agentId] });
            void queryClient.invalidateQueries({ queryKey: ["agents", "list"] });
            setEditOpen(false);
          }}
        />
      )}

      {agent && (
        <AgentDeleteDialog
          agent={agent}
          open={deleteOpen}
          onOpenChange={setDeleteOpen}
          onDeleted={() => {
            setDeleteOpen(false);
            router.push("/admin/agents");
          }}
        />
      )}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className="font-medium">{value}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Edit dialog
// ---------------------------------------------------------------------------

function AgentEditDialog({
  agent,
  open,
  onOpenChange,
  onSaved,
}: {
  agent: Agent;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(agent.name);
  const [description, setDescription] = useState(agent.description ?? "");
  const [role, setRole] = useState(agent.role);
  const [systemPrompt, setSystemPrompt] = useState(agent.system_prompt);
  const [memoryScope, setMemoryScope] = useState(agent.memory_scope);
  const [reviewCap, setReviewCap] = useState(agent.review_capability);
  const [maxTasks, setMaxTasks] = useState(agent.max_concurrent_tasks);

  useEffect(() => {
    if (open) {
      setName(agent.name);
      setDescription(agent.description ?? "");
      setRole(agent.role);
      setSystemPrompt(agent.system_prompt);
      setMemoryScope(agent.memory_scope);
      setReviewCap(agent.review_capability);
      setMaxTasks(agent.max_concurrent_tasks);
    }
  }, [open, agent]);

  const mutation = useMutation<Agent, ApiError, AgentUpdate>({
    mutationFn: (payload) =>
      apiFetch<Agent>(`/agents/${agent.id}`, {
        method: "PUT",
        body: payload,
      }),
    onSuccess: onSaved,
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Editar agente</DialogTitle>
          <DialogDescription>
            Los campos de scope (project_id, forked_from_agent_id) son set-once. Para crear una
            copia de un agente, usa la acción &quot;Hacer copia&quot; (fork).
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="ae-name">Nombre</Label>
              <Input
                id="ae-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                data-testid="edit-agent-name"
                required
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="ae-role">Role</Label>
              <select
                id="ae-role"
                value={role}
                onChange={(e) => setRole(e.target.value)}
                className="border-input bg-background focus-visible:ring-ring rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2"
                data-testid="edit-agent-role"
              >
                {ROLE_OPTIONS.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ae-description">Descripción</Label>
            <textarea
              id="ae-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              className="border-input bg-background focus-visible:ring-ring rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2"
              data-testid="edit-agent-description"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ae-prompt">System prompt</Label>
            <textarea
              id="ae-prompt"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={6}
              className="border-input bg-background focus-visible:ring-ring rounded-md border px-3 py-2 font-mono text-xs focus-visible:outline-none focus-visible:ring-2"
              data-testid="edit-agent-system-prompt"
              required
            />
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="ae-scope">Memory scope</Label>
              <select
                id="ae-scope"
                value={memoryScope}
                onChange={(e) => setMemoryScope(e.target.value)}
                className="border-input bg-background rounded-md border px-3 py-2 text-sm"
                data-testid="edit-agent-memory-scope"
              >
                {MEMORY_SCOPE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="ae-tasks">Max concurrent tasks</Label>
              <Input
                id="ae-tasks"
                type="number"
                min={1}
                max={64}
                value={maxTasks}
                onChange={(e) => setMaxTasks(Number(e.target.value) || 1)}
                data-testid="edit-agent-max-tasks"
              />
            </div>
            <div className="flex items-end gap-2">
              <input
                id="ae-review"
                type="checkbox"
                checked={reviewCap}
                onChange={(e) => setReviewCap(e.target.checked)}
                data-testid="edit-agent-review-cap"
              />
              <Label htmlFor="ae-review">Puede revisar tareas</Label>
            </div>
          </div>

          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="edit-agent-error"
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
            disabled={!name.trim() || !systemPrompt.trim() || mutation.isPending}
            onClick={() =>
              mutation.mutate({
                name: name.trim(),
                description: description.trim() || null,
                role,
                system_prompt: systemPrompt,
                memory_scope: memoryScope,
                review_capability: reviewCap,
                max_concurrent_tasks: maxTasks,
              })
            }
            data-testid="edit-agent-save"
          >
            {mutation.isPending ? "Guardando…" : "Guardar"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Delete dialog
// ---------------------------------------------------------------------------

function AgentDeleteDialog({
  agent,
  open,
  onOpenChange,
  onDeleted,
}: {
  agent: Agent;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onDeleted: () => void;
}) {
  const [typed, setTyped] = useState("");
  const matches = typed === agent.name;

  const mutation = useMutation<void, ApiError, void>({
    mutationFn: async () => {
      await apiFetch(`/agents/${agent.id}`, { method: "DELETE" });
    },
    onSuccess: onDeleted,
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) setTyped("");
        onOpenChange(v);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Borrar agente</DialogTitle>
          <DialogDescription>
            Esta acción es <strong>irreversible</strong>. Si el agente está asignado a tareas
            activas, el backend rechazará el borrado con 409.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <p className="text-sm">
            Teclea el nombre del agente para confirmar:
            <br />
            <code className="bg-muted rounded px-1 py-0.5 text-xs">{agent.name}</code>
          </p>
          <Input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={agent.name}
            data-testid="delete-agent-confirm-input"
          />
          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="delete-agent-error"
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
            data-testid="delete-agent-confirm"
          >
            {mutation.isPending ? "Borrando…" : "Borrar definitivamente"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
