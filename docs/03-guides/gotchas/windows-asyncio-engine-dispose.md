---
title: `asyncio.run(engine.dispose())` crashea el proactor de asyncio en Windows
area: windows
encountered: 2026-05-20
stack: Python 3.13, asyncpg, SQLAlchemy 2.x async, Windows 11
---

## Síntoma

En el teardown de un fixture pytest que limpia un engine async:

```python
finally:
    asyncio.run(get_engine().dispose())
```

Stacktrace:

```
AttributeError: 'NoneType' object has no attribute 'send'
  File ".../asyncio/proactor_events.py", line 402, in _loop_writing
    self._write_fut = self._loop._proactor.send(self._sock, data)
```

## Causa raíz

`asyncio.run(...)` crea un event loop NUEVO. Las conexiones del
engine fueron creadas en el loop de pytest-asyncio; al disponer
desde otro loop, asyncpg pierde el proactor original y el cleanup
del socket falla en Windows.

## Fix

**No llames `dispose()` en el teardown.** El engine se GC cuando el
proceso termina; para pruebas eso es suficiente.

```python
finally:
    # NO: asyncio.run(get_engine().dispose())
    reset_engine_cache()   # solo limpia la lru_cache
```

Si necesitas reciclar conexiones entre tests (raro), usa una fixture
async (`pytest-asyncio`) con scope `function` y `await engine.dispose()`
dentro de ella, en el mismo loop que las creó.

## Cómo verificar el fix

```bash
.venv/Scripts/pytest tests/integration -v
# No aparece "NoneType' object has no attribute 'send'" en los teardowns.
```
