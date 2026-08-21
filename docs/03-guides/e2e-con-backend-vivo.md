---
title: Los 12 specs e2e que necesitan backend vivo (y que CI no corre)
area: tests / admin-panel
docs_language: es
---

# Los 12 specs e2e que necesitan backend vivo

De los 112 specs de Playwright de `apps/admin-panel/e2e/`, **100 mockean el
backend** con `page.route` y son los que corre CI en el job «Frontend e2e
(Playwright, mocked subset)». Los **12 restantes hablan con un api-server de
verdad**: hacen login real, crean proyectos, borran equipos y leen el catálogo.

Ese es el motivo de esta guía: **nadie los había visto pasar** hasta el
2026-08-20, porque CI no los selecciona y montar el entorno a mano cuesta una
hora de prueba y error. Ahora cuesta un comando.

```powershell
.\scripts\dev\e2e-live-harness.ps1
```

El guion deja el api-server en pie e imprime las credenciales y el comando exacto
de Playwright. Requisitos: el stack de docker levantado (`.\scripts\dev\up.ps1`),
el `.venv` y `npm install` hecho en `apps/admin-panel`.

| Modificador   | Para qué                                                                                                          |
| ------------- | ----------------------------------------------------------------------------------------------------------------- |
| `-Recreate`   | Borra la base desechable y empieza de cero. Es lo que hay que usar para comprobar que el guion sigue funcionando. |
| `-SkipSeeds`  | Sin catálogo. Ahorra minutos; los specs que cuentan agentes, equipos o plantillas fallarán.                       |
| `-BuildPanel` | Construye el admin-panel contra el arnés (si no, el guion imprime el comando).                                    |
| `-Down`       | Para el api-server y deja la base en pie para el siguiente arranque.                                              |

## Qué cubren estos 12 y qué no

Cubren lo que un mock no puede: que el login real llegue al dashboard, que el
RBAC de tenant-admin frente a member se aplique de verdad, que un canal de
notificación se cree y su secreto no vuelva en la respuesta, que el catálogo
sembrado se pinte entero.

No cubren nada que ya cubra el subconjunto mockeado, y **no sustituyen a
`tests/integration/`**: si lo que quieres verificar es el backend, un test de
integración es más rápido y más preciso. Estos existen para el tramo que sólo se
ve en un navegador contra un servidor.

## Las cinco cosas que hay que saber

Las cinco costaron una hora de diagnóstico y cada una tiene su gotcha con el
síntoma exacto, porque ninguna se anuncia como lo que es.

1. **Nunca contra la base del stack.** Estos specs no leen: **crean y borran**
   proyectos, agentes, equipos y tenants. El guion aborta si la base objetivo es
   la del compose, y lo comprueba por descubrimiento (el `POSTGRES_DB` de
   `docker/.env`) además de por una lista fija.

2. **El api-server usa DOS urls de base de datos.** `API_SERVER_DATABASE_URL`
   (rol `app_user`, NOBYPASSRLS) y `API_SERVER_ADMIN_DATABASE_URL` (rol
   `service_user`, BYPASSRLS). Poner sólo la primera **no falla**: la segunda cae
   a su default, que apunta a la base del stack, y las rutas `/admin/*` escriben
   ahí con un rol que la RLS no para. El guion aborta si alguna de las dos no
   acaba en la base del arnés.
   → [test-fixture-admin-db-url-override.md](gotchas/test-fixture-admin-db-url-override.md)

3. **Los `GRANT` no vienen con la migración.** En producción los pone el init del
   compose; en una base migrada a mano hay que aplicarlos, y el síntoma de que
   faltan es un `permission denied for table user_mfa_totp` **al hacer login**
   (la sonda de MFA es parte del camino de autenticación). Ojo con el retro-grant:
   un `ON ALL TABLES` sin excepciones **deshace el REVOKE deliberado** de la
   migración 0138 y deja el arnés más permisivo que producción.
   → [postgres-alter-default-privileges-per-db.md](gotchas/postgres-alter-default-privileges-per-db.md)

4. **El limitador de logins corta la pasada sin decir 429.** El límite de
   producción son 5 intentos por ventana y los buckets suben **aunque el login
   acierte**; con 41 casos desde `127.0.0.1`, el sexto login ya es 429. El síntoma
   es un `toHaveURL` que nunca llega. El guion sube el recuento **y** acorta la
   ventana: subir sólo el recuento deja una ventana de 15 minutos y la tanda
   siguiente empieza con la deuda de la anterior.
   → [auth-rate-limit-dev-loop.md](gotchas/auth-rate-limit-dev-loop.md)

5. **Los 5 s de `expect` no llegan.** `/admin/system-health` sondea los ocho
   servicios en paralelo, pero cada sonda tiene techo de 10 s y la respuesta
   espera a la más lenta: el `services-grid` del dashboard aparece a los ~12 s en
   frío. Con el default, **21 de 41 casos fallan por el reloj y ninguno dice nada
   del producto**. De ahí `E2E_EXPECT_TIMEOUT`, cuyo default en
   `playwright.config.ts` **sigue en 5 s** — que es lo correcto para el
   subconjunto mockeado, donde no hay backend al que esperar.
   → [expect-de-cinco-segundos-no-cubre-un-backend-vivo.md](gotchas/expect-de-cinco-segundos-no-cubre-un-backend-vivo.md)

Y una sexta que no es del arnés sino de quien lo monta a mano: **`ALTER ROLE …
PASSWORD` es de clúster, no de base**. Cambiarle la contraseña a `app_user`
«para el arnés» rompe el stack, y el contenedor sigue reportándose `healthy`
porque `/healthz` no toca la base.
→ [alter-role-password-es-de-cluster-no-de-base.md](gotchas/alter-role-password-es-de-cluster-no-de-base.md)

## El veredicto del 2026-08-20

La primera vez que estos specs corrieron: **41 casos, 21 rojos** con el
presupuesto por defecto → **11** al ponerlo en 25 s → **4** tras sembrar los
usuarios que los propios specs declaran en su cabecera.

De esos cuatro salió **un defecto real del producto**: en el diálogo de canales de
notificación, un efecto resincronizaba el formulario con `enabledTypes` en sus
dependencias, y ése es un array nuevo en cada render del padre mientras la
petición está en vuelo. Con el diálogo abierto, la llegada de la respuesta
**borraba lo escrito** y dejaba «Crear» deshabilitado para siempre, sin decir
nada. Arreglado y con test de regresión (`page.test.tsx`, «lo escrito SOBREVIVE a
que los transportes lleguen tarde»).

Los otros tres se quedaron en deuda del arnés. Y uno,
`personal-assistant.spec.ts:37`, **no puede pasar sin un proveedor LLM con
credenciales**: espera una respuesta real del asistente. Eso es una precondición,
no un fallo.

## ¿Y en CI?

Medido, no estimado: la pasada de los 12 tarda **2m42s-3m06s** cuando está en
verde (los 7m54s de la primera tanda eran los rojos consumiendo su presupuesto).
Los seeds tardan 8 minutos, pero **8 de esos 8 son la ingesta del catálogo contra
Ollama**, y sin Ollama degradan solo: `_embed` captura el error, persiste
embeddings nulos y el paso commitea por documento. Los conteos que estos specs
afirman —agentes, equipos, plantillas— salen de seeds puramente de BD.

O sea que **caben**: el job `Integration tests (cross-tenant + migrations)` ya
levanta el stack y un api-server en contenedor con un setup medido en 2m40s, y el
incremento sería `alembic upgrade head`, los seeds sin Ollama y los cinco
usuarios. Estimación ~8 minutos de trabajo real.

Con dos avisos antes de meterlo:

- `personal-assistant.spec.ts:37` se excluye por lo dicho arriba. Se excluye, no
  se disfraza.
- **El selector del job mockeado ya envejeció.** `grep -rlE "page.route|\.route\("`
  es un proxy textual de «autocontenido», y desde que el ADR 0133 movió los mocks
  al helper `seedSession` hay **3 specs que pasan sin backend ninguno**
  (`dev-portal`, `playwright-templates`, `ingestion-progress`: 16 casos en 41,7 s)
  que CI se salta. De paso, ese grep le pasa a Playwright tres ficheros de
  `e2e/helpers/` como si fueran specs. Sustituirlo por una lista explícita es
  gratis y mete esos 16 casos en CI hoy.
