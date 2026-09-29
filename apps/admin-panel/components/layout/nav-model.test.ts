/**
 * Las TRES áreas del panel, decididas por ruta y por rol (`task_ui_01`).
 *
 * Plan [`ui-reestructuracion-2026-09-09`](../../../../docs/roadmap/ui-reestructuracion-2026-09-09.md),
 * ola 1. El shell deja de tener UNA barra lateral con seis grupos y pasa a
 * elegir entre tres: Trabajo (el tenant), Proyecto (contextual, dentro de uno) y
 * Sistema (sólo System Admin, ADR 0117 c).
 *
 * Esta decisión es lógica pura y por eso se prueba aquí, en entorno `node` y sin
 * React: el render de las tres barras lo cubre el test del shell y el recorrido
 * de verdad la e2e. Es el mismo reparto que ya usaban `visibleNavGroups` y sus
 * tests hermanos — la decisión vive donde se puede probar sin montar un árbol.
 *
 * ## Por qué `areaForPath` puede devolver `null`
 *
 * Hay rutas que NO son de un área concreta: `/admin/docs` se enseña en Trabajo
 * (grupo Ayuda, cualquiera lo ve) y también se lista en Sistema. Si la función
 * devolviera un área para ellas, entrar en la documentación desde Sistema te
 * echaría a Trabajo. `null` significa «no tengo preferencia, quédate donde
 * estás», y es lo que permite que la regla del plan —«un enlace a una ruta de
 * Sistema desde Trabajo abre el área Sistema»— no tenga el efecto contrario en
 * las rutas compartidas.
 */

import { describe, expect, it } from "vitest";

import {
  areaForPath,
  projectIdFromPath,
  projectNavItems,
  visibleAreas,
  type NavScope,
} from "./nav-model";

const MEMBER: NavScope = { isTenantAdmin: false, isSystemAdmin: false, isSystemOwner: false };
const TENANT_ADMIN: NavScope = { isTenantAdmin: true, isSystemAdmin: false, isSystemOwner: false };
const SYSTEM_ADMIN: NavScope = { isTenantAdmin: true, isSystemAdmin: true, isSystemOwner: false };
const OWNER: NavScope = { isTenantAdmin: true, isSystemAdmin: true, isSystemOwner: true };

const PROJECT_ID = "8f14e45f-ceea-467a-9f0a-1a2b3c4d5e6f";

describe("areaForPath", () => {
  it("deja en Trabajo el dashboard, la bandeja y los recursos del tenant", () => {
    for (const path of [
      "/admin/dashboard",
      "/admin/inbox",
      "/admin/agents",
      "/admin/agents/abc",
      "/admin/memories",
      "/admin/marketplace",
    ]) {
      expect(areaForPath(path), path).toBe("work");
    }
  });

  it("distingue el portfolio de proyectos de estar DENTRO de un proyecto", () => {
    // El listado y el alta son del tenant: su barra es la de Trabajo.
    expect(areaForPath("/admin/projects")).toBe("work");
    expect(areaForPath("/admin/projects/new")).toBe("work");
    // Dentro de uno, la barra cambia a la del proyecto.
    expect(areaForPath(`/admin/projects/${PROJECT_ID}`)).toBe("project");
    expect(areaForPath(`/admin/projects/${PROJECT_ID}/plans`)).toBe("project");
    expect(areaForPath(`/admin/projects/${PROJECT_ID}/plans/abc`)).toBe("project");
  });

  it("manda a Sistema las rutas de plataforma y de córtex", () => {
    for (const path of [
      "/admin/users",
      "/admin/invitations",
      "/admin/llm-providers",
      "/admin/model-prices",
      "/admin/ollama",
      "/admin/backup",
      "/admin/backup/restore",
      "/admin/marketplace/review",
      "/admin/settings/sso",
      "/admin/settings/sso/saml",
      "/admin/settings/platform-defaults",
      "/admin/cortex",
      "/admin/cortex/mind",
    ]) {
      expect(areaForPath(path), path).toBe("system");
    }
  });

  it("NO confunde los ajustes del tenant con los de plataforma", () => {
    // El caso que un `startsWith("/admin/settings")` se llevaría por delante:
    // tres rutas hermanas, dos áreas distintas.
    expect(areaForPath("/admin/settings")).toBe("work");
    expect(areaForPath("/admin/settings/hourly-rate")).toBe("work");
    expect(areaForPath("/admin/settings/memories")).toBe("work");
    expect(areaForPath("/admin/settings/platform-defaults")).toBe("system");
  });

  it("NO confunde el marketplace del tenant con su cola de revisión", () => {
    expect(areaForPath("/admin/marketplace")).toBe("work");
    expect(areaForPath("/admin/marketplace/private")).toBe("work");
    expect(areaForPath("/admin/marketplace/review")).toBe("system");
  });

  it("devuelve null para las rutas que viven en las dos áreas", () => {
    // `/admin/docs` se enseña en Trabajo y se lista en Sistema: no fuerza área.
    expect(areaForPath("/admin/docs")).toBeNull();
  });

  it("devuelve null fuera de `/admin` y con pathname vacío", () => {
    expect(areaForPath("/login")).toBeNull();
    expect(areaForPath(null)).toBeNull();
  });
});

describe("visibleAreas", () => {
  it("da sólo Trabajo a un miembro y a un tenant_admin", () => {
    expect(visibleAreas(MEMBER)).toEqual(["work"]);
    expect(visibleAreas(TENANT_ADMIN)).toEqual(["work"]);
  });

  it("añade Sistema al System Admin y al System Owner", () => {
    expect(visibleAreas(SYSTEM_ADMIN)).toEqual(["work", "system"]);
    expect(visibleAreas(OWNER)).toEqual(["work", "system"]);
  });

  it("no incluye `project`: no se elige, se entra en él", () => {
    // El área de proyecto no es una pestaña del selector — se llega entrando en
    // un proyecto y se sale con «← Proyectos». Si apareciera en el selector,
    // habría que inventar «¿qué proyecto?» al pulsarla.
    expect(visibleAreas(OWNER)).not.toContain("project");
  });
});

describe("projectIdFromPath", () => {
  it("saca el id de una ruta dentro del proyecto", () => {
    expect(projectIdFromPath(`/admin/projects/${PROJECT_ID}`)).toBe(PROJECT_ID);
    expect(projectIdFromPath(`/admin/projects/${PROJECT_ID}/mcp-servers`)).toBe(PROJECT_ID);
  });

  it("devuelve null en el portfolio, en el alta y fuera de proyectos", () => {
    expect(projectIdFromPath("/admin/projects")).toBeNull();
    expect(projectIdFromPath("/admin/projects/new")).toBeNull();
    expect(projectIdFromPath("/admin/dashboard")).toBeNull();
    expect(projectIdFromPath(null)).toBeNull();
  });
});

describe("projectNavItems", () => {
  const suffix = (href: string) => href.replace(`/admin/projects/${PROJECT_ID}`, "");

  it("todas sus entradas cuelgan de ESE proyecto", () => {
    for (const item of projectNavItems(PROJECT_ID)) {
      expect(item.href.startsWith(`/admin/projects/${PROJECT_ID}`), item.href).toBe(true);
      expect(item.id, item.href).toBeTruthy();
    }
  });

  it("son las ocho del plan, en su orden, empezando por el resumen", () => {
    const items = projectNavItems(PROJECT_ID);
    expect(items[0]?.href).toBe(`/admin/projects/${PROJECT_ID}`);
    expect(items.map((item) => item.labelKey)).toEqual([
      "projectOverview",
      "projectBoard",
      "projectRuns",
      "projectTeam",
      "projectCapabilities",
      "projectKnowledge",
      "projectCosts",
      "projectSettings",
    ]);
  });

  it("apunta a las rutas actuales del proyecto, conservándolas", () => {
    const bySuffix = Object.fromEntries(
      projectNavItems(PROJECT_ID).map((item) => [item.labelKey, suffix(item.href)]),
    );
    expect(bySuffix.projectCapabilities).toBe("/mcp-servers");
    expect(bySuffix.projectKnowledge).toBe("/knowledge-bases");
    expect(bySuffix.projectBoard).toBe("/board");
    // Ajustes es la ficha del proyecto (la raíz), con ancla a su sección.
    expect(bySuffix.projectSettings).toBe("#settings");
  });

  it("deja VISIBLES pero DESHABILITADAS las pestañas cuya pantalla aún no existe", () => {
    // Runs, Equipo y Costes las hace `task_ui_12`. Una entrada de menú a una ruta
    // que nadie sirve es un 404 con aspecto de producto: se pinta, no se enlaza.
    // Cuando esa tarea cree cada `page.tsx`, quita la entrada de este conjunto.
    const disabled = projectNavItems(PROJECT_ID)
      .filter((item) => item.disabled === true)
      .map((item) => item.labelKey);
    expect(disabled).toEqual(["projectRuns", "projectTeam", "projectCosts"]);
  });

  it("agrupa bajo cada pestaña las pantallas que antes eran entradas sueltas", () => {
    const items = projectNavItems(PROJECT_ID);
    const owner = (path: string) =>
      items.find((item) => item.activeSuffixes?.includes(path))?.labelKey;
    expect(owner("/plans")).toBe("projectBoard");
    expect(owner("/tasks")).toBe("projectBoard");
    expect(owner("/commands")).toBe("projectCapabilities");
    expect(owner("/agent-tools-diagnostic")).toBe("projectCapabilities");
    expect(owner("/memories")).toBe("projectSettings");
    expect(owner("/incoming-webhooks")).toBe("projectSettings");
    expect(owner("/dep-cache")).toBe("projectSettings");
  });
});
