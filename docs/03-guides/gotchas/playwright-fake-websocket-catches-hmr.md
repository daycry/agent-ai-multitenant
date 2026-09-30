---
title: "Un WebSocket falso en un e2e captura también el de HMR de next dev"
area: admin-panel
encountered: 2026-09-30
stack: next 14 (next dev), playwright 1.60
---

# Un WebSocket falso en un e2e captura también el de HMR de next dev

## Síntoma

Un e2e que simula mensajes de un WebSocket (p. ej. `ingestion-progress.spec.ts`) emite un
frame y la pantalla no reacciona: el badge sigue en `pending` y la lista de eventos no
crece. Pasa a veces sí y a veces no, y esperar a la hidratación no lo arregla.

## Causa raíz

El spec sustituye `window.WebSocket` por una clase falsa y guarda las instancias creadas.
Pero `next dev` abre su propio WebSocket de HMR con el mismo constructor, así que también
queda registrado en esa lista. Un helper que emite «a la última instancia» manda el frame
al socket de HMR cuando éste se abre después que el de la pantalla, y el frame se pierde.

## Fix

El helper del spec elige la instancia por su URL (`/ws/documents/` en el caso de la
ingesta) en vez de tomar la última. Cualquier `FakeWS` de un e2e contra `next dev` debe
filtrar por URL.

## Cómo verificar el fix

```bash
cd apps/admin-panel
E2E_EXPECT_TIMEOUT=30000 npx playwright test e2e/ingestion-progress.spec.ts --reporter=line
```
