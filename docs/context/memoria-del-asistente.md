---
title: "Memoria del asistente — órdenes permanentes, constantes y cola de pendientes"
status: published
created: 2026-07-30
docs_language: es
---

# Memoria del asistente

Claude Code guarda memoria **fuera del repositorio**, en
`~/.claude/projects/<slug-de-la-ruta>/memory/`. Eso la ataba a una máquina: al
cambiar de ordenador se perdía todo el conocimiento acumulado en meses de sesiones.
Este documento y su archivo hermano existen para que eso no pase.

- **Este fichero** es el punto de entrada: lo que **no se deduce del repositorio** y
  hay que saber antes de tocar nada.
- **[`memoria-asistente/`](memoria-asistente/)** es el archivo **verbatim** de las 63
  entradas, tal cual estaban el 2026-07-30, con su índice
  [`MEMORY.md`](memoria-asistente/MEMORY.md). Está ahí para no perder detalle, no
  para leerlo entero.

> **Aviso que va en serio.** El archivo es una **foto del 2026-07-30**, y la mitad de
> sus entradas son historial de trabajo («esto se hizo tal día»). Ese historial
> envejece, y el propio repo documenta que un resumen que dice «esto ya está» sin
> evidencia por ítem **cuesta más caro que no tenerlo**, porque se lee y se cree
> (§1 de [verificar-antes-de-implementar](../03-guides/verificar-antes-de-implementar.md)).
> Trata el archivo como pistas, nunca como verdad: **el código es la verdad**. En esta
> misma sesión un recon mío declaró inexistente un test que llevaba dos días
> commiteado, y una condición de un ADR se tachó por falsa siendo cierta.

## Cómo rehidratar la memoria en otro ordenador

El directorio de memoria se llama según la **ruta del proyecto**, con los separadores
convertidos en `-`. **Averígualo primero, no lo teclees de memoria** — en esta máquina
es `c--laragon-python-agent-ai-multitenant`, en **minúscula**, y en un sistema de
ficheros sensible a mayúsculas la variante con `C` no existe:

```bash
# 1. Encuentra el slug (arranca Claude Code una vez en el repo si aún no existe)
ls ~/.claude/projects/ | grep -i agent-ai-multitenant

# 2. Copia el archivo dentro, con el slug que te haya salido
SLUG=$(ls ~/.claude/projects/ | grep -ix 'c--.*agent-ai-multitenant' | head -1)
mkdir -p ~/.claude/projects/"$SLUG"/memory
cp docs/context/memoria-asistente/*.md ~/.claude/projects/"$SLUG"/memory/
```

```powershell
# Windows (el filesystem no distingue mayúsculas, pero el slug sigue siendo el mismo)
$slug = (Get-ChildItem "$env:USERPROFILE\.claude\projects" |
         Where-Object Name -Match 'agent-ai-multitenant$').Name
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\projects\$slug\memory" | Out-Null
Copy-Item docs\context\memoria-asistente\*.md "$env:USERPROFILE\.claude\projects\$slug\memory\"
```

**Ojo con los subdirectorios:** hay un slug por ruta, así que abrir Claude Code en
`docker/` o en `docker/agent-runtimes/agent-runtime/` da un directorio de memoria
**distinto y vacío** (en esta máquina ya existen los tres). La memoria completa vive
en la del raíz del repo; si trabajas desde un subdirectorio, no esperes encontrarla.

Y al revés: cuando la memoria viva acumule cosas nuevas que merezcan sobrevivir,
**vuelca el directorio otra vez aquí** y actualiza este fichero. Sin ese paso, esto
vuelve a envejecer.

---

## 1. Órdenes permanentes del operador

Valen para toda sesión, no solo para la que las recibió. Son lo más valioso de esta
memoria: **no se deducen del código y no están en ningún otro sitio.**

### Responder en castellano

El operador lo pidió explícitamente. Además la documentación del proyecto es en
español (`docs_language: es`) y el producto soporta solo ES+EN (principio 12).
Código, identificadores y términos técnicos siguen en inglés cuando es lo idiomático
del repo.

### Los entregables van a `docs/roadmap/`

Auditorías, planes y diseños se escriben en `docs/roadmap/<nombre>.md`. **No** crear
`docs/plans/` ni ninguna carpeta paralela: el operador mantiene todo el roadmap
consolidado ahí. Los ADR siguen en `docs/05-architecture-decisions/`.

### Prioridad: código limpio y mantenible

Por encima de soluciones rápidas, y textualmente «por mantenibilidad». En la
práctica: TDD; módulos pequeños y enfocados; seguir los patrones que ya existen;
refactor oportunista donde mejore la estructura **sin reescrituras big-bang ni scope
creep**; pre-commit en verde y nunca `--no-verify`. Lo que toque aislamiento, egress
o arquitectura va por ADR primero.

### ADR `proposed` → implementarlos de forma autónoma

Delegación explícita: analizar los ADR `proposed`, **elegir la mejor opción para el
sistema** e implementarla, sin esperar ratificación. Aplicada dos veces (2026-06-17 y
2026-07-26).

**La excepción importa tanto como la regla:** si un ADR implica una **decisión de
producto nueva**, hay que parar y preguntar. Así se resolvió el ADR 0117 — y salió
barato: la respuesta llegó en un turno y el trabajo se hizo igual. El 2026-07-30 se
aplicó el mismo criterio dejando cuatro ADR (`0133`-`0136`) en `proposed` para el
operador, y cerrando el `0137` por ser técnico.

### Fallo de un run: si es de plataforma, arreglarlo sin preguntar

Delegación textual: «las que vayan fallando, ves revisando el motivo e intenta
corregirlo en caso de que sea de código de la aplicación». Diagnóstico por
`abort_code` + `steps_log` + logs; si la causa es la plataforma → fix con TDD,
rebuild y relanzar; si es comportamiento del agente → `reassign_with_guidance`.
Escalar **solo** lo que exige decisión humana (criterios imposibles, coste, producto).

### Pero NO desbloquear ni relanzar sin verificación previa

Orden del 2026-07-03, y **sigue viva**: no resetear tareas a backlog, no disparar
`promote_ready_plans`, no relanzar ejecuciones — ni aunque la cuota se resetee —
hasta OK explícito. La observación pasiva (monitores de transiciones, `SELECT`) sí
está bien. Convive con la orden anterior: el autofix de **código** sigue delegado; lo
que está gated es **relanzar runs**.

### Push sí, PR no

Autorizado (2026-07-03): «cuando des por finalizadas las tareas, haz los commits y
push en la rama». Abrir o mergear un PR sigue siendo decisión del operador.

### UX de tools y comandos: amigable, no un volcado de enums

Insistió («sobretodo») en que la categorización de tools/comandos y su asignación a
agentes sea intuitiva: grupos con etiquetas humanas en vez de categorías crudas,
buscador, `security_level` como badge con tooltip en lenguaje llano, y para
`shell_exec` la allowlist como chips con **presets por stack** (clic, no teclear).
Es donde un no-experto configura qué puede hacer cada agente.

### Todos los textareas previsualizan Markdown

Existe `components/ui/markdown-textarea.tsx` (`MarkdownTextarea`, pestañas
Editar/Vista previa). **Caso especial:** el composer del chat tiene lógica de
menciones `@` atada al textarea crudo — ahí NO se sustituye el componente, se añade
un toggle que conserva el textarea. Y hay un **SKIP correcto**: lo que no es markdown
(clave SSH, args MCP, JSON de notificaciones, manifest YAML, XML de SAML, certs) se
deja crudo.

### Modelo LLM por agente: default heredable + override

Decisión del operador: default global de plataforma → default por proyecto/tenant →
override opcional por agente, siempre validado contra el catálogo cerrado del ADR
0021 (`claude_sdk` / `copilot` / `azure_foundry` / `ollama`). Evita configurar agente
a agente y permite afinar por rol.

---

## 2. Constantes del proyecto que no se deducen del código

### Ningún plan puede pasar a `completed` sin el PR mergeado

Es regla dura de CLAUDE.md: el criterio 5 de cierre es «PR del plan mergeado». No es
una formalidad — es lo que evita marcar `completed` código que solo existe en una rama.

**Estado a 2026-07-30: ese criterio ya se cumple para todo lo anterior.** El **PR #66**
se mergeó el 2026-07-30 a las 07:32 UTC (merge commit `72fe899b`), y con él los 543
commits que la rama `plan/runs-visor-trabajo` llevaba de ventaja. Los ~46 planes en
`pending_human_validation` cuyo código iba en ese PR ya no están bloqueados por aquí.

> **Y esta entrada es la mejor prueba de por qué este fichero lleva un aviso arriba.**
> Se escribió el mismo 2026-07-30 afirmando que el PR seguía abierto, y era falso:
> el merge había ocurrido esa misma mañana, en medio de la sesión. Un dato de estado
> —«N commits por delante», «el PR está abierto»— caduca en horas. **Regenéralo, no
> lo copies:** `git rev-list --left-right --count origin/master...HEAD` y
> `gh pr view <n> --json state,mergedAt`.

### Los 14 planes `pending_approval` son 195 días-persona de verdad

Medido el 2026-07-30 con un auditor por plan y pasada adversarial: de 163 casillas
abiertas, **121 son `GAP` real**, 25 `PARTIAL`, 10 exigen humano, 6 eran casillas
rancias y 1 la invalidó un ADR posterior.

**Importa por lo que descarta:** el patrón «la mayoría de las casillas pendientes ya
están hechas» vale para los planes **entregados** que quedaron sin marcar, y **no**
para éstos, que están de verdad sin empezar. No prometer cerrarlos en una sesión.

### El orden de trabajo que funcionó

1. Acreditar con tests los planes en `pending_human_validation` — barato y cierra
   planes.
2. Los huecos del córtex, que ya estaban especificados uno a uno.
3. Los `prod-XX`, que son el grueso.

---

## 3. Cola de pendientes que no vive en ningún plan

- **Rama `feat/builtin-customization`** entregada (ADR 0065/0066); el merge quedó
  como decisión del operador y sigue sin resolverse.
- **`persona-section.tsx`** es el único textarea que quedó **diferido** en el sweep de
  Markdown: `PromptLangField` se reutiliza con tres prefijos de id, así que tocarlo
  arrastra testids de varios e2e. Hacerlo donde se pueda ejecutar Playwright.
- **Modo voz del asistente de tenants**: el bug original (stt/tts caídos) se arregló,
  pero quedó apuntado revisarlo end-to-end con la voz del córtex.

---

## 4. Dónde vive cada clase de conocimiento

Este fichero **no duplica** lo que el repo ya guarda mejor. Si buscas:

| Busco…                                    | Está en                                                                                        |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Una trampa del toolchain                  | [`docs/03-guides/gotchas/`](../03-guides/gotchas/) — 69 entradas con síntoma, causa raíz y fix |
| Cómo no perder el tiempo (modos de fallo) | [`verificar-antes-de-implementar.md`](../03-guides/verificar-antes-de-implementar.md)          |
| Qué se hizo y por qué                     | `docs/07-changelog/<plan_id>.md` + `git log`                                                   |
| Estado y tareas de un plan                | `docs/roadmap/<plan_id>.md` (frontmatter + casillas)                                           |
| Una decisión de arquitectura              | `docs/05-architecture-decisions/`                                                              |
| Por dónde va el trabajo ahora             | [`CONTINUE_HERE.md`](../../CONTINUE_HERE.md)                                                   |
| Cómo se trabaja en este repo              | [`CLAUDE.md`](../../CLAUDE.md)                                                                 |

Las cuatro trampas que en su día solo vivían en memoria **ya están en el repo**:
resolución de provider por dos vías, `setpriv` + `HOME`, `caplog` frente al orden de
la suite, y la contaminación entre revisores en paralelo. Verificado el 2026-07-30.

## 5. Qué NO guardar aquí

Para que este fichero no se convierta en el problema que evita:

- **Historial de trabajo** («el día X implementé Y»): va al changelog del plan.
- **Trampas del toolchain**: van a `gotchas/`, con su formato de cuatro secciones.
- **Estructura del código, fixes pasados, nombres de ficheros**: se leen del repo.
- **Cifras y estados** que el frontmatter de un plan ya lleva: se regeneran, no se
  copian. Duplicarlas es exactamente cómo envejece mintiendo un resumen.

Aquí solo va lo que un ordenador nuevo no podría averiguar leyendo el repositorio.
