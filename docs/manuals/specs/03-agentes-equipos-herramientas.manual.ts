import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef } from "../lib/manual";
import { seededPhpProjectId } from "../lib/seed-helper";

// Proyecto demo sembrado (lib/seed-demo-data.mjs): si existe, el paso de MCP
// servers captura la pestaña real del proyecto; si no, cae al catálogo de
// tools (pantalla garantizada) y el body explica el resto.
const PID = seededPhpProjectId();

// GENERADO desde el workflow de redacción. Editable a mano; reejecutable.
//
// NOTA sobre las capturas: varios pasos documentan la FICHA DE DETALLE de un
// agente o de un equipo. Como esas rutas llevan un id dinámico, en vez de un
// `goto` directo se navega desde el listado con una `action` (clic en la
// primera tarjeta). Los pasos que documentan una sección concreta de una
// página larga usan `fullPage: false` + scroll a la sección, para que cada
// figura muestre la parte de la pantalla de la que habla el texto.
const manual: ManualDef = {
  order: "03",
  slug: "03-agentes-equipos-herramientas",
  title: "Agentes, equipos y herramientas",
  audience: "Administradores de tenant y operadores de la plataforma",
  intro:
    "<p>Este manual explica cómo gestionar el <b>capital agéntico</b> de tu tenant: los agentes de IA que ejecutan las tareas, los agentes humanos que representan a personas asignables, los equipos que agrupan agentes para trabajar de forma cooperativa, y el catálogo de herramientas (tools) que define qué puede hacer cada agente.</p><p>Recorreremos en profundidad cinco áreas del panel de administración: el catálogo de agentes IA (<code>/admin/agents</code>) y la ficha de detalle de cada agente (con su Hub de Capacidad, su persona, sus knowledge bases, sus tools y sus skills), los agentes humanos (<code>/admin/human-agents</code>), los equipos (<code>/admin/teams</code>) con su detalle y la adopción de equipos built-in, y el catálogo de tools (<code>/admin/tools</code>). En cada pantalla verás qué información se muestra, para qué sirve cada control y qué acciones de alta y edición están disponibles según tu rol.</p><p>El modelo mental que vertebra toda la sección de agentes es el de las <b>cuatro vías de capacidad</b>: <b>SABER</b> (qué conocimiento tiene asignado: knowledge bases), <b>RECORDAR</b> (qué memoria persiste entre ejecuciones y con qué alcance), <b>SER</b> (su persona: proveedor LLM, modelo, temperatura y system prompt) y <b>HACER</b> (qué tools puede invocar). Cada una se configura en su sección de la ficha del agente y el Hub de Capacidad las resume con su estado real.</p><p>La mayoría de acciones de creación y edición requieren rol <b>administrador del tenant</b>; los usuarios sin ese rol pueden explorar y consultar, pero verán ocultos los botones de gestión. Los elementos <b>built-in</b> (mantenidos por la plataforma) son siempre de solo lectura: la vía para personalizarlos es crear una copia (fork del agente, adopción del equipo).</p><p>Cerramos el manual con tres pasos dedicados a las <b>tres vías para ampliar lo que un agente puede HACER</b>: los tipos de implementación de las tools personalizadas (con un ejemplo real en Python), las skills (fragmentos de prompt que crean hábitos compartibles) y los servidores MCP de un proyecto (integraciones externas cuyo flujo completo — probar, importar, asignar, usar — está verificado de punta a punta). La regla mnemotécnica que los ordena: <b>MCP conecta, la tool ejecuta, la skill instruye</b>.</p>",
  steps: [
    {
      title: "Catálogo de agentes IA (built-in)",
      goto: "/admin/agents",
      body: "<p>Esta es la pantalla principal de agentes de IA, accesible desde <b>Inicio > Agentes</b>. Un <b>agente</b> es una entidad con un <b>rol</b> (project_manager, architect, backend_dev, frontend_dev, qa, reviewer, leader, worker, specialist, researcher, devops, security o technical_writer) y una persona configurada: proveedor LLM, modelo y temperatura más su system prompt bilingüe (ES/EN).</p><p>El catálogo se organiza en tres pestañas según el ámbito (scope) del agente: <b>Built-in</b> (agentes mantenidos por la plataforma, que ves seleccionada en esta captura), <b>Plantillas del Tenant</b> (plantillas reutilizables propias de tu organización) y <b>Locales del Proyecto</b> (agentes específicos de un proyecto, normalmente forkados de un built-in o plantilla). Cada pestaña muestra entre paréntesis cuántos agentes contiene.</p><p>Encima de las pestañas hay dos filtros combinables: <b>Pertenencia</b> (Todos / En equipo / Sin equipo) y <b>Equipo</b> (lista con los equipos del tenant), útiles para responder preguntas como «¿qué agentes forman parte del equipo X?» o «¿qué agentes están sueltos, sin equipo?».</p><p>Cada agente aparece como una tarjeta con su nombre, una insignia de ámbito, su rol, su descripción, un fragmento del system prompt en el idioma activo de la interfaz y las insignias de los <b>equipos</b> a los que pertenece (o la marca «Sin equipo»). Si el agente fue forkado de otro, se indica con la nota «Forked from another agent». Al pulsar una tarjeta accedes a su ficha de detalle, que cubrimos unos pasos más adelante.</p><p>Si tienes rol de administrador del tenant verás el botón <b>Nuevo agente</b> en la esquina superior derecha, que abre el formulario de alta (lo cubrimos en el paso 3).</p>",
      action: async (page) => {
        await page
          .getByRole("tab", { name: "Built-in" })
          .click()
          .catch(async () => {
            await page
              .getByRole("button", { name: "Built-in" })
              .click()
              .catch(async () => {
                await page
                  .getByText("Built-in", { exact: false })
                  .first()
                  .click()
                  .catch(() => {});
              });
          });
        await page.waitForTimeout(500);
      },
      fullPage: true,
    },
    {
      title: "Plantillas del tenant y agentes locales de proyecto",
      goto: "/admin/agents",
      body: "<p>Las otras dos pestañas del catálogo separan los agentes que crea tu organización. En <b>Plantillas del Tenant</b> (la que mostramos en esta captura) viven los agentes reutilizables en todos los proyectos del tenant: plantillas que defines una vez y reaprovechas al planificar. Es el ámbito adecuado para «vuestro backend dev estándar», «vuestro revisor de seguridad», etc.</p><p>La pestaña <b>Locales del Proyecto</b> agrupa los agentes específicos de un proyecto concreto, normalmente forkados de un built-in o de una plantilla del tenant para ajustar su persona (prompt, modelo, temperatura) sin tocar el original. Cada tarjeta forkada lo señala con la nota «Forked from another agent».</p><p>La distinción de ámbitos tiene tres consecuencias prácticas:</p><ul><li>Los <b>built-in</b> los mantiene la plataforma y son de solo lectura para el tenant: puedes usarlos y forkarlos, pero no editarlos ni borrarlos.</li><li>Las <b>plantillas del tenant</b> son editables por un administrador del tenant y visibles en todos los proyectos.</li><li>Los <b>locales del proyecto</b> solo se ofrecen dentro de su proyecto; son la vía para desviaciones puntuales (por ejemplo, un backend_dev con prompt específico del stack del proyecto).</li></ul><p>Si una pestaña aparece vacía, la propia tarjeta te indica el motivo: el tenant todavía no ha creado plantillas propias, o no hay agentes locales porque aún no se ha forkado ninguno. Cambia entre pestañas con un clic en su título; el número entre paréntesis te anticipa cuántos agentes encontrarás en cada ámbito.</p>",
      action: async (page) => {
        await page
          .getByRole("tab", { name: "Plantillas del Tenant" })
          .click()
          .catch(async () => {
            await page
              .getByRole("button", { name: "Plantillas del Tenant" })
              .click()
              .catch(async () => {
                await page
                  .getByText("Plantillas del Tenant", { exact: false })
                  .first()
                  .click()
                  .catch(() => {});
              });
          });
        await page.waitForTimeout(500);
      },
      fullPage: true,
    },
    {
      title: "Crear un nuevo agente IA",
      goto: "/admin/agents",
      body: "<p>Pulsa <b>Nuevo agente</b> para abrir el diálogo de alta (es el que ves abierto en esta captura). Permite crear una <b>plantilla del tenant</b> (reutilizable en todos los proyectos) o un <b>agente local</b> de un proyecto concreto.</p><p>Rellena el <b>Nombre</b> y elige el <b>Role</b> de la lista cerrada de roles disponibles. Añade una <b>Descripción</b> (admite formato Markdown, con previsualización) y el <b>System prompt (ES)</b>, que es obligatorio y constituye la fuente del prompt en español. El prompt es la pieza más importante del agente: define su misión, su forma de trabajar y sus límites; conviene redactarlo en segunda persona y con instrucciones concretas y verificables.</p><p>En el bloque <b>Persona (modelo)</b> configuras la pata SER del agente: el <b>Proveedor</b> (solo se ofrecen los cuatro del catálogo cerrado: Claude (suscripción), GitHub Copilot, Azure AI Foundry y Ollama (local)), el <b>Modelo</b> (texto libre, p. ej. claude-sonnet-4) y la <b>Temperatura</b> (valor entre 0 y 2; valores bajos dan respuestas más deterministas, valores altos más creativas). Si algún valor es inválido se muestra el error bajo el campo. Opcionalmente puedes añadir el <b>System prompt (EN)</b> para la versión en inglés: el sistema usará el prompt del idioma activo y caerá al otro si falta.</p><p>En <b>Scope</b> eliges entre <b>Plantilla del tenant</b> o <b>Local de un proyecto</b>; si eliges local, aparece el selector <b>Proyecto</b> para escoger entre tus proyectos del tenant (escribe para buscar). El botón <b>Crear</b> solo se habilita cuando el nombre, el system prompt ES y la persona son válidos (y hay proyecto si es local).</p><p><b>Buena práctica</b>: si el agente que necesitas se parece a un built-in, no lo crees de cero — abre el built-in y usa «Personalizar (crear copia)», que hereda además su conocimiento, tools y skills (lo vemos en el paso 8).</p>",
      action: async (page) => {
        await page
          .getByRole("button", { name: "Nuevo agente" })
          .click()
          .catch(() => {});
        await page.waitForTimeout(500);
      },
      fullPage: true,
    },
    {
      title: "Ficha de detalle de un agente",
      goto: "/admin/agents",
      body: "<p>Al pulsar la tarjeta de un agente se abre su <b>ficha de detalle</b>, la vista que ves en esta captura (aquí, la del primer agente built-in del catálogo). La cabecera muestra el nombre y la descripción, y a la derecha las acciones disponibles: <b>Personalizar (crear copia)</b> siempre, y <b>Editar</b> / <b>Borrar</b> solo si el agente NO es built-in (los built-in muestran en su lugar la insignia «read-only (built-in)»).</p><p>La primera tarjeta resume la identidad del agente mediante insignias:</p><ul><li>El <b>scope</b> (global_builtin, global_tenant_template o project_local) y el <b>rol</b>.</li><li>El <b>tipo</b> de agente (ai o human).</li><li><b>puede revisar</b>: si tiene capacidad de actuar como revisor de tareas de otros agentes.</li><li><b>plantilla</b>: si es una plantilla reutilizable.</li><li><b>Forked</b> / <b>Linked</b>: «Forked» significa que nació como copia de otro agente; «Linked» que es un original (o copia de catálogo sin origen).</li></ul><p>Debajo se muestra el <b>System prompt</b> completo en el idioma activo (la misma fuente que lee la tarjeta del catálogo) y tres campos: el <b>Memory scope</b> (con qué alcance memoriza: privada, equipo, proyecto o global), el <b>máximo de tareas concurrentes</b> que acepta y el <b>proyecto</b> al que pertenece si es local.</p><p>Atención al aviso de honestidad: un agente IA con memory_scope <b>privada</b> NO memoriza entre ejecuciones (el componente Memorizer omite ese scope, reservado a personas). La ficha lo avisa en un recuadro amarillo en vez de dejarte creer que el agente recordará.</p><p>Si el agente pertenece a uno o más equipos, su scope de memoria lo gobierna el equipo y el control individual aparece deshabilitado en el diálogo de edición, indicando qué equipo lo gestiona.</p>",
      action: async (page) => {
        await page.locator('[data-testid^="agent-link-"]').first().click({ timeout: 10_000 });
        await page.waitForTimeout(1000);
      },
      fullPage: true,
    },
    {
      title: "Hub de Capacidad: SABER / RECORDAR / SER / HACER",
      goto: "/admin/agents",
      body: "<p>Dentro de la ficha del agente, la tarjeta <b>Hub de Capacidad</b> (la que enfoca esta captura) resume en una sola vista <b>con qué cuenta realmente el agente</b>, organizada en las cuatro vías de capacidad:</p><ul><li><b>SABER</b> — qué knowledge bases tiene asignadas, cada una con su nivel de procedencia (Rol, Stack, Equipo o Plataforma).</li><li><b>RECORDAR</b> — cuántas memorias existen en cada scope al que el agente tiene acceso.</li><li><b>SER</b> — la persona efectiva: proveedor, modelo, temperatura y el <b>origen del modelo</b>, es decir, qué nivel de la cadena de herencia lo fija (Agente propio → Equipo → Proyecto → Plataforma default).</li><li><b>HACER</b> — el <b>set efectivo de tools</b> que el agente puede invocar, calculado por el backend combinando las asignaciones; cada tool lleva un tooltip con su función y <code>shell_exec</code> se destaca en amarillo por ser privilegiada.</li></ul><p>Cada sección lleva una insignia de <b>estado honesto</b>: «3 KBs asignadas», «sin memoria de proyecto», «modelo no configurado»… Nada aparenta estar activo si no lo está. Esto es clave para diagnosticar por qué un agente no rinde: si SER dice «modelo no configurado» o HACER está vacío, el agente no puede trabajar como esperas.</p><p>Arriba del todo, el bloque <b>Pasos para capacitar</b> es una checklist de onboarding con el orden recomendado: primero la Persona (SER), luego el conocimiento (SABER), después las tools (HACER) y por último la memoria (RECORDAR). Los pasos completados aparecen tachados con su marca verde.</p><p>El Hub es de <b>solo lectura</b>: es una vista del set efectivo. Cada sección indica con su verbo («Asignar», «Editar»…) desde qué sección de la ficha se modifica esa capacidad — son las secciones que recorremos en los pasos siguientes.</p>",
      action: async (page) => {
        await page.locator('[data-testid^="agent-link-"]').first().click({ timeout: 10_000 });
        await page.waitForTimeout(1000);
        await page
          .getByTestId("capability-hub")
          .scrollIntoViewIfNeeded()
          .catch(() => {});
        await page.waitForTimeout(400);
      },
      fullPage: false,
    },
    {
      title: "Persona del agente (SER): modelo y prompt efectivo",
      goto: "/admin/agents",
      body: "<p>La sección <b>SER · Persona</b> de la ficha (enfocada en esta captura) responde a la pregunta «¿quién es este agente?»: muestra el <b>Proveedor</b>, el <b>Modelo</b> y la <b>Temperatura</b> configurados. Si falta el proveedor o el modelo, el resumen lo dice con claridad («No configurado») en lugar de mostrar un valor engañoso.</p><p>Los proveedores posibles forman un <b>catálogo cerrado</b> de cuatro: Claude (suscripción), GitHub Copilot, Azure AI Foundry y Ollama. La validación se aplica tanto en el formulario como en el servidor, de modo que no es posible guardar una persona fuera de catálogo.</p><p>La parte más útil de la sección es la vista del <b>prompt efectivo</b>: el system prompt que el agente recibe realmente en ejecución no es solo el texto del rol, sino la combinación del <b>prompt del rol</b> con el <b>prompt del modo de chat</b> elegido. El selector de modo te permite previsualizar esa combinación para cada modo disponible; el modo <i>custom</i> se marca «No disponible aún» cuando no hay prompt de modo que mostrar.</p><p>Esta sección es de <b>solo lectura</b>: para cambiar la persona usa el botón <b>Editar</b> de la cabecera de la ficha (en agentes no built-in), que abre el diálogo con los mismos controles de proveedor/modelo/temperatura y la edición bilingüe ES/EN del prompt.</p><p><b>Herencia de modelo</b>: si el agente no fija modelo propio, hereda el del equipo al que pertenece; si el equipo tampoco, el del proyecto; y en última instancia el default de la plataforma. El Hub de Capacidad (paso anterior) te muestra qué nivel está fijando el modelo efectivo en cada momento.</p>",
      action: async (page) => {
        await page.locator('[data-testid^="agent-link-"]').first().click({ timeout: 10_000 });
        await page.waitForTimeout(1000);
        await page
          .getByTestId("persona-section")
          .scrollIntoViewIfNeeded()
          .catch(() => {});
        await page.waitForTimeout(400);
      },
      fullPage: false,
    },
    {
      title: "Knowledge Bases del agente (SABER)",
      goto: "/admin/agents",
      body: "<p>La sección <b>Knowledge Bases</b> de la ficha (enfocada en esta captura) lista las KBs concedidas (granted) directamente a este agente. Es la pata SABER de su capacidad: los documentos indexados en esas KBs quedan disponibles para que el agente recupere contexto vía RAG durante sus ejecuciones.</p><p>Cada fila muestra el nombre y la descripción de la KB. Con rol administrador del tenant (y en agentes no built-in) dispones de:</p><ul><li><b>Grant KB</b> (arriba a la derecha de la sección): abre un diálogo con un buscador de KBs del tenant para conceder una nueva.</li><li>El botón de <b>papelera</b> por fila: revoca el acceso del agente a esa KB.</li></ul><p>Si la lista está vacía no significa que el agente trabaje a ciegas: las <b>KBs del proyecto</b> siguen siendo visibles para él en ejecución. Las KBs por-agente se usan cuando el <b>rol</b> necesita documentación agnóstica del stack — por ejemplo, conceder al arquitecto una KB de principios de diseño REST, o al revisor una KB con la guía de estilo de la organización.</p><p><b>Buena práctica</b>: mantén las KBs de conocimiento de dominio del producto a nivel de proyecto (grant al proyecto desde la pantalla de Knowledge Bases) y reserva los grants por-agente para conocimiento propio del rol, reutilizable entre proyectos.</p>",
      action: async (page) => {
        await page.locator('[data-testid^="agent-link-"]').first().click({ timeout: 10_000 });
        await page.waitForTimeout(1000);
        await page
          .getByTestId("agent-kbs-section")
          .scrollIntoViewIfNeeded()
          .catch(() => {});
        await page.waitForTimeout(400);
      },
      fullPage: false,
    },
    {
      title: "Tools y skills del agente (HACER)",
      goto: "/admin/agents",
      body: "<p>La sección <b>Tools del agente</b> (enfocada en esta captura) define qué herramientas puede invocar el agente en ejecución: la pata HACER de su capacidad. Se organiza en dos pestañas:</p><ul><li><b>Básicas</b> — las tools de plataforma (built-in): archivos, git, ejecución/tests, red, conocimiento, notificaciones, comandos…</li><li><b>Avanzadas</b> — las tools personalizadas del tenant y las de servidores MCP.</li></ul><p>Dentro de cada pestaña las tools se agrupan por <b>categoría funcional</b> con icono y casilla de «seleccionar todo» por grupo, y hay un <b>buscador</b> por nombre o descripción. Cada fila muestra su insignia de <b>seguridad</b> (Segura, Aislada o Privilegiada) con un tooltip en lenguaje llano y su insignia de <b>origen</b> (nativa, HTTP, Python, MCP, contenedor). La selección se guarda como un conjunto completo con el botón <b>Guardar</b>; el botón <b>Restablecer</b> descarta los cambios sin guardar. Una lista vacía significa «sin restricción por agente»: el agente ve el set que le corresponda por defecto.</p><p>Junto al título hay un enlace de <b>diagnóstico</b> (en agentes locales de proyecto) que abre una verificación de solo lectura de qué tools ve efectivamente cada agente del proyecto — útil para comprobar el resultado justo después de asignar.</p><p>Más abajo, la sección <b>Skills del agente</b> funciona con la misma mecánica de casillas: cada skill pertenece a una categoría (Backend, Frontend, DevOps, QA/Testing, Investigación, Documentación) e inyecta su <b>fragmento de prompt</b> en el system prompt efectivo del agente en ejecución. Es la vía para añadir competencias transversales («escribe tests primero», «documenta cada endpoint») sin tocar el prompt base del rol.</p><p>Ambas secciones son de solo lectura en agentes built-in y para usuarios sin rol de administrador del tenant. <b>Aviso</b>: las tools privilegiadas (como <code>shell_exec</code>) amplían mucho lo que el agente puede hacer; asígnalas solo a agentes que las necesiten y apóyate en los guardrails y en la validación humana para las acciones sensibles.</p>",
      action: async (page) => {
        await page.locator('[data-testid^="agent-link-"]').first().click({ timeout: 10_000 });
        await page.waitForTimeout(1000);
        await page
          .getByTestId("agent-tools-section")
          .scrollIntoViewIfNeeded()
          .catch(() => {});
        await page.waitForTimeout(400);
      },
      fullPage: false,
    },
    {
      title: "Personalizar un agente (crear copia / fork)",
      goto: "/admin/agents",
      body: "<p>El botón <b>Personalizar (crear copia)</b> de la ficha del agente abre el diálogo que ves en esta captura. Es la vía para adaptar un agente que no puedes (built-in) o no quieres tocar: crea una <b>copia editable</b> en uno de tus proyectos, dejando el original intacto.</p><p>El diálogo pide dos datos:</p><ul><li><b>Nombre de la copia</b> — precargado con «(copia)» al final; cámbialo por algo descriptivo del ajuste que vas a hacer.</li><li><b>Proyecto destino</b> — obligatorio: la copia siempre aterriza como agente <b>local de un proyecto</b> concreto. Si no tienes proyectos, el diálogo te avisa de que debes crear uno primero.</li></ul><p>La copia <b>hereda automáticamente</b> el conocimiento (KBs), las tools y las skills del original, además de su persona (modelo y prompt). A partir de ahí es completamente independiente: editarla no afecta al agente de origen, y su ficha mostrará la insignia <b>Forked</b> para dejar constancia de su procedencia.</p><p>Al confirmar con <b>Crear copia</b>, la interfaz te lleva directamente a la ficha del agente nuevo, ya editable, donde puedes ajustar el prompt, cambiar el modelo o afinar sus tools.</p><p><b>Caso de uso típico</b>: el backend_dev built-in funciona bien en general, pero tu proyecto usa un framework con convenciones propias. Forkéalo al proyecto, añade esas convenciones a su prompt y asígnale la KB de la documentación del framework.</p>",
      action: async (page) => {
        await page.locator('[data-testid^="agent-link-"]').first().click({ timeout: 10_000 });
        await page.waitForTimeout(1000);
        await page.getByTestId("agent-fork-button").click({ timeout: 10_000 });
        await page.waitForTimeout(500);
      },
      fullPage: true,
    },
    {
      title: "Agentes humanos",
      goto: "/admin/human-agents",
      body: "<p>Un <b>agente humano</b> representa a una persona (o a un rol) que puede asignarse a tareas del plan igual que un agente de IA: el planificador puede encargarle tareas, y el sistema le notifica, espera su aceptación y escala si no responde a tiempo. Esta pantalla se organiza en dos pestañas: <b>Mis agentes humanos</b> (los de tu tenant, que ves seleccionada en esta captura) y <b>Plantillas globales</b> (plantillas sembradas por la plataforma que puedes clonar). Cada pestaña indica el número de elementos entre paréntesis.</p><p>Cada tarjeta de agente humano muestra el nombre, el rol, una insignia «humano» y, si procede, la marca «forkado». Debajo se ve el <b>usuario asignado</b> (o «Sin asignar» en aviso si no hay nadie), la <b>tarifa por hora</b> con su moneda, el <b>timeout de aceptación</b> en horas, a quién <b>escala</b> si no acepta a tiempo y los <b>canales de notificación</b> configurados.</p><p>El dato de tarifa no es decorativo: el sistema lo usa para estimar el <b>coste</b> de los planes que incluyen tareas humanas, junto con las horas de respuesta y ejecución esperadas que se configuran en el formulario (siguiente paso).</p><p>Si eres administrador del tenant, cada tarjeta propia incluye un botón <b>Editar</b>, y en la pestaña <b>Plantillas globales</b> aparece <b>Clonar y forkar</b> para crear una copia editable en tu tenant. El botón <b>Nuevo agente humano</b> (arriba a la derecha) abre el formulario de alta, que cubrimos en el paso siguiente.</p><p><b>Aviso</b>: un agente humano sin usuario asignado no puede recibir tareas de forma efectiva — la tarjeta lo destaca para que completes la asignación.</p>",
      action: async (page) => {
        await page
          .getByRole("tab", { name: "Mis agentes humanos" })
          .click()
          .catch(async () => {
            await page
              .getByRole("button", { name: "Mis agentes humanos" })
              .click()
              .catch(async () => {
                await page
                  .getByText("Mis agentes humanos", { exact: false })
                  .first()
                  .click()
                  .catch(() => {});
              });
          });
        await page.waitForTimeout(500);
      },
      fullPage: true,
    },
    {
      title: "Crear o editar un agente humano",
      goto: "/admin/human-agents",
      body: "<p>El diálogo <b>Nuevo agente humano</b> (o <b>Editar agente humano</b>) define a una persona asignable a tareas; en esta captura lo abrimos con el botón <b>Nuevo agente humano</b>. Empieza por la identidad: <b>Nombre</b> (obligatorio), <b>Rol</b> (reviewer, security, devops, frontend_dev, backend_dev, architect, technical_writer, specialist o custom) y <b>Descripción</b> opcional con Markdown.</p><p>En el bloque <b>Asignación</b> eliges el <b>Usuario asignado</b> entre los miembros del tenant, el usuario de <b>Escalación</b> al que se deriva la tarea si no se acepta a tiempo, y el <b>Timeout de aceptación</b> en horas (por defecto 24, entre 1 y 720). Este circuito de aceptación-escalación evita que un plan se quede parado indefinidamente esperando a una persona: pasado el timeout, la tarea se ofrece al escalado.</p><p>En <b>Coste</b> defines la <b>Tarifa por hora</b> y su <b>Moneda</b> (EUR, USD o GBP). En <b>Canales de notificación</b> marcas con qué medios se avisa a la persona: email, in_app (la campana del panel) y/o assistant. En <b>Estimaciones (planning)</b> indicas la <b>Respuesta esperada</b> y la <b>Ejecución esperada</b> en horas, que el planificador usa para calcular duraciones y costes de los planes con tareas humanas.</p><p>El botón <b>Crear</b> / <b>Guardar cambios</b> se habilita cuando hay nombre. Si ocurre un error al guardar se muestra un mensaje en rojo dentro del diálogo.</p><p><b>Buena práctica</b>: define siempre un usuario de escalación distinto del asignado, y ajusta el timeout a la realidad de tu equipo (24 h es razonable para revisiones; para aprobaciones urgentes conviene bajarlo).</p>",
      action: async (page) => {
        await page
          .getByRole("button", { name: "Nuevo agente humano" })
          .click()
          .catch(() => {});
        await page.waitForTimeout(500);
      },
      fullPage: true,
    },
    {
      title: "Plantillas globales de agentes humanos",
      goto: "/admin/human-agents",
      body: "<p>La pestaña <b>Plantillas globales</b> (seleccionada en esta captura) contiene los agentes humanos «de catálogo» que siembra la plataforma: definiciones tipo de roles habituales (revisor, responsable de seguridad, etc.) con valores razonables ya configurados.</p><p>Cada plantilla se muestra como una tarjeta con su nombre, la insignia «plantilla global», su rol y su descripción. Las plantillas no son directamente asignables: para usarlas en tu tenant debes clonarlas.</p><p>El botón <b>Clonar y forkar</b> (visible solo con rol administrador del tenant) crea una copia editable de la plantilla en <b>Mis agentes humanos</b>. La copia queda marcada como «forkado» y a partir de ahí la completas como cualquier agente humano propio: le asignas un usuario real de tu tenant, ajustas su tarifa, sus canales de notificación y su circuito de escalación con el botón Editar.</p><p>Si no eres administrador verás la nota «Sólo un admin del tenant puede clonar» en lugar del botón. Si la clonación falla (por ejemplo, por permisos), el error se muestra en rojo dentro de la propia tarjeta.</p><p><b>Flujo recomendado</b>: clona la plantilla que más se parezca al rol que necesitas, asígnale la persona concreta y ajusta las estimaciones de planning — es más rápido y consistente que crear el agente humano desde cero.</p>",
      action: async (page) => {
        await page
          .getByTestId("tab-templates")
          .click()
          .catch(async () => {
            await page
              .getByRole("tab", { name: "Plantillas globales" })
              .click()
              .catch(() => {});
          });
        await page.waitForTimeout(500);
      },
      fullPage: true,
    },
    {
      title: "Equipos",
      goto: "/admin/teams",
      body: "<p>La pantalla <b>Equipos</b> lista las agrupaciones de agentes que trabajan de forma cooperativa sobre un plan. Al igual que el catálogo de agentes, se organiza en tres pestañas por ámbito: <b>Built-in</b> (equipos de catálogo mantenidos por la plataforma, con su insignia «Built-in»), <b>Plantillas del Tenant</b> (equipos propios cuyos miembros son plantillas del tenant) y <b>Locales del Proyecto</b> (equipos con algún miembro local de un proyecto, típicamente fruto de una adopción a proyecto). Cada pestaña indica su recuento entre paréntesis.</p><p>Cada equipo se muestra como una tarjeta con su <b>nombre</b>, su <b>descripción</b> y el número de <b>miembros</b> que lo componen. Los equipos creados a partir de un built-in llevan la insignia <b>Adoptado</b>. Internamente, cada miembro de un equipo es un agente con un rol dentro del equipo, una posible marca de líder y una prioridad de asignación que el orquestador usa al repartir tareas.</p><p>Desde cada tarjeta tienes dos acciones: <b>Ver detalle</b>, que abre la ficha del equipo (siguiente paso), y — solo en los built-in — <b>Adoptar</b>, que crea una copia personalizable del equipo completo en tu tenant o en un proyecto (lo cubrimos dos pasos más adelante).</p><p>Si no aparece ningún equipo y esperabas los built-in, significa que aún no se ha ejecutado el seed de la plataforma; la pantalla lo indica con el comando correspondiente.</p>",
      fullPage: true,
    },
    {
      title: "Detalle de un equipo",
      goto: "/admin/teams",
      body: "<p>La ficha de detalle de un equipo (en esta captura, la del primer equipo del listado) reúne todo lo que gobierna el trabajo en grupo. De arriba abajo encontrarás:</p><ul><li><b>Hub de Capacidad del equipo</b> — la misma vista SABER/RECORDAR/SER/HACER de los agentes, pero agregada: la capacidad del equipo es la <b>unión de solo lectura</b> de la de sus miembros. Sirve para responder de un vistazo «¿qué sabe y qué puede hacer este equipo?».</li><li><b>Modelo del equipo</b> — el proveedor + modelo por defecto que heredan los agentes del equipo que no fijan modelo propio. Vacío = heredar del nivel superior (proyecto → plataforma).</li><li><b>Modelo del chat del equipo</b> — el modelo concreto que usa el chat de planificación cuando conversas con este equipo.</li><li><b>Política de memoria del equipo</b> — gobierna el scope de memoria de sus miembros. «Sin política (heredar)» deja que cada agente use su propio scope; fijar un scope hace que las lecciones aprendidas (memoria semántica) viajen a ese nivel, mientras lo puntual de cada proyecto (memoria episódica) se queda en su proyecto.</li></ul><p>Debajo, la sección <b>Miembros</b> lista cada agente con su rol en el equipo, la insignia <b>Líder</b> si procede, su <b>Prioridad</b> de asignación (0–1000) y la marca <b>Linked</b>/<b>Forked</b> según sea referencia al catálogo o copia propia. En equipos editables cada fila tiene un botón <b>Editar</b> para cambiar líder, rol y prioridad, y arriba el botón <b>Añadir miembro</b> abre un diálogo para incorporar un agente del catálogo en modo <b>Linked</b> (por referencia: si el original evoluciona, el equipo lo ve) o <b>Forked</b> (copia editable en un proyecto, independiente del original).</p><p>En los equipos <b>built-in</b> toda la ficha es de solo lectura y la cabecera ofrece <b>Adoptar / Personalizar</b> como única vía de cambio. En los equipos propios, la cabecera tiene <b>Editar</b> (nombre y descripción) y <b>Borrar</b>, que exige teclear el nombre del equipo para confirmar; borrar un equipo NO borra sus agentes, solo la pertenencia.</p>",
      action: async (page) => {
        await page.getByRole("link", { name: "Ver detalle" }).first().click({ timeout: 10_000 });
        await page.waitForTimeout(1200);
      },
      fullPage: true,
    },
    {
      title: "Adoptar un equipo built-in",
      goto: "/admin/teams",
      body: "<p>El botón <b>Adoptar</b> de un equipo built-in abre el diálogo <b>Adoptar / Personalizar equipo</b> que ves en esta captura. Adoptar crea una <b>copia editable del equipo completo</b>: sus agentes se forkean (persona + tools + skills incluidas) y el equipo built-in original no se toca.</p><p>El diálogo pide:</p><ul><li><b>Nombre del equipo</b> — precargado con «(copia)»; ponle un nombre propio de tu organización.</li><li><b>Destino</b> — dos opciones: <b>Catálogo del tenant</b> (el equipo y sus agentes viven a nivel de tenant, reutilizables en cualquier proyecto) o <b>Un proyecto</b> (el equipo y sus agentes quedan atados a un proyecto concreto, que eliges en el selector; si no tienes proyectos, el diálogo te lo indica).</li><li><b>Modelo del equipo (opcional)</b> — la casilla «Fijar un modelo por defecto» despliega el selector de proveedor/modelo/temperatura para dejar el modelo del equipo fijado desde el primer momento; si no la marcas, el equipo hereda el modelo de proyecto/plataforma.</li></ul><p>Al pulsar <b>Adoptar</b>, la interfaz navega a la ficha del equipo nuevo, ya editable: puedes renombrar miembros, cambiar líder y prioridades, ajustar su política de memoria o añadir y quitar agentes.</p><p><b>Caso de uso típico</b>: la plataforma trae un equipo built-in de desarrollo para un stack concreto. Lo adoptas a tu proyecto, ajustas el prompt del backend dev forkado a las convenciones internas y fijas el modelo del equipo al proveedor que tu organización tiene contratado.</p>",
      action: async (page) => {
        await page.locator('[data-testid^="team-adopt-"]').first().click({ timeout: 10_000 });
        await page.waitForTimeout(600);
      },
      fullPage: true,
    },
    {
      title: "Catálogo de tools (herramientas)",
      goto: "/admin/tools",
      body: "<p>El <b>Catálogo de tools</b> es el lugar central para explorar las herramientas que los agentes pueden usar y gestionar las personalizadas de tu tenant. Las tools se clasifican por tres facetas: <b>Función</b> (Archivos, Ejecución/Tests, Git, Red, Conocimiento, Notificaciones, Comandos shell, MCP, Orquestación, Personalizada), <b>Seguridad</b> (Segura, Aislada, Privilegiada) y <b>Origen</b> (Nativa, MCP, HTTP, Python, Contenedor).</p><p>Las tres facetas responden a preguntas distintas: la <b>Función</b> dice para qué sirve la tool; la <b>Seguridad</b> cuánto riesgo implica ejecutarla (una tool Privilegiada como el shell puede tocar el sistema; una Aislada corre confinada; una Segura es de solo lectura o sin efectos); y el <b>Origen</b> cómo está implementada (nativa de la plataforma, endpoint HTTP, función Python, servidor MCP o contenedor).</p><p>En la parte superior dispones de un <b>buscador</b> por nombre o descripción y de tres selectores de faceta (Función, Seguridad, Origen), cada uno con la opción «Todas»; los filtros se combinan entre sí. Cuando hay filtros activos y ningún resultado coincide, puedes pulsar <b>Limpiar filtros</b>.</p><p>El listado se divide en dos grupos: <b>De plataforma (built-in)</b>, de solo lectura y marcadas con la insignia «Solo lectura»; y <b>Personalizadas del tenant</b>, que un administrador puede editar o borrar. Cada fila muestra el nombre, la descripción y las tres insignias de faceta con su tooltip explicativo. Si una tool aún no tiene motor en el runtime, se marca con «No disponible aún» para no inducir a error: asignarla a un agente no le dará esa capacidad todavía.</p><p>Si eres administrador del tenant verás el botón <b>Nueva tool</b> (arriba a la derecha) y, en las tools custom, los iconos de <b>editar</b> (lápiz) y <b>borrar</b> (papelera). El alta la cubrimos en el siguiente paso. Recuerda que asignar tools a un agente concreto se hace desde la sección Tools de la ficha del agente (paso 8), no desde este catálogo.</p>",
      fullPage: true,
    },
    {
      title: "Crear o editar una tool personalizada",
      goto: "/admin/tools",
      body: "<p>Pulsa <b>Nueva tool</b> (o el lápiz de una tool custom) para abrir el formulario; en esta captura lo abrimos con el botón <b>Nueva tool</b>. Solo se gestionan tools <b>personalizadas del tenant</b>; las built-in las mantiene la plataforma y son de solo lectura.</p><p>Indica el <b>Nombre</b> (obligatorio; se normaliza a slug y debe ser único en el tenant, p. ej. <code>deploy_preview</code>) y una <b>Descripción</b> que explique qué hace y cuándo usarla — esa descripción es la que leerán los agentes para decidir si invocan la tool, así que redáctala pensando en ellos.</p><p>A continuación define las tres facetas mediante selectores: <b>Función</b> (categoría), <b>Origen</b> (tipo de implementación: HTTP, Python, MCP o Contenedor; la opción «Nativa» no está disponible porque es exclusiva de la plataforma) y <b>Seguridad</b> (Segura, Aislada o Privilegiada). Clasifica con honestidad: la faceta de seguridad participa en las políticas de guardrails y validación humana.</p><p>El campo <b>Referencia de implementación</b> apunta al recurso concreto: la URL del endpoint, el dotted path de la función Python, el comando del contenedor, etc.</p><p>El botón <b>Crear tool</b> / <b>Guardar cambios</b> se habilita cuando hay nombre. Si el nombre colisiona con una built-in o con otra tool del tenant, el sistema lo rechaza y muestra el aviso de duplicado. Para borrar una tool custom usa el icono de papelera, que abre una confirmación advirtiendo que la acción no se puede deshacer; ten en cuenta que los agentes que la tuvieran asignada dejarán de verla.</p>",
      action: async (page) => {
        await page
          .getByRole("button", { name: "Nueva tool" })
          .click()
          .catch(() => {});
        await page.waitForTimeout(500);
      },
      fullPage: true,
    },
    {
      title: "Tipos de implementación de una tool: quién la ejecuta cuando el agente la invoca",
      goto: "/admin/tools",
      body: "<p>El paso anterior mostró el formulario de alta; este profundiza en la faceta de <b>Origen</b> — el <code>implementation_type</code> — que decide <b>quién ejecuta la tool</b> cuando un agente la invoca durante un run. Hay cinco tipos:</p><ul><li><b>Nativa (builtin)</b> — implementada y mantenida por la plataforma (archivos, git, tests, red…). De solo lectura; no puedes crear tools de este tipo.</li><li><b><code>python_function</code></b> — lógica propia en Python. En la <b>Referencia de implementación</b> escribes el código con un contrato fijo: definir <code>def run(args: dict)</code> a nivel de módulo. El código corre en un <b>subprocess aislado</b> dentro del sandbox del run (intérprete fresco, entorno vacío, timeout duro) — nunca se evalúa en el proceso del agente.</li><li><b><code>http_endpoint</code></b> — expone una API existente sin escribir código: la referencia es una plantilla de URL con placeholders del schema (p. ej. <code>https://api.interna.empresa.com/stock/{sku}</code>); cada placeholder se sustituye por el argumento homónimo ya validado y la respuesta del endpoint es el output de la tool. El dominio debe estar en la <b>allowlist del proyecto</b> (el guard anti-SSRF revalida cada resolución) y la credencial, si la hay, vive en Vault — nunca en la URL.</li><li><b><code>docker_command</code></b> — la familia <code>run_pytest</code>, <code>run_build</code>…: ejecuta un comando en el <b>runtime-template</b> del proyecto (el contenedor del stack tecnológico), no en el sandbox del agente.</li><li><b><code>mcp_tool</code></b> — tools materializadas al importar las de un servidor MCP; las cubre el último paso de este manual.</li></ul><p><b>Ejemplo real</b> (validado en un run de esta plataforma): la custom tool <code>changelog_stamp</code>, una <code>python_function</code> que normaliza entradas de changelog. Su schema declara dos propiedades obligatorias (<code>version</code> y <code>summary</code>) y su código es un <code>def run(args)</code> de tres líneas que devuelve <code>{entry, length}</code> con la entrada formateada. Al asignarla a un agente <b>no hay que hacer nada más</b>: el modelo la ve automáticamente como una función llamada <code>changelog_stamp</code> con su descripción y su schema — <b>nadie inyecta schemas ni listas de tools a mano en ningún prompt</b>; la plataforma construye esa parte del prompt por ti.</p><p>Dos reglas de oro al crear una tool: la <b>descripción es prompt</b> (es lo único que el modelo lee para decidir usarla: di qué hace, cuándo usarla y qué devuelve) y el <b><code>input_schema</code> es contrato</b> (el runtime valida los argumentos ANTES de ejecutar — los inválidos ni llegan a tu código —, así que describe cada propiedad). El recetario de la documentación («Recetas: MCP servers, custom tools y skills», en <code>docs/03-guides/</code>) contiene estos ejemplos completos listos para copiar.</p>",
      fullPage: true,
    },
    {
      title: "Skills en profundidad: el prompt_fragment que crea hábitos",
      goto: "/admin/agents",
      body: "<p>El paso 8 presentó la sección <b>Skills del agente</b>; este paso explica qué es exactamente una skill y cómo se usa para gobernar el <i>comportamiento</i> de los agentes. Una skill <b>no añade capacidades invocables</b> (eso lo hacen las tools): añade <b>instrucción de sistema reutilizable</b>. Cada skill del catálogo tiene un nombre, una categoría (Backend, Frontend, DevOps, QA/Testing, Investigación o Documentación), una descripción y — la pieza clave — un <b><code>prompt_fragment</code></b>: un bloque de texto que viaja dentro del system prompt de <b>todos los runs</b> de los agentes que la tengan asignada. Es el mecanismo para convertir «cómo queremos que trabaje el equipo» en configuración versionable, en lugar de repetirlo en cada tarea.</p><p><b>Ejemplo real</b> (validado en un run de esta plataforma): la skill <b>«Estilo de changelog corporativo»</b> (categoría Documentación). Su fragment dice, en esencia: <i>«Cuando generes o escribas entradas de changelog: usa SIEMPRE la tool <code>changelog_stamp</code> para producir la entrada normalizada (no la formatees a mano) y termina el resumen final de tu trabajo con la palabra exacta CHANGELOG-OK»</i>. Con la skill asignada, la tarea del plan ya <b>no menciona la tool</b> — pide solo el resultado («Genera la entrada de changelog de la versión 1.2.3 con el resumen X») — y aun así el agente invoca <code>changelog_stamp</code> y sella su resumen: en el visor de runs se ve el step <code>tool_call</code> de una tool que la tarea nunca nombró. Esa es la prueba de que el fragment llegó y actuó.</p><p>Dos matices operativos importantes:</p><ul><li><b>Una skill no otorga tools.</b> Si el fragment pide usar una tool, el agente debe tenerla <b>además</b> asignada en su sección Tools; el campo <code>required_tools</code> de la skill documenta esa dependencia y la interfaz la señala.</li><li><b>Dónde vive el catálogo.</b> Las skills disponibles (las built-in del seed y las personalizadas del tenant) se exploran y asignan desde la sección <b>Skills del agente</b> de la ficha de cada agente — la que muestra esta captura. La creación de skills personalizadas se hace hoy vía API (<code>POST /api/skills</code>, rol administrador del tenant), con los campos nombre, categoría, descripción y <code>prompt_fragment</code>.</li></ul><p><b>Cuándo skill, cuándo persona, cuándo tarea</b>: la persona es la identidad de UN agente; la skill es un hábito <b>compartible entre agentes</b> (asignas la misma a todo el equipo); la tarea es lo puntual de un trabajo concreto. La heurística práctica: si te descubres copiando la misma frase en varias tareas, conviértela en skill; si es identidad de un solo agente, ponla en su persona.</p>",
      action: async (page) => {
        await page.locator('[data-testid^="agent-link-"]').first().click({ timeout: 10_000 });
        await page.waitForTimeout(1000);
        await page
          .getByTestId("agent-skills-section")
          .scrollIntoViewIfNeeded()
          .catch(() => {});
        await page.waitForTimeout(400);
      },
      fullPage: false,
    },
    {
      title: "MCP servers del proyecto: conectar, importar y usar tools externas",
      goto: PID ? `/admin/projects/${PID}/mcp-servers` : "/admin/tools",
      body: "<p>La tercera vía para ampliar lo que un agente puede HACER son los <b>servidores MCP</b> (Model Context Protocol): servicios externos — un gestor de incidencias, una wiki, un buscador de documentación, un sistema interno de tu empresa — que exponen tools invocables. A diferencia de las custom tools, los MCP servers se declaran <b>por proyecto</b>: en el hub del proyecto (<b>Proyectos → tu proyecto → pestaña MCP servers</b>, la pantalla de esta captura). El flujo completo, verificado de punta a punta en esta plataforma, tiene cinco pasos:</p><ul><li><b>1 · Declarar el server</b> — botón <b>«Añadir MCP server»</b>: nombre, transporte (<code>stdio</code>, <code>sse</code> o <code>streamable_http</code>) y URL o comando. El picker ofrece <b>plantillas verificadas</b> (GitHub, Jira, Google Drive…) que pre-rellenan la configuración y la ruta de Vault esperada; las credenciales van SIEMPRE en Vault (campo credencial del servidor), nunca en la base de datos.</li><li><b>2 · Probar</b> — el botón «Probar» hace el <b>handshake MCP real</b> contra el server y lista las tools que expone. Los errores son tipados y accionables: <code>AUTH_ERROR</code> significa secreto ausente o incorrecto en Vault; <code>TRANSPORT_ERROR</code>, que el server no es alcanzable desde la red de agentes.</li><li><b>3 · Importar tools</b> — el botón «Importar tools» materializa cada tool descubierta en el catálogo del tenant con el nombre <b><code>&lt;server&gt;.&lt;tool&gt;</code></b> (p. ej. <code>atlassian.confluence_create_page</code>); aparecen en la pestaña <b>Avanzadas</b> de la sección Tools de los agentes.</li><li><b>4 · Asignar al agente</b> — ficha del agente → Tools. Solo los agentes con la tool asignada la ven en sus runs (la allowlist filtra). Si te saltas esta capa, el server conecta pero el modelo <b>no ve</b> las tools — el fallo más común.</li><li><b>5 · El prompt</b> — para <b>acciones puntuales</b>, nombra la tool EXACTA (con su namespace) en la descripción y los criterios de aceptación de la tarea; para <b>hábitos</b> («siempre que cierres una tarea con issue asociada, transiciónala»), ponlo en una skill o en la persona del agente, como vimos en el paso anterior.</li></ul><p><b>Caso real validado end-to-end</b>: un MCP de Atlassian conectado a un proyecto, con una tarea que publicó el resumen de cierre como página de <b>Confluence</b> (<code>atlassian.confluence_create_page</code>) y transicionó la issue de <b>Jira</b> a Done (<code>atlassian.jira_transition_issue</code>), comentando la URL de la página — todo invocado por el agente, sin intervención humana.</p><p><b>Verificación y diagnóstico</b>: en el visor de runs (manual 13), el step <b><code>mcp_wire</code></b> registra, por cada server del proyecto, si conectó y qué tools quedaron registradas — o por qué falló; los steps <code>tool_call</code> muestran cada invocación con sus argumentos. Una trampa comprobada: la tarea debe ser <b>autocontenida</b> — si pide publicar un fichero que no existe en el repo, el agente agota sus iteraciones buscándolo y nunca llama al MCP; garantiza el insumo o pide crearlo primero.</p>",
      fullPage: true,
    },
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  // Manual extenso (20 pasos, varios con navegación a fichas de detalle).
  test.setTimeout(900_000);
  await login(page);
  await generateManual(page, manual);
});
