---
title: "Una entrada del beat cuya task nadie importa se encola y muere en silencio: la feature parece desplegada y nunca ha corrido"
area: celery, workers
encountered: 2026-07-30
stack: Celery 5, celery beat
---

## Síntoma

Ninguno. Ahí está el problema.

Una feature periódica se entrega completa: la task escrita y testeada, su entrada
en `BEAT_SCHEDULE` con cadencia y cola, su changelog, su ADR `accepted`. Beat
tickea sin errores, el worker está `healthy`, y la feature **nunca se ha
ejecutado ni una vez**. No hay excepción en los logs de la aplicación, no hay
alerta, no hay fila en ninguna tabla que delate la ausencia.

## Causa raíz

`beat_schedule` y el registro de tasks son **dos listas independientes**, y nadie
comprueba que cuadren:

- La entrada del beat sólo contiene el **nombre** de la task, una cadena.
- El worker registra una task cuando **importa su módulo**, y los módulos que
  importa están declarados aparte, en el `imports=(...)` de
  `workers/celery_app.py::build_celery_app`.

Si el módulo no está en `imports`, beat encola puntualmente un mensaje con un
nombre que ningún worker conoce. El worker lo rechaza con `NotRegistered` —y ese
rechazo vive en el log del worker de Celery, no en el de la aplicación—, así que
desde fuera es indistinguible de «todavía no le ha tocado el turno».

El 2026-07-30, una guarda genérica encontró **seis** entradas así, todas de
features declaradas entregadas y desplegadas: el standup diario (ADR 0120), el
vigía de credenciales (ADR 0122), la retro de planes (ADR 0124), el asesor de
configuración (ADR 0125), el restore-drill (ADR 0126) y el GC de conocimiento
(G-03). Cinco por no estar en `imports`; el sexto, `knowledge_gc`, por no estar
re-exportado desde el façade `workers/maintenance/__init__.py` (su paquete sí
está en `imports`, pero el módulo concreto no se importa).

## Fix

La guarda vive en `tests/unit/test_approval_expiry_beat.py::test_every_beat_entry_names_a_registered_task`
y es genérica: recorre **todas** las entradas del beat, no una. Al añadir una task
periódica, lo único que hay que hacer es no romperla.

Tres detalles de su construcción que importan si la tocas:

1. **Mide en un subproceso.** El registro de Celery es un singleton de módulo y
   `@app.task` engancha la task al importar. Cualquier otro test de la suite que
   importe `workers.standup` la deja registrada para el resto del proceso, así
   que medir en el proceso de pytest da un resultado que depende del orden de la
   suite (verde en aislamiento, rojo en `pytest tests/unit/`). Un proceso limpio
   es la definición exacta de «lo que importa un worker al arrancar».
2. **Lleva la deuda con igualdad, no con `>=`.** Las seis roturas conocidas están
   en un `frozenset`, y el test falla en los DOS sentidos: una rotura nueva rompe
   CI, y una que ya se arregló también — para que la lista no acabe mintiendo.
3. **Asserta que la guarda vio algo** (`len(sched) >= 15`, `len(registered) >= 20`).
   Sin eso, el día que `build_beat_schedule` devolviera un dict vacío el test
   pasaría vacuamente, que es el modo de fallo de
   [verificar-antes-de-implementar](../verificar-antes-de-implementar.md).

**Las seis no se arreglaron al descubrirlas, y es deliberado:** cablearlas
enciende seis jobs de fondo dormidos a la vez, y uno de ellos **ensaya una
restauración de backup**. Eso quiere los ojos del operador, no un efecto
colateral. Al cablear cada una, quítala del `frozenset`.

## Cómo verificar el fix

```bash
.venv/Scripts/python.exe -m pytest tests/unit/test_approval_expiry_beat.py -q
```

Y para ver la lista en vivo, lo mismo que hace el test:

```bash
.venv/Scripts/python.exe -c "from workers.celery_app import app; app.loader.import_default_modules(); \
from workers.beat_schedule import build_beat_schedule; from workers.config import Settings; \
print(sorted({e['task'] for e in build_beat_schedule(Settings()).values()} - set(app.tasks)))"
```
