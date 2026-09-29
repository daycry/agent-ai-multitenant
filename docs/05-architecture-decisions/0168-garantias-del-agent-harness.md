---
adr_id: "0168"
title: "Garantías del agent harness: dependencias declaradas, allowlist explícita, guardrails, efectos y checkpoints"
status: accepted
date: 2026-09-29
decided_on: 2026-09-29
authors: [claude-opus-5-5]
plan_referenced: harness-00-programa-profesionalizacion-2026-09-17
related: ["0020", "0051", "0102", "0114", "0135", "0145", "0151", "0169"]
docs_language: es
---

# ADR 0168 — Garantías del agent harness

> **Estado: `accepted` (2026-09-29).** Lo pide `task_h00_01` del programa harness
> antes de escribir código. Se decide bajo la delegación permanente del operador para
> los ADR de plataforma («elige la mejor opción para el sistema»), con la excepción que
> esa misma delegación reserva: **una decisión de producto no la toma plataforma
> sola**. Las dos decisiones de este ADR que rozan producto (D1 y D2) se resuelven
> **sin cambiar el comportamiento por defecto de ningún proyecto**: la capacidad nueva
> existe, pero sólo se activa cuando alguien la pide. Si el operador prefiere otro
> default, cambiarlo es una línea y no invalida el resto del ADR.

## Contexto

La [auditoría del harness del 2026-09-17](../roadmap/auditoria-harness-ciclo-kb-memoria-2026-09-17.md)
dejó seis planes (`harness-01` a `harness-06`). Tres de sus cambios contradicen
decisiones vigentes escritas en el propio código, y la cadena de precedencia de
`CLAUDE.md` dice que eso se resuelve con un ADR, no con un plan:

1. «La memoria nunca rompe el run» y «la KB nunca rompe el run»
   (`agent_runtime/__main__.py`).
2. `allowed_tools=None` significa «sin restricción» (plan 06.14).
3. Los guardrails degradan a baseline si la política no se resuelve (ADR 0102 D3).

Y tres decisiones técnicas que los planes necesitan tomadas antes de empezar: qué
garantía ofrecen los efectos, dónde vive el checkpoint y cómo se identifica un intento.

## Decisión

**D1 — Memoria y KB pueden ser obligatorias; por defecto siguen siendo opcionales.**
Cada dependencia se declara `REQUIRED`, `OPTIONAL` o `FORBIDDEN`. El default es
`OPTIONAL` para memoria y KB, que es exactamente el comportamiento de hoy, con una
diferencia: cuando fallan, el run lo **declara** (`dependency_report` y preámbulo) en
vez de tratarlo como «cero resultados». La declaración se hace por **proyecto**; no se
añade un requisito por tarea mientras nadie lo pida.

**D2 — `allowed_tools` es obligatorio en el contrato V1, y el legacy se traduce sin
cambiar nada.** En el envelope V1 una lista vacía significa «ninguna» y `["*"]`
«todas, explícitamente». El orquestador que hoy emite `None` pasa a emitir `["*"]`
para ese mismo agente, así que ningún agente sin allowlist cambia de conducta. Lo que
desaparece es la ambigüedad: la ausencia de la clave deja de ser un permiso.

**D3 — Guardrails fail-closed sólo cuando hay una política configurada que no se
puede resolver.** Si el proyecto o el tenant tienen política y su resolución falla, el
run no arranca. Si no hay ninguna capa configurada, se usa el baseline como hoy, pero
declarado en el envelope (`policy.failure_mode: baseline`). Mantiene el ADR 0102 D3
para el caso sin política y cierra el hueco del caso con política.

**D4 — Los efectos son _effectively once_, no _exactly once_.** Idempotencia por clave
estable del paso lógico, reconciliación de lo incierto y escalado a humano de lo que no
se puede reconciliar. Nadie promete que una llamada externa no se repita jamás; se
promete que no se repite **a ciegas**.

**D5 — El checkpoint vive en PostgreSQL y el sandbox sólo lo toca por la API
interna.** Con un token acotado a **una ejecución** (claim `exe`), compare-and-swap
por secuencia y cifrado Fernet del blob con la familia de claves de prod-05. El
contenedor del agente sigue sin acceso directo a la BD (principio rector 2).

**D6 — `execution_id` es la intención; `attempt_id`, cada arranque.** Una reanudación
tras un redelivery conserva el `execution_id` y estrena `attempt_id`. El lease del
intento vigente decide quién puede preparar efectos.

**D7 — Antes de construir checkpoints, se mide.** `harness-02` empieza por
`task_h02_00`: cuántos runs mueren a mitad y cuánto trabajo pierden. Si la tasa es
menor del 2 % y el coste medio menor de 1 € por muerte, el plan se recorta a la fase
de contrato de estado y a los tests de kill que documentan el comportamiento actual.

## Consecuencias

- Ningún proyecto existente cambia de comportamiento al aplicar D1, D2 y D3. Lo que
  cambia es que los fallos de dependencias opcionales dejan de ser invisibles y que la
  ausencia de una clave del contrato deja de tener significado implícito.
- `harness-01` a `harness-06` pueden arrancar en su orden sin volver a pedir decisión,
  salvo que la medición de D7 cambie el alcance de `harness-02`.
- Este ADR no toca `CLAUDE.md`: ningún principio rector cambia. El principio 2
  (aislamiento por contenedor) es precisamente el que D5 preserva.
