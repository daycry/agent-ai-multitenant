---
title: "ADR 0156: Aislamiento del córtex — el eje es el owner, y lleva RLS"
status: accepted
date: 2026-08-19
deciders: [operador, arquitectura]
relates_to: [0074, 0075, 0077, 0078, 0080, 0137]
plan_referenced: remediacion-auditoria-integral-2026-07-14
task: [task_audit14_02, task_audit14_03]
docs_language: es
---

# ADR 0156: Aislamiento del córtex — el eje es el owner, y lleva RLS

> **Estado: `accepted`.** Se ratifica la **opción B** de la decisión 2 del plan
> [remediacion-auditoria-integral-2026-07-14](../roadmap/remediacion-auditoria-integral-2026-07-14.md):
> el córtex se aísla por **`owner_user_id`**, y ese eje se defiende con **RLS
> real** (`ENABLE` + `FORCE` + policy `owner_user_id = app.user_id`) en las
> **seis** tablas del subsistema, no solo en una. Se descartan la opción A (policy
> por tenant), la opción C (excepción explícita al Principio 1, o sea el statu quo)
> y la sub-opción de B que proponía renombrar o eliminar el `tenant_id` «falso» de
> `cortex_conversations`. El porqué de cada descarte está escrito abajo, que es lo
> que hace que un ADR sirva dentro de seis meses.
>
> **Alcance ejecutado:** migración [`0140_cortex_owner_rls`](../../apps/api-server/migrations/versions/20260819_0140_cortex_owner_rls.py)
> sobre las cinco tablas que faltaban; la sexta la protegió la `0125` el 2026-07-30.

> **Quién decidió, dicho con precisión.** Esta decisión la tomó Claude Code el
> 2026-08-19 al amparo de la orden permanente del operador —«analiza los ADR
> pendientes e impleméntalos eligiendo la mejor opción»—, no en una conversación
> donde el operador viese estas opciones. Nace `accepted` porque esa orden
> autoriza a decidir y un ADR `proposed` que nadie va a leer no protege a nadie;
> pero **queda pendiente de ratificación**, y si el operador prefiere otra de las
> opciones descartadas, cambiarla es reabrir este ADR, no un descuido de nadie.

## Contexto

El [Córtex del Owner](../roadmap/cortex-system-owner.md) (ADR 0074, fases F1-F5)
es el único subsistema de la plataforma cuyo dato **no pertenece a un tenant**
sino a una persona: el System Owner. El ADR 0074 lo declaró «tablas tenant-less
sobre BYPASSRLS» y de ahí se dedujo, fase a fase, que esas tablas **no llevan
RLS**; el aislamiento sería «un filtro `owner_user_id` explícito en TODO SQL
(defensa en profundidad, **sin RLS de respaldo**)», frase literal de la migración
`0092` y de cuatro docstrings de modelo.

La [auditoría integral del 2026-07-14](../roadmap/auditoria-integral-2026-07-14.md)
marcó ese contrato como incoherente y pidió cerrarlo con un ADR. Mientras tanto,
el meta-invariante `tests/integration/test_rls_invariant.py` se ejecutó por primera
vez (2026-07-30) y midió una consecuencia concreta: `cortex_conversations` tenía
columna `tenant_id` y **cero** protección —ni `relrowsecurity`, ni `FORCE`, ni una
policy—, así que un `app_user` veía los hilos de todos los owners y podía insertar
uno a nombre ajeno. La migración `0125` lo cerró con una policy por owner… y dejó
escrito en su propio docstring que **las otras cinco tablas seguían igual**, porque
no tienen `tenant_id` y el invariante ni las mira.

Este ADR resuelve el contrato entero, no el trozo que el invariante alcanzaba a ver.

### La inferencia que falla

> «El córtex no es un recurso de tenant ⇒ el córtex no lleva RLS.»

De «el eje de autorización no es el tenant» no se sigue «no hay eje que defender»,
sino «el eje es otro y hay que defenderlo». Es exactamente el modo de fallo que
[verificar-antes-de-implementar §3](../03-guides/verificar-antes-de-implementar.md)
describe: una premisa «si no hay X entonces Y» que se rompe justo donde **X falta
por diseño**.

### Por qué esto no es teórico

`docker/postgres/init/02-roles.sh:52` concede, vía `ALTER DEFAULT PRIVILEGES`,
`SELECT/INSERT/UPDATE/DELETE` a `app_user` sobre **toda** tabla que cree Alembic —
incluidas las seis del córtex. `app_user` es `NOBYPASSRLS` y es el rol con el que
la api-server sirve el tráfico normal. Es decir: sin policy, lo único que separa la
mente privada del System Owner (identidad, estado afectivo, lo que está
investigando y el texto literal de sus conversaciones) de cualquier sesión de
tenant es que **cada query recuerde escribir su filtro**. Hoy todas lo escriben;
mañana basta un endpoint nuevo que abra la sesión normal —o una inyección que
llegue a ella— para que la base de datos no tenga nada que oponer.

## Inventario real (medido, no supuesto)

### 1. Las tablas del córtex

| Tabla                       | Migración que la crea | Eje                                  | Estado ANTES de este ADR                   |
| --------------------------- | --------------------- | ------------------------------------ | ------------------------------------------ |
| `cortex_conversations`      | `0092`                | `owner_user_id` (+ `tenant_id` dato) | `ENABLE`+`FORCE`+policy owner desde `0125` |
| `cortex_turns`              | `0092`                | `owner_user_id`                      | **sin RLS**                                |
| `cortex_affect_snapshots`   | `0093`                | `owner_user_id`                      | **sin RLS**                                |
| `cortex_identity`           | `0094`                | `owner_user_id`                      | **sin RLS**                                |
| `cortex_identity_history`   | `0094`                | `owner_user_id`                      | **sin RLS**                                |
| `cortex_curiosity_pursuits` | `0095` (+`0123`)      | `owner_user_id`                      | **sin RLS**                                |

Modelos: `db/cortex.py:60` y `:108`, `db/cortex_affect.py:44`,
`db/cortex_identity.py:44` y `:88`, `db/cortex_curiosity.py:60`.

Dos tablas **del córtex pero fuera de esta decisión**, y conviene decir por qué:

- **`browse_sessions`** (`0112`) — la navegación Playwright del córtex (ADR 0080).
  Tiene `owner_user_id` **y** `tenant_id` nullable, y ya lleva `ENABLE` + `FORCE` +
  policy por tenant. Sus filas del córtex nacen con `tenant_id IS NULL`, así que
  para `app_user` la policy las hace invisibles: **ya es fail-closed**. Añadirle
  una policy por owner sería _añadir_ permisividad, porque las policies permisivas
  se combinan con `OR`. No se toca.
- **`memory_entries`** — la memoria del owner NO es una tabla del córtex: es la
  tabla de memoria de la plataforma, `tenant_id NOT NULL` y RLS por tenant. Esta es
  la razón de ser del `tenant_id` de `cortex_conversations` (ver más abajo).

### 2. Quién abre sesión contra ellas, y con qué rol

| Camino                                                                                                      | Sesión                                       | Rol                                                                             |
| ----------------------------------------------------------------------------------------------------------- | -------------------------------------------- | ------------------------------------------------------------------------------- |
| `routers/cortex.py` (12 aperturas), `routers/cortex_voice.py`                                               | `get_admin_sessionmaker()`                   | `migrations_user` (BYPASSRLS)                                                   |
| `cortex/threads.py`, `affect_store.py`, `identity.py`, `self_context.py`, `voice_turn.py`                   | ninguna: reciben la del llamante             | —                                                                               |
| workers `cortex_affect`, `cortex_curiosity`, `cortex_initiative`, `cortex_reflection`, `cortex_maintenance` | `WORKERS_DATABASE_URL`                       | `service_user` (BYPASSRLS, sin DDL); `migrations_user` en el compose desplegado |
| `pg_dump` del backup                                                                                        | `WORKERS_BACKUP_DATABASE_URL`                | `migrations_user` (BYPASSRLS)                                                   |
| **tráfico normal de la api-server**                                                                         | `get_sessionmaker()` / `open_tenant_session` | `app_user` (**NOBYPASSRLS**)                                                    |

Hoy la última fila **no** toca ninguna tabla del córtex. Ese es justo el punto: la
policy es inerte para los consumidores actuales y solo actúa sobre el camino que
hoy no se usa y estaba completamente abierto.

### 3. Los filtros de aplicación siguen ahí (y siguen siendo la primera línea)

Verificados uno a uno, no extrapolados: `cortex/threads.py:81,140,171,203`;
`affect_store.py:119`; `identity.py:143,245`; `self_context.py:393,448,519`;
`workers/cortex_affect.py:346`; `workers/cortex_curiosity.py:530,586`;
`workers/cortex_initiative.py:133`; `workers/cortex_maintenance.py:239,473`.
Ninguna query de estas tablas se emite sin `owner_user_id == …`.

Esto importa para no malinterpretar la decisión: **la RLS no sustituye al filtro**,
lo respalda. Con los roles de hoy (BYPASSRLS) el filtro es la ÚNICA defensa que
actúa de verdad; quitarlo «porque ya hay RLS» sería un cambio de seguridad neto a
peor.

## Opciones

### A — Tenant-scoped con RLS real (`tenant_id = app.tenant_id`)

Darle a las seis tablas la policy canónica de tenant, añadiendo `tenant_id` a las
cinco que no lo tienen.

**Descartada**, por dos defectos independientes:

1. **Es más permisiva de lo que parece.** Afirma que _pertenecer al tenant A basta
   para leer la mente privada del System Owner_. El `tenant_admin` de A no debería
   ver ni su identidad, ni su estado afectivo, ni sus conversaciones.
2. **Deja al owner a oscuras.** `open_tenant_session` (`auth/deps.py:423-431`) fija
   `app.tenant_id` al tenant **elegido en la request**, no al de la conversación.
   Un System Owner entrando con contexto del tenant B —o sin contexto— dejaría de
   ver su propio historial. Un chat que se queda mudo en silencio es peor que la
   desviación que se venía a arreglar.

Y un tercer motivo que la hace directamente inviable en cinco de las seis: **no
tienen `tenant_id` ni tiene sentido dárselo**. Habría que inventar una columna
cuyo único uso sería sostener una policy equivocada.

### B — Owner-scoped con policy estructural propia ← **ELEGIDA**

`ENABLE` + `FORCE` + `CREATE POLICY <tabla>_owner_only FOR ALL USING (owner_user_id
= NULLIF(current_setting('app.user_id', true), '')::uuid) WITH CHECK (…)` en las
seis tablas. Es el patrón que ya usa `sessions` desde la migración `0001`
(`session_owner_only`) y `cortex_conversations` desde la `0125`.

Es **estrictamente más restrictiva** que la opción A (el admin del tenant tampoco
entra) y **no puede dejar al owner sin historial**, porque `open_tenant_session`
fija `app.user_id` SIEMPRE, en sus dos variantes de sesión. El `NULLIF(…, '')` hace
que una sesión que no fije el GUC compare contra `NULL` y vea **cero** filas
(fail-closed) en lugar de reventar con un error de cast.

#### B-bis — …y además renombrar o eliminar el `tenant_id` «falso»

El plan planteaba, dentro de la opción B, «renombrado/eliminación del falso
`tenant_id`» de `cortex_conversations`. **Descartada**, en sus dos formas:

- **Eliminarlo** rompe la memoria del owner. `memory_entries` exige `tenant_id NOT
NULL`, y ese valor se resuelve UNA vez (`resolve_cortex_tenant_id`,
  `cortex/threads.py:34`: la membresía activa más antigua) y se persiste. Si se
  borrase la columna habría que re-resolverlo en cada escritura, y la respuesta
  **cambia con el tiempo** —basta que se desactive una membresía— con lo que la
  memoria del owner acabaría repartida entre varios tenants sin que nadie lo pida.
  Lo leen `workers/cortex_affect.py:337`, `workers/cortex_curiosity.py:530` y
  `workers/cortex_reflection.py:522` para exactamente eso.
- **Renombrarlo** no arregla nada y puede empeorarlo. El defecto no era el nombre,
  era la ausencia de defensa estructural, que es lo que cierra este ADR. Además, el
  descubrimiento del meta-invariante busca columnas `LIKE '%tenant_id'`: un nombre
  que siga casando (`memory_tenant_id`) no cambia ninguna comprobación y solo
  genera churn en cuatro sitios de código y en los tests de integración; y un
  nombre que **deje de casar** sacaría la tabla del descubrimiento, que es
  estrictamente peor —una excepción invisible en lugar de una excepción justificada
  y visible en el diff del PR.

Lo que sí se hace, en su lugar: la columna se queda, y su naturaleza queda escrita
donde se busca —modelo, migraciones `0125`/`0140`, entrada de
`POLICY_WITHOUT_TENANT_GUC_ALLOWLIST` en el meta-invariante— como **discriminante
físico de la memoria, no eje de autorización**.

### C — Excepción explícita al Principio 1 (statu quo, documentado)

Dejar el córtex sin RLS y declarar la excepción por escrito en CLAUDE.md.

**Descartada.** Es la opción que ya estaba en vigor de facto y la que produjo la
desviación que destapó el invariante. Una excepción documentada sigue sin oponer
NADA a `app_user`, que tiene DML sobre las seis tablas por default privileges; y el
coste de la alternativa es una migración de DDL puro sin movimiento de datos. Como
recogía la recomendación del plan: _«C no aporta defensa y debe ser la última
opción»_.

## Decisión

1. Las **seis** tablas del córtex llevan `ENABLE` + `FORCE ROW LEVEL SECURITY` y
   una policy `<tabla>_owner_only` sobre `owner_user_id = app.user_id`.
2. El filtro `owner_user_id` explícito en el SQL de aplicación **se conserva**. Es
   la primera línea, y con roles BYPASSRLS es la única que actúa.
3. `cortex_conversations.tenant_id` **se conserva con su nombre**, documentado como
   discriminante físico de la memoria.
4. `browse_sessions` no se toca: su policy por tenant ya la deja fail-closed para
   `app_user`, y una policy por owner añadida encima sería más permisiva, no menos.
5. La regla deja de ser prosa y pasa a ser invariante comprobado (ver «Cómo se
   verifica»). Las entradas de allowlist que decían «aislado por `owner_user_id`»
   sin que nadie lo comprobase eran justo el tipo de promesa que
   [verificar-antes-de-implementar §4](../03-guides/verificar-antes-de-implementar.md)
   señala: una guarda que no puede fallar.

## Consecuencias

**Sobre el login y la sesión.** Ninguna. `users` sigue siendo global (ADR 0137) y
el descubrimiento de identidad ocurre antes de que haya tenant; el GUC
`app.user_id` lo fija `open_tenant_session` en ambas variantes de sesión, así que
no hay ningún camino de autenticación que dependa de leer una tabla del córtex.

**Sobre el chat del córtex.** Ninguna hoy: sus doce aperturas de sesión van por
`get_admin_sessionmaker()` (BYPASSRLS), y BYPASSRLS gana a `FORCE` —medido contra
este PostgreSQL 16 antes de escribir la `0125`, no leído de la documentación—.
Queda una **premisa explícita y comprobada por un test**: el día que el rol de los
servicios deje de ser BYPASSRLS (la dirección declarada en
`docker/postgres/init/04-service-role.sql`), esta policy SÍ les aplicará y, como
ningún camino del córtex fija `app.user_id`, el owner perdería su historial en
silencio. La salida entonces es **cablear el GUC**, no relajar la policy.

**Sobre la memoria.** Ninguna. `memory_entries` es tenant-scoped con RLS por tenant
y no cambia; el `tenant_id` que la alimenta se sigue resolviendo igual.

**Sobre los backups.** Ninguna, y merece decirse en voz alta porque es el modo de
fallo caro de `FORCE`: si el `pg_dump` corriese como **propietario sin BYPASSRLS**,
`FORCE` le aplicaría y el volcado saldría con **cero filas** del córtex sin un solo
error, y nadie lo notaría hasta un restore. No ocurre porque
`WORKERS_BACKUP_DATABASE_URL` es `migrations_user` (BYPASSRLS), que es la misma
premisa del párrafo anterior y está bajo el mismo test.

**Sobre el borrado en cascada.** Las seis tablas cuelgan de `users` con `ON DELETE
CASCADE`. PostgreSQL ejecuta las comprobaciones y acciones de integridad
referencial saltándose la seguridad de fila, así que `FORCE` no las bloquea; y
además `cortex_conversations` ya vive con `FORCE` desde la `0125`, o sea que no es
territorio nuevo.

**Sobre los tests cross-owner.** Los de F1-F4 siguen siendo válidos y siguen
haciendo falta: prueban el filtro de aplicación, que es la capa que de verdad actúa
con los roles de hoy. Se les suma la cobertura estructural nueva.

**Coste.** Una migración de DDL puro, sin movimiento de datos y reversible; su
`downgrade` devuelve las cinco tablas exactamente al estado de las `0092`-`0095` y
no toca la RLS de `cortex_conversations`, que es de la `0125`.

## Cómo se verifica

| Qué                                                                | Dónde                                                                                                     |
| ------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------- |
| Las seis tablas tienen RLS activada en alguna migración (estático) | `tests/security/test_pentest_findings.py::test_every_cortex_table_has_structural_rls`                     |
| Ninguna policy del córtex cuelga de `app.tenant_id` (estático)     | `tests/security/test_pentest_findings.py::test_the_cortex_policies_hang_off_the_owner_and_not_the_tenant` |
| No quedan exenciones de RLS rancias                                | `tests/security/test_pentest_findings.py::test_the_rls_exemptions_are_owner_scoped`                       |
| Catálogo real: `ENABLE`+`FORCE`+policy por `app.user_id` en las 6  | `tests/integration/test_rls_invariant.py::test_every_owner_scoped_table_has_owner_rls`                    |
| Aislamiento funcional bajo `app_user`, fail-closed y `WITH CHECK`  | `tests/integration/test_cortex_owner_rls.py`                                                              |
| El camino real del córtex no se queda a oscuras                    | `tests/integration/test_cortex_owner_rls.py::test_the_real_cortex_path_still_sees_everything`             |
| Round-trip `head → 0139 → head`                                    | `tests/integration/test_cortex_owner_rls.py::test_migration_round_trip_restores_and_reapplies`            |

## Cuándo hay que reabrir este ADR

- **Si el córtex deja de ser singleton del System Owner** (varios owners, o córtex
  por tenant): el eje sigue siendo el owner, pero habría que revisar si `sessions`
  y el córtex necesitan además contexto de tenant.
- **Si algún camino del córtex pasa a servirse con `app_user`**: entonces la policy
  deja de ser inerte y hay que comprobar que el GUC `app.user_id` viaja en esa
  sesión antes de desplegar.
- **Si los roles de servicio dejan de ser BYPASSRLS**: ver «Sobre el chat del
  córtex». Es la premisa que sostiene todo el apartado de consecuencias, y tiene su
  propio test para que se rompa en vez de descubrirse en producción.

## Referencias

- [ADR 0074 — Rol `system_owner` y córtex singleton](0074-rol-system-owner-y-cortex-singleton.md) —
  de donde sale «tenant-less sobre BYPASSRLS». Este ADR **no lo revoca**: mantiene
  que el córtex no es un recurso de tenant, y corrige la consecuencia que se le
  había sacado (que por eso no llevase RLS).
- [ADR 0137 — `users` global sin RLS](0137-users-global-rls.md) — el precedente de
  «una tabla puede ser global y aun así tener su aislamiento razonado».
- [Plan de remediación de la auditoría integral 2026-07-14](../roadmap/remediacion-auditoria-integral-2026-07-14.md) —
  decisión 2, tareas `task_audit14_02` y `task_audit14_03`.
- [`docs/04-reference/multi-tenancy.md`](../04-reference/multi-tenancy.md) — la
  referencia de aislamiento, actualizada con esta decisión.
