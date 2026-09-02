---
title: "`AsyncResult.get()` dentro de un task prefork lanza RuntimeError, y un doble sin la guarda lo tapa"
area: python
encountered: 2026-09-01
stack: Celery 5.6, workers prefork (pool por defecto), pytest con dobles de `app.send_task`
---

# `AsyncResult.get()` dentro de un task prefork

## Síntoma

Cada `done` con criterios automáticos llega al reviewer como fallo de
infraestructura: `test_run_completed` con `infrastructure_failure=test_phase_dispatch_failed`,
siempre, en todos los proyectos. Ningún criterio ejecutable se verifica jamás. La
suite unitaria de la fase de tests está en verde.

## Causa raíz

Celery prohíbe esperar a otro task desde dentro de un task en el mismo pool:

```text
RuntimeError: Never call result.get() within a task!
See https://docs.celeryq.dev/en/latest/userguide/tasks.html#avoid-launching-synchronous-subtasks
```

En el hijo prefork `_set_task_join_will_block(True)` se fija al arrancar, y
`AsyncResult.get()` llama a `assert_will_not_block()` **antes** de tocar el
backend. `dispatch_test_runtime_and_wait` hacía `async_result.get(timeout=…)` vía
`asyncio.to_thread` dentro de `run_execution`: el hilo no cambia nada, el flag es
del proceso. La excepción caía en el `except Exception` genérico y se persistía
como fallo de infraestructura.

**Y el test no lo veía** porque su `_FakeAsyncResult.get()` no reproducía la
guarda. Un doble que devuelve el valor sin comprobar lo que Celery comprueba es
un test que fija el defecto (`docs/03-guides/verificar-antes-de-implementar.md`).

## Fix

1. `with allow_join_result(): async_result.get(...)` — el opt-in explícito de Celery.
2. El opt-in sólo es seguro si la cola esperada la sirve OTRO worker: un
   `workers-aux` con `--queues=test,review` en el compose de dev **y** en el que
   genera el instalador (`_workers_aux_service`). Sin eso, dos runs esperando a
   la vez en el pool genérico se inanicionan hasta el presupuesto.
3. El doble del test llama a la guarda real: `celery.result.assert_will_not_block()`
   dentro de `get()`, bajo `_set_task_join_will_block(True)`
   (`tests/unit/test_test_phase_queue.py::test_the_wait_survives_inside_a_prefork_task`).

## Cómo reconocerlo la próxima vez

`RuntimeError` con «Never call result.get() within a task» en el log del worker,
o un `test_phase_dispatch_failed` con `error_type=RuntimeError` en todos los
runs. Si aparece en un test, el doble no aplica la guarda.
