---
title: joserfc.jwt.decode NO valida exp (ni acepta una clave str)
area: python
encountered: 2026-07-30
stack: joserfc 1.6.8, python-jose 3.5 (retirado), FastAPI
---

## Síntoma

Ninguno. Eso es lo grave.

Al migrar `auth/jwt.py` de `python-jose` a `joserfc` (prod-09 task_prod09_17), la
suite entera sigue en verde, el login funciona, los tokens se firman y se
verifican… y **los tokens caducados se aceptan para siempre**. No hay excepción,
no hay log, no hay 401: `decode_jwt` devuelve las claims de un token expirado
como si estuviera vivo.

El segundo síntoma sí se ve, pero en el sitio equivocado: un token inválido
devuelve **500** en vez de 401.

## Causa raíz

Las dos librerías no validan lo mismo por defecto:

|               | `python-jose`                                                                   | `joserfc`                                                                                 |
| ------------- | ------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `exp`         | lo valida `jwt.decode` y lanza `ExpiredSignatureError` (subclase de `JWTError`) | **NO lo mira**. `jwt.decode` solo verifica la FIRMA                                       |
| clave HMAC    | acepta un `str`                                                                 | exige `OctKey`; con un `str` lanza `ValueError("Invalid key")`, que **no** es `JoseError` |
| claim ausente | —                                                                               | se considera satisfecho salvo que se declare `essential`                                  |

En joserfc la validación de claims es un paso aparte:

```python
jwt.JWTClaimsRegistry(exp={"essential": True}).validate(token.claims)
```

Nada te avisa de que no lo has llamado. Y el `except JoseError` de la traducción
literal deja escapar el `ValueError` de la clave y el `ValueError` de un payload
firmado que es un array JSON en vez de un objeto → 500.

Por qué el radio de acción es mayor de lo que parece: en este repo
`routers/ws.py::_credential_still_valid` (authz-3) vuelve a llamar a `decode_jwt`
sobre un socket YA ABIERTO con el único fin de notar la caducidad, y lo dice en
su docstring («`decode_jwt` already enforces signature + `exp` in one call»).
Perder la comprobación no habría afectado solo a la siguiente petición HTTP: los
WebSockets de larga duración habrían dejado de expirar.

## Fix

`apps/api-server/src/api_server/auth/jwt.py`:

1. Una registry a nivel de módulo con `exp` **essential** (para que «sin `exp`» se
   rechace igual que «`exp` pasado»), `leeway` en su 0 por defecto:

   ```python
   _CLAIMS = jwt.JWTClaimsRegistry(exp={"essential": True})
   ```

2. `verify_claims()` como ÚNICO punto que llama a `jwt.decode`: envuelve la clave
   en `OctKey.import_key(secret)`, valida las claims, rechaza un payload que no
   sea objeto JSON, y traduce **tanto** `JoseError` **como** `TypeError`/
   `ValueError` a `InvalidTokenError` (lo que los llamantes convierten en 401).
   `auth/internal_agent.py` (los tokens worker→api, con su propia clave) usa el
   mismo par `sign_claims`/`verify_claims`: una sola pila JOSE y una sola copia de
   estas dos trampas.

## Cómo verificar el fix

```
.venv/Scripts/python.exe -m pytest tests/unit/test_jwt_roundtrip_joserfc.py -q -p no:randomly
```

25 tests. Los que fijan la trampa:

- `test_an_expired_token_is_rejected`
- `test_a_token_that_expired_one_second_ago_is_rejected`
- `test_a_correctly_signed_token_without_exp_is_rejected`
- `test_malformed_tokens_raise_invalid_token_error` (9 casos)
- `test_a_json_array_payload_is_rejected`

Para comprobar que el test puede fallar: borra `_CLAIMS.validate(claims)` de
`verify_claims` y vuelve a correrlo — 4 rojos. Los tokens de la migración se
firman **al nivel del cable** (`hmac` + `base64url`, sin librería JOSE), así que
los tests siguen valiendo con `python-jose` fuera del árbol.

## Relacionado

- El mismo hueco sigue abierto en `auth/sso/oidc.py`: `_verify_id_token` valida
  firma + `iss`/`aud`/`nonce` y **no** valida `exp` del ID token. El `nonce` de
  un solo uso lo mitiga, pero es la misma trampa y el fix es el mismo.
- `pyproject.toml`: al retirar `python-jose[cryptography]` hay que declarar
  `cryptography` por su cuenta — Fernet (secreto OIDC, secreto TOTP) lo importa
  directamente y llegaba como extra de la dependencia retirada.
- Las fixtures firman con `"test-secret"` (11 caracteres) y `OctKey` avisa por
  debajo de 112 bits. Filtrado en pytest y explicado ahí: en producción
  `Settings` rechaza cualquier secreto de firma de menos de 32 caracteres.
