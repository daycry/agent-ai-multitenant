---
title: Los defaults de Celery del api-server apuntan a 6379 aunque REDIS_PORT sea otro
area: redis
encountered: 2026-08-20
stack: celery 5.x, redis 7 (compose), Windows 11 con Laragon, docker/.env REDIS_PORT=6380
---

## Síntoma

Un proceso del api-server arrancado **en el host** (uvicorn local, un script, un
test, la CLI) se queda ~110 segundos colgado en cualquier cosa que encole trabajo,
y luego sigue como si nada. El caso que lo destapó: **cada turno del córtex**.
`POST /owner/cortex/turns` llama a `enqueue_cortex_distill_affect`, que es
fire-and-forget en la intención pero `await`-ado de hecho, así que el turno entero
paga la espera.

Lo que sale al final no menciona ni el puerto ni el broker:

```
RuntimeError: Retry limit exceeded while trying to reconnect to the Celery
result store backend. The Celery application must be restarted.
```

Medido en esta máquina el 2026-08-20: **109,9 s** hasta esa excepción.

Y como el productor se traga el fallo a propósito —«un fallo del broker no puede
tumbar el turno que el owner ya recibió»—, en los logs sólo queda un
`cortex_affect.enqueue_failed`, y en el usuario, dos minutos de espera sin
explicación.

## Causa raíz

Dos cosas que se suman:

1. **Los defaults de `api_server.config.Settings` apuntan al 6379**:
   `broker_url = redis://localhost:6379/1` y
   `result_backend = redis://localhost:6379/2`. Pero `docker/.env` publica la
   Redis del compose en **`REDIS_PORT=6380`** (el 6379 lo ocupa la Redis de
   Laragon; ver
   [`postgres-port-clash-with-laragon.md`](./postgres-port-clash-with-laragon.md),
   que es la misma historia con Postgres). Dentro del stack no se nota:
   `docker-compose.manuals.yml` inyecta `API_SERVER_BROKER_URL` y
   `API_SERVER_RESULT_BACKEND` apuntando a `redis:6379` por la red interna. La
   trampa es **sólo** para procesos del host, que es donde se depura.

2. **En el 6379 hay alguien que acepta la conexión y no contesta.** No es
   `connection refused`, que fallaría en milisegundos: el `connect()` tiene éxito
   y la respuesta no llega nunca, así que kombu reintenta con backoff hasta agotar
   la política. De ahí los ~110 s en vez de un error inmediato.

   ```console
   $ python -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1',6379)); s.sendall(b'PING\r\n'); print(s.recv(100))"
   TimeoutError: timed out          # 6379: conecta y enmudece
   $ # el mismo sondeo contra 6380 responde: b'-NOAUTH Authentication required.'
   ```

Y hay un tercer detalle que hace perder otra media hora: **el que revienta es el
`result_backend`, no el broker**. Redirigir sólo `API_SERVER_BROKER_URL` deja el
backend en su default y el síntoma no cambia. Es la misma trampa que documenta
`tests/integration/_redis_url.py` para el arnés: «con Celery hay que redirigir DOS
cosas, no una».

## Fix

Exportar **las dos** variables antes de arrancar cualquier proceso del api-server
en el host, con la contraseña y el puerto que dice `docker/.env`:

```bash
# desde la raíz del repo, en bash
export REDIS_PASSWORD=$(grep '^REDIS_PASSWORD=' docker/.env | cut -d= -f2-)
export REDIS_PORT=$(grep '^REDIS_PORT=' docker/.env | cut -d= -f2-)
export API_SERVER_BROKER_URL="redis://:${REDIS_PASSWORD}@127.0.0.1:${REDIS_PORT}/1"
export API_SERVER_RESULT_BACKEND="redis://:${REDIS_PASSWORD}@127.0.0.1:${REDIS_PORT}/2"
```

`127.0.0.1` y no `localhost` a propósito: ver
[`localhost-ipv6-primero-cuesta-dos-segundos.md`](./localhost-ipv6-primero-cuesta-dos-segundos.md).

Cuidado con la otra mitad, que esta nota **no** resuelve: las bases 1 y 2 son las
del stack vivo, así que un proceso del host encolando ahí manda trabajo real a los
workers de verdad. Para pruebas, usa una base de la 5 en adelante —y entonces
nadie drenará la cola, que suele ser justo lo que quieres.

## Cómo verificar el fix

Con las variables puestas, este sondeo debe terminar en menos de un segundo en vez
de en ~110:

```bash
python - <<'PY'
import sys, time
sys.path.insert(0, "apps/api-server/src")
from api_server.celery_client import get_celery_client
from api_server.config import get_settings
print("broker:", get_settings().broker_url)
t0 = time.monotonic()
get_celery_client().send_task("workers.cortex_distill_affect", args=["probe"], queue="cortex.affect")
print("enqueue OK en", round(time.monotonic() - t0, 2), "s")
PY
```

Sin ellas, el mismo script imprime `redis://localhost:6379/1` y tarda ~110 s en
levantar el `RuntimeError` del principio.
