---
title: httpx tira las cookies `Secure` si el `base_url` del test es http://
area: python
encountered: 2026-07-31
stack: httpx 0.27 (ASGITransport), FastAPI 0.115, pytest
---

# httpx tira las cookies `Secure` si el `base_url` del test es `http://`

## Síntoma

Un test de integración del login por cookie (ADR 0133) que parece correcto y
falla de una forma que no cuadra:

```
assert client.cookies.get("agentic_session")
AssertionError: assert None
```

...aunque el `Set-Cookie` **sí está** en la respuesta:

```python
assert "agentic_session" in response.headers["set-cookie"]   # pasa
assert client.cookies.get("agentic_session")                 # falla
```

Y la variante peor, que no falla: el test comprueba sólo la cabecera
`Set-Cookie`, pasa en verde, y **nunca acredita que la cookie autentique nada** —
la petición siguiente sigue yendo con `Authorization` o anónima.

## Causa raíz

El tarro de cookies de `httpx` implementa la regla del RFC 6265: una cookie con
el atributo `Secure` **sólo se guarda si la petición viajó por un canal seguro**.
Con

```python
AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
```

el esquema es `http`, así que la cookie de sesión se descarta **en silencio**.
No hay warning, no hay excepción: simplemente no está en el tarro.

Los navegadores hacen una excepción con `localhost` (contexto de confianza) que
`httpx` no aplica a `http://testserver`, de ahí que «en el navegador funciona».

## Fix

`base_url` **https** en cualquier test que ejercite cookies de sesión:

```python
def _client(app) -> AsyncClient:
    # https para que httpx CONSERVE las cookies `Secure`; con http las tira
    # en silencio y el test acreditaría una sesión que nunca viajó.
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver")
```

`ASGITransport` no abre socket: el esquema es sólo metadato del scope, así que
no hace falta TLS ni certificados. Si el test además comprueba un `Location`
(p. ej. el redirect del callback SSO), recuerda que el `Host` sigue siendo
`testserver` pero el `X-Forwarded-Proto` no existe, así que las URLs derivadas
del `sso_redirect_base_url` seguirán siendo las que diga la config, no `https`.

## Cómo detectarlo antes

La aserción que distingue los dos mundos:

```python
# NO basta: sólo dice que el servidor la mandó.
assert "agentic_session" in response.headers["set-cookie"]

# Esto es lo que hay que afirmar: la cookie SOLA autentica.
me = await client.get("/auth/me")          # sin Authorization
assert me.status_code == 200
```

Es el patrón del apartado 2 de
[`verificar-antes-de-implementar.md`](../verificar-antes-de-implementar.md): un
test que documenta lo observado (se emitió la cabecera) en vez de lo que debe
pasar (la sesión funciona) convierte el defecto en contrato.

## Relacionado

- Correr **dos** sesiones de pytest a la vez contra el mismo
  `TEST_PG_DB_NAME` hace que la fixture de sesión de una **borre la base de
  datos** de la otra a mitad de camino; el error que ves es
  `asyncpg.exceptions.InvalidCatalogNameError: database "..." does not exist` en
  tests que no tienen nada que ver. Un carril, una sesión de pytest.
- `create_app()` **dos veces en el mismo proceso** revienta con
  `prometheus_client.registry.DuplicateTimeseries` porque `PrometheusMiddleware`
  declara sus colectores contra el `REGISTRY` global. Se reproduce en
  `tests/integration/test_sso_global_login.py` (su segundo test falla por esto,
  sin tocar nada). Mientras no se arregle, un módulo que necesite la fixture
  `configured_app` de ese archivo sólo puede tener **un** test.
