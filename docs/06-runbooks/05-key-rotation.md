---
title: Rotación de claves y credenciales de la plataforma
docs_language: es
audience: system admin, responsable de seguridad, operador
updated: 2026-07-31
---

# Runbook — Rotación de claves y credenciales

Punto de entrada **canónico** para toda rotación de material sensible de la
plataforma: las **unseal keys** de Vault, las **claves de cifrado en reposo**, los
**secretos de firma** (JWT y tokens internos), las **credenciales de servicio**
(MinIO, Postgres) y la **revocación de emergencia**.

> Alcance: **Docker Compose en una sola máquina** (CLAUDE.md). Vault NO corre en
> modo dev en producción; se inicializa con
> [`scripts/init-vault.sh`](../../scripts/init-vault.sh) (Shamir 5-of-5,
> threshold 3).

## Lo que cambió el 2026-07-31 (prod-05) y por qué importa

Antes de prod-05 este runbook describía verificaciones que **el código no podía
cumplir**, y omitía 6 de las 8 familias de claves. Tres correcciones concretas,
porque si te acuerdas de la versión antigua te van a sorprender:

1. **«Los servicios leen el secreto rotado de Vault … sin reinicio» era falso.**
   Toda la configuración se lee una vez por proceso (`@lru_cache` en los tres
   `config.py`) y nadie lee Vault en caliente. Propagar un secreto rotado **es**
   un reinicio, y ahora está escrito como tal ([ADR 0144](../05-architecture-decisions/0144-propagacion-de-secretos-rotados.md)).
2. **El job automático de rotación no rotaba nada.** `_build_vault_client`
   devolvía un fake en memoria y el ciclo auditaba `SUCCEEDED`. Hoy, sin Vault
   vivo, el ciclo termina en **`SKIPPED` con alerta**; un `SUCCEEDED` solo puede
   salir de un cliente `hvac` real. La sección de revocación de emergencia, que
   dirigía a ese job, está corregida.
3. **Rotar una clave de cifrado ya no destruye lo que cifró.** Las cuatro
   familias Fernet, los dos secretos de firma JWT y la clave de backups son ahora
   **anillos ordenados** (`*_KEYS`, coma-separado): la primera clave cifra/firma,
   todas descifran/verifican. La variable singular sigue valiendo como anillo de
   un elemento, así que **ningún despliegue existente cambia de comportamiento**.

---

## El procedimiento de tres pasos (léelo una vez; todo lo demás lo referencia)

Toda rotación de una clave de **cifrado en reposo** o de **firma** sigue la misma
forma. Saltarse el paso 2 es lo que convierte el paso 3 en pérdida de datos.

| Paso | Qué haces                                                        | Estado tras el paso                    |
| ---- | ---------------------------------------------------------------- | -------------------------------------- |
| 1    | Añades la clave NUEVA **en cabeza** de `*_KEYS` y despliegas     | La nueva cifra; la vieja aún descifra  |
| 2    | Ejecutas el re-cifrado (solo cifrado en reposo) o esperas el TTL | Nada depende ya de la clave vieja      |
| 3    | Borras la clave vieja de la lista y despliegas                   | La clave vieja está retirada de verdad |

```bash
# Paso 1 — generar la clave nueva (48 bytes, urlsafe). NUNCA la escribas en un
# fichero versionado; va al .env del despliegue y a tu gestor de secretos.
openssl rand -base64 48 | tr -d '\n=' | tr '+/' '-_'

# Paso 1 — anteponerla, conservando la actual:
#   API_SERVER_SSO_ENCRYPTION_KEYS=<NUEVA>,<ACTUAL>
docker compose -f docker/docker-compose.yml up -d api-server workers

# Paso 2 — ver qué se movería, ANTES de mover nada:
docker compose -f docker/docker-compose.yml exec -T api-server \
  python -m api_server.cli reencrypt-secrets --dry-run

# Paso 2 — hacerlo:
docker compose -f docker/docker-compose.yml exec -T api-server \
  python -m api_server.cli reencrypt-secrets

# Paso 3 — dejar SOLO la nueva en la lista y volver a desplegar:
#   API_SERVER_SSO_ENCRYPTION_KEYS=<NUEVA>
docker compose -f docker/docker-compose.yml up -d api-server workers
```

**La señal para el paso 3 la da el paso 2, no el reloj.** Un segundo
`reencrypt-secrets` (o un `--dry-run`) que diga `moved 0` y
_«Every readable ciphertext is on the head key»_ es la única prueba de que borrar
la clave vieja es seguro. Códigos de salida del comando: `0` correcto, `1`
configuración inválida (nada escrito), **`2` hay filas ILEGIBLES** — mira la lista
de ids que imprime, esos secretos hay que reintroducirlos a mano.

> **Anillos y ventanas de reinicio.** El reinicio del paso 1 y el del paso 3 no
> cortan sesiones ni ejecuciones **porque la clave vieja sigue en el anillo**
> durante el paso 1 y ya no hace falta en el paso 3. Ese es exactamente el motivo
> por el que la aceptación dual existe.

---

## Tabla exhaustiva: las claves de la plataforma

Cada fila enlaza a su procedimiento. Una entrada marcada **SIN CAMINO DE
ROTACIÓN** no se rota: no hay mecanismo, y forzarlo rompe cosas.

| #   | Clave / credencial                                                                                                                                          | Dónde vive                                                                        | Qué protege                                                             | Radio de impacto si se rota mal                                                                         | Camino                                                                                                      |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| 1   | `API_SERVER_JWT_SECRET` / `API_SERVER_JWT_SECRETS`                                                                                                          | env de **api-server**                                                             | Firma las sesiones humanas (HS256)                                      | Todas las sesiones abiertas caen a 401                                                                  | [§JWT](#1-jwt-de-sesión-humana)                                                                             |
| 2   | `API_SERVER_INTERNAL_TOKEN_SECRET` / `API_SERVER_INTERNAL_TOKEN_SECRETS`                                                                                    | env de **api-server Y workers**                                                   | Firma `AGENTIC_INTERNAL_TOKEN` (worker → `/internal/agent/*`)           | Toda ejecución de plan en vuelo empieza a dar 401                                                       | [§Token interno](#2-token-interno-worker--api)                                                              |
| 3   | `API_SERVER_SSO_ENCRYPTION_KEY` / `..._KEYS`                                                                                                                | env de **api-server**                                                             | `sso_configurations.client_secret_encrypted` + clave privada SP de SAML | El login SSO deja de funcionar; los secretos son irrecuperables                                         | [§Cifrado en reposo](#3-6-claves-de-cifrado-en-reposo-fernet)                                               |
| 4   | `API_SERVER_MFA_ENCRYPTION_KEY` / `..._KEYS`                                                                                                                | env de **api-server**                                                             | `user_mfa_totp.secret_encrypted` (seeds TOTP)                           | **Lockout de los System Admin** si `ADMIN_REQUIRE_MFA=true`                                             | [§Cifrado en reposo](#3-6-claves-de-cifrado-en-reposo-fernet) + [§Break-glass](#break-glass-lockout-de-mfa) |
| 5   | `API_SERVER_NOTIFICATION_ENCRYPTION_KEY` / `..._KEYS` **y** `NOTIFY_NOTIFICATION_ENCRYPTION_KEY` / `..._KEYS`                                               | env de **api-server Y notification-dispatcher**                                   | `notification_channels.secret_encrypted`                                | Los envíos fallan en silencio (el dispatcher no descifra)                                               | [§Cifrado en reposo](#3-6-claves-de-cifrado-en-reposo-fernet)                                               |
| 6   | `API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEY` / `..._KEYS`                                                                                                   | env de **api-server**                                                             | `incoming_webhook_configs.signing_secret_encrypted`                     | Cada integración entrante rechaza firmas; el secreto **no se puede recuperar** (se mostró una sola vez) | [§Cifrado en reposo](#3-6-claves-de-cifrado-en-reposo-fernet)                                               |
| 7   | `WORKERS_BACKUP_ENCRYPTION_KEY` / `WORKERS_BACKUP_ENCRYPTION_KEYS`                                                                                          | env de **workers** (leída por `EnvSecretsProvider`, no es un campo de `Settings`) | Los bundles de backup (AES-256-GCM)                                     | **Todos los backups históricos ilegibles** — pérdida definitiva en un DR                                | [§Backups](#7-clave-de-cifrado-de-backups)                                                                  |
| 8   | Credencial de MinIO (`API_SERVER_MINIO_ACCESS_KEY` / `API_SERVER_MINIO_SECRET_KEY`; admin en `WORKERS_CRED_ROTATION_MINIO_ROOT_USER` / `..._ROOT_PASSWORD`) | env de **api-server** + MinIO                                                     | Object storage de toda la plataforma                                    | Si revocas antes de propagar, **se cae el object storage entero**                                       | [§MinIO](#8-credencial-de-minio)                                                                            |
| 9   | `API_SERVER_REVIEW_URL_SIGNING_SECRET`                                                                                                                      | env de **api-server Y workers**                                                   | Firma las URLs de revisión de un solo uso                               | Las URLs ya enviadas dejan de abrir                                                                     | [§Firma de URLs](#9-firma-de-urls-de-revisión)                                                              |
| 10  | `API_SERVER_VAULT_TOKEN` / `WORKERS_VAULT_TOKEN`                                                                                                            | env                                                                               | Autenticación de los servicios contra Vault                             | Se pierde la resolución de credenciales git/MCP                                                         | [§Token de Vault](#10-token-de-servicio-de-vault)                                                           |
| 11  | `API_SERVER_BRAVE_SEARCH_API_KEY`                                                                                                                           | env de **api-server**                                                             | Búsqueda web del córtex                                                 | Sin búsqueda web (degradación, no caída)                                                                | [§Terceros](#11-claves-de-terceros)                                                                         |
| 12  | Password estática de Postgres (`API_SERVER_DATABASE_URL`, `API_SERVER_ADMIN_DATABASE_URL`, `WORKERS_DATABASE_URL`, `NOTIFY_DATABASE_URL`)                   | env de los cuatro servicios                                                       | Acceso a la base de datos                                               | —                                                                                                       | **SIN CAMINO DE ROTACIÓN** — [§Postgres](#12-postgres-sin-camino-de-rotación)                               |
| 13  | Unseal keys de Vault (shares de Shamir)                                                                                                                     | custodios humanos                                                                 | El barrier de Vault                                                     | —                                                                                                       | [§Unseal keys](#unseal-keys-de-vault-rekey)                                                                 |
| 14  | Credenciales **dinámicas** de Postgres (leases de Vault)                                                                                                    | Vault, TTL corto                                                                  | Roles Postgres desechables                                              | Se auto-expiran; nada que rotar a mano                                                                  | [§Dinámicas](#credenciales-dinámicas-de-base-de-datos)                                                      |

---

## 1. JWT de sesión humana

**Qué firma**: las sesiones de `/auth/login` (`encode_jwt`). TTL por defecto 24 h
(`API_SERVER_JWT_EXPIRATION_MINUTES`).

**Procedimiento** — los tres pasos, **sin paso 2 de re-cifrado**: aquí el paso 2
es _esperar_.

1. `API_SERVER_JWT_SECRETS=<NUEVA>,<ACTUAL>` en el `.env` de **api-server**;
   `docker compose up -d api-server`. Desde ese instante se firma con la nueva y
   se verifican ambas.
   - Si la clave nueva la escribió el **job de rotación** (está en
     `secret/platform/jwt` con `pending_apply=true`), este paso es un comando:
     `./scripts/rotate-platform-secret.sh jwt`. Lee el valor del KV, lo
     **antepone** conservando el anillo anterior, reescribe el `.env` y reinicia
     — sin imprimir el secreto y sin dejar copias del `.env` por el camino. El
     paso 3 sigue siendo manual **a propósito**: retirarla es una decisión con
     reloj, no un automatismo.
2. **Espera el TTL máximo de sesión + margen** (24 h + 1 h con el default). No hay
   nada que ejecutar: es el tiempo que tardan en morir las sesiones firmadas con
   la clave vieja.
3. `API_SERVER_JWT_SECRETS=<NUEVA>` y `docker compose up -d api-server`.

**Verificación (que el código cumple)**:

- antes del paso 1, inicia sesión y guarda la cookie/token; tras el paso 1, una
  petición autenticada **sigue devolviendo 200** (la clave vieja está en el
  anillo);
- tras el paso 3, esa MISMA sesión devuelve **401**;
- la guarda de configuración rechaza el arranque si alguna entrada del anillo
  tiene menos de 32 caracteres o contiene un marcador dev (`dev-only`,
  `changeme`), **y también** si el anillo comparte alguna clave con el del token
  interno.

**Rollback**: vuelve a poner la clave anterior en cabeza y reinicia. Mientras no
hayas hecho el paso 3, el rollback es gratis.

## 2. Token interno worker → api

**Qué firma**: el `AGENTIC_INTERNAL_TOKEN` que el worker inyecta en cada
contenedor agent-runtime (`mint_agent_token`, TTL 24 h). Dominio criptográfico
**separado** del anterior por diseño ([ADR 0136](../05-architecture-decisions/0136-dominios-criptograficos-worker-api.md)).

Mismo procedimiento de tres pasos que §1, con dos diferencias que importan:

- la variable va en el `.env` de **api-server Y de workers** (el worker mintea
  importando `api_server.config`, por eso lleva el prefijo `API_SERVER_`), y los
  dos se reinician **en la misma ventana**;
- el token está inyectado en contenedores **que ya están corriendo** y no se puede
  refrescar: si retiras la clave vieja antes de que expire el token más antiguo en
  vuelo, matas ejecuciones de plan a mitad. Espera el TTL completo, o drena las
  ejecuciones primero.

Los dos anillos (§1 y §2) **no pueden compartir ninguna clave**: la configuración
lo rechaza al arrancar. Una clave presente en ambos permitiría a un worker
comprometido forjar sesiones humanas.

## 3-6. Claves de cifrado en reposo (Fernet)

Cubre SSO (#3), MFA (#4), notificaciones (#5) y webhooks entrantes (#6). Las
cuatro siguen los **tres pasos** literales del principio de este runbook, con el
paso 2 = `reencrypt-secrets`.

| Familia            | Variable                                                                              | Tablas que re-cifra                                                         | `--families`       |
| ------------------ | ------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- | ------------------ |
| SSO                | `API_SERVER_SSO_ENCRYPTION_KEYS`                                                      | `sso_configurations.client_secret_encrypted`, `...sp_private_key_encrypted` | `sso`              |
| MFA                | `API_SERVER_MFA_ENCRYPTION_KEYS`                                                      | `user_mfa_totp.secret_encrypted`                                            | `mfa`              |
| Notificaciones     | `API_SERVER_NOTIFICATION_ENCRYPTION_KEYS` **+** `NOTIFY_NOTIFICATION_ENCRYPTION_KEYS` | `notification_channels.secret_encrypted`                                    | `notification`     |
| Webhooks entrantes | `API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEYS`                                         | `incoming_webhook_configs.signing_secret_encrypted`                         | `incoming_webhook` |

Cuatro avisos, uno por familia:

- **Notificaciones: es un PAR.** El api-server escribe el ciphertext y el
  notification-dispatcher lo lee. `API_SERVER_NOTIFICATION_ENCRYPTION_KEYS` y
  `NOTIFY_NOTIFICATION_ENCRYPTION_KEYS` deben tener **el mismo valor** y
  desplegarse **en la misma ventana**. Desplegar solo uno rompe el par, y el
  síntoma es una notificación que no llega — no un error visible.
- **MFA: si nunca has puesto `API_SERVER_MFA_ENCRYPTION_KEY(S)`, MFA usa el
  anillo de SSO** ([ADR 0143](../05-architecture-decisions/0143-clave-de-cifrado-mfa-propia.md)),
  y entonces rotar SSO **también** rota los seeds TOTP: haz el paso 2 sin filtro
  de familia y lee el [break-glass](#break-glass-lockout-de-mfa) antes de empezar.
  Para separar las claves: `API_SERVER_MFA_ENCRYPTION_KEYS=<NUEVA-MFA>,<CLAVE-SSO-ACTUAL>`
  → deploy → `reencrypt-secrets --families mfa` → deja solo `<NUEVA-MFA>` → deploy.
- **Webhooks entrantes: el secreto en claro no se puede recuperar.** Se mostró al
  operador una sola vez y está pegado en GitHub/Jira. Si `reencrypt-secrets`
  reporta una fila ilegible aquí, la única salida es regenerar el secreto y
  volver a pegarlo en el proveedor externo.
- **SSO: la clave privada SP de SAML va en la misma familia** que el client secret
  de OIDC, en filas distintas de la misma tabla.

**Verificación (que el código cumple)**:

```bash
# 0 filas por mover => el paso 3 es seguro
docker compose -f docker/docker-compose.yml exec -T api-server \
  python -m api_server.cli reencrypt-secrets --dry-run
```

y, funcionalmente: login con TOTP correcto, intercambio de código OIDC correcto,
un envío de notificación de prueba entregado, un evento de webhook entrante
verificado.

## 7. Clave de cifrado de backups

**Formato v2 con key-id**: desde prod-05 cada bundle lleva en su cabecera el
identificador de la clave que lo cifró
(`[ AGENTBK1 | 0x02 | key_id(8) | nonce(12) | ct+tag ]`). Los bundles v1 (sin
key-id) **se siguen leyendo para siempre** probando todas las claves del anillo.

> ### REGLA OPERATIVA: conserva toda clave de backup retirada
>
> **Junto a los bundles que cifró, mientras esos bundles existan.** El anillo
> `WORKERS_BACKUP_ENCRYPTION_KEYS` los hace legibles; una clave borrada es un
> bundle perdido, y esto el código no lo puede arreglar. Aplica también al runbook
> de DR ([dr-full-restore.md](./dr-full-restore.md)).

**Procedimiento** (dos pasos; **no hay re-cifrado masivo de bundles** — es
deliberado, ver «Queda fuera» del plan prod-05):

1. `WORKERS_BACKUP_ENCRYPTION_KEYS=<NUEVA>,<ACTUAL>` en el `.env` de **workers**;
   `docker compose up -d workers`. Los bundles nuevos usan la nueva clave; los
   viejos siguen restaurando.
2. **No hay paso 3.** La clave vieja se queda en el anillo tanto tiempo como
   quieras poder restaurar los bundles que cifró — típicamente, la ventana de
   retención completa.

**Verificación (que el código cumple)**:

- crea un backup, rota, crea un segundo backup, y **restaura los dos**;
- quita la clave vieja del anillo y comprueba que el bundle viejo falla con un
  error **explícito** que nombra el key-id que falta y los que sí están
  configurados — no con un `InvalidTag` críptico. Ese mensaje es la diferencia
  entre «saca la clave de la caja fuerte» y «el backup está corrupto».

## 8. Credencial de MinIO

**Patrón add-then-remove**, y el orden es la propiedad de seguridad entera:

1. el ciclo de rotación **crea una service account nueva en MinIO** (a través de
   la API de administración) y **solo después** escribe el valor en
   `secret/platform/minio`, marcando la entrada `pending_apply=true` y anotando el
   access key anterior. Si MinIO no responde, **falla ruidoso y no toca KV**;
2. propagas: copias el nuevo `API_SERVER_MINIO_ACCESS_KEY` /
   `API_SERVER_MINIO_SECRET_KEY` al `.env` y reinicias los servicios afectados
   ([ADR 0144](../05-architecture-decisions/0144-propagacion-de-secretos-rotados.md));
3. **solo entonces** revocas la credencial anterior
   (`revoke_previous_minio_credential`), que borra la service account vieja y pone
   `pending_apply=false`.

**Los pasos 2 y 3, en un comando** (y en ese orden, que es lo que importa):

```bash
./scripts/rotate-platform-secret.sh minio          # --dry-run para verlo antes
```

Escribe las **dos** mitades de la credencial en el `.env`, reinicia api-server y
los tres workers, y **sólo después** llama a la revocación
(`python -m workers.rotation_apply --revoke-previous-minio`, síncrono: si falla,
el operador se entera antes de dar la ventana por cerrada). Si prefieres hacerlo
a mano, el orden de arriba no es negociable — invertir 2 y 3 borra la credencial
que los servicios siguen usando.

> **En el stack de dev/manuales el script no tiene nada que reescribir**: ese
> compose lleva los valores incrustados en línea en vez de leerlos del `.env`. El
> script es para un despliegue cuyo compose referencia `${VARS}`.

Entre 1 y 3 **ambas credenciales funcionan**, así que no hay ventana de corte.
Invertir 2 y 3 deja sin object storage a toda la plataforma.

Requisitos: `WORKERS_CRED_ROTATION_MINIO_ROOT_USER` y
`WORKERS_CRED_ROTATION_MINIO_ROOT_PASSWORD` configurados. Sin ellos, el paso MinIO
del ciclo **falla** en vez de escribir un KV que nombra una credencial que MinIO
nunca emitió. La credencial admin en sí NO la rota este procedimiento: solo se usa
para crear y borrar service accounts hijas.

## 9. Firma de URLs de revisión

`API_SERVER_REVIEW_URL_SIGNING_SECRET` va en **api-server y workers** (el worker
firma, el api-server verifica). **No tiene anillo**: es un valor único, así que
rotarlo invalida de golpe las URLs de revisión ya enviadas y no abiertas.

Procedimiento: cambia el valor en los dos `.env`, reinicia los dos servicios en la
misma ventana, y avisa a quien tenga revisiones pendientes de que su enlace ya no
sirve. Verificación: una URL nueva abre; una anterior a la rotación da 403.

## 10. Token de servicio de Vault

`API_SERVER_VAULT_TOKEN`, `WORKERS_VAULT_TOKEN`, `ORCHESTRATOR_VAULT_TOKEN` y
`NOTIFY_VAULT_TOKEN` — uno por servicio, contra la política homónima que escribe
el bootstrap del instalador. El root token **nunca** se usa como token de
servicio ([`scripts/init-vault.sh`](../../scripts/init-vault.sh)).

**Procedimiento** (un comando):

```bash
VAULT_TOKEN=<root-o-admin> ./scripts/vault-mint-service-tokens.sh >> docker/.env
docker compose -f docker/docker-compose.yml up -d   # + tus overlays
```

Acuña un token **periódico** (`-period 72h`, cambiable con `VAULT_PERIOD`) y
**huérfano** por política, y los emite como líneas `.env` por stdout. Sin
`--write` no toca el disco.

- **Periódico** porque no caduca mientras se renueve dentro de su período, y eso
  lo hace solo `VaultTokenManager` en segundo plano: en el api-server
  (`api_server.vault_client`) y en los workers (`workers.vault_client`). Un token
  de TTL fijo habría que re-acuñarlo por calendario — la carga operativa que
  produjo la bomba de relojería de 32 días que este mecanismo desactiva.
- **Huérfano** porque revocar el root token **no puede** llevarse la plataforma
  por delante. Ese orden es la razón de que el paso 2 vaya antes del 3 en
  [dr-vault-unseal-rotation.md](./dr-vault-unseal-rotation.md) §«Incidente
  abierto».

**Verificación (que el código cumple)**: en los logs del api-server, al arrancar,
un evento `vault.token.lookup` con el TTL y las políticas del token; y el gauge
`agentic_vault_token_ttl_seconds` decreciendo entre renovaciones (evento
`vault.token.renewed`). Si aparece `vault.token.renew_failed` a nivel **error**,
el token dejará de valer al final de su período: es lo que hay que mirar.

**Rollback**: el token anterior sigue vivo hasta que lo revoques
(`vault token revoke`), así que volver atrás es reponer el valor viejo en el
`.env` y reiniciar.

## 11. Claves de terceros

`API_SERVER_BRAVE_SEARCH_API_KEY`: se rota en el panel del proveedor, se pega en
el `.env` y se reinicia api-server. **Degradación, no caída**: sin ella el córtex
pierde la búsqueda web y el resto sigue.

## 12. Postgres — SIN CAMINO DE ROTACIÓN

**No rotes la password estática de Postgres siguiendo este runbook.** Vive
embebida en cuatro DSN de cuatro servicios (`API_SERVER_DATABASE_URL`,
`API_SERVER_ADMIN_DATABASE_URL`, `WORKERS_DATABASE_URL`, `NOTIFY_DATABASE_URL`),
no tiene anillo, y cambiarla exige un `ALTER ROLE` coordinado con el reinicio
simultáneo de los cuatro servicios: cualquier desincronización deja servicios sin
base de datos. El camino correcto es **consumir credenciales dinámicas** (los
leases que Vault ya sabe emitir), y eso es un plan propio, no un procedimiento de
operación.

Si la password se ha comprometido y no hay alternativa: ventana de mantenimiento
con **todos** los servicios parados, `ALTER ROLE ... PASSWORD`, los cuatro `.env`
actualizados, y arranque. Trátalo como una intervención, no como una rotación.

---

## Unseal keys de Vault (rekey)

Rotar las shares de Shamir que desellan Vault, invalidando el conjunto antiguo,
sin pérdida de datos. El **procedimiento detallado** —backup previo, `vault
operator rekey -init`, aportar el umbral de claves actuales, distribuir y
custodiar las nuevas shares, rotación opcional de la clave maestra del barrier con
`vault operator rotate`, y el rollback/aborto— está en
**[dr-vault-unseal-rotation.md](./dr-vault-unseal-rotation.md)**.

Resumen del flujo:

1. **Backup de Vault** del volumen `vault_data` antes de tocar nada
   ([dr-manual-backup.md](./dr-manual-backup.md)).
2. `vault operator rekey -init -key-shares=5 -key-threshold=3` → anota el
   **Nonce**. Vault sigue operativo con las claves antiguas mientras el rekey está
   en curso.
3. Cada custodio aporta su unseal key **actual** referenciando el Nonce hasta
   alcanzar el threshold. Al completarse, Vault **emite las nuevas shares** una
   sola vez: cópialas de inmediato.
4. Distribuye cada nueva share a su custodio en ubicaciones separadas y
   **destruye** las antiguas (`shred -u`).
5. (Opcional) `vault operator rotate` para rotar la clave de cifrado del barrier.

> El rekey es atómico: hasta que NO se aporta el umbral de claves nuevas-nonce,
> puede **cancelarse** (`vault operator rekey -cancel`) sin efecto.

**Desellar tras un restore**: si llegas desde un DR
([04-disaster-recovery.md](./04-disaster-recovery.md)) con `vault_data`
restaurado, Vault arranca **sellado**; deséllalo con el umbral de unseal keys
vigentes en el momento del backup.

## Credenciales dinámicas de base de datos

El **motor de secretos de base de datos** de Vault emite un rol Postgres
desechable por lease. Un servicio sostiene una credencial solo durante el TTL del
rol (`WORKERS_CRED_ROTATION_DB_TTL_S`, por defecto **1 h**); al expirar, Vault
**revoca** el rol. Una credencial filtrada **auto-expira**.

- El rol (`WORKERS_CRED_ROTATION_DB_ROLE`, por defecto `platform-app`) se
  configura de forma **idempotente** en cada ciclo; sus `creation_statements`
  conceden exactamente los privilegios de `app_user` (least privilege).
- Usuario/contraseña minteados viven solo dentro de `DynamicDbCredential`, cuyo
  `__repr__`/`__str__` están **redactados**. Solo se registran `lease_id` y
  duración.

> **Estado real**: el motor emite los leases, pero **api-server y workers todavía
> NO los consumen** — siguen usando la password estática de sus DSN (§12). El
> cableado es un plan propio.

## El job automático de rotación

`workers.rotate_credentials`, en beat, cadencia `WORKERS_CRED_ROTATION_CRON`
(por defecto domingo 02:00 UTC), cola `privileged`.

Cada ciclo: rota los secretos estáticos de `WORKERS_CRED_ROTATION_STATIC_SECRETS`
(por defecto `minio` + `jwt`) escribiendo en KV v2 con `pending_apply=true`, emite
una credencial dinámica de BD nueva, y renueva-luego-revoca el lease anterior.

**Tres estados, y ninguno miente**:

| Resultado   | Cuándo                                                                           | Qué hacer                                              |
| ----------- | -------------------------------------------------------------------------------- | ------------------------------------------------------ |
| `succeeded` | Vault vivo, todos los pasos completados                                          | Propagar (§8 paso 2) y revocar lo anterior (§8 paso 3) |
| `skipped`   | **No hay Vault configurado**: `WORKERS_VAULT_URL` / `WORKERS_VAULT_TOKEN` vacíos | Cablear Vault. **No se rotó nada.** Llega alerta       |
| `failed`    | Vault o MinIO respondieron mal                                                   | Las credenciales actuales siguen vivas. Investigar     |

Un `succeeded` **solo** puede salir de un cliente `hvac` real: el fake en memoria
ya no es alcanzable desde el código de producción, y hay un test de regresión que
lo pinea.

**Palancas**:

| Knob                                    | Dónde                                    | Por defecto       | Efecto                                                         |
| --------------------------------------- | ---------------------------------------- | ----------------- | -------------------------------------------------------------- |
| `cred_rotation_enabled`                 | **platform setting** (panel admin)       | `true`            | OFF en caliente; la siguiente ejecución es no-op sin reinicio  |
| `WORKERS_CRED_ROTATION_CRON`            | env del proceso **beat** (leído al boot) | `0 2 * * 0`       | Cadencia. Cambiarla exige reiniciar beat                       |
| `WORKERS_CRED_ROTATION_STATIC_SECRETS`  | env de workers                           | `["minio","jwt"]` | Qué secretos estáticos rota el ciclo                           |
| `WORKERS_CRED_ROTATION_MINIO_ROOT_USER` | env de workers                           | (vacío)           | Sin él, el paso MinIO **falla** en vez de escribir KV a ciegas |

**Verificación de un ciclo**:

- el resumen es secret-free: nombres de secreto, versiones KV e ids de lease,
  **nunca** un valor;
- `status: succeeded` significa **rotado en Vault**, no «en vigor». Lo segundo
  llega con la propagación (§8 paso 2), y el marcador `pending_apply` en la
  entrada KV es lo que distingue una cosa de la otra;
- una rotación fallida genera alerta `credential_rotation_failed` en los canales
  del System Admin, y **las credenciales previas siguen funcionando**.

---

## Break-glass: lockout de MFA

Si tras rotar la clave de cifrado los seeds TOTP no descifran, **ningún System
Admin puede entrar en `/admin/*`** con `API_SERVER_ADMIN_REQUIRE_MFA=true` (el
default fuera de dev). Salida, en este orden:

1. `API_SERVER_ADMIN_REQUIRE_MFA=false` en el `.env` de api-server y
   `docker compose up -d api-server`. **Temporal y anotado**: mientras esté
   así, `/admin/*` va con un solo factor.
2. Entra, arregla la causa: normalmente devolver la clave anterior a la cola de
   `API_SERVER_MFA_ENCRYPTION_KEYS` y ejecutar
   `reencrypt-secrets --families mfa`.
3. Si los seeds son irrecuperables (la clave se perdió, no se retiró): borra las
   filas de `user_mfa_totp` afectadas y pide **re-enrolamiento** a los usuarios.
   Los códigos de recuperación no ayudan aquí — su digest está en la misma fila.
4. **Vuelve a poner `API_SERVER_ADMIN_REQUIRE_MFA=true`** y reinicia. Este paso se
   olvida; ponlo en el checklist de la incidencia.

Prevención: mantén al menos un System Admin con TOTP enrolado **y** verifica el
`--dry-run` antes de cada rotación que toque MFA.

## Revocación de emergencia

Cuando una credencial o un lease se ha comprometido y hay que cortarlo **ya**.

### Revocar un lease dinámico concreto

```bash
docker compose -f docker/docker-compose.yml exec -T vault \
  vault lease revoke <lease_id>
```

Vault elimina el rol Postgres desechable al instante.

### Revocar TODOS los leases de un prefijo

```bash
docker compose -f docker/docker-compose.yml exec -T vault \
  vault lease revoke -prefix database/creds/platform-app
```

### Rotar YA un secreto comprometido

> **Corrección (prod-05).** Este runbook decía «ejecuta el job una vez». Con el
> job devolviendo `SUCCEEDED` contra un fake, ese consejo era **una revocación que
> no revocaba nada**. Hoy el job es real, pero **sigue sin ser el camino de
> emergencia**: rota en cadencia, no bajo demanda, y su resultado necesita la
> propagación manual de §8 para surtir efecto.

El camino de emergencia es **el procedimiento manual de la clave concreta**, con
los tres pasos comprimidos en una sola ventana:

| Secreto comprometido       | Acción inmediata                                                                           |
| -------------------------- | ------------------------------------------------------------------------------------------ |
| Clave de sesión JWT        | §1 pasos 1 **y 3 seguidos** (acepta el corte: todas las sesiones caen — es lo que quieres) |
| Token interno worker→api   | §2 pasos 1 y 3 seguidos; las ejecuciones en vuelo morirán, relánzalas                      |
| Credencial MinIO           | §8, pero revoca la anterior **inmediatamente después** de propagar, sin esperar            |
| Clave de cifrado en reposo | §3-6 completo, con `reencrypt-secrets` entre medias. **No te saltes el paso 2**            |
| Clave de backups           | §7 paso 1. **NO borres la clave vieja**: los bundles que cifró la siguen necesitando       |
| Token de servicio de Vault | revoca el token en Vault (`vault token revoke`), emite otro, §10                           |

Tras cualquiera de ellas, **rota también las unseal keys** si sospechas que el
compromiso alcanzó al material de desellado.

### Si se compromete una unseal key

Una unseal key comprometida **no** da acceso por sí sola (hace falta 3 de 5), pero
acerca a un atacante a desellar Vault. Ejecuta el rekey
([§Unseal keys](#unseal-keys-de-vault-rekey)) y revisa el audit log de Vault.

---

## A quién avisar

- **Responsable de seguridad**: lidera el rekey de unseal keys, coordina a los
  custodios y conduce cualquier revocación de emergencia.
- **System Admin**: programa la ventana, posee `cred_rotation_enabled`, ejecuta
  `reencrypt-secrets` y verifica la salud del stack. Recibe las alertas
  `credential_rotation_failed`.
- **Custodios de las shares**: reciben y guardan una nueva share, destruyen la
  antigua.
- **DBA / DevOps**: si una revocación de leases deja servicios sin credenciales.

## Notas

- Vault NO expande variables de entorno en su config; el root token solo emite
  tokens de servicio por política
  ([`scripts/init-vault.sh`](../../scripts/init-vault.sh)).
- Si Vault queda atascado en `Restarting`, revisa
  [`vault-dev-mode-port-conflict.md`](../03-guides/gotchas/vault-dev-mode-port-conflict.md)
  y [`vault-entrypoint-config-flag.md`](../03-guides/gotchas/vault-entrypoint-config-flag.md).

## Enlaces

- Detalle del rekey de unseal keys: [dr-vault-unseal-rotation.md](./dr-vault-unseal-rotation.md).
- Backup de Vault antes de rotar: [dr-manual-backup.md](./dr-manual-backup.md).
- DR / desellar tras un restore: [04-disaster-recovery.md](./04-disaster-recovery.md).
- Restauración completa (y la regla de conservar claves de backup): [dr-full-restore.md](./dr-full-restore.md).
- Salud del stack tras una rotación: [health-check.md](./health-check.md).
- ADR 0143 — clave de cifrado MFA propia: [0143](../05-architecture-decisions/0143-clave-de-cifrado-mfa-propia.md).
- ADR 0144 — propagación de secretos rotados: [0144](../05-architecture-decisions/0144-propagacion-de-secretos-rotados.md).
