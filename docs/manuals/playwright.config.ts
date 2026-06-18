import { defineConfig } from "@playwright/test";

/**
 * Generador de manuales de usuario en PDF (Plan: manuales reutilizables).
 *
 * Cada "spec" en specs/ es un MANUAL: navega la app paso a paso, captura un
 * pantallazo por paso y renderiza un PDF en docs/manuals/pdf/. Se REGENERA
 * volviendo a ejecutar `npm run manuals` cuando la UI cambia.
 *
 * Dependencias EXTERNAS que el caller debe tener arriba (las levanta
 * scripts/dev/generate-manuals.ps1, que reutiliza el mecanismo de run-e2e):
 *   - el stack docker dev (postgres/redis/vault/minio)
 *   - el api-server en http://localhost:8001 (uvicorn)
 *   - un usuario de login válido (por defecto el admin del tenant demo)
 * Este config auto-arranca el admin-panel (`next dev` :3000) salvo que ya haya
 * uno corriendo.
 *
 * Variables de entorno:
 *   MANUALS_BASE_URL        dónde apunta el navegador (def: http://localhost:3000)
 *   MANUALS_EMAIL           email de login (def: demo@demo-manuales.local)
 *   MANUALS_PASSWORD        contraseña       (def: demo-manuales-pw-2026)
 *   MANUALS_TENANT          nombre/slug del tenant a elegir tras login (def: Demo Manuales)
 *   MANUALS_API_URL         base del api-server para el admin-panel (def: http://localhost:8001)
 */
const ADMIN_PANEL_DIR = "../../apps/admin-panel";

// Stack CONTENERIZADO (recomendado): la app entera corre en Docker tras Caddy en
// http://localhost:8080 (docker/docker-compose.manuals.yml). En ese modo NO hay
// que arrancar `next dev`: se fija MANUALS_NO_WEBSERVER=1 y MANUALS_BASE_URL al
// origen de Caddy. En modo host (legacy) Playwright auto-arranca el admin-panel.
const NO_WEBSERVER = !!process.env.MANUALS_NO_WEBSERVER;

export default defineConfig({
  testDir: "./specs",
  // Los manuales se nombran *.manual.ts (no *.spec.ts).
  testMatch: "**/*.manual.ts",
  // Un manual detrás de otro: comparten el navegador y el orden importa para la
  // narrativa; además next dev compila bajo demanda y el paralelismo lo penaliza.
  fullyParallel: false,
  workers: 1,
  // Generar un manual no debe "fallar" por un timeout puntual de compilación;
  // reintenta una vez. Los pasos que no encuentran su pantalla se registran como
  // "no disponible" en el PDF en vez de abortar (ver lib/manual.ts).
  retries: 1,
  timeout: 180_000,
  reporter: [["list"]],
  use: {
    baseURL: process.env.MANUALS_BASE_URL ?? "http://localhost:3000",
    // Viewport amplio para pantallazos legibles y completos.
    viewport: { width: 1440, height: 900 },
    actionTimeout: 20_000,
    navigationTimeout: 45_000,
    locale: "es-ES",
  },
  ...(NO_WEBSERVER
    ? {}
    : {
        webServer: {
          command: process.env.MANUALS_WEBSERVER_CMD ?? "npm run dev",
          cwd: ADMIN_PANEL_DIR,
          url: "http://localhost:3000",
          reuseExistingServer: true,
          timeout: 180_000,
          stdout: "pipe" as const,
          stderr: "pipe" as const,
          env: {
            NEXT_PUBLIC_API_URL: process.env.MANUALS_API_URL ?? "http://localhost:8001",
          },
        },
      }),
});
