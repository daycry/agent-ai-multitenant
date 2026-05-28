---
title: Roles y permisos en la plataforma
audience: usuario tenant, project owner, tenant admin
phase: 06.8-rbac-enforcement
updated: 2026-05-28
---

# Roles y permisos

Esta guía explica qué puede hacer cada rol dentro de un tenant y
cómo se diferencia un usuario normal de un administrador. La
referencia técnica (matriz endpoint → rol mínimo) vive en
[`docs/04-reference/rbac.md`](../04-reference/rbac.md).

## Resumen rápido

| Acción                               | tenant_user | tenant_admin | system_admin |
| ------------------------------------ | ----------- | ------------ | ------------ |
| Ver proyectos, agentes, equipos      | ✅          | ✅           | ✅           |
| Crear / mover tareas en el kanban    | ✅          | ✅           | ✅           |
| Comentar en planes                   | ✅          | ✅           | ✅           |
| Aprobar / rechazar approval-requests | ✅          | ✅           | ✅           |
| Crear / editar / borrar proyectos    | ❌          | ✅           | ✅           |
| Crear / editar / borrar agentes      | ❌          | ✅           | ✅           |
| Configurar MCP servers               | ❌          | ✅           | ✅           |
| Crear / borrar knowledge-bases       | ❌          | ✅           | ✅           |
| Cambiar tenant-settings (rates…)     | ❌          | ✅           | ✅           |
| Aprobar plan (commit a ejecución)    | ❌          | ✅           | ✅           |
| Ver tareas escaladas a humano        | ❌          | ✅           | ✅           |
| Ver / cambiar otros tenants          | ❌          | ❌           | ✅           |

## El badge en la cabecera

En el panel `/admin/*`, al lado del menú de usuario aparece un
badge que codifica tu rol:

- **`system_admin`** (ámbar) — flag global de la plataforma. Pasas
  todos los gates. Puedes cambiar de tenant con el picker.
- **`admin`** (azul) — `tenant_admin` en el tenant activo. Puedes
  mutar la configuración del tenant: proyectos, agentes, equipos,
  MCP servers, KBs, settings.
- **`user`** (gris) — `tenant_user` en el tenant activo. Puedes
  trabajar el día a día (tareas, kanban, comentarios) pero las
  pantallas de configuración te aparecen sin botones de edición.

Si no ves el badge es que no tienes contexto de tenant (login fresco
o superadmin sin picker seleccionado).

## Qué puede hacer un `tenant_user`

Operaciones del día a día — todo lo que no toque la configuración
del tenant ni añada/cambie recursos persistentes que afectan a
otros:

- Navegar todos los proyectos, agentes, equipos, KBs y memorias que
  el tenant tiene.
- Crear tareas en cualquier proyecto, moverlas entre columnas del
  kanban, asignarlas, borrarlas (sólo el admin puede borrar tareas
  escaladas) — esto es la mecánica del flujo de trabajo, no la
  configuración del tenant.
- Comentar en planes y conversaciones de un proyecto.
- Aprobar o rechazar `approval-requests` que el sistema le manda
  cuando un agente quiere ejecutar una acción sensible.
- Ver el detalle de cualquier ejecución y plan.

Lo que NO ve / NO puede hacer:

- Los items "Validación humana" y "Settings" no aparecen en el
  sidebar.
- Los botones "Crear proyecto", "Nuevo agente", "Crear KB",
  "Editar / Borrar" en pantallas de configuración están ocultos.
- Cualquier `POST/PUT/DELETE` directo contra el API que toque
  configuración del tenant devuelve `403 tenant_admin role required`
  aunque la UI no haya mostrado el botón.

## Qué puede hacer un `tenant_admin`

Todo lo anterior **más** la configuración del tenant:

- Crear / editar / borrar proyectos. Cambiar su team asignado,
  budgets, repository config, MCP servers, KBs grantadas.
- Crear / editar / borrar agentes (catálogo del tenant) y equipos.
- Configurar MCP servers nuevos. Editar credenciales en Vault va
  por un flujo aparte (`/credentials`, follow-up de Plan 06.6).
- Aprobar planes con su firma — esto compromete al tenant a
  ejecutarlos y consumir presupuesto.
- Cambiar `tenant-settings` (hourly-rate, memory thresholds, etc.).
- Ver la página de **tareas escaladas a humano** (`/admin/plans/{id}
/escalated`) y resolverlas (reasignar con guidance, bloquear con
  reason, marcar approve_manual o cancelar).

## Qué puede hacer un `system_admin`

Pasa **todos los gates** del tenant que esté visitando + endpoints
exclusivos `/admin/*`:

- Crear / editar / borrar **tenants** (`/admin/tenants`).
- Listar usuarios globales (`/admin/users`).
- Ver el dashboard de salud del sistema (`/admin/system-health`).
- Picker de tenant en la cabecera: cambia el contexto de "tenant
  activo" sin re-emitir el token (vía cabecera `X-Tenant-Id`).

`system_admin` no es un rol per-tenant — es un flag global
`users.is_system_admin = true`. La promoción se hace por SQL o por
script `python -m api_server.seeds.create_admin` por ahora;
gestionar `system_admin`s desde UI es follow-up del Plan 15
(instalador prod).

## Cómo le doy admin a alguien

Hoy (Plan 06.8) no hay UI para gestionar memberships. Se hace por
SQL contra `user_org_memberships`:

```sql
-- Ascender a tenant_admin
UPDATE user_org_memberships
   SET role = 'tenant_admin'
 WHERE user_id = '<UUID-del-usuario>'
   AND tenant_id = '<UUID-del-tenant>';

-- Añadir membership nueva
INSERT INTO user_org_memberships (id, tenant_id, user_id, role, is_active)
VALUES (gen_random_uuid(), '<tenant>', '<user>', 'tenant_admin', true);

-- Quitar admin sin borrar la cuenta
UPDATE user_org_memberships
   SET role = 'tenant_user'
 WHERE user_id = '<user>' AND tenant_id = '<tenant>';
```

Tras un cambio de rol el usuario debe hacer logout/login para que
el JWT refleje el nuevo rol (el JWT se firma con `sys` y la role
se chequea contra la BD en cada request, pero el endpoint `/me`
cachea 5 min — un F5 fuerza el refresh).

Una UI específica para gestionar memberships llega con el Plan 15
(instalador prod) o como follow-up dedicado.

## ¿Y si la UI no muestra un botón pero quiero usar el API?

Bien. El backend valida igual:

```bash
curl -sS http://localhost:8001/projects \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"name":"X","status":"active"}'
# → 403 {"detail":"tenant_admin role required"}
```

La UI sólo refleja lo que el backend va a permitir. Si crees que
deberías poder ejecutar una acción y el backend te rechaza,
contacta a un `tenant_admin` para que te ascienda o pídele que
ejecute la operación por ti.
