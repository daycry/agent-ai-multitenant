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

> **Dónde va la deuda medida (2026-08-10).** Las dos guardas son el progreso
> observable de este plan, así que sus números viven aquí y no en la prosa de
> cada tarea:
>
> | Métrica                            | Al escribirse el plan | 2026-08-01 | 2026-08-10 | 2026-08-12 | **2026-08-19** |
> | ---------------------------------- | --------------------: | ---------: | ---------: | ---------: | -------------: |
> | Pantallas `page.tsx` > 800 líneas  |                    10 |          5 |          0 |          0 |          **0** |
> | Piezas del troceado > 500 líneas   |                     — |          2 |          2 |          2 |          **1** |
> | Ternarios de idioma (`check-i18n`) |                    63 |         34 |          9 |          0 |          **0** |
> | Ficheros con ternarios             |                    12 |          8 |          4 |          0 |          **0** |
> | Atributos con castellano fijo      |                     — |        232 |        232 |        211 |        **188** |
> | Ficheros con atributos             |                     — |          — |         85 |         80 |         **71** |
>
> Cuatro lecturas honestas de esa tabla:
>
> 1. **La columna de pantallas está cerrada** y la `ALLOWLIST` de
>    `check-component-size.mjs` está vacía, así que el trinquete pasa de saldar
>    deuda a impedirla.
> 2. **La de ternarios también, desde el 2026-08-12.** `ALLOWLIST` de
>    `check-i18n.mjs` está **vacía**: el modo normal y `--strict` dicen ya lo
>    mismo para los ternarios. Volver a añadir una entrada ahí es reabrir la
>    deuda, y hay un test que lo afirma.
> 3. **Los atributos apenas se mueven, y decirlo importa más que el número.**
>    232 → 211 en dos olas, y 211 → **188** en las dos siguientes. Un informe que
>    sólo enseña las métricas que bajaron es peor que no tener métricas: **el
>    81 % de la deuda de atributos sigue en pie**, repartida en 71 ficheros. En
>    cuatro olas se ha saldado menos de una quinta parte, así que al ritmo actual
>    esta fila no la cierra `task_prod16_03`: la cierra `task_prod16_04`, y hay
>    que contar con varias pasadas más.
> 4. **Las dos guardas subestiman la deuda a propósito y hay que leerlas así.**
>    El guard de atributos sólo ve castellano con carácter exclusivo o con
>    palabra/sufijo de su lista (ya anotado el 08-01 y afinado el 08-02), y el de
>    ternarios contaba UNO por fichero cuando el atajo estaba bien escrito: dos
>    ficheros de `capability` y el `adopt-team-dialog` escondían veinte textos
>    cada uno detrás de un `const t = (es, en) => …` local. **Cuanto más ordenado
>    el atajo, menos lo veía la guarda.** Cero ternarios NO significa cero deuda
>    de i18n: significa que ya no queda la forma de deuda que esa señal sabe ver.

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
  - ⏳ **El CÓDIGO de esta casilla está COMPLETO (2026-08-12); lo que queda es de
    quien tenga el stack.** La segunda mitad del enunciado —«eliminar los 63
    ternarios inline»— **está cerrada**: `node scripts/check-i18n.mjs` dice
    `0 ternario(s) pendientes en 0 fichero(s)` y la `ALLOWLIST` del guard se ha
    vaciado, así que el trinquete pasa de saldar deuda a impedirla. Los cuatro
    últimos, todos en esta ola: `app/admin/tools/page.tsx` (4),
    `projects/[id]/agent-tools-diagnostic/page.tsx` (3),
    `app/admin/cortex/mind/page.tsx` (1, el respaldo del banner de honestidad) y
    `components/teams/adopt-team-dialog.tsx` (1 que escondía ~20 textos).
    **Lo que le falta a un humano para poder marcarla `[x]`:** levantar el stack y
    correr `npm --prefix apps/admin-panel run e2e -- e2e/lang-switcher.spec.ts`
    (el nombre real; el del enunciado nunca existió). Es la ÚNICA razón por la que
    sigue abierta — no queda trabajo de código en ella.
- **Tiempo**: 1 día · **Complejidad**: m · **Depende de**: `task_prod16_01`
- **Tests automáticos**:
  ```yaml
  # CORREGIDO el 2026-08-19: el comando nombraba `e2e/lang-toggle.spec.ts`, que
  # NUNCA ha existido. Playwright con un fichero que no casa no pasa en verde —
  # sale con código 1 y «No tests found» (comprobado)—, así que esta casilla
  # tenía un test que no podía pasar jamás y por eso llevaba semanas sin poder
  # marcarse. El equivalente real, que cubre lo mismo (presencia del selector,
  # ES por defecto, cambio a EN, persistencia tras recarga), es:
  - id: auto_prod16_02_a
    runtime: node-jest
    command: "npm --prefix apps/admin-panel run e2e -- e2e/lang-switcher.spec.ts"
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
  - ⏳ **Pendiente (2026-08-12):** de `projects/*` entra **una** pantalla entera,
    `projects/[id]/agent-tools-diagnostic` (namespace `agentToolsDiagnostic`), con
    test en los dos idiomas (`i18n.test.tsx`, 5 casos) y fuera ya de la
    `ATTR_ALLOWLIST`. Se eligió ésa y no otra por dos razones: llevaba 3 de los 9
    ternarios que quedaban en el panel, y es una pantalla de **verificación** —
    quien la abre está comprobando si un agente ejecuta lo que cree, y con el
    toggle en EN le salía en castellano justo donde uno lee con cuidado.
    De paso entra el módulo **`teams` COMPLETO** (7 ficheros: lista, detalle y los
    cinco diálogos), que el enunciado no nombra pero es de la misma familia de
    «pantallas de mayor uso»: namespace `teams`, test `app/admin/teams/i18n.test.tsx`
    con 9 casos que abren **los cinco diálogos** en inglés.
    Sigue faltando el resto de `projects/*` (~25 ficheros) y **todo `agents/*`,
    que es de otro carril**.
    **Un hueco conocido que este carril NO puede tapar**: el `<Select>` de política
    de memoria del equipo pinta `MEMORY_SCOPE_OPTIONS` de `lib/memory/constants.ts`,
    que sólo tiene etiquetas en castellano y lo comparte la ficha del agente. Con
    el toggle en EN esas cuatro opciones siguen en castellano. El fichero está fuera
    de la propiedad de este carril; es el mismo caso que el `label_es` de
    `preferences-tab` anotado en `task_prod16_08`, y entra con `agents/*`.
  - ✅ **2026-08-19 — el bloqueo de BACKEND está retirado.** La corrección (b) de
    arriba decía que `settings/page.tsx` y `settings/memories/page.tsx` **no se
    pueden migrar sólo en el frontend** porque sus títulos y descripciones los
    sirve el backend en `label_es`/`description_es` y no existía el par `_en`.
    Ya existe:
    - `settings_registry.py` (tenant) y `platform_settings_registry.py`
      (plataforma) sirven ahora `label_en`/`description_en` en **todas** sus
      entradas: 3 + 7 categorías y 2 + 12 ajustes. El segundo tenía el mismo
      defecto y no lo nombraba ningún plan — se arregla a la vez porque dejar uno
      a medias reproduce exactamente la pantalla mitad-y-mitad que esto cierra.
    - La regla no depende de que alguien se acuerde: `require_language_pair` la
      valida **al construir el dataclass**, o sea al importar el módulo. Una
      entrada nueva sin su inglés no arranca el proceso. Comprobarlo en un test a
      posteriori habría dejado el hueco entre el `import` y el test.
    - Guardas con rojo verificado por mutación (vaciar un `label_en`, borrar una
      `description_en`, dejar de emitirla en el `_to_dict`): las dos primeras
      tumban el módulo entero; la tercera, el test de serialización.
      `pytest tests/unit/test_settings_registry.py tests/unit/test_platform_settings_registry.py`
      → **32 passed**.
      **Lo que queda de esta casilla es frontend**: que las dos pantallas de
      settings —y la de platform-settings— elijan el par según el idioma activo.
      No entra hoy porque `lib/i18n/dictionary.ts` lo están tocando dos carriles de
      la ola del córtex y editarlo a la vez es pedir un pisotón.
  - ✅ **2026-08-19 (segunda pasada del día) — la mitad de FRONTEND que quedaba del
    bloqueo, hecha; y tres pantallas de `projects/*` más.** Lo que la nota de arriba
    dejaba pendiente («que las dos pantallas de settings —y la de platform-settings—
    elijan el par según el idioma activo») **está cerrado**, y con él el módulo
    `settings/` salvo su rama `sso`:
    - `settings/page.tsx` (namespace `settingsIndex`), `settings/memories/page.tsx`
      (`settingsMemories`) y `settings/platform-defaults/page.tsx`
      (`platformDefaults`). El marco sale del diccionario y las
      etiquetas/descripciones de cada categoría y ajuste del registry con
      `pickLang` — que es lo correcto y no un atajo: el catálogo lo define el
      backend, y duplicarlo como claves reabriría la divergencia que el par `_en`
      acaba de cerrar.
    - Entra además **`cortex-model-section.tsx`** (`cortexModel`, 259 líneas), que
      el enunciado no nombra pero se renderiza DENTRO de platform-defaults: dejarla
      fuera habría dado la pantalla mitad-y-mitad que es justo el fallo que este
      plan cierra. La ve sólo el System Owner, que es quien más gana con el toggle.
    - Y tres pantallas COMPLETAS de `projects/*`: el listado (`projectsList`), la
      memoria del proyecto (`projectMemories`) y la caché de dependencias
      (`depCache`). Elegidas por ser autocontenidas.
      **Contadores, medidos ejecutando: atributos 200 → 188, ficheros 77 → 71**, y la
      `ATTR_ALLOWLIST` pierde **seis** entradas. `node scripts/check-i18n.mjs` OK.
      **Tests: 22 casos nuevos, todos ejecutados y todos en rojo ANTES de
      implementar.** `app/admin/settings/i18n.test.tsx` pasa de 4 a **14** casos (las
      cinco pantallas de settings en los dos idiomas, con fixtures del registry que
      traen las DOS caras; 6 de 13 salieron rojos en la primera pasada),
      `app/admin/projects/i18n.test.tsx` es nuevo con **7** (los 7 rojos),
      `app/admin/teams/i18n.test.tsx` gana **1** y `lib/memory/constants.test.ts` es
      nuevo con **4** (3 rojos entre los dos ficheros).
      `npm --prefix apps/admin-panel run test -- i18n.test` —el `command:` declarado—
      pasa de 17 ficheros/142 tests a **18 ficheros/160 tests**.
      **Cinco cosas que enseñó esta pasada y valen más que el contador:**
    1. **Un `status` de texto no sobrevive a la traducción.**
       `settings/memories` decidía el color con `status.startsWith("Error")` sobre el
       MENSAJE. En inglés el mensaje empieza por «Could not», así que el error habría
       salido con el color de un guardado correcto — un fallo que no rompe nada, no
       lo ve `tsc` y sólo se ve en producción. El estado pasó a **discriminante**
       (`{kind: "saving"|"saved"|"error"}`) y el texto se deriva de él. Hay un test
       que fija las dos mitades (texto inglés Y clase de error).
    2. **La guarda de atributos no vio el peor caso de este lote.**
       `ProjectBreadcrumb` escribía `"Proyectos"` fijo, así que la miga de pan de las
       **diez** sub-pantallas del proyecto seguía en castellano con el toggle en EN.
       El literal no está en un atributo —es una propiedad de un objeto— y por eso
       ninguna de las dos señales lo cuenta. Tercer ejemplo del mismo aviso ya
       anotado con los ternarios y con las frases sin tilde: **el contador mide su
       patrón, no la deuda.** Arreglado reutilizando `nav.projects` (mismo destino:
       dos claves para un enlace acaban divergiendo).
    3. **El hueco de `MEMORY_SCOPE_OPTIONS` está CERRADO**, el que la pasada del
       08-12 anotó como «lo que este carril NO puede tapar». La constante de
       `lib/memory/constants.ts` guarda ahora la **clave** del diccionario y no el
       texto, y sus **dos** consumidores la resuelven con el idioma activo
       (`teams/[team_id]` y `agents/[id]/agent-edit-dialog` — se toca el segundo a
       propósito, aunque `agents/*` sea de otro carril, porque migrar uno solo dejaba
       la constante partida). Namespace compartido `memoryScope`, y no una clave por
       pantalla, por la misma razón que la constante existe.
       **De paso salió un caso del §5 de `verificar-antes-de-implementar`**:
       `memoryScopeLabel()` tiene **cero llamantes** desde que se escribió. Se ha
       traducido igual (para no dejar un helper castellano al lado de un catálogo
       bilingüe) y se anota aquí, que es lo que faltaba: alguien debería decidir si
       se borra.
    4. **Un `[x]` que no se pone: el enunciado sigue sin cumplirse.** Quedan
       `settings/sso/*` (6 ficheros, 16 atributos), **14 ficheros de
       `app/admin/projects/*` y 3 de `components/projects/`**
       —incluido el hub `projects/[id]/page.tsx`, que reparte su texto entre seis
       ficheros de `components/projects/` y por eso NO entra a trozos— y
       `agent-kbs-section.tsx` de `agents/*`. Tres de las ~28 pantallas de
       `projects/*` no es «`projects/*` migrado».
    5. **El bloqueo de backend era real, y verificarlo costó cinco minutos.** Antes
       de escribir una línea se comprobó en el código que
       `registry_to_dict()`/`platform_registry_to_dict()` emiten ya `label_en` y
       `description_en` (`settings_registry.py:278`, `platform_settings_registry.py:453`)
       y que `require_language_pair` lo valida al construir el dataclass. Los
       fixtures de los tests son espejo de esas dos funciones, no de lo que la
       pantalla espera — que es la diferencia entre un test que verifica y uno que
       bendice el defecto.
       **Verificación ejecutada, toda desde `apps/admin-panel`:** `npx vitest run` →
       **1202 passed / 144 ficheros** (eran 1180/142 esta mañana); `npx tsc --noEmit`
       **limpio, sin ninguna excepción** (el `TS6133` ajeno que anotaba
       `task_prod16_07` ya no está); `npx next lint` sin avisos; `npx prettier --check`
       de los ficheros tocados OK; `node scripts/check-i18n.mjs` y
       `node scripts/check-component-size.mjs` en verde; y
       `NEXT_PUBLIC_API_URL=/api npx next build` **construye** (65 páginas).
       **Roturas comprobadas, cada una con su rojo y sólo el suyo:** volver `label_es`
       en el índice de settings (cae el caso inglés del índice), volver `label_es` en
       platform-defaults (cae el suyo), desmontar `<CortexModelSection/>` del hub de
       platform-defaults (caen **2**), y reintroducir
       `aria-label="Configuración del tenant"` en `settings/page.tsx` → `check-i18n`
       sale **exit 1** nombrando el fichero, o sea que el trinquete que acaba de
       protegerlas MUERDE.
- **Tiempo**: 2 días · **Complejidad**: l · **Depende de**: `task_prod16_02`
- **Tests automáticos**:
  ```yaml
  # CORREGIDO el 2026-08-19: `e2e/lang-toggle-core.spec.ts` tampoco ha existido
  # nunca, y a diferencia del de la casilla 02 **no tiene equivalente** con otro
  # nombre. No se escribe uno ahora a propósito: sería añadir una medida que
  # nadie puede ejecutar sin stack, que es el defecto que este plan corrige. Lo
  # que SÍ existe y se ejecuta sin stack son los tests de render por pantalla en
  # los dos idiomas —trece ficheros `i18n.test.tsx` hoy—, que es donde vive de
  # verdad la cobertura de esta casilla; el recorrido del selector entre
  # pantallas lo cubre `e2e/lang-switcher.spec.ts` (auto_prod16_02_a).
  # Ejecutado antes de escribirlo aquí, que es la mitad que faltaba las otras
  # dos veces: 17 ficheros, 142 tests, todos en verde (2026-08-19). Tras el lote
  # de settings + tres pantallas de projects del mismo día: **18 ficheros, 160
  # tests**, también ejecutado.
  - id: auto_prod16_03_a
    runtime: node-vitest
    command: "npm --prefix apps/admin-panel run test -- i18n.test"
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
  - ⏳ **Pendiente (2026-08-10, tercera pasada):** entra **`components/capability/`
    ENTERO**, que era el mayor bolsón que quedaba: **25 de los 34 ternarios de
    idioma vivían ahí**. Migrados los cuatro ficheros a un namespace nuevo
    (`capability`, 30 claves): `capability-hub.tsx`, `persona-section.tsx`,
    `chat-model-section.tsx` y `provider-model-selects.tsx`. Test:
    `components/capability/i18n.test.tsx` (13 casos, los cuatro componentes en los
    DOS idiomas, afirmando en ambos sentidos).
    **Contadores: 34 → 9 ternarios, 8 → 4 ficheros.** Los 9 que quedan están fuera
    de este carril: `app/admin/tools/page.tsx` (4),
    `projects/[id]/agent-tools-diagnostic` (3), `cortex/mind` (1) y
    `components/teams/adopt-team-dialog.tsx` (1).
    **Lo que enseñó migrar esto**: dos de los cuatro ficheros no tenían ternarios
    sueltos sino un `const t = (es, en) => …` local — un **diccionario privado por
    fichero**. El guard contaba UNO por fichero y detrás había veinte textos. O sea
    que el contador de ternarios **subestima la deuda de forma sistemática** en
    cuanto alguien se organiza un poco: cuanto mejor escrito está el atajo, menos
    se ve. Igual que ya se anotó con los atributos sin tilde.
    **Y un cambio de comportamiento, deliberado y con test**: el título de
    `ChatModelSection` (que hace de etiqueta del botón, «Guardar <título>») pasó de
    un ternario a `pickLang`. Con el toggle en EN y un título propio del llamante
    el botón decía **«Save modelo del chat»** — un híbrido que un `t("Guardar X",
"Save X")` suelto no podía cazar porque el título venía por props.
    **Limitación conocida del guard, anotada**: no distingue comentarios de código,
    así que documentar el anti-patrón escribiendo su forma literal en una prosa
    hace fallar la guarda. El comentario del test se reformuló para evitarlo.
    Siguen sin migrar: marketplace, guardrails, ollama, notifications, docs,
    assistant, tools, memories. **Los atributos siguen en 232** (85 ficheros, uno
    más que ayer sólo porque dos pantallas se trocearon y su deuda se repartió):
    este carril no bajó ese contador, y decirlo importa más que el número.
  - ⏳ **Pendiente (2026-08-12, cuarta pasada). Cae la mitad de «ternarios» del
    enunciado; la de atributos sigue casi entera.**
    **Migrados al completo tres de los ocho módulos que esta tarea enumera** —
    `tools`, `guardrails` y `ollama`— más dos que no nombra pero son de la misma
    familia: el módulo `teams` entero (7 ficheros) y
    `projects/[id]/agent-tools-diagnostic`. Cinco namespaces nuevos (`tools`,
    `agentToolsDiagnostic`, `teams`, `guardrails`, `ollama`) y cinco ficheros de
    test que rinden cada pantalla en los DOS idiomas y afirman en ambos sentidos:
    `app/admin/tools/i18n.test.tsx` (7), `app/admin/teams/i18n.test.tsx` (9),
    `projects/[id]/agent-tools-diagnostic/i18n.test.tsx` (5),
    `app/admin/guardrails/i18n.test.tsx` (3), `app/admin/ollama/i18n.test.tsx` (3)
    y `app/admin/cortex/mind/honesty-i18n.test.tsx` (2).
    **«Vaciar la allowlist» está hecho a MEDIAS, y la mitad que falta es la
    grande.** La de ternarios está vacía —trinquete graduado, ver la tabla de
    arriba—; la de atributos baja de **232 a 211** y sigue teniendo **80
    ficheros**. Eso es un 9 % de la deuda de atributos. `--strict` sigue saliendo 1.
    **Cuatro cosas que enseñó esta pasada y que valen más que el contador:**
    1. **El bolsón de ternarios que quedaba estaba mal contado.**
       `adopt-team-dialog.tsx` figuraba con UNO y tenía un `const t = (es, en) => …`
       local con veinte textos, igual que los de `capability` de la pasada anterior.
       El patrón se repite: **el ternario suelto es el síntoma, el diccionario
       privado por fichero es la enfermedad**, y la guarda sólo ve el síntoma.
    2. **Se decidió qué NO va al diccionario, y está escrito en el propio
       diccionario**: las etiquetas de la taxonomía de tools (ADR 0049) son datos
       bilingües y se resuelven con `label()`/`pickLang` — duplicarlas como claves
       reabriría la divergencia que ese ADR cerró; los `guardrail_type` y
       `hook_point` se quedan crudos porque el operador los busca así en los logs;
       y los nombres de modelo de Ollama son identificadores.
    3. **Cuatro fugas más de `task_prod16_05`** (`error.body` crudo en pantalla),
       ninguna llamada `errorText`: el diálogo de alta y el de borrado de
       `tools/page.tsx`, la carga del diagnóstico de tools, el alta y la edición de
       miembro de un equipo, y el detalle del equipo. **Van 22 sitios.** La única
       búsqueda que las encuentra sigue siendo la de lo que hace el código.
    4. **Troceada `app/admin/tools/page.tsx`**, que al traducirse pasó de 791 a
       **813 líneas** y disparó `check-component-size`. Repartida en `tool-types.ts`
       (50), `tool-facet-select.tsx` (71), `tool-catalog-rows.tsx` (215) y
       `tool-dialogs.tsx` (291), con `page.tsx` en **258**. Los 7 tests siguen verdes
       sin tocar una aserción. **Dato para las próximas olas: traducir ENGORDA los
       ficheros** (un literal pasa a `{t("clave")}`), así que un módulo cerca del
       techo de 800 hay que contar con trocearlo en el mismo movimiento.
       Siguen sin migrar: marketplace, notifications, docs, assistant, memories, y el
       grueso de `projects/*`.
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
  - 🔎 **Y dos más, en el chat del proyecto (2026-08-10):** al trocear
    `projects/[id]/chat/page.tsx` salieron a la luz otras dos, otra vez escritas en
    línea: el error de carga de conversaciones (`chat-error`) y el de «Generar
    Plan» (`generate-plan-error`) pintaban `error.body` **crudo**. Las dos a
    `useErrorText()`. **Van 17 sitios**, y los cuatro últimos no se llamaban
    `errorText` ni `apiErrorBody`: no se llamaban de ninguna manera. La única
    búsqueda que las encuentra es la de lo que hace el código
    (`instanceof ApiError ? … .body`), y conviene asumir que quedan más.
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

- [x] **Título**: Mismo tratamiento para
      `app/admin/projects/[id]/mcp-servers/page.tsx` y
      `app/admin/projects/[id]/plans/[planId]/page.tsx`. Refactor mecánico:
      sin cambios de comportamiento, los specs e2e existentes deben pasar sin
      tocar sus aserciones.
  - ⏳ **Pendiente (2026-07-31):** las dos páginas ya estaban partidas por el tramo de modularización #9 (`39072e22` y `415a2578`, 2026-07-09/10), no por prod-16: `mcp-servers/page.tsx` 249 líneas y `plans/[planId]/page.tsx` 153, pero `mcp-server-sections.tsx` quedó en 1125 líneas — ahí solo se movió el bulto — y la verificación que pide el plan es Playwright.
  - ⏳ **Pendiente (2026-08-10), pero mucho menos:** **`mcp-server-sections.tsx` ya
    no existe**. Era el ejemplar del «mover el bulto no es partir» —1125 líneas que
    el tramo #9 sacó del `page.tsx` sin repartir— y se ha repartido de verdad en
    cuatro: `mcp-server-card.tsx` (~85), `mcp-test-result-panel.tsx` (~130),
    `mcp-tool-roles-section.tsx` (~235) y `mcp-server-dialog.tsx`, que hereda el
    resto. Los **15 tests** del módulo (`page.test.tsx`,
    `mcp-tool-roles-section.test.tsx`, `available-capabilities.test.tsx`,
    `mcp-oauth-connect.test.tsx`) siguen verdes; lo único que cambió en ellos es
    **de dónde se importa** `McpToolRolePolicySection`, ni una aserción.
    **Lo que NO se ha hecho, y por qué**: `mcp-server-dialog.tsx` queda en 665
    líneas, por encima del techo de 500 de una pieza. Es UN formulario con una
    decena de `useState` entrelazados (estado del server, args en crudo, plantilla
    aplicada, escotilla de Vault, resultado del test, selección de tools a
    importar). Partirlo exige decidir cómo viaja ese estado —prop-drilling a cinco
    niveles o un contexto local— y eso no es un movimiento mecánico: es un rediseño
    con riesgo de regresión sobre un formulario que funciona. Queda anotado en
    `SECTION_ALLOWLIST` con su tamaño real, que es deuda **medida y decreciente**;
    trocearlo a lo bruto para que el número baje sería el atajo que esa guarda
    existe para castigar. La casilla sigue abierta con razón: le falta esa pasada
    y la verificación Playwright que el plan pide.
  - ✅ **CERRADA el 2026-08-19: `mcp-server-dialog.tsx` 665 → 468.** Los dos
    módulos del enunciado están por debajo del techo, y lo dice la guarda, no una
    opinión: `node scripts/check-component-size.mjs` sale **0** y reporta «81
    pantalla(s), 0 por encima de 800; 73 pieza(s), **1** por encima de 500» — esa
    una es `agent-tools-section.tsx` (691), que no es de esta tarea. En
    `plans/[planId]` no había nada que hacer: sus 12 piezas ya estaban repartidas,
    la mayor en 334 líneas.
  - 🔍 **Y el argumento de 2026-08-10 estaba mal, que es lo que hay que leerse de
    esta entrada.** No estaba mal en su razonamiento —partir el formulario SÍ pedía
    prop-drilling— sino en su alcance: **dos de los bloques del diálogo no
    COMPARTÍAN ese estado, lo tenían prestado por haber nacido ahí**.
    · `mcp-connection-test-section.tsx` (145) se llevó **siete** `useState`
    —probando, resultado, error, tools marcadas, importando, error de
    importación, importadas— y sólo necesita `buildPayload`: se prueba
    exactamente lo que se va a guardar.
    · `mcp-advanced-options-section.tsx` (168) se llevó 112 líneas de JSX y
    `setAuthRefManual`.
    El diálogo quedó con **menos** `useState` que antes, no con los mismos
    repartidos por la jerarquía — que era justo el riesgo que la nota temía.
    `showRawAuth` se quedó en el diálogo a propósito y está argumentado: el
    diálogo lo reinicia en dos sitios (al aplicar plantilla y al reabrirse), y
    bajarlo pedía un `key` que remonta —perdiendo el foco a media escritura— o un
    `useEffect` que dispara también cuando la plantilla pasa a null.
    **Lección para la `SECTION_ALLOWLIST`**: una entrada bien argumentada tampoco
    es permanente. Merece que alguien vuelva a mirarla con el corte en la mano, no
    con el corte que se imaginó quien la escribió. Queda anotado en el docstring
    del script.
  - ⚠️ **Un rojo que NO salió, y por eso hay un fichero de test nuevo.** Al
    verificar la rotura se quitó `<McpAdvancedOptionsSection/>` del JSX del
    diálogo: los **23** tests del módulo siguieron **verdes**. Es el modo de fallo
    nº5 de `verificar-antes-de-implementar` —«mecanismo entregado, cero
    llamantes»— en su versión más barata de cometer: la sección nueva tiene sus
    tests y pasa, el diálogo compila, `tsc` calla, y en producción las opciones
    avanzadas no existen. Se cubrió con `mcp-server-dialog.test.tsx` (5 tests) y
    con la rotura repetida: entonces sí, **4 rojos**.
  - **Verificación ejecutada (2026-08-19)**, toda desde `apps/admin-panel`:
    `npx vitest run` → **1180 passed / 142 ficheros**, de los cuales **28** son de
    este módulo (los 15 de antes intactos + 13 nuevos: 8 de la sección avanzada,
    que **no tenía ni uno**, y 5 del cableado); `npx tsc --noEmit` limpio salvo un
    rojo ajeno y anterior (`app/admin/cortex/identity/onboarding-proposal.test.tsx`,
    `TS6133`, de otro carril); `node scripts/check-component-size.mjs` OK;
    `node scripts/check-i18n.mjs` OK; y `NEXT_PUBLIC_API_URL=/api npx next build`
    **construye** — el paso que ninguno de los otros cubre.
    Roturas comprobadas, cada una con su rojo y sólo el suyo: el fallback del
    timeout a 30 (llegaba `0`/NaN al backend), el aviso de edición manual de la
    ruta de Vault, y las dos secciones desmontadas del diálogo.
  - ⏳ **Lo que sigue necesitando un humano**: la verificación Playwright que pide
    el enunciado. Los specs necesitan stack levantado; se dejan repuntados abajo a
    los ficheros que existen de verdad (`mcp-servers.spec.ts` y
    `plan-detail.spec.ts` **nunca existieron** — es el mismo caso que destapó
    `test_declared_tests_exist` con `task_prod16_02`).
- **Tiempo**: 1,5 días · **Complejidad**: m · **Depende de**: `task_prod16_06`
- **Tests automáticos**:
  ```yaml
  - id: auto_prod16_07_a
    runtime: node-jest
    command: "npx vitest run app/admin/projects/[id]/mcp-servers app/admin/projects/[id]/plans/[planId]"
  - id: auto_prod16_07_b
    runtime: node-jest
    command: "npm --prefix apps/admin-panel run e2e -- e2e/mcp-config-ui.spec.ts e2e/mcp-test-connection.spec.ts e2e/plan-detail-view.spec.ts"
  ```

#### `task_prod16_08` — Partir las 7 páginas restantes >800 líneas + guard de tamaño

- [x] **Título**: knowledge-bases (1042), llm-providers (951),
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
  - ✅ **La deuda de PANTALLAS queda a CERO (2026-08-10)**, que es el objetivo
    numérico de esta tarea. `check-component-size.mjs`: **81 pantallas, 0 por
    encima de 800**; su `ALLOWLIST` está **vacía**, así que el modo normal y
    `--strict` dicen ya lo mismo para las pantallas.
    · Caía la última que quedaba de este carril: **`projects/[id]/chat` 926 → 462**,
    repartida en `chat-types.ts` (71), `chat-mode-selector.tsx` (54),
    `message-feed.tsx` (136), `generate-plan-button.tsx` (106) y
    `chat-composer.tsx` (164).
    · Las otras cuatro que la pasada anterior listaba (`sso/saml` 943, `sso` 915,
    `teams/[team_id]` 914, `cortex/mind` 914) **ya estaban partidas por otra ola**
    cuando este carril fue a buscarlas: 220, 218, 403 y 327 respectivamente. Se
    verificó con `wc -l` antes de tocar nada, que es justo lo que
    `verificar-antes-de-implementar` §1 pide. Este carril no se apunta ese trabajo.
  - 🔎 **La red del chat tenía un agujero, y se vio porque se comprobó el rojo.**
    `page.test.tsx` (7 casos) parecía cobertura suficiente para trocear. Antes de
    mover una línea se saboteó el salto a la conversación más reciente tras borrar
    (`nextActiveAfterDelete(...)` → `null`) y **los 7 siguieron verdes**: sólo
    cubrían la barra de historial. El selector de modo, el feed, el resumen
    plegable, «Generar Plan» y el composer —justo lo que el troceo saca del
    fichero— iban a salir del monolito sin nada debajo. Se escribió
    `chat-parts.test.tsx` (14 casos, la página entera renderizada), se comprobó que
    **muerde** rompiendo tres comportamientos a propósito (dos saltaron; el tercero
    lo cubre otra guarda, y se anota), se restauró, y tras el corte los **21 tests
    siguen verdes sin tocar ni una aserción**.
  - 🔎 **Y una fuga más de `task_prod16_05` de camino**: `GeneratePlanButton` y el
    error de carga de conversaciones pintaban `error.body` **crudo**. Los dos a
    `useErrorText()`. Van ya 17 sitios encontrados buscando por lo que hace el
    código (`ApiError ? .body`) y no por el nombre `errorText`.
  - ⏳ **Pendiente (2026-08-10):** `--strict` sigue saliendo 1 por **dos PIEZAS**,
    no por ninguna pantalla: `agent-tools-section.tsx` (691) y
    `mcp-server-dialog.tsx` (665). Ver la nota de `task_prod16_07` para la segunda.
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
  - ✅ **MARCADA el 2026-08-19 — el trabajo llevaba nueve días hecho y la casilla seguía
    en `[ ]`.** Las dos mitades del enunciado están, y se comprobó ejecutando, no leyendo
    las notas: `node scripts/check-component-size.mjs --max-lines 800` desde
    `apps/admin-panel` dice **«81 pantalla(s), 0 por encima de 800 línea(s)»** y sale con
    **exit 0**. El guard existe (`scripts/check-component-size.mjs` + `npm run check:size`)
    y su `ALLOWLIST` de pantallas está **vacía**, así que el modo normal y `--strict` ya
    dicen lo mismo para pantallas.
    **De paso, un número mal contado en las notas de arriba**: dicen «11 tests» y luego
    «+7 tests … (37 en total)» en `scripts/check-component-size.test.ts`. Contados hoy:
    **19** `it(...)`, y sin `it.each` que pudiera esconder más. Las notas históricas se
    dejan como están —son el registro de lo que se creyó entonces— pero el número vigente
    es 19.
    **No es una guarda vacía**: bajando el techo a `--max-lines 100` reporta 73
    infractoras y falla, o sea que mide de verdad.
    **Lo que sigue abierto NO es de esta casilla**: `--strict` sale 1 por dos PIEZAS
    (`agent-tools-section.tsx` 691 y `mcp-server-dialog.tsx` 665), que caen bajo el techo
    de 500 de `task_prod16_06` y bajo la nota de `task_prod16_07`. El objetivo numérico de
    ESTA tarea —pantallas por encima de 800— está en cero.
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

- [x] **Título**: Separar el router mixto en paquete `routers/sso/` con
      `oidc.py`, `saml.py` y `common.py` (`_issue_identity_session` y
      modelos compartidos). Refactor puro: mismas rutas, mismos
      `response_model`, tests de integración SSO existentes en verde sin
      modificarlos.
  - ✅ **Cerrada (2026-08-10)**: `routers/sso.py` (que para entonces eran ya
    **1654** líneas, no las 1494 que midió el plan ni las 1562 de julio) es hoy el
    paquete `routers/sso/`: `common.py` (363), `oidc.py` (495), `saml.py` (631),
    `discovery.py` (295) y `__init__.py` (118).
    **Un módulo más que los tres del enunciado, y la razón importa**: `/auth/discover`,
    `/auth/sso/providers` y los dos ajustes de plataforma (`public-base-url`,
    `api-path-prefix`) no son de OIDC ni de SAML — la página de login los consulta
    ANTES de saber qué protocolo toca. Meterlos en cualquiera de los dos habría
    obligado al otro a importarlo, que es exactamente cómo el router mixto llegó a
    1654 líneas. `common.py` no publica ni una ruta.
    **El troceo fue un movimiento mecánico de verdad**: los bloques se cortaron por
    rangos del AST del fichero original, no a mano.
    **La red se escribió ANTES de partir**: `tests/unit/test_sso_router_package.py`
    captura las **22 rutas** (camino, métodos y nombre de función) del monolito y
    exige que el paquete sirva exactamente ese conjunto. Fija además tres cosas que
    son las que muerden en un troceo así: que `/auth/discover` siga colgando un
    nivel por encima, que **ninguna ruta corta sea paramétrica** (la condición bajo
    la cual repartir los `@router.get` entre módulos NO cambia el matching, que va
    por orden de registro), y que `route_paths` vea a través del router compuesto.
    **Este paquete es el primero del repo que anida sub-routers**, o sea el primero
    que arma de verdad la trampa de FastAPI 0.141 que documenta
    `routing_introspection`: `include_router` deja de aplanar y
    `main._is_admin_surface` —que decide si un router lleva la guarda de System
    Admin— leería cero rutas. Hasta hoy esa trampa estaba armada pero sin caso real.
  - 🔎 **Una línea de test SÍ hubo que tocar, y se deja dicho**: el enunciado pedía
    los tests SSO «en verde sin modificarlos» y se cumple salvo en
    `tests/integration/test_saml.py:511`, que hacía
    `monkeypatch.setattr(api_server.routers.sso, "saml_available", …)`. Parchear el
    paquete no alcanza al global del submódulo, así que la diana pasa a
    `api_server.routers.sso.saml`. Es **la diana del parche, no una aserción**: ni
    un `assert` cambió. El otro ajuste externo fue `routers/mcp_oauth.py`, que
    importaba `_effective_redirect_base` del módulo y ahora lo importa de
    `sso.common`.
  - **Verificación ejecutada**: `pytest tests/unit/test_sso_router_package.py` → 5
    passed; `pytest` sobre los 10 ficheros SSO de integración (`test_saml`,
    `test_sso_global_login`, `test_sso_global_config`, `test_oidc_config_crud`,
    `test_saml_config_crud`, `test_saml_crypto`, `test_sso_callback_redirect`,
    `test_jit_provisioning`, `test_group_mapping`,
    `test_post_login_membership_resolution`); `mypy` sobre el paquete, `ruff` y
    `black` limpios. La línea de base se tomó ANTES de tocar nada: los 7 ficheros
    SSO originales daban **85 passed**.
  - ⚠️ **Gotcha que costó una vuelta entera y no estaba documentado**: dos suites de
    integración lanzadas a la vez con el MISMO `TEST_PG_DB_NAME` se pisan — la que
    termina primero **borra la base** y la otra revienta con 84 errores de
    `InvalidCatalogNameError`, que se leen como si el refactor hubiera roto el SSO.
    No lo había roto. Un carril, una base.
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

- [x] **Título**: Dividir el modelo ORM en módulos por agregado
      (`db/models/tenancy.py`, `projects.py`, `agents.py`, `plans_tasks.py`,
      `llm.py`…), manteniendo `db/domain.py` como fachada de re-export para
      no romper los imports existentes. Verificar que `alembic` no detecta
      diferencias de esquema tras el refactor (autogenerate vacío).
  - ⏳ **Pendiente (2026-07-31):** sin empezar — `db/domain.py` sigue monolítico y ha CRECIDO a 1768 líneas (el plan lo midió en 1506); no existe el paquete `db/models/` por agregados (`db/models.py`, 1183 líneas, es otra cosa: las 5 tablas de la fase 0) ni el test `tests/integration/test_alembic_autogenerate_clean.py`.
  - ⏳ **Se deja abierta a propósito (2026-08-10): pide una ola en solitario.**
    No es una estimación: `db/domain.py` lo importa medio repo (api-server,
    workers, orchestrator y los tres árboles de tests), así que la fachada de
    re-export toca ficheros en todos ellos a la vez. Con cuatro carriles vivos
    sobre el mismo árbol eso es **garantía de conflicto**, y un conflicto en la
    capa ORM se resuelve mal: el merge compila y el esquema cambia sin que nadie
    lo vea. La condición para abordarla es tener el árbol para uno solo, y el
    test que la protege (`test_alembic_autogenerate_clean.py`, autogenerate vacío)
    hay que escribirlo **antes** de mover la primera tabla.
    El troceo de `routers/sso.py` de `task_prod16_10` sirve de patrón: cortar por
    rangos del AST y fijar el contrato con un test escrito antes.
  - ✅ **CERRADA el 2026-08-19, con el árbol para uno solo, como pedía la nota.**
    `db/domain.py` (**1840** líneas al abrirlo, no las 1506 del plan ni las 1768
    de julio) es hoy el paquete `db/domain/`: `enums.py` (347, los 22
    vocabularios, sin tablas), `agents.py` (354), `humans.py` (338),
    `executions.py` (256), `plans_tasks.py` (216), `projects.py` (209),
    `teams.py` (133), `approvals.py` (130) y un `__init__.py` (152) que **sólo
    re-exporta**.
    **El paquete NO se llama `db/models/`, y la razón importa**: existe
    `db/models.py` (el agregador de la fase 0), y un directorio con ese nombre
    junto a él gana la resolución de import y deja el módulo **inalcanzable**.
    Habría roto el `env.py` de Alembic y medio arranque, en silencio y por un
    nombre. `db/domain/` no tiene ese problema y además conserva la ruta que
    importan los 233 ficheros.
  - **El troceo fue mecánico de verdad**: los bloques se cortaron por rangos del
    AST del monolito (recuperado con `git show HEAD:`), y los imports de cada
    módulo se resolvieron por análisis de nombres libres, no a mano. **La prueba
    más fuerte no es un test**: comparando `ast.unparse` definición a definición
    contra el monolito, las **39** (22 enums + 17 modelos) son **IDÉNTICAS**.
    Cero perdidas, cero alteradas.
  - **La red se escribió ANTES de mover la primera tabla**, como en
    `task_prod16_10`: `tests/unit/test_domain_models_package.py` (7 tests) corrió
    contra el monolito con **4 en verde y 3 en rojo**, y los tres rojos eran
    exactamente «el paquete no existe». Congela los 19 modelos con su
    `__tablename__`, los 24 enums **con sus valores** —son contrato de BD: el
    `CHECK` de la migración `0101` deriva de `TaskStatus`—, el `__all__` de 41
    nombres y, lo que de verdad se puede perder, **el DDL compilado de las 17
    tablas**: digest del `CREATE TABLE` + sus `CREATE INDEX` contra el dialecto
    PostgreSQL. Eso es el «autogenerate vacío» del enunciado pero **offline**, en
    milisegundos y sin base de datos.
  - **Y se verificó el ROJO rompiendo la implementación a propósito, seis veces**:
    borrar la columna `agents.avatar_url` (rojo, sólo el test de DDL, diciendo qué
    columna); cambiar un `server_default` dejando las mismas columnas (rojo, el
    digest — es el caso sutil que justifica el digest); que la fachada dejara de
    importar `teams` (4 rojos); renombrar un valor de `TaskStatus` (rojo, sólo el
    de enums); añadir un submódulo que el `__init__` no importa (rojo, el que
    vigila que ninguna tabla se caiga de `Base.metadata`); y **mudar `TeamMember`
    al `__init__`** —el atajo de «mover el bulto», que funciona y compila— (rojo,
    el que lo prohíbe). Cada rotura tumbó su test y ninguno más.
  - 🔍 **`auto_prod16_11_b` pedía «autogenerate vacío» y hoy NO lo está — por
    razones anteriores a este troceo.** Se ejecutó de verdad contra la BD migrada
    a head: el diff trae **~150 items**. Ninguno viene de aquí (lo demuestra la
    comparación por AST), y son de tres familias: nombres de índice y de FK que
    las migraciones pusieron y el modelo no declara, `TEXT` frente a `String(n)`
    en columnas creadas por migración, e índices por partición de las cinco tablas
    del ADR 0151, que el modelo no puede declarar. Sobre las 17 tablas del
    dominio son **11**, todas de esas familias y **ninguna una columna perdida**.
    El test existe (`tests/integration/test_alembic_autogenerate_clean.py`, 2
    tests) y afirma lo que esta tarea sí puede sostener: **el diff del dominio no
    crece**, con las 11 congeladas en un inventario que sólo puede menguar.
    Comprobado en rojo borrando `agents.avatar_url`: sale
    `remove_column:agents.avatar_url`.
  - ⚠️ **Hallazgo aparte, y conviene leerlo**: `migrations/env.py` importa **sólo**
    `api_server.db.models`, que no arrastra `db/domain`. Medido: `Base.metadata`
    tiene **34** tablas por esa vía y **83** importando el paquete `db` entero. O
    sea que un `alembic revision --autogenerate` corrido hoy tal cual **no vería
    49 tablas** —`agents`, `projects`, `tasks`, `executions`…— y las propondría
    **borrar**. Es anterior a este troceo (`db/domain.py` tampoco estaba importado
    cuando era un fichero suelto) y se arregla con una línea, pero cambia lo que
    genera la herramienta de migraciones para todo el mundo: **merece su propio
    cambio**. El test lo rodea cargando el modelo completo y deja una aserción que
    se pondrá roja el día que alguien lo arregle, para que venga a quitar el apaño.
  - **Verificación ejecutada (2026-08-19)**: `pytest tests/unit tests/security
tests/docs` → **5016 passed** (los 3 rojos son de otros carriles: el
    inventario de `test_declared_tests_exist` y dos de rotación de Vault que pasan
    aislados); `pytest tests/unit -k 'domain or models'` → **337 passed**;
    `mypy apps/ packages/` → **693 ficheros, limpio**; `ruff` y `black` limpios.
    Integración con base propia (`TEST_PG_DB_NAME=agentic_ola3_l2`):
    `test_alembic_autogenerate_clean.py` → 2 passed.
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

- [x] **Título**: `routers/agents.py` (1414), `workers/backup_destinations.py`
      (1392), `routers/marketplace.py` (1380) y `pricing/litellm_sync.py`
      (1338): extraer sub-módulos cohesivos (p. ej. CRUD vs diagnóstico en
      agents; un módulo por tipo de destino en backup_destinations). Tarea
      **recortable** si D4 lo decide; en ese caso documentar el aplazamiento
      en el changelog del plan.
  - ⏳ **Pendiente (2026-07-31):** sin empezar y sin decidir D4 — los cuatro ficheros siguen enteros (`agents.py` 1461, `marketplace.py` 1465, `backup_destinations.py` 1392, `litellm_sync.py` 1338), y los dos routers han crecido respecto a lo que midió el plan.
  - ⏳ **Se deja abierta a propósito (2026-08-10): también pide ola en solitario**,
    y por la razón que el propio plan ya anticipaba en «Coordinación»:
    `backup_destinations.py` lo toca prod-04 y `marketplace.py` lo tocan
    prod-03/prod-12. A eso se suma que `routers/agents.py` lo importa medio repo.
    Trocearlos con otros carriles vivos sobre los mismos ficheros no ahorra tiempo:
    lo gasta dos veces, en el conflicto y en volver a revisar el resultado.
    **Y sigue sin decidirse D4**, que es lo que gobierna si esta tarea entra o se
    recorta — decisión humana al aprobar el plan, no de un carril.
  - ✅ **DOS de los cuatro, hechos (2026-08-12): `agents` y `backup_destinations`.**
    Los dos que NO los toca otro plan en paralelo, que era la objeción de 2026-08-10. - `routers/agents.py` (**1462** al abrirlo, no las 1414 del plan) → paquete
    `routers/agents/`: `common.py` (205, sin rutas), `crud.py` (261),
    `forks.py` (294), `knowledge_bases.py` (169), `tools.py` (369),
    `skills.py` (131), `capabilities.py` (124), `__init__.py` (107). - `workers/backup_destinations.py` (**1449**, no 1392) → paquete
    `backup_destinations/`: `base.py` (130), `s3.py` (323), `b2.py` (134),
    `sftp.py` (429), `rclone.py` (346), `factory.py` (138), `__init__.py` (163).
    Un módulo por tipo de destino, tal cual pedía el enunciado.
    **La red se escribió ANTES de partir**, como en `task_prod16_10`:
    `tests/unit/test_agents_router_package.py` (9 tests) captura las **18 rutas**
    del monolito —camino, métodos, nombre de función, `response_model` y los
    códigos 201/204— y `tests/unit/test_backup_destinations_package.py` (9)
    congela el `__all__` de 26 nombres, la herencia `B2Destination ⊂ S3Destination`
    y el nombre del logger. Los dos **se corrieron contra el monolito antes de
    tocarlo**: 6/9 y 7/9 en verde, y los rojos eran exactamente «el paquete no
    existe».
    **Y se verificó el ROJO rompiendo la implementación a propósito**, cinco veces:
    invertir el orden de `provider-options` (rojo, solo el test de orden),
    comentar un `include_router` (rojo, 2 tests), esconder un privado reexportado,
    renombrar el logger a `…base`, y añadir un import desde un submódulo. Los
    cinco dieron rojo el test que les tocaba y ninguno más.
    **La trampa que este router tenía y el de SSO no**: `GET /agents/provider-options`
    y `GET /agents/{agent_id}` SOLAPAN, y FastAPI casa por orden de registro.
    Repartirlas entre módulos sin cuidado hace desaparecer `provider-options` en
    silencio: la sirve `get_agent` y responde **422** al parsear
    `"provider-options"` como UUID. Ni un import roto, ni una ruta perdida del
    conjunto, ni un tipo mal — nada lo delata. Se comprobó invirtiendo el orden a
    propósito: con el orden roto, la resolución real de Starlette devuelve
    `get_agent`. Por eso las dos viven en el MISMO módulo y hay un test de ORDEN.
    Y una obligación de FastAPI que salió al partir: `GET`/`POST /agents` tienen
    ruta vacía, e `include_router` **rechaza** incluir un router con ruta vacía
    sin prefijo en la llamada (`FastAPIError: Prefix and path cannot be both
empty`). El prefijo `/agents` lo lleva cada sub-router, no el contenedor.
  - **La prueba más fuerte de que es refactor puro, y no es un test**: comparando
    contra `git show HEAD:` el `ast.unparse` de cada definición, las **22
    funciones** del router y las **21 definiciones top-level** de los destinos son
    **IDÉNTICAS** al monolito. Cero perdidas, cero alteradas. El único código que
    sí cambió es el rechazo 403 de `global_builtin`, que estaba TRIPLICADO
    (`_load_writable_agent_for_{kb,tools,skills}`, mismo cuerpo con distinto
    mensaje) y ahora es un helper con el mensaje por parámetro; los tres textos se
    comprobaron byte a byte contra los del monolito.
  - **Verificación ejecutada (2026-08-12)**: `pytest tests/unit/` → **4441 passed**
    (los 2 rojos que quedan son de otro carril: el recuento del README del
    roadmap); `tests/security/ tests/docs/` → 383 passed; `mypy apps/ packages/`
    → 665 ficheros limpio; `ruff` y `black --check` limpios. Integración con
    `TEST_PG_DB_NAME`/`TEST_REDIS_URL` propios: los 4 adaptadores de destino
    (69 passed), `test_dest_ui` + `test_backup_remote_upload` (11 passed — el
    camino api-server→worker que parchea `build_destination`, o sea el punto de
    parcheo probado de verdad), el lote de 13 ficheros de `/agents` (**86 passed,
    7 rojos**) y `test_admin_hardening_surface` (4 passed / 2 rojos), incluidos
    `test_every_admin_route_carries_the_hardening_gate` y
    `test_the_guard_actually_found_the_admin_surface`, que son el contrato que un
    router compuesto podría haber roto.
    **Ninguno de los 9 rojos es de este troceo**, y están diagnosticados uno a uno: - **4** por `redis.exceptions.AuthenticationError`: `test_agent_skills.py:39`,
    `test_model_config_validation.py:42` y `test_agent_tools_enforcement.py:54`
    **hardcodean** `TEST_REDIS_URL = "redis://localhost:6379/15"`, sin la
    contraseña que `REDIS_PASSWORD` volvió obligatoria (prod-10 / secrets-7).
    Ignoran la variable de entorno, así que no hay forma de pasárselos. - **2** por el validador de secretos débiles en `environment='prod'`
    (`config.py:1129`, ya en `master`): los dos tests de MFA/IP-allowlist de
    `test_admin_hardening_surface` construyen `Settings(environment="prod")` con
    secretos de dev. Rezagados de otro carril. - **2** que pasan AISLADOS y solo caen en el lote de 13
    (`test_effective_tools_endpoint::test_shell_exec_excluded_when_no_allowed_commands`,
    `test_fork_copies_capabilities::test_diff_exposes_capabilities`):
    contaminación entre ficheros que comparten app y BD. - **1 que es un defecto REAL y anterior, y conviene leerlo antes del
    redespliegue**: `test_fork_copies_capabilities::test_fork_does_not_leak_other_tenant_capabilities`
    falla con `IntegrityError: duplicate key value violates unique constraint
"uq_agents_tenant_project_name_live"` en `POST /agents/{id}/fork`. Ese
    índice lo crea la **migración 0126** (prod-13) y **ya está aplicada** en el
    stack: `select version_num from alembic_version` devuelve
    `0138_revoke_backfill_grants`, no el `0124` que afirma `CONTINUE_HERE.md`.
    O sea que esto no es un riesgo futuro: está vivo hoy. Forkear un agente
    al MISMO proyecto sin renombrarlo colisiona, y `fork_agent` hace
    `session.flush()` pelado —no `flush_or_conflict`, que es lo que usan
    `create_agent`/`update_agent`—, así que sale **500**, no 409.
    **No lo he tocado**: elegir entre 409 y auto-desambiguar el nombre es
    decisión de producto (pide ADR), y el docstring de 0126 muestra que su autor
    pensó en el fork pero sólo en el eje entre proyectos.
    **Ninguna aserción de un test existente se tocó**; el único fichero de test
    ajeno modificado es `tests/unit/test_model_options_deprecation.py`, que leía
    `routers/agents.py` **como fichero** por su ruta literal: ahora lee los siete
    módulos del paquete (guarda MÁS fuerte, no más débil — antes un
    `/model-options` reaparecido en otro módulo se le habría escapado).
  - ⏳ **Siguen abiertos los otros dos, y por lo mismo que decía la nota del 10**:
    `routers/marketplace.py` (1854 hoy, no 1380 — ha crecido 474 líneas) lo tocan
    prod-03/prod-12 y el carril de marketplace v2, y `pricing/litellm_sync.py`
    (1338). El patrón y la red a escribir están ya demostrados dos veces; lo que
    falta es que esos dos ficheros no tengan otro carril encima.
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
