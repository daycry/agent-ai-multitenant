---
title: Rotación de unseal keys y credenciales
docs_language: es
audience: system admin, responsable de seguridad, operador
updated: 2026-05-31
---

# Runbook — Rotación de unseal keys y credenciales

Punto de entrada **canónico** para toda rotación de material sensible de la
plataforma:

- las **unseal keys** de Vault (las shares de Shamir que desellan el almacén),
- las **credenciales de servicio** (la clave MinIO, la clave de firma JWT, …),
- las **credenciales dinámicas de base de datos** (roles Postgres de vida corta
  que Vault emite y revoca por lease),

y la **revocación de emergencia** cuando algo se compromete. Este runbook
**orquesta** la decisión y referencia el detalle paso a paso; no lo duplica. El
procedimiento bajo nivel del rekey de Vault vive en el runbook de Fase 12
([dr-vault-unseal-rotation.md](./dr-vault-unseal-rotation.md)) y se enlaza desde
aquí.

> Alcance: **Docker Compose en una sola máquina** (CLAUDE.md). Vault NO corre en
> modo dev en producción; se inicializa con
> [`scripts/init-vault.sh`](../../scripts/init-vault.sh) (Shamir 5-of-5,
> threshold 3) y custodia tanto los secretos estáticos como la conexión del
> motor de base de datos dinámica.

## Qué camino elegir

| Situación                                                                 | Camino                                                                                                  |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Una unseal key se ha comprometido / rota un custodio / rotación periódica | **Rekey de Vault** → §[Rotación de unseal keys](#rotación-de-unseal-keys-rekey-de-vault)                |
| Toca refrescar la clave MinIO / la clave de firma JWT                     | **Rotación de secretos estáticos** → la lleva el [job automático](#rotación-automática-de-credenciales) |
| Una credencial de servicio se ha filtrado / un atacante la conoce         | **Revocación de emergencia** → §[Revocación de emergencia](#revocación-de-emergencia)                   |
| Un lease de BD dinámica hay que cortarlo ya                               | **Revocación de emergencia** (`vault lease revoke`)                                                     |
| Configurar la cadencia / apagar el job de rotación                        | §[Cadencia y configuración](#cadencia-y-configuración)                                                  |

La diferencia conceptual clave:

- Las **unseal keys** desellan el barrier de Vault tras un arranque. NO son una
  credencial de servicio: ningún servicio las usa en caliente; las custodian
  personas. Se rotan con `vault operator rekey` (rara vez, de forma coordinada).
- Las **credenciales de servicio** (estáticas y dinámicas) las consumen los
  contenedores en cada request. Se rotan **automáticamente** y a menudo, con un
  job de Vault dynamic secrets.

---

## Rotación de unseal keys (rekey de Vault)

Rotar las shares de Shamir que desellan Vault, invalidando el conjunto antiguo,
sin pérdida de datos. El **procedimiento detallado** —backup previo, `vault
operator rekey -init`, aportar el umbral de claves actuales, distribuir y
custodiar las nuevas shares, rotación opcional de la clave maestra del barrier
con `vault operator rotate`, y el rollback/aborto— está en
**[dr-vault-unseal-rotation.md](./dr-vault-unseal-rotation.md)** (Fase 12). No se
reproduce aquí.

Resumen del flujo (el detalle, comandos y rollback están en ese runbook):

1. **Backup de Vault** del volumen `vault_data` antes de tocar nada (ver
   [dr-manual-backup.md](./dr-manual-backup.md)).
2. `vault operator rekey -init -key-shares=5 -key-threshold=3` → anota el
   **Nonce**. Vault sigue operativo con las claves antiguas mientras el rekey
   está en curso.
3. Cada custodio aporta su unseal key **actual** referenciando el Nonce hasta
   alcanzar el threshold. Al completarse, Vault **emite las nuevas shares** una
   sola vez: cópialas de inmediato.
4. Distribuye cada nueva share a su custodio en ubicaciones separadas y
   **destruye** las antiguas (`shred -u`).
5. (Opcional) `vault operator rotate` para rotar también la clave de cifrado del
   barrier.

> El rekey es atómico: hasta que NO se aporta el umbral de claves nuevas-nonce,
> el rekey puede **cancelarse** (`vault operator rekey -cancel`) sin efecto, y
> las unseal keys antiguas siguen valiendo.

**Desellar tras un restore**: si llegas aquí desde un DR
([04-disaster-recovery.md](./04-disaster-recovery.md)) con `vault_data`
restaurado, Vault arranca **sellado**; deséllalo con el umbral de unseal keys
vigentes en el momento del backup. Detalle en
[dr-vault-unseal-rotation.md](./dr-vault-unseal-rotation.md#desellar-tras-un-restore).

---

## Rotación automática de credenciales

La plataforma rota las **credenciales de servicio** automáticamente con Vault
dynamic secrets (Plan 15, `task_15_17`). Hay dos mitades complementarias; el
motor (`apps/workers/src/workers/credential_rotation.py`) implementa ambas y un
job de Celery beat (`workers.rotate_credentials`) las ejecuta en cada ciclo.

### 1. Credenciales dinámicas de base de datos (TTL corto)

El **motor de secretos de base de datos** de Vault emite un rol Postgres
desechable por lease. Un servicio sostiene una credencial solo durante el TTL
del rol (`cred_rotation_db_ttl_s`, por defecto **1 h**); al expirar el lease,
Vault **revoca** el rol automáticamente. Así, una credencial filtrada
**auto-expira** en vez de vivir para siempre.

- El rol (`cred_rotation_db_role`, por defecto `platform-app`) se configura de
  forma **idempotente** en cada ciclo (`configure_db_secrets_engine_role`); sus
  `creation_statements` conceden exactamente los privilegios del grupo de la app
  (`app_user`) — least privilege, igual que el `app_user` ligado a RLS.
- Un servicio lee `<mount>/creds/<role>` para obtener una credencial fresca; el
  motor monta en `cred_rotation_db_mount` (por defecto `database`).
- El usuario/contraseña minteados viven solo dentro de `DynamicDbCredential`,
  cuyo `__repr__`/`__str__` están **redactados**: nunca aparecen en un log ni en
  un traceback. Solo se registran el `lease_id` y su duración (nombran el lease,
  no son el secreto).

### 2. Ciclo periódico de rotación (secretos estáticos + leases)

Cada ciclo (`rotate_credentials`):

1. **Rota los secretos estáticos en sitio** (`cred_rotation_static_secrets`, por
   defecto **`minio` + `jwt`**): Vault genera un valor de alta entropía nuevo en
   cada ruta KV v2 y **sube la versión** (KV v2 retiene la versión anterior para
   rollback). Solo se registran el **nombre** del secreto y la nueva versión; el
   valor nunca se lee ni se loguea.
2. **Emite una credencial dinámica de BD nueva** desde el rol configurado.
3. **Renueva y luego revoca el lease anterior** (`previous_lease_id`): el lease
   viejo se renueva un instante (los servicios a media request siguen
   funcionando) y acto seguido se revoca, **acotando la ventana** en que dos
   credenciales válidas coexisten.

El ciclo es **best-effort: nunca lanza excepción**. Ante cualquier fallo, la
plataforma **sigue arriba con sus credenciales actuales**: el error se captura,
se escribe una entrada de auditoría `RotationAudit` con `status=failed`, y —si
hay notificador conectado— se levanta una alerta `credential_rotation_failed`
por el carril de prioridad del dispatcher de notificaciones (Plan 10), para que
un operador investigue. Los secretos **nunca** se loguean en claro: los logs
estructurados y la auditoría llevan nombres de secreto, ids de lease y conteos,
nunca un valor.

> Comprobación previa: la rotación automática asume Vault **inicializado y
> desellado**, con el motor de base de datos habilitado y la conexión
> `cred_rotation_db_connection` (por defecto `platform-postgres`) configurada. La
> ligadura real de Vault (un adaptador `hvac.Client`) se cablea en instalación y
> se ejercita en los Tests Humanos del plan; en un entorno sin Vault vivo el job
> degrada a un ciclo no-op seguro en lugar de caerse.

### Cadencia y configuración

| Knob                                   | Dónde                                                 | Por defecto                 | Qué controla                                                                   |
| -------------------------------------- | ----------------------------------------------------- | --------------------------- | ------------------------------------------------------------------------------ |
| `cred_rotation_enabled`                | **platform setting** (panel admin, solo System Admin) | `true`                      | Interruptor en caliente ON/OFF; el job lee el flag al inicio de cada ejecución |
| `WORKERS_CRED_ROTATION_CRON`           | env del **proceso beat** (leído al arrancar)          | `0 2 * * 0` (dom 02:00 UTC) | Cadencia del job de rotación                                                   |
| `WORKERS_CRED_ROTATION_DB_TTL_S`       | env de workers                                        | `3600` (1 h)                | TTL de un lease de credencial dinámica                                         |
| `WORKERS_CRED_ROTATION_DB_MAX_TTL_S`   | env de workers                                        | `86400` (24 h)              | TTL máximo al que se puede renovar un lease antes de forzar emisión fresca     |
| `WORKERS_CRED_ROTATION_DB_ROLE`        | env de workers                                        | `platform-app`              | Nombre del rol del motor de BD que mintea credenciales dinámicas               |
| `WORKERS_CRED_ROTATION_STATIC_SECRETS` | env de workers                                        | `["minio", "jwt"]`          | Secretos estáticos que el ciclo rota en sitio                                  |

Distinción importante:

- La **cadencia (cron)** la lee el **proceso beat al arrancar**: cambiarla exige
  reiniciar Celery beat. Está en la cola `privileged` (la drena un worker con el
  perfil de seguridad más estricto, porque toca secretos/Vault).
- El **flag `cred_rotation_enabled`** lo lee el job **en caliente** al principio
  de cada ejecución: ponerlo en OFF convierte la siguiente ejecución en un no-op
  (sin escrituras en Vault, sin churn de leases) **sin reiniciar** beat. Es la
  palanca que usa un System Admin para pausar la rotación durante una incidencia.
  Solo un System Admin puede escribir un platform setting; un tenant **no** puede
  disparar ni programar la rotación (es global de plataforma).

Para apagar la rotación temporalmente desde el panel admin (o vía API de
platform settings), pon `cred_rotation_enabled = false`; vuélvelo a `true` para
reanudar en la siguiente cadencia.

### Verificación de un ciclo

- El job devuelve un resumen secret-free con `ok: true` y la auditoría (nombres
  de secreto rotados, nueva versión KV, ids de lease emitido/renovado/revocado).
- No debe aparecer **ningún valor de credencial** en logs ni en la auditoría —
  solo nombres, versiones e ids de lease (test humano `human_15_03`: «credenciales
  rotadas no quedan en logs»).
- Los servicios siguen autenticando: tras un ciclo, la API y los workers leen el
  secreto rotado de Vault y operan con normalidad (sin reinicio).
- Una rotación fallida genera una alerta `credential_rotation_failed` en los
  canales del System Admin, y las credenciales **previas siguen funcionando**.

---

## Revocación de emergencia

Cuando una credencial de servicio o un lease se ha comprometido y hay que
cortarlo **ya**, sin esperar a la cadencia del job.

### Revocar un lease dinámico concreto

Si conoces el `lease_id` (aparece en la auditoría / los logs del ciclo de
rotación, nunca con la credencial), revócalo de inmediato:

```bash
docker compose -f docker/docker-compose.yml \
  exec -T vault \
  vault lease revoke <lease_id>
```

Vault elimina el rol Postgres desechable al instante; cualquier conexión que use
esa credencial deja de autenticar.

### Revocar TODOS los leases de un prefijo (ej. todo el rol de BD)

```bash
docker compose -f docker/docker-compose.yml \
  exec -T vault \
  vault lease revoke -prefix database/creds/platform-app
```

Tras esto, fuerza un ciclo de rotación (o espera al siguiente) para que los
servicios obtengan credenciales frescas.

### Rotar YA un secreto estático comprometido

No esperes a la cadencia: fuerza una rotación inmediata. Lo más simple es
**ejecutar el job una vez** (lo dispara un System Admin desde la cola
`privileged` con `cred_rotation_enabled = true`), que rota los secretos estáticos
configurados. Si solo se comprometió uno, además bumpea su versión KV v2 a mano
con un token de servicio con política de escritura sobre esa ruta — la versión
anterior queda retenida en KV v2 para rollback. Tras rotar, **rota también las
unseal keys** si sospechas que el compromiso alcanzó al material de desellado
(§[Rotación de unseal keys](#rotación-de-unseal-keys-rekey-de-vault)).

### Si se compromete una unseal key

Una unseal key comprometida **no** da acceso por sí sola (hace falta el threshold
= 3 de 5), pero acerca a un atacante a desellar Vault. Trátalo como rotación
urgente: ejecuta el rekey
(§[Rotación de unseal keys](#rotación-de-unseal-keys-rekey-de-vault)) para
invalidar el conjunto actual, y revisa el audit log de Vault por accesos
anómalos.

---

## A quién avisar

- **Responsable de seguridad**: lidera el rekey de unseal keys, coordina a los
  custodios de las shares, y conduce cualquier revocación de emergencia.
- **System Admin**: programa la ventana de mantenimiento, posee el platform
  setting `cred_rotation_enabled`, y verifica la salud del stack tras una
  rotación. Recibe las alertas `credential_rotation_failed`.
- **Custodios de las shares**: cada uno recibe y guarda una nueva share, y
  destruye la antigua.
- **DBA / DevOps**: si una revocación de leases de BD deja servicios sin
  credenciales y hay que forzar la re-emisión.

## Notas

- Vault NO expande variables de entorno en su config; el root token solo emite
  tokens de servicio por política, nunca se usa en configs de servicio (ver
  [`scripts/init-vault.sh`](../../scripts/init-vault.sh)).
- Si Vault queda atascado en `Restarting`, revisa
  [`docs/03-guides/gotchas/vault-dev-mode-port-conflict.md`](../03-guides/gotchas/vault-dev-mode-port-conflict.md)
  y
  [`vault-entrypoint-config-flag.md`](../03-guides/gotchas/vault-entrypoint-config-flag.md).

## Enlaces

- Detalle del rekey de unseal keys: [dr-vault-unseal-rotation.md](./dr-vault-unseal-rotation.md).
- Backup de Vault antes de rotar: [dr-manual-backup.md](./dr-manual-backup.md).
- DR / desellar tras un restore: [04-disaster-recovery.md](./04-disaster-recovery.md).
- Salud del stack tras una rotación: [health-check.md](./health-check.md).
- Hardening del panel admin (quién puede tocar platform settings):
  [internal-pentest-methodology.md](./internal-pentest-methodology.md).
  </content>
  </invoke>
