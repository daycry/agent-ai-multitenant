---
title: "ADR 0140: Alcance del tracing OpenTelemetry — recorte explícito, y la correlación por request_id como sustituto"
status: proposed
date: 2026-07-31
deciders: [operador]
relates_to: [0139, 0141]
plan_referenced: prod-08-observabilidad-alertas
task: task_prod08_adr_loki_otel_11
docs_language: es
---

# ADR 0140: Alcance del tracing OpenTelemetry

> **Estado: `proposed`.** La limpieza de lo que era falso está hecha
> (§Decisión). Lo que queda abierto es de producto: **adoptar o no un backend
> de trazas** (opción B) cuesta ≈ 5 persona-días no presupuestados y añade dos
> servicios a un host que ya corre el stack entero. Eso lo firma el operador.

## Contexto

El sistema tiene OpenTelemetry a medio camino, y —lo importante— **la
documentación decía que estaba entero**.

`apps/api-server/src/api_server/telemetry/setup.py` abría con:

> «Auto-instrumentation for FastAPI, **SQLAlchemy**, asyncpg, Redis, httpx.»
> «Phase 12 will swap the exporter to OTLP/Tempo without touching callers.»

Verificado contra el código:

- **`SQLAlchemyInstrumentor` no se invoca en ningún sitio.** Ni un import, ni
  una llamada. La instrumentación de SQLAlchemy **nunca ha existido**, pero
  `opentelemetry-instrumentation-sqlalchemy` sí estaba declarada en el
  `pyproject` y por tanto instalada en la imagen.
- La «Phase 12» no está planificada en ningún plan del roadmap.
- El **único** exporter es `ConsoleSpanExporter`, y es **opt-in** vía
  `API_SERVER_OTEL_CONSOLE=1`. Sin esa variable —el caso normal— **los spans se
  generan y se descartan**: se paga el coste de instrumentar y no se obtiene
  nada.
- La traza **muere en la frontera Celery**: el contexto no viaja con el mensaje.

Lo caro aquí no es la dependencia de más: es que un operador leyendo ese
docstring podía concluir que sus queries lentas ya estaban trazadas y buscar el
problema en otra parte. Documentación que miente cuesta más que documentación
que falta.

## Opciones

### Opción A — Recorte explícito _(recomendada; la parte técnica ya ejecutada)_

Declarar el tracing distribuido **fuera de alcance v1**, retirar lo muerto y
decir la verdad en el docstring. La correlación entre servicios se cubre con
`request_id`, que desde prod-08 Fase C sí viaja de punta a punta —incluida la
frontera Celery— y es buscable en Loki (ADR 0139).

- **Coste**: ≈ 0,5 persona-días. Ya pagado.
- **Qué se pierde**: el detalle intra-petición (qué span consumió el tiempo).
  Se conserva el «qué pasó y en qué orden», que es lo que se usa en el 90 % de
  las investigaciones reales.

### Opción B — OTLP + Tempo + instrumentación de Celery

- **Coste**: ≈ 5 persona-días + dos servicios (Tempo y su almacenamiento) en un
  host que ya corre PostgreSQL, Redis, Loki, Prometheus, Grafana, los workers y
  los runtimes efímeros.
- **Qué aporta**: waterfall por petición, atribución de latencia por span.
- **Cuándo tendría sentido**: cuando haya un problema de latencia concreto que
  `request_id` + los logs no basten para diagnosticar. Hoy ese problema no está
  documentado en ningún sitio.

## Decisión (parte técnica, cerrada y entregada)

1. **Retirada** `opentelemetry-instrumentation-sqlalchemy` del `pyproject` del
   api-server.
2. **Reescrito** el docstring de `telemetry/setup.py` para decir lo que el
   módulo hace hoy: cuatro instrumentaciones reales (FastAPI, asyncpg, Redis,
   httpx), **SQLAlchemy NO**, exporter único Console y opt-in, tracing
   distribuido fuera de alcance v1.
3. **Costura conservada**: `configure_tracing()` sigue siendo el único punto de
   cambio si algún día se adopta la opción B. Es un cambio de exporter, no de
   llamantes.
4. **Guarda ejecutable** (`tests/unit/test_telemetry_setup.py`): además de
   vetar la dependencia concreta, generaliza la regla — _cada_
   `opentelemetry-instrumentation-*` declarada debe tener su `*Instrumentor`
   invocado en el código. Así la próxima dependencia muerta no entra en
   silencio, que es exactamente como entró ésta.

## Lo que queda para el operador

Elegir entre consolidar la opción A o abrir un plan follow-up para la B. Si se
elige B, el seam ya está: `request_id` viaja en las cabeceras Celery y añadir
`traceparent` W3C junto a él es aditivo.

## Consecuencias

- La imagen del api-server adelgaza una dependencia. Como las imágenes de
  workers, notification-dispatcher y orchestrator se construyen **sobre** la del
  api-server, el ahorro se propaga a las cuatro.
- **Requiere rebuild de imagen**: el cambio de `pyproject` no llega a producción
  con un `restart`.
- Nadie volverá a leer una promesa de tracing de SQLAlchemy que no se cumple.
