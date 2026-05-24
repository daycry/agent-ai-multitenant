---
adr: "0011"
title: Bus de eventos de dominio sobre Redis Streams
status: accepted
date: 2026-05-22
deciders: System Architect
phase: 02-ejecucion-agentes
---

# ADR 0011 — Bus de eventos de dominio sobre Redis Streams

## Contexto

Plan 02 da vida al sistema: el orchestrator tiene que reaccionar
cuando una tarea cambia de estado (asignarla a un worker, recalcular
el DAG). Eso exige un canal entre el api-server (donde el usuario o un
agente mueven una tarea) y el orchestrator.

Requisitos:

1. **Desacople**: el api-server no debe conocer al orchestrator. Sólo
   emite "ha pasado X"; quien escuche es problema de quien escuche.
2. **Entrega fiable**: un evento no se puede perder porque el
   orchestrator estuviera reiniciándose. Al menos _at-least-once_.
3. **Reproceso seguro**: si el orchestrator cae a mitad de procesar un
   lote, al rearrancar retoma sin duplicar trabajo de forma
   descontrolada.
4. **Cero componentes nuevos**: el stack ya tiene Redis (sesiones,
   rate-limit). Añadir un broker dedicado (RabbitMQ, Kafka) para esto
   sería sobreingeniería en una instalación mono-máquina.

## Decisión

Usar **Redis Streams** como bus de eventos de dominio.

### Stream

Un único stream global `events:tasks`. Los consumidores que necesiten
acotar por tenant lo hacen filtrando el campo `tenant_id` del evento;
no se parte el stream por tenant (decenas de tenants internos no
justifican N streams).

`XADD` con `MAXLEN ~ 10000` (trim aproximado): el stream no crece sin
límite en una instalación de larga vida.

### Grupo de consumidores

El orchestrator lee mediante un **consumer group** `orchestrator`:

- `XGROUP CREATE events:tasks orchestrator 0 MKSTREAM` — idempotente
  (un `BUSYGROUP` al re-crear se traga). `MKSTREAM` permite arrancar
  el orchestrator antes de que el api-server haya emitido nada.
- `XREADGROUP GROUP orchestrator <consumer> COUNT n BLOCK ms STREAMS
events:tasks >` — entrega entradas nunca vistas; varias réplicas del
  orchestrator comparten el grupo y se reparten la carga.
- `XACK` por entrada tras procesarla → entrega _at-least-once_.

Si una réplica muere con entradas entregadas pero sin `XACK`, quedan
en su Pending Entries List; `XAUTOCLAIM` para reclamarlas es una
mejora prevista (no en task_02_01).

### Esquema del evento

Una entrada del stream es un `dict[str, str]` plano (Redis Streams
sólo almacena strings):

| Campo         | Contenido                                    |
| ------------- | -------------------------------------------- |
| `type`        | `task.created` \| `task.status_changed`      |
| `tenant_id`   | UUID del tenant dueño de la tarea            |
| `project_id`  | UUID del proyecto                            |
| `task_id`     | UUID de la tarea                             |
| `occurred_at` | ISO-8601 UTC del momento de emisión          |
| `payload`     | JSON string con datos específicos del evento |

`payload` de `task.created`: `{"status", "priority"}`.
`payload` de `task.status_changed`: `{"old_status", "new_status"}`.

El productor vive en `api_server.events`; el consumidor en
`orchestrator.events` (parser + dataclass `TaskEvent`). Las dos caras
duplican una definición pequeña a propósito — un paquete
`shared-domain` con el contrato lo absorberá cuando haya un segundo
consumidor que lo necesite.

### Publicación best-effort

El api-server publica **después** de la escritura en BD y de forma
best-effort: si Redis está caído, `publish_task_*` traga y loguea, no
falla la request. Consecuencia aceptada: un fallo de Redis justo en
ese instante pierde el evento. Mitigación futura (Plan 11 / Fase 6):
un _outbox_ transaccional si la pérdida resulta intolerable; por ahora
el coste de un evento perdido es "una tarea no se auto-asigna hasta el
siguiente cambio de estado", recuperable a mano.

## Alternativas descartadas

1. **Redis Pub/Sub.** Fire-and-forget: si el orchestrator no está
   suscrito en el instante exacto, el evento se pierde. Rompe el
   requisito 2. Streams + consumer group da persistencia + replay.
2. **Celery como bus de eventos.** Celery ya entra en Plan 02
   (`task_02_02`) para _ejecutar_ trabajo. Pero una task de Celery es
   "haz esto", no "ha pasado esto" — usarlo como event bus acopla al
   emisor con la firma de la task. Streams mantiene la semántica
   pub/sub-con-persistencia limpia. Celery y el bus coexisten:
   el orchestrator consume eventos del bus y _encola_ trabajo en
   Celery.
3. **RabbitMQ / Kafka.** Brokers dedicados con mejores garantías,
   pero un componente más que instalar, monitorizar y respaldar en
   una plataforma mono-máquina. Redis ya está. Reevaluable si la
   plataforma escala a multi-máquina (fuera del alcance actual).
4. **Tabla `outbox` + polling.** Fiabilidad transaccional total
   (el evento se escribe en la misma transacción que el cambio).
   Rechazado de momento por el coste de un poller y la latencia del
   intervalo; se reconsidera en Plan 11 si la pérdida best-effort
   resulta un problema real.

## Consecuencias

Positivas:

- Desacople real: el api-server sólo conoce `events:tasks`, no al
  orchestrator. Un futuro consumidor (memorizer, métricas) se engancha
  con su propio consumer group sin tocar al productor.
- At-least-once + replay sin broker nuevo.
- El orchestrator escala horizontalmente: N réplicas, mismo grupo.

Negativas / cuidados:

- **At-least-once, no exactly-once.** Los handlers tienen que ser
  idempotentes — procesar dos veces el mismo `task.status_changed`
  no debe asignar la tarea dos veces. Responsabilidad de task_02_03.
- **Publicación best-effort** puede perder un evento ante un fallo de
  Redis (ver arriba).
- **Pending Entries List** crece si una réplica muere sin `XACK`;
  hasta que llegue `XAUTOCLAIM`, una réplica zombi retiene entradas.
  Operacionalmente visible vía `XPENDING`.
- Entradas mal formadas (un productor con bug) se cuentan como
  `malformed`, se loguean y se `XACK`-ean igualmente para que un
  poison message no bloquee el grupo — se pierde ese evento concreto
  a propósito.

## Referencias

- `docs/roadmap/02-ejecucion-agentes.md` — Fase A, task_02_01.
- Productor: `apps/api-server/src/api_server/events.py` + hook en
  `routers/tasks.py`.
- Consumidor: `apps/orchestrator/src/orchestrator/{events,consumer,app}.py`.
- Tests: `tests/integration/test_orchestrator.py`.
- Documento maestro, secciones 12 y 13 (orquestación y ejecución).
