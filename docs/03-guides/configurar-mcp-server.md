---
title: Configurar un MCP server en un proyecto
audience: usuario tenant, project owner, system admin
phase: 05-mcp-tools-avanzadas
updated: 2026-05-28
---

# Configurar un MCP server en un proyecto

Esta guía te lleva paso a paso por las dos formas de añadir un
servidor MCP (Model Context Protocol) a un proyecto:

1. **Con plantilla del catálogo** (recomendado) — eliges una de las
   22 integraciones verificadas (GitHub, Jira, Google Drive, Slack…)
   y el sistema rellena la configuración por ti.
2. **Sin plantilla** — configuras manualmente un MCP propio o uno
   que no esté en el catálogo (HTTP custom, comando local, etc.).

> **Prerrequisito.** Estás autenticado en el panel (`/admin`) y
> tienes un proyecto activo. Configurar MCP servers en un proyecto
> requiere rol **`tenant_admin`** o `system_admin` (Plan 06.8 —
> ver [Roles y permisos](./roles-y-permisos.md) y la
> [matriz RBAC](../04-reference/rbac.md)). Si eres `tenant_user`,
> el botón **+ Añadir MCP** no aparece en la pestaña; pide a un
> `tenant_admin` que ejecute los pasos siguientes por ti.

---

## Conceptos rápidos antes de empezar

**¿Qué es un MCP server?** Es un proceso (local o remoto) que
expone _tools_ que los agentes del proyecto pueden invocar. Ejemplos:
leer issues de Jira, consultar la BD de Postgres, listar archivos
en Google Drive, etc.

**¿Y las credenciales?** Las integraciones que necesitan API key /
token (la mayoría) llevan ese secreto en **Vault**, nunca en la base
de datos. Tú, como operador del proyecto, no manejas el secreto:
sólo declaras que la integración existe y el administrador del
tenant es quien lo guarda en Vault.

**¿Quién hace qué?**

| Rol                   | Qué hace                                                                                                     |
| --------------------- | ------------------------------------------------------------------------------------------------------------ |
| Project owner         | Elige la plantilla y la añade al proyecto desde `/admin/projects/{id}/mcp-servers`.                          |
| System / tenant admin | Guarda el secreto (token, API key) en Vault siguiendo la ruta convenida.                                     |
| Agente                | Llama las tools del MCP. La plataforma resuelve el secreto en cada llamada — el agente nunca lo ve en claro. |

---

## Ejemplo 1 — Añadir Jira al proyecto (con plantilla)

Escenario: queremos que los agentes del proyecto puedan leer y
comentar issues de Jira Cloud.

### Paso 1 · Abrir el dialog "Añadir MCP server"

1. Entra a `/admin/projects/{tu-proyecto}/mcp-servers`.
2. Pulsa **"Añadir MCP server"** (esquina superior derecha).

Se abre el dialog "Configurar MCP server".

### Paso 2 · Elegir la plantilla

En lo alto del dialog ves el dropdown **"Plantilla rápida"**.

1. Desplégalo y busca el grupo **"Issue trackers"**.
2. Selecciona **"Atlassian Jira 🔒"**. El candado indica que la
   integración necesita credenciales.

Al elegirla:

- El campo **Nombre** se rellena con `jira-mcp`.
- El **Transporte** queda en `stdio`.
- El **Comando** queda en `mcp-jira`.
- En **Opciones avanzadas** aparece una card verde:

  > 🔒 **Esta integración requiere credencial**
  >
  > El sistema ya sabe dónde guardar el secreto. Pide al
  > administrador del tenant que añada
  > `JIRA_API_TOKEN, JIRA_EMAIL, JIRA_INSTANCE_URL` en Vault antes
  > del primer uso.

### Paso 3 · Probar conexión (opcional, recomendado)

Pulsa **"Probar"** dentro del bloque "Probar conexión". Si el
secreto aún no está en Vault verás el error tipado
`AUTH_ERROR` — eso es esperado y confirma que la plataforma está
intentando resolverlo correctamente. Vuelve después de que el
administrador del tenant haya guardado el secreto.

### Paso 4 · Guardar

Pulsa **"Crear"**. El MCP queda registrado en el proyecto y
aparece como una tarjeta en `/admin/projects/{id}/mcp-servers`.

### Paso 5 · El administrador del tenant guarda el secreto

(Esta sección es para el `system_admin` / `tenant_admin`.)

1. Abre Vault (UI o CLI).
2. La plantilla `jira-mcp` espera el secreto en la ruta:

   ```
   secret/data/mcp/jira/{project_id}
   ```

   donde `{project_id}` es el identificador del proyecto (el sistema
   conoce el valor; lo verás en la ruta autocompletada si pulsas
   "Detalles técnicos" dentro del dialog de configuración).

3. El payload debe contener las tres claves declaradas por la
   plantilla:

   ```json
   {
     "JIRA_API_TOKEN": "ATATT3xFfGF0...",
     "JIRA_EMAIL": "agente@empresa.com",
     "JIRA_INSTANCE_URL": "https://empresa.atlassian.net"
   }
   ```

4. Guarda en Vault con:

   ```bash
   vault kv put secret/mcp/jira/{project_id} \
     JIRA_API_TOKEN="ATATT3xFfGF0..." \
     JIRA_EMAIL="agente@empresa.com" \
     JIRA_INSTANCE_URL="https://empresa.atlassian.net"
   ```

   (Vault añade el `/data/` automáticamente en KV v2 — la ruta
   `vault:secret/data/...` que aparece en la configuración del MCP
   se mapea a `secret/...` con `vault kv`.)

### Paso 6 · Verificar end-to-end

Vuelve al dialog de Jira en el admin-panel y pulsa **"Probar"** de
nuevo. Deberías ver:

```
✅ jira-mcp v1.x.y
6 tools descubiertas: get_issue, search_issues, comment_issue, ...
```

Si sale `AUTH_ERROR` revisa la ruta exacta en Vault contra la que
muestra el campo "Detalles técnicos". Si sale `TRANSPORT_ERROR`
es que el comando `mcp-jira` no está instalado en el agent-runtime
— pasa al system admin.

---

## Ejemplo 2 — Añadir un MCP HTTP custom (sin plantilla)

Escenario: la empresa tiene un MCP propio para consultar el ERP
interno. No está en el catálogo público. Lo exponemos vía HTTP.

### Paso 1 · Abrir el dialog "Añadir MCP server"

Igual que el ejemplo anterior, desde
`/admin/projects/{tu-proyecto}/mcp-servers` → **"Añadir MCP server"**.

### Paso 2 · Dejar el picker en "Empezar en blanco"

No selecciones ninguna plantilla del dropdown. Vamos a rellenar los
campos manualmente.

### Paso 3 · Configurar los campos básicos

| Campo      | Valor                           |
| ---------- | ------------------------------- |
| Nombre     | `erp-mcp`                       |
| Transporte | `sse` (HTTP server-sent events) |
| URL        | `https://erp-mcp.internal/mcp`  |

### Paso 4 · Cabeceras estáticas (si aplica)

Si el MCP necesita una cabecera estática (no secreta), añádela en
el editor de **Cabeceras**:

| Clave         | Valor            |
| ------------- | ---------------- |
| `X-Tenant-Id` | `erp-prod`       |
| `X-Service`   | `agent-platform` |

Estas viajan **igual en todas las llamadas** y no son secretos.

### Paso 5 · Credencial (si el MCP necesita auth)

Si el MCP requiere un `Authorization: Bearer ...` o similar, abre
**Opciones avanzadas** y rellena la **Credencial del servidor**.

Convención de ruta para MCPs custom:

```
vault:secret/data/mcp/<nombre-corto>/<proyecto>
```

Donde `<nombre-corto>` es un identificador estable (en este caso
`erp-mcp`) y `<proyecto>` es el identificador del proyecto. El
admin-panel no autorrellena este campo en MCPs sin plantilla —
debes tipearlo a mano siguiendo la convención.

Ejemplo:

```
vault:secret/data/mcp/erp-mcp/<id-de-tu-proyecto>
```

> **Si el MCP no necesita autenticación** (p.ej. está en la red
> privada y confía por origen), deja el campo vacío. La validación
> del backend permite `auth_ref = null`.

### Paso 6 · Configurar el secreto en Vault

El admin del tenant guarda el secreto en la ruta que escribiste.
Para un Bearer token sencillo el payload sería:

```json
{
  "AUTHORIZATION": "Bearer eyJhbGciOiJI..."
}
```

> **Cómo decide la plataforma qué claves del Vault entran como env
> vs como header?** Para `transport=stdio` van como variables de
> entorno. Para `sse` / `streamable_http` van como cabeceras HTTP.
> El nombre de la clave en Vault es el nombre del env var o del
> header (mayúsculas, snake-case típico).

### Paso 7 · Probar y guardar

Pulsa **"Probar"** — debería listar las tools que el MCP custom
expone. Si sale OK, **"Crear"** persiste la configuración en el
proyecto.

---

## Diferencias con plantilla vs sin plantilla — tabla resumen

| Aspecto                          | Con plantilla                                       | Sin plantilla                     |
| -------------------------------- | --------------------------------------------------- | --------------------------------- |
| Nombre del MCP                   | Pre-rellenado                                       | Tú lo eliges                      |
| Transporte / comando / URL       | Pre-rellenado                                       | Tú los configuras                 |
| Ruta del secreto en Vault        | Autorrellenada                                      | Tú la tipas                       |
| Convención de ruta               | Garantizada por el sistema                          | Sigues la convención              |
| Validación del cliente MCP en CI | Sí (test_mcp_integrations)                          | No (es responsabilidad tuya)      |
| Onboarding del admin del tenant  | Sabe qué claves esperar (`secret_keys` documentado) | Documenta tú las claves esperadas |

---

## Preguntas frecuentes

### ¿Puedo añadir el mismo MCP a varios proyectos?

Sí. Cada proyecto tiene su propia entrada en `mcp_servers` y su
propia ruta en Vault (`vault:secret/data/mcp/{tipo}/{project_id}`).
Eso permite que cada proyecto use su propia API key — separación
limpia, sin secretos compartidos entre proyectos.

### ¿Y si quiero un secreto compartido para todo el tenant?

No es la convención por defecto. Si lo necesitas, edita el campo
**Credencial del servidor** manualmente y pon una ruta sin
`{project_id}`, p.ej.:

```
vault:secret/data/mcp/jira/shared
```

Asegúrate de que tu Vault tiene políticas de acceso adecuadas para
esa ruta — el sistema confía en lo que escribas.

### ¿Cómo elimino un MCP?

Desde la card en `/admin/projects/{id}/mcp-servers`, botón
papelera. Eso solo desliga el MCP del proyecto; el secreto sigue en
Vault hasta que el admin del tenant lo borre aparte.

### ¿Puedo añadir mis propias plantillas al catálogo?

Sí pero es cambio de código + ADR. Edita
[`packages/shared-mcp/src/shared_mcp/catalog.py`](../../packages/shared-mcp/src/shared_mcp/catalog.py)
añadiendo una entrada `McpServerTemplate(...)`. Tests
(`test_mcp_integrations.py`) validan que la entrada cumple el
esquema. Tras merge, la nueva plantilla aparece en el picker.

### ¿Qué pasa si el secreto no está en Vault al primer uso?

La llamada del agente devuelve un error tipado `AUTH_ERROR`. El
agente lo ve como un fallo de tool y suele reintentar o reportarlo
al humano. El sistema no se cae ni filtra credenciales.

---

## Referencias

- **Catálogo completo** (descripción técnica de cada plantilla):
  [docs/04-reference/mcp-servers.md](../04-reference/mcp-servers.md)
- **ADR del modelo de tools / MCP**:
  [docs/05-architecture-decisions/0025-mcp-tools-y-ejecutores.md](../05-architecture-decisions/0025-mcp-tools-y-ejecutores.md)
- **Plan de fase**: `docs/roadmap/05-mcp-tools-avanzadas.md`
