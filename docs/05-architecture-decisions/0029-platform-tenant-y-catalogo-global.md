---
adr_id: "0029"
title: "El platform tenant y el mecanismo canónico para contenido de catálogo global"
status: accepted
date: 2026-05-29
authors: [system_architect]
plan_referenced: 06.11-kb-ingestion-fixes
docs_language: es
---

# ADR 0029 — Platform tenant y catálogo global: un mecanismo canónico

## Contexto

El sistema es multi-tenant con aislamiento por `tenant_id` y RLS en cada
tabla. Pero hay contenido que **no pertenece a ningún tenant**: el
**catálogo built-in** que la plataforma siembra y todos los tenants
pueden consumir (agentes built-in, skills, tools, teams, plantillas de
proyecto, KBs, categorías de KB, plantillas de política de aprobación).

Para alojar ese contenido existe el **platform tenant**:
`PLATFORM_TENANT_ID = 00000000-0000-0000-0000-000000000001`, una fila
real en `organizations` (ver
[`seeds/platform.py`](../../apps/api-server/src/api_server/seeds/platform.py)).
**No es un cliente**: es un "aparcamiento" para contenido de catálogo.
Está oculto a todos los tenants por la política `org_self_only` (nadie
lo ve en su lista de organizaciones), y solo escribible por el rol
BYPASSRLS (`migrations_user`) que corre los seeds.

El problema que motiva este ADR: al construir el catálogo a lo largo de
los Planes 00→06 surgieron **dos mecanismos distintos** para responder
"¿esta fila es global?", y conviven sin una decisión escrita. Lo
destapó una auditoría del subsistema de KBs (Plan 06.11): las KB
built-in están sembradas bajo el platform tenant pero son **invisibles**
a los tenants porque a `knowledge_bases` se le olvidó su política de
lectura de catálogo.

### Los dos mecanismos en uso hoy

**(A) Bandera + platform tenant — patrón dominante (5+ tablas, Plan 00-01).**
La fila vive bajo `PLATFORM_TENANT_ID` y una columna la marca como
catálogo. Una política `<tabla>_builtin_read FOR SELECT` la expone a
TODA sesión de tenant **keyando por la bandera, no por el tenant_id**:

| Tabla                       | Bandera                    | Política                                 |
| --------------------------- | -------------------------- | ---------------------------------------- |
| `agents`                    | `scope = 'global_builtin'` | `agents_global_builtin_read`             |
| `skills`                    | `is_builtin = true`        | `skills_builtin_read`                    |
| `tools`                     | `is_builtin = true`        | `tools_builtin_read`                     |
| `teams`                     | `is_builtin = true`        | `teams_builtin_read`                     |
| `projects` (plantillas)     | `is_template = true`       | `projects_template_read`                 |
| `approval_policy_templates` | `is_builtin = true`        | `approval_policy_templates_builtin_read` |

Conviven con la política `<tabla>_tenant_isolation FOR ALL` (filtra por
`tenant_id`). Para un SELECT, PostgreSQL hace OR de ambas: el tenant ve
sus filas **y** las de catálogo. Para INSERT/UPDATE/DELETE solo aplica
la de aislamiento, así que un tenant **no puede mutar** el catálogo
(su `WITH CHECK (tenant_id = app.tenant_id)` rechaza filas del platform
tenant).

**(B) `tenant_id IS NULL` — outlier (1 tabla, Plan 06.10).**
`kb_categories` (introducida en 06.10) marca los built-in con
`tenant_id IS NULL` y los expone con `kb_categories_builtin_read FOR
SELECT USING (tenant_id IS NULL)`. No usa el platform tenant ni una
bandera.

`knowledge_bases` no implementa **ninguno** de los dos (el bug): filas
bajo el platform tenant, sin política de catálogo, sin bandera →
invisibles.

## Decisión

1. **El mecanismo canónico es (A): platform tenant + bandera +
   `_builtin_read`.** Todo contenido de catálogo global:
   - se siembra bajo `PLATFORM_TENANT_ID`,
   - lleva una bandera booleana `is_builtin` (o una semántica de
     `scope`/`is_template` ya existente y equivalente),
   - se expone con una política `<tabla>_builtin_read FOR SELECT USING
(<bandera>)`, además de su `<tabla>_tenant_isolation FOR ALL`.

   La visibilidad de catálogo **keya por la bandera, no por el
   tenant_id**. El platform tenant es el _dueño_ del contenido; la
   bandera es lo que lo hace globalmente legible y, a la vez, inmutable
   para los tenants.

2. **`tenant_id` permanece `NOT NULL` en todas las tablas con tenant.**
   No se usa `tenant_id IS NULL` como señal de "global". (Evita el ripple
   de columnas nullable en tablas core y un segundo significado del
   `tenant_id`.)

3. **`kb_categories` se alinea al patrón (A)**: se le añade `is_builtin`,
   sus built-in se re-siembran bajo `PLATFORM_TENANT_ID` con
   `is_builtin=true`, y su política pasa de `tenant_id IS NULL` a
   `is_builtin = true`. El outlier desaparece.

4. **`knowledge_bases` adopta (A)**: se le añade `is_builtin` +
   `knowledge_bases_builtin_read`, el resolver de visibilidad
   (`resolve_visible_kbs` y `visibility_filter_clause`) añade la rama de
   catálogo, y el seed marca las KB canónicas `is_builtin=true`. Esto
   las hace **grantables y visibles** a los tenants sin volver
   `tenant_id` nullable.

## Consecuencias

- **Un solo modelo mental**: "contenido global = fila del platform tenant
  con su bandera de catálogo". Vale para auditar, para la UI (mostrar un
  badge "built-in" y ocultar editar/borrar) y para nuevas tablas.
- **Built-in = solo lectura para tenants** sale gratis de la RLS (la
  política de catálogo es `FOR SELECT`; la de aislamiento bloquea
  escrituras con su `WITH CHECK`).
- **Grants sí permitidos**: conceder un built-in a un proyecto/agente
  crea filas en las tablas de junction (`kb_projects`,
  `agent_knowledge_bases`) que SÍ son del tenant — no muta el catálogo.
- **Implementación** (su propio plan, **06.12 — consistencia del
  catálogo global**): migración para `knowledge_bases.is_builtin` +
  `kb_categories.is_builtin`, swap de políticas, ajuste de seeds,
  resolver y tipos/UX del frontend (badge built-in, ocultar
  editar/borrar). El Plan 06.11 dejó esta pieza diferida apuntando a
  este ADR.
- **Las KB built-in siguen vacías de contenido** hasta que un proceso
  (manual o un plan futuro) las llene de documentos/chunks; este ADR
  resuelve la _alcanzabilidad_, no la _población_.

## Alternativas consideradas

- **Estandarizar en (B) `tenant_id IS NULL`** (lo que se eligió primero
  para KB en la conversación de 06.11). Rechazada: obliga a volver
  `tenant_id` nullable en tablas core, da dos significados al
  `tenant_id`, y exigiría migrar 5 tablas que ya usan (A) — más ripple
  que migrar la única tabla que usa (B).
- **Dejar conviviendo (A) y (B)** documentando cada caso. Rechazada: la
  inconsistencia es justo lo que confunde; un catálogo, un mecanismo.
- **Tabla aparte por catálogo** (p.ej. `knowledge_base_templates`).
  Rechazada por Plan 06.10 para `kb_categories` (duplica esquema +
  UNION en las queries); se mantiene esa decisión.

## Referencias

- Platform tenant: [`seeds/platform.py`](../../apps/api-server/src/api_server/seeds/platform.py)
- Patrón (A): migraciones `0004_agents_builtin_visibility`,
  `0005_skills_tools_builtin`, `0006_teams_builtin`,
  `0008_approval_policy_templates`.
- Outlier (B): `0028_kb_categories` (Plan 06.10).
- Bug que lo destapó: Plan 06.11, auditoría del subsistema KB.
- Modelo rol vs stack de KBs: [ADR 0026](0026-agent-scoped-kbs.md).
