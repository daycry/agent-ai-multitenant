/**
 * Mockear el api-server SIN mockear el propio panel.
 *
 * El subset autocontenido (el que CI corre sin backend) intercepta las llamadas
 * con `page.route`. El patrón que se venía usando era un glob sin origen:
 *
 *     await page.route("**​/agents/" + AGENT_ID, …)   // ⚠️ demasiado ancho
 *
 * y ese glob NO distingue entre las dos cosas que el navegador pide:
 *
 *   1. `http://localhost:8001/agents/{id}`        — la llamada de `apiFetch`.
 *   2. `http://localhost:3000/admin/agents/{id}`  — la NAVEGACIÓN del propio
 *      `page.goto(...)`, o sea el documento HTML de la pantalla.
 *
 * `**` casa con `http://localhost:3000/admin`, así que el mock respondía
 * también a la navegación y el navegador acababa enseñando el JSON del fixture
 * como si fuera la página. Verificado el 2026-08-19 en el snapshot de
 * `agent-edit-delete.spec.ts`: el `# Page snapshot` del fallo era literalmente
 * el objeto del agente serializado, sin una sola etiqueta del panel. De ahí que
 * el síntoma fuese siempre el mismo y despistara — «el testid no aparece
 * nunca» — cuando lo que no había era pantalla.
 *
 * `apiRoute()` ata el patrón al ORIGEN del api-server, que es el único sitio
 * donde vive la API. La navegación del panel (:3000) deja de casar, y el mock
 * sigue casando con lo que debe.
 *
 * El origen se lee de `NEXT_PUBLIC_API_URL` porque es la MISMA variable con la
 * que se compila el panel (`lib/api.ts` la lee en build time, y CI compila con
 * `http://localhost:8001`). Si alguien construye el panel apuntando a otro
 * sitio y no exporta la variable al correr Playwright, los mocks dejan de casar
 * y los specs fallan RUIDOSAMENTE — que es lo que queremos: el modo de fallo
 * silencioso era justo el anterior.
 */

/** Origen del api-server contra el que están escritos los mocks. */
export const API_ORIGIN = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

/**
 * Patrón de `page.route` acotado al api-server.
 *
 * `path` es la ruta tal cual la pide `lib/api.ts` (con `/` inicial) y admite los
 * comodines de Playwright (`*`, `**`), p. ej. `apiRoute("/agents/*​/tools")`.
 */
export function apiRoute(path: string): string {
  return `${API_ORIGIN}${path}`;
}
