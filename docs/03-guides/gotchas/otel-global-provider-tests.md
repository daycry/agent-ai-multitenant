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
