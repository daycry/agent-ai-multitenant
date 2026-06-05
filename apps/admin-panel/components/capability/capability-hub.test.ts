import { describe, expect, it } from "vitest";

import {
  buildChecklist,
  buildSections,
  globalAgentNotice,
  hacerStatus,
  hubTitle,
  isGlobalAgentWarning,
  recordarStatus,
  resolveKBLevel,
  saberStatus,
  serStatus,
  UNAVAILABLE_LABEL,
  VERB_ASSIGN,
  VERB_EDIT,
  VERB_REMOVE,
  WARN_GLOBAL_AGENT,
  type CapabilitiesResponse,
} from "@/lib/capability/hub";

/**
 * Plan 06.17 task_06_17_09 — Hub de Capacidad por entidad.
 *
 * El Hub es el modelo mental ÚNICO SABER/RECORDAR/SER/HACER (training-model.md)
 * encima de `GET /{entity}/{id}/capabilities`. La lógica de derivación es pura
 * (sin React/DOM) para testearla aislada, igual que `lib/memory/honesty.ts` y
 * `lib/tools/taxonomy.ts`. Cubrimos las cinco propiedades que la tarea exige:
 * (1) 4 secciones, (2) estado HONESTO por sección, (3) verbo único Asignar/
 * Quitar, (4) aviso de agente global (ADR 0054), (5) checklist con el orden de
 * onboarding Persona → Saber → Hacer → Recordar.
 */

// Un agente bien configurado (todas las vías activas) como base reutilizable.
function agentCaps(over: Partial<CapabilitiesResponse> = {}): CapabilitiesResponse {
  return {
    entity_type: "agent",
    entity_id: "11111111-1111-1111-1111-111111111111",
    saber: {
      knowledge_bases: [
        { kb_id: "kb-1", name: "Stack CI4", level: "rol", is_builtin: false },
        { kb_id: "kb-2", name: "Catálogo global", level: "plataforma", is_builtin: true },
      ],
    },
    recordar: {
      memory_scope: "project_shared",
      memory: [
        { scope: "global", count: 3 },
        { scope: "project_shared", count: 5 },
      ],
    },
    ser: {
      model_configured: true,
      provider: "claude_sdk",
      model: "claude-opus-4",
      temperature: 0.2,
      system_prompt_present: true,
    },
    hacer: {
      effective: ["rag_search", "shell_exec"],
      unrestricted: false,
      shell_exec_effective: true,
    },
    warnings: [],
    ...over,
  };
}

describe("capability hub — 4 secciones (modelo mental único)", () => {
  it("siempre construye exactamente las 4 secciones SABER/RECORDAR/SER/HACER en orden", () => {
    const sections = buildSections(agentCaps(), "es");
    expect(sections.map((s) => s.key)).toEqual(["saber", "recordar", "ser", "hacer"]);
  });

  it("las 4 secciones existen también para un proyecto (SER queda 'No aplica')", () => {
    const projectCaps: CapabilitiesResponse = {
      entity_type: "project",
      entity_id: "22222222-2222-2222-2222-222222222222",
      saber: {
        knowledge_bases: [{ kb_id: "kb-1", name: "KB stack", level: "stack", is_builtin: false }],
      },
      recordar: { memory_scope: null, memory: [{ scope: "project_shared", count: 2 }] },
      ser: null,
      hacer: { effective: [], unrestricted: true, shell_exec_effective: false },
      warnings: [],
    };
    const sections = buildSections(projectCaps, "es");
    expect(sections).toHaveLength(4);
    const ser = sections.find((s) => s.key === "ser");
    expect(ser?.status.active).toBe(false);
    expect(ser?.status.badge).toBe("No aplica");
  });

  it("los títulos de sección son bilingües ES + EN", () => {
    expect(buildSections(agentCaps(), "es").map((s) => s.title)).toContain("SABER · Conocimiento");
    expect(buildSections(agentCaps(), "en").map((s) => s.title)).toContain("KNOW · Knowledge");
  });
});

describe("capability hub — estado HONESTO por sección (regla 4)", () => {
  it("SABER cuenta las KBs asignadas ('2 KBs asignadas') y queda activa", () => {
    const st = saberStatus(agentCaps().saber, "es");
    expect(st.badge).toBe("2 KBs asignadas");
    expect(st.active).toBe(true);
  });

  it("SABER sin KBs → 'Sin conocimiento asignado' y NO activa (no finge)", () => {
    const st = saberStatus({ knowledge_bases: [] }, "es");
    expect(st.active).toBe(false);
    expect(st.badge).toBe("Sin conocimiento asignado");
  });

  it("RECORDAR con memory_scope=private → 'Privada: no memoriza' y NO activa", () => {
    const st = recordarStatus(
      { memory_scope: "private", memory: [{ scope: "private", count: 0 }] },
      "es",
    );
    expect(st.active).toBe(false);
    expect(st.badge.toLowerCase()).toContain("no memoriza");
    expect(st.tone).toBe("warning");
  });

  it("RECORDAR sin memoria de proyecto lo dice explícitamente ('sin proyecto')", () => {
    const st = recordarStatus(
      { memory_scope: "global", memory: [{ scope: "global", count: 4 }] },
      "es",
    );
    expect(st.badge.toLowerCase()).toContain("sin proyecto");
  });

  it("SER sin model_config → 'Modelo no configurado' y NO activa (ADR 0055)", () => {
    const st = serStatus(
      {
        model_configured: false,
        provider: null,
        model: null,
        temperature: null,
        system_prompt_present: false,
      },
      "es",
    );
    expect(st.active).toBe(false);
    expect(st.badge).toBe("Modelo no configurado");
  });

  it("SER configurado muestra provider · model y queda activa", () => {
    const st = serStatus(agentCaps().ser, "es");
    expect(st.active).toBe(true);
    expect(st.badge).toContain("claude_sdk");
    expect(st.badge).toContain("claude-opus-4");
  });

  it("HACER cuenta acciones efectivas ('2 acciones efectivas')", () => {
    const st = hacerStatus(agentCaps().hacer, "es");
    expect(st.badge).toBe("2 acciones efectivas");
    expect(st.active).toBe(true);
  });

  it("HACER unrestricted → 'Sin restricción por agente' (honesto, no '0 tools')", () => {
    const st = hacerStatus(
      { effective: [], unrestricted: true, shell_exec_effective: false },
      "es",
    );
    expect(st.badge.toLowerCase()).toContain("sin restricción");
    expect(st.active).toBe(true);
  });

  it("los badges de estado son bilingües ES + EN", () => {
    expect(saberStatus({ knowledge_bases: [] }, "en").badge).toBe("No knowledge assigned");
    expect(serStatus(null, "en").badge).toBe("Not applicable");
  });
});

describe("capability hub — verbo único Asignar / Quitar (regla 1)", () => {
  it("SABER y HACER ofrecen 'Asignar'; SER ofrece 'Editar'", () => {
    const sections = buildSections(agentCaps(), "es");
    const verbBy = Object.fromEntries(sections.map((s) => [s.key, s.verb]));
    expect(verbBy.saber).toBe("Asignar");
    expect(verbBy.recordar).toBe("Asignar");
    expect(verbBy.hacer).toBe("Asignar");
    expect(verbBy.ser).toBe("Editar");
  });

  it("el verbo es bilingüe y NUNCA usa jerga ('grant'/'conceder'/'vincular')", () => {
    expect(VERB_ASSIGN.es).toBe("Asignar");
    expect(VERB_ASSIGN.en).toBe("Assign");
    expect(VERB_REMOVE.es).toBe("Quitar");
    expect(VERB_REMOVE.en).toBe("Remove");
    expect(VERB_EDIT.es).toBe("Editar");
    const allVerbs = [VERB_ASSIGN, VERB_REMOVE, VERB_EDIT]
      .flatMap((v) => [v.es, v.en])
      .map((s) => s.toLowerCase());
    for (const banned of ["grant", "conceder", "vincular", "añadir", "habilitar"]) {
      expect(allVerbs).not.toContain(banned);
    }
  });
});

describe("capability hub — aviso de agente global bilingüe (ADR 0054)", () => {
  it("detecta el warning del backend por su code y devuelve el texto del idioma activo", () => {
    const caps = agentCaps({
      warnings: [
        {
          code: WARN_GLOBAL_AGENT,
          es: "agente global: no ve conocimiento ni memoria de proyecto en esta vista (ADR 0054)",
          en: "global agent: does not see project knowledge or memory in this view (ADR 0054)",
        },
      ],
    });
    expect(isGlobalAgentWarning(caps)).toBe(true);
    // ES y EN salen del MISMO warning estructurado (la rama EN ya no está muerta).
    expect(globalAgentNotice(caps, "es")?.toLowerCase()).toContain("agente global");
    expect(globalAgentNotice(caps, "en")?.toLowerCase()).toContain("global agent");
  });

  it("empareja por code, no por el texto castellano", () => {
    // Un warning de otro tipo (modelo no configurado) NO dispara el aviso global.
    const caps = agentCaps({
      warnings: [
        {
          code: "model_not_configured",
          es: "modelo no configurado",
          en: "model not configured",
        },
      ],
    });
    expect(isGlobalAgentWarning(caps)).toBe(false);
    expect(globalAgentNotice(caps, "es")).toBeNull();
  });

  it("un agente sin ese warning NO muestra el aviso (honestidad)", () => {
    expect(isGlobalAgentWarning(agentCaps())).toBe(false);
    expect(globalAgentNotice(agentCaps(), "es")).toBeNull();
  });

  it("un proyecto NUNCA dispara el aviso de agente global", () => {
    const projectCaps: CapabilitiesResponse = {
      ...agentCaps(),
      entity_type: "project",
      ser: null,
      warnings: [
        {
          code: WARN_GLOBAL_AGENT,
          es: "agente global: ...",
          en: "global agent: ...",
        },
      ],
    };
    expect(isGlobalAgentWarning(projectCaps)).toBe(false);
  });
});

describe("capability hub — checklist orden Persona → Saber → Hacer → Recordar (regla 6)", () => {
  it("el checklist de un agente sigue exactamente ese orden", () => {
    const steps = buildChecklist(agentCaps(), "es");
    expect(steps.map((s) => s.section)).toEqual(["ser", "saber", "hacer", "recordar"]);
  });

  it("cada paso queda 'done' según el estado HONESTO de su sección", () => {
    const steps = buildChecklist(agentCaps(), "es");
    // El agente base tiene las 4 vías activas → todos los pasos hechos.
    expect(steps.every((s) => s.done)).toBe(true);

    const empty = buildChecklist(
      agentCaps({
        saber: { knowledge_bases: [] },
        recordar: { memory_scope: "private", memory: [] },
        ser: {
          model_configured: false,
          provider: null,
          model: null,
          temperature: null,
          system_prompt_present: false,
        },
        hacer: { effective: [], unrestricted: false, shell_exec_effective: false },
      }),
      "es",
    );
    expect(empty.every((s) => !s.done)).toBe(true);
  });

  it("para un proyecto (sin persona propia) el paso SER se omite del checklist", () => {
    const projectCaps: CapabilitiesResponse = {
      ...agentCaps(),
      entity_type: "project",
      ser: null,
    };
    const steps = buildChecklist(projectCaps, "es");
    expect(steps.map((s) => s.section)).toEqual(["saber", "hacer", "recordar"]);
  });
});

describe("capability hub — nivel explícito y título", () => {
  it("resuelve la etiqueta de nivel bilingüe (Rol / Plataforma)", () => {
    const kbRol = resolveKBLevel({ kb_id: "k", name: "X", level: "rol", is_builtin: false }, "es");
    expect(kbRol.levelLabel).toBe("Rol");
    const kbPlat = resolveKBLevel(
      { kb_id: "k", name: "X", level: "plataforma", is_builtin: true },
      "en",
    );
    expect(kbPlat.levelLabel).toBe("Platform");
  });

  it("un nivel desconocido cae a 'Rol' (no filtra el slug crudo)", () => {
    const kb = resolveKBLevel({ kb_id: "k", name: "X", level: "weird", is_builtin: false }, "es");
    expect(kb.level).toBe("rol");
    expect(kb.levelLabel).toBe("Rol");
  });

  it("el título del Hub es por entidad y bilingüe", () => {
    expect(hubTitle("agent", "es")).toBe("Capacidad del agente");
    expect(hubTitle("project", "en")).toBe("Project capability");
    expect(hubTitle("team", "es")).toBe("Capacidad del equipo");
  });

  it("expone la etiqueta 'No disponible aún' reutilizable", () => {
    expect(UNAVAILABLE_LABEL.es).toBe("No disponible aún");
    expect(UNAVAILABLE_LABEL.en).toBe("Not available yet");
  });
});
