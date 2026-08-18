---
title: El `TracerProvider` global de OTEL no se puede reemplazar
area: otel
encountered: 2026-05-21
stack: opentelemetry-api 1.42
---

## Síntoma

En un fixture de test que llama `configure_tracing(...)` después de
que `main.py` ya lo configurase:

```
WARNING opentelemetry.trace: Overriding of current TracerProvider is
  not allowed
```

Y los spans del test acaban en el provider viejo, no en el del test.

## Causa raíz

`opentelemetry.trace.set_tracer_provider()` solo acepta la primera
llamada por proceso (la segunda emite un warning y se ignora). Esto
es intencional: el provider es el singleton del proceso.

## Fix

No intentes reemplazar el provider. En lugar de eso:

1. Asegúrate de que `configure_tracing` es **idempotente** (devuelve
   el provider existente en la segunda llamada).

2. En los tests, añade un span processor extra al provider existente
   en vez de crear otro provider:

   ```python
   @pytest.fixture()
   def exporter():
       configure_tracing(service_name="api-server-test")  # idempotente
       provider = trace.get_tracer_provider()
       in_memory = InMemorySpanExporter()
       processor = SimpleSpanProcessor(in_memory)
       provider.add_span_processor(processor)
       try:
           yield in_memory
       finally:
           processor.shutdown()
   ```

`SimpleSpanProcessor` (síncrono) entrega los spans al exporter
inmediatamente, así que `exporter.get_finished_spans()` los ve sin
necesitar `force_flush`.

## Cómo verificar el fix

```python
def test_idempotent():
    first = configure_tracing(...)
    second = configure_tracing(...)
    assert first is second
```

## La reincidencia (2026-08-18): un reset «de test» que sólo resetea la mitad

El fix de arriba aguantó hasta que `telemetry/setup.py` ganó un
`_reset_for_tests()` que ponía `_PROVIDER = None`. La caché del módulo se
suelta, pero **el global de OTEL no**, porque es irreversible. A partir de ahí
`configure_tracing()` volvía a entrar en su cuerpo, construía un provider nuevo,
`set_tracer_provider()` lo rechazaba en silencio (sólo un WARNING) y la función
**devolvía y cacheaba el provider rechazado**: un objeto que no usa ningún
tracer del proceso.

Síntoma, y por qué despista: `test_configure_tracing_is_idempotent` pasaba en
solitario y fallaba en la suite completa con

```
assert <TracerProvider object at 0x…A> is <TracerProvider object at 0x…B>
```

Sólo falla en lote porque hace falta que **otro** fichero haya instalado antes
el provider — y basta con que importe `api_server.main`, que llama a
`configure_tracing()` a nivel de módulo. Tiene toda la pinta de «flaky de
orden»; no lo es.

Y no era un problema sólo de tests: el `exporter` que se le pasa a
`configure_tracing()` se colgaba del provider muerto, así que **el destino de
trazas que pidió el llamante quedaba mudo sin un solo error**.

**Fix**: `configure_tracing()` lee el provider EFECTIVO después de intentar
instalarlo (`trace.get_tracer_provider()`), cuelga ahí el exporter y devuelve
ése. Nunca devuelve uno propio que OTEL haya rechazado. Regla general: cuando un
recurso global es _set-once_, «lo instalo y devuelvo lo que instalé» es una
suposición; lo correcto es «lo intento instalar y devuelvo lo que hay».
