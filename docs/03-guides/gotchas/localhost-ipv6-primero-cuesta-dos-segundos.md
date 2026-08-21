---
title: "`localhost` en el arnés cuesta 2 s por conexión (IPv6 primero) y tumba `/readyz`"
area: tests
encountered: 2026-08-18
stack: Windows 11, Docker Desktop, pytest integración, asyncpg + redis-py
---

## Síntoma

Dos síntomas que no se parecen entre sí y tienen la misma causa:

1. **La suite de integración tarda horas** más de lo razonable, sin que ningún
   test falle ni ningún log diga nada.
2. **`test_health_readiness` da 503 con PostgreSQL y Redis VIVOS**:

   ```
   {"status":"not_ready","checks":[
     {"name":"postgresql","ok":false,"duration_ms":2007,"detail":"timeout tras 2s"},
     {"name":"redis","ok":false,"duration_ms":2016,"detail":"timeout tras 2s"}]}
   ```

   Los dos checks tardan lo mismo (~2.01 s) y los dos caen por deadline. Que
   fallen **los dos a la vez y por timeout** es la pista: no es que las dos
   dependencias estén rotas, es que las dos pagan el mismo peaje.

## Causa raíz

En Windows, `getaddrinfo("localhost")` devuelve `::1` **antes** que `127.0.0.1`:

```python
>>> socket.getaddrinfo("localhost", 6379, type=socket.SOCK_STREAM)
[(AF_INET6, ..., ('::1', 6379, 0, 0)), (AF_INET, ..., ('127.0.0.1', 6379))]
```

Y los puertos que publica Docker Desktop escuchan **sólo en IPv4**. El intento
IPv6 no falla rápido: tarda ~2 s en darse por rechazado, y sólo entonces se
prueba la IPv4, que conecta en milisegundos. Medido en esta máquina:

```
sync connect localhost:6379   -> 2.053s
sync connect 127.0.0.1:6379   -> 0.013s
connect ::1:6379              -> ConnectionRefused tras 2.007s
connect localhost:15432       -> 2.041s
connect 127.0.0.1:15432       -> 0.003s
```

Como **todo acaba conectando**, no hay ningún error que seguir: son 2 s
regalados en CADA conexión nueva del arnés (y el arnés abre una conexión
`asyncpg` por helper de siembra). Ahí se van las horas.

Donde sí se ve es en cualquier cosa con un deadline corto. `/readyz` da
`READINESS_TIMEOUT_SECONDS = 2.0` por check (`api_server/routers/health.py`), o
sea justo por debajo del peaje: el check nunca llega a hablar con la
dependencia y readiness reporta «timeout tras 2s» de una base de datos que está
perfectamente viva.

## Fix

Que el arnés no escriba nunca `localhost`:

- `tests/integration/conftest.py` — `PG_HOST` por defecto es `127.0.0.1`.
- `tests/integration/_redis_url.py` — `_prefer_ipv4_loopback()` reescribe el
  host (y sólo el host, reconstruyendo el netloc: un `replace` sobre la URL
  entera corrompería una contraseña que contuviese «localhost») tanto de la URL
  por defecto como de la que llegue por `TEST_REDIS_URL`.

Lo que **no** es el arreglo: subir `READINESS_TIMEOUT_SECONDS` en el test. El
deadline de 2 s es una constante de producción y el test que la roza no está
midiendo latencia, está midiendo semántica; relajarlo esconde el peaje que sigue
pagando el resto de la suite.

En producción no aplica: dentro del compose los hosts son `postgres` y `redis`,
que resuelven a una sola dirección IPv4.

## Cómo verificar el fix

```bash
.venv/Scripts/python.exe - <<'PY'
import asyncio, time
async def t(h, p):
    s = time.monotonic()
    r, w = await asyncio.open_connection(h, p); w.close()
    print(h, p, round(time.monotonic() - s, 3))
asyncio.run(t("localhost", 6379))    # ~2.0 s en esta máquina
asyncio.run(t("127.0.0.1", 6379))    # ~0.005 s
PY
```

Y el test que lo denuncia:

```bash
P=$(grep '^REDIS_PASSWORD=' docker/.env | cut -d= -f2-)
TEST_REDIS_URL="redis://:${P}@localhost:6379/6" TEST_PG_DB_NAME=agentic_infra_salud \
  .venv/Scripts/python.exe -m pytest tests/integration/test_health_readiness.py -q -p no:randomly
```

Ojo al detalle: la URL de arriba lleva `localhost` **a propósito**, para
comprobar que la normalización actúa también sobre la que exporta quien lanza
la tanda.

## Parientes

- `docs/03-guides/gotchas/powershell-invoke-restmethod-localhost-hang.md` — el
  mismo `::1`-primero, visto desde PowerShell.
- `docs/03-guides/gotchas/redis-con-contrasena-rompe-la-integracion.md` — la
  otra trampa de la misma URL.
