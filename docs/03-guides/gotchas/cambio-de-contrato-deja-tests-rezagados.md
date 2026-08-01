---
title: "Cambiar el contrato de una ruta deja rojos los tests que NO corriste: busca por la ruta, no por el fichero"
area: tests, api
encountered: 2026-08-01
stack: pytest, FastAPI, httpx
---

## Síntoma

Un commit cambia lo que devuelve un endpoint —de `200 + JSON` a `303 + Set-Cookie`,
por ejemplo— y actualiza los tests afectados. La suite que corres queda verde y se
empuja. Días después, al correr un lote de integración por otro motivo, salen
rojos que no tienen nada que ver con lo que estás tocando:

```
FAILED tests/integration/test_jit_provisioning.py::test_first_login_creates_user_without_membership
E   assert 303 == 200
```

El `assert` no miente y el código no está roto: el test es de la versión anterior
del contrato. Lo peligroso es que **parece que lo acabas de romper tú**, porque
aparece en tu sesión y no en la del commit que lo causó.

## Causa raíz

Al cambiar el contrato se actualizaron los tests **del fichero que se tenía
delante** (`test_sso_global_login.py`), no todos los que ejercitan esa ruta. En
este repo la misma ruta la tocan siete ficheros de integración, y la misma
respuesta la producen dos endpoints distintos (el callback OIDC y el ACS de SAML
comparten `_identity_session_redirect`). Quedaron nueve tests afirmando `200`.

Y no se vio porque **`tests/integration/` son 517 ficheros**: nadie corre la
suite entera antes de empujar, así que un rojo ahí puede sobrevivir commits.
El único filtro real es correr «lo que toqué», y por definición eso no incluye
los ficheros que no sabías que existían.

## Fix

Al cambiar el contrato de una respuesta, **el criterio de búsqueda es la ruta,
no el fichero**:

```bash
# TODO lo que ejercita el endpoint, no solo el test que ya conoces
grep -rln "auth/sso/oidc/callback\|saml/acs" tests/

# y ese lote entero, en su propia BD (ver integration-tests-share-one-database.md)
TEST_PG_DB_NAME=..._sso TEST_REDIS_URL=redis://localhost:6379/11 \
  pytest tests/integration/test_saml.py tests/integration/test_jit_provisioning.py ... -q -p no:randomly
```

Si el cambio afecta a un helper compartido (aquí `_identity_session_redirect`),
busca también **por el helper**: da los llamantes, y cada llamante es otra ruta
con sus propios tests.

Y al arreglar los rezagados, extrae el cambio a un helper del propio test
(`_session_cookie(client)`, `follow_redirects=False` dentro de `_sso_callback`)
en vez de repetirlo en cada aserción: el siguiente cambio de contrato se hace
entonces en un sitio.

## Cómo reconocerlo antes de diagnosticar

Antes de creer que has roto algo, mira **quién** cambió esa línea:

```bash
git log -S"_identity_session_redirect" --oneline -- apps/api-server/src/api_server/routers/sso.py
git show <commit> --stat | grep tests/     # ¿qué tests actualizó... y cuáles no?
```

Si el commit que cambió el comportamiento actualizó unos tests y no otros, es
esto y no una regresión tuya. Es el mismo reflejo que con
[el `npm install` a medias](node-modules-a-medio-instalar-finge-regresion.md) y con
[la BD compartida](integration-tests-share-one-database.md): **antes de arreglar
un test, averigua si el rojo es del test o del código**.

## Cómo verificar el fix

```bash
grep -rln "<la ruta>" tests/ | xargs pytest -q -p no:randomly   # el lote entero verde
```
