import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef, Step } from "../lib/manual";
import { seededPhpPlanId, seededPhpProjectId } from "../lib/seed-helper";

// GENERADO desde el workflow de redacción. Editable a mano; reejecutable.
//
// El seed (lib/seed-demo-data.mjs) deja en assets/seed.json el proyecto PHP y su
// plan «MVP — API Hello World en PHP» (en borrador, con 4 tareas Kanban) y un
// segundo plan rechazado con tareas correctivas («Página de estado — demo del
// ciclo de correcciones») cuyo spec sí tiene tareas → DAG/Gantt/correcciones.
const PID = seededPhpProjectId();
const PLAN_ID = seededPhpPlanId();

// Pasos sobre el plan real sembrado (solo si el seed dejó proyecto y plan).
const planDetailSteps: Step[] =
  PID && PLAN_ID
    ? [
        {
          title: "La pestaña Planes del proyecto",
          goto: `/admin/projects/${PID}/plans`,
          fullPage: true,
          body: "<p>Además del tablero global, cada proyecto tiene su pestaña <b>Planes</b> con la lista completa de sus planes de construcción. Es la vista de gestión del ciclo de vida: aquí se ve en qué estado está cada plan y se entra a su detalle.</p><ul><li><b>Filtros por estado</b>: la fila de chips permite filtrar por los diez estados del ciclo — <i>Borrador</i>, <i>Pendiente de aprobación</i>, <i>Aprobado</i>, <i>En progreso</i>, <i>Bloqueado</i>, <i>Pendiente validación humana</i>, <i>Completado</i>, <i>Rechazado</i>, <i>Cancelado</i> y <i>Archivado</i> — cada uno con su contador entre paréntesis.</li><li><b>Dos vistas</b>: lista (una tarjeta por plan, con título, insignia de estado y descripción) o un mini-kanban de planes agrupado por estado, conmutables con el selector de vista de la cabecera.</li><li><b>«Generar desde chat»</b>: abre el chat del proyecto en modo Planning para crear un plan nuevo conversando con el equipo de agentes.</li></ul><p>Haz clic en un plan para abrir su <b>vista de detalle</b>, que recorremos en los pasos siguientes.</p>",
        },
        {
          title: "Detalle del plan: cabecera y ciclo de vida",
          goto: `/admin/projects/${PID}/plans/${PLAN_ID}`,
          fullPage: true,
          body: "<p>La vista de detalle de un plan concentra todo lo que hay que saber de él. En cabecera: el <b>título</b>, la <b>insignia de estado</b> (aquí <i>Borrador</i>) y la descripción del plan.</p><p>Justo debajo aparece la barra <b>«Ciclo de vida del plan»</b>. No es un indicador: es una <b>barra de acción</b> que ofrece únicamente la transición legal para el estado actual:</p><ul><li>Plan en <b>borrador</b> → botón <b>«Enviar a aprobación»</b>: lo pasa a <i>pendiente de aprobación</i> para que un responsable lo revise.</li><li>Plan <b>pendiente de aprobación</b> → botón <b>«Aprobar plan»</b>: lo aprueba; a partir de ahí ya se pueden materializar sus tareas al Kanban.</li><li>Plan <b>aprobado</b> → botón <b>«Empezar ejecución»</b>: lo marca <i>en curso</i> y crea sus tareas en el Kanban para que el equipo de agentes empiece a trabajar.</li><li>Plan <b>bloqueado</b> → botón <b>«Desbloquear plan»</b>: lo reactiva y re-encola todas sus tareas bloqueadas, reiniciando sus reintentos.</li></ul><p>Si ninguna transición aplica (por ejemplo, un plan ya en curso o completado), la barra no se muestra. Un texto bajo los botones explica qué significa la acción disponible, y los errores de transición se muestran en línea.</p><p>Más abajo, la tarjeta <b>«Paneles del plan»</b> ofrece accesos directos: <b>Tareas escaladas y bloqueadas</b> (siempre) y, cuando el plan está en validación humana, la <b>sesión de review</b>. La tarjeta <b>«Resumen»</b> recoge la especificación del plan (descripción, alcance dentro/fuera, decisiones y riesgos con su mitigación) cuando la conversación de planning la ha generado; en este plan sembrado a mano aparece vacía.</p>",
        },
        {
          title: "Sincronizar al Kanban: materializar las tareas del plan",
          goto: `/admin/projects/${PID}/plans/${PLAN_ID}`,
          fullPage: false,
          settleMs: 800,
          action: async (page) => {
            await page
              .getByTestId("plan-sync-to-kanban")
              .scrollIntoViewIfNeeded()
              .catch(() => {});
            await page.waitForTimeout(400);
          },
          body: "<p>La tarjeta <b>«Sincronizar al Kanban»</b> convierte las tareas de la <i>especificación</i> del plan en <b>tarjetas reales del Kanban</b> sobre las que trabajan los agentes. Es el puente entre el plan diseñado y el trabajo ejecutable.</p><p>El botón <b>«Sincronizar al Kanban»</b> abre un diálogo con tres <b>alcances</b>:</p><ul><li><b>Plan completo</b>: materializa todas las tareas del plan (el diálogo indica cuántas).</li><li><b>Una fase</b>: solo las tareas de la fase elegida en el desplegable — útil para trabajar por etapas sin inundar el tablero.</li><li><b>Selección custom</b>: marca con casillas exactamente qué tareas materializar.</li></ul><p>Al confirmar, se muestra el <b>resultado</b>: cuántas tareas nuevas se han creado, cuántas ya existían (la sincronización es idempotente: repetirla no duplica tarjetas) y cuántas <b>dependencias</b> entre tareas se han creado.</p><p><b>Guardas importantes</b>: solo se pueden materializar tareas de un plan <b>aprobado o en curso</b> — un borrador no puede sembrar el Kanban (la propia tarjeta lo avisa, como se ve en la captura). Y si el plan aún no tiene tareas en su especificación, el botón queda deshabilitado.</p>",
        },
        {
          title: "Desglose de coste y estimaciones del plan",
          goto: `/admin/projects/${PID}/plans/${PLAN_ID}`,
          fullPage: false,
          settleMs: 800,
          action: async (page) => {
            await page
              .getByTestId("plan-cost-breakdown")
              .scrollIntoViewIfNeeded()
              .catch(() => {});
            await page.waitForTimeout(400);
          },
          body: "<p>La tarjeta <b>«Desglose de coste»</b> responde a la pregunta gerencial clave: <i>¿cuánto costaría este plan hecho por personas y cuánto hecho por agentes?</i> Presenta dos tablas calculadas tarea a tarea:</p><ul><li><b>Coste humano</b>: por cada tarea, las <b>horas estimadas</b> y su coste aplicando la <b>tarifa por hora</b> del tenant (configurable en los ajustes de la plataforma), con la fila de <b>total</b> de horas y euros.</li><li><b>Coste IA</b>: por cada tarea, su <b>complejidad</b>, el <b>modelo</b> LLM que la ejecutaría y una horquilla de <b>coste mínimo y máximo</b> derivada de los tokens estimados y los precios del catálogo de modelos. La fila final muestra el <b>rango total</b>. Si algún modelo no tiene precio en el catálogo, un aviso lo señala para que el administrador lo complete.</li></ul><p>Cuando la especificación del plan incluye estimaciones globales, la tarjeta <b>«Estimaciones»</b> (encima) resume además la <b>duración de calendario</b>, el <b>esfuerzo en persona-días</b> y los costes humano e IA agregados.</p><p><b>Caso de uso</b>: comparar ambos totales antes de aprobar un plan es la base del business case del trabajo agéntico; si el plan aún no tiene tareas, la tarjeta lo indica y no muestra números.</p>",
        },
        {
          title: "Comentarios del plan",
          goto: `/admin/projects/${PID}/plans/${PLAN_ID}`,
          fullPage: false,
          settleMs: 800,
          action: async (page) => {
            await page
              .getByTestId("plan-comments")
              .scrollIntoViewIfNeeded()
              .catch(() => {});
            await page.waitForTimeout(400);
          },
          body: "<p>Al pie del detalle, la tarjeta <b>«Comentarios»</b> es el canal de feedback humano sobre el plan. No es un chat decorativo: <b>los comentarios sobre una tarea llegan al prompt del agente</b> que la ejecuta, así que son la vía directa para matizar o corregir el trabajo sin re-planificar.</p><ul><li>La lista superior muestra los comentarios existentes, cada uno etiquetado con su <b>objetivo</b>: <i>sobre el plan</i> (observación general), <i>sobre una fase</i> o <i>sobre una tarea</i> concreta (con su identificador).</li><li>Para escribir uno, elige el objetivo en el desplegable (<b>«Sobre el plan»</b> o <b>«Sobre una tarea»</b> — este segundo activa un selector con las tareas de la especificación), redacta en el área de texto (admite <b>Markdown</b> con previsualización) y pulsa <b>«Comentar»</b>.</li></ul><p><b>Buena práctica</b>: comenta sobre la tarea concreta siempre que puedas — un comentario dirigido («en fix-1, el filtro debe acotarse a api/*») guía al agente mucho mejor que una observación general sobre el plan.</p>",
        },
        {
          title: "Un plan con especificación completa: fases, tareas, DAG y Gantt",
          goto: `/admin/projects/${PID}/plans`,
          fullPage: true,
          settleMs: 1200,
          action: async (page) => {
            // Abre el plan de demo con especificación completa (tareas con
            // dependencias): su detalle renderiza las secciones DAG y Gantt.
            const row = page
              .locator('[data-testid^="plan-row-"]', { hasText: "Página de estado" })
              .first();
            await row.click().catch(async () => {
              await page
                .locator('[data-testid^="plan-row-"]')
                .first()
                .click()
                .catch(() => {});
            });
            await page.waitForTimeout(1000);
          },
          body: "<p>Cuando la especificación del plan incluye tareas (lo habitual en un plan generado desde el chat de planning), el detalle muestra varias secciones adicionales. Aquí lo vemos con un plan de demostración cuyo spec trae tres tareas encadenadas:</p><ul><li><b>Fases</b>: la cadena ordenada de fases del plan, cada una con su descripción y la lista de tareas que contiene.</li><li><b>Tareas (N)</b>: la tabla plana de la especificación con, por tarea, su <b>ID</b> corto, <b>título</b>, <b>rol</b> responsable (backend_dev, qa…), <b>complejidad</b> (s/m/l) y de qué tareas <b>depende</b>. Las tareas nacidas de un rechazo humano llevan la insignia ámbar <i>corrección</i>.</li><li><b>Grafo de dependencias</b> (DAG): la representación visual de las dependencias entre tareas — qué debe terminar antes de que empiece qué. Es la forma más rápida de detectar cuellos de botella: una tarea de la que cuelgan muchas otras es crítica.</li><li><b>Gantt</b>: la proyección temporal de las tareas según sus dependencias y estimaciones, con el <b>camino crítico</b> destacado — la secuencia que determina la duración total del plan.</li></ul><p><b>Para qué sirve</b>: revisar el DAG y el Gantt antes de aprobar permite reequilibrar el plan (partir tareas grandes, paralelizar ramas) cuando cambiar aún es barato.</p>",
        },
        {
          title: "Plan rechazado: correcciones a partir del motivo del validador",
          goto: `/admin/projects/${PID}/plans`,
          fullPage: false,
          settleMs: 1000,
          action: async (page) => {
            const row = page
              .locator('[data-testid^="plan-row-"]', { hasText: "Página de estado" })
              .first();
            await row.click().catch(async () => {
              await page
                .locator('[data-testid^="plan-row-"]')
                .first()
                .click()
                .catch(() => {});
            });
            await page.waitForTimeout(1000);
            await page
              .getByTestId("plan-corrections")
              .scrollIntoViewIfNeeded()
              .catch(() => {});
            await page.waitForTimeout(400);
          },
          body: "<p>Cuando un plan llega a <b>validación humana</b> (<code>pending_human_validation</code>), su detalle muestra la tarjeta <b>«Validación humana — probar la app»</b>: si el proyecto tiene configurado el app-preview, el botón <b>«Abrir app para probar»</b> levanta la aplicación construida por los agentes en un contenedor de revisión (servida por un proxy firmado, sin publicar puertos), y la <b>consola de revisión</b> da acceso a terminal, logs y checklist. Desde ahí el validador emite su veredicto: <b>Aprobar plan</b> o <b>Rechazar</b> — el rechazo pide un <b>motivo</b> (en Markdown), y ese motivo es exactamente el feedback que recibirán los agentes, así que conviene concretar qué está mal, dónde y qué se espera.</p><p>Tras un rechazo, el plan pasa a <code>rejected</code> y aparece la tarjeta <b>«Correcciones del rechazo»</b> (en la captura):</p><ul><li>Arriba se muestra el <b>motivo del validador</b> tal cual lo escribió.</li><li>El botón <b>«Generar tareas correctivas»</b> convierte ese motivo en tareas propuestas que se añaden <i>al mismo plan</i> (verás cada una con su rol, complejidad, dependencias y criterios de aceptación).</li><li>Revisa la propuesta, <b>desmarca</b> las tareas que no quieras materializar y pulsa <b>«Aceptar correcciones»</b>: las tareas aceptadas se crean en el Kanban y el plan <b>vuelve a estar en curso</b> — mismo plan, misma rama git, sin empezar de cero.</li></ul><p><b>Aviso</b>: si el plan se rechazó sin sesión de review con motivo, no hay desde dónde generar correcciones automáticas; en ese caso crea las tareas a mano.</p>",
        },
        {
          title: "Tareas escaladas y bloqueadas del plan",
          goto: `/admin/plans/${PLAN_ID}/escalated`,
          fullPage: true,
          body: "<p>El panel <b>«Tareas escaladas y bloqueadas»</b> (accesible desde la tarjeta «Paneles del plan» del detalle) reúne las tareas del plan que <b>esperan una decisión humana</b>: aquellas que los agentes no pudieron completar (reintentos agotados, bloqueo explícito, incertidumbre escalada) y quedaron en espera.</p><p>Para cada tarea escalada se muestra su título, descripción, el número de <b>reintentos</b> consumidos y su historial; y se ofrecen las acciones humanas:</p><ul><li><b>Aprobar manualmente</b>: das el trabajo por bueno tal como está y la tarea sigue su curso.</li><li><b>Reasignar con instrucciones</b>: reencolas la tarea añadiendo una <i>guía</i> concreta para el siguiente intento del agente (se abre un diálogo para escribirla).</li><li><b>Bloquear con motivo</b>: congelas la tarea documentando por qué (diálogo con el motivo).</li><li><b>Cancelar</b> / <b>Reintentar</b>: descartas la tarea o relanzas el intento sin cambios.</li></ul><p>El panel también permite <b>añadir una tarea libre</b> al plan — útil cuando durante la validación detectas trabajo nuevo que no estaba en la especificación.</p><p>Si el plan entero está bloqueado, recuerda que el botón <b>«Desbloquear plan»</b> (aquí, en el detalle del plan o en su tarjeta del tablero) lo reactiva y re-encola todas sus tareas bloqueadas de una vez.</p>",
        },
      ]
    : [];

const manual: ManualDef = {
  order: "02",
  slug: "02-planes-kanban-aprobaciones",
  title: "Planes, Kanban y Aprobaciones",
  audience:
    "Gestores de proyecto, revisores y operadores de tenant que supervisan el avance del trabajo agéntico y autorizan acciones sensibles.",
  intro:
    "<p>Este manual explica cómo seguir y gobernar el trabajo de los equipos de agentes desde el panel de administración: el <b>doble Kanban</b> de planes y tareas, el <b>detalle de cada tarea</b> (criterios, ejecuciones y comentarios), el <b>ciclo de vida completo de un plan</b> — borrador, aprobación, ejecución, validación humana, rechazo y correcciones —, la <b>sincronización al Kanban</b>, el <b>desglose de costes</b>, las vistas <b>DAG y Gantt</b>, la <b>cola de aprobaciones</b> de acciones sensibles y la configuración de las <b>políticas de validación humana</b> por proyecto.</p><p>Aprenderás a leer el tablero, mover tareas entre estados, inspeccionar una tarea y sus ejecuciones, aprobar y arrancar planes, materializar sus tareas, atender solicitudes de aprobación pendientes y elegir qué categorías de acciones requieren intervención humana. El objetivo es que entiendas, paso a paso, qué muestra cada pantalla, qué elementos contiene y qué acciones puedes ejecutar.</p>",
  steps: [
    {
      // PASO 1 — Visión general del tablero + fila de planes y selección.
      title: "El tablero (doble Kanban): planes y selección",
      goto: "/admin/board",
      action: async (page) => {
        // Selecciona el primer plan disponible para resaltar su tarjeta y cargar
        // sus tareas. Tolerante: si no hay planes en este entorno, no rompe.
        await page
          .locator('[data-testid^="plan-card-"]')
          .first()
          .click()
          .catch(() => {});
        await page.waitForTimeout(500);
      },
      body: "<p>La pantalla <b>Tablero</b> es la vista operativa central y respeta el principio del <b>doble Kanban</b>: nunca mezcla tareas de varios planes en un tablero plano. Se divide en dos secciones apiladas verticalmente.</p><ul><li><b>Arriba (gerencial)</b>: la sección <b>Planes</b>, una rejilla de tarjetas con todos los planes del tenant, de cualquier proyecto. A la derecha del título verás el contador total (p. ej. <code>3 planes</code>). Cada tarjeta indica el <b>nombre</b> del plan, una insignia azul con el <b>proyecto</b> al que pertenece y una insignia con su <b>estado</b> del ciclo de vida (<code>draft</code>, <code>approved</code>, <code>in_progress</code>, <code>pending_human_validation</code>, <code>rejected</code>…). Si un plan está <b>bloqueado</b>, su propia tarjeta ofrece el botón <b>«Desbloquear»</b>, que lo reactiva y re-encola sus tareas bloqueadas sin salir del tablero.</li><li><b>Abajo (operativa)</b>: el tablero de <b>Tareas</b> del plan seleccionado, organizado en columnas por estado.</li></ul><p>Al cargar la pantalla se selecciona automáticamente el primer plan. Para cambiar de plan, <b>haz clic sobre su tarjeta</b>: se resalta con un borde destacado y, debajo, el tablero de tareas se actualiza para mostrar únicamente las tareas de ese plan. Si el tenant aún no tiene planes, aparece un mensaje invitándote a crear uno desde el chat de planning de un proyecto.</p><p><b>Cómo leerlo</b>: la fila de planes responde a «¿cómo van los frentes abiertos?» (visión de gestión); el tablero de tareas responde a «¿en qué está trabajando el equipo de este plan ahora mismo?» (visión operativa).</p>",
      fullPage: true,
    },
    {
      // PASO 2 — Tablero de tareas: columnas, tarjetas, mover y tiempo real.
      title: "Tablero de tareas: columnas, mover y tiempo real",
      goto: "/admin/board",
      action: async (page) => {
        // Selecciona el último plan disponible: distinta tarjeta resaltada y,
        // habitualmente, distintas tareas que en el paso anterior.
        await page
          .locator('[data-testid^="plan-card-"]')
          .last()
          .click()
          .catch(() => {});
        await page.waitForTimeout(500);
      },
      body: "<p>La sección inferior <b>Tareas</b> muestra las tareas del plan seleccionado (su nombre aparece junto al título) distribuidas en columnas, una por estado. Las columnas son, en orden: <b>Backlog</b>, <b>Ready</b>, <b>En curso</b>, <b>Pendiente de aprobación</b>, <b>Revisión</b>, <b>Bloqueada</b>, <b>Hecho</b> y <b>Cancelada</b>. Cada columna lleva una insignia con su nombre y un contador; las vacías indican <code>Sin tareas</code>.</p><p>Cada <b>tarjeta de tarea</b> muestra su título, una insignia de <b>prioridad</b> (baja, media, alta o crítica) y un fragmento de su descripción. Si la tarea <b>depende de otras</b>, un candado lo señala: <b>candado rojo cerrado</b> = tiene dependencias sin completar (no puede avanzar), <b>candado gris abierto</b> = todas sus dependencias están hechas. Al pasar el ratón, el candado detalla cuántas dependencias faltan. <b>Haz clic</b> en una tarjeta para abrir su panel de detalle (siguiente paso).</p><p>Para cambiar el estado de una tarea, <b>arrástrala</b> a la columna destino (que se resalta mientras arrastras). El cambio se aplica de forma optimista — la tarjeta salta al instante — y el servidor lo valida: si la transición no es legal, la tarjeta vuelve a su columna y un <b>banner</b> explica el motivo. Dos guardas habituales:</p><ul><li><b>Dependencias sin completar</b>: no puedes llevar a <i>Ready</i> (ni a estados posteriores) una tarea cuyas dependencias no estén hechas — el banner indica cuántas faltan.</li><li><b>Transición no válida</b>: la máquina de estados rechaza saltos ilegales (p. ej. de <i>Hecho</i> a <i>En curso</i>).</li></ul><p>La insignia <b>Tiempo real</b> confirma que el tablero se actualiza en vivo por WebSocket: si otro usuario o un agente cambia el estado de una tarea, o crea una nueva, la verás moverse sin recargar. Nota de comportamiento: al <b>aprobar</b> una tarea pendiente de aprobación vuelve a <i>Backlog</i> y al <b>rechazarla</b> pasa a <i>Bloqueada</i>.</p>",
      fullPage: true,
    },
    {
      // PASO 3 — Detalle de una tarea desde el tablero (sheet con runs, criterios
      // y comentarios). Selecciona el plan MVP (tiene tareas Kanban sembradas) y
      // abre la primera tarjeta.
      title: "Detalle de una tarea: criterios, ejecuciones y comentarios",
      goto: "/admin/board",
      fullPage: false,
      settleMs: 1000,
      action: async (page) => {
        const mvpCard = page
          .locator('[data-testid^="plan-card-"]', { hasText: "Hello World" })
          .first();
        await mvpCard.click().catch(async () => {
          await page
            .locator('[data-testid^="plan-card-"]')
            .first()
            .click()
            .catch(() => {});
        });
        await page.waitForTimeout(800);
        await page
          .locator('[data-testid^="task-card-"]')
          .first()
          .click()
          .catch(() => {});
        await page.waitForTimeout(800);
      },
      body: "<p>Al hacer clic en una tarjeta del tablero se abre el <b>panel de detalle de la tarea</b>, un diálogo con todo lo necesario para inspeccionarla sin abandonar el tablero. Contiene, de arriba abajo:</p><ul><li><b>Título y estado</b> de la tarea, seguidos de su <b>descripción</b> (renderizada desde Markdown).</li><li><b>Criterios de aceptación</b>: la lista de condiciones concretas y verificables que definen «hecho». Son editables: <b>«Editar»</b> abre un editor fila a fila (añadir, modificar, quitar), y <b>«Generar con IA»</b> propone criterios a partir de la descripción — si la tarea ya tenía criterios, se abre un diálogo de <b>comparación</b> (actuales vs. propuestos) para que nada se sobrescriba sin tu confirmación explícita.</li><li><b>Depende de</b>: las tareas de las que depende, resueltas a su <b>título</b> legible (no identificadores crudos).</li><li><b>Runs</b>: el historial de <b>ejecuciones</b> de la tarea por los agentes. Cada fila muestra la fecha, el <b>agente</b> y el <b>modelo</b> LLM usados, la <b>duración</b>, los <b>tokens</b> consumidos, el <b>coste</b> y el <b>veredicto</b> (<code>running</code>, <code>done</code>, <code>failed</code>, <code>aborted</code>…). Mientras hay una ejecución en curso, la lista se refresca sola. Haz clic en una fila para abrir el <b>visor completo del run</b> (transcripción, herramientas invocadas, diffs).</li><li><b>Comentarios</b>: hilo de comentarios de la tarea (para tareas que pertenecen a un plan). Lo que escribas aquí <b>llega al prompt del agente</b> en su siguiente ejecución — es el canal directo para dar instrucciones o correcciones puntuales.</li></ul><p><b>Caso de uso</b>: una tarea falla repetidamente → abre su detalle, revisa el último run (¿qué intentó el agente?), afina los criterios de aceptación si eran ambiguos y deja un comentario con la pista concreta antes de reencolarla.</p>",
    },
    ...planDetailSteps,
    {
      // Cola de aprobaciones (vista + resolución).
      title: "Cola de aprobaciones: revisar, aprobar o rechazar",
      goto: "/admin/approvals",
      body: "<p>La pantalla <b>Aprobaciones</b> (grupo <i>Trabajo</i> de la navegación) es la bandeja de revisión: lista todas las <b>solicitudes de aprobación humana pendientes</b> que un revisor debe resolver para que la ejecución del agente continúe. Cuando un agente intenta una acción que la política del proyecto clasifica como sensible (un push, un despliegue, el acceso a un secreto…), su ejecución <b>se pausa</b> y aquí aparece la solicitud.</p><p>En la cabecera aparece el título <code>Aprobaciones</code> y, debajo, la etiqueta <b>Pendientes</b> con una insignia que indica cuántas hay. Si no hay nada pendiente verás el estado vacío <i>Sin aprobaciones pendientes</i> (la captura); si falla la carga, un bloque de error.</p><p>Cada solicitud se muestra como una tarjeta con:</p><ul><li>La <b>categoría</b> de la acción (p. ej. push, despliegue a producción, acceso a secretos) y una insignia con su <b>estado</b>, junto a la fecha en que se <b>solicitó</b>.</li><li>Un bloque de código con la <b>acción concreta</b> que el agente quiere ejecutar (en JSON), para que revises exactamente qué va a pasar — no una descripción vaga, sino los parámetros reales.</li><li>Un campo <b>Motivo (opcional)</b> para dejar constancia de tu decisión, y los botones <b>Aprobar</b> (la ejecución continúa) y <b>Rechazar</b> (se deniega).</li></ul><p>Al resolver, la solicitud desaparece de la lista y el agente recibe el resultado; si la operación falla, el error se muestra en la propia tarjeta.</p><p><b>Buena práctica</b>: revisa siempre el JSON de la acción antes de aprobar — es tu última barrera antes de que la acción sensible se ejecute — y deja motivo en los rechazos: queda en la traza de auditoría y orienta al agente.</p>",
      fullPage: true,
    },
    {
      // Validación humana: plantilla Sandbox (todo automático).
      title: "Validación humana: plantilla Sandbox",
      goto: "/admin/approval-policy",
      action: async (page) => {
        // Selecciona la plantilla Sandbox (la más permisiva: todo automático).
        await page
          .getByRole("tab", { name: "Sandbox" })
          .click()
          .catch(async () => {
            await page
              .getByRole("button", { name: "Sandbox" })
              .click()
              .catch(async () => {
                await page
                  .getByText("Sandbox", { exact: true })
                  .first()
                  .click()
                  .catch(() => {});
              });
          });
        await page.waitForTimeout(500);
      },
      body: "<p>La pantalla <b>Validación humana</b> (grupo <i>Configuración del tenant</i>) define <b>qué tipos de acciones puede ejecutar un agente automáticamente y cuáles exigen aprobación de una persona</b>. Es la política que alimenta la cola de aprobaciones del paso anterior: cada categoría marcada como «Humano» generará una solicitud cuando un agente intente una acción de esa categoría.</p><p>Todo se gobierna mediante <b>plantillas predefinidas</b> (presets), pensadas para los cuatro perfiles de riesgo típicos:</p><ul><li><b>Sandbox</b>: todo automático — para entornos de experimentación donde el coste de un error es nulo.</li><li><b>Desarrollo</b>: automatiza lo cotidiano y pide validación para lo delicado.</li><li><b>Producción</b>: exige intervención humana en las categorías de mayor impacto.</li><li><b>Cliente Externo</b>: el perfil más conservador, para trabajo sobre sistemas de terceros.</li></ul><p>En la fila superior se muestra una tarjeta por plantilla, con su <b>nombre</b>, una <b>descripción</b> y una insignia que resume cuántas categorías requieren intervención humana. Aquí seleccionamos <b>Sandbox</b>: su insignia muestra <i>Todo automático</i> porque ninguna categoría exige validación. Al hacer clic en una tarjeta, esta se resalta con un borde destacado y la tabla inferior se recalcula con las decisiones de esa plantilla.</p>",
      fullPage: true,
    },
    {
      // Validación humana: plantilla Producción + ajuste + aplicar.
      title: "Validación humana: ajustar categorías y aplicar al proyecto",
      goto: "/admin/approval-policy",
      action: async (page) => {
        // 1) Cambia a la plantilla Producción (varias categorías en "Humano").
        await page
          .getByRole("tab", { name: "Producción" })
          .click()
          .catch(async () => {
            await page
              .getByRole("button", { name: "Producción" })
              .click()
              .catch(async () => {
                await page
                  .getByText("Producción", { exact: true })
                  .first()
                  .click()
                  .catch(() => {});
              });
          });
        await page.waitForTimeout(400);
        // 2) Invierte una categoría concreta (Push) para mostrar el override y la
        //    insignia "Cambios sin guardar".
        await page
          .getByTestId("toggle-git_push")
          .click()
          .catch(() => {});
        await page.waitForTimeout(300);
        // 3) Abre el desplegable de proyecto del panel "Aplicar a un proyecto".
        await page
          .getByTestId("project-select")
          .click()
          .catch(() => {});
        await page.waitForTimeout(400);
      },
      body: "<p>Bajo las plantillas, una tabla lista las <b>13 categorías de acciones sensibles</b> que la plataforma reconoce: cambios de código, commit, push, HTTP GET externo, HTTP POST externo, acceso a secretos, migración de datos, despliegue a producción, aprovisionar infraestructura, rotación de secretos, comunicación externa, exportar PII y gestión de usuarios. Cada fila muestra la categoría, una pista de qué cubre, y un botón que alterna su decisión entre <b>Auto</b> (verde — el agente la ejecuta sin preguntar) y <b>Humano</b> (ámbar — la ejecución se pausa y se crea una solicitud en la cola de Aprobaciones).</p><p>Las plantillas son un punto de partida, no una camisa de fuerza: puedes <b>invertir categorías concretas</b>. Aquí partimos de la plantilla <b>Producción</b> e invertimos la categoría <b>Push</b>: la celda que difiere de la plantilla base se marca con la insignia <b>Override</b>, y mientras haya ajustes pendientes se muestra la insignia <i>Cambios sin guardar</i>.</p><p>La política resultante se aplica <b>por proyecto</b>: en el panel <b>Aplicar a un proyecto</b>, despliega el selector <b>Proyecto</b> (abierto en la captura), elige uno y revisa el <b>Resumen</b> (cuántas categorías quedan en auto y cuántas en humano). Pulsa <b>Aplicar política</b> para guardarla en ese proyecto; un mensaje confirma el éxito o muestra el error. Si el tenant no tiene proyectos, se te indica que crees uno antes de poder guardar.</p><p><b>Buena práctica</b>: distintos proyectos, distintas políticas — Sandbox para el laboratorio y Producción (o más estricto) para lo que toca sistemas reales. Revisa la política tras cada cambio de alcance del proyecto.</p>",
      fullPage: true,
    },
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  test.setTimeout(480_000);
  await login(page);
  await generateManual(page, manual);
});
