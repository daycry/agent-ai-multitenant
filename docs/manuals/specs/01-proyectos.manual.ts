import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef, Step } from "../lib/manual";
import { seededPhpProjectId } from "../lib/seed-helper";

// El proyecto demo "Hello World PHP" se siembra con lib/seed-demo-data.mjs antes
// de generar; sus sub-páginas se capturan con contenido real.
const PID = seededPhpProjectId();

// Pasos del hub de un proyecto real (solo si hay proyecto sembrado).
const hubSteps: Step[] = PID
  ? [
      {
        title: "El hub del proyecto «Hello World PHP»",
        goto: `/admin/projects/${PID}`,
        fullPage: true,
        body: `<p>Al abrir un proyecto llegas a su <b>hub</b>: la cabecera muestra el
          <b>nombre</b>, la <b>insignia de estado</b> y la descripción, junto a los
          botones <b>«Editar»</b> y <b>«Borrar»</b>. Debajo, una cuadrícula de
          tarjetas da acceso a cada sección del proyecto.</p>
          <p>Este proyecto de ejemplo expone un microservicio PHP con un endpoint
          <code>GET /hello</code>; lo usaremos como hilo conductor para ver planes,
          tareas, conocimiento y ejecución.</p>`,
      },
      {
        title: "Chat con los agentes del proyecto",
        goto: `/admin/projects/${PID}/chat`,
        fullPage: true,
        body: `<p>La sección <b>Chat</b> es la conversación con el equipo de agentes
          del proyecto. Desde aquí pides trabajo en lenguaje natural («crea el
          endpoint y su test»); el sistema lo traduce en <b>planes</b> y <b>tareas</b>
          que los agentes ejecutan. Cada mensaje queda asociado al proyecto y a su
          contexto (conocimiento, memoria, herramientas autorizadas).</p>`,
      },
      {
        title: "Planes del proyecto",
        goto: `/admin/projects/${PID}/plans`,
        fullPage: true,
        body: `<p>La sección <b>Planes</b> lista los planes de construcción del
          proyecto. Cada plan es la <b>unidad de cambio</b>: agrupa un conjunto
          ordenado de tareas con dependencias y se materializa como una rama git
          <code>plan/&lt;id&gt;-&lt;slug&gt;</code>. Verás el plan
          <b>«MVP — API Hello World en PHP»</b> creado para este ejemplo.</p>
          <p>Al abrir un plan accedes a su <b>Kanban</b> de tareas (ver el manual de
          Planes y Kanban).</p>`,
      },
      {
        title: "Tareas del proyecto",
        goto: `/admin/projects/${PID}/tasks`,
        fullPage: true,
        body: `<p>La sección <b>Tasks</b> reúne <b>todas</b> las tareas del proyecto,
          pertenezcan o no a un plan. Para el plan de ejemplo verás cuatro tareas:
          definir el endpoint, implementar el controlador PHP, escribir el test
          PHPUnit y documentarlo. Cada tarea muestra su título, estado, prioridad y,
          si está asignada, el agente responsable.</p>`,
      },
      {
        title: "Bases de conocimiento del proyecto (RAG)",
        goto: `/admin/projects/${PID}/knowledge-bases`,
        fullPage: true,
        body: `<p>Aquí se gestionan las <b>bases de conocimiento</b> (RAG) concedidas
          al proyecto: documentación, convenciones y material que los agentes
          consultan al trabajar. Se conceden bases del catálogo del tenant o
          built-in; los documentos se indexan (embeddings en pgvector) para
          búsqueda semántica.</p>`,
      },
      {
        title: "Comandos autorizados y runtime por defecto",
        goto: `/admin/projects/${PID}/commands`,
        fullPage: true,
        body: `<p>La sección <b>Comandos & runtime</b> define la <b>lista blanca</b>
          de comandos que los agentes pueden ejecutar vía <code>shell_exec</code>
          (deny-by-default: lista vacía = no ejecuta nada) y el <b>runtime template</b>
          por defecto del proyecto — aquí <code>php-phpunit</code>. Los runtimes son
          contenedores efímeros y aislados donde se ejecutan los tests, nunca el
          worker.</p>`,
      },
      {
        title: "Servidores MCP del proyecto",
        goto: `/admin/projects/${PID}/mcp-servers`,
        fullPage: true,
        body: `<p>La sección <b>MCP servers</b> conecta el proyecto con servidores
          <b>Model Context Protocol</b>: herramientas externas (repos, bases de datos,
          servicios) que los agentes pueden invocar de forma gobernada. Cada servidor
          declara sus herramientas, que luego se asignan por agente.</p>`,
      },
      {
        title: "Memoria del proyecto",
        goto: `/admin/projects/${PID}/memories`,
        fullPage: true,
        body: `<p>La <b>Memoria</b> del proyecto es lo que el equipo «recuerda» en el
          ámbito <b>project_shared</b>: decisiones, hechos y aprendizajes que persisten
          entre ejecuciones. La plataforma distingue ámbitos de memoria (privada del
          agente, de equipo, de proyecto y global) y nunca los mezcla.</p>`,
      },
      {
        title: "Diagnóstico de herramientas por agente",
        goto: `/admin/projects/${PID}/agent-tools-diagnostic`,
        fullPage: true,
        body: `<p>Esta vista de <b>solo lectura</b> muestra, para cada agente del
          proyecto, qué <b>herramientas</b> tiene asignadas y con qué variante
          (sandboxed, etc.). Es útil para auditar de un vistazo qué puede hacer cada
          agente antes de lanzar una ejecución.</p>`,
      },
    ]
  : [];

const manual: ManualDef = {
  order: "01",
  slug: "01-proyectos",
  title: "Proyectos",
  audience:
    "Administradores de tenant (tenant_admin) y operadores que gestionan los proyectos de su organización en el panel de administración.",
  intro:
    "<p>Este manual explica cómo trabajar con <b>Proyectos</b> en el panel de administración de la plataforma agéntica. Un proyecto es la unidad de trabajo donde viven los planes, tareas, conversaciones con agentes, comandos autorizados, servidores MCP, bases de conocimiento y memorias del equipo.</p><p>Aprenderás a localizar y consultar la lista de proyectos de tu tenant, a crear un proyecto nuevo paso a paso mediante el asistente (partiendo de una plantilla o en blanco), y recorrerás un proyecto real de ejemplo (<b>«Hello World PHP»</b>) sección por sección.</p>",
  steps: [
    {
      title: "Lista de proyectos",
      goto: "/admin/projects",
      fullPage: true,
      body: "<p>Esta es la pantalla principal de <b>Proyectos</b>. Muestra los proyectos activos de tu tenant en una cuadrícula de tarjetas; las plantillas no aparecen aquí, se eligen al crear un proyecto.</p><p>Cada tarjeta muestra el <b>nombre</b> del proyecto, una <b>insignia de estado</b> (<code>active</code> en verde, <code>paused</code> en ámbar, <code>archived</code> en gris) y la <b>descripción</b>. Haz clic en cualquier tarjeta para abrir el detalle de ese proyecto.</p><p>En la esquina superior derecha, los usuarios con rol <b>administrador de tenant</b> o superior ven el botón <b>«Crear proyecto»</b>.</p>",
    },
    {
      title: "Asistente de creación — Paso 1: elegir plantilla o empezar en blanco",
      goto: "/admin/projects/new",
      fullPage: true,
      body: "<p>Al pulsar «Crear proyecto» entras en el <b>asistente de dos pasos</b>. El paso 1 te permite elegir el punto de partida. La primera opción es <b>«Proyecto en blanco»</b>: empieza sin plantilla y no concede ninguna base de conocimiento por defecto. Pulsa <b>«Empezar en blanco»</b> para pasar al paso 2.</p><p>Debajo se muestra la cuadrícula de <b>plantillas</b> disponibles. Cada tarjeta indica su nombre, opcionalmente el <b>equipo</b> al que pertenece (insignia azul) y una descripción. Pulsa <b>«Usar plantilla»</b> para seleccionarla; el nombre y la descripción del proyecto se rellenarán automáticamente.</p>",
    },
    {
      title: "Asistente de creación — Paso 2: personalizar y crear",
      goto: "/admin/projects/new",
      fullPage: true,
      body: "<p>El paso 2 permite ajustar los detalles antes de crear. En el panel <b>«Detalles del proyecto»</b> rellenas: <b>Nombre</b> (obligatorio), <b>Descripción</b> y <b>Runtime por defecto</b> (un desplegable con el catálogo de runtimes del stack; para este ejemplo elegiríamos <code>PHP · PHPUnit</code>). Si elegiste una plantilla, aparece la casilla <b>«Conceder las bases de conocimiento de la plantilla»</b>.</p><p>El panel <b>«Preview»</b> resume lo que llevará el proyecto (plantilla, equipo, política de aprobación humana, repositorio). El botón <b>«Crear proyecto»</b> se habilita cuando hay nombre; al crearlo, el sistema te redirige a la lista.</p>",
    },
    ...hubSteps,
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  test.setTimeout(360_000);
  await login(page);
  await generateManual(page, manual);
});
