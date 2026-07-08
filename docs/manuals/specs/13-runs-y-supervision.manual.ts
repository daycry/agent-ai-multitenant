import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef, Step } from "../lib/manual";
import { seededPhpPlanId } from "../lib/seed-helper";

/**
 * Manual 13 — Visor de runs y supervisión de la ejecución autónoma.
 *
 * Las pantallas de OBSERVABILIDAD del trabajo de los agentes: el visor global
 * de ejecuciones (/admin/runs), el timeline paso a paso de un run, las
 * sesiones de revisión activas y el panel de tareas escaladas/bloqueadas de un
 * plan. Es el manual del supervisor: dónde mirar cuando quieres saber qué
 * están haciendo los agentes, cuánto cuesta y qué se ha atascado.
 */
const PLAN = seededPhpPlanId();

/** Abre el timeline de la ejecución más reciente del tenant (si existe). */
async function gotoLatestExecution(page: import("@playwright/test").Page): Promise<void> {
  const token = await page.evaluate(() => localStorage.getItem("agentic.token"));
  const res = await page.request.get(`/api/tenant-stats/runs?window_days=730`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok()) return;
  const rows = (await res.json()) as Array<{
    execution_id?: string;
    id?: string;
    status?: string;
  }>;
  const pick = rows.find((r) => r.status === "done") ?? rows[0];
  const id = pick?.execution_id ?? pick?.id;
  if (id) {
    await page.goto(`/admin/executions/${id}`, { waitUntil: "networkidle" }).catch(() => {});
    await page.waitForTimeout(1500);
  }
}

const steps: Step[] = [
  {
    title: "El visor global de runs",
    goto: "/admin/runs",
    fullPage: true,
    settleMs: 2000,
    body: `<p>El <b>visor de runs</b> es la vista de supervisión de TODO lo que
    los agentes han ejecutado en el tenant: cada fila es una <b>ejecución</b>
    (un run del bucle del agente sobre una tarea) con su <b>estado</b>
    (<code>running</code> / <code>done</code> / <code>aborted</code> /
    <code>failed</code>), su <b>veredicto</b> de review, las
    <b>iteraciones</b> que consumió, los <b>tokens</b> y el <b>coste</b>.</p>
    <ul>
      <li><b>Filtros</b>: por estado, veredicto, proyecto y ventana temporal —
      para responder preguntas como «¿qué runs fallaron hoy?» o «¿qué está
      corriendo ahora mismo?».</li>
      <li><b>Paginación</b> con orden más-reciente-primero.</li>
      <li>Cada fila enlaza a su <b>timeline</b> (el detalle paso a paso del
      run, siguiente apartado).</li>
    </ul>
    <p>Úsalo como punto de partida de cualquier investigación: si algo huele
    raro (costes altos, abortos repetidos, tareas que no avanzan), aquí se ve
    primero.</p>`,
  },
  {
    title: "El timeline de una ejecución, paso a paso",
    fullPage: true,
    settleMs: 1800,
    action: gotoLatestExecution,
    body: `<p>El <b>timeline</b> de una ejecución cuenta la historia completa
    de un run: cada paso del bucle del agente en orden, con su latencia y su
    resultado.</p>
    <ul>
      <li><b>Percepción</b>: qué tarea recibió el agente y con qué contexto
      (criterios de aceptación, feedback de reviews anteriores).</li>
      <li><b>Recuerdo</b>: qué memorias y conocimiento (RAG) recuperó.</li>
      <li><b>Llamadas al modelo</b>: cada invocación al LLM con tokens de
      entrada/salida y coste calculado con el precio vigente del modelo.</li>
      <li><b>Acciones (tools)</b>: qué herramientas ejecutó — leer/escribir
      ficheros del worktree, <code>stack_exec</code> (composer/pytest/npm en el
      runtime del stack), búsquedas, etc. — con sus resultados.</li>
      <li><b>Cierre</b>: el resultado estructurado que entrega
      (<code>submit_result</code>) y la <b>auto-revisión</b>.</li>
    </ul>
    <p>La cabecera resume estado, iteraciones, tokens y coste del run; si el
    run sigue vivo verás el botón <b>Cancelar</b>. Esta pantalla es la prueba
    auditable de que la tarea se ejecutó de verdad y de cuánto costó.</p>`,
  },
  {
    title: "Sesiones de revisión activas",
    goto: "/admin/review/active",
    fullPage: true,
    settleMs: 1800,
    body: `<p><b>Revisión activa</b> lista todas las <b>sesiones de review</b>
    vivas del tenant: cada una corresponde a un plan en validación humana con
    su app levantada (o esperando app-preview). Desde aquí el validador —o un
    supervisor— salta directamente a:</p>
    <ul>
      <li>La <b>app en ejecución</b> (URL firmada, sin publicar puertos).</li>
      <li>La <b>consola de revisión</b> (terminal, logs en vivo, checklist).</li>
      <li>El <b>detalle del plan</b> para emitir el veredicto.</li>
    </ul>
    <p>Es la bandeja de trabajo del validador: si hay algo aquí, hay un plan
    esperando a un humano. Las sesiones caducan solas (48 h por defecto) y sus
    contenedores se reciclan automáticamente; el veredicto y el motivo quedan
    en el historial.</p>`,
  },
  ...(PLAN
    ? ([
        {
          title: "Tareas escaladas y bloqueadas de un plan",
          goto: `/admin/plans/${PLAN}/escalated`,
          fullPage: true,
          settleMs: 1800,
          body: `<p>Cuando una tarea agota sus reintentos, su proveedor falla de
          forma persistente o el propio agente pide ayuda, la tarea se
          <b>escala</b>: queda <code>blocked</code> a la espera de una decisión
          humana. Este panel reúne, para un plan, todas las tareas en ese
          estado con las <b>acciones humanas</b> disponibles:</p>
          <ul>
            <li><b>Reintentar</b> — re-encola la tarea reseteando sus
            reintentos; si el plan entero estaba bloqueado por ella, también lo
            reactiva.</li>
            <li><b>Reasignar con guía</b> — devuelve la tarea al agente con una
            indicación tuya (aparece en su contexto del siguiente run).</li>
            <li><b>Bloquear con motivo</b> — la aparca documentando por qué.</li>
          </ul>
          <p>Si el plan completo está <code>blocked</code> (ninguna tarea puede
          avanzar), el botón <b>«Desbloquear plan»</b> — disponible aquí y en la
          cabecera del detalle del plan — lo reactiva y re-encola todas sus
          tareas bloqueadas de una vez.</p>
          <p>Ejemplo real: un run abortado por <i>cuota del proveedor agotada</i>
          (HTTP 429) bloquea su tarea; cuando la cuota resetea, «Reintentar» la
          relanza y el ciclo continúa donde estaba.</p>`,
        },
      ] as Step[])
    : []),
];

const manual: ManualDef = {
  order: "13",
  slug: "13-runs-y-supervision",
  title: "Visor de runs y supervisión de la ejecución",
  audience:
    "Supervisores de la ejecución autónoma, tech leads y operadores que auditan qué hicieron los agentes, cuánto costó y qué se atascó.",
  intro: `<p>Los agentes trabajan solos, pero <b>nunca a oscuras</b>: cada run queda registrado paso a
    paso con sus tokens, su coste y su resultado, y las situaciones que necesitan a un humano
    (validaciones, escalados, bloqueos) tienen su propia bandeja. Este manual recorre las cuatro
    pantallas de supervisión: el <b>visor global de runs</b>, el <b>timeline</b> de una ejecución,
    las <b>sesiones de revisión activas</b> y el panel de <b>tareas escaladas</b> de un plan.</p>`,
  steps,
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  test.setTimeout(240_000);
  await login(page);
  await generateManual(page, manual);
});
