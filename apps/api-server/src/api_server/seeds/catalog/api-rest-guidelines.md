# Principios de diseño de APIs REST

Guía de diseño de APIs REST agnóstica de stack: recursos, verbos HTTP, códigos
de estado, versionado, paginación, filtrado, HATEOAS y autenticación con
OAuth 2 / JWT. Referencia para diseñar contratos HTTP coherentes.

## Recursos y URIs

- Modela **recursos** (sustantivos), no acciones. La URI identifica una cosa,
  el verbo HTTP dice qué hacer con ella.
- Colecciones en plural: `/projects`, `/projects/{id}`,
  `/projects/{id}/tasks`.
- Anida sólo para expresar pertenencia real; evita anidamiento profundo
  (`>2` niveles) — prefiere `/tasks?project_id=...`.
- URIs en minúscula con guiones (`/knowledge-bases`), sin extensiones de
  fichero ni verbos (`/getProject` es incorrecto).
- Las acciones que no encajan en CRUD se modelan como sub-recursos o
  "controllers": `POST /orders/{id}/cancel`.

## Verbos HTTP y semántica

| Verbo    | Uso                           | Idempotente | Seguro |
| -------- | ----------------------------- | ----------- | ------ |
| `GET`    | Leer un recurso/colección     | Sí          | Sí     |
| `POST`   | Crear / acción no idempotente | No          | No     |
| `PUT`    | Reemplazar completo           | Sí          | No     |
| `PATCH`  | Actualización parcial         | No\*        | No     |
| `DELETE` | Eliminar                      | Sí          | No     |

- `GET` nunca muta estado. No pongas efectos secundarios en lecturas.
- `PUT` reemplaza el recurso entero; `PATCH` aplica un cambio parcial
  (JSON Merge Patch o JSON Patch).
- Soporta **idempotencia en POST** con una cabecera `Idempotency-Key` cuando el
  reintento pueda duplicar.

## Códigos de estado

Usa el código correcto; el cliente actúa según él:

- `200 OK` — éxito con cuerpo.
- `201 Created` — recurso creado; incluye `Location` con su URI.
- `202 Accepted` — aceptado para proceso asíncrono.
- `204 No Content` — éxito sin cuerpo (p.ej. `DELETE`).
- `400 Bad Request` — petición malformada.
- `401 Unauthorized` — falta o falla la autenticación.
- `403 Forbidden` — autenticado pero sin permiso.
- `404 Not Found` — recurso inexistente (o oculto por permisos).
- `409 Conflict` — conflicto de estado (duplicado, versión obsoleta).
- `422 Unprocessable Entity` — validación semántica fallida.
- `429 Too Many Requests` — rate limit; añade `Retry-After`.
- `500/503` — error del servidor / no disponible.

No devuelvas `200` con un campo `error` dentro: el código de estado es el
contrato.

## Formato de errores

Devuelve errores estructurados y consistentes. Adopta **RFC 9457 (Problem
Details)** o un formato propio estable:

```json
{
  "type": "https://errors.example.com/validation",
  "title": "Validation failed",
  "status": 422,
  "detail": "name must not be empty",
  "errors": [{ "field": "name", "message": "must not be empty" }],
  "trace_id": "abc123"
}
```

Incluye un `trace_id` para correlación. Nunca filtres trazas internas.

## Versionado

- Versiona desde el día uno. Estrategia más común: prefijo de ruta
  (`/v1/projects`). Alternativa: cabecera `Accept` con media type versionado.
- Sube de versión sólo ante cambios **incompatibles** (eliminar campos,
  cambiar tipos/semántica). Añadir campos opcionales es compatible y no rompe.
- Documenta y comunica la política de deprecación; usa la cabecera
  `Deprecation` / `Sunset`.

## Paginación, filtrado, ordenación

- Pagina toda colección que pueda crecer. Dos estilos:
  - **Offset**: `?page=2&page_size=50` — simple, pero degrada en offsets
    grandes y puede saltar/duplicar con escrituras concurrentes.
  - **Cursor/keyset**: `?cursor=...&limit=50` — estable y eficiente; preferido
    para feeds y volúmenes grandes.
- Devuelve metadatos: total (si es barato), `next`/`prev` cursors.
- Filtrado por query params explícitos (`?status=active&owner=...`).
- Ordenación con `?sort=-created_at` (prefijo `-` = descendente). Limita los
  campos ordenables a un allowlist.
- Permite proyección de campos (`?fields=id,name`) para respuestas ligeras.

## HATEOAS y descubribilidad

- Cuando aporte valor, incluye enlaces a transiciones posibles:

```json
{
  "id": "p1",
  "status": "draft",
  "_links": {
    "self": { "href": "/v1/projects/p1" },
    "publish": { "href": "/v1/projects/p1/publish", "method": "POST" }
  }
}
```

- HATEOAS reduce el acoplamiento de URIs, pero es opcional: documenta bien si
  no lo aplicas de forma completa.

## Contrato y documentación

- Define el contrato con **OpenAPI**; que sea la fuente de verdad y se genere
  o valide desde el código.
- Especifica request/response schemas, ejemplos y todos los códigos de error
  por endpoint.
- Mantén nombres de campos consistentes (`snake_case` o `camelCase`, pero uno
  solo en toda la API).

## Autenticación y autorización

- Autenticación con **OAuth 2 / OIDC**; transporta el acceso como **JWT**
  Bearer en `Authorization: Bearer <token>`.
- Valida en cada request: firma, `exp`, `iss`, `aud`. Tokens de acceso de vida
  corta + refresh tokens.
- **Autorización ≠ autenticación**: comprueba permisos por recurso (RBAC/ABAC)
  aunque el token sea válido. En multi-tenant, deriva el `tenant_id` del token,
  nunca de un parámetro manipulable por el cliente.
- Devuelve `401` si falta/expira el token, `403` si el sujeto no tiene permiso.

## Robustez operativa

- **Rate limiting** con cabeceras `X-RateLimit-*` y `Retry-After`; responde
  `429` al exceder.
- **CORS** explícito (orígenes permitidos), nunca `*` con credenciales.
- HTTPS obligatorio; HSTS y cabeceras de seguridad.
- Caché con `ETag` / `Cache-Control` y peticiones condicionales
  (`If-None-Match`) en lecturas.
- Salud y observabilidad: endpoints de `liveness`/`readiness` y propagación de
  `trace_id` end-to-end.
