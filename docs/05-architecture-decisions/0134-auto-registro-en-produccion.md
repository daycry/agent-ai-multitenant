---
title: "ADR 0134: Auto-registro de usuarios en producción"
status: accepted
date: 2026-07-29
deciders: [operador]
relates_to: [0074, 0133]
plan_referenced: prod-09-sesiones-autorizacion-frontend
task: task_prod09_06
---

# ADR 0134: Auto-registro de usuarios en producción

> **Estado: `accepted`** (2026-07-31, decidido por el operador).
>
> **Decisión tomada: Opción C — registro **por invitación**.**
>
> El operador eligió la opción MÁS restrictiva de las tres, por encima de la
> recomendación del ADR (que era la A, el ajuste `allow_self_registration`). El
> registro público se cierra y se entra con un token emitido por un admin: hay que
> construir emisión, caducidad, canje y la pantalla correspondiente.
>
> Sigue siendo obligatorio el arreglo del **System Owner**, y ahora con más motivo:
> hoy el único sitio que asigna `is_system_owner` es el registro público con la
> tabla `users` vacía, así que al cerrarlo hay que dar esa vía por otro lado o el
> córtex queda inalcanzable para siempre. El arranque de la primera instalación es
> parte del alcance de esta decisión, no un detalle de implementación.

## Contexto verificado

### `POST /auth/register` está abierto de par en par

El endpoint no pide autenticación, no tiene rate limit y no consulta ningún
ajuste
([routers/auth.py:173-219](../../apps/api-server/src/api_server/routers/auth.py#L173-L219)).
Cualquiera que alcance el api-server puede crear una cuenta. En producción el
api-server se alcanza desde internet si el dominio es público: Caddy publica
`https://{domain}/api/*`
([proxy_generator.py:117-119](../../apps/installer/backend/src/installer_backend/proxy_generator.py#L117-L119)).

Un usuario recién registrado no tiene membresía en ningún tenant, así que
aterriza en la pantalla `no_access`
([lib/session.ts:100-102](../../apps/admin-panel/lib/session.ts#L100-L102)). No
accede a datos. Pero:

- **crea filas de `users` sin límite** (spam de cuentas, ruido en `/admin/users`,
  crecimiento de una tabla global no-RLSed);
- **confirma direcciones de correo**: registrar un email ya existente devuelve
  **409** (`IntegrityError` del flush →
  [auth.py:211-216](../../apps/api-server/src/api_server/routers/auth.py#L211-L216))
  y uno nuevo devuelve 201. Es un oráculo de enumeración perfecto, y encima más
  fiable que el del login (que ya se está igualando en tiempos vía
  `task_prod09_05`);
- **es la puerta del JIT de SSO**: un usuario local con el mismo email que una
  identidad del IdP es un caso de colisión que conviene no regalar.

### El detalle que convierte esto en algo más que higiene

Cuando la tabla `users` está **vacía**, el registro promociona al primer usuario
a **System Admin y System Owner**
([auth.py:197-207](../../apps/api-server/src/api_server/routers/auth.py#L197-L207)):

```python
is_system_admin=is_first_user,
is_system_owner=is_first_user,
```

En un despliegue donde el api-server arranque con la base vacía y sea alcanzable
antes de que nadie siembre el admin, **el primer visitante se queda con el
despliegue**. No es una hipótesis remota: es exactamente lo que pasa si alguien
levanta el stack a mano (`docker compose up`) en vez de por el instalador.

### Y el hallazgo que sale de tirar de ese hilo

El instalador **sí** siembra el tenant inicial y su admin antes de exponer nada
([real_step_executor.py:196-211](../../apps/installer/backend/src/installer_backend/real_step_executor.py#L196-L211)
→ `python -m api_server.seeds.init_tenant`), así que la ventana de arriba está
cerrada en la ruta soportada. Pero ese seed **no fija `is_system_owner`**
([seeds/init_tenant.py:103-108](../../apps/api-server/src/api_server/seeds/init_tenant.py#L103-L108)
pasa `is_system_admin=is_first_user` y nada más; la columna tiene
`server_default false`,
[models.py:271-273](../../apps/api-server/src/api_server/db/models.py#L271-L273)).

Y `auth.py:206` es el **único** sitio de todo `apps/` que escribe
`is_system_owner=True` (comprobado: cero coincidencias de `is_system_owner=True`
en `apps/`, y ningún endpoint de `/admin/users` lo promueve).

Consecuencia, hoy, en cualquier instalación hecha con el instalador:

> **nadie es System Owner.** Todo lo que `require_system_owner` protege —el
> córtex, [ADR 0074](./0074-rol-system-owner-y-cortex-singleton.md)— es
> inalcanzable de forma permanente, y no hay gesto en el producto para arreglarlo
> salvo un `UPDATE` a mano en la base de datos.

Esto importa para esta decisión por una razón concreta: **cerrar el
auto-registro no puede ser lo que cimente ese bug**. Si mañana alguien vacía la
base para reinstalar y el registro está cerrado, la única puerta que quedaba
—registrarse el primero— también se cierra. La decisión de producto y el arreglo
del propietario tienen que viajar juntos.

### El mecanismo para el ajuste ya existe

`platform_settings` es una tabla plana `key → JSONB`
([models.py:407-419](../../apps/api-server/src/api_server/db/models.py#L407-L419))
con un registro tipado y su UI
([platform_settings_registry.py:50-98](../../apps/api-server/src/api_server/platform_settings_registry.py#L50-L98)).
Añadir un `bool` es media hora. Hay **una arista**: `PlatformSettingDef.default`
es un valor estático del dataclass, así que «`false` en staging/prod y `true` en
dev» **no se puede expresar como default del registro** — o el lector lo deriva
de `settings.environment` cuando la fila no existe, o el instalador siembra la
fila. Es una decisión de implementación, pero conviene fijarla aquí para que no
se resuelva por accidente en el peor sentido (un default estático `true` sería
fail-open).

## Opciones

### Opción A — `allow_self_registration`, default `false` fuera de dev

Ajuste de plataforma; con el registro cerrado, `POST /auth/register` devuelve un
**403 genérico** (mismo cuerpo tanto si el email existe como si no, con lo que se
cierra de paso el oráculo del 409). En dev el default es `true` para no estorbar.
El alta de usuarios pasa a ser: siembra del instalador → invitación desde
`/admin/users` → SSO JIT → SCIM.

**Coste**: ~4 h (registro del ajuste + lectura en el endpoint + tests), dentro de
`task_prod09_05`, que ya toca ese endpoint.

**Riesgo**: bloquear el arranque de un despliegue nuevo si el default se aplica
antes de que exista el primer usuario. **Mitigación obligatoria**: la promoción
del primer usuario debe seguir funcionando aunque el ajuste esté cerrado — o
sea, el gate es «tabla `users` no vacía **y** ajuste cerrado → 403». Un gate que
ignore ese matiz convierte una reinstalación en un ladrillo.

### Opción B — Dejarlo abierto, con rate limit y CAPTCHA

Solo `task_prod09_05` (rate limit por IP). El auto-registro sigue disponible como
vía de alta.

**Coste**: 0 adicional. **Lo que NO cierra**: la enumeración por 409, el spam de
cuentas y la posibilidad de que un despliegue con la base vacía sea reclamado por
un extraño. Es la opción correcta **solo** si el producto quiere que la gente se
dé de alta sola y luego un admin les asigne tenant — un flujo que hoy no existe
en la UI (no hay bandeja de «solicitudes de acceso»; el usuario ve `no_access` y
se acabó). O sea: hoy el auto-registro **no sirve para nada** salvo crear filas.

### Opción C — Registro por invitación (token)

Se retira el `POST /auth/register` público y se sustituye por
`POST /auth/register?invite=<token>`, donde el token lo emite un admin desde
`/admin/users` y lleva tenant + rol.

**Coste**: 2-3 días (modelo de invitación, caducidad, email, UI, tests). **Lo que
gana sobre A**: convierte el alta en autoservicio _controlado_ en vez de un gesto
manual del admin. **Cuándo**: cuando haya volumen de usuarios que lo pague. Con
el alcance actual —departamentos y equipos internos,
[CLAUDE.md](../../CLAUDE.md)— no lo paga.

### Opción D — El ajuste, pero por tenant

Descartada de entrada: el registro crea un usuario **sin tenant**, así que no hay
tenant en cuyo contexto evaluar el ajuste. Se documenta para que nadie la
proponga otra vez.

## Decisión propuesta (recomendación)

**Opción A**, con tres condiciones:

1. **El gate mira la tabla, no solo el ajuste**: si `users` está vacía, el
   registro se permite **siempre** (y promociona), independientemente del ajuste.
   Es la vía de arranque de un despliegue nuevo y de una reinstalación.
2. **El 403 es genérico y de tiempo constante** respecto al 409 actual: cerrar el
   registro debe cerrar también el oráculo de enumeración, no moverlo de sitio.
3. **Se arregla `is_system_owner` en el mismo tramo.** `init_tenant` debe fijar
   `is_system_owner=is_first_user` igual que fija `is_system_admin`. Sin eso, la
   Opción A cierra la única puerta que quedaba a un rol que hoy ya no tiene
   ninguna. Es un cambio de una línea + su test; **no es opcional**.

El default en `dev` queda en `true` por comodidad de desarrollo, pero conviene
ser consciente de que eso hace que **el camino que se prueba a diario sea el
permisivo**. El test de contrato debe afirmar el comportamiento en `prod`
explícitamente (con `environment` forzado), no solo el de dev.

## Consecuencias

- **`task_prod09_05`** absorbe el ajuste (ya toca `register` para el rate limit).
  Su alcance crece en ~4 h y su test debe cubrir los tres estados: abierto,
  cerrado, y «tabla vacía» (que gana sobre cerrado).
- **Fuera de prod-09**: el arreglo de `is_system_owner` en `init_tenant` es
  código del instalador/seeds y no pertenece a este plan. Queda anotado como
  dependencia dura de esta decisión.
- **Documentación**: `docs/02-getting-started/` y el runbook de instalación deben
  decir cómo se da de alta un usuario cuando el registro está cerrado. Si no,
  el primer operador que lo necesite lo reabrirá «temporalmente» y se quedará
  abierto.
- **Si se elige B**, la consecuencia honesta es que hay que construir la bandeja
  de solicitudes de acceso, o el auto-registro seguirá siendo una función que
  solo produce filas huérfanas y superficie de enumeración.

## Verificación

1. Con el ajuste cerrado y al menos un usuario en la base: `POST /auth/register`
   → **403**, con el **mismo cuerpo y el mismo código** para un email existente
   y para uno nuevo.
2. Con el ajuste cerrado y la tabla `users` **vacía**: `POST /auth/register` →
   **201**, y el usuario creado es System Admin **y** System Owner.
3. Con el ajuste abierto: comportamiento actual intacto.
4. `environment=prod` sin fila en `platform_settings` → el registro está
   **cerrado** (el default derivado, no un `true` estático).
5. Tras un `init_tenant`, existe exactamente **un** System Owner (hoy: cero).
