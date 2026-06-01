---
title: Configurar proveedores LLM (System Admin)
audience: system admin
phase: 11.2-llm-provider-admin-ui
updated: 2026-06-01
---

# Configurar proveedores LLM (System Admin)

Esta guía te lleva paso a paso por la configuración de los cuatro
proveedores LLM soportados desde el panel, post-instalación: crearlos,
probar la conexión, completar el Device Flow de GitHub Copilot y rotar
sus credenciales.

> **Prerrequisito — sólo `system_admin`.** Los proveedores LLM son
> **platform-global** (ADR 0028): se configuran una sola vez para toda la
> plataforma, NO por tenant. La página `/admin/llm-providers` y todos sus
> endpoints requieren rol **`system_admin`**. Un `tenant_admin` no la ve
> (404/oculta) — el tenant sólo elige qué modelo asigna a cada agente, no
> toca proveedores. Ver la [matriz RBAC](../04-reference/rbac.md) y el
> [ADR 0028](../05-architecture-decisions/0028-platform-global-providers.md).

---

## Conceptos rápidos antes de empezar

**¿Qué proveedores hay?** El catálogo es **cerrado** (ADR 0021): añadir
un quinto pide un ADR explícito. Los cuatro caminos son:

| `kind`          | Proveedor                          | Credencial              | `base_url`             |
| --------------- | ---------------------------------- | ----------------------- | ---------------------- |
| `claude_sdk`    | Claude Agent SDK (suscripción)     | `oauth_token`           | — (no aplica)          |
| `copilot`       | GitHub Copilot (OAuth Device Flow) | `oauth_token` (acuñado) | — (no aplica)          |
| `azure_foundry` | Azure AI Foundry vía APIM          | `api_key` (APIM)        | gateway APIM (req.)    |
| `ollama`        | Ollama (local o cloud)             | `bearer_token` (opc.)   | endpoint Ollama (req.) |

**¿Dónde viven las credenciales?** **Sólo en Vault**, nunca en la base de
datos. Al guardar un proveedor, el backend recibe la credencial, la
escribe en Vault en `platform/llm/<provider_id>` y persiste **sólo** el
puntero `secret_vault_path`. La API nunca devuelve la credencial: en la
lista verás un indicador `has_credential` ("credencial configurada"),
nunca el valor.

**¿Cómo se usa lo que configuras?** El factory de runtime aplica
precedencia **fila de BD activa > variables de entorno/instalador**: si
hay una fila `is_active` del `kind` que un agente usa, el runtime toma su
`base_url` + la credencial de Vault; si no, cae al fallback del
instalador sin romper nada (puedes desactivar una fila para volver al
fallback). Ver el
[ADR 0028 §Cableado del runtime](../05-architecture-decisions/0028-platform-global-providers.md#cableado-del-runtime-precedencia-db--env).

---

## Abrir la página

1. Inicia sesión como `system_admin`.
2. En el menú lateral, entra a **LLM Providers** (`/admin/llm-providers`).
3. Verás la lista de proveedores configurados con su `kind`, estado
   (activo/inactivo), si tienen credencial, y acciones por fila.

Si aún no hay ninguno, verás el estado vacío con un botón **Añadir
proveedor**.

---

## Ejemplo 1 — Ollama (local o cloud)

Escenario: quieres usar un Ollama corriendo en tu red.

1. Pulsa **Añadir proveedor** y elige `kind = ollama`.
2. Rellena:
   - **Display name**: p.ej. `Ollama local`.
   - **Base URL** (requerido): p.ej. `http://ollama:11434`.
   - **Bearer token** (opcional): sólo para Ollama Cloud.
   - **Activo**: déjalo marcado.
3. Pulsa **Guardar**. El backend persiste la fila; si pusiste bearer, lo
   escribe en Vault (`platform/llm/<id>`).
4. Pulsa **Probar conexión** (ver abajo) — debería responder OK haciendo
   `GET {base_url}/api/tags`.

---

## Ejemplo 2 — Azure AI Foundry (vía APIM)

Escenario: el gateway APIM corporativo expone modelos OpenAI-compatible.

1. **Añadir proveedor** → `kind = azure_foundry`.
2. Rellena:
   - **Display name**: p.ej. `Azure Foundry prod`.
   - **Base URL** (requerido): el endpoint del gateway APIM, p.ej.
     `https://apim.empresa.com`.
   - **API key** (requerido): la subscription key de APIM.
3. **Guardar**. La API key va a Vault; la BD guarda sólo el puntero.
4. **Probar conexión**: el probe hace un `GET` autenticado contra el
   gateway con la cabecera `Ocp-Apim-Subscription-Key`. Un `401/403` se
   clasifica como `auth_error`; un endpoint inalcanzable como
   `connection_error`.

---

## Ejemplo 3 — Claude Agent SDK (suscripción)

Escenario: usas una suscripción Claude Pro/Max con su OAuth token.

1. **Añadir proveedor** → `kind = claude_sdk`.
2. Rellena:
   - **Display name**: p.ej. `Claude (suscripción)`.
   - **OAuth token** (requerido): el token de la suscripción.
   - No hay `base_url` para este `kind`.
3. **Guardar**. El token va a Vault.
4. **Probar conexión**: este `kind` no tiene un endpoint público barato de
   liveness, así que el probe verifica que **la credencial está presente
   en Vault** y devuelve OK ("credential configured") o `config_error` si
   falta. No se hace una llamada real al modelo.

---

## Ejemplo 4 — GitHub Copilot (OAuth Device Flow)

Copilot **no** se configura pegando un token a mano: se acuña con el
Device Flow de GitHub, que la UI conduce de principio a fin.

### Paso 1 · Crear el proveedor

1. **Añadir proveedor** → `kind = copilot`.
2. **Display name**: p.ej. `GitHub Copilot`.
3. **Guardar**. El proveedor queda creado **sin credencial todavía** (el
   token se acuña en el Device Flow).

### Paso 2 · Iniciar el Device Flow

1. En la fila del proveedor Copilot, pulsa **Conectar (Device Flow)**.
2. La UI llama a `POST /admin/llm/copilot/device-flow/start` y muestra:
   - Un **user code** (p.ej. `WDJB-MJHT`).
   - Una **verification URI** (p.ej. `https://github.com/login/device`).
3. Abre la verification URI en el navegador, introduce el user code y
   autoriza la app de GitHub.

### Paso 3 · Esperar a que el polling complete

Mientras tú autorizas en GitHub, la UI hace polling automático con
`POST /admin/llm/copilot/device-flow/poll`:

- Mientras no autorices, el estado es `pending` (o `slow_down`, y la UI
  espera el `interval` que GitHub indica).
- En cuanto autorizas, el poll devuelve `authorized: true`: el backend
  acuña el token OAuth de GitHub, lo escribe en Vault
  (`platform/llm/<provider_id>`, campo `oauth_token`) y fija
  `secret_vault_path`. **El token nunca aparece en una respuesta.**
- Si el flujo caduca (`expired`) o lo deniegas (`denied`), reinicia desde
  el Paso 2.

Tras completar, la fila muestra **credencial configurada** y puedes
**Probar conexión**.

---

## El botón "Probar conexión"

Cada fila tiene **Probar conexión** (`POST /admin/llm-providers/{id}/test`).
El backend lee el secreto de Vault (nunca lo echo-ea) y hace una llamada
mínima por `kind`, devolviendo un estado clasificado que la UI colorea:

| Estado             | Significado                                                 |
| ------------------ | ----------------------------------------------------------- |
| `ok`               | El proveedor respondió correctamente.                       |
| `auth_error`       | El proveedor rechazó la credencial (401/403).               |
| `connection_error` | El endpoint no es alcanzable (DNS / connect / timeout).     |
| `config_error`     | Falta un campo requerido (sin `base_url` / sin credencial). |
| `upstream_error`   | El endpoint respondió con un estado inesperado.             |

El `detail` es un mensaje legible que, por construcción, **nunca contiene
el secreto**.

---

## Rotación de credenciales

Para rotar la credencial de un proveedor existente:

1. Pulsa **Editar** en su fila.
2. Introduce **sólo** el nuevo valor del campo de credencial
   (`oauth_token` / `api_key` / `bearer_token` según el `kind`). El resto
   de campos los puedes dejar como están.
3. **Guardar** (`PUT /admin/llm-providers/{id}`). El backend escribe el
   nuevo secreto en la misma ruta de Vault (sobrescribe), conservando el
   puntero. Si **no** envías ningún campo de credencial, el secreto
   existente queda intacto (sólo se actualizan los campos que cambies).
4. (Recomendado) **Probar conexión** para confirmar la nueva credencial.

Para Copilot, "rotar" es **re-ejecutar el Device Flow** (Paso 2 en
adelante): el nuevo token sobrescribe el anterior en Vault.

### Activar / desactivar

El toggle **Activo** controla si el factory de runtime usa esta fila. Una
fila inactiva hace que el runtime caiga al fallback de env/instalador para
ese `kind`, sin romper ejecuciones — útil para volver atrás sin borrar la
configuración.

### Borrar un proveedor

**Eliminar** (`DELETE /admin/llm-providers/{id}`) borra **primero** el
secreto de Vault (idempotente) y luego la fila. Si Vault no está
disponible, la operación falla (`502`) antes de tocar la fila, para no
dejar un secreto huérfano.

---

## Asociar modelos del catálogo a su proveedor

El catálogo de precios (`model_prices`, ver
[`pricing.md`](../04-reference/pricing.md)) ahora puede enlazar cada
modelo a un proveedor configurado:

- En `/admin/model-prices` puedes filtrar por proveedor
  (`?provider_id=<uuid>`) y, al crear/editar un precio, asociarlo a una
  fila de `llm_providers`.
- La asociación es **opcional** (`provider_id` nullable): el feed
  principal del catálogo es el sync desde LiteLLM, que sólo conoce la
  familia (`anthropic`, `openai`...). Tú asocias explícitamente cuando
  quieras vincular un precio a un proveedor concreto.
- Borrar un proveedor **no** borra el precio ni su histórico
  (`ON DELETE SET NULL`).

---

## Preguntas frecuentes

### ¿Por qué no veo la credencial que guardé?

Por diseño. Las credenciales son **write-only**: van a Vault y nunca se
devuelven. La lista te dice si hay credencial (`has_credential`) y dónde
vive (`secret_vault_path`), nunca el valor.

### Configuré el proveedor pero el agente sigue usando el del instalador

Comprueba que la fila esté **activa**. La precedencia es _fila activa de
BD > env/instalador_: si la fila está inactiva o no es del `kind` que el
agente usa, gana el fallback. Si la fila está activa y aun así no se
aplica, revisa que su `kind` coincida con `agents.llm_provider`.

### "Probar conexión" da `config_error` para Copilot / Claude

Significa que el `oauth_token` no está en Vault todavía. Para Copilot,
completa el Device Flow; para Claude SDK, edita el proveedor e introduce
el `oauth_token`.

### Vault está caído — ¿qué pasa?

Crear/editar un proveedor con credencial responde `503`/`502` (no se
persiste una fila sin su secreto). En ejecución, un blip transitorio de
Vault degrada al fallback de env en vez de romper el run.

---

## Referencias

- **ADR del modelo platform-global**:
  [docs/05-architecture-decisions/0028-platform-global-providers.md](../05-architecture-decisions/0028-platform-global-providers.md)
- **ADR del catálogo cerrado de proveedores**:
  [docs/05-architecture-decisions/0021](../05-architecture-decisions/) (ADR 0021)
- **Matriz RBAC** (sección Platform-global configuration):
  [docs/04-reference/rbac.md](../04-reference/rbac.md)
- **Changelog del plan**:
  [docs/07-changelog/11.2-llm-provider-admin-ui.md](../07-changelog/11.2-llm-provider-admin-ui.md)
- **Plan de fase**: `docs/roadmap/11.2-llm-provider-admin-ui.md`
