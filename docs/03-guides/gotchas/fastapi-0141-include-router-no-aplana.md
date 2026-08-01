---
title: "FastAPI 0.141 dejó de aplanar `include_router`: `app.routes` pierde 300 rutas sin dar un error"
area: api, tests, dependencias
encountered: 2026-08-01
stack: FastAPI 0.141, Starlette 1.3, uv.lock
---

## Síntoma

Dos guardas que llevaban meses verdes se ponen rojas **sin que nadie toque el
código de la app**, y su mensaje dice algo tan gordo que no te lo crees:

```
FAILED tests/unit/test_metrics_endpoint_wired.py::test_metrics_does_not_shadow_the_authenticated_inbox_metrics
FAILED tests/unit/test_security_headers_middleware.py::...
```

Es decir, «desapareció el contrato público de la API». Y en el `.venv` de
desarrollo **pasan**.

## Causa raíz

Hasta 0.136, `app.include_router(r)` **aplanaba**: las rutas del hijo aparecían
en `app.routes` como `APIRoute`, cada una con su `.path` ya prefijado. Desde
**0.141** el padre recibe un objeto `_IncludedRouter` que:

- **no tiene `.path`** — `getattr(route, "path", None)` devuelve `None`;
- **no tiene `.routes`** — el hijo cuelga de `.original_router`;
- guarda el prefijo del include en `.include_context.prefix`.

Así que el idioma de toda la vida:

```python
paths = {getattr(route, "path", None) for route in app.routes}
```

pasa de ver ~300 rutas a ver cuatro (las montadas a pelo: `/openapi.json`,
`/docs`…) **sin lanzar ninguna excepción**. Silencioso.

## Lo que de verdad estaba en juego (y no eran los tests)

`api_server/main.py::_is_admin_surface` usa esta misma introspección para
decidir **si un router administrativo recibe `require_hardened_system_admin`**.
Existe porque una vez 9 de los 10 routers `/admin/*` —incluido el de restaurar
backups, que es destructivo— se publicaron sin esa dependencia.

Con la introspección rota, un router admin **compuesto de sub-routers** presenta
cero rutas `/admin`, la función devuelve `False`, y el router se monta **sin la
guarda**. Sin error y sin aviso: el fallo histórico, reintroducido por una
actualización de dependencia.

Hoy no salta porque ningún router del repo anida sub-routers. Es una trampa
armada, no una avería — y salta el día que alguien parta un router grande en
piezas, que en este repo pasa a menudo.

## Por qué no se veía

**El `.venv` de desarrollo tiene 0.136.1 y el `uv.lock` pina 0.141.1.** La
divergencia solo aparece en un entorno instalado desde el lock, o sea en CI. Con
CI caído por facturación, nadie corría esa resolución.

Vale la pena tenerlo presente como clase de problema: _lo que corres en local no
es lo que corre CI_, y un lock que nadie instala es una bomba de relojería con
la mecha del tamaño de la deuda de facturación.

```bash
.venv/Scripts/python.exe -c "import fastapi; print(fastapi.__version__)"   # 0.136.1
grep -A2 '^name = "fastapi"' uv.lock                                       # 0.141.1
```

## Fix

`api_server/routing_introspection.py`: `route_paths(app_o_router)` recorre las
rutas descendiendo por `original_router` y **acumulando el prefijo** de
`include_context.prefix`. Funciona igual en las dos versiones (en la vieja no
hay nada que bajar porque ya venía aplanado).

```python
from api_server.routing_introspection import route_paths

paths = route_paths(app)          # en vez de {getattr(r, "path", None) for r in app.routes}
```

## Cómo verificar el fix — y la parte que casi se me escapa

**No basta con que la suite pase en local**: en local pasa la versión vieja, que
es justamente la que no tiene el problema. Hay que probar contra la del lock:

```bash
python -m venv /tmp/fa141
/tmp/fa141/Scripts/python.exe -m pip install "fastapi==0.141.1"
cp apps/api-server/src/api_server/routing_introspection.py /tmp/
# …y un script que ejercite los casos con esa versión
```

Al hacerlo, **cuatro de los cinco casos pasaban y uno no**: el del prefijo. El
descenso a `original_router` da los `path` del hijo _sin prefijar_, así que un
router incluido con `prefix="/admin"` presentaba `/cosa` y la guarda seguía sin
reconocerlo como superficie administrativa. Justo el caso de seguridad.

Si me hubiera quedado en «la suite local está verde», habría dado por arreglado
un fallo que seguía abierto — y con la sensación de haberlo verificado. Ésa es
la diferencia entre creer que el arreglo funciona y saberlo.
