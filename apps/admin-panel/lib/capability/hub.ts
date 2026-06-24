/**
 * Lógica PURA del Hub de Capacidad por entidad (Plan 06.17 task_06_17_09).
 *
 * Estrella polar del plan (`docs/04-reference/training-model.md`): capacitar a
 * un agente/proyecto/equipo es dotarlo de CAPACIDAD por CUATRO vías —**SABER**
 * (KBs+RAG), **RECORDAR** (memoria por scope), **SER** (persona/modelo) y
 * **HACER** (tools/comandos)—. El Hub es el modelo mental ÚNICO encima de
 * `GET /{entity}/{id}/capabilities` (task_06_17_08), que ya compone la sección
 * HACER con `effective-tools` de 06.18.
 *
 * Este módulo NO toca React ni el DOM: deriva, a partir del contrato JSON del
 * endpoint, las 4 secciones con su **estado honesto** (regla 4 del plan: nada
 * parece activo si no lo está), las etiquetas del **verbo único** "Asignar /
 * Quitar" (regla 1), las etiquetas de **nivel explícito** Rol/Stack/Equipo/
 * Plataforma (regla 3), el **aviso de agente global** (ADR 0054) y el
 * **checklist** con el orden de onboarding Persona → Saber → Hacer → Recordar
 * (regla 6). Es la fuente ÚNICA que consume `capability-hub.tsx` y se testea
 * aislada (`capability-hub.test.ts`), igual que `lib/memory/honesty.ts` y
 * `lib/tools/taxonomy.ts`.
 */

import type { Lang } from "@/lib/lang-context";

// ---------------------------------------------------------------------------
// Contrato del endpoint (espeja api_server.capabilities.CapabilitiesResponse).
// NUNCA inventar campos: estos shapes reflejan el Pydantic del backend.
// ---------------------------------------------------------------------------

/** Nivel canónico de una KB / capacidad (espeja LEVEL_* del backend). */
export type CapabilityLevel = "rol" | "stack" | "plataforma" | "equipo";

export interface CapabilityKB {
  kb_id: string;
  name: string;
  /** `rol` | `stack` | `plataforma` (el backend no emite `equipo` en una KB). */
  level: string;
  is_builtin: boolean;
}

export interface CapabilitySaber {
  knowledge_bases: CapabilityKB[];
}

export interface CapabilityMemoryScope {
  scope: string;
  count: number;
}

export interface CapabilityRecordar {
  /** `memory_scope` del agente; `null` para proyecto/equipo. */
  memory_scope: string | null;
  memory: CapabilityMemoryScope[];
}

export interface CapabilitySer {
  model_configured: boolean;
  provider: string | null;
  model: string | null;
  temperature: number | null;
  system_prompt_present: boolean;
  // Ola D / ADR 0065: nivel que fija el modelo efectivo en la cadena de herencia
  // ("agent" | "team" | "project" | "platform").
  model_origin?: string | null;
}

export interface CapabilityHacer {
  effective: string[];
  unrestricted: boolean;
  shell_exec_effective: boolean;
}

export type CapabilityEntityType = "agent" | "project" | "team";

/**
 * Aviso honesto BILINGÜE (espeja `api_server.capabilities.CapabilityWarning`).
 * `code` es el identificador estable idioma-neutral (p. ej. el de agente global,
 * ADR 0054); `es`/`en` son el mismo mensaje en cada idioma soportado. El Hub
 * renderiza el idioma activo y empareja por `code` (NUNCA por el texto, que
 * dejaba muerta la rama EN antes del follow-up bilingual-warnings).
 */
export interface CapabilityWarning {
  code: string;
  es: string;
  en: string;
}

/** Código del aviso de agente global (ADR 0054); espeja `WARN_GLOBAL_AGENT`. */
export const WARN_GLOBAL_AGENT = "global_agent_no_project_context";

/** Texto de un aviso bilingüe en el idioma activo. */
export function warningText(warning: CapabilityWarning, lang: Lang): string {
  return lang === "es" ? warning.es : warning.en;
}

export interface CapabilitiesResponse {
  entity_type: CapabilityEntityType;
  entity_id: string;
  saber: CapabilitySaber;
  recordar: CapabilityRecordar;
  /** Solo poblada para un agente; `null` para proyecto/equipo. */
  ser: CapabilitySer | null;
  hacer: CapabilityHacer;
  warnings: CapabilityWarning[];
}

// ---------------------------------------------------------------------------
// Las CUATRO secciones del modelo mental (orden visual = orden de lectura).
// ---------------------------------------------------------------------------

/** Identificador estable de cada sección del Hub. */
export type SectionKey = "saber" | "recordar" | "ser" | "hacer";

/** Tono semántico del badge de estado (mapea a <Badge variant>). */
export type SectionTone = "success" | "warning" | "muted" | "info";

/** El verbo único del plan (regla 1). "grant" NUNCA es etiqueta de botón. */
export const VERB_ASSIGN: Record<Lang, string> = { es: "Asignar", en: "Assign" };
export const VERB_REMOVE: Record<Lang, string> = { es: "Quitar", en: "Remove" };
/** En SER la persona se EDITA (training-model.md: "Editar/Asignar"). */
export const VERB_EDIT: Record<Lang, string> = { es: "Editar", en: "Edit" };

const SECTION_TITLE: Record<SectionKey, Record<Lang, string>> = {
  saber: { es: "SABER · Conocimiento", en: "KNOW · Knowledge" },
  recordar: { es: "RECORDAR · Memoria", en: "REMEMBER · Memory" },
  ser: { es: "SER · Persona", en: "BE · Persona" },
  hacer: { es: "HACER · Acciones", en: "DO · Actions" },
};

const SECTION_QUESTION: Record<SectionKey, Record<Lang, string>> = {
  saber: { es: "¿Qué corpus curado consulta?", en: "Which curated corpus does it query?" },
  recordar: { es: "¿Qué recuerda entre runs?", en: "What does it remember across runs?" },
  ser: { es: "¿Quién es y cómo se comporta?", en: "Who is it and how does it behave?" },
  hacer: { es: "¿Qué puede ejecutar?", en: "What can it execute?" },
};

/** Etiquetas bilingües del NIVEL explícito de una capacidad (regla 3). */
export const LEVEL_LABEL: Record<CapabilityLevel, Record<Lang, string>> = {
  rol: { es: "Rol", en: "Role" },
  stack: { es: "Stack", en: "Stack" },
  equipo: { es: "Equipo", en: "Team" },
  plataforma: { es: "Plataforma", en: "Platform" },
};

/** Etiqueta "No disponible aún" reutilizada (honestidad de estado, regla 4). */
export const UNAVAILABLE_LABEL: Record<Lang, string> = {
  es: "No disponible aún",
  en: "Not available yet",
};

/**
 * El estado HONESTO de una sección: su badge ("3 KBs asignadas", "sin memoria
 * de proyecto", "modelo no configurado") + el tono semántico. `active` indica
 * si la sección representa una capacidad REAL (no finge: regla 4).
 */
export interface SectionStatus {
  /** Texto del badge en el idioma activo. */
  badge: string;
  tone: SectionTone;
  /** `true` cuando la sección tiene capacidad real configurada. */
  active: boolean;
}

/** Una sección renderizable del Hub. */
export interface HubSection {
  key: SectionKey;
  title: string;
  question: string;
  status: SectionStatus;
  /** Verbo que la sección ofrece (Asignar para SABER/HACER, Editar para SER). */
  verb: string;
}

/** Una KB lista para render: con su etiqueta de nivel ya resuelta. */
export interface HubKB {
  kb_id: string;
  name: string;
  level: CapabilityLevel;
  levelLabel: string;
  is_builtin: boolean;
}

function isKnownLevel(level: string): level is CapabilityLevel {
  return level === "rol" || level === "stack" || level === "equipo" || level === "plataforma";
}

/** Resuelve la etiqueta de nivel de una KB (fallback honesto a Rol). */
export function resolveKBLevel(kb: CapabilityKB, lang: Lang): HubKB {
  const level: CapabilityLevel = isKnownLevel(kb.level) ? kb.level : "rol";
  return {
    kb_id: kb.kb_id,
    name: kb.name,
    level,
    levelLabel: LEVEL_LABEL[level][lang],
    is_builtin: kb.is_builtin,
  };
}

// ---------------------------------------------------------------------------
// Estado por sección (honesto): cada builder mira SOLO su parte del contrato.
// ---------------------------------------------------------------------------

/** SABER: cuántas KBs hay asignadas (0 → "sin conocimiento", muted). */
export function saberStatus(saber: CapabilitySaber, lang: Lang): SectionStatus {
  const n = saber.knowledge_bases.length;
  if (n === 0) {
    return {
      badge: lang === "es" ? "Sin conocimiento asignado" : "No knowledge assigned",
      tone: "muted",
      active: false,
    };
  }
  const badge =
    lang === "es"
      ? `${n} ${n === 1 ? "KB asignada" : "KBs asignadas"}`
      : `${n} ${n === 1 ? "KB assigned" : "KBs assigned"}`;
  return { badge, tone: "success", active: true };
}

/**
 * RECORDAR: suma las memorias por scope. Para un agente, además, un
 * `memory_scope=private` NO memoriza (skip silencioso) → warning honesto. La
 * ausencia de memoria de proyecto se refleja con "sin memoria de proyecto".
 */
export function recordarStatus(recordar: CapabilityRecordar, lang: Lang): SectionStatus {
  const total = recordar.memory.reduce((acc, m) => acc + m.count, 0);
  // private silencioso (solo en agente): la sección NO está realmente activa.
  if (recordar.memory_scope === "private") {
    return {
      badge: lang === "es" ? "Privada: no memoriza" : "Private: not remembering",
      tone: "warning",
      active: false,
    };
  }
  const hasProject = recordar.memory.some((m) => m.scope === "project_shared" && m.count > 0);
  if (total === 0) {
    return {
      badge: lang === "es" ? "Sin memoria todavía" : "No memory yet",
      tone: "muted",
      active: false,
    };
  }
  if (!hasProject) {
    return {
      badge:
        lang === "es" ? `${total} en memoria · sin proyecto` : `${total} in memory · no project`,
      tone: "info",
      active: true,
    };
  }
  const badge =
    lang === "es"
      ? `${total} ${total === 1 ? "memoria" : "memorias"}`
      : `${total} ${total === 1 ? "memory" : "memories"}`;
  return { badge, tone: "success", active: true };
}

/**
 * SER: persona/modelo. Solo aplica a un agente (`ser != null`). Honestidad
 * (ADR 0055): un `model_config` sin provider/model NO está configurado →
 * "modelo no configurado", warning.
 */
export function serStatus(ser: CapabilitySer | null, lang: Lang): SectionStatus {
  if (ser === null) {
    return {
      badge: lang === "es" ? "No aplica" : "Not applicable",
      tone: "muted",
      active: false,
    };
  }
  if (!ser.model_configured) {
    return {
      badge: lang === "es" ? "Modelo no configurado" : "Model not configured",
      tone: "warning",
      active: false,
    };
  }
  const model = ser.model ?? "";
  const badge = ser.provider ? `${ser.provider} · ${model}`.trim() : model;
  return {
    badge: badge || (lang === "es" ? "Modelo configurado" : "Model configured"),
    tone: "success",
    active: true,
  };
}

/**
 * HACER: el set efectivo de tools (compuesto con 06.18). `unrestricted` →
 * "sin restricción" (info, honesto: no es 0 tools, es "todas las wired");
 * lista vacía y restringido → "sin acciones".
 */
export function hacerStatus(hacer: CapabilityHacer, lang: Lang): SectionStatus {
  if (hacer.unrestricted) {
    return {
      badge: lang === "es" ? "Sin restricción por agente" : "No per-agent restriction",
      tone: "info",
      active: true,
    };
  }
  const n = hacer.effective.length;
  if (n === 0) {
    return {
      badge: lang === "es" ? "Sin acciones efectivas" : "No effective actions",
      tone: "muted",
      active: false,
    };
  }
  const badge =
    lang === "es"
      ? `${n} ${n === 1 ? "acción efectiva" : "acciones efectivas"}`
      : `${n} ${n === 1 ? "effective action" : "effective actions"}`;
  return { badge, tone: "success", active: true };
}

/**
 * Construye las CUATRO secciones del Hub, en orden de lectura visual
 * (Saber, Recordar, Ser, Hacer). SER se incluye SIEMPRE para mantener el modelo
 * mental de 4 secciones estable; para proyecto/equipo su estado es "No aplica".
 */
export function buildSections(caps: CapabilitiesResponse, lang: Lang): HubSection[] {
  return [
    {
      key: "saber",
      title: SECTION_TITLE.saber[lang],
      question: SECTION_QUESTION.saber[lang],
      status: saberStatus(caps.saber, lang),
      verb: VERB_ASSIGN[lang],
    },
    {
      key: "recordar",
      title: SECTION_TITLE.recordar[lang],
      question: SECTION_QUESTION.recordar[lang],
      status: recordarStatus(caps.recordar, lang),
      verb: VERB_ASSIGN[lang],
    },
    {
      key: "ser",
      title: SECTION_TITLE.ser[lang],
      question: SECTION_QUESTION.ser[lang],
      status: serStatus(caps.ser, lang),
      verb: VERB_EDIT[lang],
    },
    {
      key: "hacer",
      title: SECTION_TITLE.hacer[lang],
      question: SECTION_QUESTION.hacer[lang],
      status: hacerStatus(caps.hacer, lang),
      verb: VERB_ASSIGN[lang],
    },
  ];
}

// ---------------------------------------------------------------------------
// Aviso de agente global (ADR 0054): el endpoint ya lo emite en `warnings`
// cuando entity_type=agent y no ve contexto de proyecto. Lo detectamos para
// destacarlo como aviso de primera clase en el Hub (no solo en la lista).
// ---------------------------------------------------------------------------

/** `true` si entre los warnings del endpoint está el aviso de agente global. */
export function isGlobalAgentWarning(caps: CapabilitiesResponse): boolean {
  return caps.entity_type === "agent" && caps.warnings.some((w) => w.code === WARN_GLOBAL_AGENT);
}

/**
 * Texto BILINGÜE del aviso de agente global para destacarlo en cabecera del Hub.
 * Devuelve `null` si no aplica. Empareja por `code` (fuente única) y renderiza el
 * idioma activo; si por alguna razón no viaja, cae a una redacción equivalente.
 */
export function globalAgentNotice(caps: CapabilitiesResponse, lang: Lang): string | null {
  if (caps.entity_type !== "agent") return null;
  const warning = caps.warnings.find((w) => w.code === WARN_GLOBAL_AGENT);
  if (warning) return warningText(warning, lang);
  return null;
}

// ---------------------------------------------------------------------------
// Checklist de onboarding: orden Persona → Saber → Hacer → Recordar (regla 6).
// Cada paso queda "hecho" según el estado HONESTO de su sección (no finge).
// ---------------------------------------------------------------------------

export interface ChecklistStep {
  /** Sección asociada (el orden del checklist NO es el orden visual). */
  section: SectionKey;
  label: string;
  /** `true` cuando la capacidad de ese paso está realmente activa. */
  done: boolean;
}

const CHECKLIST_LABEL: Record<SectionKey, Record<Lang, string>> = {
  ser: { es: "Define la persona (modelo y prompt)", en: "Define the persona (model and prompt)" },
  saber: { es: "Asigna conocimiento (KBs)", en: "Assign knowledge (KBs)" },
  hacer: { es: "Asigna acciones (tools)", en: "Assign actions (tools)" },
  recordar: { es: "Configura la memoria", en: "Configure memory" },
};

/**
 * Construye el checklist en el orden de onboarding **Persona → Saber → Hacer →
 * Recordar**. Para proyecto/equipo (sin persona propia) el paso SER se omite,
 * porque no es accionable a ese nivel (honestidad: no pedir lo que no aplica).
 */
export function buildChecklist(caps: CapabilitiesResponse, lang: Lang): ChecklistStep[] {
  const order: SectionKey[] = ["ser", "saber", "hacer", "recordar"];
  const statusBy: Record<SectionKey, SectionStatus> = {
    saber: saberStatus(caps.saber, lang),
    recordar: recordarStatus(caps.recordar, lang),
    ser: serStatus(caps.ser, lang),
    hacer: hacerStatus(caps.hacer, lang),
  };
  const steps: ChecklistStep[] = [];
  for (const section of order) {
    if (section === "ser" && caps.ser === null) continue;
    steps.push({
      section,
      label: CHECKLIST_LABEL[section][lang],
      done: statusBy[section].active,
    });
  }
  return steps;
}

/** Título del Hub por tipo de entidad. */
export function hubTitle(entityType: CapabilityEntityType, lang: Lang): string {
  const titles: Record<CapabilityEntityType, Record<Lang, string>> = {
    agent: { es: "Capacidad del agente", en: "Agent capability" },
    project: { es: "Capacidad del proyecto", en: "Project capability" },
    team: { es: "Capacidad del equipo", en: "Team capability" },
  };
  return titles[entityType][lang];
}
