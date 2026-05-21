# Cómo ver los tests E2E (Playwright)

Cinco maneras de **ver** lo que hacen los Playwright specs. Cada una
responde a una necesidad distinta. Esta guía cubre cuándo usar cada
una, qué se ve, qué pasa cuando no se ve, y los atajos útiles.

> **CRÍTICO — lanza siempre desde TU terminal PowerShell.**
>
> Los modos visibles (`-Headed`, `-Ui`, `--debug`) abren ventanas
> reales del sistema operativo. Si los lanzas desde un terminal
> incrustado en una herramienta externa (un agente IA, un shell vía
> RDP/SSH, un editor con backgrounding raro), Windows puede abrir el
> proceso en una sesión no-interactiva y la ventana **no llega a tu
> escritorio**. Síntoma: "no veo nada". Solución: abre PowerShell de
> verdad y relanza el mismo comando.

## Resumen

| Modo                        | Comando                                                      | Navegador             | Cuándo                                               |
| --------------------------- | ------------------------------------------------------------ | --------------------- | ---------------------------------------------------- |
| **Headless** (CI / default) | `.\scripts\dev\run-e2e.ps1`                                  | invisible             | Validación rápida, CI, regresión                     |
| **Headed**                  | `.\scripts\dev\run-e2e.ps1 -Headed`                          | Chromium real         | Ver la app real haciendo las cosas                   |
| **Headed + SlowMo + Grep**  | `.\scripts\dev\run-e2e.ps1 -Headed -SlowMo 2000 -Grep "..."` | Chromium muy lento    | Demo paso a paso, **un único test** en ventana única |
| **UI Inspector**            | `.\scripts\dev\run-e2e.ps1 -Ui`                              | invisible + GUI       | Depurar un test que falla (time-travel)              |
| **Debug paso a paso**       | `npx playwright test --debug e2e/foo.spec.ts`                | Chromium + Inspector  | Avanzar acción por acción manualmente                |
| **Navegación manual real**  | `.\scripts\dev\up.ps1` → abre `localhost:3000/login` tú      | tu navegador habitual | Probar la app como un usuario, sin tests             |

Filtros disponibles en `run-e2e.ps1`:

| Flag            | Para qué                                                                           |
| --------------- | ---------------------------------------------------------------------------------- |
| `-Spec <path>`  | Limita a un archivo `.spec.ts` (forward slash, p.ej. `e2e/project-wizard.spec.ts`) |
| `-Grep <texto>` | Filtra por **título del test**. Sustring o regex. Útil para abrir UNA sola ventana |
| `-SlowMo <ms>`  | Delay entre acciones cuando hay `-Headed`. 400 = rápido, 1500-2500 = demo          |

> **Atención con las rutas**: el spec va con `/` (forward slash).
> Playwright trata el argumento como **regex** y los backslashes
> de Windows lo rompen — el wrapper lo normaliza por ti, pero si
> invocas directo `npx playwright test e2e\foo.spec.ts` te dirá
> `No tests found.`

---

## Modo 1 — Headless (default)

Lo que corre el CI y el flujo manual de validación. Sin ventanas, lo
más rápido. Si pasa, pasa.

```powershell
.\scripts\dev\run-e2e.ps1
```

Equivalente directo a Playwright:

```powershell
cd apps\admin-panel
npx playwright test
```

Cuándo lo quieres: cada vez que termines una pantalla nueva y antes
de commitear. Si solo pasan los unit + integration y no este, tienes
un bug que no captura tu modelo Python.

---

## Modo 2 — Headed (Chromium visible)

Lanza un **Chromium real** que ves en tu escritorio. Pulsa, escribe,
navega como un usuario. Se cierra al terminar el test.

```powershell
.\scripts\dev\run-e2e.ps1 -Headed
```

**Limitación 1: velocidad.** Cada test pasa en segundos. Si parpadeas
te lo pierdes.

**Solución**: `-SlowMo <ms>` añade ese delay entre cada acción
(click, fill, navigate). Recomendaciones:

- `-SlowMo 400` — rápido pero perceptible
- `-SlowMo 800` — lectura cómoda
- `-SlowMo 1500-2500` — demo a otra persona, ritmo "explicar"

```powershell
.\scripts\dev\run-e2e.ps1 -Headed -SlowMo 1500 -Spec e2e/agents-catalog.spec.ts
```

**Limitación 2: ventanas múltiples.** Entre tests Playwright
**cierra el contexto del browser** y abre uno nuevo. Si corres N
tests, ves N Chromiums abrir-cerrar en cadena. Confuso si lo que
quieres es "demostrar UN flujo".

**Solución**: `-Grep <texto>` filtra al test concreto cuyo título
contenga ese texto. Sustring o regex:

```powershell
.\scripts\dev\run-e2e.ps1 -Headed -SlowMo 2000 `
    -Spec e2e/project-wizard.spec.ts `
    -Grep "picking"
```

Eso corre **solo** el test `picking a template advances to step 2`
y abre una única ventana de Chromium del principio al fin.

### Qué deberías ver, paso a paso

Con `-Headed -SlowMo 2000 -Spec e2e/project-wizard.spec.ts -Grep "picking"`:

1. (0-10 s) El stack docker valida health, las migraciones se
   aplican, el seed corre. Sin ventana todavía. Mira la consola.
2. (10-15 s) Chromium abre. Pantalla en blanco al principio.
3. (15-17 s) URL cambia a `/login`. Aparece el formulario.
4. (17-23 s) Se escribe `root@example.com` y `longenoughpw`
   _carácter a carácter_. Clic en "Sign in".
5. (23-27 s) URL pasa a `/admin/dashboard`. Aparece la salud de
   servicios.
6. (27-30 s) URL pasa directa a `/admin/projects/new` (el test va
   por URL, no clica el botón del dashboard).
7. (30-35 s) Aparece el grid con las 8 plantillas.
8. (35-40 s) Hover sobre la card "Plantilla: API REST", clic en
   "Usar plantilla →".
9. (40-44 s) La pantalla pasa al **Step 2** del wizard con el campo
   "Nombre" prefilled como `API REST` y el panel "Preview" a la
   derecha.
10. (44-48 s) Clic en "Cambiar plantilla". Vuelve al Step 1.
11. (48-50 s) Chromium cierra. El test finaliza con ✔.

Si no ves al menos hasta el paso 6, hay un problema. Revisa la
sección de troubleshooting al final.

### Cómo se implementa `-SlowMo`

Playwright **no tiene flag `--slow-mo`** en CLI; se configura por
`launchOptions.slowMo` en `playwright.config.ts`. El wrapper exporta
`E2E_SLOW_MO` y el config lo lee:

```ts
// apps/admin-panel/playwright.config.ts
launchOptions: {
  slowMo: Number(process.env.E2E_SLOW_MO ?? 0),
}
```

Si pasas `-SlowMo` sin `-Headed`, el wrapper lo ignora (no tiene
sentido ralentizar lo que no se ve).

---

## Modo 3 — UI Inspector (interactivo, time-travel)

Lo más potente para **depurar un test que falla**. Abre una app
Electron-like con la lista de tests, una timeline de acciones, y un
panel grande que muestra el DOM capturado en cada paso. No es un
navegador en vivo — es una sala de mando con grabaciones.

```powershell
.\scripts\dev\run-e2e.ps1 -Ui
```

### Anatomía de la ventana

```
┌─ Playwright Inspector ──────────────────────────────────────────┐
│  TESTS (izquierda)       TIMELINE (centro)       SNAPSHOT (der) │
│  ▶ admin-login.spec      ⚙ beforeEach            ┌───────────┐  │
│    ✔ login + dashboard   ▸ goto /login           │  DOM en   │  │
│    ✔ wrong password      ▸ fill email            │  el       │  │
│  ▶ agents-catalog        ▸ fill password         │  instante │  │
│    ✔ tabs visible        ▸ click "Sign in"       │  del paso │  │
│    ✔ 11 built-ins        ▸ expect URL dashboard  │  seleccio-│  │
│    ✔ empty state         ▸ click "Proyectos"     │  nado     │  │
│  ▼ project-wizard        ▸ click "Crear proy."   │           │  │
│    ▶ step 1: lists 8  ⏵  ▸ expect step-1         │           │  │
│    ▶ picking advances ⏵                          │           │  │
│                                                  │           │  │
│                          Tabs inferiores:        │           │  │
│                          [Locator|Source|Console│           │  │
│                           |Network|Errors]       │           │  │
└─────────────────────────────────────────────────────────────────┘
```

### Flujo de uso

1. **Lanza** `.\scripts\dev\run-e2e.ps1 -Ui` — espera 5-15s, el
   stack docker arranca, el seed corre, el Inspector abre.
2. **Clic en el ▶ de un test** (o en el ▶▶ global para correr todos).
3. Mientras corre, el Inspector NO te muestra un navegador en
   directo (corre en headless internamente). Va llenando la Timeline.
4. Al terminar: la Timeline tiene una fila por cada acción del test.
5. **Clic en una fila** de la timeline → el panel grande pinta el
   DOM tal cual estaba ANTES de esa acción (por defecto).
6. **Pestañas Before / Action / After** encima del snapshot:
   - **Before**: antes del clic
   - **Action**: el momento exacto (puntero animado)
   - **After**: el resultado
7. **Re-run**: cambia el `.spec.ts`, guárdalo, y si tienes el botón
   "Watch" activado (icono ojo arriba) re-ejecuta automáticamente.

### Las pestañas inferiores

- **Locator** — pasa el ratón sobre el DOM del snapshot y te
  sugiere el selector Playwright idiomático (`getByRole(...)`,
  `getByLabel(...)`). Útil al escribir specs nuevos.
- **Source** — el código del test con la línea del paso resaltada.
  Doble clic sobre un paso → salta a esa línea en tu IDE si tienes
  el plugin Playwright para VS Code.
- **Console** — `console.log` que la app emitió durante el test.
- **Network** — cada petición HTTP con timing. Útil para diagnosticar
  por qué un `expect(...).toHaveURL(...)` se quedó en `/login`
  (probablemente un 401 / 429 en `/auth/login`).
- **Errors** — stack traces de assertions fallidas.

### Atajos

| Atajo                             | Acción                                     |
| --------------------------------- | ------------------------------------------ |
| `Ctrl+Enter`                      | Run del test enfocado                      |
| Espacio sobre un paso de timeline | Toggle Before / After                      |
| Doble clic en un paso             | Salta al código de ese paso (panel Source) |
| Icono ojo (toolbar)               | Activa Watch mode (re-run al guardar)      |

### Cuándo falla "veo el panel gris"

Si das play y el snapshot grande queda gris:

1. **No has seleccionado un paso**. Clic en cualquier fila de la
   Timeline. Si la timeline también está vacía → el test no ha
   corrido (mira la columna izquierda buscando ❌).
2. **El paso es "non-snapshottable"** (un `beforeEach` puro de
   código sin tocar página). Salta al siguiente que sí tenga DOM.
3. **El test arrancó antes de que el dev server estuviera listo**.
   Mira Console / Network: si ves `ERR_CONNECTION_REFUSED`, el
   webServer del config no llegó a tiempo. Sube el timeout en
   `playwright.config.ts` o relanza.

---

## Modo 4 — Inspector con pausas (debug paso a paso)

Para cuando quieres **avanzar acción por acción manualmente**. Es el
modo más interactivo de todos: abre Chromium + un panel "Playwright
Inspector" con botones Step / Resume.

No está envuelto en `run-e2e.ps1` (es para depurar fino), pero es
fácil:

```powershell
# 1. Levanta el stack manualmente (al script de debug le estorba
#    que run-e2e haga su propio webServer).
.\scripts\dev\up.ps1

# 2. Asegúrate de tener seeds y admin user (si no, los aplicas
#    una sola vez con: .\scripts\dev\run-e2e.ps1 (en otra terminal,
#    espera a que falle si te queda sin compilar; con tener los
#    seeds + admin promovido ya vale)).

# 3. Lanza el spec en modo debug.
cd apps\admin-panel
$env:E2E_ADMIN_EMAIL = "root@example.com"
$env:E2E_ADMIN_PASSWORD = "longenoughpw"
$env:NEXT_PUBLIC_API_URL = "http://127.0.0.1:8001"
npx playwright test --debug e2e/project-wizard.spec.ts
```

Te abre dos ventanas:

- **Chromium real** (visible)
- **Playwright Inspector** mini con botones: ▶ Resume, ⏭ Step over,
  el código del test, y un botón "Record" para grabar nuevas acciones.

Es modal: tu test queda **pausado en la primera acción**. Pulsas
"Step" para avanzar una acción cada vez, ves el resultado en Chromium
y en el Inspector. Útil cuando un test pasa pero el comportamiento
visual no es el que esperabas.

---

## Modo 5 — Navegación manual real (sin tests)

Si lo que quieres es **probar la app como usuario**, no ver un test
ejecutarse, esto es lo más simple y lo más real:

```powershell
.\scripts\dev\up.ps1
```

Te imprime las URLs y deja todo el stack vivo:

```
Admin panel:   http://localhost:3000/login
               (root@example.com / longenoughpw)
API docs:      http://127.0.0.1:8001/docs
```

Abres `http://localhost:3000/login` en **tu navegador habitual**
(Chrome, Firefox, Edge), te logueas, y navegas a mano. Es la
experiencia real de usuario.

Cuando termines:

```powershell
.\scripts\dev\down.ps1
# o, si quieres bajar también docker:
.\scripts\dev\down.ps1 -Docker
```

**Tip**: si te sale 401 al loguear, probablemente el rate limit
está saturado de runs anteriores. Limpia las claves de Redis:

```powershell
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml `
  exec redis redis-cli DEL "rl:login:email:root@example.com" "rl:login:ip:127.0.0.1"
```

---

## Cheat sheet

```powershell
# Healthcheck rápido (CI mode, headless)
.\scripts\dev\run-e2e.ps1

# Ver un solo test, ventana única, ritmo de demo
.\scripts\dev\run-e2e.ps1 -Headed -SlowMo 2000 `
    -Spec e2e/project-wizard.spec.ts `
    -Grep "picking"

# Ver toda la suite headed (varias ventanas en cadena)
.\scripts\dev\run-e2e.ps1 -Headed -SlowMo 800

# Depurar un test que falla con time-travel
.\scripts\dev\run-e2e.ps1 -Ui

# Avanzar acción por acción (debugger)
.\scripts\dev\up.ps1
cd apps\admin-panel
npx playwright test --debug e2e/team-detail.spec.ts

# Navegar la app como usuario real
.\scripts\dev\up.ps1
# Abre http://localhost:3000/login en tu navegador
.\scripts\dev\down.ps1   # cuando termines
```

### Resolución de problemas

| Síntoma                                          | Probable causa                                                                                              |
| ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| `Error: No tests found.`                         | Spec con backslashes; usa `e2e/foo.spec.ts`. El wrapper lo normaliza pero `npx playwright test` directo no. |
| `error: unknown option '--slow-mo=400'`          | Playwright no acepta `--slow-mo` por CLI. Usa `-SlowMo` del wrapper (que setea `E2E_SLOW_MO`).              |
| `EADDRINUSE :::3000`                             | Tienes `up.ps1` corriendo; haz `down.ps1` antes de `run-e2e`.                                               |
| Tests se quedan en `/login` (401/429)            | Rate limit acumulado en Redis; `down.ps1 -Docker` + `up.ps1` resetea.                                       |
| Panel snapshot gris en UI mode                   | Selecciona un paso de la timeline; sin paso seleccionado no hay snapshot.                                   |
| `expect.toHaveURL` falla en cold-start           | Next dev compila bajo demanda y se pasa de los 5s default; `workers: 1` ya está en el config.               |
| `-Headed`: Chromium se abre y cierra al instante | Sube `-SlowMo` a 1500+ o filtra a un test único con `-Grep`.                                                |
| `-Headed`: NO veo ninguna ventana                | Estás lanzando desde un terminal no-interactivo (ej. un agente IA / RDP raro). Lanza desde TU PowerShell.   |
| Veo "login y cierre" sin ver el resto            | Es el aislamiento entre tests: cada test reabre Chromium. Filtra a UN test con `-Grep`.                     |
