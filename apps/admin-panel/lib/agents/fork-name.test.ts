import { describe, expect, it } from "vitest";

import { namesTakenInProject, suggestForkName } from "./fork-name";
import { translate } from "@/lib/i18n/translate";
import type { Translator } from "@/lib/i18n/translate";

/** El traductor real en castellano: la sugerencia sale del diccionario. */
const t: Translator<"agents"> = (key, vars) => translate("es", "agents", key, vars);

describe("suggestForkName", () => {
  it("sin nada cogido sugiere la copia llana", () => {
    expect(suggestForkName("Ada", [], t)).toBe("Ada (copia)");
  });

  it("esquiva el nombre que ya existe en el destino", () => {
    expect(suggestForkName("Ada", ["Ada", "Ada (copia)"], t)).toBe("Ada (copia 2)");
  });

  it("sigue subiendo mientras el numerado también esté cogido", () => {
    expect(suggestForkName("Ada", ["Ada (copia)", "Ada (copia 2)", "Ada (copia 3)"], t)).toBe(
      "Ada (copia 4)",
    );
  });

  it("no propone un nombre que sólo se distinga por mayúsculas o espacios", () => {
    // El índice de Postgres SÍ los distingue, así que «ADA (COPIA)» pasaría;
    // pero dos agentes que se leen igual en un role_map son un problema peor.
    expect(suggestForkName("Ada", ["  ADA (COPIA)  "], t)).toBe("Ada (copia 2)");
  });

  it("el traductor manda: en inglés sugiere en inglés", () => {
    const tEn: Translator<"agents"> = (key, vars) => translate("en", "agents", key, vars);
    expect(suggestForkName("Ada", ["Ada (copy)"], tEn)).toBe("Ada (copy 2)");
  });
});

describe("namesTakenInProject", () => {
  const AGENTS = [
    { name: "Ada", project_id: null },
    { name: "Linus", project_id: "p1" },
    { name: "Grace", project_id: "p2" },
  ];

  it("sólo cuenta los del proyecto destino", () => {
    expect(namesTakenInProject(AGENTS, "p1")).toEqual(["Linus"]);
  });

  it("un agente global no ocupa nombre en un proyecto (vive en el otro índice)", () => {
    expect(namesTakenInProject(AGENTS, "p2")).toEqual(["Grace"]);
  });

  it("sin destino elegido no hay nada cogido", () => {
    expect(namesTakenInProject(AGENTS, "")).toEqual([]);
  });
});
