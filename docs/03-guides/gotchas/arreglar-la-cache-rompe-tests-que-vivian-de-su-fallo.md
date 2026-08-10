---
title: "Arreglar la caché de platform_settings pone rojos tests que pasaban PORQUE estaba rota"
area: tests, cache, redis
encountered: 2026-08-10
stack: pytest, Redis, asyncio, platform_settings
---

## Síntoma

Tras arreglar que `get_platform_setting` no acertara nunca en caché, aparecen
rojos en tests que llevaban meses verdes y **no tienen nada que ver con la
caché**:

```
tests/unit/test_budget_models.py::test_get_thresholds_reads_override
    assert [80, 90, 100] == [50, 75, 100]

tests/integration/test_celery_idempotency.py::test_execution_time_limits_default_override_and_clamp
    assert (7500, 7800) == (600, 900)
```

Los valores que aparecen son **los del paso anterior del mismo test**, o los
defaults que ese test acababa de comprobar.

## Causa raíz

Los dos tests leían un ajuste de plataforma **dos veces** con una escritura en
medio, y la escritura iba por debajo de la API:

- el unitario montaba un `_FakeSession` y esperaba que la lectura fuese a él;
- el de integración añadía las filas con `session.add(PlatformSetting(...))`,
  saltándose `set_platform_setting`, que es quien invalida.

Mientras la caché estuvo rota —el cliente Redis quedaba atado al event loop de la
primera llamada, la segunda levantaba, `_cached_read` lo capturaba y devolvía
«no hay caché»— **toda lectura caía a la BD** y el atajo funcionaba. Al arreglar
el defecto, la segunda lectura empezó a servir lo que la primera había cacheado.

O sea: **arreglar un defecto de producción puso rojo un test que se apoyaba en él
sin saberlo**. Los tests no estaban mal escritos por descuido; estaban escritos
contra un mundo en el que la caché no funcionaba. Y ese mundo era el real, solo
que nadie lo sabía.

Merece la pena nombrar la clase, porque volverá: **cuando una optimización lleva
mucho tiempo sin funcionar, el código a su alrededor se adapta a su ausencia.**
Arreglarla no es un cambio neutro.

## Fix

Depende de qué esté probando el test:

**Si la caché no es el sujeto** (la mayoría) — que no participe. En
`tests/unit/` ya lo hace un `conftest.py` autouse que corta
`_redis_for_this_loop`, con la regla escrita: en unitarios la BD es la única
fuente de verdad. Para probar la caché, marca el fichero:

```python
pytestmark = [pytest.mark.unit, pytest.mark.uses_platform_cache]
```

**Si el test escribe ajustes por debajo de la API** (integración, seeds) —
invalida como haría la escritura real, en vez de desactivar la caché. Así el
test sigue recorriendo el mismo camino de lectura que producción:

```python
from api_server.db.platform_settings import invalidate_platform_setting_cache

async with sm() as s, s.begin():
    s.add(PlatformSetting(key=CLAVE, value=600))
await invalidate_platform_setting_cache(CLAVE)   # lo que hace set_platform_setting
```

## Lo que NO hay que hacer

- **Subir el TTL a 0 o borrar la caché** para que los tests pasen: revierte un
  arreglo de rendimiento real (esa clave se lee en el camino caliente de cada
  run) por comodidad del arnés.
- **Meter un `sleep` hasta que expire el TTL**: 30 segundos por test.
- **Dar el rojo por flaky**: es determinista; lo que cambió es que la caché ahora
  funciona.

## Cómo verificar el fix

Que la invalidación sea de carga, no decorativa — sustitúyela por `return None` y
el test tiene que volver a rojo:

```bash
TEST_PG_DB_NAME=..._x TEST_REDIS_URL=redis://localhost:6379/6 \
  pytest tests/integration/test_celery_idempotency.py -q -p no:randomly
```

Y ojo con el hermano de esta trampa: **la Redis de test sobrevive entre sesiones
de pytest**. Un valor cacheado por una sesión anterior puede servir a la
siguiente. Detalle en
[integration-tests-share-one-database.md](integration-tests-share-one-database.md).
