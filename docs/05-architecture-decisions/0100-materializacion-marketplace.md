---
adr: "0100"
title: "Materialización del marketplace: puente install→catálogo, provenance y gates"
status: accepted
date: 2026-07-03
deciders: operador (pendiente)
phase: auditoria-plataforma-2026-07-03
related: ["0081", "0019", "0032", "0049", "0050", "0066", "0067"]
docs_language: es
---

# ADR 0100 — Materialización del marketplace: puente install→catálogo, provenance y gates

## Contexto

La auditoría de plataforma 2026-07-03 (`docs/roadmap/tools-y-cierre-plan-fixes.md`,
hallazgo **g3**, línea 41) reconfirma el gap que ADR 0081 ya había registrado y diferido:
**instalar un listing del marketplace no produce ninguna capacidad que un agente pueda
invocar**. El hallazgo se marca como _«deuda aceptada y transparente»_ y _«no urgente»_
(línea 58: _«ya cubierto por ADR 0081 Fase B/C; ADR candidato 0100 si se quiere
adelantar»_). Este ADR existe para decidir **cómo** se materializaría si se adelanta —
no para forzar que se adelante.

Evidencia en el repo (HEAD `3d22337`):

- **El install fresco no materializa nada.** `POST /marketplace/installations`
  (`apps/api-server/src/api_server/routers/marketplace.py:932`) hace su propio consent +
  persistencia inline: sólo inserta una fila `MarketplaceInstallation`
  (`:932-944`) + un `MarketplaceAuditEntry` (`:956-972`). El comentario en
  `:910-914` lo dice literalmente: \*«DEFERRED to Phase B/C (ADR 0081): run the pre-install
  gates … blocked on an out-of-process sandbox runner (the api-server has no Docker socket)
  - the artifact registry.»\*
- **Los gates de seguridad no corren en el install fresco.** `_run_security_gates`
  (`apps/api-server/src/api_server/marketplace/install.py:482`) sólo se alcanza desde
  `InstallOrchestrator.install()` (`:334`) y `.update()` (`:430`). El endpoint fresco **no
  enruta por el orquestador**, así que fetch → parse → firma → análisis estático → sandbox
  nunca se aplican al alta; sólo el gate de _consent_ (un listing no-verified aterriza
  `DISABLED` sin permisos, `routers/marketplace.py:924-930`).
- **El catálogo nativo no tiene provenance.** `Tool`
  (`apps/api-server/src/api_server/db/domain.py:526`) y `Skill` (`:485`) sólo llevan
  `is_builtin`; **no** hay columna que enlace una fila a su origen del marketplace
  (`source_listing_id` / `source_installation_id`). El idioma de trazabilidad ya existe en
  el dominio para forks: `Agent.forked_from_agent_id` (`:467`), `Team.forked_from_team_id`
  (ADR 0066, migración 0086) — pero no se ha extendido a `tools`/`skills`.
- **El propio modelo ya desmiente la usabilidad.** `InstallationStatus.ENABLED`
  (`apps/api-server/src/api_server/db/marketplace.py:130-145`) documenta que `enabled`
  _«records intent + permission, NOT a live capability … an agent can[not] actually invoke
  it»_. El copy es honesto (fix de ADR 0081); el gap no es un defecto oculto.

**Por qué materializar una fila no basta.** Para que un agente invoque una capacidad hacen
falta tres cosas, no una: (1) una fila `Tool`/`Skill` en el catálogo del tenant
(materialización), (2) su asignación al agente (`agent_tools`/`agent_skills`), y (3) que el
runtime **pueda ejecutarla**. El tercer punto es el que ata este ADR a la infraestructura:

- Un **skill** es `prompt_fragment` (`domain.py:510`): texto que se inyecta en el system
  prompt. **No ejecuta código**: materializarlo es seguro y barato.
- Un **tool** con `implementation_type ∈ {mcp_tool, http_endpoint}`
  (`domain.py:562-564`) ejecuta por **red**, y esa vía ya está cubierta por la infra
  existente (cliente MCP + egress guardrails, ADR 0067/0094).
- Un **tool** con `implementation_type ∈ {python_function, docker_command}` ejecuta
  **código arbitrario** → necesita exactamente el sandbox out-of-process que el api-server
  no tiene (Principio 2 / ADR 0019) y que ADR 0081 difirió. Para los builtins, la ejecución
  además exige estar en `RUNTIME_WIRED_TOOL_NAMES`
  (`packages/shared-domain/src/shared_domain/tool_names.py:109`); un nombre custom no
  cableado se anuncia al LLM pero el `ToolRegistry` lo rechaza en tiempo de llamada.

Es decir: **la línea donde aparece la dependencia de infra dura no es «skill vs tool», es
el `implementation_type`.** Ese es el corte natural de la decisión.

## Decisión (aprobada)

Adoptar la **Opción (c): materialización parcial cortada por `implementation_type`/trust**,
implementada como un puente transaccional install→catálogo con provenance, en tres piezas
que pueden secuenciarse (la primera es independiente y no activa el feature):

1. **Migración de provenance (schema-only, reversible, sin cambio de comportamiento).**
   Añadir a `tools` y `skills` tres columnas nullable, espejo del idioma `forked_from_*`:
   `source_listing_id` (FK `marketplace_listings` ON DELETE SET NULL),
   `source_installation_id` (FK `marketplace_installations` ON DELETE SET NULL) y
   `source_version` (TEXT). Índice parcial `WHERE source_installation_id IS NOT NULL`. Esta
   migración puede aterrizar **ya**, sola, sin tocar ningún path de ejecución: da trazabilidad
   y desbloquea el resto sin comprometer a activar la materialización.

2. **Paso de materialización transaccional**, disparado al pasar una instalación a `ENABLED`,
   **restringido al slice sin sandbox de arranque**:
   - **Materializa ahora**: listings `kind=skill`; y `kind=tool`/`mcp_server` cuyo
     `implementation_type` resuelto sea `mcp_tool` o `http_endpoint`. Upsert de la fila
     `Tool`/`Skill` del tenant instalador desde el manifest, con `category` **válida del
     catálogo cerrado** (ADR 0049 para tools, ADR 0050 para skills), nombre resuelto contra
     el índice `uq_tools_tenant_name` (`domain.py:543`) — colisión → sufijo determinista o
     422 explícito, nunca insert silencioso — y `source_*` poblado.
   - **Difiere** (no materializa; queda `ENABLED`-intent documentado, sin fila de catálogo):
     `implementation_type ∈ {python_function, docker_command}`, hasta que exista el sandbox
     out-of-process (Fase B/C completa de ADR 0081).
   - `uninstall`/`revoke` hace **soft-delete de la fila materializada en la misma
     transacción** (test de no-orfandad), reutilizando el `deleted_at` del `SoftDeleteMixin`.

3. **Gates aplicables al slice materializado** (los que **no** exigen Docker), enrutando el
   endpoint fresco por `InstallOrchestrator.install()` como ya hace `update`: fetch → parse →
   firma (`verified`) → análisis estático. Se **omite el gate de sandbox de arranque** para
   este slice porque su `implementation_type` no ejecuta código arbitrario; el gate de
   sandbox se reserva para el slice diferido. Para el **catálogo oficial**, el
   `LocalArtifactFetcher` (`install.py:152`) lee su root (`MARKETPLACE_ARTIFACT_ROOT`,
   `install.py:695`) poblado en el seed/imagen — no requiere un registry vivo. La firma
   `verified` exige la clave pública de plataforma; se provee vía Vault
   (`MARKETPLACE_SIGNING_PUBLIC_KEY`).

**Gating explícito** (memoria «gated→ADR primero», «sin big-bang»): la pieza 1 es aterrizable
de inmediato; las piezas 2 y 3 quedan **GATED** tras dos prerequisitos baratos —
(i) el artifact root poblado para el catálogo oficial (seed) y (ii) la clave de firma en
Vault— y su activación es decisión del operador. Dado que en dev hay **0 instalaciones** y no
hay promesa rota, no hay urgencia: este ADR fija el diseño correcto para cuando se quiera
capacidad viva, sin regresión.

## Opciones evaluadas

| Opción                                                                                                                 | Pros                                                                                                                                                                                                                                                                                                                                                                                                                              | Contras                                                                                                                                                                                                                                                                                                                                                                                                          |
| ---------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **(a) Materialización completa + cablear TODOS los gates en el install fresco** (Fase B/C entera de ADR 0081 de golpe) | Cierra el gap por completo; feature real para todo el catálogo; reutiliza el `InstallOrchestrator` ya testeado; provenance auditable.                                                                                                                                                                                                                                                                                             | Bloqueado por **tres** piezas de infra a la vez (sandbox out-of-process, registry de artefactos vivo, clave de firma en Vault); cablear los gates **sin** esa infra es una **regresión** — todo install → 422 — que es exactamente lo que ADR 0081 rechazó; y aun materializando un tool con código, sigue **sin poder ejecutarse** sin el executor/sandbox (vuelve al mismo gap). Big-bang.                     |
| **(b) Mantener el diferimiento de ADR 0081** (copy honesto, no materializar) hasta tener toda la infra                 | Cero regresión; es el estado `accepted` actual; honesto; 0 instalaciones en dev → nadie afectado; coste cero.                                                                                                                                                                                                                                                                                                                     | El feature sigue sin producir capacidad viva; la deuda queda abierta indefinidamente; no aporta ni siquiera la trazabilidad (provenance) que es barata y útil por sí sola.                                                                                                                                                                                                                                       |
| **(c) Materialización parcial cortada por `implementation_type`/trust** (RECOMENDADA)                                  | Entrega capacidad viva **hoy** para la mayoría del catálogo (skills + tools MCP/HTTP) **sin** el sandbox de arranque (la pieza de infra más dura, Principio 2); el corte por `implementation_type` es justo donde nace la necesidad de sandbox; provenance desde el día uno; reutiliza el orquestador; **no regresa** (los tipos sin infra no se materializan); la migración de provenance es un slice reversible aterrizable ya. | Dos caminos (materializable vs diferido) → más complejidad de código y de UI (hay que señalar qué instalaciones son «vivas» y cuáles «intent»); el path `verified` sigue necesitando la clave de firma en Vault y el artifact root poblado; el análisis estático de un skill (inyección en `prompt_fragment`) es un check distinto del `bandit` de código (guardrail `pre_llm`, no análisis estático de fuente). |
| **(d) Materializar sin gates para listings `verified`/oficiales, gates para el resto**                                 | Mínimo esfuerzo para el catálogo oficial de confianza; evita depender de Vault a corto plazo.                                                                                                                                                                                                                                                                                                                                     | Confía en el `trust_level` como sustituto de verificación real → si el artifact root se corrompe o se re-apunta, se materializa código no verificado; rompe el invariante _fail-closed_ del orquestador (`install.py` docstring); descartada por seguridad, misma familia que el _fail-open_ que la auditoría penaliza en g6.                                                                                    |

## Consecuencias

**Si se acepta (c):**

- **Cambia el esquema**: nueva migración (reversible) que añade `source_listing_id`,
  `source_installation_id`, `source_version` a `tools` y `skills`, con índices parciales.
  Es la única pieza que puede mergearse sin activar nada; da trazabilidad y satisface el
  punto 4 del «Plan de Fase B/C» de ADR 0081 de forma incremental.
- **Cambia el flujo de install** (piezas 2-3, GATED): el endpoint
  `routers/marketplace.py:932` deja de persistir inline y enruta por
  `InstallOrchestrator.install()`, unificando la lógica de consent/persistencia hoy duplicada
  entre router y orquestador (deuda que ADR 0081 §5 ya señalaba). El orquestador gana un
  parámetro para **saltar el gate de sandbox** cuando el `implementation_type` no ejecuta
  código, y un **paso de materialización** en la transacción de `ENABLED`.
- **Reutiliza infra existente**: la ejecución de los tools materializados (mcp_tool/
  http_endpoint) ya está cubierta por el cliente MCP + egress guardrails (ADR 0067/0094); no
  se introduce ninguna vía de ejecución nueva en el api-server (Principio 2 intacto: el
  api-server sigue sin socket Docker).
- **Deja explícitamente pendiente** el slice de tools con código
  (`python_function`/`docker_command`): siguen sin materializarse hasta que exista el sandbox
  out-of-process (la Fase B/C completa de ADR 0081, punto 1). Este ADR **no** deroga ADR 0081;
  lo **estrecha**: convierte «todo diferido» en «el subconjunto que necesita sandbox,
  diferido».
- **Plan de remediación que lo implementa**: un plan futuro (candidato, no en este ADR) con
  fases — F1 migración de provenance (aterrizable ya) · F2 seed del artifact root oficial +
  clave de firma en Vault · F3 materialización + gates sin-sandbox · F4 UI que distingue
  instalación «viva» de «intent» · (F5 = slice con sandbox, sigue bajo ADR 0081).
- **Riesgos/migraciones**: la migración debe ser reversible (Principio: no `up -d --build`
  sin migración reversible verificada); el paso de materialización debe ser idempotente
  (re-install/re-enable no duplica filas) y resolver colisión de nombre contra
  `uq_tools_tenant_name`; `revoke` debe des-materializar en la misma transacción para evitar
  filas huérfanas o capacidades «vivas» tras revocar.

**Si se rechaza** (se queda en (b)): sin acción; g3 permanece como deuda consciente de
ADR 0081. Aceptable dado el impacto nulo actual (0 instalaciones), pero se pierde la
trazabilidad barata de la pieza 1 y el marketplace sigue sin ser un feature utilizable.

## Criterio de aceptación

1. **Pieza 1 (provenance)**: migración `up`/`down` reversible verificada; `tools` y `skills`
   exponen `source_listing_id`/`source_installation_id`/`source_version` nullable; una fila
   built-in y una fila normal existente conservan `NULL` (cero cambio de comportamiento); test
   de que el FK `ON DELETE SET NULL` no rompe la fila materializada si se borra el listing.
2. **Pieza 2 (materialización)**: un install `ENABLED` de un skill oficial crea una fila
   `Skill` del tenant con `category` válida (ADR 0050), `prompt_fragment` del manifest y
   `source_*` poblado; un agente con ese skill asignado lo ve en su system prompt. Un install
   de un tool `mcp_tool`/`http_endpoint` crea una fila `Tool` invocable (aparece en el
   allowlist del dispatch y el runtime la ejecuta por la vía MCP/HTTP existente). Un install de
   un tool `python_function`/`docker_command` **no** crea fila de catálogo y queda marcado como
   diferido. `uninstall`/`revoke` soft-borra la fila materializada en la misma transacción:
   **test de no-orfandad** (no queda `Tool`/`Skill` viva sin instalación viva, ni viceversa).
3. **Pieza 3 (gates)**: un artefacto oficial manipulado (firma inválida) → install `verified`
   → **422** con audit de abort (fail-closed); un skill/tool MCP con manifest inválido →
   rechazado en gate 2; el gate de sandbox de arranque **no** se ejecuta para el slice
   materializable (no requiere Docker) y el api-server sigue sin socket Docker.
4. **Aislamiento cross-tenant**: un install del tenant A materializa filas **sólo** bajo RLS de
   A; el tenant B no ve la `Tool`/`Skill` resultante (test cross-tenant en CI, Principio 1).
5. **No regresión**: con las piezas 2-3 desactivadas (gate del operador), el comportamiento es
   idéntico al de HEAD `3d22337` (sólo la pieza 1 de esquema presente).

## Estado de implementación (2026-07-13)

OPCION (c) IMPLEMENTADA (mandato del operador «abordamos estos puntos»). Pieza 1: migracion **0111** (tools+skills: source_listing_id/source_installation_id/source_version, FK SET NULL, indice parcial). Pieza 2: `marketplace/materialize.py` — al pasar a ENABLED (install fresco verified o flip de consent) upsert transaccional de la fila nativa: kind=skill -> Skill (prompt_fragment obligatorio, categoria del catalogo cerrado con fallback honesto); tool/mcp_server con implementation_type de RED (mcp_tool/http_endpoint) -> Tool (security_level sandboxed por defecto); python_function/docker_command -> DIFERIDO honesto sin fila (deferred_reason al audit) hasta el sandbox ADR 0081 B/C. Idempotente por source_installation_id (re-enable resucita); colision de nombre -> sufijo determinista -mkt-XXXXXX; manifest invalido -> 422 y el enable aborta entero. Des-materializacion en uninstall/revoke (\_revoke_installation) y al quedarse DISABLED tras consent — la capacidad no sobrevive a su permiso (tests de no-orfandad). Pieza 3: el gate de analisis estatico YA corria en el install fresco (task_prod12_mkt_01); el gate de FIRMA verified sigue pendiente de la clave MARKETPLACE_SIGNING_PUBLIC_KEY en Vault (prerequisito de despliegue, no de codigo). Integracion 4/4.
