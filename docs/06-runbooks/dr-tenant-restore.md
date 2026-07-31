---
title: Restore selectivo por tenant
docs_language: es
audience: operador, system admin
updated: 2026-05-30
---

# Runbook — Restore selectivo por tenant

Recuperación quirúrgica de **un solo tenant** desde un bundle de backup,
sin tocar a ningún otro tenant. Caso de uso: un tenant borró por error
todos sus proyectos y hay que devolverlo al punto del backup elegido,
mientras el resto de tenants siguen operando con sus datos actuales.

Usa el motor de restore selectivo de Fase C
(`workers.restore_per_tenant.run_per_tenant_restore`, `task_12_11`). Si
necesitas restaurar TODO el stack, usa
[dr-full-restore.md](./dr-full-restore.md) en su lugar.

## Propósito

- Revertir la pérdida de datos de un único tenant a un backup conocido.
- Garantizar **cero impacto** sobre los demás tenants durante la
  operación (aislamiento multi-tenant).

## Precondiciones

- Un **bundle de backup** de Fase A accesible en disco (mismo layout que
  en [dr-full-restore.md](./dr-full-restore.md)). El restore selectivo se
  apoya en que el dump de PostgreSQL es **lógico** (formato directorio):
  permite restaurarlo en una base de datos de staging desechable y
  filtrar por `tenant_id`.
- El `tenant_id` (UUID) del tenant afectado.
- **dblink habilitado en PostgreSQL (prerequisito de despliegue —
  PENDIENTE).** El motor copia las filas del tenant del staging a la
  base de datos viva con `dblink(...)` dentro de una sola transacción.
  La extensión `dblink` **NO se crea hoy en ninguna migración ni en el
  init de PostgreSQL** — hay que crearla manualmente antes de la primera
  ejecución, una sola vez:

  ```bash
  docker compose \
    -f docker/docker-compose.yml \
    exec -T postgres \
    psql -U postgres -d agentic_platform \
    -c 'CREATE EXTENSION IF NOT EXISTS dblink;'
  ```

  Sin esta extensión, el copiado filtrado falla. (Gap conocido: ver
  «Notas y limitaciones» al final; debería formalizarse como migración en
  un plan posterior.)

- El stack **arriba y sano** (este restore NO detiene los servicios: solo
  toca las filas del tenant en la base de datos viva y su prefijo en
  MinIO).
- Vault accesible si el bundle está cifrado.

> El motor **verifica el bundle antes de escribir nada** (fail-closed) y
> bordea cada sentencia con `WHERE tenant_id = '<target>'`, por lo que
> ninguna fila de otro tenant entra jamás en el predicado.

## Pasos

### 1. Identifica el bundle y el tenant

```bash
ls -1 "${WORKERS_BACKUP_ROOT:-/data/agent-platform/backups}"
```

Anota el `<backup_id>` a usar y el `<tenant_id>` (UUID) del tenant
afectado.

### 2. (Una sola vez por despliegue) habilita dblink

Ver el comando `CREATE EXTENSION IF NOT EXISTS dblink;` en Precondiciones.
Es idempotente; reejecutarlo es inofensivo.

### 3. Vista previa (dry-run, NO escribe nada)

Calcula la lista de tablas afectadas + recuento de filas que se
restaurarían, sin tocar la base de datos viva. Es lo que la UI de restore
(`task_12_12`) muestra para la segunda confirmación del operador.

```bash
# Desde el HOST: el servicio se llama `workers`, no `worker`, y el motor no
# debe correr dentro de un contenedor que la propia operación puede parar.
python -c '
from workers.restore_per_tenant import run_per_tenant_restore, confirmation_token
preview = run_per_tenant_restore(
    "<backup_id>",
    tenant_id="<tenant_id>",
    confirm=confirmation_token("<tenant_id>", "<backup_id>"),
    dry_run=True,
)
print("preview:", preview)
'
```

Revisa que las tablas y los recuentos cuadran con lo esperado antes de
continuar.

### 4. Ejecuta el restore selectivo

El token de confirmación es `f"{tenant_id}@{backup_id}"`
(`confirmation_token(...)` lo construye). Un token que no coincide rechaza
la operación antes de cualquier trabajo.

```bash
# Desde el HOST: el servicio se llama `workers`, no `worker`, y el motor no
# debe correr dentro de un contenedor que la propia operación puede parar.
python -c '
from workers.restore_per_tenant import run_per_tenant_restore, confirmation_token
res = run_per_tenant_restore(
    "<backup_id>",
    tenant_id="<tenant_id>",
    confirm=confirmation_token("<tenant_id>", "<backup_id>"),
    dry_run=False,
)
print("restore ok:", res)
'
```

El motor: verifica el bundle; restaura el dump completo en una base de
datos de **staging** desechable; en UNA transacción contra la base viva
borra las filas del tenant (orden FK inverso) y las re-inserta desde
staging vía `dblink` (orden FK), siempre con `WHERE tenant_id =
'<target>'`; **elimina** el staging en un `finally`; y re-extrae solo el
prefijo `<tenant_id>/` del object storage. Las filas y objetos de otros
tenants no se tocan.

## Verificación

- El tenant afectado recupera sus datos al momento del backup elegido
  (proyectos, planes, conversaciones).
- Los **demás tenants NO se ven afectados**: comprueba que un usuario de
  otro tenant sigue viendo sus datos actuales sin cambios.
- El audit log refleja la operación con quién la hizo y sobre qué tenant
  (test humano `human_12_03`).
- **Los objetos del tenant se leen por API S3**, no mirando el filesystem
  (test humano `human_prod_04_03`): lista y descarga un objeto del tenant
  con `mc` o el SDK, y abre un documento suyo desde la UI. Que el fichero
  esté en el `_data` NO significa que MinIO lo sirva.
- Ejecuta [health-check.md](./health-check.md) para confirmar que el
  stack sigue sano.

> **MinIO se para durante la extracción** (prod-04 task_prod_04_10). Escribir
> el `_data` de MinIO por debajo mientras corre no está soportado: el formato
> xl guarda metadatos por objeto y el servidor cachea, así que una extracción
> en caliente deja objetos que el filesystem tiene y la API no ve. El motor
> para el servicio `minio` alrededor de la extracción de la rebanada y lo
> vuelve a arrancar SIEMPRE, incluso si la extracción falla — dejarlo caído
> por el restore de un tenant dejaría sin object storage a todos los demás.
> Cuenta con unos segundos de indisponibilidad del object storage.
>
> El vaciado de la rebanada es un **error duro**: antes era best-effort
> (`ignore_errors=True`) y un borrado a medias dejaba al tenant con una mezcla
> de dos momentos distintos que nadie detectaba.

## Rollback / aborto

- **Dry-run (paso 3)**: no escribe nada; abortar es no ejecutar el paso 4.
- **Token incorrecto**: el motor rechaza sin tocar la base viva.
- **Bundle corrupto** (`PerTenantRestoreVerificationError`): aborta antes
  de escribir; usa otro bundle.
- **Fallo a mitad del copiado**: todo el copiado va en UNA transacción con
  `ON_ERROR_STOP=1`; cualquier error hace **ROLLBACK** y la base viva
  vuelve a su estado previo al restore. El staging se elimina igualmente
  en el `finally`. Reintenta cuando hayas corregido la causa (p. ej.
  `dblink` no habilitado).
- **Restauré el tenant equivocado / al punto equivocado**: vuelve a
  ejecutar el restore selectivo de ese tenant con el `backup_id` correcto;
  la operación es idempotente para el tenant (borra + reinserta sus filas).

## A quién avisar

- **System Admin**: aprueba el restore selectivo (acción sensible:
  destructiva para el tenant objetivo).
- **Responsable del tenant** afectado: para confirmar el punto de
  restauración deseado y validar los datos recuperados.
- **DBA / DevOps**: si `dblink` no está habilitado o la transacción de
  copiado falla por integridad referencial.

## Notas y limitaciones conocidas

- **dblink no se crea automáticamente (gap de despliegue).** El restore
  selectivo depende de la extensión `dblink`, que hoy NO se aprovisiona en
  ninguna migración Alembic ni en el init de PostgreSQL. Hay que crearla
  manualmente (paso 2). Recomendación: convertir ese `CREATE EXTENSION` en
  una migración idempotente en un plan posterior para que el prerequisito
  no dependa de un paso manual.
- **Sincronización a destino remoto aún no auto-cableada** (ver
  [dr-manual-backup.md](./dr-manual-backup.md)): si el bundle solo vive en
  remoto, descárgalo antes a `WORKERS_BACKUP_ROOT`.
- **Huérfanos referenciales post-restore (PROJ-03, auditoría 2026-07-17).**
  El copiado filtrado corre con `session_replication_role = replica` (los
  triggers de FK quedan apagados dentro de la transacción): si el bundle y
  la base viva divergen (catálogo builtin distinto, filas que referencian
  otro tenant, restores parciales) pueden quedar filas huérfanas que
  ninguna FK volverá a validar. El motor ejecuta automáticamente un **sweep
  de integridad post-restore** (`workers.maintenance.integrity.
sweep_fk_orphans`) que detecta y borra los huérfanos (transitivamente) y
  deja el informe en el log (`restore_per_tenant.integrity_sweep`) y en el
  resultado (`fk_orphans_deleted`). Si el sweep borra algo, revisa el log:
  indica divergencia entre bundle y base viva. El reconciler además vigila
  (solo WARNING, cada 90s) hijos de tenants inexistentes
  (`maintenance.tenant_integrity`).
