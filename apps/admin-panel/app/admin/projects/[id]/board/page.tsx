"use client";

/**
 * task_ui_11 — Tablero DENTRO del proyecto: el doble Kanban (planes arriba,
 * tareas del plan seleccionado abajo; CLAUDE.md principio 6) acotado a un
 * proyecto. El tablero es el mismo componente que usaba `/admin/board`; el
 * portfolio de todos los proyectos vive ahora en `/admin/board`.
 */

import { useParams } from "next/navigation";

import { PlanKanban } from "@/components/board/plan-kanban";

export default function ProjectBoardPage() {
  const params = useParams<{ id: string }>();
  return <PlanKanban projectId={params.id} />;
}
