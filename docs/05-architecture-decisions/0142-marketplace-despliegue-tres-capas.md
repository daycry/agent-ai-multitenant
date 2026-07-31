---
title: "ADR 0142: El despliegue del marketplace como entidad, y la configuración repartida en tres capas"
status: accepted
date: 2026-07-31
deciders: [operador]
relates_to: [0032, 0081, 0100, 0127, 0128]
plan_referenced: marketplace-v2-despliegue
task: [task_mkt2_00]
docs_language: es
---

# ADR 0142: El despliegue como entidad y la config en tres capas

> **Nace `accepted`, no `proposed`.** Registra una decisión que el operador ya
> tomó el 2026-07-31 en la sesión de diseño de
> [`marketplace-v2-diseno.md`](../roadmap/marketplace-v2-diseno.md) (decisiones
> D1-D8, dos enfoques rechazados). Escribirlo como pendiente sería el pecado
> documental de esta casa: un ADR `proposed` que en realidad describe algo ya
> decidido y ya en construcción.

## Contexto: el marketplace muere en el catálogo

El marketplace de hoy (planes 09/09.1, ADR 0032 y 0100) llega hasta el catálogo
y para ahí. Medido en el código, no inferido:

1. **`install_listing` no escribe ni una fila en `agent_tools` ni en
   `agent_skills`.** Cero referencias a esas tablas en todo el flujo de
   instalación (`marketplace/install.py`, `marketplace/materialize.py`,
   `routers/marketplace.py`). Instalar una tool la materializa en el catálogo
   del tenant (`tools`/`skills`, ADR 0100) y **no se la da a ningún agente**.
2. **Un listing `mcp_server` instalado no configura ningún servidor MCP en
   ningún proyecto.** Esa configuración vive en `Project.mcp_servers` (JSONB,
   con su OAuth por proyecto del ADR 0127) y el marketplace no la toca.
3. **La config guiada de Playwright se pide al instalar y se guarda a nivel de
   tenant.** Dos errores a la vez: el _grano_ (la `base_url` del sitio a probar
   es del proyecto — el proyecto A prueba `app-a.example` y el B
   `app-b.example`, pero solo hay una config) y el _momento_ (los proyectos que
   usarán la capacidad **aún no existen** cuando se instala).

En una frase: **instalar hoy es comprar sin recibir**. Es la misma forma que la
auditoría del workflow de proyectos llamó «el fallo del último tramo» y que
[`verificar-antes-de-implementar.md`](../03-guides/verificar-antes-de-implementar.md)
§5 describe como el patrón dominante de esta base: mecanismo entregado, cero
llamantes.

## Decisión

### 1. El despliegue es una entidad de primera clase

Se crea **`marketplace_deployments`**: una fila por (instalación, proyecto). La
cadena entera queda trazable —listing → versión → instalación → despliegue →
las filas concretas que materializó— y con ella:

- «¿dónde está desplegado esto?» es un `SELECT`, no un escaneo de tablas
  heterogéneas;
- las tres puertas de despliegue (wizard de proyecto, ficha de la instalación,
  pestañas del proyecto) **escriben la misma entidad**, así que no pueden
  divergir en silencio;
- actualizar y retirar saben **exactamente** qué tocar, porque cada fila creada
  queda anotada en `created_refs`.

Y se crea **`marketplace_listing_versions`**: una fila por versión publicada
(snapshot del manifest, permisos, `config_schema`, changelog, quién publicó y
quién revisó). La instalación **pina** la versión que consintió
(`pinned_version_id`), que es lo que permite comparar «lo consentido» con «lo
vigente» y re-consentir solo el delta.

#### Enfoques rechazados

| Enfoque                                | Por qué no                                                                                                                                                                                                                          |
| -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Cablear directo, sin entidad nueva** | Ahorra una tabla hoy y la paga en cada retirada y cada actualización: sin `created_refs` no se sabe si una fila `agent_tools` la puso el despliegue o el operador a mano. Las dos superficies de UI pueden divergir sin que se vea. |
| **Todo vía plantillas de proyecto**    | No resuelve los proyectos **vivos** ni el despliegue puntual, y contradice la decisión D4 (las dos vías).                                                                                                                           |

### 2. La configuración se reparte en tres capas

| Capa            | Qué guarda                                                                               | Cuándo se pide                |
| --------------- | ---------------------------------------------------------------------------------------- | ----------------------------- |
| **Listing**     | qué ES la cosa: manifest, permisos declarados, `config_schema`, defaults, `targets`      | al publicar                   |
| **Instalación** | el consentimiento de permisos del tenant — **nada más**                                  | al instalar                   |
| **Despliegue**  | los **VALORES** por proyecto (`base_url`, punteros a credenciales, timeouts…), validados | al desplegar en cada proyecto |

El formulario guiado que hoy se rinde **al instalar** se muda **al desplegar**,
pre-rellenado con los defaults del manifest y distinto en cada proyecto. Eso es
lo que hace expresable el caso que el modelo viejo no podía: dos proyectos del
mismo tenant con `base_url` distinta para la misma capacidad instalada.

El manifest gana dos campos **opcionales**: `targets` (roles sugeridos) y
`config_schema` (generalizado desde Playwright a los tres tipos de listing). Un
manifest sin ellos sigue siendo válido — retro-compatibilidad con todo lo
publicado: sin `targets` no se pre-marca nada, sin `config_schema` el despliegue
no muestra formulario.

### 3. Los secretos no entran en el `config` del despliegue

`marketplace_deployments.config` es JSONB, y por tanto **no es sitio para un
secreto**. Un campo declarado `secret: true` en el `config_schema` solo acepta un
**puntero a Vault** (prefijo `vault:`) — el mismo contrato que
`MCPServerConfigModel.auth_ref` ya exige (`mcp/config.py`). El validador rechaza
un valor en claro **sin ecoarlo** en el mensaje de error: un error de validación
que imprime el secreto lo copia al log.

### 4. Sin política paralela: el `role_map` escribe `projects.mcp_tool_roles`

Ésta es la regla que evita la peor de las regresiones posibles de este ADR.

Para un listing `mcp_server`, el `role_map` del despliegue («qué roles del
equipo reciben esto») **se materializa escribiendo la política rol→tool que ya
existe por proyecto**: `projects.mcp_tool_roles` (ADR 0128 fase 2). El
despliegue es **quien la rellena**, no un mecanismo competidor.

Queda prohibido, y esta prohibición es normativa:

- crear una tabla o columna nueva de «roles autorizados de una tool MCP»;
- que el runtime consulte el `role_map` del despliegue para decidir si un agente
  puede usar una tool MCP. El runtime sigue leyendo lo de siempre
  (`filter_mcp_tools_by_role_policy` sobre `project.mcp_tool_roles`).

Una sola fuente de verdad de «qué rol usa qué tool MCP», gobernada desde el
despliegue y visible donde el operador ya la mira.

### 5. La retirada es EXACTA

`retire_deployment` borra **exactamente** lo que `created_refs` dice que el
despliegue creó, y nada más. El caso que lo justifica: si el operador asignó a
mano la misma tool al mismo agente, retirar el despliegue **no se la lleva**.
Sin `created_refs` esa distinción es indecidible, y el modo de fallo es
silencioso (una capacidad que desaparece de un agente sin que nadie lo pidiera).

La fila del despliegue **no se borra**: pasa a `retired` y conserva su
auditoría. Igual que una instalación revocada.

## Consecuencias

**A favor**

- Instalar deja de ser comprar sin recibir: hay un test de integración que va de
  publicar a que el agente tenga la tool y el proyecto el MCP.
- Retirar y actualizar dejan de ser adivinanzas.
- El aislamiento cross-tenant del despliegue es RLS de PostgreSQL, no lógica de
  servicio: `marketplace_deployments` nace con `ENABLE` + `FORCE` + política
  `tenant_isolation`, como toda tabla con `tenant_id` en este repo.

**En contra / coste**

- Dos tablas más y una columna nueva en `marketplace_installations`
  (`pinned_version_id`). El backfill es obligatorio: cada listing existente pare
  su fila de versión y cada instalación pina esa fila.
- La UI crece en tres sitios (las tres puertas). El formulario del
  `config_schema` es una pieza reutilizada, no tres copias.
- `PlaywrightToolConfig` pervive como **la validación tipada** que su
  `config_schema` declara, invocada desde el validador de despliegue — no desde
  el install. Mudar sus datos existentes es una migración de datos con caso
  esperado vacío (hoy no hay despliegues).

**Lo que este ADR NO desbloquea**

El sandbox para tools con código propio sigue **gated** por la infra que el ADR
0081 nombra (sandbox out-of-process, registry de artefactos, clave de firma).
Los `implementation_type` `python_function` / `docker_command` siguen diferidos
en la materialización, y desplegarlos no los hace ejecutables. Este ADR no toca
esa línea ni lo pretende.

## Alternativas de configuración descartadas

- **La config en la instalación (statu quo)**: no expresa dos proyectos con
  valores distintos, y se pide antes de que existan los proyectos.
- **La config en el proyecto, sin entidad de despliegue**: pierde la trazabilidad
  al listing y a la versión, y deja la retirada sin contrato.

## Relación con otros ADR

- **ADR 0032** (modelo híbrido de confianza) — intacto: el nivel de confianza
  sigue graduando guardrails, no la disponibilidad.
- **ADR 0100** (materialización con provenance y dedupe) — el despliegue
  **reutiliza** las filas `Tool`/`Skill` que la instalación ya materializó; no
  crea catálogo nuevo.
- **ADR 0127** (conector OAuth genérico) — un `mcp_server` que declara OAuth
  nace `pending_connection` en el proyecto y el flujo «Conectar» lo completa. El
  despliegue no finge que quedó vivo.
- **ADR 0128** (tools MCP aportadas por el proyecto) — **extendido por este
  ADR**: el `role_map` del despliegue rellena `projects.mcp_tool_roles`, la
  política que 0128 introdujo. Su estado no cambia.
- **ADR 0081** (lo que un install NO garantiza) — sigue vigente para los tipos
  gated.
