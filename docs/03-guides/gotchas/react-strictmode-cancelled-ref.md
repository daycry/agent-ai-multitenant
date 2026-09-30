---
title: "Un cancelledRef que no se rearma al montar descarta respuestas bajo StrictMode"
area: admin-panel
encountered: 2026-09-30
stack: react 18 (StrictMode en next dev)
---

# Un cancelledRef que no se rearma al montar descarta respuestas bajo StrictMode

## Síntoma

Un diálogo que arranca una petición al abrirse se queda para siempre en su estado de carga
en desarrollo (el device flow de Copilot en «Iniciando…»), aunque la petición responde
bien. En producción funciona.

## Causa raíz

El componente usa un `cancelledRef` para ignorar respuestas que llegan tras desmontar: lo
pone a `true` en el cleanup del `useEffect` y nunca lo vuelve a `false`. StrictMode de
React 18 monta, limpia y vuelve a montar cada componente en desarrollo. Tras ese ciclo el
ref queda en `true` y la respuesta del segundo montaje se descarta como si el componente
ya no existiera.

## Fix

Rearmar el ref al montar, al principio del efecto: `cancelledRef.current = false`. Aplicado
en `app/admin/llm-providers/copilot-device-flow-dialog.tsx`.

## Cómo verificar el fix

```bash
cd apps/admin-panel
npx vitest run app/admin/llm-providers/copilot-device-flow-dialog.test.tsx
```

El test monta el diálogo dentro de `<StrictMode>`. Fallaba antes del cambio y pasa con él.
