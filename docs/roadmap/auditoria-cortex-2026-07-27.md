---
title: "Auditoría de los planes del córtex (F1–F5) — checkbox contra código"
status: published
created: 2026-07-27
owner: Claude Code
related_plans:
  [
    "cortex-f1-memoria-cognitiva",
    "cortex-f2-afectivo",
    "cortex-f3-identidad",
    "cortex-f4-autonomia",
    "cortex-f5-voz-avatar",
  ]
related_adrs: ["0074", "0075", "0076", "0077", "0078", "0080"]
docs_language: es
---

# Auditoría del córtex — qué de las 76 casillas era cierto

## Por qué se hizo

Los cinco planes del córtex tenían **76 casillas sin marcar y cero marcadas**, y
`CONTINUE_HERE.md` afirmaba que eran «**rancias**: el córtex está implementado y
desplegado; los planes quedaron sin marcar». Esa afirmación descansaba en tres
comprobaciones puntuales (migración 0093, `CortexAffectSnapshot`, `decay_emotion`)
extrapoladas a las 76.

Se verificó **tarea por tarea contra el código**, con una segunda pasada
adversarial por fase cuyo encargo era **refutar** lo que la primera diera por
hecho (instrucción explícita: ante la duda, refutar).

## Veredicto

| Estado        |  N  |                                                         |
| ------------- | :-: | ------------------------------------------------------- |
| `implemented` | 29  | verificado con evidencia `file:line` y contraverificado |
| `partial`     | 45  | existe algo, pero no todo lo que la tarea pedía         |
| `missing`     |  2  | no existe                                               |

**La extrapolación era falsa.** Solo 29 de 76 estaban realmente completas. Pero el
matiz importa igual en la otra dirección: _ninguna_ de las 45 parciales significa
«no hay nada» — el córtex funciona. Lo que falta es casi siempre **el último tramo**
(un test que el plan exigía, un endpoint del contrato, un flag que nadie pasa).

### La verificación adversarial también se pasa de frenada

Un caso comprobado a mano: **F2 L105** (`update_mood`) se marcó `partial` porque
«la banda de temperamento no se aplica al eje arousal». Es un **falso positivo**:
el ADR 0075 §1 define `arousal ∈ [0,1]` mientras la banda `[MOOD_FLOOR, MOOD_CEIL]
= [-0.6, 0.6]` es para los ejes **bipolares**; aplicarla a arousal permitiría
valores negativos fuera de su rango. `PADState.__post_init__` ya lo clampa a
`[0,1]` por construcción. El código estaba bien y el docstring ya lo decía.

Moraleja para quien lea esta tabla: **un `partial` es una pista, no una sentencia**.
Antes de «arreglar» uno, ábrelo.

## Cerradas en esta misma sesión (2026-07-27)

Cinco de las parciales eran defectos reales y quedan corregidas con test:

| Tarea                          | Defecto                                                                                                                                                                                                                                                                                                           |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **F1 L155**                    | El camino web **nativo** del ADR 0076 (dec. 3, `WebSearch`/`WebFetch` del SDK, anti-SSRF gratis) era código muerto: `build_cortex_model` recibe `web_enabled` y **nadie se lo pasaba**. Con la web encendida el córtex caía siempre en el camino _degradado_ (dec. 4).                                            |
| **F3 L161 · F4 L99 · F4 L169** | `cortex_curiosity_cron`, `cortex_reflection_cron` y `cortex_maintenance_cron` estaban documentados como «operator-tunable» y eran **código muerto**: el beat hardcodeaba `run_every=900.0` y dos `crontab()` literales. Los defaults documentados además **mentían** (`*/30` contra 15 min, `42 4` contra 04:45). |
| **F2 L105**                    | Falso positivo (arriba). Se marca sin tocar código.                                                                                                                                                                                                                                                               |

Tests: `tests/unit/test_cortex_native_web_wiring.py` (6) y
`tests/unit/test_cortex_beat_schedule.py` (+4).

De paso, dos mentiras documentales que la auditoría destapó y quedan corregidas:

- **Siete ADR** (`0063`, `0073`, `0075`, `0077`, `0078`, `0080`, `0102`) tenían
  `status: accepted` en el frontmatter y un banner en el cuerpo diciendo
  **`Estado: proposed`**. El 0080 llegaba a decir «DISEÑO, no implementado» con
  `cortex/browse.py`, la tabla `browse_sessions` y su kill-switch ya en el repo.
  Una sesión que leyera el cuerpo creería abierta una decisión cerrada.
- La e2e `apps/admin-panel/e2e/cortex-voice.spec.ts` asertaba el testid
  **`cortex-voice-card`, que no existe en la app**. El test no podía pasar nunca
  — y su cabecera decía «PENDING HUMAN VERIFICATION», así que el rojo se lo
  habría comido el operador. El testid real es `cortex-voice-call`.

## Los 42 huecos que quedan

Están en [`gaps-cortex-2026-07-27.md`](gaps-cortex-2026-07-27.md), uno por
entrada con su evidencia, **indexados por título** (los números de línea de este
informe son los de la revisión de la auditoría y envejecen al primer párrafo que
se añada a un plan; el título se busca con un `grep`). Agrupados por naturaleza:

| Naturaleza                                                | Aprox. | Qué hacer                                                                                             |
| --------------------------------------------------------- | :----: | ----------------------------------------------------------------------------------------------------- |
| **Falta el test que el plan exigía** (código correcto)    |  ~14   | Escribir el test. Barato y es lo que separa «funciona hoy» de «seguirá funcionando».                  |
| **Divergencia de forma** (módulo/función con otro nombre) |   ~5   | **No renombrar.** Anotar la divergencia en el plan: el código vive y renombrarlo es churn con riesgo. |
| **Falta un tramo funcional real**                         |  ~15   | Ver abajo.                                                                                            |
| **Documentación / changelog**                             |   ~4   | Redactar al cerrar las fases.                                                                         |
| **Frontend sin test o con helper duplicado**              |   ~4   | Ver `affectToVisual` abajo.                                                                           |

### Los tramos funcionales que sí faltan (por valor)

1. **F4 — el gate de aprobación del owner para la curiosidad no existe**
   (L152/L120/L184/L90): no hay columna `approved`, ni setting
   `cortex.curiosity_approval_gate`, ni endpoint `/approve`. El ADR 0078 aprueba
   comportamiento autónomo **con gobierno**; hoy el gobierno es solo el
   kill-switch global. Es el hueco más grande y toca producto → merece su ADR o
   una decisión explícita del operador antes de implementarlo.
2. **F4 — la dimensión de COSTE del budget no existe** (L104): solo se cuentan
   búsquedas. `cortex_curiosity_pursuits.cost_usd` queda siempre a 0, y por eso
   la métrica `_cost_usd_total` de L198 sería 0 aunque se implementara.
3. **F5 D2 — el mantenimiento ignora el budget por owner y el circuit-breaker**
   (L152): solo consulta el kill-switch global, teniendo los otros dos ya hechos.
4. **F5 D3 — el olvido no tiene índice** (L160): los contadores viven en
   `metadata_` (JSONB) y el barrido se acota a mano con `_FORGET_SCAN_LIMIT = 500`.
   El diseño pivotó a JSONB sin dejar constancia en el plan.
5. **F3 — falta el histórico de identidad de punta a punta** (L130/L168/L177):
   sin `list_history`, sin `GET /owner/cortex/identity/history`, sin timeline.
6. **F3 — la reflexión no tiene budget cap** (L149/L154), solo kill-switch.
7. **F5 C1 — `affectToVisual` no gobierna el avatar vivo** (L110): la lógica está
   duplicada inline en `realistic-avatar.tsx`, así que la función pura y testeada
   no es la que se ejecuta.
8. **F4 L198 — no existe ninguna métrica OTEL del bucle** (`missing`).

## Qué NO hacer con este documento

- **No marcar casillas leyendo esta tabla.** Los 34 `[x]` de los planes ya se
  pusieron aquí, con evidencia. El resto se marca cuando se cierre su hueco.
- **No “arreglar” una divergencia de nombre** para que cuadre con el plan. El
  plan describía una intención; el código eligió otra forma y funciona. Lo que
  se corrige es el plan.
- **No confiar en el `partial` sin abrir el fichero.** Ver el falso positivo de
  F2 L105.

## Método (para poder repetirlo)

12 agentes: 5 verificadores (uno por fase, con el mapa del código y la
instrucción de exigir evidencia `file:line`), 5 escépticos (uno por fase, con el
encargo de refutar lo declarado hecho) y 2 sobre los follow-ups de
`registry-egress-followups`. 1,58 M tokens, 543 llamadas a herramientas, ~29 min.

El punto que más falsos «hecho» evitó fue una instrucción del prompt: _«el número
de migración escrito en el plan puede NO coincidir con el real: busca por nombre
de tabla, no por número»_. Las migraciones del córtex se renumeraron (el plan dice
0092 para tres tablas distintas que hoy son 0092/0094/0095).
