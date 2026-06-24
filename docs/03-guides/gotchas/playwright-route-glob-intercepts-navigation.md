---
title: page.route("**/X") intercepta la navegación page.goto(".../X") (Playwright 1.60)
area: admin-panel, e2e, Playwright
encountered: 2026-06-17
stack: Playwright @playwright/test 1.60 + Next.js (next start)
---

## Síntoma

Specs e2e que mockean la API con `page.route` fallan con `toBeVisible` /
`locator.click` timeout: el elemento esperado (p. ej. `project-link-…`, una
card, un nav) **no aparece**. El snapshot de la página al fallar muestra que el
`<body>` es el **JSON crudo del mock**, no la app:

```yaml
- generic: '[{"id":"aaaa…","name":"Proyecto Click","status":"active",…}]'
```

Reproducible en local con `next dev` Y `next start` (NO es CI-only ni
dev-vs-prod).

## Causa raíz

El mock usa un glob "desnudo" del recurso:

```ts
await page.route("**/projects", (route) => route.fulfill({ body: JSON.stringify(FIXTURE) }));
await page.goto("/admin/projects"); // ← la página
```

En **Playwright 1.60** la semántica de globs de URL se endureció: `**/projects`
casa con **cualquier** URL que termine en `/projects`, **incluida la navegación
del documento** `http://localhost:3000/admin/projects` (su path acaba en
`/projects`). Resultado: `page.goto` recibe el JSON del mock como documento en
vez de la app, así que ningún elemento de la página se renderiza.

Los specs se escribieron con una Playwright anterior donde el glob no colisionaba;
el pin `^1.60.0` (intent committeado) trae la semántica nueva. Afecta solo a los
specs cuyo path de página **termina con el mismo recurso** que el glob
(`**/projects` ↔ `/admin/projects`, `**/agents/${id}` ↔ `/admin/agents/${id}`,
`**/teams/${id}`, `**/human-agents`, `**/memories`, …). Los globs con prefijo
distintivo (`**/api/plans`, `**/api/review/${id}`, `**/tenant-settings/memories`)
NO colisionan porque el path de página no contiene ese prefijo.

## Fix

Hacer que la ruta mockeada NO pueda casar con la navegación. La forma robusta
(independiente de versión de Playwright y de la URL base de la API) es un
**predicado por `pathname` exacto**:

```ts
// Antes (colisiona con /admin/projects):
await page.route("**/projects", handler);
// Después (solo casa con el fetch de la API, cuyo pathname es /projects):
await page.route((u) => new URL(u).pathname === "/projects", handler);

// Parametrizado:
await page.route((u) => new URL(u).pathname === `/projects/${PROJECT_ID}`, handler);
```

La navegación `http://localhost:3000/admin/projects` tiene `pathname`
`/admin/projects` (≠ `/projects`), así que el predicado no la intercepta; el
fetch a `http://localhost:8001/projects` sí (`pathname === "/projects"`).

Alternativa menos robusta: anclar el glob al origen de la API
(`*://localhost:8001/projects`), válido mientras `NEXT_PUBLIC_API_URL` use el
default `:8001`.

**Regla:** al mockear con `page.route`, nunca uses un glob de recurso "desnudo"
si existe una página cuyo path termina en ese recurso. Usa predicado por
`pathname` o ancla al host de la API.
