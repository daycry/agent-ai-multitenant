---
title: Publicar en el marketplace (skills, tools y MCP servers)
audience: tenant admin, project owner
phase: 09.1-marketplace-seed-publish
updated: 2026-06-01
---

# Publicar en el marketplace

Esta guía explica cómo publicar (y despublicar) un listing en el marketplace
del tenant: los **formatos de manifest** (SKILL.md, tool.yaml, MCP server), los
**niveles de confianza** y qué implica cada uno, el flujo desde
`/admin/marketplace/private`, **qué deriva el servidor** (y por tanto no puedes
falsificar) y **cómo se siembra el catálogo oficial** de arranque.

Para la referencia exhaustiva de endpoints, RLS y seguridad ver
[`../04-reference/marketplace.md`](../04-reference/marketplace.md); para el ADR
de fondo, [ADR 0032](../05-architecture-decisions/0032-marketplace-confianza-catalogo-hibrido-instalacion-gated.md).

> **TL;DR**: en `/admin/marketplace` pulsa **«Publicar»** → llegas a
> `/admin/marketplace/private`. Elige el **tipo** (skill / tool / MCP server),
> pega el **manifest** (o pulsa **«Usar ejemplo»** para partir de uno válido) y
> publica. El servidor valida el manifest; si está mal, te dice **qué** falla
> (422) y **no** crea nada. Tu listing nace **privado** y **community**: solo lo
> ve tu tenant.

## El modelo en una frase

El marketplace es un **catálogo híbrido**:

- los listings **oficiales** los siembra la plataforma — son **`verified`**,
  **globales** (los ve todo tenant) y los publica el equipo de plataforma;
- los listings de **tenant** los publicas tú desde la UI — son **`community`**,
  **privados** (solo los ve tu tenant) y están aislados por RLS.

Los dos mundos **no se mezclan**: un tenant nunca puede publicar algo global ni
marcarlo como `verified` (lo impide la RLS y la derivación en servidor, ver más
abajo). Lo que publicas siempre es un listing privado community de tu tenant.

## Formatos de manifest

Hay **dos formatos**, según lo que publiques. El tipo que eliges en la UI debe
coincidir con el formato que pegas:

| Tipo elegido | Formato del manifest                 | Parser            |
| ------------ | ------------------------------------ | ----------------- |
| `skill`      | **SKILL.md** (Markdown)              | `skill_format.py` |
| `tool`       | **tool.yaml** (YAML)                 | `tool_format.py`  |
| `mcp_server` | **tool.yaml** con `kind: mcp_server` | `tool_format.py`  |

### SKILL.md — una skill (capacidad declarativa)

Inspirado en Anthropic Skills: un Markdown cuyo **head es un frontmatter YAML**
(delimitado por líneas `---`) con la metadata legible por máquina, seguido del
**cuerpo Markdown** de documentación en prosa.

```markdown
---
name: internal-reporter
description: Genera el informe interno semanal del equipo.
version: 1.0.0
dependencies:
  - httpx>=0.27
permissions:
  allowed_paths: [/workspace/reports]
  network_policy: none
examples:
  - title: Informe semanal
    prompt: "Genera el informe de la semana 23"
---

# Internal Reporter

Skill interna del tenant que recopila métricas y produce el informe
semanal en /workspace/reports.

## Uso

Indica la semana y la skill genera el documento.
```

- **Obligatorios**: `name`, `description`, `version` (semver, p. ej. `1.0.0`).
- **Opcionales**: `dependencies` (lista), `permissions`
  (`allowed_domains` / `allowed_paths` / `network_policy`), `examples` (lista de
  `{ title, prompt }`).
- El frontmatter ausente o malformado, un semver inválido o una clave de permiso
  fuera del vocabulario son un **422** al publicar (no se crea fila).

### tool.yaml — una tool (función ejecutable)

Una tool es una función ejecutable: su manifest es un **YAML plano** (sin cuerpo
Markdown) con un schema de entrada/salida y un **puntero a la implementación**.

```yaml
name: internal-fetch
version: 1.0.0
description: Descarga una URL interna y devuelve su cuerpo.
kind: tool
entrypoint: internal_fetch.main:run
implementation:
  runtime: python
  module: internal_fetch.main
  reference: git+https://git.interno.test/tools/internal-fetch@v1.0.0
dependencies:
  - httpx>=0.27
permissions:
  allowed_domains: [api.interno.test]
  network_policy: restricted
input_schema:
  type: object
  properties:
    url: { type: string }
  required: [url]
output_schema:
  type: object
  properties:
    status: { type: integer }
    body: { type: string }
```

- **Obligatorios**: `name`, `version` (semver), `description`, `entrypoint`
  (`módulo:función`), `implementation.runtime`.
- **Opcionales**: `kind` (por defecto `tool`), `implementation.module` /
  `implementation.reference`, `dependencies`, `input_schema`, `output_schema`,
  `permissions`.

### MCP server

Un MCP server usa el **mismo YAML que una tool**, con `kind: mcp_server`. El
`kind` del manifest **debe coincidir** con el tipo que elegiste en la UI (un
`tool` pegado bajo «MCP server», o al revés, es un **422**).

```yaml
name: internal-mcp
version: 1.0.0
description: Servidor MCP interno que expone las herramientas del equipo.
kind: mcp_server
entrypoint: internal_mcp.server:main
implementation:
  runtime: node
  module: internal_mcp.server
  reference: npm:@interno/mcp-server@1
permissions:
  allowed_domains: [mcp.interno.test]
  network_policy: restricted
```

El vocabulario de permisos (`allowed_domains` / `allowed_paths` /
`network_policy` ∈ `none | restricted | open`) y la validación de semver están
**compartidos** entre los dos formatos (`marketplace/_format_common.py`), así
que no divergen.

## Niveles de confianza

El nivel de confianza gobierna **los guardrails que se aplican al instalar, no
la disponibilidad**: todo listing se puede navegar e instalar; el nivel solo
decide cuántas puertas impone el install.

| Nivel          | Quién lo publica         | Firma | Consent. por permiso | Sandbox | Qué implica al instalar                                     |
| -------------- | ------------------------ | ----- | -------------------- | ------- | ----------------------------------------------------------- |
| `verified`     | Equipo de plataforma     | sí    | no                   | no      | Instala `enabled`; guardrails mínimos                       |
| `community`    | Un tenant (tus listings) | no    | **sí**               | sí      | Nace `disabled` hasta que el owner concede **cada** permiso |
| `experimental` | Tercero no verificado    | no    | **sí**               | sí      | Como community + tolerancia cero a findings del análisis    |

- Lo que **tú publicas** es siempre **`community`** (derivado en servidor). No
  puedes auto-asignarte `verified`: esa marca es exclusiva del equipo de
  plataforma y va firmada criptográficamente.
- Un listing community/experimental **nace `disabled`** al instalarse: el
  project owner aprueba **uno a uno** los permisos que pide (`allowed_domains`,
  `allowed_paths`, `network_policy`) y solo cuando están todos concedidos pasa a
  `enabled`. Por eso conviene declarar en el manifest **solo los permisos que
  realmente necesita** la skill/tool.

## Publicar un listing privado

1. Entra en `/admin/marketplace` y pulsa el CTA **«Publicar»** (visible para
   `tenant_admin`). Llegas a `/admin/marketplace/private`.
2. Elige el **Tipo** (skill / tool / MCP server). La ayuda de formato inline
   lista los campos obligatorios y opcionales de ese tipo.
3. Pega el **manifest**, o pulsa **«Usar ejemplo»** para insertar uno válido y
   editarlo desde una base que ya publica correctamente.
4. (Opcional) Rellena **Autor**.
5. Pulsa **«Publicar»**. El nombre y la versión se leen del manifest.

Si el manifest está mal formado, verás un **error claro con el mensaje del
parser** (qué campo falta, semver inválido, permiso desconocido…) y **no se
crea ninguna fila**. Corrige según el mensaje y vuelve a publicar.

Re-publicar la **misma** `(tipo, nombre, versión)` es un **409**: sube la
versión (semver) o usa la actualización del listing.

### Qué deriva el servidor (no se puede falsificar)

Aunque el manifest lo pegas tú, varios campos los **fija el servidor** y nunca
se toman del wire:

| Campo         | Valor derivado                                        |
| ------------- | ----------------------------------------------------- |
| `tenant_id`   | El tenant del llamante (la RLS `WITH CHECK` lo exige) |
| Fuente        | La fuente **privada** de tu tenant                    |
| `trust_level` | **`community`** (nunca `verified` desde un tenant)    |
| Firma         | `null` (los privados no se firman)                    |

`name` / `version` / `description` / `manifest` / permisos **sí** salen del
manifest validado. La consecuencia: un tenant **no puede** publicar un listing
global ni hacerlo pasar por `verified`, y **nunca** ve ni toca los privados de
otro tenant (aislamiento por RLS).

### Despublicar

En `/admin/marketplace/private`, cada listing propio tiene un botón
**«Despublicar»** (solo `tenant_admin`). Despublicar es un **soft-delete**: la
fila se retira del catálogo pero la auditoría y las claves foráneas sobreviven.
Los listings **oficiales** (globales, verified) **no** se pueden despublicar
desde un tenant.

## RBAC

| Acción                      | Rol mínimo      |
| --------------------------- | --------------- |
| Navegar el catálogo / leer  | `tenant_member` |
| Publicar un listing privado | `tenant_admin`  |
| Despublicar / actualizar    | `tenant_admin`  |

La UI gatea estas acciones con `<RoleGuard min="tenant_admin">`, pero la barrera
real está en el backend (RBAC) + RLS: el frontend solo configura.

## Cómo se siembra el catálogo oficial

El catálogo **de arranque** lo siembra la plataforma con el loader
`seed_marketplace_listings` (`marketplace/seed.py`), cableado en el runner de
seeds (`seeds/__main__.py`). Estos listings son **`verified` + globales** (los
ve todo tenant vía la RLS de catálogo) y se publican bajo la fuente
`official-catalog`:

- la tool **Playwright** (reusa `seed_playwright_listing`), y
- un conjunto de **skills** de convenciones de stack derivadas de los docs de la
  plataforma: FastAPI, React/Next.js, PHP/Symfony, PostgreSQL y diseño de APIs
  REST.

El seed es **idempotente**: re-ejecutarlo **no duplica**. Cada listing se
_upserta_ por su identidad estable `(fuente, tenant_id=NULL, nombre, versión)`;
un re-seed refresca la metadata en sitio en vez de crear filas nuevas. Como
escribe filas globales (`tenant_id NULL`), corre sobre la sesión publicadora
**BYPASSRLS** (no una sesión de tenant). No hay migración: un listing es una
fila + un `manifest` JSONB; los SKILL.md son **datos** del seed, el loader es el
**código**.

> Diferencia clave para no confundirse: **tú publicas community/privado**; la
> **plataforma siembra verified/global**. Mismo formato de manifest, distinto
> origen y distinta confianza.
