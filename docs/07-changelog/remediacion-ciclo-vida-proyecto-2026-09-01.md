---
plan_id: remediacion-ciclo-vida-proyecto-2026-09-01
title: Remediación del ciclo de vida de un proyecto — workers, agentes, memorias y runtimes
completed_at: null
status: pending_human_validation
docs_language: es
---

# Plan remediacion-ciclo-vida-proyecto-2026-09-01

## Resumen

Treinta y seis tareas en cinco olas, nacidas del informe
[`auditoria-ciclo-vida-proyecto-2026-09-01.md`](../roadmap/auditoria-ciclo-vida-proyecto-2026-09-01.md):
lo que un proyecto vive desde que se crea hasta que su plan abre un PR, medido
contra la base de datos y el código en vez de contra lo que los documentos
prometían. Todas las casillas están `[x]` con su test en verde; el plan queda en
`pending_human_validation` hasta que el operador pase `human_cv_01..04`.

Los PR: #177 (olas 0 y 1), #178 (ola 2), #179 (ola 3) y el de la ola 4.

## Ola 0 — Roto en producción hoy

Reclamaciones con identidad (`claim_id`) que el reconciler no confunde; el auto-PR
que se reintenta y deja `pr_error`; el sweeper recupera el resultado real de un
contenedor `exited`; los sidecars de `runtime_services` arrancan (`x-infra-caps`);
`commit_failed` bloquea la tarea en vez de darla por hecha.

## Ola 1 — Integridad del ciclo de vida

Sin trabajo versionado que se destruya (ADR 0164); reviewer que ve sólo el diff de
su tarea; políticas de aprobación sembradas para todo proyecto; plantillas que no
prometen binarios ajenos (`toolchains` en el catálogo de runtimes).

## Ola 2 — Seguridad del sandbox

Un bridge interno por ejecución (ADR 0012, addendum); preview de review sin
worktree en RW; spec y token por fichero en `/run/secrets`; dep-cache por tenant;
tools MCP sin categoría que pasan por el gate; observaciones acotadas y memorias
valladas frente a inyección.

## Ola 3 — Lo que el modelo ve

Escalera de lectura de memoria según el ADR 0071; guía de ejecución que sigue a las
tools efectivas; `merge` de forks con capacidades; refresco de arranque de
plantillas, políticas y corpus.

## Ola 4 — Operación

- **Presupuestos antes del gasto** (`task_cv_40`): el restante del wall-clock es el
  `timeout` de cada llamada al proveedor; coste estimado con los precios del catálogo
  cuando el proveedor devuelve 0; `guardrails_unavailable` si la política declara
  `block` y el motor no arranca (ADR 0102, addendum).
- **Escaladas visibles** (`task_cv_41`): `task_blocked` en cada escalada del bucle de
  review; el despacho de review respeta proyecto pausado y pausa por presupuesto; el
  reviewer nunca implementa su propia tarea; el panel muestra la escalada.
- **Mantenimiento que respeta runs vivos** (`task_cv_42`): `expires` + cerrojo
  `SET NX` en las tareas beat con efecto en disco; la poda no toca un worktree con
  ejecución `running`; el worktree de docs del cierre se retira (ADR 0163, addendum).
- **Git remoto con timeout, DLQ con lector, quiesce que sella** (`task_cv_43`):
  `WORKERS_GIT_REMOTE_TIMEOUT_S`; `dlq:executions` en `agentic_dlq_depth` y en
  `GET /admin/dead-letters`; al apagarse, el worker mata sus agent-runtimes y sella
  sus filas `failed:quiesced`.
- **Imágenes por digest** (`task_cv_44`): imágenes del tenant con `@sha256:` o de
  un registry allowlisted; versiones del catálogo pineadas; `agent-runtime` y
  `browser-runtime` en la release y en el manifiesto de digests (ADR 0148, 0129).
- **Restos** (`task_cv_45`): `timeout -k`; rutas de host bajo `data_root` (ADR
  0060); embedding fallido contado; `restore_reconcile` sin CRITICAL falso; watchdog
  que re-resuelve; retro idempotente por tag y por la persistencia común; `args` en
  la observación; techo de `ask_human`; lote read-only que anuncia lo expulsado;
  memorizer humano con causa; memorias sin secretos ni rutas de host; evento
  `git_credential_failed`; `direct_to_default_allowed` retirada (ADR 0072, addendum).

## Despliegue

Imagen del `agent-runtime` y worker **antes** que orquestador (`claim_id`, spec y
token por fichero, `ask_human_remaining`). Nueva variable opcional
`WORKERS_TENANT_IMAGE_REGISTRY_ALLOWLIST`; `WORKERS_GIT_REMOTE_TIMEOUT_S` (300 s).
Los proyectos con `push_policy=direct_to_default_allowed` deben pasar a
`branch_only_pr_required` (la API lo rechaza).

## Pendiente humano

`human_cv_01..04` (§Tests humanos del plan): el reviewer ve un `<test-report>` real;
un proyecto creado por API sin política aparece en la bandeja humana ante un
`http_post`; adoptar `full-stack-web` y lanzar un plan no aborta `model_unresolved`;
rechazar dos veces con hermanas activas deja al reviewer sólo el diff de la tarea.
