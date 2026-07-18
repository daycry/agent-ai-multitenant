---
title: "ADR 0117: Tres decisiones menores del dominio Proyecto (MCP, validación humana por tarea, web-app)"
status: proposed
date: 2026-07-18
---

# ADR 0117: Decisiones menores del dominio Proyecto

Salen de la auditoría integral del dominio Proyecto (2026-07-17, hallazgos
PROJ-02, PROY2-06 y el `apps/web-app` vacío) vía `task_proy_f4` del plan
`remediacion-proyecto-integral-2026-07-17`. Las tres son decisiones de
producto/alcance que el equipo de plataforma NO debe tomar unilateralmente:
este ADR las deja preparadas con opciones y recomendación para que el operador
elija. Ninguna bloquea el resto de la remediación (ya implementada).

## (a) MCP por proyecto: empaquetar los servers stdio o retirar la superficie (PROJ-02)

### Contexto

La UI de proyecto ofrece configurar servidores MCP (`projects.mcp_servers` +
formulario en el admin-panel), pero es una **fachada**: los ~24 templates
stdio históricos referencian binarios (`npx …`, `uvx …`) que no existen en
ninguna imagen del stack; `test-connection` no puede validar stdio; y el
worker no lanza esos procesos. El único MCP operativo del stack es interno
(docling — y su vía operativa real hoy es docling-serve HTTP). Un operador que
configura un server MCP en su proyecto obtiene silencio.

### Opciones

1. **Empaquetar**: imagen `mcp-runners` con node+uv y los binarios de los
   templates soportados; el worker la lanza como sidecar efímero por-run con
   la misma red restringida que los runtimes. Coste: imagen nueva mantenida,
   superficie de seguridad (procesos arbitrarios stdio), matriz de versiones.
2. **Retirar de la UI** (recomendada a corto): ocultar la sección MCP de la
   página de proyecto (o marcarla «experimental — sin runtime») hasta que
   exista la imagen; conservar `shared-mcp` y la columna (`mcp_servers`)
   intactas. Coste: ninguna función se pierde (hoy no funciona); honestidad
   inmediata.
3. **Híbrida**: retirar stdio y permitir SOLO servers MCP **HTTP/SSE**
   remotos (URL + token), que sí son alcanzables sin binarios locales. Coste
   medio: validación de egress (allowed_domains) + test-connection HTTP real.

### Recomendación

**(2) ahora, con (3) como siguiente paso si hay demanda.** (1) solo con un
caso de uso concreto que lo pague.

## (b) `task.human_validation_required`: implementar el flag o corregir CLAUDE.md (PROY2-06)

### Contexto

El principio 7 de CLAUDE.md promete: «Tests humanos a nivel de plan.
Excepción: `task.human_validation_required=true` para tareas individuales
críticas». Ese flag **no existe** (ni columna, ni schema, ni código): la
validación humana es solo por plan (review session al pasar a
`pending_human_validation`) y por categorías de acción sensible
(approval policies). La promesa lleva desde el día uno sin implementación.

### Opciones

1. **Implementar**: columna `tasks.human_validation_required BOOL` +
   `on_task_done` la respeta (la tarea queda `in_review` esperando un
   veredicto humano por-tarea en vez de `done`). Coste: nueva máquina de
   revisión por-tarea (hoy la review humana es por plan), UI, notificaciones.
2. **Corregir CLAUDE.md** (recomendada): borrar la excepción del principio 7
   y documentar las DOS vías reales de control humano fino que ya existen:
   políticas de aprobación por categoría de acción (13 categorías, 4
   plantillas) y `ask_human` (ADR 0114). Cubren el caso de uso («esta acción
   crítica necesita un humano») con granularidad mayor que un flag por tarea.
3. **Aplazar** con el flag documentado como «previsto»: perpetúa la promesa
   falsa — descartada.

### Recomendación

**(2)**: las approval policies + `ask_human` ya dan el control fino real; el
flag por-tarea duplicaría maquinaria. Si el operador prefiere (1), es un plan
propio (~2-3 d) — no un remiendo.

## (c) `apps/web-app` vacío: consolidar en admin-panel o plan de separación

### Contexto

CLAUDE.md declara `apps/web-app` («Frontend Next.js de tenants») separado de
`apps/admin-panel` («Frontend del System Admin»), pero `apps/web-app` está
vacío desde el día uno: TODO el frontend (tenants + System Admin) vive en
`admin-panel`, con RBAC por rol dentro de la misma app. Ningún compose lo
construye.

### Opciones

1. **Consolidar** (recomendada): declarar `admin-panel` como el frontend
   único (tenants + admin, separación por RBAC/rutas), borrar `apps/web-app`
   y actualizar CLAUDE.md + architecture-overview. Coste: cero código; el
   nombre `admin-panel` queda algo impreciso (renombrarlo sería un churn de
   imágenes/compose que no paga nada hoy).
2. **Separar**: plan para extraer las vistas de tenant a `web-app` (build,
   imagen, compose, auth compartida). Coste alto; beneficio real solo si los
   ciclos de release de tenant y admin deben divergir o si el aislamiento de
   superficie (bundle del admin no descargable por tenants) se vuelve un
   requisito.

### Recomendación

**(1)**: consolidar y documentar. (2) solo si aparece el requisito de
aislamiento de superficie.

## Consecuencias

- Este ADR no cambia código por sí mismo; cada resolución del operador se
  implementa como tarea pequeña (b y c son S; a-opción-2 es S, a-opción-1/3
  son M/L con plan propio).
- Mientras esté `proposed`, la UI MCP sigue visible (fachada conocida), el
  principio 7 sigue impreciso y `apps/web-app` sigue vacío — los tres costes
  son de honestidad documental, no de fiabilidad del runtime.
