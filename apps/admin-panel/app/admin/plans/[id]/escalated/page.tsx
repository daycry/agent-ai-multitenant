"use client";

/**
 * Escalated-tasks panel (Plan 06 task_06_34b3) + free task form (06_34b5).
 *
 * Lists tasks in ``awaiting_human`` for one plan with four action
 * buttons each (approve_manual / reassign_with_guidance /
 * block_with_reason / cancel). Plus a "Añadir tarea libre" form that
 * spawns a plan-scoped task not bound to any checkbox.
 *
 * Backend endpoints:
 *   GET  /api/plans/{id}/escalated-tasks  → list
 *   POST /api/tasks/{id}/human-action     → { action, reason?, guidance? }
 *   POST /api/plans/{id}/free-task        → { title, description }
 */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

interface EscalatedTask {
  id: string;
  title: string;
  description: string;
  retry_count: number;
  history: Array<{ at: number; kind: string; payload: Record<string, unknown> }>;
}

export default function EscalatedPage() {
  const params = useParams<{ id: string }>();
  const planId = params?.id ?? "";
  const [tasks, setTasks] = useState<EscalatedTask[]>([]);
  const [newTitle, setNewTitle] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [freeTaskMsg, setFreeTaskMsg] = useState("");

  const reload = async () => {
    const res = await fetch(`/api/plans/${planId}/escalated-tasks`, {
      credentials: "include",
    });
    const data = (await res.json()) as { tasks: EscalatedTask[] };
    setTasks(data.tasks ?? []);
  };

  useEffect(() => {
    if (planId) void reload();
  }, [planId]);

  const doAction = async (taskId: string, action: string, extra: Record<string, string> = {}) => {
    await fetch(`/api/tasks/${taskId}/human-action`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, ...extra }),
    });
    void reload();
  };

  const createFreeTask = async () => {
    if (!newTitle) {
      setFreeTaskMsg("Falta el título");
      return;
    }
    const res = await fetch(`/api/plans/${planId}/free-task`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: newTitle, description: newDescription }),
    });
    setFreeTaskMsg(res.ok ? "Tarea libre creada" : "Error al crear");
    if (res.ok) {
      setNewTitle("");
      setNewDescription("");
    }
  };

  return (
    <div data-testid="escalated-page" className="container mx-auto px-4 py-6 space-y-6">
      <h1 className="text-2xl font-bold">Tareas escaladas</h1>

      <section data-testid="escalated-list">
        {tasks.length === 0 && <p data-testid="escalated-empty">Sin tareas escaladas.</p>}
        {tasks.map((task) => (
          <div
            key={task.id}
            data-testid={`escalated-${task.id}`}
            className="border rounded p-3 mb-3"
          >
            <h3 className="font-semibold">{task.title}</h3>
            <p className="text-sm text-gray-600">{task.description}</p>
            <p className="text-xs">Reintentos: {task.retry_count}</p>
            <div className="mt-2 flex gap-2 flex-wrap">
              <button
                data-testid={`approve-${task.id}`}
                onClick={() => void doAction(task.id, "approve_manual")}
                className="px-3 py-1 bg-green-600 text-white rounded text-sm"
              >
                Aprobar manualmente
              </button>
              <button
                data-testid={`reassign-${task.id}`}
                onClick={() =>
                  void doAction(task.id, "reassign_with_guidance", {
                    guidance: "Intenta otro enfoque",
                  })
                }
                className="px-3 py-1 bg-blue-600 text-white rounded text-sm"
              >
                Reasignar con guía
              </button>
              <button
                data-testid={`block-${task.id}`}
                onClick={() =>
                  void doAction(task.id, "block_with_reason", { reason: "blocked externally" })
                }
                className="px-3 py-1 bg-yellow-600 text-white rounded text-sm"
              >
                Bloquear
              </button>
              <button
                data-testid={`cancel-${task.id}`}
                onClick={() => void doAction(task.id, "cancel")}
                className="px-3 py-1 bg-red-600 text-white rounded text-sm"
              >
                Cancelar
              </button>
            </div>
          </div>
        ))}
      </section>

      <section data-testid="free-task-form" className="border-t pt-4">
        <h2 className="text-lg font-semibold mb-2">Añadir tarea libre al plan</h2>
        <input
          data-testid="free-task-title"
          value={newTitle}
          onChange={(e) => setNewTitle(e.target.value)}
          placeholder="Título"
          className="border rounded px-2 py-1 mr-2"
        />
        <input
          data-testid="free-task-description"
          value={newDescription}
          onChange={(e) => setNewDescription(e.target.value)}
          placeholder="Descripción"
          className="border rounded px-2 py-1 mr-2"
        />
        <button
          data-testid="free-task-submit"
          onClick={() => void createFreeTask()}
          className="px-3 py-1 bg-blue-600 text-white rounded text-sm"
        >
          Añadir
        </button>
        {freeTaskMsg && (
          <span data-testid="free-task-status" className="ml-3 text-sm">
            {freeTaskMsg}
          </span>
        )}
      </section>
    </div>
  );
}
