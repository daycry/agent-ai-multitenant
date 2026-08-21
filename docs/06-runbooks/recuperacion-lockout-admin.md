---
title: Recuperar el acceso de System Admin tras un lockout
docs_language: es
audience: operador, sysadmin
updated: 2026-08-01
---

# Recuperar el acceso de System Admin tras un lockout

**Cuándo usarlo**: cuando el único System Admin no puede entrar en `/admin/*` y
la causa es el endurecimiento de esa superficie — allowlist de IP mal
configurada, MFA perdida (móvil roto, llave extraviada) o sesión que caduca a
los 15 minutos antes de poder arreglar nada.

Este runbook existe porque el endurecimiento de `/admin/*` (prod-09
`task_prod09_01`) **puede dejar fuera al operador**, y era el riesgo 3 del plan.
La instrucción del propio plan es clara: **este documento se lee ANTES de
configurar la allowlist en producción**, no cuando ya estás fuera. Si estás
leyéndolo con prisa, salta al §3.

Referencia del mecanismo: [`04-reference/sesiones.md` §4](../04-reference/sesiones.md).

---

## 1. Los tres controles que te pueden dejar fuera

Solo se aplican cuando `API_SERVER_ENVIRONMENT` es `staging` o `prod`. En `dev`
la dependencia es un pass-through, así que **un lockout en dev no es un lockout,
es otra cosa** — no sigas este runbook, mira los logs.

| Control         | Síntoma exacto                                 | Código |
| --------------- | ---------------------------------------------- | ------ |
| Allowlist de IP | `source IP not in the admin allowlist`         | 403    |
| Sesión corta    | `admin session expired; re-authenticate`       | 401    |
| MFA obligatoria | `admin access requires an enrolled MFA factor` | 403    |

Se evalúan **en ese orden** (IP → sesión → MFA), así que el mensaje que ves es
el del primero que falla: arreglar la IP puede destapar un problema de MFA que
estaba detrás.

## 2. Diagnóstico en 60 segundos

Desde el host del stack:

```bash
# ¿Qué entorno cree que es?
docker compose exec api-server printenv API_SERVER_ENVIRONMENT

# ¿Qué allowlist tiene cargada?
docker compose exec api-server printenv API_SERVER_ADMIN_IP_ALLOWLIST

# ¿Qué IP ve el api-server de TI? (la que compara con la allowlist)
docker compose logs api-server --tail 200 | grep -i "allowlist"
```

**La trampa nº1 de la allowlist**: la IP que se compara es la que devuelve
`get_client_ip`, que **prefiere `X-Forwarded-For`** (entrada de más a la
izquierda) y solo cae al peer del socket si no viene. Detrás de un reverse
proxy, si el proxy no propaga `X-Forwarded-For` verás la IP interna del
contenedor del proxy, no la tuya. Meter tu IP pública en la allowlist no
arregla nada en ese caso: hay que arreglar el proxy o poner la IP que el
api-server ve de verdad.

## 3. Recuperación

Las tres vías van de menos a más invasiva. **Prueba en orden.**

### Vía A — Ampliar o vaciar la allowlist (la más común)

Una allowlist **vacía significa «sin restricción de red»**, y es la salida de
emergencia diseñada. En el `.env` del stack:

```bash
# Emergencia: sin restricción de red (los otros dos controles siguen activos)
API_SERVER_ADMIN_IP_ALLOWLIST=[]
```

```bash
docker compose up -d api-server        # recrea SOLO el api-server
docker compose exec api-server printenv API_SERVER_ADMIN_IP_ALLOWLIST   # verifica
```

No hace falta reconstruir imagen ni tocar la base de datos. **Vuelve a poner la
allowlist correcta en cuanto recuperes el acceso** y verifica desde la IP buena
antes de cerrar la ventana: dejarla vacía «temporalmente» es como se queda
vacía para siempre.

### Vía B — Rebajar temporalmente MFA o el TTL de sesión

Si el problema es la MFA (móvil perdido) o que 15 minutos no dan para
reconfigurar nada:

```bash
API_SERVER_ADMIN_REQUIRE_MFA=false        # ventana de emergencia, NO estado final
API_SERVER_ADMIN_SESSION_TTL_MINUTES=60   # sube el TTL mientras arreglas
```

`docker compose up -d api-server` y entra. **Lo primero que haces dentro** es
re-enrolar el segundo factor; lo segundo, devolver las dos variables a su valor
(`true` / `15`) y reiniciar. Un stack de producción con `admin_require_mfa=false`
es exactamente el agujero que el control cerraba.

> **No pruebes con `API_SERVER_ENVIRONMENT=dev` para saltarte los tres a la
> vez.** Además de abrir toda la superficie admin, `dev` desarma el guard
> fail-closed de secretos: el proceso pasaría a aceptar los secretos por defecto
> del repo, y quedarías firmando sesiones con un secreto público. Es cambiar un
> lockout por un compromiso.

### Vía C — Intervención en base de datos (último recurso)

Cuando no hay ningún System Admin utilizable (el único se borró, se desactivó o
perdió el factor y la vía B no es aceptable). Es una operación **auditable**:
déjala escrita en el registro de incidencias con quién, cuándo y por qué.

```bash
docker compose exec postgres psql -U postgres -d agentic_platform
```

```sql
-- ¿Quién es admin hoy?
SELECT id, email, is_system_admin, is_system_owner, is_active
FROM users WHERE is_system_admin OR is_system_owner;

-- Reactivar / promocionar a un usuario que YA existe y controlas.
UPDATE users SET is_system_admin = true, is_active = true
WHERE email = 'operador@example.com';

-- Retirar un factor TOTP inservible para poder re-enrolar desde cero.
DELETE FROM user_mfa_totp
WHERE user_id = (SELECT id FROM users WHERE email = 'operador@example.com');

-- Y la llave WebAuthn, si la perdida es esa.
DELETE FROM webauthn_credentials
WHERE user_id = (SELECT id FROM users WHERE email = 'operador@example.com');
```

Después: **mata las sesiones vivas de ese usuario** (el flag `is_system_admin`
se re-verifica contra BD en cada request desde `task_prod09_04`, pero una sesión
abierta con la MFA vieja no debe sobrevivir a una recuperación):

```bash
docker compose exec redis redis-cli --scan --pattern 'session:*'
```

Si no queda **ningún** usuario en la tabla `users`, hay una salida documentada
que no exige SQL: con `users` vacía, `POST /auth/register` está abierto y
promociona al primer usuario a System Admin **y** System Owner (ADR 0134). Es
la puerta de arranque; existe precisamente para que una instalación no pueda
quedar inaccesible para siempre. Ojo: vaciar `users` a mano para usar esa puerta
**no** es un procedimiento de recuperación — arrasa membresías y propiedad.

## 4. Antes de tocar la allowlist en producción (prevención)

Cinco minutos que evitan el runbook entero:

1. **Dos vías de entrada, siempre**: al menos dos System Admin con MFA enrolada,
   o una IP de administración estable **más** un acceso de emergencia por consola
   al host.
2. **Prueba la allowlist con un usuario de prueba primero**, no con el tuyo.
3. **Comprueba qué IP ve el api-server** (§2) antes de escribir el CIDR. Detrás
   de proxy, la respuesta casi nunca es la que crees.
4. **Deja el acceso al host asegurado**: todas las vías de este runbook exigen
   `docker compose` en la máquina. Si el único acceso al host era el panel, el
   lockout es total.
5. **Guarda los códigos de recuperación de MFA** fuera del móvil que los genera.

## 5. Verificación de que has salido del lockout

```bash
# Debe responder 200 con la sesión buena, y 403 sin MFA / desde una IP fuera.
curl -i -b "agentic_session=$TOKEN" https://<host>/admin/platform-settings
```

Y las tres comprobaciones de que el sistema quedó **como debía**, no como lo
dejó la emergencia:

- `API_SERVER_ADMIN_REQUIRE_MFA` vuelve a ser `true`.
- `API_SERVER_ADMIN_SESSION_TTL_MINUTES` vuelve a ser `15`.
- `API_SERVER_ADMIN_IP_ALLOWLIST` vuelve a tener la lista correcta, verificada
  desde la IP buena **antes** de cerrar la ventana.

## Relacionado

- [`04-reference/sesiones.md`](../04-reference/sesiones.md) — el contrato de la
  sesión, los tres controles y todas las variables.
- [`04-reference/rbac.md`](../04-reference/rbac.md) — qué exige cada endpoint.
- [`02-troubleshooting.md`](./02-troubleshooting.md) — fallos generales del stack.
- [`sso-global-auth.md`](./sso-global-auth.md) — cuando el acceso va por IdP.
