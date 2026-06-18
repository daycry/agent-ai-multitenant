import { test } from "@playwright/test";
import { credsFromEnv, login } from "../lib/auth";
import { generateManual, ManualDef } from "../lib/manual";

/**
 * Manual 00 — Introducción y primeros pasos.
 * Documenta el FLUJO DE LOGIN REAL paso a paso: pantalla de login → credenciales
 * → envío → panel principal (dashboard). No pre-autentica: ejecuta el login como
 * parte del manual y espera a que el dashboard cargue antes de capturarlo.
 */
const c = credsFromEnv();

const manual: ManualDef = {
  slug: "00-introduccion-y-primeros-pasos",
  order: "00",
  title: "Introducción y primeros pasos",
  audience: "Cualquier usuario del panel (administrador de tenant)",
  intro: `
    <p>La <b>Plataforma Agéntica Multi-Tenant</b> permite construir y orquestar
    equipos de agentes de IA que trabajan sobre tus proyectos de software. Toda
    la operativa se hace desde el <b>panel de administración</b> (este manual).</p>
    <p>Este primer manual te lleva paso a paso desde el inicio de sesión hasta el
    panel principal, explicando la organización multi-tenant: tus datos viven
    aislados dentro de tu <b>tenant</b> (organización) y nunca se mezclan con los
    de otros.</p>`,
  steps: [
    {
      title: "Abrir el panel: pantalla de inicio de sesión",
      goto: "/login",
      body: `<p>Abre la URL del panel en tu navegador. Verás la pantalla de
        <b>inicio de sesión</b> con el formulario de <b>email</b> y
        <b>contraseña</b>.</p>
        <ul>
          <li>Si tu organización tiene <b>SSO</b> (Google, Microsoft, etc.)
            configurado, verás además los botones de proveedor bajo el formulario.</li>
          <li>Tras varios intentos fallidos el acceso se limita temporalmente
            (protección anti-fuerza-bruta).</li>
        </ul>`,
    },
    {
      title: "Introducir las credenciales",
      body: `<p>Escribe tu <b>email</b> y tu <b>contraseña</b>. La contraseña se
        muestra siempre enmascarada y se almacena de forma segura (hash
        <code>argon2id</code>), nunca en claro. Cuando estén rellenos, pulsa
        <code>Sign in</code>.</p>`,
      action: async (page) => {
        // Rellenar JUSTO antes de capturar (el formulario mostrará tus datos).
        await page.locator("#email").fill(c.email);
        await page.locator("#password").fill(c.password);
      },
    },
    {
      title: "Acceder al panel (el dashboard se carga tras autenticar)",
      body: `<p>Al pulsar <code>Sign in</code>, la plataforma valida tus
        credenciales y resuelve a qué <b>organización (tenant)</b> perteneces:
        si es una sola, entras directo; si son varias, eliges una en el
        selector de tenant. A continuación llegas al <b>panel principal</b>.</p>`,
      settleMs: 1800,
      // Login robusto (re-rellena + envía + espera a /admin), idéntico al resto
      // de manuales. La captura se toma ya en el dashboard, no en el login.
      action: async (page) => {
        await login(page, c);
      },
    },
    {
      title: "El panel principal (dashboard)",
      goto: "/admin/dashboard",
      settleMs: 1500,
      body: `<p>Este es el <b>panel principal</b>. Desde aquí accedes a todas las
        áreas mediante la <b>navegación lateral</b>: proyectos, agentes, equipos,
        planes, conocimiento (RAG), asistente, memoria, marketplace, ajustes, etc.
        El panel muestra un resumen del estado de tu tenant: actividad reciente,
        ejecuciones y accesos rápidos.</p>`,
    },
    {
      title: "Tu organización (tenant) activa",
      goto: "/admin/dashboard",
      settleMs: 1200,
      body: `<p>Todo lo que ves pertenece a tu <b>tenant</b> activo. La plataforma
        es <b>multi-tenant</b>: cada organización tiene sus propios proyectos,
        agentes y datos, completamente aislados (a nivel de base de datos con
        Row-Level Security).</p>
        <p>Si tu usuario pertenece a <b>varias organizaciones</b>, tras el login
        verás un <b>selector de tenant</b> para elegir en cuál trabajar; puedes
        cambiar de tenant en cualquier momento desde el menú.</p>`,
    },
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  test.setTimeout(180_000);
  // NO pre-login: este manual documenta el flujo de login completo.
  await generateManual(page, manual);
});
