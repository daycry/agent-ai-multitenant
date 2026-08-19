---
title: "ADR 0157: Quién reescribe la narrativa del córtex — la frontera está en lo acotado, no en lo autobiográfico"
status: accepted
date: 2026-08-19
deciders: [claude-code]
relates_to: [0074, 0075, 0077, 0078, 0156]
plan_referenced: cortex-f3-identidad
task: [F3.5]
docs_language: es
---

# ADR 0157 — Quién reescribe la narrativa del córtex

> **Estado: `accepted`.** Gana la **implementación** (opción B): la `narrative` del
> córtex **la puede reescribir el owner**, y la frontera de lo intocable se
> redibuja donde de verdad hay un invariante que defender — **el estado derivado
> NUMÉRICO** (`traits`, `mood_baseline`, `relationship_model`, `affect_params`),
> que sólo muta la reflexión de forma clampeada y acotada. Se descartan la opción A
> (gana el plan: 422 al tocar `narrative`) y la opción C (editable sólo hasta el
> onboarding). El porqué de cada descarte está abajo, que es lo que hace que este
> ADR sirva dentro de seis meses.
>
> **Alcance ejecutado:** el enunciado del plan
> [cortex-f3-identidad](../roadmap/cortex-f3-identidad.md) (bloque de endpoints y
> casilla F3.5) se reescribe para decir lo que el sistema hace y por qué; el código
> no cambia; la decisión queda **acreditada con dos tests** que se ponen rojos si
> alguien la deshace en cualquiera de los dos sentidos
> (`tests/integration/test_cortex_f3_identity_endpoints.py`).

> **Quién decidió, dicho con precisión.** Esta decisión la tomó Claude Code el
> 2026-08-19 al amparo de la orden permanente del operador —«analiza los ADR
> pendientes e impleméntalos eligiendo la mejor opción», y la luz verde explícita
> para resolver esta contradicción con un ADR nuevo—, **no** en una conversación
> donde el operador viese estas opciones. Nace `accepted` porque esa orden autoriza
> a decidir y un ADR `proposed` que nadie va a leer no protege a nadie; pero **queda
> pendiente de ratificación**, y si el operador prefiere la opción A o la C,
> cambiarla es reabrir este ADR, no un descuido de nadie.

## Contexto

La identidad del córtex (fase [F3](../roadmap/cortex-f3-identidad.md), ADR 0074) es
un único blob `identity_state` con dos familias de campos muy distintas:

| Familia               | Campos                                                                            | Quién los escribe (hoy)                                |
| --------------------- | --------------------------------------------------------------------------------- | ------------------------------------------------------ |
| **Prosa co-diseñada** | `name`, `core_values`, `narrative`, `language`, `learning_goals`                  | El owner (`PUT /owner/cortex/identity`) y la reflexión |
| **Derivado numérico** | `traits` (Big-Five), `mood_baseline` (PAD), `relationship_model`, `affect_params` | **Sólo** la reflexión, clampeada y acotada             |

El plan de F3 escribió una frontera distinta de la que se implementó. Literal del
plan (casilla F3.5): «`PATCH` rechaza con 422 cualquier intento de tocar
`narrative`/`traits` (solo la reflexión los muta)». Literal del código:
`OWNER_EDITABLE_FIELDS` **incluye** `narrative`
([`cortex/identity.py:85-91`](../../apps/api-server/src/api_server/cortex/identity.py)),
y el 422 lo produce el `extra="forbid"` del schema
([`schemas/cortex_identity.py:74-90`](../../apps/api-server/src/api_server/schemas/cortex_identity.py)),
que sólo expone los cinco campos de prosa: cualquier otro —`traits`,
`mood_baseline`, `relationship_model`, `affect_params`— es un 422.

La contradicción lleva abierta desde el 2026-06-24 y es la razón —única— por la que
la casilla F3.5 seguía sin marcar dos meses después de estar el código en verde y
desplegado. No es un olvido de implementación: el changelog de la fase ya la
declaraba «divergencia deliberada». Lo que faltaba era decidir cuál de las dos
frases es la buena, porque mientras no se decida el plan y el código se desmienten
mutuamente y nadie sabe cuál leer.

La pregunta, dicha sin rodeos: **¿quién debería poder reescribir la narrativa
autobiográfica del córtex?**

### Lo que hay que saber antes de opinar

1. **La narrativa entra en el system prompt de CADA turno** (`identity_preamble`,
   `cortex/self_context.py`). No es un campo decorativo de una ficha: gobierna la
   conducta del córtex en la siguiente conversación.
2. **La narrativa es el único campo que la reflexión reescribe SIN cota.** El
   guardrail de auto-modificación del ADR 0074 (`bounded_update` + clamps,
   `BASELINE_MAX_DELTA_PER_REFLECTION = 0.05`) se aplica a `traits` y a
   `mood_baseline` — números. La prosa se sustituye entera cada ciclo.
3. **La reflexión puede no correr nunca.** Es un bucle de fondo bajo el
   kill-switch `cortex.autonomy_enabled`, que sigue **OFF por defecto** (ADR 0078),
   y es fail-open: sin Ollama, no-op.
4. **Todo cambio queda versionado con su autoría**: cada escritura añade fila a
   `cortex_identity_history` con `updated_by ∈ {reflection, owner_override,
onboarding}` y su `diff`, expuesto en `GET /owner/cortex/identity/history` y
   pintado por el timeline del panel.
5. **El onboarding co-diseñado de F3.3 pasa por ahí**: `propose_identity`
   ([`cortex/identity.py:391`](../../apps/api-server/src/api_server/cortex/identity.py))
   hace que el córtex se autonombre y proponga valores **y narrativa**, y lo filtra
   por `editable_owner_state` para que el LLM no pueda tocar los derivados. El owner
   confirma esa propuesta con el mismo `PUT`.

## Decisión

**Gana la implementación (opción B), con la frontera reformulada:**

> El owner **co-diseña la prosa** (`name`, `core_values`, `narrative`, `language`,
> `learning_goals`) y **no puede escribir a mano el estado derivado numérico**
> (`traits`, `mood_baseline`, `relationship_model`, `affect_params`), que sólo muta
> la reflexión de forma clampeada, acotada y versionada. Un intento de tocarlos por
> la API es **422**, no un campo ignorado en silencio.

Y su corolario, que es lo que sustituye a la prohibición: **la honestidad se
sostiene por procedencia, no por prohibición**. Quién escribió cada narrativa es
consultable —`updated_by` por versión— y visible en el timeline.

### Por qué la frontera va por ahí y no por «lo autobiográfico»

1. **La cota es sobre números, no sobre prosa.** El invariante que el ADR 0074
   defiende es que un ciclo no pueda derivar la identidad de golpe: |Δ| ≤ 0.05 por
   rasgo y ciclo. Un número escrito a mano **rompe esa cota en silencio** y además
   convierte `cortex_identity_history` en un registro falso de «cómo evolucionó».
   La narrativa no tiene cota ninguna. Prohibírsela al owner, por tanto, **no
   protege ningún invariante**: sólo entrega el único campo sin cota en propiedad
   exclusiva a un LLM, sin correctivo humano. Eso invierte la intención del
   guardrail en vez de cumplirla.
2. **El onboarding co-diseñado exige que el owner escriba la narrativa.** La
   propuesta que el córtex hace de sí mismo incluye narrativa, y sólo se persiste
   cuando el owner la confirma. Con la opción A esa confirmación daría 422
   justamente en el campo que la propuesta más cuida, y F3.3 necesitaría un segundo
   camino de escritura privilegiado — que sería «el owner escribe la narrativa» con
   otro nombre y sin quedar registrado como override.
3. **Sin editar no hay correctivo.** La narrativa dice, en primera persona, lo que
   el córtex cree de sí mismo y de su owner, y se le enseña al owner en pantalla.
   Si una reflexión escribe algo equivocado, alucinado o desagradable, bajo la
   opción A las únicas salidas son esperar a otro ciclo —que con la autonomía OFF
   puede no llegar nunca— o entrar a `psql`. Una superficie de producto cuyo único
   arreglo es la consola no es una superficie de producto.
4. **La procedencia ya está construida y responde la pregunta que importa.** Lo que
   el copy honesto necesita no es «esta prosa la escribió una máquina», sino «se
   puede saber quién escribió esta prosa». Eso existe: versión, autor, motivo y
   `diff`. La prohibición no añadiría nada ahí.

### Lo que se pierde (dicho, no escondido)

- **La narrativa deja de ser un derivado garantizado de lo vivido.** El owner puede
  escribir ahí lo que quiera —incluida una afirmación de consciencia— y eso entra en
  el preámbulo de cada turno. Se acepta por tres razones: el copy honesto del panel
  es texto fijo de la UI, no se deriva de la narrativa; la autoría queda registrada
  por versión; y el owner **ya** moldea el preámbulo legítimamente vía
  `name`/`core_values`/`learning_goals`, así que la opción A no cerraba esa puerta,
  sólo la estrechaba.
- **No hay protección de último escritor.** Si el owner edita la narrativa y luego
  corre una reflexión, la reflexión la pisa sin enterarse. Hoy es inocuo (autonomía
  OFF) y no se pierde nada —cada versión queda en el histórico—, pero es una arista
  real: se deja escrita aquí para que se encuentre como consecuencia conocida y no
  se redescubra como bug. Cerrarla (p. ej. que la reflexión respete una narrativa
  con `updated_by='owner_override'` más reciente que su última pasada) es trabajo de
  F4/F5, no de esta decisión.

## Alternativas descartadas

**A) Gana el plan: `narrative` de sólo lectura para el owner (422).** Descartada por
los cuatro argumentos de arriba, y por uno más de forma: obligaría a revertir un
comportamiento desplegado y usado —el formulario de identidad del panel escribe
narrativa hoy— para ganar una garantía que, medida, no se sostiene (la reflexión
reescribe la prosa sin cota y puede no correr nunca). El coste de esa reversión
sería quitarle al owner el único modo de corregir lo que un LLM diga de él.

**B) Gana el código (elegida).**

**C) Editable sólo hasta `onboarded_at`, congelada después.** Descartada: hace que la
mutabilidad del campo dependa de un timestamp, y el owner descubre que está
congelado **el día que más quiere corregirlo** — justo después de una reflexión
mala. Añade un estado que razonar sin ningún invariante que defender a cambio.

## Consecuencias

- ✅ El plan y el código dejan de desmentirse; la casilla F3.5 puede cerrarse con
  evidencia en vez de con una promesa.
- ✅ La frontera queda dicha en términos del invariante que existe («lo acotado no se
  escribe a mano»), que es una regla que se puede aplicar a los campos que vengan.
- ✅ El 422 se amplía de facto a `relationship_model` y `affect_params` —ya lo daba
  el `extra="forbid"`, ahora está **acreditado con test**, que es lo que impide que
  un `extra="ignore"` de mañana lo convierta en un ignorado silencioso.
- ⚠️ La narrativa puede contener texto del owner en el preámbulo de cada turno. La
  honestidad se apoya en la procedencia (timeline) y en el copy fijo del panel.
- ⚠️ Queda abierta la protección de último escritor descrita arriba.

## Referencias

- [ADR 0074](0074-rol-system-owner-y-cortex-singleton.md) — córtex singleton,
  tablas por `owner_user_id`, guardrail de auto-modificación.
- [ADR 0077](0077-politica-olvido-consolidacion-memoria-cortex.md) — la identidad no se auto-olvida.
- [ADR 0078](0078-bucles-cognitivos-fondo-cortex.md) — bucles de fondo, kill-switch,
  budget.
- [ADR 0156](0156-aislamiento-estructural-del-cortex.md) — las seis tablas del
  córtex llevan RLS de eje owner además del filtro explícito.
- Plan [cortex-f3-identidad](../roadmap/cortex-f3-identidad.md) — casilla F3.5.
