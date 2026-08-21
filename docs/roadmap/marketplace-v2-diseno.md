---
title: "Marketplace v2 — diseño: del catálogo muerto al despliegue con tres capas"
status: published
created: 2026-07-31
approved_by: operador
docs_language: es
---

# Marketplace v2 — el último tramo del marketplace

> **Diseño aprobado por el operador el 2026-07-31**, tras una sesión de
> brainstorming con seis decisiones de producto registradas abajo. Este documento
> es el QUÉ y el POR QUÉ; el plan de implementación (fichero aparte, mismo
> directorio) es el CÓMO y el CUÁNDO. La decisión arquitectónica central —la
> entidad de despliegue y el reparto de la configuración en tres capas— se
> formaliza como **ADR 0142** en la fase 0 del plan, siguiendo la regla de la
> casa («lo gated va por ADR primero»).

## 1. El problema, medido en el código

El marketplace de hoy (planes 09/09.1, ADR 0032/0100) llega hasta el catálogo y
**muere ahí**:

- `install_listing` no escribe ni una fila en `agent_tools` ni en
  `agent_skills` (grep en todo el flujo de instalación: cero referencias).
  Instalar un tool no se lo da a ningún agente.
- Un listing `mcp_server` instalado materializa una fila `Tool` del catálogo,
  pero **no configura ningún servidor MCP en ningún proyecto**: esa config vive
  en `Project.mcp_servers` (JSONB, con su OAuth por proyecto del ADR 0127) y el
  marketplace ni la toca.
- La config guiada de Playwright —el listing estrella— se pide **al instalar** y
  se guarda **a nivel de tenant**. Dos errores a la vez, señalados por el
  operador: el _grano_ (la `base_url` del sitio a probar es del proyecto, no del
  tenant: el proyecto A prueba `app-a.example` y el B `app-b.example`, pero solo
  hay una config) y el _momento_ (los proyectos que usarán la capacidad **aún no
  existen** cuando se instala).

En una frase: **instalar hoy es comprar sin recibir**. Es el mismo patrón que la
auditoría del workflow de proyectos llamó «el fallo del último tramo»: las
piezas existen, el cableado final no.

Lo que SÍ funciona y este diseño conserva sin tocar: el modelo híbrido de
confianza (`verified`/`community`/`experimental`, ADR 0032), el consentimiento
granular por permiso con auditoría append-only, la materialización con
provenance y dedupe (ADR 0100, opción c) y su corte por `implementation_type`
(los tipos que exigen sandbox de código siguen como «intent» hasta que exista
esa infra — este diseño NO la desbloquea ni lo pretende).

## 2. Decisiones de producto (operador, 2026-07-31)

| #   | Decisión                                                                                                                                                                                                                    |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D1  | **Propósito híbrido**: catálogo oficial curado por plataforma + publicación entre equipos internos. Los dos flujos completos.                                                                                               |
| D2  | **Instalar NO es el momento del cableado**: los proyectos se crean después de la instalación. Instalar añade la capacidad al fondo del tenant.                                                                              |
| D3  | **El wizard de proyecto ofrece las capacidades instaladas** al crear: lo marcado queda configurado y asignado desde el día 1.                                                                                               |
| D4  | **Proyectos vivos: las dos vías** — despliegue central en lote desde la ficha de la instalación Y activación local desde las pestañas del proyecto. Ambas escriben la misma config.                                         |
| D5  | **Roles destino: el manifest sugiere (`targets`), quien despliega confirma o ajusta.** Sin `targets`, se elige a mano.                                                                                                      |
| D6  | **Publicar pasa por revisión del system admin** antes de ser visible (`pending_review` → community/verified/rechazado, con auditoría). Nada entra al catálogo sin ojos.                                                     |
| D7  | **Versiones: actualización explícita con re-consentimiento del delta de permisos.** Los despliegues se refrescan al confirmar; rollback desde el histórico. Nada se actualiza solo.                                         |
| D8  | **La configuración se reparte en tres capas** (ver §3): el manifest declara el esquema, la instalación solo consiente permisos, el despliegue captura los valores por proyecto. El formulario guiado se muda al despliegue. |

## 3. La arquitectura elegida: el despliegue como entidad

De los tres enfoques valorados, el operador aprobó el primero:

1. **`marketplace_deployment` como entidad de primera clase** _(elegido)_ — la
   cadena completa queda trazable: listing → instalación → despliegue → filas
   concretas. Las tres puertas de despliegue escriben la misma entidad, así que
   no pueden divergir; «¿dónde está desplegado esto?» es un SELECT; actualizar
   y retirar saben exactamente qué tocar.
2. _Cablear directo sin entidad nueva_ — rechazado: ahorra una tabla hoy y la
   paga en cada retirada/actualización; las dos superficies de UI pueden
   divergir en silencio; «¿dónde está esto?» exige escanear tablas heterogéneas.
3. _Todo vía plantillas de proyecto_ — rechazado: no resuelve los proyectos
   vivos ni el despliegue puntual; contradice D4.

### Las tres capas de configuración (D8)

| Capa            | Qué guarda                                                                                                       | Cuándo se pide                |
| --------------- | ---------------------------------------------------------------------------------------------------------------- | ----------------------------- |
| **Listing**     | qué ES la cosa: manifest, permisos declarados, `config_schema` (qué campos existen), defaults, `targets` (roles) | al publicar                   |
| **Instalación** | el consentimiento de permisos del tenant — nada más                                                              | al instalar                   |
| **Despliegue**  | los VALORES por proyecto: `base_url`, credenciales (Vault/OAuth), timeouts…, validados contra el `config_schema` | al desplegar en cada proyecto |

`config_schema` ya existe para Playwright (embebido en su manifest y renderizado
como formulario); se generaliza a los tres tipos de listing y **el formulario se
muda del instalar al desplegar**, pre-rellenado con los defaults del manifest y
distinto en cada proyecto.

## 4. Modelo de datos

Dos tablas nuevas y un adelgazamiento:

**`marketplace_deployments`** _(nueva; RLS por tenant con FORCE, como toda tabla
con `tenant_id`)_

- `installation_id` FK, `project_id` FK, `tenant_id`
- `config` JSONB — los valores, validados contra el `config_schema` de la
  versión desplegada. **Los secretos NUNCA van aquí**: van a Vault y el JSONB
  guarda el puntero, siguiendo el patrón existente.
- `role_map` JSONB — qué roles del equipo reciben qué (derivado de `targets`
  más los ajustes de quien despliega)
- `deployed_version` — la versión del listing que este despliegue tiene aplicada
- `status`: `active` / `disabled` / `retired` (la fila se conserva para auditoría)
- `deployed_by`, timestamps
- De cada despliegue cuelgan con provenance las filas concretas que creó: la
  entrada en `Project.mcp_servers` y/o las filas `agent_tools`/`agent_skills`.
  Retirar deshace exactamente eso, y nada más.

**`marketplace_listing_versions`** _(nueva)_ — una fila por versión publicada:
snapshot del manifest, permisos declarados, `config_schema`, changelog, quién la
publicó y quién la revisó. El listing apunta a su versión vigente; **la
instalación pina la versión que consintió** (el re-consentimiento de D7 compara
la pinada con la nueva).

**`marketplace_installations`** _(adelgaza)_ — conserva el consentimiento
(granted/denied por permiso, ya existente) y pierde la config guiada. Migración
de datos: si alguna instalación existente lleva config de Playwright, se
convierte en un despliegue sobre el proyecto que la use o se descarta con aviso
en el log de migración — no hay despliegues hoy, así que el caso esperado es el
vacío.

**Manifest** — gana dos campos opcionales: `targets: [rol, …]` (D5) y
`config_schema` (generalizado). Los manifests existentes sin ellos siguen
siendo válidos: sin `targets` no se pre-marca nada; sin `config_schema` el
despliegue no muestra formulario.

## 5. Flujos

### Publicar → revisar (D1, D6)

Máquina de estados del listing: `draft → pending_review → published | rejected`,
más la promoción `published → verified` reservada al system admin. Cada
transición escribe en la auditoría append-only existente (actor + motivo en los
rechazos). Los skills publican con `SKILL.md`, las tools con el YAML estándar
(task_09_10), los MCP con comando/imagen + permisos + `config_schema`. La cola
de revisión es una vista del admin con diff del manifest cuando es una versión
nueva de algo ya publicado.

### Instalar (D2)

Consentir permisos → materialización ADR 0100 (sin cambios) → capacidad en el
fondo del tenant. **Sin formulario de config.** Los tipos gated siguen gated.

### Desplegar (D3, D4, D5, D8) — el tramo nuevo

Tres puertas, un solo servicio:

1. **Wizard de proyecto**, paso «Capacidades»: lista lo instalado, se marca qué
   activar; lo marcado se despliega al crear el proyecto.
2. **Ficha de la instalación**, «Desplegar a…»: selección múltiple de proyectos
   existentes; el mismo sitio lista dónde está desplegado y permite retirar.
3. **Pestañas MCP/Tools del proyecto**, sección «disponibles del tenant»:
   activación local.

El servicio de despliegue: renderiza el formulario del `config_schema` (defaults
del manifest), pre-marca los roles de `targets`, valida, y materializa según el
tipo — `mcp_server` → entrada en `Project.mcp_servers` (si declara OAuth, el
flujo «Conectar» del ADR 0127 se encadena a continuación); `tool`/`skill` →
asignación a los agentes del equipo según `role_map`. Idempotente: re-desplegar
sobre un proyecto que ya lo tiene es no-op con aviso.

**Sin política paralela**: para un `mcp_server`, el `role_map` del despliegue se
materializa escribiendo la política rol→tool **que ya existe** por proyecto
(`projects.mcp_tool_roles`, ADR 0128 fase 2) — el despliegue es quien la rellena,
no un mecanismo competidor. Una sola fuente de verdad de «qué rol usa qué tool
MCP», gobernada desde el despliegue y visible donde siempre.

### Actualizar (D7)

Versión nueva publicada → pasa la misma revisión → las instalaciones que pinan
una versión anterior muestran el banner con el **diff de permisos**. El admin
re-consiente **solo el delta** y, al confirmar, los despliegues se refrescan:
campos nuevos del `config_schema` toman su default, los retirados se limpian, y
una rotura de esquema (un campo requerido sin default) se señala ANTES de
aplicar, despliegue por despliegue. Rollback = volver a pinar la versión
anterior desde el histórico, con el mismo mecanismo de refresco.

## 6. UI

- **Catálogo**: badges de confianza con tooltip en lenguaje llano, versión
  vigente, y la cola de revisión para el admin.
- **Ficha de instalación**: estado, versión pinada, «desplegado en N proyectos»
  con la lista, botones desplegar/retirar, banner de actualización.
- **Wizard de proyecto**: el paso «Capacidades».
- **Pestañas del proyecto**: sección «disponibles del tenant».
- Órdenes permanentes de UX del operador que aplican: formularios guiados en vez
  de YAML crudo, grupos con etiquetas humanas, chips y presets donde toque, y
  todo textarea con vista previa Markdown.

## 7. Seguridad

Sin conceptos nuevos — este diseño **reutiliza** las defensas existentes:
niveles de confianza que gradúan guardrails (ADR 0032), consentimiento granular
(que ahora además se re-ejerce en cada delta de versión), categorías de
aprobación sensibles intactas (una tool desplegada sigue pasando por el gate de
aprobación que le toque), RLS con FORCE en las tablas nuevas, secretos solo en
Vault, y auditoría append-only en publicar/revisar/instalar/desplegar/actualizar
/retirar. El aislamiento cross-tenant del despliegue lleva test de integración
obligatorio (una instalación del tenant A no puede desplegarse en un proyecto
del tenant B, ni verse).

## 8. Fuera de alcance, a propósito

- Ratings, reseñas y contadores sociales.
- Federación con marketplaces externos o publicación pública.
- Auto-update en cualquier variante (D7 lo rechaza).
- El sandbox para tools con código propio: sigue gated por la infra que el ADR
  0100 nombra (sandbox out-of-process, registry de artefactos, clave de firma).
  Este diseño no lo toca ni lo necesita.

## 9. Criterios de éxito y tests

El test que da sentido a todo lo demás es **la cadena entera en integración**:

> publicar → revisar/aprobar → instalar (consentir) → desplegar en un proyecto →
> **el agente del rol destino tiene la tool asignada y el proyecto tiene el MCP
> en su config** → retirar → todo limpio.

Más: aislamiento cross-tenant del despliegue; idempotencia de re-despliegue;
re-consentimiento que solo pide el delta; refresco de despliegues en
actualización y rollback; rechazo de config que no valida contra el
`config_schema`; unit de las dos máquinas de estados (listing y despliegue);
vitest del paso del wizard, del formulario de despliegue y de la cola de
revisión; e2e Playwright escritos (la ejecución exige navegador y queda para el
entorno que lo tenga).

## 10. Fases previstas (detalle en el plan)

0. **ADR 0142** (despliegue + tres capas) — formalizar esta decisión.
1. **Esquema y servicio de despliegue** con la cadena de integración en verde
   (sin UI): la fase que convierte «comprar» en «recibir».
2. **Las tres puertas de UI** (wizard, ficha, pestañas).
3. **Publicar → revisar** (máquina de estados + cola del admin).
4. **Versiones** (tabla de versiones, pin, re-consentimiento del delta,
   refresco, rollback).
5. **Migración de Playwright** al nuevo reparto (config al despliegue) y
   actualización de la doc de referencia + changelog.
