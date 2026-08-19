---
title: Un commit a media request se lleva por delante el contexto de tenant
area: postgres
encountered: 2026-08-19
stack: SQLAlchemy 2.x async, asyncpg, PostgreSQL 16, FastAPI
---

# `await session.commit()` dentro de un handler apaga la RLS para el resto del request

## Síntoma

**No hay síntoma.** Eso es lo que hace peligrosa esta trampa: el código funciona,
los tests pasan en verde y la request devuelve lo que debe. El daño aparece más
tarde, cuando alguien añade una consulta después de ese commit — y entonces la
forma que toma depende de quién sea el rol:

- con `app_user` (NOBYPASSRLS) la consulta devuelve **cero filas** sin error,
  porque las políticas comparan contra un `app.tenant_id` que ya está vacío;
- con la sesión BYPASSRLS del System Admin devuelve **filas de todos los
  tenants**, que es una fuga.

Es decir: el fallo se manifiesta en el commit **siguiente** al culpable, escrito
por otra persona, en otro sitio del handler.

## Causa raíz

`api_server.auth.deps.open_tenant_session` acota la RLS así:

```python
async with sessionmaker() as session:
    async with session.begin():
        await session.execute(
            text("SELECT set_config('app.user_id', :uid, true)"), {"uid": ...}
        )
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": ...}
        )
        yield session
```

El tercer argumento de `set_config` es `is_local`, y va a `true`: el valor tiene
**ámbito de transacción**, igual que un `SET LOCAL`. Es la decisión correcta —un
GUC de sesión sobreviviría a la devolución de la conexión al pool y contaminaría
la request siguiente— pero tiene una consecuencia que no se ve leyendo el
handler: **cerrar la transacción borra el contexto de tenant.**

Y `session.commit()` cierra la transacción. Después de él, la sesión abre otra
implícita en la primera consulta, sin `app.user_id` ni `app.tenant_id`.

Se descubrió al mover las puertas del marketplace a Celery (prod-13
`task_prod13_01`): el productor comiteaba a media request para que el worker no
leyese una fila aún no durable. Cumplía su objetivo, no rompía nada porque después
no había ninguna consulta más… y dejaba un campo de minas para la siguiente línea.

## Fix

Para el caso «necesito que esto se publique DESPUÉS del commit», que es el que
lleva a comitear a mano, ya existe el mecanismo:

```python
from api_server.auth.deps import schedule_after_commit

schedule_after_commit(session, _publish)   # corre tras el commit del request
```

`open_tenant_session` ejecuta esos callbacks al salir del `session.begin()`, o sea
con la fila ya durable, y se los traga si fallan (una publicación fallida no puede
romper una request ya comiteada). Su docstring documenta el mismo problema para
los eventos de dominio: publicar inline deja que un consumidor rápido lea la fila
sin comitear y la salte en silencio.

Dos cosas más, si escribes uno de esos callbacks:

1. **Captura los ids en variables ANTES del closure.** El callback corre con la
   transacción cerrada; leer un atributo de una instancia expirada dispara un
   refresh sobre una sesión que ya no puede consultar.
2. **Si de verdad necesitas comitear a media request**, vuelve a fijar los GUC
   después con el mismo `set_config(..., true)`, y escribe por qué. La opción por
   defecto es no hacerlo.

En el worker el problema es el inverso y conviene no confundirlos: allí la sesión
de `workers.db.worker_session` **no** abre transacción, la abre implícitamente la
primera consulta — así que un `async with session.begin()` posterior revienta con
`InvalidRequestError: a transaction is already begun`. En los workers se comitea a
mano; en el api-server, no.
