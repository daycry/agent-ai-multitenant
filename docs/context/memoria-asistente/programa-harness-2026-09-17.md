---
name: programa-harness-2026-09-17
description: Programa harness, compat-modelos, ui-reestructuracion y memoria-agentes en la rama plan/harness-programa-2026-09-17; estado al 2026-09-30
metadata:
  node_type: memory
  type: project
  originSessionId: 21b9b7a2-9cad-4343-9a42-ea6bfde1b60c
  modified: 2026-09-30T02:04:35.433Z
---

Rama `plan/harness-programa-2026-09-17` (sobre la cadena marketplace → ui → instalador,
nada mergeado a master, push hecho el 2026-09-30 a petición del operador). Estado al 2026-09-30:

- **ui-reestructuracion-2026-09-09** `blocked` (1df542e5): 11/13 casillas, revisión
  adversarial cerrada en 3 intentos. Abiertas: mitad URL de `task_ui_21` (ADR 0170
  `proposed`) y `task_ui_40` (piel, sólo si el operador la aprueba). Changelog escrito.
- **memoria-agentes-2026-09-09** `in_progress`: 6/7 casillas (`task_mem_10` abierta: su eval pide el
  dataset dorado del Plan 14, que no mide recall; decide el operador). Revisión adversarial intento 1 hecha;
  sus correcciones están comiteadas (a9803128) pero **la integración de memoria está SIN verificar**: los
  implementadores se cortaron por límite de uso y Docker Desktop no arrancaba el 2026-09-30. Al volver:
  integración de memoria (incluida la migración 0154 up/down/up) e intento 2 de la revisión.
- **harness-00** `approved`: h00_01, h00_02, h00_04 cerradas; faltan h00_03 (baseline) y
  h00_05 (aceptación, depende de todos los hijos). harness-01..06 `pending_approval`.
- **compat-modelos** `pending_human_validation`. Codex y Ollama cloud sin prueba viva:
  Vault sellado, sin credenciales.
- Decisiones abiertas del operador: ADR 0170; coste por run visible a miembros (`/runs`);
  built-in miembros que el dispatch del ADR 0091 no cuenta; `task_ui_40`.

**Why:** el operador pidió implementar todo el roadmap y soporte de modelos pequeños.

**How to apply:** todo con el plugin ([[usar-plugin-custom-agents-siempre]]); la fase
activa es memoria-agentes. **Gotchas:** e2e del panel fiables sólo con la receta de CI
(`npm run build` + `E2E_WEBSERVER_CMD="npm run start"`, `--timeout=15000`): con `next dev`
salen 12-18 rojos distintos en cada tanda; integración necesita `WORKERS_BROKER_URL` a la
Redis de tests y el cerrojo `/c/tmp/itest.lock` si hay varios agentes; no correr `next dev`
en dos puertos a la vez (comparten `.next`).
