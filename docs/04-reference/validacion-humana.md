---
title: Validación humana — 13 categorías, 4 presets y el ciclo de una aprobación
audience: backend-dev, ai-engineer, architect, security, tenant-admin
phase: prod-03-guardrails-validacion-humana
updated: 2026-08-01
---

# Validación humana — Referencia

El **principio rector nº11** dice que la validación humana es configurable por
proyecto, con 13 categorías de acciones sensibles y 4 plantillas. Esta página
documenta el vocabulario, los presets, el mapa tool→categoría y el ciclo de vida
de una solicitud de aprobación.

Para el motor de guardrails (que es otra cosa: checks declarativos en 4 hooks)
ver [`guardrails.md`](./guardrails.md).

> **Ojo con una promesa que nunca existió**: **no hay
> `task.human_validation_required`**. Fue una afirmación de `CLAUDE.md` que
> nunca tuvo columna ni código, retirada por el
> [ADR 0117 (b)](../05-architecture-decisions/0117-decisiones-menores-dominio-proyecto.md).
> Los tests humanos son a nivel de **plan**; para exigir un humano en un punto
> concreto están las políticas por categoría de esta página y la tool
> `ask_human` ([ADR 0114](../05-architecture-decisions/0114-ask-human-no-terminal.md)).

## Las 13 categorías canónicas

Fuente única: `packages/shared-domain/src/shared_domain/approval_categories.py`.
La comparten el seed de los presets (api-server) y el gate del sandbox
(`agent_runtime.approval`), que **no se importan entre sí** — un test de
contrato los pinea.

`code_changes` · `git_commit` · `git_push` · `external_http_get` ·
`external_http_post` · `secrets_access` · `data_migration` ·
`production_deploy` · `infra_provision` · `secret_rotation` ·
`external_communication` · `data_export_pii` · `user_management`

> **Por qué son «canónicas» y no una lista más.** En junio de 2026 el gate del
> runtime emitía otras cuatro (`code_execution` / `file_write` /
> `network_access` / `agent_delegation`) que **no intersectaban ninguna** de
> estas trece. Como una categoría no listada cae en `auto`, el resultado fue que
> ni el preset «Cliente Externo» detenía una sola tool (hallazgo g6). El
> vocabulario compartido es lo que impide que vuelva a pasar.

## Mapa tool→categoría

Los builtins, keyed por nombre canónico (ADR 0048), en
`agent_runtime.approval.DEFAULT_TOOL_CATEGORIES`. Una tool ausente del mapa
**no es sensible y nunca se gatea**.

| Tool                                                   | Categoría                |
| ------------------------------------------------------ | ------------------------ |
| `shell_exec`, `stack_exec`                             | `code_changes`           |
| `write_file`, `delete_file`                            | `code_changes`           |
| `run_pytest`, `run_lint`, `run_typecheck`, `run_build` | `code_changes`           |
| `agent_invoke`                                         | `code_changes`           |
| `memory_store`                                         | `code_changes`           |
| `promote_to_kb`                                        | `data_migration`         |
| `send_notification`                                    | `external_communication` |
| `http_get`                                             | `external_http_get`      |
| `http_post`                                            | `external_http_post`     |

Dos decisiones del mapa que conviene no deshacer sin leer el porqué:

- **`memory_store` → `code_changes` manda la FRECUENCIA, no la semántica.** Es
  tool de familia de sistema (la tiene TODO agente) y se usa de rutina. El gate
  no «pide permiso y sigue»: aborta el run. Darle una categoría que el preset
  `development` marca `human_required` convertiría cada run que guarda un
  aprendizaje en una parada.
- **`kanban_update` se queda sin categoría a propósito**: hoy no está cableada,
  y ninguna de las 13 cubre «gestión de tareas/plan». Inventar la 14ª toca los
  cuatro presets y la UI, o sea que es decisión de producto. Si alguien la
  recablea, `test_every_wired_tool_is_gated_or_exempt_with_a_reason` se pone
  rojo y fuerza la decisión ahí mismo. Es intencional.

### Tools MCP y custom

Una tool MCP se llama `<server>.<tool>`, un nombre que depende del servidor que
declare cada proyecto: no cabe en un mapa estático. Su categoría se **deriva**
del `security_level` de la fila (`spec_approval_category`) y viaja en el
ToolSpec. Dos reglas del merge:

- **el builtin gana la colisión** — un spec no puede rebajar el gate de
  `write_file` declarándose con una categoría más laxa;
- **una categoría fuera de las 13 se descarta** — propagarla haría creer que la
  tool está cubierta cuando `requires_human` caería en `auto`.

`security_level = "safe"` es el único opt-out explícito y por-tool.

## Los cuatro presets

Sembrados en `seeds/builtin_approval_policies.py`, los cuatro sobre las 13
categorías completas:

| Preset              | Filosofía                                                   |
| ------------------- | ----------------------------------------------------------- |
| `sandbox`           | todo `auto` — entornos aislados, demos internas             |
| `development`       | bucle de coding en `auto`, el resto gateado                 |
| `production`        | estricto                                                    |
| `customer-external` | trabajo de cara al cliente; hasta lecturas y comunicaciones |

Un proyecto **sin** política explícita hereda el preset por defecto de
plataforma (`default_approval_policy_preset`, default `development`) — ADR 0104.
No se queda sin gate.

## Qué pasa con una categoría que la política NO lista

Hasta el [ADR 0153](../05-architecture-decisions/0153-categoria-no-listada-en-la-politica-de-aprobacion.md)
esto era **fail-open**: lo que la política no nombraba corría sin humano. Y lo
que acaba en `projects.human_approval_policy` no siempre nombra las 13 — los
proyectos creados desde una plantilla copian su esqueleto, no un preset.

Ahora lo decide la política, con esta clave **hermana** de `categories`:

```json
{
  "preset": "production",
  "categories": { "code_changes": "auto", "...": "..." },
  "unlisted_category": "human_required"
}
```

Orden de resolución, idéntico en el api-server (`db/approval_repo.py`) y en el
gate del sandbox (`agent_runtime/approval.py`) — son **dos implementaciones
espejo** que no se importan entre sí, y `tests/unit/test_unlisted_approval_category.py`
las compara caso a caso:

1. la categoría está en `categories` → manda esa decisión;
2. hay `unlisted_category` (`auto` \| `human_required`) → manda esa;
3. no la hay → se deriva del `preset`: `auto` en `sandbox`/`development`,
   `human_required` en `production`/`customer-external`;
4. no hay preset reconocible, o el valor de la clave no se entiende →
   **`human_required` (fail-closed)**.

El punto 4 es deliberado: ante una política que no se sabe interpretar, parar y
preguntar es recuperable; dejar correr una acción sensible, no. La solicitud que
nace así lleva un campo `gate_reason` con el motivo en castellano —«la política
no lista esta categoría y su preset es X»—, visible en la pantalla de
aprobaciones. Sin él, quien recibe la aprobación no puede deducir por qué paró y
la aprueba sin leer.

La clave se ve y se edita en **/admin/approval-policy**, como una fila más de la
tabla de categorías, y se escribe siempre al aplicar la política a un proyecto.
La API rechaza (422) un valor que no sea `auto` o `human_required`, y rechaza
colarla DENTRO de `categories` — ahí sería una categoría fantasma que ningún
`review()` consulta, que es exactamente lo que le pasaba a `external_http`.

> Un proyecto **sin** política (`NULL` / `{}`) no entra aquí: lo cubre el ADR
> 0104 heredando el preset de plataforma. Fallar cerrado ahí gatearía todo run
> de un proyecto recién creado antes de que ese preset llegue a aplicarse.

## Ciclo de vida de una solicitud

```
tool sensible → ApprovalGate.review() devuelve categoría
              → el run para (execution: awaiting_human_approval)
              → el worker crea ApprovalRequest (pending)
                    ├── aprobada  → approved  → la acción queda autorizada
                    ├── rechazada → rejected  → execution aborted
                    └── sin atender → timed_out (barrido) → aborted + task blocked
```

### Qué autoriza exactamente una aprobación (ADR 0135)

**Esa acción exacta, en esa task, una vez.** No un permiso por tool ni por
categoría:

- la huella es `tool` canónico + `args` **verbatim** (`to_canonical` +
  `json.dumps(sort_keys)` + SHA-256, **sin** normalización con pérdida), en
  `shared_domain/approval_action.py` — una sola implementación para los dos
  extremos;
- la lista viaja al sandbox como `approved_actions` en el task spec y
  `ApprovalGate.review(tool, args)` la **canjea**: un canje por aprobación;
- una acción distinta de la misma categoría, o los mismos args con un espacio de
  más, se vuelven a aparcar — y la nueva solicitud enseña el **delta** al
  revisor (`action.prior_approvals`).

El **fallback por `(tool, categoría)` con TTL corto está RECHAZADO**: convertía
la ruta laxa en la ruta normal.

`resolve_approval` gasta un reintento por aprobación y escala a `blocked` con
evento `approval_retry_capped` al llegar a `max_retries`: el bucle
aprobar→re-aparcar deja de ser infinito.

**Deuda con nombre**: la persistencia del canje (`consumed_at`) no está
implementada — tal cual la propone el ADR produce un livelock cuadrático, porque
al re-ejecutarse la task DESDE CERO el agente vuelve a proponer las acciones ya
consumidas. El canje es **por run**.

### Resolución atómica

`resolve_approval` es un `UPDATE ... WHERE id = :id AND status = 'pending'`: si
la fila no gana la transición, `POST /approvals/{id}/resolve` responde **409** y
las transiciones de Execution/Task **no** se aplican. Dos revisores simultáneos
→ exactamente un 200 y un 409. El barrido de caducidad comparte ese guard, así
que la carrera aprobar-vs-timeout tampoco pisa una decisión humana.

### Caducidad

`workers.expire_stale_approvals` (beat, cada 15 min, cola `default`):

| Palanca                   | Default | Qué hace                            |
| ------------------------- | ------- | ----------------------------------- |
| `approval_expiry_enabled` | `true`  | interruptor vivo del barrido        |
| `approval.timeout_hours`  | `24`    | ventana; clampada a [0,25 h, 720 h] |

Se lee en **cada pasada**, así que cambiarlas surte efecto sin reiniciar nada.
Un valor no numérico cae al default documentado en vez de explotar: un typo en
la UI no puede convertir el barrido en «caduca todo».

El barrido va **tenant a tenant, cada uno en su propia transacción**: corre con
el rol BYPASSRLS del worker, donde RLS no acota nada, así que el scope es
responsabilidad explícita del código. Al caducar emite el evento de
notificación con el `tenant_id` de la fila.

Ver el runbook: [`aprobaciones-atascadas.md`](../06-runbooks/aprobaciones-atascadas.md).
