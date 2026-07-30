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
        body: `<p>Al abrir un proyecto llegas a su <b>hub</b>: la ficha central desde
          la que se accede a todo lo demás. De arriba abajo encontrarás:</p>
          <ul>
            <li><b>Cabecera</b>: el nombre del proyecto, su descripción y los botones
              <b>«Editar»</b> (campos básicos) y <b>«Borrar»</b> (con confirmación
              escribiendo el nombre; borra planes, tareas y conversaciones — los
              repos git en disco no se tocan).</li>
            <li><b>Fila de estado</b>: la insignia de estado (<code>active</code>,
              <code>paused</code>, <code>archived</code>), si es plantilla y el
              equipo asignado.</li>
            <li><b>Hub de capacidad</b>: un resumen de qué <i>sabe</i> el proyecto
              (bases de conocimiento concedidas) y qué <i>recuerda</i> (ámbitos de
              memoria), con el estado de configuración de cada sección.</li>
            <li><b>Modelo del proyecto</b> y <b>Modelo del chat</b>: el proveedor y
              modelo LLM por defecto para la ejecución de agentes y para el chat.
              Vacío = heredar del nivel superior (equipo → plataforma), siguiendo la
              herencia de modelos de la plataforma.</li>
            <li><b>Configuración Git</b>: remoto, rama por defecto, autenticación
              (PAT o clave SSH) y las políticas del flujo git del plan.</li>
            <li><b>App-preview de validación humana</b>: la imagen y el puerto con
              los que se levanta la app del proyecto cuando un plan llega a
              validación humana (lo vemos en detalle más adelante).</li>
            <li><b>Secciones</b>: la cuadrícula de tarjetas de acceso a cada
              sub-página (Chat, Planes, Tasks, Knowledge Bases, Memoria, MCP
              servers, Tools por agente, Comandos &amp; runtime, Caché de
              dependencias y Webhooks entrantes).</li>
          </ul>
          <p>Este proyecto de ejemplo expone un microservicio PHP con un endpoint
          <code>GET /hello</code>; lo usaremos como hilo conductor para recorrer
          planes, tareas, conocimiento y ejecución.</p>`,
      },
      {
        title: "Editar el proyecto: campos básicos",
        goto: `/admin/projects/${PID}`,
        fullPage: false,
        settleMs: 800,
        action: async (page) => {
          await page
            .getByTestId("project-edit-button")
            .click()
            .catch(() => {});
          await page.waitForTimeout(500);
        },
        body: `<p>El botón <b>«Editar»</b> de la cabecera abre el diálogo de edición
          de los <b>campos básicos</b> del proyecto:</p>
          <ul>
            <li><b>Nombre</b> (obligatorio) y <b>Descripción</b> — el editor de
              descripción soporta Markdown con previsualización.</li>
            <li><b>Estado</b>: <i>Activo</i> (operativa normal), <i>Pausado</i>
              (trabajo suspendido temporalmente) o <i>Archivado</i> (cerrado; deja
              de aparecer en las vistas operativas).</li>
            <li><b>Equipo</b>: el equipo de agentes del proyecto. Gobierna qué
              agentes ejecutan sus tareas y la política de memoria. Puedes dejarlo
              «Sin equipo» y asignarlo más tarde.</li>
          </ul>
          <p>La configuración avanzada (servidores MCP, bases de conocimiento,
          comandos…) no se edita aquí: cada área tiene su propia sub-sección en el
          hub. Pulsa <b>«Guardar»</b> para aplicar los cambios o
          <b>«Cancelar»</b> para descartarlos.</p>
          <p><b>Aviso</b>: el botón «Borrar» de la cabecera es irreversible y exige
          teclear el nombre exacto del proyecto como confirmación — un seguro contra
          borrados accidentales.</p>`,
      },
      {
        title: "App-preview de validación humana (imagen y puerto)",
        goto: `/admin/projects/${PID}`,
        fullPage: false,
        settleMs: 800,
        action: async (page) => {
          await page
            .getByTestId("review-preview-section")
            .scrollIntoViewIfNeeded()
            .catch(() => {});
          await page.waitForTimeout(400);
        },
        body: `<p>La tarjeta <b>«App-preview de validación humana»</b> configura la
          demo en vivo que ve el revisor cuando un plan llega a validación humana:
          la plataforma levanta la app del proyecto en un contenedor de revisión
          para que se pueda <b>probar de verdad</b> antes de aprobar o rechazar el
          plan.</p>
          <ul>
            <li><b>Imagen del app-preview</b>: el tag de una imagen Docker
              auto-servible (su <code>CMD</code> arranca un servidor HTTP; el código
              del plan se monta en <code>/workspace</code>). La imagen la construye
              y publica la CI del propio proyecto — la plataforma solo la
              referencia. En desarrollo vale un tag local; en producción, la
              referencia del registry.</li>
            <li><b>Puerto</b>: el puerto HTTP interno en el que escucha la app
              (vacío = 8080).</li>
          </ul>
          <p>Si dejas la imagen vacía, el <b>app-preview queda desactivado</b>: la
          sesión de validación humana funciona igualmente (checklist + veredicto),
          solo que sin app en vivo que probar.</p>
          <p><b>Buena práctica</b>: configura la imagen desde el primer plan; probar
          la app real reduce drásticamente los rechazos tardíos.</p>`,
      },
      {
        title: "Chat con los agentes del proyecto",
        goto: `/admin/projects/${PID}/chat`,
        fullPage: true,
        body: `<p>La sección <b>Chat</b> es la conversación con el equipo de agentes
          del proyecto y el punto de partida natural de todo el trabajo. Desde aquí
          pides lo que necesitas en lenguaje natural («crea el endpoint y su test»)
          y el sistema lo convierte en <b>planes</b> y <b>tareas</b> ejecutables.</p>
          <ul>
            <li><b>Modos de conversación</b>: la cabecera tiene un selector de modo
              persistente — <i>Planning</i> (diseñar un plan de construcción),
              <i>Discusión</i> (consultar sin generar trabajo), <i>Ejecución</i> y
              <i>Custom</i>. En modo Planning, la conversación termina proponiendo
              un plan con fases, tareas y estimaciones que puedes insertar en el
              proyecto.</li>
            <li><b>@-menciones</b>: puedes dirigirte a un especialista concreto del
              equipo (project_manager, architect, backend_dev, frontend_dev, qa,
              reviewer, devops, security…) escribiendo <code>@</code> en el
              compositor.</li>
            <li><b>Historial</b>: el proyecto conserva sus conversaciones; puedes
              retomar una anterior o empezar una nueva.</li>
          </ul>
          <p>Cada mensaje queda asociado al proyecto y a su contexto: las bases de
          conocimiento concedidas, la memoria del equipo y las herramientas
          autorizadas. Los agentes responden con ese contexto, no en el vacío.</p>`,
      },
      {
        title: "Planes del proyecto",
        goto: `/admin/projects/${PID}/plans`,
        fullPage: true,
        body: `<p>La sección <b>Planes</b> lista los planes de construcción del
          proyecto. Cada plan es la <b>unidad de cambio</b>: agrupa un conjunto
          ordenado de tareas con dependencias, se materializa como una rama git
          <code>plan/&lt;id&gt;-&lt;slug&gt;</code> y, al completarse, genera un PR
          automático. Verás el plan <b>«MVP — API Hello World en PHP»</b> creado
          para este ejemplo.</p>
          <ul>
            <li><b>Filtros por estado</b>: la fila de chips filtra por el ciclo de
              vida completo — Borrador, Pendiente de aprobación, Aprobado, En
              progreso, Bloqueado, Pendiente validación humana, Completado,
              Rechazado, Cancelado y Archivado — con el contador de planes en cada
              estado.</li>
            <li><b>Dos vistas</b>: lista (una tarjeta por plan con título,
              descripción e insignia de estado) o kanban por estado, conmutables
              desde la cabecera.</li>
            <li><b>«Generar desde chat»</b>: acceso directo al chat en modo
              Planning para crear un plan nuevo conversando con el equipo.</li>
          </ul>
          <p>Al hacer clic en un plan accedes a su <b>vista de detalle</b>
          (resumen, fases, tareas, DAG, Gantt, costes, sincronización al Kanban…),
          que se documenta a fondo en el manual de <b>Planes y Kanban</b>.</p>`,
      },
      {
        title: "Tareas del proyecto",
        goto: `/admin/projects/${PID}/tasks`,
        fullPage: true,
        body: `<p>La sección <b>Tasks</b> reúne <b>todas</b> las tareas del proyecto,
          pertenezcan o no a un plan — incluye por tanto las tareas sueltas que se
          crean fuera del flujo de planning. Para el plan de ejemplo verás cuatro
          tareas: definir el endpoint, implementar el controlador PHP, escribir el
          test PHPUnit y documentarlo.</p>
          <ul>
            <li>Cada tarea muestra su <b>título</b>, <b>estado</b> (backlog, ready,
              en curso, revisión, bloqueada, hecha…), <b>prioridad</b> (baja, media,
              alta, crítica) y, si está asignada, el <b>agente responsable</b>.</li>
            <li>Las tareas de un plan llevan además sus <b>criterios de
              aceptación</b>: condiciones concretas y verificables que el trabajo
              debe cumplir para darse por bueno (p. ej. «Devuelve 200»,
              «Content-Type application/json»).</li>
          </ul>
          <p><b>Cuándo usar esta vista</b>: para auditar el trabajo del proyecto en
          conjunto. Para la operativa diaria de un plan concreto es más cómodo el
          <b>Tablero</b> (doble Kanban), que se explica en el manual 02.</p>`,
      },
      {
        title: "Bases de conocimiento del proyecto (RAG)",
        goto: `/admin/projects/${PID}/knowledge-bases`,
        fullPage: true,
        body: `<p>Aquí se gestionan las <b>bases de conocimiento</b> (RAG) concedidas
          al proyecto: la documentación, convenciones, especificaciones y material
          de referencia que los agentes consultan mientras trabajan. Un agente que
          «sabe» las convenciones del proyecto produce código alineado a la primera;
          por eso esta sección importa tanto.</p>
          <ul>
            <li>Las bases se <b>conceden</b> desde el catálogo del tenant o desde
              las built-in de la plataforma: el proyecto no duplica contenido, sino
              que recibe acceso.</li>
            <li>Los documentos se <b>indexan automáticamente</b>: se trocean y se
              generan embeddings (vectores en pgvector) para que la búsqueda
              semántica encuentre pasajes relevantes aunque no coincidan las
              palabras exactas.</li>
            <li>Si creaste el proyecto desde una <b>plantilla</b>, las bases de la
              plantilla pueden haberse concedido automáticamente (la casilla del
              asistente de creación).</li>
          </ul>
          <p><b>Buena práctica</b>: concede solo las bases pertinentes al proyecto.
          Más conocimiento no siempre es mejor — el contexto irrelevante diluye las
          respuestas.</p>`,
      },
      {
        title: "Comandos autorizados y runtime por defecto",
        goto: `/admin/projects/${PID}/commands`,
        fullPage: true,
        body: `<p>La sección <b>Comandos &amp; runtime</b> gobierna qué puede
          ejecutar el equipo de agentes en el stack del proyecto. Tiene dos partes:</p>
          <ul>
            <li><b>Comandos autorizados</b>: la <b>lista blanca</b> de binarios que
              los agentes pueden lanzar vía <code>shell_exec</code>. Es
              <i>deny-by-default</i>: con la lista vacía no se ejecuta nada. Los
              comandos se muestran como <b>chips</b> que puedes quitar uno a uno; el
              campo de texto añade nuevos, y los botones de <b>preset por stack</b>
              (PHP, Node, .NET, Python) rellenan de un golpe los binarios típicos de
              cada stack (p. ej. PHP añade <code>php</code>, <code>composer</code>,
              <code>vendor/bin/phpunit</code> y <code>pest</code>) sin borrar los
              que ya tuvieras.</li>
            <li><b>Runtime por defecto</b>: el <i>runtime template</i> del proyecto —
              aquí <code>php-phpunit</code> —, elegido de un catálogo mantenido por
              la plataforma. Los runtimes son <b>contenedores efímeros y
              aislados</b> (sin red abierta, sin privilegios) donde se ejecutan los
              tests y comandos del stack; los workers de la plataforma solo
              orquestan, nunca ejecutan código del usuario.</li>
          </ul>
          <p>La edición requiere rol de <b>administrador de tenant</b>; el resto de
          miembros ve la configuración en modo lectura.</p>
          <p><b>Buena práctica</b>: autoriza el mínimo imprescindible y amplía bajo
          demanda; cada binario añadido es superficie de ejecución extra.</p>`,
      },
      {
        title: "Caché de dependencias por runtime",
        goto: `/admin/projects/${PID}/dep-cache`,
        fullPage: true,
        body: `<p>La sección <b>Caché de dependencias</b> muestra, por cada runtime
          template usado por el proyecto, la caché de dependencias descargadas
          (paquetes de composer, npm, pip…) que la plataforma conserva entre
          ejecuciones para no re-descargarlo todo en cada tarea.</p>
          <ul>
            <li>La tabla lista los <b>runtimes con caché</b> y permite
              <b>invalidarla</b> por runtime: la próxima ejecución partirá de cero y
              volverá a resolver las dependencias.</li>
            <li>Invalidar es útil cuando una dependencia corrupta o desactualizada
              provoca fallos raros en los tests: es el equivalente a «borrar
              node_modules y reinstalar».</li>
          </ul>
          <p>En un proyecto recién creado esta lista estará vacía: la caché se va
          poblando a medida que los agentes ejecutan instalaciones de dependencias
          en los runtimes.</p>`,
      },
      {
        title: "Servidores MCP del proyecto",
        goto: `/admin/projects/${PID}/mcp-servers`,
        fullPage: true,
        body: `<p>La sección <b>MCP servers</b> conecta el proyecto con servidores
          <b>Model Context Protocol</b>: integraciones externas (repositorios,
          bases de datos, servicios de terceros, buscadores…) que exponen
          herramientas invocables por los agentes de forma gobernada.</p>
          <ul>
            <li>Cada servidor MCP <b>declara sus herramientas</b>; una vez
              registrado, esas herramientas entran en el catálogo y se asignan
              <b>por agente</b> — un agente solo puede usar las herramientas que
              tiene concedidas.</li>
            <li>Las credenciales de los servidores no se guardan en claro: la
              plataforma usa su gestor de secretos (Vault) como única vía de
              credenciales.</li>
            <li>La vista <b>Tools por agente</b> (más abajo) permite auditar el
              resultado final de estas asignaciones.</li>
          </ul>
          <p><b>Caso de uso</b>: conectar el MCP de tu gestor de incidencias para
          que el equipo de agentes pueda leer tickets y enlazarlos con las tareas
          del plan.</p>`,
      },
      {
        title: "Webhooks entrantes del proyecto",
        goto: `/admin/projects/${PID}/incoming-webhooks`,
        fullPage: true,
        body: `<p>La sección <b>Webhooks entrantes</b> permite que herramientas
          externas (GitHub, Jira, Sentry…) <b>disparen acciones</b> en el proyecto
          enviando eventos a una URL dedicada. Es la vía para reaccionar
          automáticamente a un push, a un ticket nuevo o a una alerta de errores.</p>
          <ul>
            <li>Cada webhook se configura con un <b>origen</b>, un <b>nombre</b> y
              una o varias <b>reglas evento → acción</b> (qué evento externo dispara
              qué acción en la plataforma).</li>
            <li>Al crearlo se genera un <b>secreto HMAC</b> que se muestra <i>una
              sola vez</i> (banner con botón de copiar): configúralo en la
              herramienta externa. Toda entrega entrante se <b>verifica por
              firma</b> antes de actuar — las peticiones sin firma válida se
              descartan.</li>
            <li>Cada tarjeta de webhook muestra su URL completa, su estado
              (activado/desactivado) y acciones de <b>editar</b>, <b>rotar el
              secreto</b> y <b>borrar</b>; un desplegable lista las <b>entregas
              recientes</b> para depurar integraciones.</li>
          </ul>
          <p><b>Aviso</b>: si rotas el secreto, actualízalo también en la
          herramienta externa o sus entregas empezarán a rechazarse.</p>`,
      },
      {
        title: "Memoria del proyecto",
        goto: `/admin/projects/${PID}/memories`,
        fullPage: true,
        body: `<p>La <b>Memoria</b> del proyecto es lo que el equipo «recuerda» en el
          ámbito <b>project_shared</b>: decisiones tomadas, hechos aprendidos,
          convenciones acordadas y lecciones de ejecuciones anteriores, que
          persisten entre ejecuciones y alimentan el contexto de los agentes.</p>
          <ul>
            <li>La plataforma distingue <b>cuatro ámbitos de memoria</b> y nunca los
              mezcla: <i>private</i> (personal del usuario humano — un agente de IA
              ni la lee ni la escribe), <i>team_shared</i> (del equipo),
              <i>project_shared</i> (de este proyecto — lo que ves aquí) y
              <i>global</i> (de la organización).</li>
            <li>Las memorias se crean tanto automáticamente (los agentes registran
              aprendizajes relevantes) como manualmente desde esta pantalla.</li>
            <li>Puedes revisar, editar o eliminar entradas: la memoria es
              gobernable, no una caja negra.</li>
          </ul>
          <p><b>Buena práctica</b>: purga periódicamente las memorias obsoletas
          (decisiones revertidas, datos caducos). Una memoria limpia produce agentes
          más precisos.</p>`,
      },
      {
        title: "Diagnóstico de herramientas por agente",
        goto: `/admin/projects/${PID}/agent-tools-diagnostic`,
        fullPage: true,
        body: `<p>Esta vista de <b>solo lectura</b> muestra, para cada agente del
          proyecto, qué <b>herramientas</b> tiene efectivamente asignadas y con qué
          variante (p. ej. sandboxed). Es el resultado final de combinar el catálogo
          del tenant, los servidores MCP del proyecto y las asignaciones por
          agente.</p>
          <ul>
            <li><b>Para qué sirve</b>: auditar de un vistazo qué puede hacer cada
              agente <i>antes</i> de lanzar una ejecución — si el QA no tiene la
              herramienta de ejecutar tests, mejor descubrirlo aquí que a mitad de
              un plan.</li>
            <li>Al ser diagnóstico, <b>no se edita nada</b> desde esta pantalla: las
              asignaciones se cambian en el catálogo de herramientas y en la ficha
              de cada agente.</li>
          </ul>
          <p><b>Caso de uso</b>: un plan se bloquea porque el agente no puede
          escribir ficheros → entra aquí, comprueba qué variante de herramienta de
          escritura tiene concedida y corrige la asignación.</p>`,
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
    "<p>Este manual explica cómo trabajar con <b>Proyectos</b> en el panel de administración de la plataforma agéntica. Un proyecto es la unidad de trabajo donde viven los planes, tareas, conversaciones con agentes, comandos autorizados, servidores MCP, bases de conocimiento, memorias del equipo, webhooks entrantes y la configuración de git y de validación humana.</p><p>Aprenderás a localizar y consultar la lista de proyectos de tu tenant, a crear un proyecto nuevo paso a paso mediante el asistente (partiendo de una plantilla o en blanco), y recorrerás un proyecto real de ejemplo (<b>«Hello World PHP»</b>) sección por sección: el hub, la edición, el app-preview de validación, el chat con los agentes, los planes, las tareas, el conocimiento RAG, los comandos y runtimes, la caché de dependencias, los servidores MCP, los webhooks, la memoria y el diagnóstico de herramientas.</p>",
  steps: [
    {
      title: "Lista de proyectos",
      goto: "/admin/projects",
      fullPage: true,
      body: "<p>Esta es la pantalla principal de <b>Proyectos</b> (grupo <i>Recursos</i> de la navegación lateral). Muestra los proyectos activos de tu tenant en una cuadrícula de tarjetas; las plantillas no aparecen aquí — se eligen al crear un proyecto nuevo.</p><p>Cada tarjeta muestra el <b>nombre</b> del proyecto, una <b>insignia de estado</b> (<code>active</code> en verde, <code>paused</code> en ámbar, <code>archived</code> en gris) y la <b>descripción</b>. Haz clic en cualquier tarjeta para abrir el <b>hub</b> de ese proyecto, la ficha desde la que se accede a todas sus secciones.</p><p>En la esquina superior derecha, los usuarios con rol <b>administrador de tenant</b> o superior ven el botón <b>«Crear proyecto»</b>, que lanza el asistente de creación en dos pasos.</p><p><b>Buena práctica</b>: usa el estado para mantener la lista útil — pausa los proyectos aparcados y archiva los terminados; así la operativa diaria se concentra en lo activo.</p>",
    },
    {
      title: "Asistente de creación — Paso 1: elegir plantilla o empezar en blanco",
      goto: "/admin/projects/new",
      fullPage: true,
      body: "<p>Al pulsar «Crear proyecto» entras en el <b>asistente de dos pasos</b>. El paso 1 decide el punto de partida:</p><ul><li><b>«Proyecto en blanco»</b> (tarjeta superior): empieza sin plantilla. No se concede ninguna base de conocimiento por defecto y el equipo lo eliges tú en el paso 2. Pulsa <b>«Empezar en blanco»</b> para continuar.</li><li><b>Plantillas</b> (cuadrícula inferior): proyectos preconfigurados que traen equipo de agentes, política de validación humana, configuración de repositorio y bases de conocimiento por defecto. Cada tarjeta muestra el nombre, el <b>equipo</b> asociado (insignia azul) y la descripción. Pulsa <b>«Usar plantilla»</b> para seleccionarla — el nombre y la descripción del proyecto se precargan a partir de la plantilla.</li></ul><p><b>Cuándo usar cada opción</b>: las plantillas son la vía rápida para stacks conocidos (traen el equipo y las convenciones listas); el proyecto en blanco es para casos a medida donde prefieres montar cada pieza tú mismo.</p>",
    },
    {
      title: "Asistente de creación — Paso 2: personalizar y crear",
      goto: "/admin/projects/new",
      fullPage: true,
      body: "<p>El paso 2 ajusta los detalles antes de crear. El panel <b>«Detalles del proyecto»</b> contiene:</p><ul><li><b>Nombre</b> (obligatorio) y <b>Descripción</b> (con soporte Markdown y previsualización).</li><li><b>Runtime por defecto</b>: un desplegable con el catálogo real de runtime templates del stack (para este ejemplo elegiríamos <code>PHP · PHPUnit</code>). Puede dejarse vacío — cada herramienta usará entonces su runtime por defecto.</li><li>Si empezaste <b>en blanco</b>: un selector de <b>Equipo</b> (el equipo gobierna qué agentes ejecutan las tareas y la política de memoria; puedes asignarlo después desde la ficha).</li><li>Si elegiste <b>plantilla</b>: la casilla <b>«Conceder las bases de conocimiento de la plantilla»</b> (activada por defecto; si la desmarcas, el proyecto adopta la plantilla pero sin KBs).</li></ul><p>El panel <b>«Preview»</b> de la derecha resume lo que llevará el proyecto: la plantilla elegida (o «en blanco»), el <b>equipo</b>, la casilla <b>«Personalizar el equipo para este proyecto»</b> (crea una copia editable del equipo en lugar de referenciar el compartido), la <b>política de validación humana</b> heredada y la configuración de <b>repositorio</b>.</p><p>El botón <b>«Crear proyecto»</b> se habilita cuando hay nombre; al crearlo, vuelves a la lista con el proyecto nuevo disponible. El botón <b>«← Cambiar plantilla / Volver»</b> regresa al paso 1 sin perder lo tecleado.</p>",
    },
    ...hubSteps,
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  test.setTimeout(480_000);
  await login(page);
  await generateManual(page, manual);
});
