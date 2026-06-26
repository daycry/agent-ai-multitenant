---
adr_id: "0083"
title: "Colas heavy/gpu: routing real por complejidad/GPU o recorte del contrato de colas"
status: accepted
date: 2026-06-25
decided_at: 2026-06-26
authors: [claude-opus]
plan_referenced: prod-06-ciclo-vida-ejecucion
docs_language: es
related: ["0027"]
supersedes: []
---

# ADR 0083 — Colas `heavy`/`gpu`: routing real o recorte del contrato

> **Estado: `accepted`** — el operador eligió la **Opción B (recortar)** el
> 2026-06-26. `task_prod06_colas_02` implementa el recorte: `heavy`/`gpu`
> eliminadas de `QUEUE_NAMES`, runbook + este ADR + ADR 0027 actualizados.

## Contexto

`apps/workers/src/workers/celery_app.py` declara **7 colas** (`QUEUE_NAMES`):
`default, heavy, gpu, ingestion, test, review, privileged`. Pero el **dispatcher**
(`apps/orchestrator/src/orchestrator/dispatch.py`) encola SIEMPRE `run_execution`
a **una única cola fija** (`settings.dispatch_queue`, default `default`):
`_send_run_execution` usa `queue=self._settings.dispatch_queue` y nadie enruta
hacia `heavy`/`gpu`.

Consecuencia (workers-7): **`heavy` y `gpu` no tienen ningún productor** — son
colas muertas. Además `Task.estimated_complexity` (xs/s/m/l/xl) se **persiste**
(en `sync_to_kanban`) pero **no se consume** en ningún sitio del dispatch, y el
requisito de GPU de un runtime template tampoco influye en el enrutado.

Las colas especializadas que SÍ tienen productor y se quedan en cualquier caso:
`ingestion` (Docling), `test`/`review` (runtimes), `privileged` (secretos/infra),
`default` (el grueso de los runs de agente).

El alcance del sistema es **Docker Compose en una sola máquina** (no Kubernetes,
no multi-host). Hoy no hay un despliegue separado de workers por lane.

## Decisión (a tomar por el operador)

### Opción A — Routing real por complejidad + GPU

- El dispatcher elige cola según `Task.estimated_complexity` (`l`/`xl` → `heavy`)
  y según el requisito de GPU del runtime template del proyecto (→ `gpu`, con
  **fallback a `default`** si no hay worker GPU desplegado).
- Requiere: lógica de routing en `dispatch.py`, detección/log de "cola sin
  consumidores", y —para que aporte valor real— **desplegar workers dedicados**
  a `heavy`/`gpu` (concurrencia/recursos propios) en el compose.
- **Pros:** aísla runs largos/pesados del lane común; prepara el terreno para GPU.
- **Contras:** más superficie operativa (lanes + tuning) en un single-host donde
  hoy nadie escala por separado; la `gpu` sin host GPU es teatro (fallback a
  default). Más caro de mantener.

### Opción B — Recortar el contrato de colas (Recomendada)

- Eliminar `heavy` y `gpu` de `QUEUE_NAMES`; mantener
  `default + ingestion + test + review + privileged`.
- Actualizar el runbook de capacidad y **ADR 0027** (que describe la topología).
- El dispatcher sigue enrutando a `default` + las especializadas (sin cambio).
- **Pros:** elimina colas muertas y la falsa promesa de aislamiento; el contrato
  refleja la realidad single-host; menos superficie. YAGNI.
- **Contras:** si en el futuro se quiere aislar runs pesados o añadir un host GPU,
  habría que reintroducir el lane (reversible, es un cambio de config + un ADR).

## Recomendación

**Opción B (recortar)**, salvo que el operador prevea a corto plazo cargas GPU
reales (inferencia local en GPU, runtime templates GPU) o necesidad de aislar
runs `l`/`xl` en un lane con recursos propios. En el alcance actual (single-host,
sin worker GPU desplegado, sin escalado por lane) `heavy`/`gpu` son colas muertas
y la Opción A añade complejidad operativa sin un consumidor real. La Opción B es
**reversible**: reintroducir un lane el día que haga falta es un cambio de config

- un ADR, no una migración.

## Consecuencias e implementación (`task_prod06_colas_02`, bloqueado)

- **Si A:** routing en `dispatch.py` (complejidad/GPU → cola, fallback default) +
  detección/log de cola sin consumidores + workers dedicados en el compose +
  `tests/unit/test_dispatch_queue_routing.py`.
- **Si B:** quitar `heavy`/`gpu` de `QUEUE_NAMES` + actualizar runbook
  `06-capacity-management` + ADR 0027 + test de que el dispatcher solo usa colas
  con consumidor.

En AMBOS casos, el dispatcher debe **loguear** cuando enrute a una cola sin
consumidores (observabilidad; la regla de alerta vive en prod-08).

## Decisión (2026-06-26)

El operador eligió la **Opción B (recortar)**. Implementado en
`task_prod06_colas_02`:

- `heavy` y `gpu` eliminadas de `QUEUE_NAMES`
  (`apps/workers/src/workers/celery_app.py`); topología = `default + ingestion +
test + review + privileged` (las lanes con productor + consumidor real).
- `docs/06-runbooks/06-capacity-management.md` y **ADR 0027** actualizados a la
  topología recortada.
- Tests: `tests/unit/test_queue_topology_trimmed.py` (heavy/gpu fuera; toda cola
  de beat/dispatch es una cola declarada) + `tests/integration/test_celery_queues.py`.

Reintroducir un lane el día que exista un host GPU o un worker pesado dedicado es
un cambio de config + un ADR, no una migración.
