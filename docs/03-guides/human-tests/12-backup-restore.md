# Plan 12 — tests humanos

Esta guía cubre los **4 tests humanos** del Plan 12 (Backup, Restore y
Continuidad). Validan lo que no se puede automatizar sin destruir el
entorno: que el **backup automático diario** corre sin intervención y se
sincroniza al destino remoto, que un **restore completo en máquina
virgen** recupera todo el stack, que el **restore selectivo por tenant**
no afecta a los demás, y que las **alertas del host** llegan y disparan
el modo degradado al llenarse el disco.

> **Estado del plan**: `pending_human_validation`. Las 17 tareas y sus
> tests automáticos están en verde (script de backup full con pg_dump +
> tar de volúmenes + verificación, cifrado opcional con clave de Vault,
> verificación post-backup, cron configurable desde panel, destinos S3 /
> B2 / SFTP / rclone + UI con test de conectividad, restore completo +
> selectivo por tenant + UI con doble confirmación, node-exporter +
> cAdvisor, Alertmanager con reglas de host, dashboards Grafana, runbooks
> de DR). Estos 4 tests humanos son el último paso antes de pasar a
> `completed`.

## TL;DR

No hay `setup_demo_12.py` ni launcher dedicado para este plan: los tests
operan sobre el stack real (un cron a las 03:00, un restore destructivo,
un disco que se llena). El setup es manual y **destructivo** en
`human_12_02` — hazlo en una máquina/VM virgen, nunca sobre datos que te
importen:

```powershell
.\scripts\dev\up.ps1     # stack completo: postgres + redis + minio + vault + prometheus + grafana + alertmanager
```

Las tres pantallas del admin-panel (System Admin):

```
http://localhost:3000/admin/backup                 # estado del backup + cron/ventana horaria configurable
http://localhost:3000/admin/backup/destinations     # destinos remotos (S3/B2/SFTP/rclone) + test de conectividad
http://localhost:3000/admin/backup/restore          # lista de backups + preview + doble confirmación + log
```

Los scripts de backup/restore viven en el repo (`scripts/backup.sh` /
`scripts/install.sh` por convención del proyecto, ejecutados dentro del
contenedor); los runbooks de DR están en `docs/06-runbooks/`.

## Pre-requisitos

| Requisito                                          | Por qué                                                                |
| -------------------------------------------------- | ---------------------------------------------------------------------- |
| Stack dev arriba (`up.ps1`)                        | postgres + minio + vault + redis + prometheus + grafana + alertmanager |
| Un usuario `system_admin`                          | Backup, restore y alertas del host son operaciones de System Admin     |
| Un destino remoto configurado (S3/B2/SFTP/rclone)  | `human_12_01` valida la sincronización al remoto                       |
| Una máquina/VM virgen para restaurar               | `human_12_02` hace un restore completo desde cero (destructivo)        |
| Al menos dos tenants con datos                     | `human_12_03` restaura un solo tenant sin tocar los demás              |
| Canal de notificación del System Admin configurado | `human_12_01`/`04` esperan notificación al admin (Plan 10)             |
| Espacio en disco que puedas llenar a propósito     | `human_12_04` simula disco lleno con `dd`                              |

---

## `human_12_01` — Backup automático funciona sin intervención

**Qué prueba**: a las 03:00 el job de backup dispara solo, el backup
completo aparece en disco, se sincroniza al destino remoto si hay uno
configurado, y queda registro en el audit + notificación al admin si
falla.

**Precondiciones**:

- El cron de backup activo (ventana 03:00 por defecto, configurable en
  `/admin/backup`).
- Un destino remoto configurado y con test de conectividad OK en
  `/admin/backup/destinations`.
- Login como `system_admin`.

**Pasos**:

1. En `/admin/backup`, confirma la **ventana horaria** del cron (03:00 por
   defecto). Para no esperar 24 h puedes adelantar la ventana o lanzar el
   job manualmente — pero el test canónico es **esperar al disparo
   automático**.
2. Tras el disparo, comprueba que el **backup completo** aparece en disco
   (`/data/agent-platform/.../backups/` o la ruta de tu instalación).
3. Verifica que se ha **sincronizado al destino remoto** configurado
   (revisa el bucket/carpeta remota).
4. Abre el **audit log** del backup: debe registrar la corrida.
5. (Caso de fallo) Fuerza un fallo de backup (p.ej. destino remoto
   inalcanzable): debe llegar una **notificación al admin** (vía Plan 10).

**Resultado esperado**: el job dispara a su hora, el backup completo
aparece en disco y se sincroniza al remoto, con registro en audit y
notificación al admin si falla.

**Checklist**:

- [ ] A las 03:00 el job dispara.
- [ ] Backup completo aparece en /data/.../backups/.
- [ ] Si hay destino remoto configurado, se sincroniza.
- [ ] Log de backup en audit + notificación al admin si fallo.

**Pitfalls conocidos**:

- El backup es **pg_dump lógico** (no pgBaseBackup binario): permite
  restore selectivo por tenant pero tarda más en bases grandes. No te
  alarmes si el job dura.
- La **verificación post-backup** (pg_restore --list, tar -tf) corre
  automáticamente: si el backup aparece pero marcado como inválido, es la
  verificación detectando corrupción — eso es señal buena del guardado,
  mira el log.
- La notificación de fallo depende del **canal del System Admin**
  configurado en Plan 10; sin canal, el fallo se registra pero no se
  notifica.

---

## `human_12_02` — Restore completo en máquina virgen

**Qué prueba**: en una máquina nueva, restaurar un backup completo
recupera el stack con todos los tenants y sus datos, los usuarios pueden
hacer login con sus credenciales previas, proyectos/planes/conversaciones
aparecen intactos, y los volúmenes (MinIO, Vault, Redis) restauran
correctamente.

**Precondiciones**:

- Un **backup completo** válido (verificado) disponible.
- Una **máquina/VM virgen** con el stack desplegable (esto es
  **destructivo**: no lo hagas sobre tu entorno con datos).
- Login como `system_admin` en la máquina de destino.

**Pasos**:

1. En la máquina virgen, despliega el stack base y lleva el backup
   completo a la ruta esperada.
2. Lanza el **restore completo** (`/admin/backup/restore` o el runbook de
   DR en `docs/06-runbooks/`): detiene el stack, restaura y reinicia
   limpio.
3. Tras el restore, comprueba que **todos los tenants** y sus datos
   están.
4. Haz **login** con credenciales de usuarios **previas al backup** →
   deben funcionar.
5. Verifica que **proyectos, planes y conversaciones** aparecen
   **intactos**.
6. Confirma que los **volúmenes** restauraron: ficheros en **MinIO**,
   secretos en **Vault**, estado en **Redis (RDB)**.

**Resultado esperado**: el stack se restaura completo, los usuarios
loguean con sus credenciales previas, los datos están intactos y los
volúmenes restauran correctamente.

**Checklist**:

- [ ] El stack se restaura con todos los tenants y sus datos.
- [ ] Los usuarios pueden hacer login con sus credenciales previas.
- [ ] Los proyectos, planes, conversaciones aparecen intactos.
- [ ] Los volúmenes (MinIO, Vault, Redis) restauran correctamente.

**Pitfalls conocidos**:

- **Vault** necesita unseal tras el restore: el restore trae los datos
  cifrados, pero hay que **unseal** con las unseal keys (ver runbook de
  rotación de unseal keys). Sin unseal, los secretos no se leen.
- Si el login falla tras el restore, comprueba que el **secreto de firma
  de JWT** restauró con Vault (un secreto distinto invalida los tokens,
  pero el login con password debería seguir funcionando — re-login).
- El restore completo **detiene el stack**: hazlo en ventana controlada,
  nunca en caliente sobre producción.

---

## `human_12_03` — Restore selectivo por tenant

**Qué prueba**: cuando un tenant borra accidentalmente todos sus
proyectos, la UI ofrece restaurar solo ese tenant; los demás tenants no
se ven afectados durante el restore, el tenant afectado recupera sus
datos al momento del backup elegido, y el audit log refleja quién lo hizo.

**Precondiciones**:

- Al menos **dos tenants** con datos, y un backup completo previo a la
  pérdida.
- Un escenario de pérdida: un tenant (A) "borra" sus proyectos.
- Login como `system_admin`.

**Pasos**:

1. Provoca la pérdida: con el tenant **A**, borra sus proyectos (o simula
   la pérdida de sus datos).
2. En `/admin/backup/restore`, elige el **restore selectivo del tenant
   A**: la UI debe ofrecer esa opción (solo sus tablas + sus volúmenes
   parciales).
3. Selecciona el **backup** del que restaurar (al momento previo a la
   pérdida) y confirma con la **doble confirmación**.
4. Durante y tras el restore, comprueba que el tenant **B (y los demás)
   NO se ven afectados** — sus datos siguen intactos.
5. Confirma que el tenant **A recupera sus datos** al momento del backup
   elegido.
6. Abre el **audit log**: la operación de restore selectivo debe
   reflejar **quién la hizo**.

**Resultado esperado**: la UI ofrece restore selectivo, los demás
tenants no se ven afectados, el tenant afectado recupera sus datos del
backup elegido, y el audit log registra al autor.

**Checklist**:

- [ ] La UI ofrece restore selectivo del tenant afectado.
- [ ] Otros tenants NO se ven afectados durante el restore.
- [ ] El tenant afectado recupera sus datos al momento del backup
      elegido.
- [ ] Audit log refleja la operación con quién la hizo.

**Pitfalls conocidos**:

- El restore selectivo por tenant es posible **gracias al pg_dump
  lógico** (decisión clave del plan): un backup binario no permitiría
  recuperar solo un tenant.
- Verifica que el restore selectivo respeta **RLS / `tenant_id`**: nunca
  debe escribir filas de otro tenant. Si ves datos cruzados, repórtalo.
- Los **volúmenes parciales** (ficheros MinIO del tenant) se restauran
  por prefijo de tenant: si el tenant tiene objetos sin prefijo
  correcto, podrían no recuperarse — comprueba el log del restore.

---

## `human_12_04` — Alertas del host funcionan

**Qué prueba**: simular un disco lleno hace que una alerta llegue al
canal del System Admin en menos de 5 min, el sistema entra en modo
degradado (pausa workers no críticos, evita escribir más backups), y tras
liberar espacio llega una alerta de recuperación.

**Precondiciones**:

- node-exporter + cAdvisor + Prometheus + Alertmanager arriba (parte del
  stack).
- Una regla de alerta de disco >80% activa (Alertmanager).
- Un canal de notificación del System Admin configurado (Plan 10).
- Espacio en disco que puedas llenar a propósito.

**Pasos**:

1. **Simula el disco lleno**: llena el disco con `dd` (p.ej.
   `dd if=/dev/zero of=/data/fill.tmp bs=1M count=...`) hasta superar el
   **80 %** de ocupación.
2. Espera: la alerta de **disco >80%** debe llegar al **canal del System
   Admin en < 5 min**.
3. Comprueba el **modo degradado**: el sistema **pausa workers no
   críticos** y **evita escribir más backups** (para no agravar el disco).
4. **Libera espacio** (`rm /data/fill.tmp`).
5. Tras bajar del umbral, debe llegar una **alerta de recuperación**.

**Resultado esperado**: la alerta llega al canal del System Admin en
menos de 5 min, el sistema entra en modo degradado, y tras liberar
espacio llega la alerta de recuperación.

**Checklist**:

- [ ] Alerta llega al canal del System Admin en menos de 5 min.
- [ ] El sistema entra en modo degradado: pausa workers no críticos,
      evita escribir más backups.
- [ ] Tras liberar espacio, alerta de recuperación.

**Pitfalls conocidos**:

- Ten cuidado con `dd`: llenar el disco del **host** puede afectar a
  otros servicios. Hazlo en una VM o en una partición/volumen aislado, y
  apunta a un path que puedas borrar después.
- La alerta tiene una **ventana de evaluación** (la regla de RAM es ">90%
  sostenida"): para disco el umbral es 80%, pero Alertmanager agrupa y
  espera el `for:` de la regla — los "< 5 min" cuentan con ese retardo.
- La notificación llega por el **canal del System Admin** (Plan 10): si
  no hay canal configurado, la alerta se ve en Alertmanager/Grafana pero
  no llega al chat/email.
- Confirma que `node-exporter` y `cAdvisor` están **scrapeados por
  Prometheus** (`node_load1` responde, task_12_13); sin métricas, ninguna
  regla dispara.

---

## Cierre del plan

Tras pasar los 4 tests humanos:

1. Edita `docs/roadmap/12-backup-restore.md`:
   ```yaml
   status: completed
   completed_at: 2026-MM-DD
   ```
2. Verifica la entrada en
   [`docs/07-changelog/12-backup-restore.md`](../../07-changelog/) y los
   runbooks de DR en [`docs/06-runbooks/`](../../06-runbooks/).
3. Verifica que el PR `plan/12-backup-restore` está mergeado a `master`.

## Troubleshooting

| Síntoma                                 | Causa probable                                                        | Fix                                                                       |
| --------------------------------------- | --------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| El job de backup no dispara a su hora   | La ventana del cron no está bien configurada o el servicio cron caído | Revisa `/admin/backup`; comprueba el contenedor del scheduler             |
| El backup no sincroniza al remoto       | Test de conectividad del destino falla                                | `/admin/backup/destinations` → re-test; revisa credenciales del destino   |
| Login falla tras el restore completo    | Vault sin unseal o secreto de firma JWT no restaurado                 | Unseal Vault con las unseal keys (runbook); re-login con password         |
| El restore selectivo toca otros tenants | (No debería) fallo de aislamiento — repórtalo                         | El pg_dump lógico restaura solo las tablas del tenant; revisa RLS         |
| La alerta de disco no llega             | Sin canal de System Admin (Plan 10) o métricas no scrapeadas          | Configura el canal; verifica `node_load1` en Prometheus                   |
| `dd` afectó a otros servicios           | Llenaste el disco del host, no un volumen aislado                     | Borra el fichero de relleno; usa una VM/partición dedicada la próxima vez |

Errores transversales viven en `docs/03-guides/gotchas/`.
