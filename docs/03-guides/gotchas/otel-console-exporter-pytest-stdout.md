---
title: `ConsoleSpanExporter` revienta cuando pytest captura stdout
area: otel
encountered: 2026-05-21
stack: opentelemetry-sdk 1.42, pytest 8
---

## Síntoma

Durante un test que ejecuta una request a un app instrumentado:

```
ValueError: I/O operation on closed file.
  ...
  File "opentelemetry/sdk/trace/export/__init__.py", line 341, in export
    self.out.write(self.formatter(span))
```

El test falla aunque la lógica de tracing sea correcta.

## Causa raíz

`ConsoleSpanExporter` escribe los spans a `sys.stdout`. Pytest, por
defecto, _captura_ stdout (lo redirige a un buffer que cierra al
acabar el test). El `BatchSpanProcessor` flush-ea spans
asíncronamente; cuando le toca escribir, stdout ya está cerrado.

## Causa raíz secundaria: OTEL no permite reemplazar el provider

```
Overriding of current TracerProvider is not allowed
```

Una vez que un proceso llama `trace.set_tracer_provider(...)`, no
puede volver a llamarlo. Los tests no pueden simplemente "reiniciar"
el provider entre módulos.

## Fix

1. **Saca el `ConsoleSpanExporter` del default**:

   ```python
   def configure_tracing(*, service_name, exporter=None) -> TracerProvider:
       # NO añadir BatchSpanProcessor(ConsoleSpanExporter()) por defecto.
       # El caller decide qué exporter usar.
   ```

   En `main.py` llama explícitamente `add_console_exporter()` después
   de `configure_tracing()`.

2. **En tests, añade un span processor extra** (sin sustituir el
   provider) con un `InMemorySpanExporter`:

   ```python
   @pytest.fixture()
   def exporter():
       configure_tracing(service_name="api-server-test")
       provider = trace.get_tracer_provider()
       in_memory = InMemorySpanExporter()
       processor = SimpleSpanProcessor(in_memory)
       provider.add_span_processor(processor)
       try:
           yield in_memory
       finally:
           in_memory.clear()
           processor.shutdown()
   ```

   `SimpleSpanProcessor` es síncrono (no flush diferido); los spans
   están disponibles inmediatamente tras la request.

## Cómo verificar el fix

`pytest tests/integration/test_tracing.py -v` pasa sin tocar stdout.
