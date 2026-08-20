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
> | Métrica                            | Al escribirse el plan | 2026-08-01 | 2026-08-10 | 2026-08-12 | 2026-08-19 | **2026-08-20** |
> | ---------------------------------- | --------------------: | ---------: | ---------: | ---------: | ---------: | -------------: |
> | Pantallas `page.tsx` > 800 líneas  |                    10 |          5 |          0 |          0 |          0 |          **0** |
> | Piezas del troceado > 500 líneas   |                     — |          2 |          2 |          2 |          1 |          **1** |
> | Ternarios de idioma (`check-i18n`) |                    63 |         34 |          9 |          0 |          0 |          **0** |
> | Ficheros con ternarios             |                    12 |          8 |          4 |          0 |          0 |          **0** |
> | Atributos con castellano fijo      |                     — |        232 |        232 |        211 |        188 |        **119** |
> | Ficheros con atributos             |                     — |          — |         85 |         80 |         71 |         **45** |
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
  - ❌ **2026-08-20 — la nota de arriba era FALSA, y el fallo que tapaba era el
    del propio enunciado.** «No queda trabajo de código en ella» se comprobó
    sobre `app/login/page.tsx`, que efectivamente no tiene ni un literal. Pero la
    pantalla de login no es ese fichero: son ese fichero **más los tres que
    monta**, y los tres estaban sin migrar:
    - **`components/login/mfa-challenge.tsx` estaba ENTERO en castellano
      cableado** — la ayuda, la etiqueta «Código de verificación», el botón
      «Verificar» y los tres mensajes de error. Con el toggle en EN, quien tiene
      TOTP activado leía castellano en el paso del segundo factor, que es donde
      más se lee. No aparece nunca en `app/login/i18n.test.tsx` porque ese test
      sólo renderiza el formulario de password: el desafío sólo existe con
      `mfa_required`.
    - **`components/login/provider-buttons.tsx`** escribía el separador
      «o continúa con» en castellano fijo… y el test que ya existía lo
      **mockeaba a `null`**, así que ese texto no llegó nunca a una aserción. Es
      la variante de «un módulo migrado que importa un componente sin migrar no
      está migrado» de la ola 7, aquí con el test como cómplice.
    - **`components/login/provider-brand.tsx` tenía el mismo defecto en el otro
      sentido**: los cinco textos de respaldo de marca en INGLÉS fijo («Sign in
      with Microsoft»). Con el panel en castellano —que es el idioma por
      defecto— el botón de SSO salía en inglés desde el día 1, y «Sign in» es
      literalmente el literal que denuncia el enunciado de esta casilla. Ahora el
      spec de marca guarda la CLAVE (`defaultLabelKey`) y no el texto; la
      etiqueta que escribe el OPERADOR sigue mandando sobre el respaldo, porque
      la redactó una persona para su IdP.
      Ninguna de las dos guardas de `check-i18n.mjs` podía verlo: no hay ternario
      de idioma y el castellano de `MfaChallenge` vive en texto JSX suelto, no en
      atributos. **Séptimo ejemplo del aviso que este plan lleva anotado cuatro
      veces: el contador mide su patrón, no la deuda.**
      **Y dos specs de Playwright que llevaban meses sin poder pasar, arreglados de
      paso** (los dos son el mismo error de premisa, no consecuencia de este
      cambio):
    - `e2e/mfa-enrollment.spec.ts:26` hacía
      `getByRole("button", { name: "Sign in", exact: true })`. El literal dejó de
      existir el 2026-08-01, cuando el login pasó al diccionario: el panel arranca
      en ES y el botón dice «Iniciar sesión». Pasa a un regex ANCLADO y bilingüe
      (`/^(iniciar sesión|sign in)$/i`), que además no colisiona con los botones
      SSO «… con Microsoft» — que era la razón de usar `exact`.
    - `e2e/login-providers.spec.ts:129` esperaba «Sign in with Google» del
      respaldo de marca; con el respaldo traducido, en el idioma por defecto se
      lee en castellano.
      **Cobertura nueva y ejecutada:** `components/login/i18n.test.tsx` (nuevo, 11
      casos) — los dos idiomas del segundo factor con sus dos errores tipados, el
      separador SSO, las cinco marcas y la precedencia de la etiqueta del operador.
      **Ejecutado antes de implementar: 6 rojos y 5 verdes**; los 5 verdes eran los
      ES, que son la mitad que hace que el rojo signifique algo. Con
      `app/login/i18n.test.tsx` (6), la pantalla de login tiene ahora **17 casos en
      los dos idiomas**. Rotura comprobada: devolver `Código de verificación` fijo
      al desafío → cae **1** caso y sólo ése.
      **La casilla sigue sin marcarse `[x]`, y ahora por la razón correcta.** El
      código de login sí está completo —esta vez comprobado sobre la PANTALLA y no
      sobre un fichero—, pero su test declarado (`auto_prod16_02_a`) es Playwright
      y **`e2e/lang-switcher.spec.ts` hace un login real**: necesita el stack
      levantado y el usuario semilla. No es ejecutable desde este carril, y
      marcarla sin ejecutarlo sería justo lo que prohíbe la regla dura de
      `CLAUDE.md`. Lo que le falta a un humano no ha cambiado; lo que ha cambiado es
      que ya no es lo único que falta**ba**.
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

  - 📏 **Qué la bloquea EXACTAMENTE (2026-08-20, medido).** Las notas anteriores
    decían «levantar el stack y correr `lang-switcher.spec.ts`». Se intentó, y esa
    frase esconde dos requisitos que no son lo mismo:
    1. **Un api-server en `localhost:8001`.** El spec **no mockea nada**
       (`grep -c 'page.route' e2e/lang-switcher.spec.ts` → 0), así que no entra en
       el subset que corre CI y necesita backend vivo. El stack desplegado NO
       sirve: su contenedor publica `8000/tcp` hacia dentro y nada al host
       (`docker ps` lo confirma), y el gateway del 8080 contesta 404 en esa ruta.
       Lo que el arnés espera es el `uvicorn api_server.main:app --port 8001` que
       documenta el encabezado de `playwright.config.ts`.
    2. **Un usuario con contraseña.** El spec hace login REAL con
       `E2E_ADMIN_EMAIL`/`E2E_ADMIN_PASSWORD` (defaults `root@example.com` /
       `longenoughpw`) y espera llegar a `/admin/dashboard`.
       Con el 1 sin cumplir, los tres tests caen en el MISMO sitio —
       `expect(page).toHaveURL(/\/admin\/dashboard$/)` en la línea 26, el login que
       no completa—, así que **el rojo no dice nada sobre el idioma**: no es evidencia
       ni a favor ni en contra de esta casilla.
       **Y una advertencia para quien lo monte:** los specs no mockeados crean y
       borran proyectos, agentes y equipos. Apuntar ese api-server a la BD del stack
       vivo le escribe encima. Se levanta contra una **BD desechable**, como hizo el
       carril del córtex con `agentic_cortex_f2`.

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
  - ⏳ **Pendiente (2026-08-20) — `settings/` CERRADO del todo, y cinco pantallas
    más de `projects/*`.** Seis módulos enteros, con su test en los dos idiomas y
    fuera de la `ATTR_ALLOWLIST`:
    - **`settings/sso/` COMPLETO** (8 ficheros): las dos pantallas —OIDC y
      SAML— con sus tarjetas, sus fichas y sus **dos diálogos**, más los tres
      catálogos que vivían como texto en las constantes (`SECRET_SOURCE_LABEL`,
      `KEY_SOURCE_LABEL`, `NAME_ID_FORMATS`), que ahora guardan la CLAVE.
      Namespaces `ssoOidc` + `ssoSaml`. Con esto **el módulo `settings/` no tiene
      ya ninguna pantalla sin migrar**, que era lo que dejaba abierto la nota
      anterior.
    - **`projects/[id]/incoming-webhooks`** (`incomingWebhooks`),
      **`projects/[id]/commands`** (`projectCommands`),
      **`projects/[id]/knowledge-bases`** (`projectKbs`), **`projects/new`**
      (`projectWizard`) y **`projects/[id]/chat` COMPLETO** (`projectChat`: la
      pantalla, el selector de modo, el feed, el composer y el botón de generar
      plan).
      **Contadores medidos ejecutando, y separando lo que es de este carril:**
      de las **188 marcas en 71 ficheros** con las que empezó el día, este carril
      se lleva **34 atributos en 11 ficheros** de la `ATTR_ALLOWLIST` (16 de
      `settings/sso`, 18 de `projects/*`). El resto de la bajada —la guarda
      cerró el día en 119/45— es del otro carril, que migraba `marketplace`,
      `docs`, `assistant`, `notifications` y `memories` **en el mismo árbol de
      trabajo**; mezclar las dos cifras en un solo número haría irreproducible
      cualquiera de las dos.
      **Tests: 28 casos nuevos, todos ejecutados y todos en ROJO antes de
      implementar** (los ES, que fijan que no se rompe el castellano, ya pasaban:
      ésa es la mitad que hace que el rojo signifique algo).
      `app/admin/settings/sso/i18n.test.tsx` (nuevo, 10 casos),
      `app/admin/projects/[id]/chat/i18n.test.tsx` (nuevo, 5) y
      `app/admin/projects/i18n.test.tsx` pasa de 7 a **20**.
      `npm --prefix apps/admin-panel run test -- i18n.test` —el `command:`
      declarado— pasa de 18 ficheros/160 tests a **26 ficheros/246 tests, todos en
      verde**; de esa subida, **2 ficheros y 28 casos** son de este carril y el
      resto del otro, por la misma razón que los contadores de arriba.
      **Seis cosas que enseñó la pasada, y valen más que el contador:**
    1. **La traducción existía y el render la tiraba.** `BUILT_IN_MODES` del chat
       declaraba `labelEs` **y `labelEn`** para los tres modos… y el selector
       pintaba siempre `labelEs`. No es deuda por escribir: es trabajo ya hecho
       que no llegaba a pantalla, y **ninguna de las dos guardas puede verlo**
       (no hay ternario ni atributo). Cuarto ejemplo del mismo aviso. Al pasar el
       catálogo a claves, la cara inglesa deja de poder quedarse sin llamante.
    2. **La pantalla mitad-y-mitad también existe en el otro sentido.** El wizard
       de alta de proyecto tenía `"Could not load templates:"` cableado en
       **inglés**: con el panel en castellano —que es el idioma por defecto— ese
       error salía en inglés desde el día 1. Se ve sólo cuando falla la carga de
       plantillas, así que llevaba ahí sin que nadie lo mirase.
    3. **Un literal que NO se traduce, y por qué.** La KB implícita del proyecto
       se llama «Documentos de {proyecto}», y ese nombre es la **clave del
       find-or-create**: traducirlo haría que subir con el toggle en inglés
       creara una KB nueva en vez de reutilizar la del castellano, y los
       documentos acabarían repartidos en dos. Se extrajo a `implicitKbName()`
       —lo usaban la mutación y el texto de ayuda por separado— y hay un test que
       afirma que en inglés el nombre sigue saliendo en castellano.
    4. **Migrar cambió el tamaño de una pantalla y la otra guarda mordió.**
       `incoming-webhooks/page.tsx` pasó de 797 a **811 líneas** al sustituir
       literales por llamadas, y `check-component-size` falló (límite 800, con la
       `ALLOWLIST` de pantallas VACÍA desde `task_prod16_08`). No se tocó el
       umbral: se troceó con el patrón del propio plan → `webhook-types.ts` (104)
       - `webhook-dialog.tsx` (264) + `page.tsx` (**489**). Las dos guardas
         tirando en direcciones distintas sobre el mismo fichero es una señal, no
         un estorbo: la pantalla ya estaba en el límite.
    5. **Un default silencioso es una regresión futura.** `conversationLabel()`
       (helper puro de `lib/conversation-history.ts`) pintaba dos textos
       castellanos fijos en el selector del historial. Ahora recibe `lang` como
       parámetro **obligatorio, sin default**: con default, el próximo llamante
       reintroduce el fallo sin enterarse. Su test sube de 3 a 5 casos (los dos
       nuevos son las caras inglesas).
    6. **`useT` se llama `t` y `t` es el nombre favorito de los `.map()`.** En el
       wizard, `templatesQuery.data.map((t) => …)` sombreaba el traductor: dentro
       del map, `t("useTemplate")` llamaba al objeto plantilla y la pantalla
       reventaba con «t2 is not a function». Lo cazaron los tests nuevos, no el
       compilador —`t` sigue siendo invocable— y por eso el traductor de ese
       fichero se llama `tWizard`.
       **Lo que queda de esta casilla, contado en ficheros y no en adjetivos:**
       el hub `projects/[id]/page.tsx` **con sus seis piezas de
       `components/projects/`** y `lib/project-governance.ts` (que guarda sus
       etiquetas y sus mensajes de error en castellano), `plans/` y
       `plans/[planId]/*` (14 ficheros), `mcp-servers/*` (8), `tasks/page.tsx`
       —que reparte su texto con `components/tasks/*`, compartido con `board` y
       con `plans/[id]/escalated`, o sea que no es de este módulo solo—, y
       `agent-kbs-section.tsx` de `agents/*`, que es de otro carril. **No se
       marca `[x]`**: `projects/*` no está migrado mientras el hub siga en
       castellano.
       **Verificación ejecutada, toda desde `apps/admin-panel`:** `npx tsc
--noEmit` **limpio**; `npx next lint --max-warnings=0` sin avisos; `npx
prettier --check` de lo tocado OK; `node scripts/check-i18n.mjs` y
       `node scripts/check-component-size.mjs` **en verde**; `npx vitest run
app/admin/projects app/admin/settings lib/conversation-history.test.ts
scripts/check-i18n.test.ts scripts/check-component-size.test.ts` →
       **33 ficheros / 254 tests, todos pasan**.
       **Roturas comprobadas, cada una con su rojo y sólo el suyo:** reintroducir
       `title="Añadir configuración"` en `incoming-webhooks/page.tsx` → `check-i18n`
       sale **exit 1** nombrando el fichero; y devolver el selector de modo del
       chat a `"Discusión"` fijo → cae **1** caso de `chat/i18n.test.tsx` y sólo
       ése.
       **Una excepción que se añadió a mano y conviene auditar:** 20 claves de
       estos namespaces tienen `es === en` y están anotadas en el
       `identicalOnPurpose` de `lib/i18n/i18n.test.ts` — nombre de producto
       (Vault), términos del estándar SAML que el operador copia literales de su
       IdP («Entity ID»), los tres sufijos de URN de `NameID`, los cinco emisores
       de webhook y los cuatro stacks de los presets. Ese invariante existe justo
       para que esa lista no se llene de traducciones pendientes: si crece con
       algo que sí se traduce, deja de servir.
  - ⏳ **Pendiente (2026-08-20, segundo carril del día) — el HUB del proyecto,
    entero.** Es la pantalla que la nota de arriba dejaba fuera diciendo que «NO
    entra a trozos», y no entró a trozos: van juntos
    `app/admin/projects/[id]/page.tsx` (namespace `projectHub`), sus **seis**
    piezas de `components/projects/` —`projectGit`, `projectReviewPreview`,
    `projectRuntimeServices`, `projectGovernance`, `previewLauncher`— y
    `lib/project-governance.ts`, que guardaba los cinco nombres de presupuesto,
    los tres catálogos y los diez mensajes de validación. Con ellos entran dos
    cosas que el enunciado no nombra y sin las cuales la pantalla seguía
    mitad-y-mitad: **`components/ui/markdown-textarea.tsx`** (namespace
    `markdownTextarea`) y la línea de `plans/[planId]/page.tsx` que pasaba el
    título castellano al lanzador de preview.
    **Contadores medidos ejecutando, y separando lo que es de este carril:** de
    las **119 marcas en 45 ficheros** con las que empezó el día, este carril se
    lleva **14 atributos en 6 ficheros** de la `ATTR_ALLOWLIST`
    (`projects/[id]/page.tsx` 1, `plans/[planId]/page.tsx` 1, git-config 2,
    governance 2, runtime-services 6, markdown-textarea 2). Los seis miden
    **cero** al terminar. El resto de la bajada es del otro carril, que migraba
    `plans/*`, `mcp-servers/*` y `login/*` **en el mismo árbol de trabajo**;
    sumar las dos cifras en un número haría irreproducible cualquiera de las dos.
    **Tests: 19 casos nuevos en `app/admin/projects/[id]/i18n.test.tsx`, los 19
    ejecutados antes de implementar y 15 en ROJO** — los 4 verdes eran los ES,
    que fijan que no se rompe el castellano, y son la mitad que hace que el rojo
    signifique algo. `lib/project-governance.test.ts` sube de 10 a 12 (los dos
    nuevos son las caras inglesas, y salieron rojos). Casi todos los casos rinden
    la **página entera** y no las piezas sueltas: es el único render que
    demuestra que las seis secciones traducen a la vez, que es exactamente lo que
    no se puede comprobar por trozos.
    **Cinco cosas que enseñó la pasada, y valen más que el contador:**
    1. **Un componente COMPARTIDO no lo migra nadie porque no es de nadie.**
       `markdown-textarea` valía 2 atributos en el mapa y lo montan **22
       pantallas**, entre ellas `knowledge-bases`, `teams`, `memories`,
       `projects/new` y `agents/*`, todas dadas por migradas. Su barra de
       pestañas («Editar» / «Vista previa»), su ayuda de sintaxis y su estado
       vacío salían en castellano dentro de diálogos por lo demás ingleses. Es la
       variante de la ola 7 en su forma más pura: **el guard mira ficheros, no
       pantallas**, así que esa deuda no se la cargaba ninguna de las 22 y por eso
       llevaba ahí desde el principio. Migrarlo arregla las 22 de una vez.
    2. **Cuando el castellano vive en el LLAMANTE, migrar el componente no
       traduce nada.** `PreviewLauncher` recibía `title?: string` y sus **dos**
       llamantes le pasaban su literal (`"Preview de la app (proyecto)"` y
       `"…(este plan)"`). Traducir el componente entero habría dejado las dos
       pantallas igual. El prop se ha **retirado**: el título y la descripción
       salen del diccionario elegidos por `scope`, que es el dato que ya
       distinguía los dos casos. Un prop de texto es una puerta abierta a la
       deuda; quitarlo la cierra en el tipo, no en la disciplina.
    3. **Otro módulo puro invisible a las dos guardas**, el mismo caso que
       `lib/assistant.ts` de la casilla 04: `lib/project-governance.ts` tenía los
       cinco nombres de presupuesto, los tres catálogos (periodos, modos de
       revisión, presupuestos) y **diez** mensajes de validación en castellano
       fijo. Ahora guarda la CLAVE y `governanceProblems(form, lang)` recibe el
       idioma como parámetro **obligatorio y sin default** — con default, el
       próximo llamante reintroduce el fallo sin enterarse (misma decisión que
       `conversationLabel`).
    4. **La fecha y el número también tienen idioma, y el arreglo obvio está
       prohibido.** Había nueve `toLocaleString("es-ES")` cableados en el panel;
       dos de ellos en este lote (la fecha del último sync de git y el techo de
       tokens de plataforma). El arreglo natural —`lang === "es" ? "es-ES" :
"en-GB"`— es justo el ternario que `check-i18n` prohíbe, y con razón: es la
       misma decisión repetida en nueve sitios. Va al diccionario como
       `common.dateLocale`, que no es texto de UI pero sí un dato por idioma.
       Quedan siete llamantes en ficheros de otros carriles.
    5. **Y nueve fugas más del cuerpo crudo del backend** (`task_prod16_05`, que
       iba por 17 sitios y va por **26**): seis pintaban `.body` a pelo
       —git-config ×2, governance, runtime-services, review-preview y
       preview-launcher, las cuatro últimas con el `e instanceof ApiError ? e.body
: String(e)` escrito en línea que no sale al buscar por nombre de función—
       y **tres más por una vía que el enunciado no contempla**: el hub usaba
       `error?.message`, y `ApiError.message` es `api {status}: {body}`, o sea el
       cuerpo crudo con un prefijo. Todas a `useErrorText()`, y hay un caso que lo
       fija: un 500 con `<html>nginx traceback</html>` NO debe aparecer en
       pantalla.
       **Un error propio que conviene dejar escrito**, porque lo caza el toolchain y
       no el ojo: sustituir la clave `saveError` de `projectGit` con un
       `str.replace` global sobre un diccionario de 6.000 líneas se llevó **también**
       las de `agents` y `projectCommands`, que tienen el mismo texto palabra por
       palabra. Lo cazó `tsc --noEmit` en dos líneas exactas. En un diccionario
       grande, un reemplazo por texto no es una operación local: o se ancla al
       namespace o se comprueba con el compilador antes de creerse el diff.
       **Lo que queda de esta casilla**, contado en ficheros: `plans/`,
       `plans/[planId]/*`, `mcp-servers/*` y `tasks/page.tsx` con
       `components/tasks/*` —el otro carril de esta misma ola— y
       `agent-kbs-section.tsx` de `agents/*`. **No se marca `[x]`.**
       **Verificación ejecutada, toda desde `apps/admin-panel`:** `npx vitest run`
       (suite completa) → **1.354 passed / 156 ficheros, 1 fallo** que NO es de este
       carril: el invariante `identicalOnPurpose` de `lib/i18n/i18n.test.ts` le
       faltan 20 claves de los namespaces `planCost`, `agentRole`, `mcpServers` y
       `planDetail`, del otro carril, que las añadirá con su lote (las 8 de este
       carril sí están). `npx tsc --noEmit` **limpio**; `npx next lint
--max-warnings=0` sin avisos; `npx prettier --check` de lo tocado OK;
       `node scripts/check-component-size.mjs` en verde.
       **Roturas comprobadas, cada una con su rojo y sólo el suyo:** devolver
       `placeholder="••• (vacío = conservar)"` a git-config → `check-i18n` sale
       **exit 1 nombrando el fichero** (o sea que el trinquete que acaba de soltar
       esas seis entradas MUERDE); volver la pestaña del editor markdown a «Vista
       previa» fija → cae **1** caso y sólo ése; y hacer que
       `executionBudgetLabel` resuelva siempre en castellano → caen **3**, los tres
       que afirman la cara inglesa del módulo puro.
       **Aviso de coordinación (no es de este carril y bloquea `check:i18n`):**
       `components/login/i18n.test.tsx`, nuevo hoy, tiene `lang === "es"` **dentro
       de un comentario en prosa** (línea 21), y el trinquete de ternarios —a
       diferencia del de atributos— no exime los `.test.tsx`. `node
scripts/check-i18n.mjs` sale **exit 1** por eso, más un segundo ternario vivo
       en `plan-spec-types.ts`. No se toca desde aquí ni se afloja la guarda por
       ello: lo arregla su carril reescribiendo el comentario. Y quedan tres
       entradas de `mcp-servers` en la `ATTR_ALLOWLIST` ya a cero (avisos, no
       errores), que también son suyas.
       **Una excepción que se añadió a mano y conviene auditar:** 8 claves de estos
       namespaces tienen `es === en` y están anotadas en el `identicalOnPurpose` de
       `lib/i18n/i18n.test.ts` — los cuatro nombres de sub-sección que la UI
       castellana YA escribía en inglés («Chat», «Tasks», «Knowledge Bases», «MCP
       servers»), «irreversible», y las siglas del formulario de git y de servicios
       («PAT (HTTPS)», «Token (PAT)», «Alias (hostname)»), que son lo que el
       operador copia de la consola de su proveedor.
  - ⏳ **Pendiente (2026-08-20, tercer carril del día) — `plans/*`,
    `mcp-servers/*` y `tasks/*` de `projects/`, los tres COMPLETOS.** Con el hub
    del carril anterior, `projects/*` queda migrado salvo lo que nunca fue suyo.
    Entran **32 ficheros** y diez namespaces nuevos:
    - **`projects/[id]/plans/` + `plans/[planId]/*`** (16 ficheros): el listado y
      el detalle con sus quince piezas —cabecera de estado, preflight, ciclo de
      vida, validación humana con su diálogo de rechazo, retro, deep links,
      correcciones del rechazo, sincronización al Kanban con su diálogo, diff de
      código, comentarios, secciones presentacionales y el editor del spec.
      Namespaces `plansList`, `planDetail` y `planStatus`. Arrastran
      `lib/plan-dag.tsx`, `lib/plan-gantt.tsx` y `lib/plan-spec-edit.ts` (los
      mensajes de validación del editor, en un módulo puro).
    - **`projects/[id]/mcp-servers/`** (9 ficheros): la pantalla, la ficha, el
      diálogo con sus opciones avanzadas, «Probar conexión» con la importación
      selectiva de tools, el flujo OAuth y la política rol→tool. Namespaces
      `mcpServers` y `agentRole`.
    - **`projects/[id]/tasks/page.tsx` con la ficha COMPARTIDA de
      `components/tasks/*`** (3 ficheros) y `components/ui/view-toggle.tsx`.
      Namespaces `projectTasks`, `taskStatus`, `taskDetail`, `taskActions` y
      `viewToggle`.
      **Contadores medidos ejecutando, y separando lo que es de este carril:** de
      las **119 marcas en 45 ficheros** con las que empezó el día, este carril se
      lleva **28 atributos en 11 entradas** de la `ATTR_ALLOWLIST` (14 de
      `plans/*` + `mcp-servers/*` + los dos diagramas de `lib/`, y 14 de
      `tasks/*` + `components/tasks/*`). La guarda cerró el día en **77 atributos / 28
      ficheros**; el resto de la bajada es de los otros dos carriles, que migraban
      el hub del proyecto y `marketplace`/`docs`/`assistant` **en el mismo árbol de
      trabajo** — sumar las cifras en un solo número haría irreproducible
      cualquiera de las tres.
      **Tests: 4 ficheros `i18n.test.tsx` nuevos con 54 casos, los 54 ejecutados
      antes de implementar y 42 en ROJO.** Los 12 verdes eran los ES, que fijan que
      no se rompe el castellano: son la mitad que hace que el rojo signifique algo.
      `components/login/i18n.test.tsx` (11), `mcp-servers/i18n.test.tsx` (17),
      `plans/i18n.test.tsx` (13) y `tasks/i18n.test.tsx` (13). Además
      `plan-status-header.test.tsx` sube de 9 a 10 casos y
      `lib/plan-spec-edit.test.ts` de 8 a 10 (los nuevos son las caras inglesas, y
      los tres salieron rojos).
      **Siete cosas que enseñó la pasada, y valen más que el contador:**
    1. **Un fichero a MEDIO migrar es indistinguible de uno migrado para las dos
       guardas, y es el peor caso visto hasta ahora.** `plan-cost-section.tsx` ya
       usaba `useT("planCost")` y no tenía ni un atributo con castellano —o sea,
       limpio para las dos señales y fuera de las dos allowlists— y seguía
       pintando el título de la tarjeta (dos veces), el texto de carga, el estado
       vacío, las **dos** cabeceras de tabla y los dos totales en castellano
       fijo. Los tres avisos anteriores del plan eran sobre ficheros SIN migrar
       que la guarda no veía; éste es sobre uno que la guarda daba por hecho.
    2. **`STATUS_LABEL` de plan existía DOS veces, copiado byte a byte** en
       `plans/page.tsx` y en `plans/[planId]/plan-spec-types.ts`. Traducir dos
       copias del mismo enum del backend es garantizar que divergen en cuanto
       alguien añada un estado, así que se quedó una: el mapa guarda la CLAVE y
       el listado lo importa. Lo mismo obligó a hacer el catálogo de estados de
       TAREA un namespace compartido (`taskStatus`), porque `app/admin/board`
       tiene hoy su tercera copia — ese fichero es de otro lote, pero ya no
       tendrá que volver a escribir el texto.
    3. **`ROLE_LABEL` repitió el caso de `MEMORY_SCOPE_OPTIONS` al pie de la
       letra.** Vive en `mcp-server-types.ts` (módulo puro, invisible a las dos
       guardas) y lo consume también
       `components/marketplace/deployment-config-form.tsx`, que estaba **dado por
       migrado** desde su propia ola y aun así pintaba los diez roles de agente
       en castellano con el toggle en EN. Namespace compartido `agentRole` y la
       constante guardando la clave: arregla las dos pantallas y la cara inglesa
       ya no puede quedarse sin llamante.
    4. **`components/ui/view-toggle.tsx` nunca tuvo entrada en la allowlist**, y
       llevaba «Cambiar vista» y «Lista» cableados desde el principio: ninguna de
       las dos palabras lleva tilde ni está en `SPANISH_WORDS`, así que el
       detector le veía **cero**. Lo montan exactamente las dos pantallas de este
       lote. Y lo importante: al comprobarlo por mutación, devolverlo a
       `label="Lista"` **no rompe `check-i18n`** — sólo lo caza el assert que se
       añadió a `tasks/i18n.test.tsx` a raíz de esa comprobación. Sin la
       mutación, ese test habría quedado siendo una guarda que no puede fallar
       (§4 de `verificar-antes-de-implementar`).
    5. **Tres helpers PUROS con castellano fijo, y `lang` obligatorio sin default
       en los tres.** `lib/plan-spec-edit.ts` (los mensajes de validación del
       editor del spec, incluido el ciclo del DAG), `phaseLabel()` (el rótulo
       «Fase 3») y `describeTaskMoveError()` (por qué se revierte un arrastre del
       Kanban). Ninguno lo veía ninguna de las dos guardas. Con default, el
       próximo llamante reintroduce el fallo sin enterarse — misma decisión que
       `conversationLabel()` y `governanceProblems()`.
    6. **El separador decimal también es del idioma.** `formatTokens()` y
       `formatCostRange()` tenían `"es-ES"` cableado, así que el panel en inglés
       escribía «812,3k» donde un lector inglés lee «812.3k». Se resuelven con
       `common.dateLocale` —el mecanismo que el carril del hub introdujo el mismo
       día— y **no** con un mapa propio: dos mecanismos para el mismo dato
       divergen. Dos de los siete llamantes que aquella nota dejaba pendientes en
       «ficheros de otros carriles» eran éstos.
    7. **La advertencia del plan sobre `tasks/page.tsx` era exacta, y la
       respuesta fue migrar lo que arrastra.** Su texto no es suyo: está
       repartido con `components/tasks/*`, que montan además `app/admin/board` y
       `app/admin/plans/[id]/escalated`. Migrar sólo el `page.tsx` habría dejado
       el Kanban del proyecto abriendo una ficha en castellano. **Lo que este
       lote NO se lleva, dicho en ficheros:** el texto PROPIO de `board/page.tsx`
       (4 atributos) y de `escalated/page.tsx` (2) — sus cabeceras, columnas y
       estados vacíos. Lo que les deja es la ficha de tarea ya bilingüe.
       **Verificación ejecutada, toda desde `apps/admin-panel`:** `npx tsc --noEmit`
       **limpio**; `npx next lint --max-warnings=0` sin avisos; `npx prettier
--check` de lo tocado OK; `node scripts/check-i18n.mjs` y
       `node scripts/check-component-size.mjs` **en verde**; `npm run test --
i18n.test` —el `command:` declarado— pasa de 26 ficheros/246 tests a **31
       ficheros / 319 tests, todos en verde**; y la suite completa `npx vitest run`
       → **157 ficheros / 1368 tests, todos en verde**.
       **Roturas comprobadas, cada una con su rojo y sólo el suyo:** reintroducir
       `title="MCP servers del proyecto"` en `mcp-servers/page.tsx` → `check-i18n`
       sale **exit 1** nombrando el fichero; devolver `Código de verificación` fijo
       al desafío MFA → cae **1** caso de `components/login/i18n.test.tsx`; volver a
       `{ROLE_LABEL[role]}` en el formulario del marketplace → lo caza `tsc`
       (`TS6133`: el traductor deja de usarse); y forzar «En curso» en una columna
       del Kanban de tareas → cae **1** caso y sólo ése.
       **Y un rojo de e2e que NO era de este cambio, con su diagnóstico y su
       arreglo.** El plan exige correr `e2e/mcp-test-connection.spec.ts` y
       `e2e/mcp-config-ui.spec.ts` juntos y en ese orden al tocar `mcp-servers/*`
       (por el 401 que cerraba la sesión, arreglado el 08-20). Se corrieron: **7 de
       11 en rojo**, y los 7 son EXACTAMENTE los que pulsan `mcp-add-button`
       —tres de `mcp-config-ui` y los cuatro de `mcp-test-connection`—; el que abre
       el MISMO diálogo por el lápiz de edición pasa. La correlación es la causa:
       `mcp-add-button` sale en el PRIMER render (vive en la cabecera, antes de que
       resuelva la consulta del proyecto), así que Playwright lo encuentra y lo
       pulsa **antes de que React haya hidratado** y el click no llega a ningún
       handler; el lápiz sólo existe después de que la consulta pinte la ficha, o
       sea después de la hidratación, y por eso ése pasa. Bajo `next start`
       —precompilado, como en CI— la hidratación gana la carrera y el spec pasaba;
       bajo `next dev` en una máquina con cinco agentes trabajando, no. Es la misma
       trampa que `playwright.config.ts` ya tiene anotada para CI.
       **El arreglo no afloja ninguna aserción**: los cinco sitios esperan ahora el
       estado vacío de la lista —que sí depende de la consulta— antes de pulsar.
       **Y la comprobación, ejecutada**: `NEXT_PUBLIC_API_URL=http://localhost:8001
npx next build` (construye, 65 páginas) y después
       `E2E_WEBSERVER_CMD="npm run start" npx playwright test
e2e/mcp-test-connection.spec.ts e2e/mcp-config-ui.spec.ts` →
       **11 de 11 en verde (1,1 min)**, en el orden que el plan exige. O sea que los
       7 rojos eran latencia de `next dev`, no una regresión — y ahora tampoco
       dependen de ella.
       Y la primera de las tres pasadas hay que descartarla entera: se lanzó con un
       `next build` en paralelo escribiendo el mismo `.next`, y ahí el fallo fue
       `net::ERR_ABORTED` en el propio `page.goto`. **Dos specs de Playwright no se
       pueden correr contra un servidor de desarrollo que otro proceso está
       reconstruyendo**, y confundir eso con un rojo del código cuesta media hora.
       Corolario para quien venga: si estos dos specs salen rojos, **antes de mirar
       el código, córrelos sobre `next start`**.
       **Una excepción que se añadió a mano y conviene auditar:** 26 claves de estos
       namespaces tienen `es === en` y están anotadas en el `identicalOnPurpose` de
       `lib/i18n/i18n.test.ts` — ocho nombres de rol de agente que la UI castellana
       ya escribía en inglés (los otros dos, «Arquitecto» y «Especialista», SÍ se
       traducen), la jerga del dominio («tool», «Runs», «Backlog», «Ready»,
       «Tasks», «Plan», «Kanban»), «Pull request» y «Gantt» como nombres propios, y
       «ID»/«Total»/«est.», que coinciden. Ese invariante existe justo para que esa
       lista no se llene de traducciones pendientes.
       **`[x]` que no se pone, y con qué falta exactamente:** el enunciado nombra
       `agents/*`, y de ahí sigue faltando `agent-kbs-section.tsx` (1 atributo), que
       es de otro carril. Con `projects/*` cerrado, es lo ÚNICO que le queda a esta
       casilla de lo que enumera — así que la marca quien cierre ese fichero, no
       este carril.
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
  # tests**, también ejecutado. Tras los tres carriles del 08-20 (el hub del
  # proyecto, marketplace/docs/assistant, y plans+mcp-servers+tasks): **31
  # ficheros, 319 tests**, ejecutado también — y la suite completa
  # (`npx vitest run`) en 157 ficheros / 1368 tests, toda en verde.
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
  - ⏳ **Pendiente (2026-08-19, cuarta pasada). Los CINCO módulos que enumeraba
    el enunciado entran ENTEROS; lo que queda ya no es de esta tarea.**
    Migrados al completo, de menor a mayor: **`memories`** (1 fichero),
    **`assistant`** (3 + su `lib/assistant.ts`), **`notifications`** (6, las tres
    pestañas + la bandeja), **`docs`** (12) y las tres pantallas que le faltaban a
    **`marketplace`** (catálogo, marketplace privado y consentimiento de
    permisos). Más dos piezas compartidas que el enunciado no nombra pero que
    rompían la migración de otros: los **cuatro comboboxes**
    (`entity-combobox` + los wrappers de proyecto, equipo y KB) y la **shell de
    videollamada** (`voice-call-shell`, el modo voz del asistente).
    Doce namespaces nuevos (`memories`, `combobox`, `assistant`,
    `assistantModel`, `voiceCall`, `notifications`, `notificationsInbox`, `docs`,
    `docFacets`, `marketplace`, `marketplacePrivate`, `marketplaceConsent`) y
    **seis ficheros de test nuevos con 58 casos**, cada uno rindiendo su pantalla
    en los DOS idiomas y afirmando en ambos sentidos:
    `app/admin/memories/i18n.test.tsx` (10),
    `app/admin/assistant/i18n.test.tsx` (11),
    `app/admin/notifications/i18n.test.tsx` (11),
    `app/admin/docs/i18n.test.tsx` (12),
    `app/admin/marketplace/i18n.test.tsx` (12) y
    `components/voice/i18n.test.tsx` (2).
    **Contadores, y la mitad honesta del dato**: la allowlist de atributos pasa
    de **188 en 71 ficheros a 119 en 45**. De esa bajada, **35 atributos en 15
    ficheros son de este carril** (los otros 34 en 11 son del carril de
    `settings/` + `projects/`, que corría en paralelo el mismo día): el contador
    es COMPARTIDO y leerlo como si fuera de una sola tanda infla el trabajo
    propio. `--strict` sigue saliendo 1.
    **Lo que enseñó esta pasada, que vale más que el contador:**
    1. **`docs` es el caso extremo del aviso que las tres notas anteriores
       venían dando.** Doce ficheros, ~2.300 líneas, y las dos guardas le veían
       **ocho atributos en cinco ficheros y cero ternarios**. Su deuda entera
       vivía en texto JSX suelto: los seis estados que tiene cada panel (idle,
       hint, cargando, error, vacío, resultados). Ninguna de las dos señales mira
       ahí. Migrar «lo marcado» habría dejado un visor con la cabecera en inglés
       y los seis estados en castellano.
    2. **`assistant` escondía 30 textos en un módulo PURO.** Las ocho etiquetas y
       ocho descripciones del catálogo de herramientas y los cinco mensajes de
       validación del formulario estaban cableados en `lib/assistant.ts`, que no
       es una pantalla. `ASSISTANT_TOOL_CATALOGUE` lleva ahora la CLAVE del
       diccionario y `validateAssistantIdentity` recibe `lang` —el mismo patrón
       que ya usaba `memoryDetectorState`—, con su test afirmando el mismo fallo
       en los dos idiomas.
    3. **Y un tercer escondite nuevo: el TIPO.** `VoiceOption.gender` era la
       unión de literales `"Mujer" | "Hombre"`, castellano cableado en la firma
       de un tipo, así que el selector de voz decía «Mujer · Dora» con el toggle
       en EN. Cuarta forma de deuda que ninguna guarda ve, después del atributo
       sin tilde, el diccionario privado por fichero y el texto JSX suelto.
    4. **Un bug de traducción que NO era un literal**: la matriz de preferencias
       de notificaciones leía siempre `label_es` del catálogo de eventos, que el
       backend sirve BILINGÜE desde NOTIF-3 (`label_es` + `label_en`). El campo
       `label_en` existía y no lo usaba nadie —el patrón «mecanismo entregado,
       cero llamantes» del §5 de `verificar-antes-de-implementar`—. Resuelto con
       `pickLang`, que además cae al otro idioma si el pedido viene vacío.
    5. **Diecisiete fugas más de `task_prod16_05`** (`error.body` crudo en
       pantalla): tres en `assistant` (chat, toggle e identidad), dos en
       `memories` (la query y el alta), dos en la bandeja de notificaciones —que
       tenía su PROPIA copia de `apiErrorBody`, distinta de la que ya se retiró
       en la pasada de agosto: la 16.ª—, cinco en `docs` (barra lateral ×2,
       buscador, visor y diff) y cinco en `marketplace` (listado privado,
       despublicar, permisos, consentimiento y `publishErrorMessage`, que era
       `errorText` reescrito peor: extraía el `detail` y, si no lo había, pintaba
       el cuerpo crudo). **Van 39 sitios**, y sigue valiendo lo que dijo la
       pasada anterior: la única búsqueda que los encuentra es la de lo que hace
       el código (`instanceof ApiError ? … .body`), no la del nombre.
    6. **Lo único que se deja fuera a propósito, y por qué**:
       `app/admin/marketplace/review/` tiene un diccionario LOCAL
       (`review-i18n.ts`) que ya es bilingüe y correcto. NO es deuda de
       traducción: es el patrón «diccionario privado por fichero» que las notas
       anteriores señalaron como enfermedad. Mudarlo al diccionario global es un
       refactor sin cambio de comportamiento y con conflicto de merge garantizado
       mientras dos carriles escriben en `dictionary.ts`, así que queda anotado y
       sin tocar.
    7. **Del enunciado de esta casilla no queda ningún módulo.** Las 45 entradas
       que siguen en la allowlist de atributos son de otras casillas o de otro
       carril: `cortex`, `inbox`, `approvals`, `approval-policy`, `board`,
       `office`, `dashboard`, `documents`, `eval-quality`, `executions`,
       `human-agents`, `plans`, `agents/[id]`, `developers/*`, el resto de
       `projects/*` y los componentes compartidos de `components/` (tasks,
       projects, evals, executions, shared, layout, cortex, ui) más
       `lib/plan-dag` y `lib/plan-gantt`.
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
  - ✅ **Cerrado el 2026-08-20, y el «autogenerate vacío» del enunciado ya casi se
    puede exigir de verdad.** Las dos notas de arriba describen un mundo que duró
    un día; se dejan porque son el diagnóstico correcto, pero esto es lo que hay
    ahora. El hallazgo de `env.py` **tuvo su propio cambio** (commit `bc521ad4`):
    recorre el paquete en vez de listar imports —**53 módulos, 84 tablas**— y con
    la metadata completa `alembic check` dejó de morir con `NoReferencedTableError`
    y por fin dio veredicto: **162 items de deriva en 23 tablas**. Una ola de
    cuatro carriles lo bajó a **22 items en 16 tablas** declarando en el MODELO los
    índices y las FK que las migraciones habían creado (las migraciones son la
    verdad desplegada; no se escribió ninguna nueva). Lo que eso compró, y era el
    riesgo real: el `--autogenerate` de hoy **no propone ni un solo `drop_index`**,
    donde el de ayer proponía borrar el HNSW del RAG.
    - Sobre las **17 tablas del dominio**, las 11 congeladas de la nota anterior
      son hoy **2**: `remove_fk:plans.fk_plans_conversation_id` y
      `add_constraint:task_dependencies.uq_task_dependencies_pair`, las dos de la
      familia «nombre que la migración puso y el modelo no declara» y **ninguna
      una columna perdida**, que es lo que esta tarea tenía que sostener. Las dos
      se nombran una a una en el test, así que el resto del dominio ya se exige en
      vacío.
    - El test pasó de **tolerar a cerrar**: la banda `50 <= total <= 250` era una
      guarda que no podía fallar (§4 de `verificar-antes-de-implementar`) porque
      comparaba **sin** el `include_object` del proyecto y **97 de sus 119 items
      eran particiones mensuales del ADR 0151** — ruido que nadie puede cerrar y
      que sostenía el suelo. Ahora compara con la política real, mide lo mismo que
      imprime `alembic check`, y el inventario cubre el esquema **entero** y sólo
      puede menguar en las dos direcciones (item nuevo → rojo; item arreglado que
      sigue en la lista → rojo).
    - **`auto_prod16_11_b` sigue sin marcarse**, y ahora por un motivo concreto en
      vez de por una deuda difusa: de los 22 items restantes, **7 son
      `modify_nullable` donde el modelo tiene razón y la BD está mal** (las
      migraciones 0108/0109/0112 crearon `created_at`/`updated_at` sin
      `nullable=False`; hay 163 columnas NOT NULL en 95 tablas y sólo estas 7
      nullables). Cerrarlos pide **una migración que endurezca el esquema de
      producción, y eso lo firma el operador**, no un agente. Los otros 15 sí son
      model-side y aditivos.
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
