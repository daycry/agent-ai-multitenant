---
title: "ADR 0138: Qué hacer con las fases empezadas sin cumplir su gate — el mecanismo se decide aquí, la campaña de validación no"
status: proposed
date: 2026-07-29
deciders: []
relates_to: [0117]
plan_referenced: prod-15-gobernanza-roadmap-docs
task: task_gov_adr_gates_03
docs_language: es
---

# ADR 0138: Gates de validación humana del roadmap

> **`proposed` a propósito, y esta es la parte importante de la decisión.**
> Este ADR **cierra la parte técnica** (§Decisión) porque es mía: la forma exacta
> del mecanismo, dónde vive y qué test lo vigila. **No cierra la parte de
> producto**: elegir entre A, B o la híbrida compromete **tiempo humano de
> calendario** (del orden de 35 sesiones de validación) y cambia una regla dura
> de `CLAUDE.md`. Eso no lo puede firmar un agente. Mientras nadie firme, no se
> ha escrito ni un `gate_override` en ningún frontmatter.

## Contexto

`CLAUDE.md` §"Reglas Duras del Protocolo" dice, sin matices:

> ❌ NUNCA empezar una fase si algún plan listado en su `blocking_plan` no está
> `completed`.

La auditoría de producción de 2026-06 (hallazgo **docsroadmap-2**, severidad
high) reportó que "~26 fases" se empezaron en bloque el 2026-05-30/31 violando
esa regla, dejando todo el código 07–16 en `master` sin sign-off humano.

### Lo medido, que no es lo reportado

Antes de escribir este ADR se volvió a medir sobre los 69 planes con frontmatter
de `docs/roadmap/` (2026-07-29, script equivalente a
`tests/unit/test_roadmap_frontmatter.py::unmet_gates`):

| Métrica                                                | Auditoría 2026-06 | Medido 2026-07-29 |
| ------------------------------------------------------ | ----------------- | ----------------- |
| Planes en `pending_human_validation`                   | ~26               | **35**            |
| Planes `completed`                                     | —                 | 19                |
| Planes `pending_approval`                              | —                 | 14                |
| Planes `in_progress` a la vez                          | varios            | **0**             |
| Fases empezadas con `blocking_plan` **NO** `completed` | ~26               | **6**             |

Las dos correcciones importantes:

1. **La violación del gate son 6 fases, no 26.** El resto de las 35 en
   `pending_human_validation` sí tenían su gate cumplido cuando arrancaron. La
   regla se incumplió mucho menos de lo que decía el informe.
2. **El problema real no es el gate, es la cascada.** Ninguna fase llega a
   `completed` porque `completed` exige sign-off humano, y el sign-off no ha
   ocurrido nunca. Así que cada fase que dependa de una fase code-complete lee su
   `blocking_plan` como "incumplido" aunque el trabajo esté hecho. **El cuello de
   botella es la ausencia de validación humana, no la indisciplina.** Cualquier
   opción que no desatasque eso deja el roadmap igual de mudo.

### Inventario exacto de las 6 fases con el gate incumplido

| Fase                         | `status`                   | `started_at` | `blocking_plan` sin `completed`                       |
| ---------------------------- | -------------------------- | ------------ | ----------------------------------------------------- |
| `06.10-kb-categories`        | `completed`                | 2026-05-28   | `06.9-agent-scoped-kbs`                               |
| `06.17-capacitacion-agentes` | `pending_human_validation` | 2026-06-04   | `06.9-agent-scoped-kbs`, `06.18-tools-overhaul`       |
| `11.1-budgets-fx`            | `pending_human_validation` | 2026-05-31   | `11-guardrails-precios`                               |
| `15-instalador-produccion`   | `pending_human_validation` | 2026-05-31   | `07`, `08`, `09`, `10`, `11`, `12`, `13`, `14` (ocho) |
| `16-human-agents`            | `pending_human_validation` | 2026-05-31   | `10-asistente-personal`, `11-guardrails-precios`      |
| `prod-17-bucle-ai-reviewer`  | `pending_human_validation` | 2026-06-26   | `prod-06-ciclo-vida-ejecucion`                        |

**Caso agravado**: `06.10-kb-categories` está en `completed` — el estado que
`CLAUDE.md` protege con dos reglas duras a la vez — con su bloqueante sin cerrar.

**Dato recuperado durante prod-15**: en `15-instalador-produccion` y
`16-human-agents`, el override **sí estaba escrito por un humano**… en el campo
`| **Estado** |` duplicado de la tabla de cabecera («override humano del gate
blocking_plan»), el mismo campo que `task_gov_cabeceras_07` venía a retirar por
desincronizado. Se preservó como nota en prosa antes de borrar la fila. Es la
prueba de que el mecanismo hace falta: sin un sitio previsto, la excepción
acabó anotada donde nadie la iba a leer y a punto de perderse en una limpieza.

## Opciones

### Opción A — Campaña de validación humana completa

Validar fase a fase antes de tocar ningún estado. El protocolo queda intacto sin
inventar mecanismos nuevos.

- **Coste**: ~35 sesiones de validación humana. Es el coste dominante de todo
  prod-15 y no es paralelizable con un agente.
- **Riesgo**: si la agenda no aguanta, el roadmap sigue mintiendo mientras tanto
  (que es exactamente lo que ha pasado desde mayo).

### Opción B — Re-estado honesto con `gate_override`

Añadir al frontmatter de cada fase empezada con gate saltado un bloque
`gate_override: {by, date, reason}`, actualizar la regla dura de `CLAUDE.md` para
reconocer el override humano **explícito**, y mantener `pending_human_validation`
como la cola real de trabajo.

- **Coste**: bajo (6 frontmatter + un párrafo de `CLAUDE.md`).
- **Riesgo**: si el override se puede poner sin coste, deja de ser una excepción
  y se vuelve el camino normal. Mitigable: lo firma una persona con nombre y el
  test lo vigila (ver §Decisión).

### Opción C (recomendada por prod-15) — Híbrida

B como base documental inmediata **+** campaña priorizada (A) para las fases que
tocan producción de forma directa: `12-backup-restore`,
`15-instalador-produccion`, `08-sso-empresarial`, `09-marketplace`.

- Es la única que ataca el cuello de botella real (§Contexto) sin dejar el
  roadmap mintiendo mientras la agenda se organiza.

## Decisión

### Lo que se decide aquí (técnico, sin esperar a nadie)

Si se aprueba B o C, el mecanismo es **este** y no otro, para que quien firme no
tenga que diseñarlo:

```yaml
gate_override:
  by: "nombre de la persona" # NUNCA un agente: el override es humano por definición
  date: 2026-07-30 # cuándo se firmó
  reason: "texto libre, una frase" # por qué se saltó el gate
  unmet: [11-guardrails-precios] # qué bloqueantes seguían abiertos al arrancar
```

Reglas del mecanismo:

1. **Vive en el frontmatter del plan afectado.** No hay registro central: un
   segundo sitio con el mismo dato se desincroniza (es literalmente el hallazgo
   docsroadmap-6, y el campo `| **Estado** |` de las cabeceras lo demostró en 22
   de 51 planes).
2. **`by` no puede ser un agente.** Un override que un agente pueda firmar no es
   un gate.
3. **No convierte nada en `completed`.** `gate_override` justifica **arrancar**;
   cerrar sigue exigiendo tests humanos + changelog + PR mergeado. Es la línea
   que impide que el mecanismo se coma el gate entero.
4. **Lo vigila un test, no la buena voluntad.**
   `tests/unit/test_roadmap_frontmatter.py` ya está escrito y en verde con dos
   guardas:
   - `test_gate_debt_inventory_has_not_grown` — **verde hoy**: falla si aparece
     una fase empezada con el gate incumplido que no esté en la deuda medida el
     2026-07-29. Impide que la deuda crezca mientras esto se decide.
   - `test_started_phase_declares_its_gate` — **`xfail(strict=True)` hoy**:
     documenta las 6 en rojo esperado. El día que se firme este ADR y aparezcan
     los `gate_override`, el test pasará, pytest lo marcará **XPASS** y la suite
     fallará obligando a retirar el marcador. La deuda no se puede saldar en
     silencio ni olvidar sin que algo se queje.

### Lo que NO se decide aquí (producto — lo firma un humano)

- **La opción: A, B o C.** Compromete calendario humano.
- **El texto exacto de la regla dura de `CLAUDE.md`.** Reconocer un override
  cambia el contrato que leen todos los agentes en cada sesión.
- **Responsable y ventana por fase** de la campaña de validación. `prod-15`
  exige nombrarlos; este ADR no los puede inventar.
- **Si `06.10-kb-categories` revierte de `completed`.** Está `completed` con el
  bloqueante abierto: o se valida, o baja a `pending_human_validation`. Bajar el
  estado de un plan cerrado es decisión del operador.

## Consecuencias

- **Si se firma B o C**: 6 frontmatter ganan `gate_override`, un párrafo de
  `CLAUDE.md` reconoce el override, se retira el `xfail` y la cola priorizada de
  [`docs/roadmap/README.md`](../roadmap/README.md#cola-de-validación-humana) pasa
  a tener responsable y fechas.
- **Si se firma A**: no se toca ningún frontmatter; se ejecuta la campaña y los
  gates se cumplen solos al ir cerrando fases. El `xfail` se retira cuando la
  última de las 6 tenga su bloqueante `completed`.
- **Si no se firma nada**: el estado queda como está y **es honesto**, que ya es
  una mejora sobre mayo — la deuda está medida, escrita, con inventario exacto y
  con un test que impide que crezca. Lo que no se puede es seguir diciendo que la
  regla dura se cumple.

## Relacionado

- Plan [prod-15](../roadmap/prod-15-gobernanza-roadmap-docs.md) — decisión D1,
  tareas `task_gov_adr_gates_03` y `task_gov_reestado_04`.
- ADR [0117](./0117-decisiones-menores-dominio-proyecto.md) — precedente de
  retirar del documento normativo una promesa que nunca tuvo código.
- [`docs/03-guides/verificar-antes-de-implementar.md`](../03-guides/verificar-antes-de-implementar.md)
  §1 («un plan pendiente miente más de lo que parece») y §4 («una guarda que no
  puede fallar no es una guarda»).
