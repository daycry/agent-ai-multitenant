---
title: "ADR 0149: Consistencia del bundle de backup — quiesce, snapshot de FS o skew aceptado"
status: proposed
date: 2026-07-31
deciders: [dirección, operador]
relates_to: [0129, 0145, 0146]
plan_referenced: prod-04-backup-dr-restaurable
task: [task_prod_04_06, task_prod_04_07]
docs_language: es
---

# ADR 0149: Consistencia del bundle de backup

> **Nace `proposed` y tiene que seguir así hasta que un humano elija.** Las tres
> opciones tienen coste de disponibilidad o coste de requisitos del host, y
> ninguna de las dos cosas la puede decidir quien escribe el código. Lo que sí
> era implementable sin esa decisión ya está en el árbol y se describe abajo
> (§ «Lo que ya no depende de esta decisión»).

## Contexto: el bundle es internamente inconsistente, y eso no es un detalle

El backup nocturno ensambla un bundle con el stack **vivo** (hallazgo gap3-3 de
la auditoría de producción 2026-06-10). Cada artefacto retrata un instante
distinto:

| Artefacto                         | Qué captura                 | Coherencia interna                        |
| --------------------------------- | --------------------------- | ----------------------------------------- |
| `pg_dump` (dirección `directory`) | PostgreSQL entero           | **Sí** — snapshot MVCC del instante t₀    |
| `redis_tar`                       | AOF + RDB de Redis          | Sí, del instante t₁ (tras `BGREWRITEAOF`) |
| `bind_tar` de MinIO               | Objetos de la KB            | No — se escribe durante la captura        |
| `bind_tar` de Vault               | File backend (secretos)     | Verificada (huella antes/después), t₂     |
| `projects_tar`                    | Bare repos de los proyectos | No — un agente puede comitear durante t₃  |

t₀ < t₁ < t₂ < t₃, y entre el primero y el último puede haber minutos. Las
consecuencias no son teóricas y son de dos clases distintas:

1. **Skew referencial entre almacenes.** Una fila de `documents` escrita en t₀+ε
   apunta a un `source_storage_key` que MinIO recibió después de su captura: el
   documento existe en la BD y su binario no está en el bundle. Simétricamente,
   un blob huérfano. Lo mismo entre `llm_providers.secret_vault_path` y el Vault
   capturado, y entre un plan activo y la rama `plan/*` de su bare repo.
2. **Incoherencia INTERNA de un artefacto.** Un `tar` de un árbol que se está
   escribiendo puede producir un fichero a medias. En MinIO eso es un objeto
   ilegible; en el file backend de Vault puede ser un barrel de claves
   inconsistente — y ese caso no da ninguna señal hasta que alguien intenta
   desellar el Vault restaurado, en pleno DR.

La clase (1) la detecta `restore_reconcile` (task_prod_04_13) DESPUÉS de
restaurar. La clase (2) es la que estas opciones atacan.

## Lo que ya no depende de esta decisión (implementado)

Tres cosas eran estrictamente mejores que el statu quo con cualquiera de las tres
opciones, así que no esperan a nadie:

1. **Redis con `BGREWRITEAOF` + artefacto propio.** Antes entraba de rebote en el
   tar del bind del data-root: un `appendonlydir` en escritura activa, acumulado
   durante días, copiado mientras el servidor le escribía. Ahora se le pide a
   Redis un AOF fresco, se espera a que el rewrite termine **y se comprueba
   `aof_last_bgrewrite_status`** (un rewrite puede terminar habiendo fallado), y
   se tarea el directorio como artefacto `redis_tar` verificado y **restaurado**.

   > **La letra del plan era incorrecta, y se midió.** El plan pedía «`BGSAVE` y
   > capturar solo el `dump.rdb` resultante». Contra `redis:7-alpine`, el
   > 2026-07-31: con `--appendonly yes` (como lo arranca el compose), un Redis que
   > encuentra un `dump.rdb` y ningún `appendonlydir` **no lee el RDB** — registra
   > «Creating AOF base file … on server start» y sirve `DBSIZE 0`. El bundle
   > habría pasado toda verificación y el restore habría perdido las sesiones, el
   > broker de Celery y los contadores de rate limit **en silencio**. Con el
   > `appendonlydir` capturado, el mismo banco restaura `DBSIZE 4` incluyendo las
   > escrituras posteriores al rewrite («DB loaded from base file … / from incr
   > file …»), sin tocar la configuración.

2. **Captura verificada del árbol de Vault.** Huella (ruta, tamaño, SHA-256 del
   contenido) antes y después del `tar`; si el árbol se movió, se reintenta, y si
   no converge el run **falla** en vez de guardar una copia rota sin decirlo. La
   huella lee el contenido a propósito: la primera versión comparaba
   `(tamaño, mtime)` y eso no ve una reescritura del mismo tamaño dentro de la
   misma marca de reloj — Vault reescribe ficheros de tamaño constante, así que
   el caso es justo el suyo, y detectar «a veces» es peor que no detectar.

3. **Los transitorios fuera de los tars.** `worktrees/` y `dep-cache/` se
   excluyen: eran la fuente principal del «file changed as we read it» de `tar`
   (rc≠0 → el clean-failure borraba el bundle entero) y son regenerables.

Con esto, el skew residual queda **acotado y descrito**, que es la condición
previa para que la decisión de abajo sea informada. Está documentado en
[`docs/06-runbooks/04-disaster-recovery.md`](../06-runbooks/04-disaster-recovery.md).

## Decisión pendiente: las tres opciones

### Opción A — Quiesce corto de escritores en la ventana del backup

Parar los servicios de aplicación (`api-server`, `orchestrator`, `workers`,
`notification-dispatcher`, `admin-panel`) durante la captura, dejando en pie
PostgreSQL / MinIO / Redis / Vault, que son los que se leen.

- **Coste**: 1-3 min de indisponibilidad diaria a las 03:00. Las ejecuciones de
  agentes en vuelo se interrumpen (el reaper las recupera, pero un run largo
  pierde su iteración). Los webhooks entrantes de esa ventana se pierden salvo
  reintento del emisor.
- **Gana**: elimina la clase (2) por completo y reduce la clase (1) a lo que
  escriba la propia infraestructura.
- **Requisitos del host**: ninguno.
- **Recomendación del plan**: ésta, por simplicidad en single-host.

### Opción B — Snapshot de filesystem (LVM / ZFS)

Tomar un snapshot atómico del volumen que contiene el data-root, capturar del
snapshot y liberarlo.

- **Coste**: cero indisponibilidad. Complejidad operativa alta y **acoplamiento
  al host**: exige que `{data_root}` viva en un LV con espacio libre en el VG (o
  un dataset ZFS). Hoy no lo exige nada del instalador, así que adoptarla
  convierte un requisito de despliegue nuevo en bloqueante — y el runbook de
  instalación tendría que verificarlo, o el backup «funcionaría» sin snapshot y
  volveríamos al punto de partida sin saberlo.
- **Gana**: coherencia total (todos los artefactos del MISMO instante), incluida
  la clase (1) entre almacenes de filesystem. El `pg_dump` seguiría siendo un
  instante distinto salvo que se capture PGDATA del snapshot, lo que cambia el
  formato del backup (físico en vez de lógico) y **rompería el restore por
  tenant**, que exige un dump lógico.

### Opción C — Aceptar y documentar el skew residual

No parar nada. El skew queda descrito y la red de seguridad es
`restore_reconcile`, que enumera las divergencias BD↔MinIO↔Vault↔git y devuelve
código ≠ 0 antes de dar el restore por bueno.

- **Coste**: cero disponibilidad, cero requisitos. Un DR puede necesitar
  intervención manual sobre las divergencias que el reconciliador reporte (p. ej.
  marcar como `failed` un documento cuyo binario no viajó).
- **Gana**: nada nuevo; formaliza lo que ya hay.
- **Exige**: reforzar `restore_reconcile` hasta que sus criterios cubran las
  cuatro parejas con umbrales acordados, y que el acta del drill registre las
  divergencias como resultado esperado y no como incidencia.

## Decisión ligada: ¿Redis es crítico?

Independiente de A/B/C y también para un humano. Redis aloja **sesiones de
servidor, el broker de Celery y los contadores de rate limit**. Declararlo _no
crítico por recreable_ es defendible: tras un DR los usuarios vuelven a
autenticarse y las tareas se re-encolan desde la BD, que es la fuente de verdad
del DAG.

- Si se declara recreable: `WORKERS_BACKUP_REDIS_DIR=""` y desaparece el
  artefacto, el `BGREWRITEAOF` y su coste. **Pero hay que comprobar antes** que
  ninguna cola de Celery contiene trabajo que no sea reconstruible desde la BD —
  si un mensaje encolado es la única constancia de un trabajo, perderlo es
  perder el trabajo, no una sesión.
- Si se declara crítico: se queda como está (capturado y restaurado).

## Decisión ligada: ¿el Vault viaja DENTRO del blob cifrado?

Anotada aquí porque task_prod_04_07 lo pide. Hoy la clave AES que cifra el
bundle vive en `WORKERS_BACKUP_ENCRYPTION_KEY` —el entorno de la misma máquina
que se respalda— y el Vault viaja dentro del blob. Ante pérdida total del host, y
sin la clave en custodia offsite, el backup es **matemáticamente irrecuperable**:
las unseal keys no descifran AES-GCM, solo abren un Vault que está dentro del
blob que no se puede abrir. El fingerprint de custodia (ya implementado) verifica
que la clave activa es la declarada como depositada, pero no puede probar que el
sobre la contenga.

Dos opciones estructurales, para el mismo humano:

- **C1 — excluir `vault_data` del blob cifrado** y respaldarlo aparte con su
  propio control de acceso. Rompe la circularidad de raíz.
- **C2 — cifrar el Vault con una clave DISTINTA**, custodiada por otra persona
  (separación de deberes).

## Consecuencias de no decidir

El estado actual es la Opción C **de hecho pero no de derecho**: nadie ha
aceptado el skew por escrito, así que el drill de DR (`human_prod_04_01`) no
tiene criterio para juzgar si las divergencias que aparezcan son un fallo o el
comportamiento acordado. Eso es lo que bloquea el cierre de `task_prod_04_06`, no
la implementación.

## Cómo se implementa cada opción, si se elige

- **A**: `restore.py` ya sabe parar y arrancar servicios de compose; el backup no.
  Añadir al `backup_task` un stop/start de `backup_quiesce_services` alrededor de
  la captura, con `try/finally` incondicional para el arranque (aquí sí: dejar el
  stack parado por un fallo del backup sería peor que el skew) y una métrica de la
  duración del quiesce. Estimación: 1 día.
- **B**: fuera del alcance de este plan. Exige un prerequisito de host verificado
  por el instalador y un cambio de formato del backup de PostgreSQL que rompe el
  restore por tenant. Pide su propio plan.
- **C**: cerrar `restore_reconcile` con umbrales acordados y añadir al acta del
  drill una sección de divergencias esperadas. Estimación: 0,5 días.
