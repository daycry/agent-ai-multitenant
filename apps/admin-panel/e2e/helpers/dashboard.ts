/**
 * `GET /tenant/dashboard` vacío con la FORMA que espera la página (`task_ui_02`).
 *
 * Un comodín que responde `[]` a toda la API rompe el dashboard: lee
 * `data.spend.reason` y el límite de error se lleva también la cabecera, así que
 * un spec que sólo quería aterrizar en `/admin/dashboard` falla buscando
 * `admin-header` o `user-menu`. Lo pagaron `logout-cache-purge` y `sso-roundtrip`.
 */
export const EMPTY_DASHBOARD = {
  pending: { total: 0, by_kind: {}, oldest: [] },
  projects: [],
  spend: { available: false, reason: "forbidden" },
  issues: [],
  activity: [],
  mcp: [],
  generated_at: "2026-09-30T00:00:00Z",
};
