---
title: Secuencia maestra de ejecución — implementar los planes pendientes fase a fase
version: 1.0
last_updated: 2026-05-29
status: published
---

# Secuencia Maestra de Ejecución

Este documento ordena la **implementación de todos los planes pendientes** del roadmap por
dependencias (`blocking_plan`), respetando la regla de **una sola fase `in_progress` a la vez**,
y fija el **protocolo de trabajo** (misma metodología que 06.8/06.14): TDD, tests automáticos
exhaustivos (incl. cross-tenant), scripts de prueba humana, un PR por plan, y cierre solo con
tests verdes + validación humana + changelog. Derivado del workflow de evaluación `wf_f3f6388f`.

## Estado real (resumen)

- **Completados (00–06 + 06.5/06.6/06.7):** 10 planes.
- **Código hecho y mergeado a `master`, pero SIN cerrar formalmente:** `06.8`, `06.9`, `06.10`,
  `06.11`, `06.12` — tests automáticos exhaustivos presentes; faltan changelog, sign-off humano
  y corregir frontmatter.
- **Nuevos a implementar:** `06.13` (contenido de KBs), `06.14` (hardening — ya spec'd al 100%),
  y las features grandes `07`–`16`.
- **Violación de protocolo a corregir:** 3 fases marcadas `in_progress` a la vez
  (`06.10`/`06.11`/`06.12`) — causa: `06.10` se mergeó pero su frontmatter no se actualizó.

## Protocolo de ejecución (por plan)

1. **Una fase `in_progress`** a la vez. Al empezar: `status: in_progress`, `started_at`.
2. **TDD**: primero el test (rojo) que demuestra el requisito/fallo, luego el código (verde).
3. **Tests automáticos exhaustivos**: cada tarea con su test; obligatorio cubrir **aislamiento
   cross-tenant**, rutas de error y casos límite. Suite verde antes de cerrar.
4. **Scripts de prueba humana**: extraídos del bloque `Tests humanos` del plan a una guía
   ejecutable; los ejecuta un **humano** (yo no puedo) y firma el sign-off.
5. **Un PR por plan** (`gh pr create`), **sin auto-merge**: queda abierto hasta tests verdes +
   sign-off humano. El humano mergea.
6. **Cierre**: todas las tareas `[x]`, tests verdes, sign-off humano, **changelog** en
   `docs/07-changelog/{plan_id}.md`, PR mergeado → `status: completed`, `completed_at`.
7. Activar la siguiente fase.

> ⚠️ **Gate humano**: los pasos 4 (pruebas humanas) y 5 (merge del PR) requieren a una persona.
> Yo dejo todo verde y el PR listo; tú validas y mergeas.
>
> ⚠️ **Auto-sync de VS Code**: tu VS Code hace push/sync de la rama. Para que el código sin
> validar **no llegue solo a `master`**, trabajamos con PR abierto y NO ejecuto `gh pr merge`.

## Orden de implementación (olas)

| Ola | Planes | Tipo | Notas |
| --- | ------ | ---- | ----- |
| **1** | `06.8`, `06.9` | Cierre | Código hecho; changelog + frontmatter + sign-off humano. ~1–2 días. |
| **2** | `06.10` → `06.11` → `06.12` | Cierre serializado | Resuelve la violación de 3×`in_progress`. Cerrar 06.10 primero. |
| **3** | `06.13`, `06.14` | Nuevo código | 06.13 = corpus + ingesta de KBs builtin; **06.14 = seguridad P0** (ver nota). |
| **4** | `07`, `08` | Feature | Portal de docs; SSO empresarial. |
| **5** | `09`, `10` | Feature | Marketplace; asistente personal (10 desbloquea 16). |
| **6** | `11`, `12`, `13`, `14` | Feature | Guardrails+precios (desbloquea 16); backup; API pública; evals. |
| **7** | `15`, `16` | Producción | Instalador (depende de casi todo); human-agents (depende de 10+11). |

**Cadena de dependencias (crítica):**
`06.8 ← 06.9 ← 06.10 ← 06.11 ← 06.12 ← 06.13 ← 06.14 ← 07 ← 08 ← (09‖10) ← (11‖12‖13‖14) ← 15 ← 16`

Esfuerzo total estimado: **~16–18 semanas** (todo 07–16 son features grandes/xlarge).

### 🔴 Nota de prioridad sobre 06.14 (seguridad)
El orden por dependencias coloca 06.14 al final de la fase 06, PERO contiene **vulnerabilidades
activas P0** (fuga cross-tenant en WebSocket, workers sin frontera de tenant, secretos sin validar).
**Recomendación**: adelantar al menos los 3 P0 de 06.14 (`task_06_14_01/02/03`) en cuanto se cierre
la Ola 1, en paralelo al cierre de 06.10–06.12, dado que son fallos de seguridad reales en producción.
Decisión tuya.

## Acciones inmediatas (Ola 1 — cierre de 06.8 / 06.9)

Para cada uno: (a) ejecutar su suite automática y confirmar verde; (b) crear
`docs/07-changelog/{plan_id}.md`; (c) alinear frontmatter (`status: pending_human_validation`,
sin inconsistencias); (d) preparar la guía de pruebas humanas; (e) abrir PR; (f) → tú firmas y mergeas.

- **06.8-rbac-enforcement**: tests `test_auth_role_helpers.py`, `test_rbac_resources.py`,
  `test_me_endpoint.py`, `test_admin_rbac.py`. Falta changelog. 4 pruebas humanas (login user/admin/
  system_admin, tasks por member).
- **06.9-agent-scoped-kbs**: tests `test_agent_kb_grants.py`, `test_visible_kbs_resolver.py`.
  Falta changelog. 4 pruebas humanas (grant/revoke UI, matriz visibilidad, adopción de template).

## Gobernanza a corregir (antes de avanzar de ola)

1. Cerrar `06.10` (status→completed + changelog) para dejar **una sola** fase activa.
2. Crear changelogs faltantes: `06.8`, `06.9`, `06.10`, `06.11` (06.12 ya lo tiene).
3. Alinear frontmatter `status:` en 06.8/06.9/06.10 a la realidad.
4. Notación explícita de tareas diferidas (`06.11 task_06_11_04 → 06.12`, `06.10 task_06_10_09`).

## Seguimiento

El progreso por tarea se mantiene en cada plan (`[ ]`/`[x]`) y en la lista de tareas de la sesión.
Este documento se actualiza al cerrar cada ola.
