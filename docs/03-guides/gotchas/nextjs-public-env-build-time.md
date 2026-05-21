---
title: `NEXT_PUBLIC_*` env vars se inlinean al compilar; cambiarlas en runtime no las actualiza
area: next.js
encountered: 2026-05-21
stack: Next.js 14.2 App Router, Playwright webServer
---

## Síntoma

`lib/api.ts` define:

```typescript
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";
```

Levantamos uvicorn en `:8002` y los E2E de Playwright fallan: el
navegador hace POST a `http://localhost:8001/auth/login` (que no
existe) y queda en `/login` indefinidamente. Pero la API en `:8002`
funciona perfecto desde `curl` y desde `Invoke-RestMethod`.

## Causa raíz

`NEXT_PUBLIC_*` son env vars **públicas**: Next las **embebe en
el bundle** del cliente al compilar (build-time inlining). El
valor que ve `process.env.NEXT_PUBLIC_API_URL` es el que estaba
presente cuando `next build` o `next dev` arrancó, no el actual.

Si exportas la variable **después** de `npm run dev`, los modulos
ya compilados siguen usando el valor anterior (o el fallback). El
mismo fenómeno aplica en producción con `next build`.

## Fix

Exporta `NEXT_PUBLIC_*` **antes** de arrancar el dev server o el
build, no después:

```bash
# MAL — Next ya cacheó http://localhost:8001
npm run dev
export NEXT_PUBLIC_API_URL=http://127.0.0.1:8002

# BIEN
export NEXT_PUBLIC_API_URL=http://127.0.0.1:8002
npm run dev
```

En PowerShell:

```powershell
$env:NEXT_PUBLIC_API_URL = "http://127.0.0.1:$ApiPort"
npm run e2e   # Playwright lanza npm run dev internamente
```

Para builds productivos: setea la variable en el entorno del runner
de CI o en el `.env.production` antes de `npm run build`.

## Cómo verificar el fix

```bash
# Compila con la variable seteada
NEXT_PUBLIC_API_URL=http://127.0.0.1:8002 npm run build

# La URL aparece literal en el bundle del cliente:
grep -r "127.0.0.1:8002" .next/static/chunks/  # → match en JS minificado
```

`scripts/dev/run-e2e.{ps1,sh}` exporta esta variable antes de
`npm run e2e` para que Playwright apunte al uvicorn correcto.
