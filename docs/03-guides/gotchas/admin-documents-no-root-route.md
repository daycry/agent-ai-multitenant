---
title: /admin/documents/<id> da 404 (no hay page.tsx raíz)
area: admin-panel, next.js
encountered: 2026-05-26
stack: Next.js 14 app router
---

## Síntoma

Abres una URL como `http://localhost:3000/admin/documents/<uuid>` y
Next.js devuelve la página `404 page not found`. El UUID existe en BD
y el endpoint API `/documents/<uuid>/citations` responde 200.

## Causa raíz

El admin-panel **no tiene** un `page.tsx` directo bajo
`apps/admin-panel/app/admin/documents/[id]/`. Sólo dos subrutas:

- `apps/admin-panel/app/admin/documents/[id]/citations/page.tsx`
- `apps/admin-panel/app/admin/documents/[id]/ingestion/page.tsx`

Next 14 app router requiere un `page.tsx` por cada ruta servible. Una
ruta intermedia sin `page.tsx` propio NO renderiza nada; cualquier
visita cae al not-found global.

Se diseñó así a propósito: el Document es siempre "una de dos vistas"
(citas con bboxes o estado de ingestión). No hay una vista "neutra"
útil del Document por sí solo.

## Fix

Para humanos / scripts demo: usa siempre una de las dos subrutas.

```
http://localhost:3000/admin/documents/<uuid>/citations    # chunks + bboxes
http://localhost:3000/admin/documents/<uuid>/ingestion    # estado pipeline
```

Los demos del Plan 04.5 ya imprimen las URLs con sufijo en su footer.
Si encuentras un enlace sin sufijo (un changelog antiguo, una nota
pegada de otro sitio), añade `/citations` por defecto.

Si en el futuro hace falta una vista raíz (p.ej. metadata del
Document + tabs para las dos sub-vistas), basta con crear
`apps/admin-panel/app/admin/documents/[id]/page.tsx` que renderice un
header con el título + descripción y las pestañas hacia `/citations`
e `/ingestion`.

## Cómo verificar el fix

```powershell
# Antes del fix (URL mal formada): 404
curl http://localhost:3000/admin/documents/<uuid>
# →  HTML del not-found

# Con sufijo: OK
curl http://localhost:3000/admin/documents/<uuid>/citations
# →  HTML con CitationViewerPage
```

## Referencias

- `apps/admin-panel/app/admin/documents/[id]/` — estructura real.
- `docs/03-guides/run-demo-human-tests.md` — guía de tests humanos,
  sección Plan 04.5.
