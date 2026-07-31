---
title: "ADR 0139: Loki como agregador de logs — ya está desplegado; lo que faltaba era que los logs fueran consultables"
status: proposed
date: 2026-07-31
deciders: [operador]
relates_to: [0140, 0141]
plan_referenced: prod-08-observabilidad-alertas
task: task_prod08_adr_loki_otel_11
docs_language: es
---

# ADR 0139: Loki como agregador de logs

> **Estado: `proposed`.** La parte técnica está cerrada y entregada (§Decisión);
> lo que queda abierto es de producto: **la retención** y **el coste en RAM/disco
> de una máquina única**. Eso compromete recursos del operador, así que no lo
> firma un agente.
>
> **Aviso de recon**: el plan prod-08 planteaba este ADR como «desplegar Loki
> (opción A) vs retirarlo del stack declarado (opción B)». **Esa pregunta ya no
> existe**: Loki y Promtail llevan desplegados y `healthy` desde antes de
> escribirse este documento. La decisión real es otra, y es la que se aborda aquí.

## Contexto

El plan prod-08 (redactado a partir de la auditoría de 2026-06) afirma que «Loki
está declarado en CLAUDE.md pero no existe en ningún compose». Al ir a
implementarlo, el estado del repo y del host decían otra cosa:

| Pieza                                            | Estado real (2026-07-31)                                                                    |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| `docker/docker-compose.monitoring.yml`           | `loki` (grafana/loki:3.1.1) y `promtail` declarados                                         |
| `docker/monitoring/loki/loki-config.yml`         | existe                                                                                      |
| `docker/monitoring/promtail/promtail-config.yml` | existe                                                                                      |
| Datasource Grafana                               | `provisioning/datasources/loki.yml` existe                                                  |
| Contenedores                                     | `agentic-platform-loki-1` (healthy) y `agentic-platform-promtail-1`, arriba desde hace 30 h |

Es decir: **la opción A del plan estaba implementada** salvo por el detalle de
que el plan proponía Grafana Alloy y lo desplegado es Promtail (equivalente
para este caso de uso; Promtail está en mantenimiento pero soportado, y
migrarlo a Alloy es un cambio de imagen y de fichero de config, no de
arquitectura).

### El agujero que sí existía

Lo que estaba roto no era el despliegue de Loki: era **que los logs no se
podían consultar por lo que importa**.

`configure_logging()` terminaba su cadena de procesadores en un `JSONRenderer()`,
que devuelve un **string**. Ese string se pasaba al logger de stdlib, y el
`ProcessorFormatter` del handler raíz lo trataba como un registro «foráneo» y
lo volvía a envolver. Resultado: **JSON doblemente codificado**.

```json
{
  "event": "{\"execution_id\": \"e-1\", \"tenant_id\": \"t-9\", \"event\": \"execution_finished\"}",
  "level": "info",
  "logger": "workers.execution",
  "timestamp": "...",
  "service": "workers"
}
```

En el nivel superior sobrevivían `event`, `level`, `logger`, `timestamp`,
`service` y los contextvars. **Todo campo de negocio pasado como kwarg quedaba
sepultado dentro de una cadena.** Con eso, la consulta que justifica tener Loki

```logql
{container=~".*workers.*"} | json | execution_id = "e-1"
```

no encuentra nada: para el operador `| json`, `execution_id` no es un campo,
es texto dentro de `event`. Nadie lo había detectado porque **ningún test
afirmaba nunca sobre la forma de la línea de log** — solo sobre
`mask_pii_in_text` por separado.

Y a esto se sumaba que **workers y notification-dispatcher no llamaban a
`configure_logging()` en absoluto** (hallazgo `observability-3`): sus líneas ni
siquiera eran JSON, y salían **sin enmascarado PII**.

## Opciones

### Opción A — Mantener Loki y arreglar la consultabilidad _(recomendada, ya ejecutada la parte técnica)_

Dejar el despliegue como está y corregir las tres cosas que impedían que
sirviera: un solo nivel de JSON, `configure_logging()` en los dos servicios
Celery, y `request_id` cruzando la frontera Celery.

- **Coste**: ya pagado (≈ 1 persona-día, entregado en prod-08 Fase C).
- **Beneficio**: `| json | execution_id="…"` funciona de verdad, y correlaciona
  api-server ↔ workers por `request_id`.
- **Riesgo**: el consumo de RAM/disco de Loki en máquina única (riesgo #3 del
  plan) sigue existiendo y no lo resuelve este ADR.

### Opción B — Retirar Loki y quedarse con `docker logs`

- **Coste**: bajo en ejecución, alto en operación: la retención json-file por
  defecto (~50 MB/contenedor) borra la evidencia justo del incidente que se
  investiga días después, y no hay búsqueda cruzada entre servicios.
- **Cuándo tendría sentido**: si el host se queda sin memoria y hay que elegir
  entre Loki y capacidad de ejecución de agentes.

### Opción C — Migrar Promtail → Grafana Alloy

Lo que el plan presuponía. Promtail está en modo mantenimiento; Alloy es su
sucesor.

- **Coste**: ≈ 0,5 persona-días. No aporta ninguna capacidad nueva a este caso
  de uso.
- **Recomendación**: aplazar. Es deuda técnica conocida, no un problema abierto.

## Decisión (parte técnica, cerrada y entregada)

1. `configure_logging()` termina la cadena structlog en
   `ProcessorFormatter.wrap_for_formatter` y el renderizado JSON ocurre **una
   sola vez**, en el formatter. Los campos de negocio quedan en el nivel
   superior y son consultables con `| json`.
2. `workers` y `notification-dispatcher` instalan el pipeline vía la señal
   `celery.signals.setup_logging` (ver ADR 0141): JSON, campo `service` y
   enmascarado PII en los tres servicios.
3. El `request_id` viaja en las cabeceras del mensaje Celery y se rebindea en
   `task_prerun`, de modo que una búsqueda por `request_id` en Loki devuelve
   **la petición HTTP y el trabajo del worker que disparó**.

Tests: `tests/unit/test_logging_output_shape.py`,
`tests/unit/test_celery_logging_pipeline.py`,
`tests/unit/test_celery_logging_wired.py`.

## Lo que queda para el operador

- **Retención**: `loki-config.yml` fija hoy la retención efectiva. El plan
  presupuestaba 30 días. Confirmar o ajustar según el disco disponible.
- **Límites de recursos**: decidir si se le ponen `mem_limit`/`cpus` a Loki en
  el overlay de monitoring, y cuáles.
- **Promtail vs Alloy** (opción C): aplazar o programar.

## Consecuencias

- La afirmación de `CLAUDE.md` sobre Loki pasa a ser cierta. No hace falta la
  edición vía prod-15 que el plan preveía para el caso B.
- **El formato de las líneas de log cambia** para todos los servicios. Cualquier
  consulta LogQL guardada que dependiera de la forma doblemente codificada deja
  de funcionar — y debe hacerlo: buscaba dentro de una cadena.
- Promtail no parsea el JSON interno (solo extrae el campo `log` del driver de
  Docker), así que el cambio **no requiere tocar su configuración**: el parseo
  ocurre en tiempo de consulta.
