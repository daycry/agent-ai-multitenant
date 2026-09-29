---
status: informe
title: Consolidación de todo lo pendiente del roadmap — inventario, orden y quién desbloquea cada cosa
date: 2026-09-29
branch_analyzed: plan/harness-programa-2026-09-17 (6ed4e225)
docs_language: es
---

# Consolidación de lo pendiente (2026-09-29)

Este informe **no es un plan nuevo ni copia estados**. Es el índice ordenado de
todo lo que queda abierto, medido el 2026-09-29 sobre la cabeza de la cadena de
ramas, con la respuesta a dos preguntas por cada bloque: **qué falta** y **quién
lo desbloquea**. La fuente de verdad sigue siendo el frontmatter de cada plan; si
algo de aquí contradice a uno, gana el frontmatter y este informe se corrige.

## Cómo se midió

- `git fetch --all` y `git for-each-ref` por fecha: ningún commit en ninguna rama
  después del 2026-09-17.
- Frontmatter de `docs/roadmap/*.md` con `plan_id`, más el recuento de casillas
  `- [ ]` y `- [x]` de cada fichero.
- `status:` de los 168 ADR: 167 `accepted`, 1 `rejected`, **cero `proposed`**.
- `gh pr list --state open` y `git merge-base --is-ancestor` para cada rama sin mergear.

## Foto de conjunto

| Estado                     | Planes |
| -------------------------- | -----: |
| `completed`                |     19 |
| `pending_human_validation` |     41 |
| `pending_approval`         |     20 |
| `approved`                 |      2 |
| `in_progress`              |      1 |
| `blocked`                  |      1 |

## Bloque 0 — Integración: la cadena de ramas sin mergear

Todo el trabajo desde el 2026-09-02 vive en **una sola cadena lineal** que no ha
llegado a `master`:

```text
origin/master (02392c80, #182)
  └─ plan/marketplace-mcp-2026-09-02        PR #183 abierto
      └─ plan/ui-reestructuracion-2026-09-09   sin PR
          └─ plan/instalador-runs-de-serie-2026-09-09   sin PR
              └─ plan/harness-programa-2026-09-17   sin PR  ← cabeza
```

Las otras cuatro ramas sin mergear **no contienen nada vivo**: `ola2` y `ola3`
entraron por squash en #178 y #179, `fix/las-tools-no-pueden-borrar-el-git` (#170
cerrado) lo reemplazó #171, y `docs/adr-0057…` entró en #48.

**Quién desbloquea**: el operador, mergeando. Mientras no se mergee, ningún plan de
la cadena puede pasar a `completed` (regla 5 del cierre de fase). Hay además unos
veinte PR de Dependabot y seis de `chore/runtime-digests-*` abiertos desde agosto.

## Bloque 1 — Trabajo implementable sin esperar a nadie

Ordenado por lo que desbloquea. Es lo que se ejecuta a continuación de este informe.

|   # | Qué                                                                                                     | Plan                                                                               | Esfuerzo | Por qué va aquí                                                                                             |
| --: | ------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- | -------- | ----------------------------------------------------------------------------------------------------------- |
| 1.1 | Changelog y paso a `pending_human_validation` de siete planes entregados que siguen como «no empezados» | `cadena-pr-plan`, `prod-03`, `prod-05`, `prod-07`, `prod-09`, `prod-13`, `prod-14` | 1 d      | Deuda inventariada en `_DELIVERED_BUT_UNSTARTED_2026_08_12`; sin bloqueante; los saca de la cola equivocada |
| 1.2 | `claim_id` en el serializador canónico                                                                  | `harness-00` `task_h00_02`                                                         | 0,5 d    | Bug real y barato; no depende del ADR                                                                       |
| 1.3 | ADR 0168 de garantías del harness                                                                       | `harness-00` `task_h00_01`                                                         | 1 d      | Desbloquea los seis planes harness; ver criterio de decisión abajo                                          |
| 1.4 | Terminar la fase activa                                                                                 | `ui-reestructuracion-2026-09-09`, 11 tareas                                        | 22 d     | Es la única `in_progress`; ninguna otra fase puede arrancar antes                                           |
| 1.5 | Memoria por capas                                                                                       | `memoria-agentes-2026-09-09`, 7 tareas                                             | 12 d     | `approved`; su `task_mem_20` alimenta `task_ui_31`                                                          |
| 1.6 | Programa harness en su orden                                                                            | `harness-01` a `harness-06`                                                        | 44-56 d  | Tras el ADR 0168; `harness-05` después de 1.5                                                               |
| 1.7 | i18n del panel, tres lotes                                                                              | `prod-16`                                                                          | 5-7 d    | Las tres migraciones al diccionario no piden decisión; la cuarta casilla depende de D3                      |

**Criterio para el ADR 0168.** La directiva permanente del operador delega los ADR
en el agente «eligiendo la mejor opción», con una excepción ya ejercida en el ADR
0117: cuando la decisión es de producto, se para y se pregunta. De las seis
decisiones del 0168, cuatro son técnicas (effectively once, checkpoint store,
`attempt_id`, guardrails fail-closed con política configurada). Las dos de
producto —memoria y KB `REQUIRED`, `allowed_tools` obligatorio— se resuelven
**sin cambiar el comportamiento de nadie**: el default sigue siendo el de hoy y el
cambio sólo existe para el proyecto que lo pida. Así el ADR puede aceptarse sin
tomar la decisión de producto en nombre del operador.

**Orden entre 1.4 y 1.5.** La regla de una sola fase `in_progress` impide llevar las
dos a la vez. `ui-reestructuracion` va primero porque ya está arrancada; su
`task_ui_31` (memorias con procedencia) se deja la última de la fase porque
depende de las columnas de `task_mem_20`.

## Bloque 2 — Espera una decisión o una acción humana

Nada de esto lo puede cerrar un agente. Está aquí para que conste en un solo sitio.

| Qué                                                  | Plan                            | Qué hace falta                                                                     |
| ---------------------------------------------------- | ------------------------------- | ---------------------------------------------------------------------------------- |
| Merge de la cadena de ramas                          | —                               | Revisar y mergear #183 y abrir y mergear los tres siguientes                       |
| 41 planes en `pending_human_validation`              | varios                          | Ejecutar sus tests humanos; la cola ordenada está en el README del roadmap         |
| `prod-04` a `pending_human_validation`               | `prod-04-backup-dr-restaurable` | Su bloqueante `prod-01` no está `completed`: pide `gate_override` firmado          |
| Eval conductual con dataset real                     | `gov-01` `task_gov_04`          | Secreto del proveedor y variables del repo configurados, y dataset dorado sembrado |
| Firma del ADR de niveles de cambio                   | `gov-01`                        | El borrador existe desde el 2026-08-20; falta elegir                               |
| Pasadas de review por nivel                          | `gov-01`                        | Depende de la firma anterior                                                       |
| Responsable y ventana de la cola de validación       | `prod-15`                       | Compromete el calendario de una persona; el ADR 0138 lo deja fuera del agente      |
| Receiver directo email o Slack para alertas críticas | `prod-08`                       | Credenciales del canal y destinatario real                                         |
| Custodia offsite de las unseal keys y re-init        | `prod-10`                       | Acción física con las claves de Shamir                                             |
| Branch protection obligatoria                        | `prod-11`                       | Plan de pago y admin de GitHub (ADR 0058)                                          |
| Pentest externo y release v1.0.0                     | `15-instalador-produccion`      | Contratación y decisión de release                                                 |
| E2E con runner Docker                                | `prod-18`                       | Runner con Docker; no acreditable en local                                         |
| Dos e2e observacionales                              | `guardas-research-por-novedad`  | Relanzar runs reales, que la orden del 2026-07-03 prohíbe hasta tu OK              |
| Generación de tipos desde OpenAPI                    | `prod-16` D3                    | Elegir la opción D3                                                                |

## Bloque 3 — Deuda de inventario que no es trabajo

`06-testing-revision-git` está `completed` con siete casillas abiertas. Las siete
son tareas **retiradas** en agosto porque el código que describían se borró del
repo; están congeladas en el inventario de `test_no_new_completed_plan_has_open_boxes`.
No hay nada que implementar.

## Qué cambia en este informe cuando se ejecuta

Cada línea del bloque 1 que se cierre deja su evidencia en su propio plan, no aquí.
Este informe se relee y se corrige al acabar el bloque 1.4, que es el punto donde
cambia el orden de lo que queda.
