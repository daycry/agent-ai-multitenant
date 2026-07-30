---
title: Validación humana de planes — probar la app, aprobar, rechazar y corregir
audience: usuario tenant (validador), operador
updated: 2026-07-09
related: [ADR 0062, ADR 0063, ADR 0107]
---

# Validación humana de planes

Cuando los agentes terminan TODAS las tareas de un plan, el plan pasa solo a
**`pending_human_validation`** y la plataforma levanta una **sesión de
revisión**: la app construida por los agentes corriendo en un contenedor
efímero, más una consola de revisión con logs, terminal y checklist. Esta
guía cubre el ciclo completo del validador: probar → aprobar o rechazar → y,
si rechazas, convertir tu motivo en **tareas correctivas del MISMO plan**
(ADR 0107).

## 1. Dónde valida el humano

En el **detalle del plan** (Proyecto → Planes → el plan) aparece la tarjeta
**«Validación humana — probar la app»** con dos accesos:

- **Abrir app para probar** — la aplicación real servida por el
  review-runtime a través del proxy firmado del api-server (no se publica
  ningún puerto; el enlace lleva una firma con caducidad).
- **Consola de revisión** — terminal + logs + checklist de los tests humanos
  del plan (si el spec los define).

> La app que ves corre sobre el **worktree del plan** (la rama `plan/...`),
> con la imagen de preview del proyecto. Si el enlace responde «este proyecto
> no tiene app-preview configurada», configura la imagen en Ajustes del
> proyecto — ver [app-review-images.md](./app-review-images.md).

## 2. Aprobar

Si lo que pruebas está bien: **Aprobar plan**. El plan pasa a `completed` y
la plataforma abre automáticamente el **PR del plan** contra la rama
principal del repo del proyecto (un PR por plan, con los commits de todas
sus tareas). La sesión de revisión se recicla sola (sus contenedores los
recoge el sweep de mantenimiento; el veredicto queda en el historial).

## 3. Rechazar — el motivo ES el trabajo correctivo

Si algo está mal: **Rechazar** abre una modal con un textarea (con preview
de Markdown). Escribe el motivo como se lo contarías al equipo: **qué está
mal, dónde y qué esperabas**. Ejemplo real:

> El filtro global que fija `Content-Type: application/json` debe acotarse
> al grupo de rutas `api/v1`. La aplicación es web + API: las páginas web
> (p. ej. la ruta `/`) devuelven HTML y deben servirse con
> `Content-Type: text/html`.

El plan pasa a `rejected` y el motivo queda registrado en la sesión de
revisión. **Rechazar no es un callejón sin salida**: habilita el ciclo de
correcciones.

## 4. Correcciones en el MISMO plan (ADR 0107)

Con el plan en `rejected`, el detalle del plan muestra la tarjeta
**«Correcciones del rechazo»**:

1. **Motivo del validador** — tu texto, renderizado.
2. **Generar tareas correctivas** — el PM (LLM del proyecto) convierte el
   motivo en tareas concretas con criterios de aceptación verificables, ids
   `fix-*`, rol y dependencias. Se añaden al plan como **propuestas** (se
   ven también en la tabla de tareas con el badge «corrección»).
3. **Revisar y aceptar** — cada propuesta lleva su checkbox (todas marcadas
   por defecto); desmarca las que no quieras. **Aceptar correcciones**
   materializa las marcadas en el Kanban y **reactiva el plan**
   (`rejected → in_progress`) en el mismo acto.

A partir de ahí el ciclo sigue solo: el orquestador despacha las tareas
correctivas a los agentes (misma rama git del plan — el PR final llevará el
trabajo original + los fixes), el review IA las valida una a una, y cuando
todas están `done` el plan vuelve a `pending_human_validation` con una
**sesión de revisión nueva** para que valides otra vez.

### Preguntas frecuentes

| Pregunta                                              | Respuesta                                                                                                                                 |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| ¿Puedo editar las tareas propuestas antes de aceptar? | Puedes desmarcar las que no quieras y aceptar el resto. Para retocar título/criterios, edita la tarea en el Kanban una vez materializada. |
| ¿Y si el motivo era malo y las propuestas no sirven?  | No aceptes nada: el plan sigue `rejected`. Puedes re-rechazar la siguiente sesión con un motivo mejor o crear tareas a mano.              |
| ¿Se pierde la relación entre rechazo y correcciones?  | No: `specification.corrections[]` guarda motivo, sesión, tareas y estado (`proposed`/`accepted`) — visible en la tarjeta como historial.  |
| ¿Cuántas veces puede iterar?                          | Sin límite de producto: cada rechazo con motivo puede generar su tanda. Cada tanda usa ids nuevos (`fix-*` deduplicados).                 |
| ¿Quién puede aceptar correcciones?                    | `tenant_admin` (generar: cualquier miembro del tenant).                                                                                   |

## 5. Estados del plan en este ciclo

```
in_progress ──(todas done)──▶ pending_human_validation ──(aprobar)──▶ completed ──▶ PR
                                        │
                                   (rechazar)
                                        ▼
                                    rejected ──(aceptar correcciones)──▶ in_progress ──▶ …
```

`rejected` sin aceptar correcciones es un aparcamiento seguro: nada se
ejecuta ni se re-lanza hasta que alguien decida.

## Referencias

- [app-review-images.md](./app-review-images.md) — construir la imagen de preview.
- ADR 0062 (review-runtime + proxy firmado), ADR 0063 (la imagen la trae el
  proyecto), **ADR 0107** (rechazo con correcciones en el mismo plan).
- API: `GET /plans/{id}/review-session`, `POST .../generate-corrections`,
  `POST .../accept-corrections`.
