---
title: "Un clic de Playwright antes de la hidratación se pierde sin error"
area: admin-panel
encountered: 2026-09-29
stack: next 14 (next dev), playwright 1.60, react 18
---

# Un clic de Playwright antes de la hidratación se pierde sin error

## Síntoma

Un e2e mockeado falla por tiempo esperando algo que el clic debía provocar: el menú de
usuario no se abre, una pestaña no cambia, un filtro no llega a la petición (la URL sale
sin `provider=anthropic`) o un diálogo no aparece. El elemento sobre el que se hizo clic
**sí existe y es visible**. Si el mismo spec se corre solo, a veces pasa: es intermitente
y empeora cuanto más tarda `next dev` en compilar la ruta.

## Causa raíz

`page.goto(url, { waitUntil: "domcontentloaded" })` vuelve en cuanto llega el HTML del
servidor. El botón ya está pintado, pero React todavía no ha hidratado el árbol y sus
manejadores `onClick` no están enganchados. Playwright hace clic sobre HTML inerte: no hay
error, no hay excepción, el evento simplemente no lo recibe nadie. Con `next dev`, que
compila cada ruta la primera vez, esa ventana dura segundos.

## Fix

Esperar a una señal **real** de hidratación antes del primer clic:

- `components/layout/hydration-marker.tsx` pone `data-hydrated="true"` en `<html>` desde un
  `useEffect`, que sólo corre después de hidratar. Lo monta `AdminShell`, así que existe en
  toda ruta bajo `app/admin/layout.tsx`.
- `e2e/helpers/hydration.ts` exporta `waitForHydration(page)`, que espera a ese atributo.
  Se llama tras cada `page.goto` en los specs que interactúan.

Esperar a que el elemento sea visible no basta: lo es antes de hidratar.

## Cómo verificar el fix

```bash
cd apps/admin-panel
npx vitest run components/layout/hydration-marker.test.tsx
E2E_EXPECT_TIMEOUT=30000 npx playwright test e2e/sidebar-complete.spec.ts --reporter=line
```

El spec del menú de usuario (`la seguridad de la cuenta vive en el menú de usuario`) fallaba
siempre sin la espera y pasa con ella.
