---
adr: "0103"
title: Recalibración de las guardas de research del runtime — exploración legítima sin fricción (Fase G auditoría 2026-07-03)
status: proposed
date: 2026-07-05
deciders: operador (pendiente)
phase: auditoria-plataforma-2026-07-03
related: ["0089", "0092", "0097"]
docs_language: es
---

# ADR 0103 — Recalibración de las guardas de research del runtime (Fase G)

## Contexto

La auditoría de plataforma 2026-07-03 (`docs/roadmap/auditoria-plataforma-2026-07-03.md`, causa raíz F) y el
síntoma reportado en vivo por el operador —«sigue apareciendo el tema de producir output; la exploración
legítima no funciona» (ejemplo: tarea «Tests de feature»)— apuntan a que las **guardas de novedad** del
runtime del agente castigan lectura legítima y ciclos edit→build. El inventario de restricciones (r1-r7) se
verificó adversarialmente en Opus 4.8; los matizados/refutados se publicaron igual (auditoría §7).

Dos recalibraciones ya se aplicaron limpiamente en esta remediación y **no se re-abren aquí**:

- **G3/r4** — `has_produced` exige `observation.ok` antes de latchear (`graph.py:982-988`), evitando que un
  `shell_exec` denegado desvíe cada trip a `needs_human_review`.
- **G6a/r1** — el allowlist base del SDK suma utilidades de lectura `sed/awk/sort/uniq/cut/tr/echo`
  (`apps/workers/src/workers/execution.py:292-317`, pineado por `tests/unit/test_shell_allowlist_read_utils.py`).

Y se **descartó G8/r7 en la sesión** (reset del `LoopDetector`) porque el conteo ACUMULATIVO es intencional por
ADR 0089 y un test lo pinea (`tests/unit/test_loop_detection.py::test_only_the_repeated_action_trips`: una
acción repetida INTERCALADA con acciones distintas SÍ debe tripar) — aquí se documenta como GATED, no como
hecho.

Este ADR **clasifica** las recalibraciones candidatas restantes en dos cubos según su relación con las
decisiones ya ratificadas (ADR 0089 convergencia/loop-detector, ADR 0092 allowlist deny-by-default) y con los
tests que pinean el diseño intencional. Las **SAFE** se implementan sin gating; las **GATED** requieren
ratificación del operador porque cambian comportamiento aceptado por ADR.

### Estado del código relevante (HEAD, rama `plan/runs-visor-trabajo`)

- `LoopDetector` — conteo acumulativo por fingerprint `(tool,args)`, `threshold=3`, sin reset
  (`docker/agent-runtimes/agent-runtime/agent_runtime/loop_detection.py:24-33`).
- Señales de research por NOVEDAD en `graph.py`: `_RESEARCH_TOOLS` (línea 69, 4 nombres),
  `_read_target` ignora offset/limit (129-142), `_track_research` (955-990) — `read_counts` acumulativo (971),
  `read_churn_streak` estéril (977), latch de `has_produced` con `ok` (982-988).
- Backstop duro `_research_exhausted` (381-413), gated por `has_produced`/`review_retries`/`is_review`
  (invariante D3/D4 de ADR 0089); `_sterile_hard_limit` relativo al presupuesto (98-104).
- `_select_nudge` (992-1030) devuelve el mensaje específico por variante, pero el summary del step está
  **hardcoded** «guidance: stop researching, produce output» (`reflect`, línea 1067).
- Memoria de lecturas: `_READ_DIGESTS_MAX=20` / `_READ_DIGEST_CHARS=100` (94-95), `_harvest_read_digest`
  (1116-1143), render en `_progress_summary` (1150-1186).
- `ToolRegistry` (tools.py:47-99) es `name → fn`: **no** transporta `security_level`/`read_only`.

## Decisión

Se adopta la clasificación SAFE/GATED de la Fase G del plan `guardas-research-por-novedad.md`. Las guardas
**duras** (budgets de iteraciones/tokens/wall-clock, `recursion_limit`, y el `LoopDetector` como red de
seguridad) siguen siendo el techo de terminación garantizado (invariante ADR 0089) en todos los cubos. Nada
específico de un provider entra en el loop común (restricción del operador, ADR 0021/0092).

## Recalibraciones SAFE (implementables sin gating)

No cambian el diseño intencional de ADR 0089/0092 ni rompen ningún test que lo pinee; sólo reducen fricción de
exploración legítima. Todas viven en el loop compartido (`graph.py`) que ejecutan los 4 providers por igual.

### G2 — decay/reset per-target tras turno productivo

- **Dónde:** `graph.py:955-990` `_track_research`. Cuando un producing tool tiene `observation.ok` (ya latchea
  `has_produced`, 982-988), **decaer/resetear** `self.read_counts` (todo el mapa o sólo el target tocado);
  umbrales proporcionales al presupuesto como `_sterile_hard_limit`.
- **Por qué es seguro:** no toca `LoopDetector` ni el escalado-vs-abort (ADR 0089 D2/D3) ni el allowlist
  (ADR 0092). El backstop D4 sigue cubierto por `read_churn_streak` (racha estéril CONSECUTIVA) + budgets, que
  ADR 0089 fija como techo. Ningún test intercala una producción entre lecturas del mismo target y luego afirma
  que el contador sobrevive; `test_research_churn_after_production_escalates_with_deliverable` produce al inicio
  (sin reset intermedio) y corta por `sterile_streak`, así que sigue verde.
- **Test:** bucle TDD — leer `Routes.php` 4× (exploración), `write_file` OK, `phpunit` fallido, re-leer
  `Routes.php` → NO dispara el same-target nudge/trip. Regresión del churn-post-producción intacta.

### G3b — fallos de plataforma no suman esterilidad

- **Dónde:** `graph.py:955-990` `_track_research`, rama research: clasificar `observation.error` por firma de
  PLATAFORMA (`not allowed`, `unknown tool`, `no executor`, `EACCES`/permission, worktree vacío) → NO
  incrementa `read_churn_streak` ni `read_counts`. Un file-not-found ADIVINADO por el agente SÍ sigue estéril.
- **Por qué es seguro:** clasificador estrecho por firma; preserva el pin anti-gaming
  `test_errored_reads_count_as_sterile_not_novel` (paths `nope{i}.php` son file-not-found del agente → siguen
  estériles y no acumulan novedad). No toca ADR 0089/0092. Es además prerequisito de G4a (un `search_code` sin
  executor da `ok=False` y no debe castigar como churn del agente).
- **Test:** 3× `command not allowed` / `unknown tool: search_code` seguidos NO disparan el trip de esterilidad;
  3× file-not-found de paths nuevos SÍ acumulan racha (pin intacto).

### G4a — `search_code` cuenta como research y gana novedad

- **Dónde:** `graph.py:69` añadir `search_code` a `_RESEARCH_TOOLS`; `graph.py:129-142` añadir rama en
  `_read_target` (`search_code:{query|pattern}`) para que gane novedad y NO cuente como mutador
  (`_is_mutating_tool` deja de devolver True para él).
- **Por qué es seguro:** aditivo; ningún test pinea el contenido exacto de `_RESEARCH_TOOLS` ni
  `_read_target('search_code',…)`. Independiente de la decisión wire-or-remove (tools-y-cierre g4): si se
  retira, nunca aparece; si se cablea, queda bien clasificado. Requiere G3b para que sus `ok=False` (sin
  executor hoy) no penalicen.
- **Test:** `_read_target('search_code',{'query':'x'}) == 'search_code:x'`; una llamada `search_code` no
  clasifica como mutador.
- **Nota (fuera del envelope SAFE):** la clasificación por METADATA del catálogo (`security_level=safe`/
  read-only ⇒ research), en vez de una lista fija, exige plumbing nuevo: `ToolRegistry` (tools.py:47-99) es
  `name → fn` y no transporta `security_level`. No lo bloquea ningún ADR, pero está fuera del cambio mínimo
  seguro y se coordina con tools-y-cierre g4 (wire-or-remove).

### G5 — resumen del visor por variante + `safeguard_stats` en el visor

- **Dónde:** `graph.py:1067` — sustituir el summary hardcoded «guidance: stop researching, produce output» por
  el texto de la variante REAL (same-target / esterilidad / ya-produjo-FINISH / repetición); `_select_nudge`
  (992-1030) ya conoce el `kind`/mensaje, basta propagarlo. Además exponer `safeguard_stats` (ya adjunto al
  step de finalize, 1211-1221) en el visor de runs (frontend).
- **Por qué es seguro:** pura observabilidad; no cambia el comportamiento del loop ni ADR 0089/0092. Ningún
  test afirma sobre el string del summary (verificado: «stop researching» no aparece en la suite del runtime).
- **Test:** un turno cuyo nudge es «FINISH» (ya produjo) → el summary del step NO contiene «stop researching»;
  el visor lista `safeguard_stats` por tipo.

### G10 — digests de lectura con más presupuesto y firma de símbolos

- **Dónde:** `graph.py:95` `_READ_DIGEST_CHARS` 100→~400 y `_harvest_read_digest` (1116-1143) extrae la 1.ª
  `def`/`class` para código; rebalancear el nº de entradas mostradas (`_progress_summary` 1173-1176 muestra 12
  hoy) o el cap para que el bloque PROGRESS no supere su presupuesto (~400 tokens).
- **Por qué es seguro:** tuning de presupuesto de prompt; los pines son cap=20 entradas
  (`test_read_digests_are_lru_capped`) y «1.ª línea significativa» (`test_progress_includes_read_digests`),
  ambos preservados. No toca ADR 0089/0092.
- **Test:** el digest de un `.py` incluye su 1.ª `def`/`class`; el bloque PROGRESS renderizado no supera su cap.

## Recalibraciones GATED (requieren ratificación del operador)

Cambian comportamiento aceptado por ADR 0089 (o el hardening anti-gaming del que depende su backstop D4) y
rompen tests que pinean ese diseño.

### G8 — `LoopDetector` con reset por progreso (r7)

- **Cambio:** `loop_detection.py:24-33` — resetear/decaer el conteo del fingerprint `(tool,args)` cuando hubo
  un turno productivo intermedio (write o target nuevo), para que `edit→build→edit→build` con un comando de
  test/build IDÉNTICO no acumule hacia el 4.º trip; y un mutador FALLIDO repetido (`command not allowed`)
  inyecte guidance en el canal sticky en vez de contar (no mutó nada).
- **Conflicto ADR:** **ADR 0089** — el conteo ACUMULATIVO-TOTAL es intencional y el pin
  `tests/unit/test_loop_detection.py::test_only_the_repeated_action_trips` fija que una acción repetida
  INTERCALADA con acciones distintas DEBE tripar a la 4.ª (exactamente el patrón edit→build). Eximir mutadores
  fallidos toca además D3 (clasificación mutador vs read-only). Nota de impacto: r7 fue **refutado como P0** —
  en las 5 ejecuciones reales siempre ESCALÓ a `needs_human_review` (trabajo preservado), nunca hard-abort.
- **Opciones:**
  - **A** — reset del conteo tras turno productivo: rompe el pin (hay que reescribir el test y aceptar que un
    build idéntico intercalado con writes ya no tripa).
  - **B** — excepción SÓLO para producing tools EXITOSOS idempotentes con progreso intermedio; el hard-trip se
    mantiene para mutadores idénticos SIN progreso (relaja el pin, no lo borra).
  - **C** — no tocar el detector (coste bajo: siempre escala con trabajo preservado); sólo mejorar el mensaje.
  - **Recomendación:** **B**, más el sub-cambio (casi-safe, separable) de tratar el mutador FALLIDO repetido
    como guidance en vez de mutador. Requiere ratificar la reescritura del pin `test_only_the_repeated_action_trips`.

### G9 — cache de contenido por target leído (relectura de fichero no modificado = gratis)

- **Cambio:** capa de cache en el tool-call path del runtime: si el agente relee un path no modificado desde la
  última lectura, servir del cache sin round-trip y NO contar esterilidad; invalidar al escribir el path.
- **Conflicto ADR:** **ADR 0089-D4** — el backstop de esterilidad (`read_churn_streak`) existe para cortar el
  read-churn ANTES de `max_iterations`. Hacer no-estéril la relectura desactiva ese corte temprano para un
  modelo que re-lee lo mismo en bucle (sigue quemando iteraciones/tokens por turno-LLM aunque el I/O sea
  gratis) → fuga a max_iterations. Rompe el pin
  `docker/agent-runtimes/agent-runtime/tests/test_research_nudge.py::test_reflect_reread_same_target_builds_churn_even_varying_offset`
  (re-leer el mismo fichero construye churn=5).
- **Opciones:**
  - **A** — cache + no contar esterilidad (máxima ergonomía, pierde el corte D4; reescribir el pin).
  - **B** — cache SÓLO como optimización de I/O (respuesta gratis) pero seguir contando la relectura hacia
    churn/esterilidad (preserva D4; el pin sobrevive) — captura el grueso del beneficio (coste, latencia) sin
    tocar las guardas.
  - **C** — no implementar (G10 + memoria de lecturas ya mitigan la causa raíz de la relectura).
  - **Recomendación:** **B** — desacoplar el ahorro de I/O de la señal de convergencia.

### G1 — offset/limit en la clave del target (r2)

- **Cambio:** `graph.py:129-142` `_read_target` incluiría offset/limit en la clave → paginar dejaría de contar
  como releer el mismo target.
- **Conflicto ADR:** revierte el hardening anti-gaming del 2026-07-01 del que depende la detección same-target
  del backstop D4 de **ADR 0089**; rompe los pines
  `test_research_nudge.py::test_read_target_ignores_offset_and_limit` y
  `test_reflect_reread_same_target_builds_churn_even_varying_offset`. r2 quedó **matizado**: el escenario
  «paginar fichero grande» es INALCANZABLE (el lector builtin no pagina, erra a >1 MB).
- **Opciones:**
  - **A** — implementar G1 (abre el hueco: un re-read con offset variable se disfraza de exploración).
  - **B** — NO implementar: el residual real de r2 es la ACUMULACIÓN sin decay, que cierra **G2 (SAFE)**, no el
    offset.
  - **Recomendación:** **B, rechazar G1**; el offset sigue fuera de la clave mientras el lector no pagine.

## Consecuencias

- La exploración legítima (leer N ficheros nuevos, paginar, ciclos TDD) deja de recibir fricción indebida sin
  degradar la red de seguridad: budgets + `LoopDetector` + `read_churn_streak` siguen siendo el techo de
  terminación (invariante ADR 0089).
- Las SAFE (G2/G3b/G4a/G5/G10) son aditivas al loop compartido → sin impacto en runs en vuelo (viven en la
  imagen `agent-runtime`, instanciada por-run) y sin recrear workers.
- La clasificación de research por metadata (G4b) queda como follow-up con plumbing (pasar `security_level` del
  catálogo al spec del runtime), coordinado con tools-y-cierre g4; no lo bloquea ningún ADR.
- Las GATED (G8/G9/G1) no se implementan hasta ratificación; G8 y G9 se recomiendan en su variante quirúrgica
  (B) que preserva los pines/ADR; G1 se recomienda rechazar.
- ADR 0097 (sesión SDK persistente) sigue `proposed` y es ortogonal: atacaría la causa raíz de la relectura
  (memoria conversacional) sólo para claude_sdk; estas recalibraciones son provider-agnósticas y no dependen de
  él.

## Criterio de aceptación

1. SAFE implementadas con su test en verde y sin romper la suite del runtime existente
   (`test_research_nudge.py`, `test_loop_detection.py`, `test_shell_allowlist_read_utils.py`).
2. Un run explorador (10+ ficheros nuevos) SIN ningún nudge de research en `safeguard_stats`.
3. Un bucle edit→build con comando idéntico intercalado con writes NO dispara `repetitive_loop` **sólo** si se
   ratifica G8 (opción B); mientras tanto, sigue escalando con trabajo preservado (comportamiento actual).
4. El summary del step de un nudge «FINISH» NO dice «stop researching»; el visor muestra `safeguard_stats`.
5. Las GATED registradas con su decisión (implementar opción X / rechazar) firmada por el operador antes de
   tocar `loop_detection.py` o la clave de `_read_target`.
