/**
 * Siembra datos REALES en el tenant de manuales (idempotente) para que los
 * manuales muestren contenido en vez de estados vacíos: dos proyectos (uno PHP
 * "Hello World", uno Node), un plan con varias tareas, y deja el id del
 * proyecto PHP en assets/seed.json para que los specs naveguen su hub.
 *
 * Reutilizable: se ejecuta contra el stack contenerizado (Caddy :8080) antes de
 * generar los manuales. NO duplica si ya existen (busca por nombre).
 *
 *   node lib/seed-demo-data.mjs            # usa http://localhost:8080/api
 *   MANUALS_API_BASE=http://host/api node lib/seed-demo-data.mjs
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const API = (process.env.MANUALS_API_BASE || "http://localhost:8080/api").replace(/\/$/, "");
const EMAIL = process.env.MANUALS_EMAIL || "demo@example.com";
const PASSWORD = process.env.MANUALS_PASSWORD || "demo-manuales-pw-2026";

let TOKEN = "";
async function api(method, p, body) {
  const res = await fetch(`${API}${p}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    throw new Error(
      `${method} ${p} → ${res.status}: ${typeof data === "string" ? data : JSON.stringify(data)}`,
    );
  }
  return data;
}

const TENANT = process.env.MANUALS_TENANT || "Demo Manuales";

async function login() {
  // 1) login → token SIN claim de tenant (tid).
  const r = await api("POST", "/auth/login", { email: EMAIL, password: PASSWORD });
  TOKEN = r.access_token || r.token || r.accessToken;
  if (!TOKEN) throw new Error("login sin token");
  // 2) resolver sesión: un único tenant ya devuelve token con tid; varios exigen
  //    elegir (el usuario demo es system_admin y ve varios tenants).
  const resolved = await api("GET", "/auth/session/resolve");
  if (resolved.access_token) {
    TOKEN = resolved.access_token;
    console.log("  login OK (tenant auto-resuelto)");
    return;
  }
  const memberships = resolved.memberships || [];
  const match =
    memberships.find(
      (m) =>
        m.tenant_name === TENANT ||
        m.name === TENANT ||
        m.tenant_slug === "demo-manuales" ||
        m.slug === "demo-manuales",
    ) || memberships[0];
  const tenantId = match && (match.tenant_id || match.id);
  if (!tenantId) throw new Error(`no encuentro el tenant '${TENANT}' en memberships`);
  // 3) seleccionar tenant → token CON tid.
  const sel = await api("POST", "/auth/session/select-tenant", { tenant_id: tenantId });
  TOKEN = sel.access_token;
  console.log(`  login OK (tenant '${TENANT}' = ${tenantId})`);
}

async function ensureProject(name, description, runtime) {
  const existing = await api("GET", "/projects");
  const found = (Array.isArray(existing) ? existing : []).find((p) => p.name === name);
  if (found) {
    console.log(`  proyecto ya existe: ${name} (${found.id})`);
    return found;
  }
  const created = await api("POST", "/projects", {
    name,
    description,
    default_runtime_template: runtime,
  });
  console.log(`  proyecto creado: ${name} (${created.id})`);
  return created;
}

async function ensurePlanWithTasks(projectId) {
  const plans = await api("GET", `/projects/${projectId}/plans`).catch(() => []);
  let plan = (Array.isArray(plans) ? plans : []).find((p) => p.title?.includes("Hello World"));
  if (!plan) {
    plan = await api("POST", `/projects/${projectId}/plans`, {
      title: "MVP — API Hello World en PHP",
      description:
        "Plan de construcción del primer endpoint: un servicio PHP que responde " +
        '{"message":"Hello, World!"} con su test PHPUnit y su documentación.',
    });
    console.log(`  plan creado: ${plan.id}`);
  } else {
    console.log(`  plan ya existe: ${plan.id}`);
  }
  const tasks = await api("GET", `/projects/${projectId}/tasks`).catch(() => []);
  if ((Array.isArray(tasks) ? tasks : []).length >= 4) {
    console.log(`  tareas ya existen (${tasks.length})`);
    return plan;
  }
  const specs = [
    {
      title: "Definir el endpoint GET /hello",
      description: "Especificar la ruta, el contrato JSON de respuesta y los códigos de estado.",
      acceptance_criteria: [
        "Devuelve 200",
        'Cuerpo {"message":"Hello, World!"}',
        "Content-Type application/json",
      ],
    },
    {
      title: "Implementar el controlador en PHP",
      description: "Crear el controlador que responde el saludo, siguiendo PSR-12.",
      acceptance_criteria: ["Clase HelloController", "Sin warnings de linter"],
    },
    {
      title: "Escribir el test PHPUnit",
      description: "Test que verifica el código de estado y el cuerpo JSON del endpoint.",
      acceptance_criteria: ["Test verde en runtime php-phpunit", "Cobertura del happy-path"],
    },
    {
      title: "Documentar el endpoint en /docs",
      description: "Añadir la referencia del endpoint a la documentación del proyecto.",
      acceptance_criteria: ["Entrada en 04-reference", "Ejemplo de petición y respuesta"],
    },
  ];
  for (const s of specs) {
    await api("POST", `/projects/${projectId}/tasks`, { ...s, plan_id: plan.id }).catch((e) =>
      console.log(`  (aviso) tarea "${s.title}": ${e.message}`),
    );
  }
  console.log(`  ${specs.length} tareas creadas`);
  return plan;
}

async function main() {
  console.log(`Sembrando datos demo en ${API}`);
  await login();
  const php = await ensureProject(
    "Hello World PHP",
    "Proyecto de demostración: un microservicio PHP que expone un endpoint GET /hello " +
      'devolviendo {"message":"Hello, World!"}, con sus tests PHPUnit. Sirve de ejemplo end-to-end ' +
      "para los manuales (creación, planes, tareas, ejecución por agentes).",
    "php-phpunit",
  );
  await ensureProject(
    "Catálogo de Productos (API REST)",
    "Segundo proyecto de ejemplo (Node + Jest): una API REST de catálogo para ilustrar el " +
      "trabajo con varios proyectos en el mismo tenant.",
    "node-jest",
  );
  const plan = await ensurePlanWithTasks(php.id);

  const out = path.join(HERE, "..", "assets", "seed.json");
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(
    out,
    JSON.stringify(
      { phpProjectId: php.id, phpProjectName: php.name, phpPlanId: plan ? plan.id : null },
      null,
      2,
    ),
  );
  console.log(`\nSeed completo. PHP project id → ${php.id} (guardado en assets/seed.json)`);
}

main().catch((e) => {
  console.error("SEED FALLÓ:", e.message);
  process.exit(1);
});
