/**
 * Pure helpers for a task's `acceptance_criteria` (the agent/reviewer's
 * "definition of done"). A criterion is either a plain descriptive **string**
 * (the planner / manual-edit shape — see ADR Feature A) or a structured
 * **object** (e.g. a reviewer criterion carrying `id`/`kind`/metadata). The
 * agent renders both via the same `_criterion_text` rule server-side.
 *
 * The editor in `task-criteria-section.tsx` keeps logic out of the component:
 * it seeds each editable row from `draftFromCriterion(original)` and, on save,
 * rebuilds the list with `cleanCriteria` — preserving structured criteria
 * instead of flattening them to strings.
 *
 * ---------------------------------------------------------------------------
 * ADR 0162 — declarar CÓMO se comprueba un criterio
 * ---------------------------------------------------------------------------
 *
 * Hasta el 2026-08-29 una fila nueva emitía SIEMPRE una cadena, y el worker
 * sólo ejecuta los criterios que son un dict con `runtime` **y** `command`
 * (`workers/execution.py::_run_task_tests`). Las dos mitades nunca se
 * emparejaron: **no existía ningún camino humano para declarar que una tarea se
 * verifica ejecutando algo**, así que ninguna tarea llevaba criterios
 * ejecutables, el `<test-report>` del reviewer salía vacío y «no hubo tests» era
 * indistinguible de «el proyecto no tiene tests». Eso es el falso verde que mide
 * el ADR 0162.
 *
 * Este fichero es un **espejo** de dos piezas del backend, y como todo espejo
 * hay que poder compararlo con el original sin montar React:
 *
 * - `workers/test_runtime.py::_coerce_check` — qué entradas se ejecutan.
 * - `workers/execution.py::_run_task_tests` — qué entradas llegan siquiera a
 *   la fase de tests.
 * - `shared_test_runtimes/signals.py::evaluate_signal` — qué significa que un
 *   check «pasó», que desde la ola 2 del ADR 0162 ya no es sólo el código de
 *   salida.
 *
 * Si cambian allí, cambian aquí. Una copia desincronizada es peor que no
 * tenerla, porque promete una ejecución que no ocurre — o, como pasó con
 * `expected_signal`, niega una que sí ocurre y desanima al operador de
 * declararla. `tests/unit/test_espejo_ts_de_la_senal.py` ata mecánicamente el
 * único trozo del espejo que se puede atar: el default.
 */

/** Mirror of the planner's caps (`planning_llm._MAX_ACCEPTANCE_CRITERIA`). */
export const MAX_ACCEPTANCE_CRITERIA = 8;
/** Mirror of the planner's per-criterion length cap (`_MAX_CRITERION_LEN`). */
export const MAX_CRITERION_LEN = 300;

/**
 * Espejo del default de `AcceptanceCheck.expected_signal` (`test_runtime.py`),
 * que es el mismo `SIGNAL_EXIT_ZERO` de `shared_test_runtimes/signals.py`.
 *
 * **Qué promete y qué no, desde la ola 2 del ADR 0162.** El worker SÍ evalúa
 * `expected_signal`, por check y con la salida de ese check
 * (`test_runtime.py` → `evaluate_signal`), y lo reporta en `check_signals` con
 * tres estados que no se colapsan: se cumplió / NO se cumplió / **no se pudo
 * evaluar**. Lo que NO hace —y es deliberado— es decidir: `all_passed()` sigue
 * saliendo sólo del código de salida, porque bloquear es la opción C del ADR y
 * sigue sin firmar. Una señal incumplida se ve en el `<test-report>` del
 * reviewer y no degrada ningún veredicto.
 *
 * Por eso este default sigue siendo `exit_code == 0` y tiene que seguir
 * siéndolo: exigirle un recuento haría que cada criterio ya escrito pidiera
 * algo que nadie declaró. Quien quiera cerrar la trampa del §«La trampa que hay
 * que cerrar CON A» —un comando que sale con código 0 sin ejecutar un test—
 * escribe `exit_code == 0 and tests > 0` a mano. Es OPT-IN.
 */
export const DEFAULT_EXPECTED_SIGNAL = "exit_code == 0";

/** Los dos únicos `check_type` que este editor produce y sabe leer. */
export const CHECK_TYPES = ["manual", "automated"] as const;
export type CheckType = (typeof CHECK_TYPES)[number];

/**
 * La declaración de un criterio tal y como la edita un humano: cadenas, porque
 * es lo que devuelve el DOM, y los dos modos a la vez para que cambiar de uno a
 * otro y volver no pierda lo tecleado. Lo que se emite depende de `checkType`.
 */
export interface CriterionCheck {
  checkType: CheckType;
  runtime: string;
  command: string;
  expectedSignal: string;
  /** Por qué esto NO es verificable a máquina. Obligatorio cuando es manual. */
  manualReason: string;
}

/**
 * A single editable row: the shown/edited text plus the original criterion it
 * came from (`null` for a row the operator added from scratch).
 *
 * `check` tiene **tres** estados y los tres significan cosas distintas:
 *
 * - `undefined` — la fila nunca pasó por el editor de declaración. El criterio
 *   original se preserva tal cual (es el camino de siempre, y el que protege a
 *   los criterios con un vocabulario que no es nuestro).
 * - `null` — el operador **retiró** la declaración. Los campos que declaraban
 *   se limpian: sin este tercer estado, «quitar la declaración» dejaba el
 *   comando puesto y el botón mentía.
 * - objeto — la declaración vigente, que se emite.
 */
export interface CriterionDraft {
  text: string;
  original: unknown;
  check?: CriterionCheck | null;
}

/**
 * Qué le va a pasar de verdad a un criterio, no lo que el objeto dice de sí
 * mismo:
 *
 * - `automated` — el worker lo va a ejecutar.
 * - `manual` — declarado no-automatizable; se registra como «skipped».
 * - `undeclared` — nadie ejecuta nada y nadie ha dicho por qué.
 */
export type CriterionCheckState = "automated" | "manual" | "undeclared";

/** Claves i18n (namespace `taskDetail`) de lo que impide guardar una fila. */
export type CriterionErrorKey =
  | "errorCriterionTextRequired"
  | "errorCriterionRuntimeRequired"
  | "errorCriterionCommandRequired"
  | "errorCriterionReasonRequired";

/** Flatten any criterion shape to a display string. Strings pass through; objects
 * use the first of description/text/criterion/name; anything else → JSON. */
export function criterionText(c: unknown): string {
  if (typeof c === "string") return c;
  if (c && typeof c === "object") {
    const o = c as Record<string, unknown>;
    return String(o.description ?? o.text ?? o.criterion ?? o.name ?? JSON.stringify(c));
  }
  return String(c);
}

/** True when `c` is a structured criterion (a plain object we should preserve),
 * not a string and not an array. */
function isStructured(c: unknown): c is Record<string, unknown> {
  return typeof c === "object" && c !== null && !Array.isArray(c);
}

/** El valor de una clave del criterio como cadena recortada (`""` si no la hay). */
function field(c: Record<string, unknown>, key: string): string {
  const raw = c[key];
  return typeof raw === "string" ? raw.trim() : "";
}

/**
 * Una declaración recién desplegada por un humano.
 *
 * Nace **manual** a propósito. El default implícito del worker es `automated`
 * (`entry.get("check_type", "automated")`) y es justo el defecto que el ADR 0162
 * denuncia por su nombre: «un valor ausente no puede significar nada más fuerte
 * que *desconocido*». Quien acaba de abrir la fila todavía no ha declarado nada,
 * así que lo que se le presupone tiene que ser lo que NO produce un verde — y
 * además obliga a escribir el motivo, que es la mitad que el ADR exige.
 */
export function newCheck(): CriterionCheck {
  return {
    checkType: "manual",
    runtime: "",
    command: "",
    expectedSignal: DEFAULT_EXPECTED_SIGNAL,
    manualReason: "",
  };
}

/**
 * La declaración que ya trae un criterio, o `null` si no trae ninguna que este
 * editor sepa leer.
 *
 * La primera guarda es la importante: si el criterio declara un `check_type`
 * que **no es de nuestro vocabulario** (`descriptive`, `human`, lo que sea que
 * escriba otro productor), devolvemos `null` y el criterio se preserva verbatim.
 * Reclamarlo lo reescribiría a `automated`/`manual` al guardar, cambiando el
 * significado de un criterio que el operador ni siquiera tocó.
 */
export function checkFromCriterion(c: unknown): CriterionCheck | null {
  if (!isStructured(c)) return null;
  const declared = field(c, "check_type");
  if (declared && declared !== "automated" && declared !== "manual") return null;

  const runtime = field(c, "runtime");
  const command = field(c, "command");
  const reason = field(c, "manual_reason");
  if (!declared && !runtime && !command && !reason) return null;

  // Sin `check_type` explícito, mandan los campos: con runtime/command el worker
  // lo ejecuta (su default es `automated`), y sin ellos no ejecuta nada.
  const checkType: CheckType =
    declared === "manual" || declared === "automated"
      ? declared
      : runtime || command
        ? "automated"
        : "manual";

  const signal = field(c, "expected_signal");
  return {
    checkType,
    runtime,
    command,
    expectedSignal: signal || DEFAULT_EXPECTED_SIGNAL,
    manualReason: reason,
  };
}

/** Fila del editor sembrada desde un criterio existente. */
export function draftFromCriterion(c: unknown): CriterionDraft {
  const check = checkFromCriterion(c);
  const draft: CriterionDraft = { text: criterionText(c), original: c };
  // `undefined` y no `null`: no es lo mismo «no había declaración» que «el
  // operador la retiró» (ver `CriterionDraft.check`).
  if (check) draft.check = check;
  return draft;
}

/**
 * Qué va a pasar con este criterio, según las mismas reglas que el worker.
 *
 * Espeja `_coerce_check` (ausente == `automated`) **y** el filtro previo de
 * `_run_task_tests` (hace falta runtime y command). Por eso un criterio que se
 * declara `automated` pero no trae comando cuenta como `undeclared`: el worker
 * lo descarta en silencio, y pintarlo como automático sería prometer una
 * ejecución que no ocurre — el mismo falso verde que el ADR mide, un piso más
 * arriba. Que la declaración esté rota se ve al abrir el editor, que lo señala
 * como error.
 */
export function criterionCheckState(c: unknown): CriterionCheckState {
  // OJO CON EL VOCABULARIO, porque hay dos preguntas distintas y el worker
  // responde la otra:
  //
  //   * esta función responde **cómo se comprueba** el criterio — realidad de
  //     ejecución. Un dict con `runtime` y `command` SE EJECUTA aunque nadie
  //     declarase `check_type`, así que aquí es `automated`. Es lo que el
  //     operador quiere ver de un vistazo: qué se verifica de verdad.
  //   * el worker cuenta **quién lo declaró** (`checks_without_declared_check_type`,
  //     ADR 0162). Ahí ese mismo criterio suma como NO DECLARADO, y es correcto:
  //     se ejecuta por inercia del formato, no porque alguien lo decidiera.
  //
  // Las dos son ciertas y no se pueden fundir sin perder una. Si algún día se
  // quiere enseñar la segunda en pantalla, va en un indicador APARTE — no
  // cambiando lo que significa `automated` aquí, que dejaría la ficha diciendo
  // «sin comprobación» de tareas que sí se comprueban.
  if (!isStructured(c)) return "undeclared";
  const declared = field(c, "check_type") || "automated";
  if (declared !== "automated") return "manual";
  return field(c, "runtime") && field(c, "command") ? "automated" : "undeclared";
}

/**
 * Lo que la declaración de un criterio dice, para poder leerlo SIN abrir el
 * editor: el comando que se va a ejecutar, o el motivo por el que nadie lo va a
 * ejecutar. `""` cuando no hay nada que enseñar.
 *
 * Se decide por `criterionCheckState` y no por los campos crudos a propósito.
 * Un criterio que trae `command` pero al que le falta el `runtime` lo descarta
 * `_run_task_tests` en silencio: enseñar ese comando sería prometer una
 * ejecución que no ocurre, que es el falso verde del ADR 0162 escrito en la
 * ficha. Sin declaración no se inventa ninguna — «nadie ha declarado nada» y
 * «declarado manual sin motivo escrito» son estados distintos y ninguno de los
 * dos se rellena con un texto de relleno.
 */
export function criterionDeclarationDetail(c: unknown): string {
  if (!isStructured(c)) return "";
  const state = criterionCheckState(c);
  if (state === "automated") return field(c, "command");
  if (state === "manual") return field(c, "manual_reason");
  return "";
}

/** Cuántos criterios de una tarea se comprueban de verdad, y cuántos no. */
export function criteriaCheckSummary(criteria: readonly unknown[]): {
  automated: number;
  manual: number;
  undeclared: number;
  total: number;
} {
  const counts = { automated: 0, manual: 0, undeclared: 0, total: criteria.length };
  for (const c of criteria) counts[criterionCheckState(c)] += 1;
  return counts;
}

/**
 * Lo que impide guardar una fila, en claves i18n.
 *
 * Sólo mira las filas **declaradas**: una fila de prosa vacía no es un error,
 * se descarta y ya (comportamiento de siempre). Una fila declarada sin
 * enunciado sí lo es, porque `cleanCriteria` la descartaría igual y el operador
 * vería desaparecer la declaración que acaba de escribir.
 */
export function criterionErrors(draft: CriterionDraft): CriterionErrorKey[] {
  const check = draft.check;
  if (!check) return [];
  const out: CriterionErrorKey[] = [];
  if (!draft.text.trim()) out.push("errorCriterionTextRequired");
  if (check.checkType === "automated") {
    if (!check.runtime.trim()) out.push("errorCriterionRuntimeRequired");
    if (!check.command.trim()) out.push("errorCriterionCommandRequired");
  } else if (!check.manualReason.trim()) {
    out.push("errorCriterionReasonRequired");
  }
  return out;
}

/** Todos los errores del editor, sin repetir, para bloquear el guardado. */
export function criteriaErrors(drafts: readonly CriterionDraft[]): CriterionErrorKey[] {
  const seen = new Set<CriterionErrorKey>();
  for (const draft of drafts) for (const key of criterionErrors(draft)) seen.add(key);
  return [...seen];
}

/** Las cinco claves que este editor gobierna; se reescriben enteras al emitir. */
const DECLARATION_KEYS = [
  "check_type",
  "runtime",
  "command",
  "expected_signal",
  "manual_reason",
] as const;

/** El criterio que emite una fila con declaración vigente. */
function emitDeclared(
  base: Record<string, unknown>,
  text: string,
  check: CriterionCheck,
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...base, description: text };
  // Se retiran TODAS y luego se ponen las del modo elegido: un criterio manual
  // que conserva el `command` de cuando fue automático deja dos declaraciones
  // contradictorias en el mismo objeto, y quien lo lea después —o el worker— no
  // sabe cuál manda.
  for (const key of DECLARATION_KEYS) delete out[key];
  out.check_type = check.checkType;
  if (check.checkType === "automated") {
    out.runtime = check.runtime.trim();
    out.command = check.command.trim();
    out.expected_signal = check.expectedSignal.trim() || DEFAULT_EXPECTED_SIGNAL;
  } else {
    out.manual_reason = check.manualReason.trim();
  }
  return out;
}

/**
 * Rebuild a clean `acceptance_criteria` list from editor rows: trim each text,
 * drop empties, cap length and count.
 *
 * Tres caminos, uno por estado de `draft.check`:
 *
 * - sin declaración (`undefined`) — comportamiento de siempre: una fila
 *   respaldada por un criterio estructurado se preserva (sólo se sobrescribe su
 *   `description`), y una fila de prosa emite una cadena.
 * - declaración vigente — emite el dict con `check_type` y los campos del modo.
 * - declaración retirada (`null`) — preserva el resto del criterio pero limpia
 *   las cinco claves que declaraban.
 *
 * Esto NO valida: `criteriaErrors` es lo que impide guardar una fila rota. Aquí
 * se serializa lo que la fila dice, para que un serializador y una guarda no
 * acaben discrepando sobre qué es válido.
 */
export function cleanCriteria(drafts: readonly CriterionDraft[]): unknown[] {
  const out: unknown[] = [];
  for (const draft of drafts) {
    const text = draft.text.trim().slice(0, MAX_CRITERION_LEN).trim();
    if (!text) continue;
    const base = isStructured(draft.original) ? draft.original : null;
    if (draft.check) {
      out.push(emitDeclared(base ?? {}, text, draft.check));
    } else if (draft.check === null && base) {
      const stripped: Record<string, unknown> = { ...base, description: text };
      for (const key of DECLARATION_KEYS) delete stripped[key];
      out.push(stripped);
    } else if (base) {
      out.push({ ...base, description: text });
    } else {
      out.push(text);
    }
    if (out.length >= MAX_ACCEPTANCE_CRITERIA) break;
  }
  return out;
}
