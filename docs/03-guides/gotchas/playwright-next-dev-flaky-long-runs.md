---
title: "El subset e2e contra next dev da rojos distintos en cada tanda"
area: admin-panel
encountered: 2026-09-30
stack: next 14, playwright 1.60
---

# El subset e2e contra next dev da rojos distintos en cada tanda

## Síntoma

El subset mockeado de Playwright (unos 115 specs) corrido en local con el `next dev` que
arranca el propio `playwright.config.ts` acaba con entre 12 y 18 rojos. **Cada tanda falla en
specs distintos**, y casi todos pasan al repetirlos solos. Medido el 2026-09-30: 18 rojos en
una tanda de 35 minutos y 12 en otra, con sólo tres specs en común entre las dos.

## Causa raíz

`next dev` compila cada ruta la primera vez que se pide. En una tanda larga, con la máquina
cargada, esa compilación cae dentro del presupuesto de un `expect` o de un clic, y el spec
que falla es el que tuvo la mala suerte de pedir una ruta fría en ese momento. No es un fallo
del producto ni, en general, del spec: es la latencia del servidor de desarrollo. CI no la
sufre porque sirve una build de producción.

Se suma a la otra trampa que agrava esa ventana, el clic antes de la hidratación
([playwright-click-before-hydration.md](./playwright-click-before-hydration.md)).

## Fix

Para dar un veredicto sobre el subset entero, reproducir la receta de CI
(`.github/workflows/ci.yml`, job del subset mockeado):

```bash
cd apps/admin-panel
NEXT_PUBLIC_API_URL=http://localhost:8001 npm run build
E2E_WEBSERVER_CMD="npm run start" npx playwright test <specs> --timeout=15000
```

La selección de specs es por exclusión, con la lista `vivos` del job. `next dev` sigue
valiendo para iterar sobre uno o dos specs.

## Cómo verificar el fix

Con esa receta, el subset dio 437 de 438 el 2026-09-30. El único rojo era real, un mock con
la forma equivocada, y se corrigió. La misma tanda contra `next dev` daba 12 rojos.
