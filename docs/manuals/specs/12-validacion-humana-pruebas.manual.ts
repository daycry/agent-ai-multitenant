import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef, Step } from "../lib/manual";
import { seededCorrectionsPlanId, seededPhpPlanId, seededPhpProjectId } from "../lib/seed-helper";

/**
 * Manual 12 — Validación humana: probar la app levantada (ADR 0062).
 *
 * El flujo PRIMORDIAL: cuando un plan termina, la plataforma levanta la app en
 * un contenedor de revisión y el panel ofrece un LINK CLICABLE para abrirla y
 * probarla antes de aprobar el plan. La demo se siembra con
 * scripts/dev/seed-review-demo.ps1 (review-session + app "Hello World PHP").
 */
const PID = seededPhpProjectId();
const PLAN = seededPhpPlanId();
const CORRECTIONS_PLAN = seededCorrectionsPlanId();

const steps: Step[] =
  PID && PLAN
    ? [
        {
          title: "Ver las tareas ejecutándose: el timeline del agente",
          fullPage: true,
          settleMs: 1800,
          body: `<p>Antes de validar, conviene ver <b>cómo trabajaron los
          agentes</b>. La plataforma registra el <b>timeline de ejecución</b> de
          cada tarea paso a paso: <i>percibe</i> la tarea, <i>recuerda</i>
          contexto, <i>llama al modelo</i> (LLM), <i>decide</i>, <i>finaliza</i>
          y se <i>auto-revisa</i> — con el <b>estado</b>, las <b>iteraciones</b>,
          los <b>tokens</b> consumidos y el <b>coste</b> de la ejecución.</p>
          <p>Esta es la prueba de que la tarea se <b>ejecutó de verdad</b>: un
          agente recibió la tarea, invocó al modelo del proveedor configurado y
          produjo el resultado. Cada paso muestra su latencia y su veredicto
          (<code>ok</code>), y la vista se actualiza en vivo mientras corre.</p>`,
          // Localiza la ejecución más reciente del tenant (listado newest-first) y
          // abre su timeline — la pantalla real de seguimiento de la ejecución.
          action: async (page) => {
            const token = await page.evaluate(() => localStorage.getItem("agentic.token"));
            const res = await page.request.get(`/api/tenant-stats/runs?window_days=730`, {
              headers: token ? { Authorization: `Bearer ${token}` } : {},
            });
            if (res.ok()) {
              const rows = (await res.json()) as Array<{
                execution_id?: string;
                id?: string;
                status?: string;
              }>;
              const pick = rows.find((r) => r.status === "done") ?? rows[0];
              const id = pick?.execution_id ?? pick?.id;
              if (id) {
                await page
                  .goto(`/admin/executions/${id}`, { waitUntil: "networkidle" })
                  .catch(() => {});
                await page.waitForTimeout(1500);
              }
            }
          },
        },
        {
          title: "El plan en validación humana",
          goto: `/admin/projects/${PID}/plans/${PLAN}`,
          fullPage: true,
          settleMs: 1800,
          body: `<p>Cuando todas las tareas de un plan terminan, el plan pasa a
          <code>pending_human_validation</code> y la plataforma <b>levanta la app
          construida</b> en un contenedor de revisión. En el detalle del plan
          aparece el panel <b>«Validación humana — probar la app»</b> con un
          botón <b>«Abrir app para probar»</b> y los botones de veredicto
          (<b>Aprobar</b> / <b>Rechazar</b>).</p>
          <p>El enlace abre la app servida por el <b>review-runtime</b> a través
          del <b>proxy firmado del api-server</b> (ADR 0062): no se publica ningún
          puerto al exterior; el acceso solo es posible con la URL firmada.</p>`,
        },
        {
          title: "Abrir la app levantada y probarla",
          fullPage: true,
          settleMs: 1500,
          body: `<p>Al pulsar <b>«Abrir app para probar»</b> se abre la
          <b>aplicación en ejecución</b> (aquí el ejemplo <i>Hello World PHP</i>,
          que expone <code>GET /hello</code>). Es la app REAL construida por los
          agentes, levantada en su contenedor, lista para que la pruebes: navega,
          ejecuta acciones y comprueba que cumple los criterios del plan.</p>
          <p>Así un humano puede <b>validar que el plan es correcto</b> probando
          el resultado de verdad, no solo leyendo el código.</p>`,
          // Obtiene la URL firmada de la sesión de review (con el token del
          // operador) y abre la app — exactamente lo que hace el botón del panel.
          action: async (page) => {
            const token = await page.evaluate(() => localStorage.getItem("agentic.token"));
            const res = await page.request.get(`/api/plans/${PLAN}/review-session`, {
              headers: token ? { Authorization: `Bearer ${token}` } : {},
            });
            if (res.ok()) {
              const data = (await res.json()) as { app_url?: string };
              if (data.app_url) {
                await page.goto(data.app_url, { waitUntil: "networkidle" }).catch(() => {});
              }
            }
          },
        },
        {
          title: "Consola de revisión (terminal, logs y checklist)",
          fullPage: true,
          settleMs: 1500,
          body: `<p>Junto a la app, la <b>consola de revisión</b> reúne las
          herramientas para validar a fondo: una <b>terminal web</b> acotada al
          workspace, los <b>logs en vivo</b> del contenedor (WebSocket), un botón
          para <b>re-ejecutar los tests</b> automáticos, y el <b>checklist de
          tests humanos</b> del plan. Se abre con la misma URL firmada que la
          app (sin necesidad de cuenta: pensada para revisores invitados).</p>`,
          action: async (page) => {
            const token = await page.evaluate(() => localStorage.getItem("agentic.token"));
            const res = await page.request.get(`/api/plans/${PLAN}/review-session`, {
              headers: token ? { Authorization: `Bearer ${token}` } : {},
            });
            if (res.ok()) {
              const data = (await res.json()) as { review_url?: string };
              if (data.review_url) {
                await page.goto(data.review_url, { waitUntil: "networkidle" }).catch(() => {});
              }
            }
          },
        },
        {
          title: "Emitir el veredicto: aprobar o rechazar el plan",
          goto: `/admin/projects/${PID}/plans/${PLAN}`,
          // Viewport (no fullPage) + scroll al panel: primer plano del veredicto,
          // distinto del paso 1 (que muestra el plan completo).
          fullPage: false,
          settleMs: 1500,
          action: async (page) => {
            await page
              .getByTestId("plan-human-validation")
              .scrollIntoViewIfNeeded()
              .catch(() => {});
          },
          body: `<p>Tras probar la app y revisar el checklist, emites el
          <b>veredicto</b> desde el panel de validación: <b>«Aprobar plan»</b>
          (el plan pasa a <code>completed</code>, se cierra el ciclo y la
          plataforma abre automáticamente el <b>Pull Request del plan</b>) o
          <b>«Rechazar»</b> (el plan pasa a <code>rejected</code> con tu
          motivo). Los contenedores de la sesión los recicla el mantenimiento;
          el veredicto y el motivo quedan en el historial de la sesión.</p>
          <p>Este es el control humano final: ningún plan se da por bueno sin que
          una persona haya <b>probado la app levantada</b> y dado el visto bueno.</p>`,
        },
        {
          title: "Rechazar con motivo: la modal con Markdown",
          goto: `/admin/projects/${PID}/plans/${PLAN}`,
          fullPage: false,
          settleMs: 1500,
          action: async (page) => {
            await page
              .getByTestId("plan-human-validation")
              .scrollIntoViewIfNeeded()
              .catch(() => {});
            await page
              .getByTestId("plan-verdict-reject")
              .click({ timeout: 5000 })
              .catch(() => {});
            await page.waitForTimeout(600);
          },
          body: `<p>Si algo no está bien, <b>«Rechazar»</b> abre una modal con un
          <b>textarea con vista previa de Markdown</b>. El motivo que escribas
          aquí <b>no se pierde</b>: es la materia prima del ciclo de
          correcciones. Escríbelo como se lo contarías al equipo — <b>qué está
          mal, dónde y qué esperabas</b>.</p>
          <ul>
            <li>Cuanto más concreto el motivo, mejores serán las tareas
            correctivas que la plataforma proponga después.</li>
            <li>Puedes usar Markdown (código, listas, negritas) y previsualizarlo
            antes de enviar.</li>
            <li>Al confirmar, el plan pasa a <code>rejected</code> — un
            aparcamiento seguro: nada se ejecuta hasta que alguien decida.</li>
          </ul>`,
        },
        ...(CORRECTIONS_PLAN
          ? ([
              {
                title: "La tarjeta «Correcciones del rechazo» (ADR 0107)",
                goto: `/admin/projects/${PID}/plans/${CORRECTIONS_PLAN}`,
                fullPage: true,
                settleMs: 1800,
                body: `<p>Un plan rechazado NO es un callejón sin salida. En su
                detalle aparece la tarjeta <b>«Correcciones del rechazo»</b> con
                tres piezas:</p>
                <ul>
                  <li><b>El motivo del validador</b>, renderizado en Markdown —
                  la referencia compartida de qué hay que corregir.</li>
                  <li><b>«Generar tareas correctivas»</b>: el Project Manager
                  (el LLM del proyecto) convierte el motivo en tareas concretas
                  con <b>criterios de aceptación verificables</b>, rol,
                  complejidad y dependencias — ids <code>fix-1</code>,
                  <code>fix-2</code>… que también verás en la tabla de tareas
                  del plan con el badge <b>«corrección»</b>.</li>
                  <li><b>Las propuestas con checkbox</b> (todas marcadas por
                  defecto): revisa título, descripción y criterios de cada una,
                  y desmarca las que no quieras materializar.</li>
                </ul>
                <p><b>«Aceptar correcciones»</b> crea las tareas marcadas en el
                Kanban y <b>reactiva el plan</b> (<code>rejected →
                in_progress</code>) en el mismo acto: los agentes corrigen en la
                <b>misma rama git</b> del plan (el PR final llevará el trabajo
                original + los fixes) y, cuando terminan, el plan vuelve a
                validación humana con una sesión nueva. La relación
                rechazo↔corrección queda guardada en el propio plan como
                historial.</p>`,
              },
              {
                title: "Las tareas correctivas en la tabla del plan",
                goto: `/admin/projects/${PID}/plans/${CORRECTIONS_PLAN}`,
                fullPage: false,
                settleMs: 1500,
                action: async (page) => {
                  await page
                    .getByTestId("plan-tasks")
                    .scrollIntoViewIfNeeded()
                    .catch(() => {});
                },
                body: `<p>Las tareas nacidas de un rechazo se distinguen a simple
                vista: llevan el badge <b>«corrección»</b> junto al título en la
                tabla de tareas del plan. Mantienen todo el contrato de una
                tarea normal — rol, complejidad, dependencias (pueden depender
                de tareas originales o de otras correctivas) y criterios de
                aceptación — así el orquestador, el review IA y el Kanban las
                tratan exactamente igual que al resto.</p>
                <p>Si repites el ciclo (rechazas otra vez con otro motivo), cada
                tanda genera ids nuevos deduplicados: el plan acumula su
                historial de correcciones sin pisarse.</p>`,
              },
            ] as Step[])
          : []),
      ]
    : [
        {
          title: "Validación humana (requiere datos sembrados)",
          goto: "/admin/board",
          body: `<p>Este manual documenta la validación humana con una app levantada
          real; siembra la demo con <code>scripts/dev/seed-review-demo.ps1</code>
          antes de generarlo.</p>`,
        },
      ];

const manual: ManualDef = {
  order: "12",
  slug: "12-validacion-humana-pruebas",
  title: "Validación humana: probar la app levantada",
  audience:
    "Validadores de plan, responsables técnicos y comité de dirección que deben probar el resultado antes de aprobarlo.",
  intro: `<p>El paso de <b>validación humana</b> es el control final de calidad: antes de dar un plan
    por bueno, una persona <b>prueba la aplicación construida</b>, levantada en un contenedor de
    revisión y accesible mediante un <b>enlace clicable</b> desde el panel. Este manual recorre ese
    flujo de extremo a extremo: localizar el plan en validación, abrir la app para probarla, usar la
    consola de revisión y emitir el veredicto.</p>`,
  steps,
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  test.setTimeout(240_000);
  await login(page);
  await generateManual(page, manual);
});
