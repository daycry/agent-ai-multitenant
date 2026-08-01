---
plan_id: prod-16-frontend-i18n-calidad
title: "Frontend: i18n ES+EN real y partición de componentes"
status: pending_approval
blocking_plan: null
started_at: null
completed_at: null
estimated_duration_calendar: 15-20 días laborables (3-4 semanas)
estimated_effort_person_days: 18.5
estimated_cost_human_eur: 8.300 € – 11.100 €
estimated_cost_ai_eur: 80 € – 150 €
created_by: auditoria-claude-2026-06
spec_sections_referenced: []
docs_language: es
priority: P2
---

# Plan prod-16 — Frontend: i18n ES+EN real y partición de componentes

## Cabecera

| Campo                              | Valor                           |
| ---------------------------------- | ------------------------------- |
| **ID del Plan**                    | `prod-16-frontend-i18n-calidad` |
| **Prioridad**                      | P2                              |
| **Bloqueado por**                  | — (ninguno; ver coordinación)   |
| **Tiempo estimado (calendario)**   | 15-20 días laborables           |
| **Tiempo estimado (persona-días)** | 18,5                            |
| **Rama git sugerida**              | `plan/prod-16-frontend-i18n`    |

> **Estado**: la fuente de verdad es el frontmatter YAML de este fichero (`status:`). El campo duplicado que había en esta tabla se retiró en prod-15 (hallazgo docsroadmap-6): se había desincronizado en 22 de 51 planes.

Plan correctivo nº 16 (último) de la serie derivada de la auditoría integral
de producción de 2026-06-10. Cierra los hallazgos **frontend-9**,
**frontend-10** y **quality-7**.

---

## Resumen

El admin-panel es disciplinado en lo formal (strict TS, cero `any`, cliente
API centralizado) pero arrastra tres deudas de calidad verificadas:

1. **El i18n ES+EN del principio nº 12 de CLAUDE.md es cosmético**
   (frontend-9): existe `LanguageProvider` + toggle en el header, pero
   `useLang()` solo se consume en 15 de ~150 ficheros, las traducciones son
   63 ternarios inline `lang === "es" ? … : …` repartidos en 12 ficheros, el
   login mezcla inglés ("Sign in", "Invalid email or password.") con español
   ("Panel de administración") y `app/layout.tsx:25` declara
   `<html lang="en">` fijo aunque el default real es ES. Cambiar a EN deja el
   ~90 % del panel en español.
2. **Componentes monstruo y duplicación** (frontend-10): diez `page.tsx`
   superan las 800 líneas (model-prices 1311, mcp-servers 1105,
   plans/[planId] 1079…), el helper `errorText` está copiado byte a byte en
   13 ficheros y además pinta el body crudo del backend en la UI, y el script
   `generate:api-types` (openapi-typescript → `types/api.ts`) apunta a un
   directorio que no existe: cada página redeclara sus interfaces a mano con
   riesgo de drift frente al esquema real.
3. **Seis ficheros Python entre 1300 y 1506 LOC** (quality-7): `db/domain.py`
   (1506), `routers/sso.py` (1494, mezcla OIDC+SAML), `routers/agents.py`
   (1414), `workers/backup_destinations.py` (1392), `routers/marketplace.py`
   (1380) y `pricing/litellm_sync.py` (1338). Crecerán con las fases
   pendientes; partirlos hoy es barato, en 3 fases no lo será.

Este plan introduce un **diccionario i18n central tipado** y migra todo el
panel pantalla a pantalla, **trocea las 10 páginas >800 líneas** siguiendo el
patrón de secciones colocadas ya usado en `agents/[id]/*-section.tsx`,
**unifica `errorText`** en `lib/api.ts` con humanización del detail, **adopta
(o retira) los tipos generados de OpenAPI**, y — si el equipo aprueba la
decisión D4 — **parte los 6 ficheros Python** señalados.

## Alcance

**Entra**:

- Infraestructura i18n: `lib/i18n/` con diccionarios `Record<Lang, …>` por
  módulo, helper `useT()`, sincronización del atributo `lang` del `<html>`
  con el idioma activo, y guard de CI contra regresión (ternarios inline y
  strings hardcodeados nuevos).
- Migración i18n de TODO el panel (~150 ficheros) en lotes por módulo,
  empezando por login + shell.
- `errorText` único en `lib/api.ts` (humanizando el `detail` Pydantic) y
  borrado de las 13 copias.
- Partición de las 10 páginas >800 líneas, con guard de tamaño en CI.
- Decisión y ejecución sobre `types/api.ts` generado de OpenAPI (adoptar con
  check de drift, o eliminar el script para no sugerir una garantía falsa).
- Partición de `routers/sso.py` y `db/domain.py`; las otras 4 particiones
  Python son recortables (decisión D4).

**Queda fuera** (cubierto por otros planes de la serie):

- Manejo global de 401, cookie httpOnly, callback SSO del panel y purga de
  caché en logout (frontend-1..4) → **prod-09-sesiones-autorizacion-frontend**.
- Ejecutar vitest/Playwright en CI (frontend-7) → **prod-02-ci-en-verde**.
  Los tests automáticos de este plan asumen ese job ya existente.
- Cabeceras de seguridad en `next.config.js` (frontend-6) → prod-09/prod-01.
- Upgrade de next 14.2.5 → 14.2.35 (quality-1) → **prod-11-cadena-suministro**.
- Idiomas adicionales a ES+EN (prohibido por principio nº 12) y preferencia
  de idioma por usuario persistida en BD (follow-up post-plan).
- `apps/web-app` (placeholder `.gitkeep`, sin código que migrar).

## Decisiones clave

- **D1 — Enfoque i18n**. Opciones: (A) retirar el toggle hasta tener
  cobertura real; (B) diccionario central tipado hecho a mano
  (`Record<Lang, …>` por módulo, sin librería); (C) adoptar next-intl/i18next
  con routing por locale. **Recomendación: B** — el principio nº 12 cierra el
  catálogo a ES+EN, es un panel interno sin SEO y una librería con routing
  por locale es sobrecoste; B además conserva el tipado estricto (una clave
  que falta en EN no compila). Si el humano eligiera A (decisión de
  producto: renunciar temporalmente a EN), requiere **ADR propuesto** porque
  matiza el principio nº 12 — no la toma este plan.
- **D2 — Persistencia del idioma**: mantener default ES + `localStorage`
  (estado actual de `lang-context.tsx`). Preferencia por usuario en BD queda
  como follow-up. Sin objeción esperada.
- **D3 — Tipos de API**: (A) adoptar `types/api.ts` generado, versionado en
  git, con check de drift en CI y migración piloto de 2 páginas; (B) eliminar
  el script `generate:api-types` de `package.json`. **Recomendación: A** —
  las interfaces a mano ya divergen silenciosamente; si el equipo rechaza A,
  ejecutar B para no mantener un script muerto.
- **D4 — Alcance de la Fase C (Python)**: el auditor jefe permite recortarla.
  **Recomendación**: mantener al menos `sso.py` (crecerá con prod-09) y
  `domain.py` (crece con cada migración); `task_prod16_12` (los 4 restantes)
  es el candidato natural a recorte si hay presión de calendario. Decidir al
  aprobar el plan.

## Tareas

### Fase A — i18n ES+EN real (frontend-9)

#### `task_prod16_01` — Infraestructura i18n: diccionario tipado + `useT()` + `<html lang>` + guard

- [x] **Título**: Crear `apps/admin-panel/lib/i18n/` con tipos `Lang`/claves
      por módulo, hook `useT(namespace)` sobre el `LanguageProvider`
      existente (`lib/lang-context.tsx`), efecto que sincroniza
      `document.documentElement.lang` con el idioma activo (hoy
      `app/layout.tsx:25` fija `lang="en"`), y script
      `apps/admin-panel/scripts/check-i18n.mjs` que falla ante nuevos
      ternarios `lang === "es" ?` fuera de `lib/i18n/` (allowlist decreciente
      versionada con los ficheros aún no migrados).
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod16_01_a
    runtime: node-jest
    command: "npm --prefix apps/admin-panel run test -- lib/i18n"
  - id: auto_prod16_01_b
    runtime: node-jest
    command: "node apps/admin-panel/scripts/check-i18n.mjs"
  ```

#### `task_prod16_02` — Migrar login, shell y rutas de sesión

- [ ] **Título**: Migrar al diccionario `app/login/page.tsx` (hoy mezcla
      "Sign in"/"Invalid email or password." con "Panel de administración",
      líneas 49/71/110), `app/select-tenant/`, `app/no-access/`,
      `components/layout/admin-header.tsx` y la sidebar; eliminar los 63
      ternarios inline de los 12 ficheros que ya "traducen" a mano,
      moviéndolos a claves del diccionario.
  - ⏳ **Pendiente (2026-08-01):** login, `select-tenant`, `no-access`, header y shell YA usan `useT()`, y los ternarios inline bajaron de **77 en 18 ficheros a 44 en 11** (siete módulos de `lib/` migrados: `capability/hub`, `memory/honesty`, `tools/taxonomy`, `runtime-templates`, `cortex-curiosity`, `cortex-identity`, `persona/persona`). Lo que falta para cerrar: los 44 restantes, todos en `components/capability/*` (25), `agents/*` (10), `tools/page.tsx` (4), `agent-tools-diagnostic` (3) y `cortex/mind` (1) — y el spec `e2e/lang-toggle.spec.ts` que el plan exige, que sigue sin existir y es Playwright (no ejecutable sin stack levantado). El equivalente en vitest SÍ existe ya para login (`app/login/i18n.test.tsx`).
  - ⏳ **Re-verificada (2026-08-01), y sigue abierta con razón:** la **primera**
    mitad del enunciado está hecha y comprobada fichero a fichero —
    `app/login/page.tsx` no tiene ni un literal (todo `t(...)`, incluidos los
    tres errores de credenciales/rate-limit/red), y `select-tenant`, `no-access`,
    `admin-header` y `admin-shell` importan `useT`. `app/layout.tsx:29` ya declara
    `lang="es"`, no el `lang="en"` fijo que denunciaba el hallazgo. Los 6 tests de
    `app/login/i18n.test.tsx` cubren las dos caras y que en EN no queda castellano.
    La **segunda** mitad («eliminar los 63 ternarios inline») no: quedan **34 en 8
    ficheros**, y ninguno está en este carril — `components/capability/*` (25),
    `app/admin/tools/page.tsx` (4), `projects/[id]/agent-tools-diagnostic` (3),
    `cortex/mind` (1) y `components/teams/adopt-team-dialog.tsx` (1).
    Además el test que el plan nombra (`e2e/lang-toggle.spec.ts`) no existe con ese
    nombre; **sí existe `e2e/lang-switcher.spec.ts`**, que cubre lo mismo (presencia
    del selector, default ES, cambio a EN, persistencia tras recarga) y es Playwright,
    así que no se puede ejecutar sin stack levantado. La casilla no se marca: no la
    cierra este carril.
- **Tiempo**: 1 día · **Complejidad**: m · **Depende de**: `task_prod16_01`
- **Tests automáticos**:
  ```yaml
  - id: auto_prod16_02_a
    runtime: node-jest
    command: "npm --prefix apps/admin-panel run e2e -- e2e/lang-toggle.spec.ts"
  ```

#### `task_prod16_03` — Migrar módulos núcleo de administración

- [ ] **Título**: Migrar al diccionario las pantallas de mayor uso: `users`,
      `tenants`, `tenant-stats`, `backup/*` (3 páginas), `settings/*`,
      `projects/*` y `agents/*` (las dos últimas con sus secciones). Reducir
      la allowlist de `check-i18n.mjs` en cada lote.
  - ⏳ **Pendiente (2026-08-01):** migradas **al completo** `users`, `backup/*` (las 3 páginas), `tenant-stats` y dos de `settings/`
    (`security`, `hourly-rate`), con tests de render en los dos idiomas
    (`app/admin/backup/i18n.test.tsx`, `app/admin/settings/i18n.test.tsx`,
    `app/admin/tenant-stats/i18n.test.tsx`) y fuera ya de la `ATTR_ALLOWLIST`.
    Faltan `projects/*` y `agents/*`.
    **Dos correcciones al enunciado del plan**, verificadas contra el código:
    (a) `tenants` NO existe como pantalla — no hay ningún `app/admin/tenants/`,
    la gestión de tenants vive dentro de `users` (diálogo de memberships), así
    que esa casilla del enunciado no tiene destino;
    (b) `settings/page.tsx` y `settings/memories/page.tsx` **NO se pueden
    migrar sólo en el frontend**: sus títulos y descripciones los sirve el
    backend en `label_es`/`description_es`
    (`apps/api-server/src/api_server/settings_registry.py`) y no existe el par
    `_en`. Traducir sólo el marco dejaría la pantalla mitad en inglés y mitad
    en castellano, que es justo el fallo que este plan cierra. Requiere añadir
    `label_en`/`description_en` al registry (Python) antes de tocar el panel.
    Su test (`e2e/lang-toggle-core.spec.ts`) sigue siendo Playwright y no existe.
- **Tiempo**: 2 días · **Complejidad**: l · **Depende de**: `task_prod16_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_prod16_03_a
    runtime: node-jest
    command: "npm --prefix apps/admin-panel run e2e -- e2e/lang-toggle-core.spec.ts"
  ```

#### `task_prod16_04` — Migrar el resto del panel y barrido final

- [ ] **Título**: Migrar los módulos restantes (~100 ficheros: marketplace,
      guardrails, knowledge-bases, llm-providers, model-prices, ollama,
      notifications, docs, assistant, tools, memories…) en lotes, vaciar la
      allowlist de `check-i18n.mjs` y hacer barrido final: con el toggle en
      EN no debe quedar ninguna cadena de UI en castellano (y viceversa).
  - ⏳ **Pendiente (2026-08-01):** `node scripts/check-i18n.mjs --strict` sigue saliendo 1, pero el contador bajó otra vez: de **44 ternarios en 11 ficheros a 34 en 8**, y de **127 atributos en 68 ficheros a 115 en 62**. Migrados **al completo** dos de los módulos que enumera esta tarea: `llm-providers` (namespace `llmProviders`, con la tabla, el diálogo de alta/edición y el Device Flow de Copilot) y `model-prices` (namespace `modelPrices`, con la tabla, los filtros y los tres diálogos), más el módulo `agents` entero (catálogo, hub, sección de tools y el buscador de skills). Tests en `app/admin/llm-providers/i18n.test.tsx`, `app/admin/agents/i18n.test.tsx` y `app/admin/model-prices/i18n.test.tsx`: cada uno rinde su pantalla en los DOS idiomas y afirma que en EN no queda castellano por debajo.
    **Un cambio de comportamiento, deliberado y con test**: `promptIn` (tarjeta del catálogo de agentes) pasó de un ternario a `pickLang`, que cae al otro idioma cuando el pedido viene **vacío**, no sólo cuando falta. Un agente con `system_prompts.en: ""` pintaba una tarjeta sin prompt teniéndolo en castellano.
    Siguen sin migrar: marketplace, guardrails, knowledge-bases, ollama, notifications, docs, assistant, tools, memories.
    **Por qué knowledge-bases no entró aunque el guard sólo le marque 3 atributos**: esos 3 son la punta de ~2100 líneas de castellano cableado repartidas en 5 ficheros. Traducir sólo los atributos deja la pantalla mitad en inglés y mitad en castellano, que es exactamente el fallo que este plan cierra (mismo razonamiento que ya obligó a parar en `settings/`).
  - ⏳ **Pendiente (2026-08-01, segunda pasada):** **`knowledge-bases` entra ENTERO**, que era lo que la pasada anterior dejó explícitamente aparcado. Los cinco ficheros migrados a dos namespaces nuevos (`knowledgeBases`, `kbCategories`, 130 claves): `page.tsx`, `kb-row.tsx`, `kb-category-select.tsx`, `kb-form-dialogs.tsx`, `kb-danger-dialogs.tsx`, `kb-documents-panel.tsx`, `kb-assignments-dialog.tsx` y `categories/page.tsx`. Test: `app/admin/knowledge-bases/i18n.test.tsx` (17 casos) rinde la pantalla en los DOS idiomas incluidos **los cuatro diálogos y el panel de documentos plegado**, que es donde vive la mitad del texto y donde un `useT()` olvidado no se ve hasta que alguien despliega una fila.
    Contadores: **34 ternarios en 8 ficheros** (sin cambio: los 34 restantes están todos fuera de este carril, en `components/capability/*`, `agents/*`, `tools/` y `cortex/mind`) y **113 → 110 atributos, 61 → 58 ficheros**.
    **Lo que enseñó medir esto**: el guard marcaba 3 atributos en un módulo con ~2.100 líneas sin traducir. Se comprobó a mano por qué — reintroducir `title="Dar acceso a un proyecto"` en un fichero ya migrado **NO hace fallar al guard**, porque la frase no lleva ni una tilde; con `title="Crear categoría nueva"` sí falla (exit 1). El patrón sólo ve atributos con carácter exclusivo del castellano: mide la deuda detectable, no la deuda. Anotado en el propio `check-i18n.mjs`.
    Siguen sin migrar: marketplace, guardrails, ollama, notifications, docs, assistant, tools, memories.
- **Tiempo**: 3 días · **Complejidad**: l · **Depende de**: `task_prod16_03`
- **Tests automáticos**:
  ```yaml
  - id: auto_prod16_04_a
    runtime: node-jest
    command: "node apps/admin-panel/scripts/check-i18n.mjs --strict"
  ```

### Fase B — Partición frontend, `errorText` y tipos de API (frontend-10)

#### `task_prod16_05` — `errorText` único en `lib/api.ts` con humanización

- [x] **Título**: Extraer el helper duplicado en 13 ficheros (users, backup
      ×3, llm-providers, model-prices, tenant-stats, marketplace, guardrails,
      eval-quality, ollama, platform-defaults, assistant/model-cards) a
      `lib/api.ts`. La versión común parsea el body JSON, extrae el `detail`
      Pydantic legible y nunca pinta el cuerpo crudo; las claves de error van
      al diccionario i18n. Borrar las 13 copias.
  - 🔎 **Fuga encontrada y tapada (2026-08-01):** quedaba una 14ª copia sin
    migrar. `app/admin/settings/hourly-rate/page.tsx` seguía pintando
    `mutation.error.body` **crudo** en pantalla, que es justo lo que esta tarea
    prohíbe. Sustituido por `useErrorText()`. La casilla estaba marcada `[x]`
    con el defecto vivo: el conteo "13 copias" del enunciado se quedó corto.
  - 🔎 **Y seis fugas más, en el mismo módulo (2026-08-01, segunda pasada):**
    `knowledge-bases` pintaba `error.body` crudo en **seis** sitios —
    `page.tsx` y `categories/page.tsx` (el error de la query) y los **cuatro**
    `onError` de `kb-assignments-dialog.tsx` (revocar/conceder × proyecto/agente).
    Todos a `useErrorText()`. Las 14 copias que el enunciado contaba eran las
    que se llamaban `errorText`; estas seis hacían lo mismo escrito en línea
    (`err instanceof ApiError ? err.body : String(err)`), que es la forma que no
    sale al buscar por el nombre de la función.
  - 🔎 **Y la copia nº 15, con otro nombre (2026-08-01):** `notifications`
    tenía la misma función llamada `apiErrorBody`, usada en **seis** puntos
    (error de plataforma, guardar plataforma, listar canales, guardar canal,
    listar preferencias, guardar preferencia). Todos a `useErrorText()` al
    trocear la pantalla. **Lección**: buscar por el NOMBRE de la función no
    encuentra la deuda; buscar por lo que hace (`ApiError ? .body`) sí. El
    conteo del enunciado ("13 copias") era un censo de nombres.
- **Tiempo**: 0,5 días · **Complejidad**: s · **Depende de**: `task_prod16_01`
- **Coordinación**: prod-09 añade manejo global de 401 en `apiFetch`; si se
  ejecuta antes, rebasar sobre su versión de `lib/api.ts`.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod16_05_a
    runtime: node-jest
    command: "npm --prefix apps/admin-panel run test -- lib/api-error.test.ts"
  ```

#### `task_prod16_06` — Partir `model-prices/page.tsx` (1311 líneas)

- [x] **Título**: Trocear `app/admin/model-prices/page.tsx` en secciones
      colocadas (tabla, diálogos de edición, sync, filtros) siguiendo el
      patrón existente de `app/admin/agents/[id]/*-section.tsx`. Objetivo:
      `page.tsx` < 400 líneas, ninguna sección > 500.
  - ✅ **Cerrada (2026-08-01)**: los dos objetivos numéricos, cumplidos.
    `page.tsx` **514 → 253** y la sección mayor **686 → 289**. El módulo pasó de
    3 ficheros a 7: `price-filters.tsx` (162), `price-table.tsx` (202),
    `sync-diff-dialog.tsx` (289), `price-form-dialog.tsx` (264),
    `price-history-dialog.tsx` (197) y `model-price-types.ts` (173).
    El tramo #9 (`679b4237`) había sacado los tres diálogos del monolito pero
    los dejó juntos en un solo fichero de 686 líneas: mover el bulto no es
    partir, y por eso la casilla seguía abierta con razón.
    **El troceo fue mecánico y con red**: `page.test.tsx` (5 tests de
    caracterización de la tabla, el histórico, el gate >10% del sync y el
    formulario) estaba verde antes de mover una línea y siguió verde después,
    sin tocar ni una aserción. El test que el plan pedía es Playwright y no se
    puede ejecutar sin stack; esta es la verificación que sí se pudo correr.
- **Tiempo**: 1 día · **Complejidad**: m · **Depende de**: `task_prod16_05`
- **Tests automáticos**:
  ```yaml
  - id: auto_prod16_06_a
    runtime: node-jest
    command: "npm --prefix apps/admin-panel run e2e -- e2e/admin-models-prices.spec.ts"
  ```

#### `task_prod16_07` — Partir `mcp-servers` (1105) y `plans/[planId]` (1079)

- [ ] **Título**: Mismo tratamiento para
      `app/admin/projects/[id]/mcp-servers/page.tsx` y
      `app/admin/projects/[id]/plans/[planId]/page.tsx`. Refactor mecánico:
      sin cambios de comportamiento, los specs e2e existentes deben pasar sin
      tocar sus aserciones.
  - ⏳ **Pendiente (2026-07-31):** las dos páginas ya estaban partidas por el tramo de modularización #9 (`39072e22` y `415a2578`, 2026-07-09/10), no por prod-16: `mcp-servers/page.tsx` 249 líneas y `plans/[planId]/page.tsx` 153, pero `mcp-server-sections.tsx` quedó en 1125 líneas — ahí solo se movió el bulto — y la verificación que pide el plan es Playwright.
- **Tiempo**: 1,5 días · **Complejidad**: m · **Depende de**: `task_prod16_06`
- **Tests automáticos**:
  ```yaml
  - id: auto_prod16_07_a
    runtime: node-jest
    command: "npm --prefix apps/admin-panel run e2e -- e2e/mcp-servers.spec.ts e2e/plan-detail.spec.ts"
  ```

#### `task_prod16_08` — Partir las 7 páginas restantes >800 líneas + guard de tamaño

- [ ] **Título**: knowledge-bases (1042), llm-providers (951),
      settings/sso/saml (943), tenant-stats (862), settings/sso (842),
      agents/[id] (813) y notifications (810). Añadir a `check-i18n.mjs` (o
      script hermano `check-component-size.mjs`) un guard que falle si algún
      `page.tsx` supera 800 líneas, para que la deuda no vuelva a crecer.
  - ⏳ **Pendiente (2026-08-01, segunda pasada):** de las ocho que quedaban por
    encima de 800 caen **dos más**, las dos partidas de verdad y con test:
    · **llm-providers 996 → 89**, en `providers-table.tsx` (361),
    `provider-form-dialog.tsx` (333), `copilot-device-flow-dialog.tsx` (210) y
    `llm-provider-types.ts` (97).
    · **agents/[id] 824 → 312**, en `agent-edit-dialog.tsx` (266),
    `agent-fork-dialog.tsx` (152), `agent-delete-dialog.tsx` (105) y
    `agent-detail-types.ts` (87).
    Las dos se migraron al diccionario en el mismo movimiento, así que sus tests
    (`i18n.test.tsx` de cada módulo) verifican a la vez la traducción y que
    ninguna pieza se quedó sin montar tras el corte — un diálogo que deje de
    abrirse salta ahí y no en producción.
    Quedan **seis** por encima de 800: sso/saml 943, projects/[id]/chat 926,
    sso 915, teams/[team_id] 914, cortex/mind 914 y notifications 831.
  - ⏳ **Pendiente (2026-08-01):** el guard **ya existe**:
    `scripts/check-component-size.mjs` + `npm run check:size`, con 11 tests en
    `scripts/check-component-size.test.ts` (mismo trinquete que `check-i18n`:
    allowlist que sólo mengua, fichero nuevo obeso = error, fichero de la
    allowlist que CRECE = error, `--strict`, y autocomprobación contra el paso
    en vacío). Partida **tenant-stats**: 861 → `page.tsx` 69 + 6 secciones
    colocadas (mayor 284). Siguen **ocho** por encima de 800: llm-providers 996,
    sso/saml 943, projects/[id]/chat 926, sso 915, teams/[team_id] 914,
    cortex/mind 914, notifications 831 y agents/[id] 824.
    **Nota de alcance**: la lista del enunciado envejeció — `knowledge-bases`
    ya está por debajo, y `projects/[id]/chat`, `teams/[team_id]` y
    `cortex/mind` cruzaron el límite DESPUÉS de escribirse el plan. Es
    exactamente el crecimiento que el guard viene a frenar.
  - ⏳ **Pendiente (2026-08-01, tercera pasada):** cae **`notifications` 831 → 95**,
    la última de las que el enunciado nombra por su nombre. Partida en
    `channels-tab.tsx` (418), `preferences-tab.tsx` (162), `platform-tab.tsx` (112)
    y `notification-types.ts` (119).
    **La red se escribió antes de mover una línea y se comprobó que muerde**:
    `app/admin/notifications/page.test.tsx`, 13 casos de caracterización (pestañas
    por rol, alta con el config ya parseado, JSON inválido que NO llega al backend,
    edición sin selector de ámbito, borrado con confirmación, matriz evento ×
    transporte, default ON, guardar transportes de plataforma). Verde ANTES del
    corte; luego se rompió el código a propósito (`scope: "user"` → `"tenant"` en
    el upsert) y **el test se puso rojo**; restaurado, verde otra vez; y después
    del troceo sigue verde sin tocar ni una aserción.
    De paso, `kb-sections.tsx` (782, el «mover el bulto» del tramo #9) se partió
    en `kb-row.tsx` (122), `kb-category-select.tsx` (196), `kb-form-dialogs.tsx`
    (287) y `kb-danger-dialogs.tsx` (206), con `page.tsx` de knowledge-bases en 209.
    Quedan **cinco** por encima de 800: sso/saml 943, projects/[id]/chat 926,
    sso 915, teams/[team_id] 914 y cortex/mind 914 — **ninguna de este carril**.
    **Hallazgo que NO se arregló, a propósito**: `preferences-tab.tsx` pinta
    `label_es` del catálogo de eventos sin mirar el idioma, así que con el toggle
    en EN las filas de la matriz siguen en castellano teniendo `label_en` al lado.
    Es un caso de libro de `pickLang` (texto bilingüe que llega en DATOS). Se deja
    abierto porque arreglarlo suelto dejaría la pantalla mitad y mitad: entra con
    la migración de `notifications` al diccionario, en `task_prod16_04`.
  - ⏳ **El guard tenía un agujero que premiaba el atajo, y se ha tapado
    (2026-08-01):** sólo medía `page.tsx`, así que **mudar** 700 líneas del
    monolito a un solo `algo-sections.tsx` bajaba el contador sin haber partido
    nada. No era hipotético: el propio script lo admitía en un comentario
    (`mcp-server-sections.tsx`, 1125 líneas, daba OK) y lo dejaba «a ojo en
    review», que es no vigilar. `check-component-size.mjs` mide ahora **dos**
    cosas con la misma mecánica: pantallas (techo 800) y **piezas** del troceado
    —`*-section`, `*-sections`, `*-dialog(s)`, `*-tab(s)`, `*-panel`, `*-table`—
    con techo **500**, que es el que fija `task_prod16_06` («ninguna sección >
    500»). Su `SECTION_ALLOWLIST` nace con **exactamente dos** entradas
    (`mcp-server-sections.tsx` 1125 y `agent-tools-section.tsx` 691): las otras
    51 piezas del panel ya están por debajo, así que el trinquete pasa verde hoy
    y sólo puede menguar. **+7 tests** en `scripts/check-component-size.test.ts`
    (37 en total), escritos en rojo antes de la implementación, más una
    autocomprobación nueva en el script: si `SECTION_SUFFIXES` dejara de casar
    con cómo se nombran las piezas, falla en vez de pasar en vacío.
- **Tiempo**: 2,5 días · **Complejidad**: l · **Depende de**: `task_prod16_07`
- **Tests automáticos**:
  ```yaml
  - id: auto_prod16_08_a
    runtime: node-jest
    command: "node apps/admin-panel/scripts/check-component-size.mjs --max-lines 800"
  ```

#### `task_prod16_09` — Tipos de API generados desde OpenAPI (según D3)

- [ ] **Título**: Si D3=A: ejecutar `generate:api-types`, versionar
      `types/api.ts`, añadir check de drift (regenerar contra el OpenAPI del
      api-server y `git diff --exit-code`) al job de frontend de CI
      (coordinación con prod-02), y migrar como piloto las interfaces a mano
      de `llm-providers/page.tsx` (6 interfaces) y `model-prices`. Si D3=B:
      eliminar el script y la dev-dep `openapi-typescript` de
      `apps/admin-panel/package.json`.
  - ⏳ **Pendiente (2026-08-01):** D3 sigue **sin decidir** y ninguna de las dos ramas ejecutada — no existe `apps/admin-panel/types/`, y `generate:api-types` + la dev-dep `openapi-typescript` siguen en `package.json`. **No la toma este carril**: las dos ramas son decisión de producto con coste fuera del frontend. La rama A obliga a levantar el api-server para regenerar y añade un job de drift al CI (coordinación con prod-02, que no está mergeado); la rama B borra una capacidad que alguien pudo dar por hecha. Elegir una u otra cambia el flujo de build, así que la decide un humano al aprobar el plan.
- **Tiempo**: 1,5 días · **Complejidad**: m · **Depende de**: `task_prod16_08`
- **Tests automáticos**:
  ```yaml
  - id: auto_prod16_09_a
    runtime: node-jest
    command: "npm --prefix apps/admin-panel run typecheck"
  ```

### Fase C — Partición de ficheros Python ~1500 LOC (quality-7, recortable según D4)

#### `task_prod16_10` — Partir `routers/sso.py` (1494) en `sso/oidc.py` + `sso/saml.py`

- [ ] **Título**: Separar el router mixto en paquete `routers/sso/` con
      `oidc.py`, `saml.py` y `common.py` (`_issue_identity_session` y
      modelos compartidos). Refactor puro: mismas rutas, mismos
      `response_model`, tests de integración SSO existentes en verde sin
      modificarlos.
  - ⏳ **Pendiente (2026-07-31):** sin empezar — `routers/sso.py` sigue siendo un solo fichero y ha CRECIDO a 1562 líneas (el plan lo midió en 1494); no existe el paquete `routers/sso/`.
- **Tiempo**: 1 día · **Complejidad**: m
- **Coordinación**: **ejecutar después de mergear prod-09** (que modifica el
  callback OIDC/ACS para redirigir al panel) para no pisar su diff.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod16_10_a
    runtime: python-pytest
    command: "pytest tests/integration -k sso -v"
  ```

#### `task_prod16_11` — Partir `db/domain.py` (1506) por agregados

- [ ] **Título**: Dividir el modelo ORM en módulos por agregado
      (`db/models/tenancy.py`, `projects.py`, `agents.py`, `plans_tasks.py`,
      `llm.py`…), manteniendo `db/domain.py` como fachada de re-export para
      no romper los imports existentes. Verificar que `alembic` no detecta
      diferencias de esquema tras el refactor (autogenerate vacío).
  - ⏳ **Pendiente (2026-07-31):** sin empezar — `db/domain.py` sigue monolítico y ha CRECIDO a 1768 líneas (el plan lo midió en 1506); no existe el paquete `db/models/` por agregados (`db/models.py`, 1183 líneas, es otra cosa: las 5 tablas de la fase 0) ni el test `tests/integration/test_alembic_autogenerate_clean.py`.
- **Tiempo**: 1,5 días · **Complejidad**: l
- **Tests automáticos**:
  ```yaml
  - id: auto_prod16_11_a
    runtime: python-pytest
    command: "pytest tests/unit tests/integration -k 'domain or models' -v"
  - id: auto_prod16_11_b
    runtime: python-pytest
    command: "pytest tests/integration/test_alembic_autogenerate_clean.py -v"
  ```

#### `task_prod16_12` — Partir los 4 restantes (agents, backup_destinations, marketplace, litellm_sync)

- [ ] **Título**: `routers/agents.py` (1414), `workers/backup_destinations.py`
      (1392), `routers/marketplace.py` (1380) y `pricing/litellm_sync.py`
      (1338): extraer sub-módulos cohesivos (p. ej. CRUD vs diagnóstico en
      agents; un módulo por tipo de destino en backup_destinations). Tarea
      **recortable** si D4 lo decide; en ese caso documentar el aplazamiento
      en el changelog del plan.
  - ⏳ **Pendiente (2026-07-31):** sin empezar y sin decidir D4 — los cuatro ficheros siguen enteros (`agents.py` 1461, `marketplace.py` 1465, `backup_destinations.py` 1392, `litellm_sync.py` 1338), y los dos routers han crecido respecto a lo que midió el plan.
- **Tiempo**: 2 días · **Complejidad**: l · **Depende de**: `task_prod16_10`
- **Coordinación**: `backup_destinations.py` lo toca prod-04 y
  `marketplace.py` lo tocan prod-03/prod-12 — ejecutar esta tarea la última
  del plan y rebasar sobre master.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod16_12_a
    runtime: python-pytest
    command: "pytest tests/unit tests/integration -k 'agents or backup_destinations or marketplace or litellm' -v"
  ```

## Hallazgos de auditoría cubiertos

| fid         | Severidad | Tarea(s) que lo cierran                                                                  |
| ----------- | --------- | ---------------------------------------------------------------------------------------- |
| frontend-9  | medium    | `task_prod16_01`, `task_prod16_02`, `task_prod16_03`, `task_prod16_04`                   |
| frontend-10 | low       | `task_prod16_05`, `task_prod16_06`, `task_prod16_07`, `task_prod16_08`, `task_prod16_09` |
| quality-7   | low       | `task_prod16_06` (page.tsx 1311), `task_prod16_10`, `task_prod16_11`, `task_prod16_12`   |

## Riesgos

1. **Volumen de la migración i18n** (~150 ficheros): alto riesgo de claves
   perdidas o pantallas a medias. Mitigación: lotes por módulo con allowlist
   decreciente en CI y barrido final con `--strict`.
2. **Colisión con planes paralelos de la serie**: prod-09 toca `lib/api.ts`,
   login y `sso.py`; prod-04 toca `backup_destinations.py`; prod-03/prod-12
   tocan `marketplace.py`. Mitigación: orden explícito (Fase C tras prod-09;
   `task_prod16_12` la última) y rebases frecuentes.
3. **Los tests que protegen el refactor no corren aún en CI** (frontend-7,
   prod-02): si prod-02 no está mergeado, los guards y e2e de este plan solo
   se ejecutan a mano. Mitigación: declarar prod-02 como prerrequisito
   operativo en la aprobación, aunque no sea `blocking_plan` formal.
4. **Particiones de páginas con estado compartido**: extraer secciones de un
   `page.tsx` con muchos `useState`/`useQuery` entrelazados puede introducir
   bugs sutiles de render. Mitigación: refactor mecánico sin cambiar
   comportamiento + specs e2e existentes como red de seguridad.
5. **Drift del `types/api.ts` generado**: si el backend cambia el esquema sin
   regenerar, el fichero versionado miente. Mitigación: check de drift en CI
   (parte de `task_prod16_09`); sin CI viva, preferir D3=B.
6. **`domain.py`**: la partición del ORM puede crear ciclos de import o
   diferencias accidentales de metadata. Mitigación: fachada de re-export +
   test de autogenerate vacío (`auto_prod16_11_b`).

## Tests humanos del Plan

```yaml
- id: human_prod16_01
  description: "El toggle ES/EN traduce TODO el panel, no el 10%"
  hint: "Cambiar a EN en el header y recorrer el panel entero"
  checklist:
    - "Con EN activo: login, sidebar, header y dashboard 100% en inglés"
    - "Muestrear 10 pantallas (users, backup, marketplace, guardrails, kbs, llm-providers, model-prices, notifications, settings/sso, tenant-stats): cero strings en castellano"
    - "Con ES activo: las mismas 10 pantallas, cero strings en inglés"
    - "El atributo lang del <html> (inspector) cambia es/en con el toggle"
    - "El idioma elegido sobrevive a recargar la página"

- id: human_prod16_02
  description: "Errores de API legibles, sin JSON crudo en la UI"
  hint: "Provocar un error de validación (p. ej. crear un usuario con email inválido)"
  checklist:
    - "El mensaje de error es una frase legible, no un body JSON con 'detail'"
    - "El mismo error en EN aparece traducido"

- id: human_prod16_03
  description: "Las páginas particionadas funcionan igual que antes"
  hint: "Smoke manual de las 4 páginas más grandes tras el refactor"
  checklist:
    - "model-prices: listar, editar un precio, sync — sin regresiones"
    - "projects/[id]/mcp-servers: alta, edición y borrado de un MCP server"
    - "plans/[planId]: kanban de tareas del plan carga y permite mover tareas"
    - "ningún page.tsx de app/admin supera 800 líneas (wc -l)"

- id: human_prod16_04
  description: "SSO y backups intactos tras la partición Python (si Fase C entra)"
  hint: "Regresión manual mínima de los módulos refactorizados"
  checklist:
    - "Login OIDC y SAML completos funcionan igual que antes del refactor"
    - "Un backup con destino configurado se ejecuta y lista correctamente"
```

## Criterios de cierre

1. Todas las tareas del alcance aprobado con `[x]` (si D4 recorta
   `task_prod16_12`, documentar el recorte en el changelog).
2. Tests automáticos del plan en verde, incluidos los guards
   `check-i18n.mjs --strict` y `check-component-size.mjs` integrados en el
   job de frontend de CI (prod-02).
3. Los 4 tests humanos validados por un humano.
4. Entrada de changelog en
   `docs/07-changelog/prod-16-frontend-i18n-calidad.md`.
5. PR del plan mergeado a `master`.

## Próximo Plan

**Ninguno**: prod-16 es el último plan de la serie correctiva de la
auditoría 2026-06 (prod-01 … prod-16). Al cerrarlo, la serie queda completa
y el trabajo vuelve al roadmap ordinario según el protocolo de CLAUDE.md
(gobernanza y sinceramiento del roadmap en
[prod-15-gobernanza-roadmap-docs](prod-15-gobernanza-roadmap-docs.md), que
define el estado real de las fases 00-15 tras la auditoría).
