---
title: `.next/` cache corrupto tras cambiar de branch o hot-reload roto
area: next.js
encountered: 2026-05-28
stack: Next.js 14, npm run dev en Windows
---

## Síntoma

El admin-panel devuelve **500 Server Error** en cualquier ruta (incluso
`/favicon.ico`) con un stack como:

```
Server Error
Error: Cannot find module './682.js'
Require stack:
- C:\...\apps\admin-panel\.next\server\webpack-runtime.js
- C:\...\apps\admin-panel\.next\server\app\favicon.ico\route.js
- ...next\dist\server\require.js
- ...next\dist\server\dev\hot-middleware.js
```

El número del chunk (`./682.js`, `./415.js`, …) varía entre ejecuciones.
El error aparece después de:

- cambiar de branch git (`git checkout otra-rama`) mientras
  `npm run dev` seguía corriendo, o
- un hot-reload en mitad de un edit grande, o
- el antivirus de Windows bloqueando un escritura puntual de chunks.

## Causa raíz

El compilador de Next.js mantiene en `.next/` un **manifest** que
mapea rutas → IDs de chunk (`123.js`, `682.js`, …) y los chunks
correspondientes. Hot-reload re-escribe el manifest y los chunks por
separado; en Windows, el antivirus o un branch switch pueden:

1. Re-escribir el manifest con el nuevo ID del chunk **antes** de
   terminar de escribir el chunk con el contenido nuevo, o
2. Dejar el manifest apuntando a un chunk que el nuevo bundle ya no
   genera (cambió el árbol de imports tras el branch switch).

Cuando llega un request, Next.js hace `require('./682.js')` y el
archivo no existe → 500 con `Cannot find module`.

No es un bug del código fuente — es **cache podrido**.

## Fix

Para el dev server, borra `.next/` (y el cache de webpack en
`node_modules/.cache` si existe), y re-arranca:

```powershell
# Para el dev server (Ctrl+C en la terminal donde corre).
# Luego:
Remove-Item -Recurse -Force apps\admin-panel\.next, apps\admin-panel\node_modules\.cache -ErrorAction SilentlyContinue
cd apps\admin-panel
npm run dev
```

One-liner desde cualquier shell:

```powershell
Stop-Process -Name node -ErrorAction SilentlyContinue;
Remove-Item -Recurse -Force apps\admin-panel\.next, apps\admin-panel\node_modules\.cache -ErrorAction SilentlyContinue;
cd apps\admin-panel; npm run dev
```

Tras el rebuild, la primera carga de cada ruta tarda 2-5 s extra (Next
re-compila). Esto es normal.

## Prevención

- **Para `npm run dev` antes de cambiar de branch.** Cualquier
  `git checkout`, `git pull`, `git rebase` que cambie archivos en
  `apps/admin-panel/**/*.{ts,tsx,js,jsx,css}` invalida la mitad del
  cache.
- Si vas a iterar mucho en el admin-panel, considera **excluir `.next/`
  del antivirus** (Windows Defender: Settings → Virus & threat
  protection → Exclusions → Folder → ruta absoluta del
  `apps/admin-panel/.next/`).
- En producción esto no aplica: `npm run build` genera un manifest
  consistente y `npm run start` lo sirve sin re-escribir nada.

## Cómo verificar el fix

```powershell
# Después del rebuild, abre cualquier ruta:
curl http://localhost:3000/admin/dashboard
# → HTML válido (no JSON con stack trace).
```

Si el error persiste tras borrar `.next/`, prueba con `node_modules`
limpio: `Remove-Item -Recurse -Force apps\admin-panel\node_modules`,
`cd apps\admin-panel; npm ci; npm run dev`. Eso descarta corrupción
de las dependencias.

## Síntomas relacionados

- **`Cannot find module 'pino-pretty'`** o similar — la entry de
  `package.json` cambió de branch pero `node_modules` no se actualizó.
  `npm ci` lo arregla.
- **`webpack-runtime.js not found`** — variante del mismo problema con
  el runtime de webpack. Mismo fix.
- **Hidratación inconsistente / mismatch** — no es este bug. Suele
  venir de extensiones del navegador (ColorZilla, Grammarly) que
  inyectan atributos post-SSR. El admin-panel ya usa
  `suppressHydrationWarning` en `<body>`.
