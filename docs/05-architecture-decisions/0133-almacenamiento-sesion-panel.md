---
title: "ADR 0133: Almacenamiento de la sesión del panel — cookie httpOnly vs localStorage"
status: proposed
date: 2026-07-29
deciders: [operador]
relates_to: [0002, 0010, 0015, 0047, 0074, 0117]
plan_referenced: prod-09-sesiones-autorizacion-frontend
task: task_prod09_06
---

# ADR 0133: Almacenamiento de la sesión del panel — cookie httpOnly vs localStorage

> **Estado: `proposed`.** Nadie ha decidido todavía. Este documento existe para
> que el operador elija; las tareas `task_prod09_07` … `task_prod09_12` del plan
> `prod-09` están escritas asumiendo la Opción A y hay que reescribirlas si se
> elige otra.
>
> `task_prod09_06` pedía **un** ADR con la Decisión 1 y la Decisión 4 del plan.
> Se han separado en dos documentos porque son decisiones independientes con
> radios de explosión distintos: éste y el [ADR 0134](./0134-auto-registro-en-produccion.md)
> (auto-registro). Los dos juntos cierran `task_prod09_06`.

## Contexto verificado

### Cómo está hoy

El token de sesión —un JWT HS256 de **24 h** por defecto
([config.py:71](../../apps/api-server/src/api_server/config.py#L71))— vive en
`localStorage` bajo la clave `agentic.token`
([lib/auth.ts:9-26](../../apps/admin-panel/lib/auth.ts#L9-L26)). El propio
fichero lleva escrito desde el día uno que «Phase 15 will move it to an httpOnly
cookie»; el [ADR 0015](./0015-ui-tiempo-real-websocket.md#L85-L87) aplazó lo
mismo («la cookie httpOnly llega en Fase 15»). Seguimos aquí.

De ahí lo leen tres consumidores:

| Consumidor                                                                   | Qué hace                                      |
| ---------------------------------------------------------------------------- | --------------------------------------------- |
| [`lib/api.ts:54-57`](../../apps/admin-panel/lib/api.ts#L54-L57)              | `Authorization: Bearer <token>` en cada fetch |
| [`lib/ws.ts:56-57`](../../apps/admin-panel/lib/ws.ts#L56-L57)                | `?token=<jwt>` en la URL del WebSocket        |
| [`app/admin/layout.tsx:24`](../../apps/admin-panel/app/admin/layout.tsx#L24) | verja de auth en `useEffect`: «¿hay token?»   |

No existe `apps/admin-panel/middleware.ts` (comprobado: el fichero no está), así
que **no hay ninguna verja en el servidor**: la protección de `/admin/*` es un
`useEffect` de cliente que redirige después de haber servido y hidratado la
página.

Lo que un XSS se lleva de ahí no es un token cualquiera: si el usuario es System
Admin, el JWT lleva el claim `sys`
([auth/jwt.py:63-64](../../apps/api-server/src/api_server/auth/jwt.py#L63-L64)) y
con él se lee y escribe **cualquier tenant** vía la cabecera `X-Tenant-Id`
([ADR 0010](./0010-superadmin-cross-tenant.md)). Es la credencial más valiosa de
la plataforma, y está en un almacén que cualquier script de la página puede leer.

**Matiz al día (verificado 2026-07-29, tras aterrizar `task_prod09_04`)**:
`require_system_admin` ya **no** se fía solo del claim — re-lee
`users.is_system_admin` en cada request
([auth/deps.py:220-247](../../apps/api-server/src/api_server/auth/deps.py#L220-L247)),
igual que `require_system_owner` hace desde el ADR 0074
([deps.py:263-276](../../apps/api-server/src/api_server/auth/deps.py#L263-L276)).
Eso cierra la ventana de 24 h de un privilegio **revocado**, que era authz-4. Lo
que **no** cambia en absoluto es el caso de este ADR: el token robado de un admin
que **sigue siendo admin** funciona entero, cross-tenant, hasta que expire o
alguien revoque la sesión. La re-verificación mitiga el off-boarding, no el robo.

### El dato que cambia la ecuación: en producción hay UN solo origen

El compose que genera el instalador publica **exclusivamente** Caddy, y su
Caddyfile sirve el panel en `/` y el api-server en `/api/*` bajo el mismo
`https://{domain}`
([proxy_generator.py:111-123](../../apps/installer/backend/src/installer_backend/proxy_generator.py#L111-L123),
[compose_generator.py:939-966](../../apps/installer/backend/src/installer_backend/compose_generator.py#L939-L966)).
Panel y API son **same-origin en producción**.

Eso importa mucho, porque los tres argumentos clásicos contra las cookies
(«CORS», «SameSite», «no viajan al WebSocket») **no aplican en esa topología**:

- una cookie `SameSite=Lax` (incluso `Strict`) viaja en todas las peticiones del
  panel a `/api/*`;
- CORS deja de ser relevante para el panel (hoy hay allowlist con
  `allow_credentials=True`,
  [main.py:391-396](../../apps/api-server/src/api_server/main.py#L391-L396));
- y la cookie viaja **también en el handshake del WebSocket**, que es
  same-origin.

En **desarrollo** la topología es otra: panel en `localhost:3000`, API en
`localhost:8001`. Son puertos distintos pero el mismo _site_ (`SameSite` ignora
el puerto), y los navegadores tratan `localhost` como contexto seguro, así que
`Secure` no rompe. Es decir: la Opción A es viable en los dos entornos, pero por
razones distintas y con una comprobación pendiente en dev.

### El coste real, medido

- **107** ficheros `*.spec.ts` en `apps/admin-panel/e2e/`.
- **95** tocan `localStorage`.
- **93** inyectan la clave `agentic.token` a mano — el patrón que el
  [ADR 0015](./0015-ui-tiempo-real-websocket.md#L67-L72) documenta como
  deliberado («inyectan el JWT directamente en localStorage (la verja de auth
  del layout admin sólo lo comprueba en cliente)»).

El plan `prod-09` dice «99 specs». Son 107 hoy, y **el número crece cada
sprint**: cada spec nuevo que se escribe con el patrón viejo encarece esta
migración. Eso es un argumento de calendario, no de seguridad: cuanto más se
aplace, más cara es.

## La contrapartida honesta que ninguna opción evita

Hoy el esquema Bearer es **inmune a CSRF por construcción**: un atacante en otro
sitio puede provocar una petición al api-server, pero no puede añadir la
cabecera `Authorization`. Moverse a cookies **crea** una superficie CSRF que hoy
no existe y que hay que cerrar a mano. El intercambio es:

| Vector                                   | Hoy (localStorage + Bearer) | Con cookie httpOnly                      |
| ---------------------------------------- | --------------------------- | ---------------------------------------- |
| Exfiltración del token por XSS           | **abierta**                 | cerrada                                  |
| Uso del token por XSS desde la página    | abierta                     | abierta (el navegador adjunta la cookie) |
| Token superviviendo al cierre de pestaña | 24 h en disco               | cerrada (cookie de sesión)               |
| CSRF                                     | imposible                   | **abierta si no se cierra**              |
| Cross-Site WebSocket Hijacking           | imposible                   | **abierta si no se cierra**              |

Las dos últimas filas son trabajo nuevo obligatorio, no opcional:

- **CSRF**: doble-submit token (cookie legible + cabecera `X-CSRF-Token`
  verificada en toda mutación). Barato y estándar.
- **CSWSH**: el handshake de WebSocket **no honra CORS**, así que una cookie
  autentica el socket desde cualquier origen. Hoy
  [`routers/ws.py`](../../apps/api-server/src/api_server/routers/ws.py) **no
  valida `Origin` en absoluto** (cero ocurrencias de `origin` en el fichero) —
  no hace falta porque el credencial va en la query. Con cookie, validar
  `Origin` contra la allowlist pasa a ser un requisito de seguridad. Si se
  olvida, la migración a cookie **empeora** la postura del WebSocket.

Quien recomiende la Opción A sin nombrar estas dos cosas está vendiendo una
mejora que puede resultar una regresión.

## Opciones

### Opción A — Cookie `httpOnly + Secure + SameSite=Lax` + doble-submit CSRF

El api-server emite la sesión como cookie en login / select-tenant / callback
SSO; los deps de auth aceptan cookie **además** del Bearer (que se conserva para
la API pública, `curl` y los SDK); `middleware.ts` de Next verja `/admin/*` en el
edge leyendo la cookie; `apiFetch` pasa a `credentials: 'include'` y añade la
cabecera CSRF en mutaciones; `lib/ws.ts` deja de adjuntar el JWT y el endpoint WS
valida `Origin`.

**Coste** (sobre la estimación del plan, corregida con los números reales):

| Pieza                                                     | Estimación                                                         |
| --------------------------------------------------------- | ------------------------------------------------------------------ |
| Backend: `Set-Cookie` + dep dual cookie/Bearer + CSRF     | 12 h                                                               |
| Frontend: `middleware.ts`, `apiFetch`, retirar `getToken` | 12 h                                                               |
| SSO end-to-end (callback → cookie → redirect)             | 12 h                                                               |
| Validación de `Origin` en WS + retirada del `?token=`     | 4 h (no estaba presupuestado)                                      |
| **Migración de 93 specs e2e**                             | **16-24 h**, no las «actualizar los specs» que insinúa la tarea 08 |

**Lo que gana**: cierra frontend-2 / secrets-10 de verdad; hace posible una verja
server-side (hoy imposible: el edge no puede leer `localStorage`); y —efecto
lateral valioso— **hace innecesario el ticket WS de `task_prod09_12`**, porque
con un solo origen la cookie viaja en el handshake y el JWT desaparece de la URL
sin inventar nada.

**Riesgo**: si la migración de specs se hace a mano spec a spec, se va de
presupuesto y tienta a dejar la compatibilidad dual «temporal» para siempre —
que es exactamente el estado del que venimos.

### Opción B — Seguir en `localStorage`, con mitigación documentada

CSP estricta en `next.config.js` (sin `unsafe-inline` de script) + TTL de sesión
admin de 15 min **en todos los entornos** (hoy los 15 min de
[config.py:243-246](../../apps/api-server/src/api_server/config.py#L243-L246)
solo se aplican en staging/prod —
[admin_hardening.py:72-81](../../apps/api-server/src/api_server/auth/admin_hardening.py#L72-L81)
sigue restringiendo la aplicación a `{staging, prod}`, verificado hoy).
La re-verificación de `sys` contra BD (`task_prod09_04`) **ya está** y no es
crédito de esta opción: ayuda contra la revocación, no contra el robo.

**Coste**: ~8 h (la CSP ya está presupuestada en `task_prod09_15`, así que el
delta real es el clamp del TTL y la documentación). **Cero** specs tocados.

**Lo que NO gana**: la CSP mitiga la _inyección_ de script pero no protege de una
dependencia npm comprometida ni de un XSS a través de contenido que el panel
renderiza (y el panel renderiza **markdown de agentes y de humanos** en todos los
textareas con preview, más diagramas mermaid — superficie real, no teórica). El
token de System Admin sigue siendo legible por cualquier script de la página.
Riesgo residual **alto** para la credencial más valiosa del sistema.

**Cuándo es la respuesta correcta**: si el despliegue es una red interna sin
usuarios no confiables y hay algo más urgente en la cola. Es una decisión
defendible; lo que no es defendible es tomarla por omisión, que es lo que lleva
pasando desde el ADR 0015.

### Opción C — Token en memoria + cookie httpOnly de refresco

El access token vive **solo en memoria JS** (nunca en `localStorage`); una cookie
`httpOnly` de refresco, de larga vida, permite re-mintearlo al recargar la
página. Patrón «BFF-lite».

**Coste**: A + un endpoint `/auth/refresh` nuevo, la lógica de re-minteo en el
arranque del panel y la revocación del refresco en logout. Aproximadamente
**A + 10-14 h**.

**Lo que gana sobre A**: nada relevante en esta topología. Con un solo origen, A
ya no expone el token a JS en absoluto; C lo vuelve a exponer (en memoria) para
poder mandarlo como Bearer. Es la opción correcta cuando el frontend y el API
están en dominios distintos y no se puede usar cookie de sesión — **no es
nuestro caso**.

Se documenta para que quede constancia de que se consideró y por qué se
descarta, no como candidata real.

## Decisión propuesta (recomendación)

**Opción A**, con dos condiciones que no son adornos:

1. **La migración de los 93 specs se hace con un helper, no a mano.** Un único
   `e2e/helpers/session.ts` que establezca la cookie de sesión vía
   `context.addCookies()`, y una pasada mecánica que sustituya el
   `localStorage.setItem("agentic.token", …)` por su llamada. Si la migración se
   plantea spec a spec, la recomendación cambia a B: el coste real supera el
   beneficio en esta ventana.
2. **`Origin` se valida en el WS en la MISMA PR** que emite la cookie. Emitir la
   cookie antes de cerrar el CSWSH deja el sistema peor que hoy durante el
   tiempo que medie entre las dos PRs.

Y un aviso de estado, no de opción (verificado en el árbol de trabajo el
2026-07-29): **las tareas 01, 02, 03, 04 y 13 ya están implementadas** —
superficie `/admin/*` endurecida, entorno como enum cerrado con guarda
fail-closed, secreto interno separado ([ADR 0136](./0136-dominios-criptograficos-worker-api.md)),
re-verificación de `sys` contra BD y re-validación periódica de la sesión del
WebSocket (`ws_session_revalidate_seconds`,
[config.py:257](../../apps/api-server/src/api_server/config.py#L257)). Siguen sin
hacer la 05 (el rate limit vive en `login`, no en `register`) y la 12 (no existe
`POST /ws/ticket`).

O sea: **la Fase B es prácticamente lo único que sigue esperando esta firma.** Si
la respuesta es B, la Fase B se cierra en un día; si es A, es el trabajo más
grande que le queda a prod-09.

## Consecuencias sobre las tareas 07-12 de prod-09

| Tarea                                            | Si se elige **A**                                                                                                                                                                                        | Si se elige **B**                                                                                                                                                                                                                                             |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `task_prod09_07` (backend cookie + CSRF)         | Se ejecuta tal cual. **Añadir** al alcance: validación de `Origin` en WS.                                                                                                                                | **Se cae**. Sustituida por: clamp del TTL admin a 15 min en dev + documentar el riesgo residual en `docs/04-reference/`.                                                                                                                                      |
| `task_prod09_08` (frontend, fin de localStorage) | Se ejecuta, con el **coste de specs corregido a 16-24 h** y el helper de sesión como entregable explícito.                                                                                               | **Se cae**. La verja de `/admin/*` sigue siendo cliente-side; el `middleware.ts` no se puede escribir (el edge no lee `localStorage`).                                                                                                                        |
| `task_prod09_09` (SSO end-to-end)                | Se ejecuta: `Set-Cookie` + `RedirectResponse` con allowlist de orígenes.                                                                                                                                 | **Se ejecuta igual, pero peor**: el callback tiene que entregar el token al panel por un camino que JS pueda leer (fragmento `#token=` o página puente), y eso es una fuga nueva en logs/historial. Si se elige B, esta tarea necesita su propio mini-diseño. |
| `task_prod09_10` (401 global)                    | Independiente. Se ejecuta igual.                                                                                                                                                                         | Igual.                                                                                                                                                                                                                                                        |
| `task_prod09_11` (purga de caché TanStack)       | Independiente. Se ejecuta igual.                                                                                                                                                                         | Igual.                                                                                                                                                                                                                                                        |
| `task_prod09_12` (ticket WS de un solo uso)      | **Se simplifica o desaparece**: con un solo origen la cookie autentica el handshake y el `?token=` se retira sin ticket. Queda solo el `?tenant_id=`, que no es secreto. Ahorro estimado: 8 de las 10 h. | Se ejecuta tal cual (10 h): sin cookie, el ticket es la única forma de sacar el JWT de la URL.                                                                                                                                                                |

Neto: **A cuesta más de lo presupuestado en la tarea 08 y menos en la 12**. B
ahorra ~4,5 días de la Fase B y añade una deuda que el próximo auditor volverá a
levantar como high.

## Qué NO decide este ADR

- El auto-registro en producción → [ADR 0134](./0134-auto-registro-en-produccion.md).
- El contenido exacto de la CSP (`task_prod09_15`): se necesita en las dos
  opciones y su calibración para Next + mermaid es trabajo aparte.
- Si el `X-Tenant-Id` del superadmin debería viajar en la sesión server-side en
  vez de como cabecera de cliente ([ADR 0010](./0010-superadmin-cross-tenant.md)).
  Con cookie sigue siendo una cabecera, y una cabecera custom exige preflight
  CORS, así que el riesgo no cambia — pero es una pregunta legítima para otro día.

## Verificación

Si se acepta A, estas afirmaciones tienen que quedar fijadas por un test, no por
la memoria de nadie:

1. Tras un login por contraseña, `localStorage` **no contiene ninguna clave** con
   forma de JWT y la cookie de sesión tiene `HttpOnly` y `Secure`.
2. Una mutación con la cookie pero **sin** la cabecera CSRF → 403.
3. Un handshake de WebSocket con la cookie y un `Origin` fuera de la allowlist →
   rechazado. (Este es el test que nadie escribe y el que convierte la migración
   en una regresión si falta.)
4. `GET /admin/...` sin cookie → redirect del middleware a `/login` **sin** que
   se haya servido contenido de la página (no «flash» de UI protegida).
5. El Bearer sigue funcionando para un cliente de API (`curl` con
   `Authorization`), porque la API pública y los SDK dependen de ello.
6. Un login por SSO (OIDC) aterriza en el panel autenticado y **nunca** en un
   JSON crudo (hoy sí lo hace — frontend-1).

Si se acepta B, la verificación es más corta pero igual de obligatoria:

1. La sesión admin caduca a los 15 min **en dev** (hoy solo en staging/prod).
2. La CSP en `Report-Only` no registra violaciones durante una semana antes de
   promoverla a enforce.
3. `docs/04-reference/` documenta explícitamente el riesgo residual aceptado, con
   fecha y firma — para que el próximo auditor encuentre una decisión, no un
   olvido.
