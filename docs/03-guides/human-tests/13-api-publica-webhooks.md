# Plan 13 — tests humanos

Esta guía cubre los **4 tests humanos** del Plan 13 (API Pública,
Webhooks Entrantes y Eventos Externos). Validan lo que no se puede
automatizar sin sistemas externos reales: que un **X-API-Token respeta
el scope del tenant** (no hay fuga cross-tenant), que un **webhook de
GitHub real crea tareas** con firma HMAC válida, que el **rate limiting
devuelve 429** al pasar el umbral, y que el **SDK Python se instala y
ejecuta** el ejemplo del README con type hints.

> **Estado del plan**: `pending_human_validation`. Las 15 tareas
> (`task_13_01`..`task_13_15`) y sus tests automáticos están en verde
> (modelo ApiToken con scope/vigencia/rate_limit/IP allowlist, endpoint
> admin de tokens, middleware X-API-Token con cache Redis, rate limiting
> con sliding window, endpoints REST v1 `/api/v1/...`, OpenAPI 3.1 +
> Swagger UI, versionado, webhooks entrantes con HMAC, plantillas
> GitHub/Jira/Sentry/Linear/GitLab, mapeo webhook→acción, UI por
> proyecto, replay desde audit, SDK Python + SDK TypeScript, docs + ADR
> 0037). Estos 4 tests humanos son el último paso antes de pasar a
> `completed`.

## TL;DR

No hay `setup_demo_13.py` ni launcher dedicado para este plan: los tests
necesitan una cuenta de GitHub real (para configurar un webhook
apuntando a la instancia) y un cliente HTTP para forzar el rate limit.
El setup es manual:

```powershell
.\scripts\dev\up.ps1     # api-server :8001 + admin-panel :3000 + postgres + redis
```

Las pantallas y endpoints implicados:

```
http://localhost:3000/admin/settings/api-tokens        # Tenant Admin: crear/listar/revocar tokens
http://localhost:3000/admin/projects/{id}/webhooks       # config de webhooks entrantes por proyecto
http://localhost:8001/api/v1/openapi.json                # OpenAPI 3.1
http://localhost:8001/api/v1/docs                         # Swagger UI
```

La referencia completa de la API pública vive en
[`docs/04-reference/public-api.md`](../../04-reference/public-api.md) y
la guía de uso (token + SDK Python/TS + curl + registro de webhook) en
[`docs/03-guides/api-publica-y-webhooks.md`](../api-publica-y-webhooks.md).

## Pre-requisitos

| Requisito                                       | Por qué                                                                 |
| ----------------------------------------------- | ----------------------------------------------------------------------- |
| Stack dev arriba (`up.ps1`)                     | api-server + admin-panel + postgres + redis                             |
| Un usuario `tenant_admin`                       | Crear/revocar X-API-Tokens es operación de Tenant Admin                 |
| Dos tenants con datos (A y B)                   | `human_13_01` comprueba que el token de A no llega a recursos de B      |
| Una cuenta de GitHub (repo de pruebas)          | `human_13_02` configura un webhook real apuntando a la instancia        |
| Túnel HTTPS a la instancia (ngrok/cloudflared)  | GitHub solo entrega webhooks por HTTPS; expón `:8001` con un túnel      |
| `curl` o un cliente HTTP scriptable             | `human_13_01`/`03` lanzan requests con el token y fuerzan el rate limit |
| Python 3.12 + pip + el SDK del registry interno | `human_13_04` instala y ejecuta el ejemplo del SDK Python               |

---

## `human_13_01` — Token funciona y respeta scope

**Qué prueba**: un X-API-Token creado para el Tenant A lista los
proyectos del propio tenant, pero acceder a un recurso de otro tenant
devuelve 404 (no 403, para no filtrar existencia), y si el token tiene
IP allowlist, una conexión desde IP no autorizada falla.

**Precondiciones**:

- Login como `tenant_admin` del Tenant A.
- Dos tenants con datos (A y B), conociendo un ID de proyecto del B.
- `curl` o cliente HTTP a mano.

**Pasos**:

1. Como `tenant_admin` de A, ve a `/admin/settings/api-tokens` y **crea
   un token** (anota el valor — se muestra UNA vez).
2. **Lista proyectos del propio tenant** con el token:
   ```bash
   curl -s http://localhost:8001/api/v1/projects \
     -H "X-API-Token: $TOKEN_A"
   # → 200 con los proyectos de Tenant A
   ```
3. **Intenta un recurso de otro tenant** (usa el ID de un proyecto de B):
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" \
     http://localhost:8001/api/v1/projects/<ID_DE_TENANT_B> \
     -H "X-API-Token: $TOKEN_A"
   # → 404 (no se filtra que el recurso exista en otro tenant)
   ```
4. (Opcional) Crea otro token **con IP allowlist** que NO incluya tu IP,
   y repite el paso 2 desde esa IP: debe **fallar** (403/denegado por
   allowlist).

**Resultado esperado**: el token lista los recursos del propio tenant,
un recurso de otro tenant devuelve 404, y con IP allowlist una IP no
autorizada falla.

**Checklist**:

- [ ] Con el token, se puede listar proyectos del propio tenant.
- [ ] Intentar acceder a `/api/v1/projects` con ID de otro tenant
      devuelve 404.
- [ ] Si el token tiene IP allowlist, conexión desde IP no autorizada
      falla.

**Pitfalls conocidos**:

- El token va en el **header `X-API-Token`**, nunca en query param
  (Decisión Clave de seguridad). Si pruebas con `?token=`, la API no lo
  honra a propósito.
- El cross-tenant devuelve **404, no 403**: es intencional para no
  filtrar que el recurso existe en otro tenant. Si ves 403, repórtalo.
- El middleware **cachea el token en Redis** (`task_13_03`): si revocas
  un token y sigue funcionando un instante, espera a que expire el cache
  (o reinicia redis para forzar miss).

---

## `human_13_02` — Webhook GitHub crea tareas

**Qué prueba**: configurar un webhook desde GitHub real apuntando a
`/webhooks/incoming/github/...` hace que un push cree una tarea de
revisión, un PR abierto cree una tarea de revisión técnica, los issues
creen tareas, y una firma HMAC inválida devuelva 401.

**Precondiciones**:

- Un repo de GitHub de pruebas con permiso para configurar webhooks.
- La instancia accesible por HTTPS (túnel ngrok/cloudflared a `:8001`).
- El webhook entrante configurado en el proyecto desde
  `/admin/projects/{id}/webhooks` (origen `github` + su secreto HMAC).

**Pasos**:

1. En `/admin/projects/{id}/webhooks`, **registra un webhook entrante**
   de origen **GitHub**: copia la URL
   `/webhooks/incoming/github/{...}` y el **secreto HMAC**.
2. En GitHub (repo → Settings → Webhooks), **añade el webhook** con esa
   URL (HTTPS, vía túnel), el secret, y eventos push + pull_request +
   issues.
3. **Push a una rama**: en el proyecto debe aparecer una **tarea de
   revisión**.
4. **Abre un PR**: debe crearse una **tarea de revisión técnica**.
5. **Crea un issue**: debe crearse una **tarea automática**.
6. **Fuerza una firma inválida**: cambia el secret en GitHub (o manda un
   POST con HMAC erróneo) → la entrega debe devolver **401**.

**Resultado esperado**: push/PR/issue crean las tareas mapeadas en el
proyecto, y una firma HMAC inválida devuelve 401.

**Checklist**:

- [ ] Push a branch crea tarea de revisión en el proyecto
      correspondiente.
- [ ] PR opened crea tarea de revisión técnica.
- [ ] Issues crean tareas automáticas.
- [ ] Si la firma HMAC falla, devuelve 401.

**Pitfalls conocidos**:

- GitHub **solo entrega por HTTPS**: `localhost:8001` no es alcanzable
  desde GitHub. Usa un túnel (ngrok/cloudflared) y registra esa URL.
- La **firma HMAC es obligatoria** (Decisión Clave): si el secret de
  GitHub no coincide con el del webhook registrado, todo entra como 401
  — verifica que copiaste el mismo secret en ambos lados.
- Si el push no crea tarea pero GitHub marca la entrega como 200,
  comprueba el **mapeo webhook→acción** del proyecto (`task_13_10`): el
  evento debe estar mapeado a "crear tarea". El **replay desde audit**
  (`task_13_12`) ayuda a re-disparar sin volver a hacer push.

---

## `human_13_03` — Rate limiting funciona

**Qué prueba**: con el mismo token, superar el umbral (default 100
req/min) hace que la request 101 devuelva 429, el header
`X-RateLimit-Remaining` decrementa, y tras 60 s la ventana se reinicia.

**Precondiciones**:

- Un X-API-Token válido del Tenant A (del `human_13_01`).
- Un cliente HTTP capaz de lanzar >100 requests en menos de un minuto.

**Pasos**:

1. Lanza **>100 requests/min** con el mismo token contra un endpoint v1:
   ```bash
   for i in $(seq 1 105); do
     curl -s -o /dev/null -w "%{http_code} " \
       http://localhost:8001/api/v1/projects \
       -H "X-API-Token: $TOKEN_A"
   done; echo
   # → 100 veces 200, luego 429
   ```
2. Observa que la **request 101 devuelve 429 Too Many Requests**.
3. Inspecciona el header **`X-RateLimit-Remaining`** en respuestas
   sucesivas: debe **decrementar** hacia 0 (usa `curl -D -`).
4. **Espera 60 s** y vuelve a lanzar una request: la ventana se ha
   **reiniciado** y responde 200 de nuevo.

**Resultado esperado**: la request que supera el umbral devuelve 429,
`X-RateLimit-Remaining` decrementa, y la ventana se reinicia tras 60 s.

**Checklist**:

- [ ] La request 101 devuelve 429 Too Many Requests.
- [ ] Header `X-RateLimit-Remaining` decrementa correctamente.
- [ ] Tras 60 s la ventana se reinicia.

**Pitfalls conocidos**:

- El rate limit es por **token**, no por IP, y usa **sliding window en
  Redis** (`task_13_04`): si redis está caído, el límite no se aplica —
  comprueba que el contenedor de redis responde.
- El límite por defecto es **100 req/min configurable** por token: si tu
  token tiene un límite distinto, ajusta el número de iteraciones del
  bucle.
- La ventana es **deslizante** (no fija a minuto de reloj): tras el
  primer 429, no esperes que se libere exactamente en el segundo 60 del
  reloj sino 60 s después de las primeras requests.

---

## `human_13_04` — SDK Python es usable

**Qué prueba**: el SDK Python se instala desde el registry interno, el
ejemplo del README ejecuta sin errores, y los type hints están
disponibles en el IDE.

**Precondiciones**:

- Python 3.12 + pip + un virtualenv limpio.
- Acceso al registry interno donde se publica el SDK.
- Un X-API-Token válido para el ejemplo.

**Pasos**:

1. En un virtualenv limpio, **instala el SDK** desde el registry
   interno:
   ```bash
   pip install agentic-sdk    # nombre según packages/sdk-python/README.md
   ```
2. **Ejecuta el ejemplo del README** del SDK
   (`packages/sdk-python/README.md`): configura el `X-API-Token` y lista
   proyectos. Debe ejecutar **sin errores**.
3. Abre el ejemplo en tu **IDE** (VS Code/PyCharm) y comprueba que los
   **type hints** del cliente y los modelos Pydantic v2 están
   disponibles (autocompletado + tipos).

**Resultado esperado**: `pip install` funciona, el ejemplo del README
ejecuta sin errores y los type hints aparecen en el IDE.

**Checklist**:

- [ ] `pip install` funciona desde el registry interno.
- [ ] Ejemplo del README ejecuta sin errores.
- [ ] Type hints disponibles en IDE.

**Pitfalls conocidos**:

- El SDK Python se generó con `datamodel-code-generator` (Pydantic v2) +
  cliente httpx fino escrito a mano (no con `openapi-python-client`, que
  produciría modelos attrs incompatibles con ruff/mypy) — ver
  `packages/sdk-python/README.md`. Los type hints vienen de los modelos
  Pydantic.
- Si el ejemplo falla con 401, comprueba que el token no caducó y que va
  en el header `X-API-Token`.
- El SDK habla con `/api/v1/...`: si tu api-server no expone v1 (revisa
  `/api/v1/openapi.json`), el SDK no encuentra endpoints.

---

## Cierre del plan

Tras pasar los 4 tests humanos:

1. Edita `docs/roadmap/13-api-publica-webhooks.md`:
   ```yaml
   status: completed
   completed_at: 2026-MM-DD
   ```
2. Verifica la entrada en
   [`docs/07-changelog/13-api-publica-webhooks.md`](../../07-changelog/),
   la guía
   [`docs/03-guides/api-publica-y-webhooks.md`](../api-publica-y-webhooks.md)
   y la referencia
   [`docs/04-reference/public-api.md`](../../04-reference/public-api.md).
3. Verifica que el PR `plan/13-api-publica-webhooks` está mergeado a
   `master`.

## Troubleshooting

| Síntoma                                       | Causa probable                                       | Fix                                                                       |
| --------------------------------------------- | ---------------------------------------------------- | ------------------------------------------------------------------------- |
| El token llega a recursos de otro tenant      | (No debería) fallo de scope — repórtalo              | El cross-tenant debe dar 404; el token está scoped al tenant que lo creó  |
| GitHub marca la entrega del webhook en rojo   | URL no HTTPS o instancia inalcanzable desde GitHub   | Usa un túnel HTTPS (ngrok/cloudflared) y registra esa URL en GitHub       |
| El webhook entra pero no crea tarea           | Mapeo webhook→acción no configurado para ese evento  | `/admin/projects/{id}/webhooks` → revisa el mapeo; usa replay desde audit |
| La firma HMAC válida da 401                   | Secret distinto en GitHub y en el webhook registrado | Copia el mismo secret en ambos lados; el HMAC es obligatorio              |
| El rate limit nunca dispara el 429            | Redis caído (la sliding window vive en Redis)        | Comprueba el contenedor redis; sin Redis no hay límite                    |
| `pip install` del SDK no encuentra el paquete | Registry interno no configurado en pip               | Apunta pip al registry interno (índice extra); ver README del SDK         |

Errores transversales viven en `docs/03-guides/gotchas/`.
