/**
 * Tipos y constantes compartidos del chat de proyecto (plan prod-16,
 * `task_prod16_08`).
 *
 * Viven aparte para que las piezas del troceado (`chat-mode-selector`,
 * `message-feed`, `chat-composer`, `generate-plan-button`) y la propia
 * `page.tsx` compartan UNA definición de `Message`/`Conversation`. Redeclararlas
 * en cada pieza es cómo divergen: un campo nuevo en el backend se añade en una
 * copia y no en las otras, y el desajuste no se ve hasta que algo pinta vacío.
 */

export interface Conversation {
  id: string;
  tenant_id: string;
  project_id: string;
  title: string | null;
  current_mode: string;
  custom_mode_name: string | null;
  related_plan_id: string | null;
  created_at: string;
}

export interface Message {
  id: string;
  tenant_id: string;
  conversation_id: string;
  author_kind: "user" | "agent" | "system";
  author_user_id: string | null;
  author_agent_id: string | null;
  content: string;
  mode: string;
  attachments: Array<Record<string, unknown>>;
  related_plan_id: string | null;
  is_summary: boolean;
  created_at: string;
}

// A-01: cuántos mensajes carga el feed. Un turno de planning emite entre 6 y 10
// (framing del PM → un especialista por rol → síntesis), así que 100 cubre unos
// diez turnos completos. El endpoint devuelve LOS MÁS RECIENTES.
export const MESSAGE_WINDOW = 100;

export interface ModeOption {
  value: string;
  labelEs: string;
  labelEn: string;
  description: string;
}

// Built-in modes; ``custom`` is reachable via a separate creation flow
// in task_03_08, not from this base selector.
export const BUILT_IN_MODES: ModeOption[] = [
  {
    value: "planning",
    labelEs: "Planning",
    labelEn: "Planning",
    description: "El equipo construye un plan estructurado",
  },
  {
    value: "discussion",
    labelEs: "Discusión",
    labelEn: "Discussion",
    description: "Ronda abierta de ideas y opiniones",
  },
  {
    value: "execution",
    labelEs: "Ejecución",
    labelEn: "Execution",
    description: "El equipo ejecuta tareas del plan aprobado",
  },
];
