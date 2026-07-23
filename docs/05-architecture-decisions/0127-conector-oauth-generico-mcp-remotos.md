---
title: "ADR 0127: Conector OAuth genérico para servidores MCP remotos"
status: proposed
date: 2026-07-23
deciders: [operador]
relates_to: [0021, 0052, 0117]
---

# ADR 0127: Conector OAuth genérico para servidores MCP remotos

## Contexto

Tras el ADR 0117(a) el catálogo **solo ofrece MCP de transporte HTTP**. Las tres
plantillas ofrecibles hoy son:

- `context7` — remoto, sin auth (key opcional en cabecera).
- `github-remote` — remoto, **PAT estático** en cabecera `Authorization` desde Vault.
- `atlassian` — **sidecar** self-hosted (`ghcr.io/sooperset/mcp-atlassian`), con el
  **API token en el ENV del sidecar** (no en la petición).

Muchos servidores MCP remotos "de primera" (el oficial de Atlassian
`mcp.atlassian.com`, el remoto de GitHub, Notion, Google Workspace, Linear, Slack)
se autentican con **OAuth 2.1**, y hoy **no se pueden usar de forma limpia**:

- `shared_mcp/auth.py` (`VaultResolver.resolve`) inyecta un **bearer ESTÁTICO**
  desde Vault. No hace el consentimiento interactivo ni **refresca** el token.
- Un access token de OAuth pegado a mano **caduca (~1 h)** → el server dejaría de
  funcionar a mitad de un run. No es viable para ejecución autónoma.
- El **sidecar** esquiva OAuth (usa API token de larga vida), pero: es infra a
  desplegar/mantener y, con un solo sidecar, **comparte credenciales/instancia**
  entre proyectos/tenants (olor multi-tenant; ver la discusión del sidecar).

**Requisito del operador (2026-07-23):** el flujo OAuth **no debe ser a medida de
Atlassian**, sino **genérico y reutilizable** para cualquier MCP del mismo estilo
(GitHub, Notion, Google…). Es la decisión de diseño correcta: el **propio estándar
MCP define un flujo OAuth 2.1 común** para transportes HTTP (descubrimiento del
authorization server — RFC 9728/8414, Dynamic Client Registration — RFC 7591,
Authorization Code + PKCE, refresh). Una sola implementación conforme sirve para
todos.

**Base ya disponible (verificado):**

- `shared_mcp/client.py` usa el **SDK oficial `mcp`** (`streamablehttp_client`,
  `sse_client`, `ClientSession`). Ese SDK **incluye un cliente OAuth**
  (`mcp.client.auth.OAuthClientProvider`) que hace discovery + PKCE + intercambio +
  **refresh**, con un **`TokenStorage` enchufable** (callback de lectura/escritura de
  tokens). El grueso del OAuth ya está hecho por el SDK.
- La plataforma ya tiene patrones OAuth reutilizables: **OIDC/SSO**
  (`auth/sso/oidc.py`, callback/redirect) y el **device-flow de GitHub Copilot**
  (ADR 0021, tokens en Vault). El almacenamiento en Vault por-tenant también existe.

## Decisión propuesta

Construir **UN conector OAuth genérico para MCP remotos**, data-driven por la
plantilla del catálogo, apoyado en el `OAuthClientProvider` del SDK + un
`TokenStorage` respaldado por Vault + un disparo de consentimiento único.

Piezas:

1. **Metadatos en `McpServerTemplate`**: añadir `auth_kind` ∈
   {`none`, `static`, `oauth`, `sidecar`} (+ pistas OAuth opcionales: `scopes`,
   `authorization_server` si el server no publica discovery). La UI del picker
   muestra un botón **«Conectar»** para `auth_kind="oauth"` en vez de un campo de
   token.
2. **Flujo «Conectar» (interactivo, UNA vez, por tenant + proyecto + server):**
   endpoint que inicia OAuth (discovery + PKCE vía el SDK), redirige al consentimiento
   del proveedor, y un **callback** intercambia el código por tokens. Reutiliza el
   patrón callback/redirect de OIDC/SSO y de device-flow de Copilot (ADR 0021).
3. **Token store en Vault**, con clave **por (tenant, proyecto, server)** →
   multi-tenancy **limpia**: cada tenant autoriza SU cuenta; sin bot compartido y sin
   sidecar por-instancia. `secret_vault_path` ya es el pointer; se amplía el payload
   a `{access_token, refresh_token, expires_at, ...}`.
4. **Runtime**: `shared_mcp` conecta el `OAuthClientProvider` del SDK a un
   `VaultTokenStorage` (implementa el `TokenStorage` del SDK sobre `VaultResolver`).
   El SDK **auto-refresca** con el refresh token al expirar/401 y persiste el nuevo
   par vía el store → sin daemon de refresco propio (opcionalmente un beat que
   pre-refresca para evitar el primer 401 de un run).
5. **Primeros consumidores**: `atlassian` (remoto oficial `mcp.atlassian.com`) y
   `github-remote`; después `notion`, `linear`, `google-*`. El sidecar `atlassian`
   actual se conserva como alternativa sin-OAuth (API token) para quien lo prefiera.

## Opciones consideradas

1. **Conector OAuth genérico (recomendada).** Una implementación estándar-MCP para
   todos. Coste medio (se apoya en el SDK); paga por sí sola desde el 2º consumidor.
2. **OAuth a medida por servicio.** Rechazada: N× trabajo, divergencia, justo lo que
   el operador pide evitar.
3. **Statu quo (sidecar + tokens estáticos).** Se mantiene para servicios con API
   token de larga vida (Atlassian sidecar, GitHub PAT), pero **no** resuelve los
   remotos OAuth ni la multi-tenancy con credenciales por-tenant.

## Consecuencias

- **A favor:** cualquier MCP OAuth se conecta con «Conectar» una vez; tokens
  por-tenant (multi-tenancy limpia); cero infra de sidecar; oficial y siempre al día;
  reutiliza el OAuth del SDK (menos código propio). Un solo conector cubre Atlassian,
  GitHub, Notion, Google, Linear…
- **Riesgos / a validar:** (a) el consentimiento exige un humano **una vez** al
  conectar (aceptable; es la naturaleza de OAuth). (b) Soporte de **Dynamic Client
  Registration** desigual por proveedor — algunos exigen `client_id/secret`
  pre-registrado (guardable en Vault por-server). (c) Madurez del
  `OAuthClientProvider` del SDK a comprobar con Atlassian/GitHub reales. (d) Revocación
  del refresh token → volver a «Conectar»; superficiar el estado en la UI. (e)
  El token store debe ser por-tenant y en Vault (nunca en BD).
- **Relación:** complementa ADR 0117(a) (catálogo HTTP-only); extiende los patrones
  OAuth del ADR 0021; ortogonal al ADR 0052 (importación de tools). No cambia nada
  hasta implementarse: los remotos OAuth siguen sin ofrecerse como `oauth` hasta
  entonces (el sidecar Atlassian y el PAT de GitHub siguen siendo el camino actual).

## Estimación (orden de magnitud)

Media. Núcleo: `VaultTokenStorage` + `OAuthClientProvider` en `shared_mcp`
(pequeño, el SDK hace el trabajo), endpoints connect/callback en api-server
(reutilizan patrón OIDC), campo `auth_kind` + botón «Conectar» en el picker, y
tests (flujo connect con proveedor fake + refresh). Los siguientes consumidores
(Notion, Google…) son solo una fila de catálogo `auth_kind="oauth"` cada uno.
