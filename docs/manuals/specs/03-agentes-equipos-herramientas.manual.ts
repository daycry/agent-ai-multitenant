import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef } from "../lib/manual";

// GENERADO desde el workflow de redacción. Editable a mano; reejecutable.
const manual: ManualDef = {
  order: "03",
  slug: "03-agentes-equipos-herramientas",
  title: "Agentes, equipos y herramientas",
  audience: "Administradores de tenant y operadores de la plataforma",
  intro:
    "<p>Este manual explica cómo gestionar el <b>capital agéntico</b> de tu tenant: los agentes de IA que ejecutan las tareas, los agentes humanos que representan a personas asignables, los equipos que agrupan agentes para trabajar de forma cooperativa, y el catálogo de herramientas (tools) que define qué puede hacer cada agente.</p><p>Recorreremos cuatro pantallas del panel de administración: el catálogo de agentes IA (<code>/admin/agents</code>), los agentes humanos (<code>/admin/human-agents</code>), los equipos (<code>/admin/teams</code>) y el catálogo de tools (<code>/admin/tools</code>). En cada una verás qué información se muestra, qué puedes filtrar y qué acciones de alta y edición están disponibles según tu rol.</p><p>La mayoría de acciones de creación y edición requieren rol <b>administrador del tenant</b>; los usuarios sin ese rol pueden explorar y consultar, pero verán ocultos los botones de gestión.</p>",
  steps: [
    {
      title: "Catálogo de agentes IA (built-in)",
      goto: "/admin/agents",
      body: '<p>Esta es la pantalla principal de agentes de IA, accesible desde <b>Inicio > Agentes</b>. Un <b>agente</b> es una entidad con un <b>rol</b> (por ejemplo project_manager, architect, backend_dev, qa, reviewer…) y una persona configurada (proveedor LLM, modelo y temperatura más su system prompt).</p><p>El catálogo se organiza en tres pestañas según el ámbito (scope) del agente: <b>Built-in</b> (agentes mantenidos por la plataforma, que ves seleccionada en esta captura), <b>Plantillas del Tenant</b> (plantillas reutilizables propias de tu organización) y <b>Locales del Proyecto</b> (agentes específicos de un proyecto, normalmente forkados de un built-in o plantilla). Cada pestaña muestra entre paréntesis cuántos agentes contiene.</p><p>Cada agente aparece como una tarjeta con su nombre, una insignia de ámbito, su rol, su descripción y un fragmento del system prompt en el idioma activo. Si el agente fue forkado de otro, se indica con la nota "Forked from another agent". Al pulsar una tarjeta accedes a su ficha de detalle, donde se puede inspeccionar y editar la persona del agente.</p><p>Si tienes rol de administrador del tenant verás el botón <b>Nuevo agente</b> en la esquina superior derecha, que abre el formulario de alta (lo cubrimos en el siguiente paso).</p>',
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
      body: '<p>Las otras dos pestañas del catálogo separan los agentes que crea tu organización. En <b>Plantillas del Tenant</b> (la que mostramos en esta captura) viven los agentes reutilizables en todos los proyectos del tenant: plantillas que defines una vez y reaprovechas al planificar.</p><p>La pestaña <b>Locales del Proyecto</b> agrupa los agentes específicos de un proyecto concreto, normalmente forkados de un built-in o de una plantilla del tenant para ajustar su persona (prompt, modelo, temperatura) sin tocar el original. Cada tarjeta forkada lo señala con la nota "Forked from another agent".</p><p>Si una pestaña aparece vacía, la propia tarjeta te indica el motivo: el tenant todavía no ha creado plantillas propias, o no hay agentes locales porque aún no se ha forkado ninguno. Cambia entre pestañas con un clic en su título; el número entre paréntesis te anticipa cuántos agentes encontrarás en cada ámbito.</p>',
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
      body: "<p>Pulsa <b>Nuevo agente</b> para abrir el diálogo de alta (es el que ves abierto en esta captura). Permite crear una <b>plantilla del tenant</b> (reutilizable en todos los proyectos) o un <b>agente local</b> de un proyecto concreto.</p><p>Rellena el <b>Nombre</b> y elige el <b>Role</b> de la lista cerrada de roles disponibles. Añade una <b>Descripción</b> (admite formato Markdown) y el <b>System prompt (ES)</b>, que es obligatorio y constituye la fuente del prompt en español.</p><p>En el bloque <b>Persona (modelo)</b> configuras la pata SER del agente: el <b>Proveedor</b> (solo se ofrecen los cuatro del catálogo cerrado: Claude (suscripción), GitHub Copilot, Azure AI Foundry y Ollama (local)), el <b>Modelo</b> (texto libre, p. ej. claude-sonnet-4) y la <b>Temperatura</b> (valor entre 0 y 2). Si algún valor es inválido se muestra el error bajo el campo. Opcionalmente puedes añadir el <b>System prompt (EN)</b> para la versión en inglés.</p><p>En <b>Scope</b> eliges entre <b>Plantilla del tenant</b> o <b>Local de un proyecto</b>; si eliges local, aparece el selector <b>Proyecto</b> para escoger entre tus proyectos del tenant. El botón <b>Crear</b> solo se habilita cuando el nombre, el system prompt ES y la persona son válidos (y hay proyecto si es local).</p>",
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
      title: "Agentes humanos",
      goto: "/admin/human-agents",
      body: '<p>Un <b>agente humano</b> representa a una persona (o a un rol) que puede asignarse a tareas del plan igual que un agente de IA. Esta pantalla se organiza en dos pestañas: <b>Mis agentes humanos</b> (los de tu tenant, que ves seleccionada en esta captura) y <b>Plantillas globales</b> (plantillas seedeadas por la plataforma que puedes clonar). Cada pestaña indica el número de elementos entre paréntesis.</p><p>Cada tarjeta de agente humano muestra el nombre, el rol, una insignia "humano" y, si procede, la marca "forkado". Debajo se ve el <b>usuario asignado</b> (o "Sin asignar" en aviso si no hay nadie), la <b>tarifa por hora</b> con su moneda, el <b>timeout de aceptación</b> en horas, a quién <b>escala</b> si no acepta a tiempo y los <b>canales de notificación</b> configurados.</p><p>Si eres administrador del tenant, cada tarjeta propia incluye un botón <b>Editar</b>, y en la pestaña <b>Plantillas globales</b> aparece <b>Clonar y forkar</b> para crear una copia editable en tu tenant. El botón <b>Nuevo agente humano</b> (arriba a la derecha) abre el formulario de alta, que cubrimos en el siguiente paso.</p>',
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
      body: "<p>El diálogo <b>Nuevo agente humano</b> (o <b>Editar agente humano</b>) define a una persona asignable a tareas; en esta captura lo abrimos con el botón <b>Nuevo agente humano</b>. Empieza por la identidad: <b>Nombre</b> (obligatorio), <b>Rol</b> (reviewer, security, devops, frontend_dev, backend_dev, architect, technical_writer, specialist o custom) y <b>Descripción</b> opcional con Markdown.</p><p>En el bloque <b>Asignación</b> eliges el <b>Usuario asignado</b> entre los miembros del tenant, el usuario de <b>Escalación</b> al que se deriva la tarea si no se acepta a tiempo, y el <b>Timeout de aceptación</b> en horas (por defecto 24, entre 1 y 720).</p><p>En <b>Coste</b> defines la <b>Tarifa por hora</b> y su <b>Moneda</b> (EUR, USD o GBP). En <b>Canales de notificación</b> marcas con qué medios se avisa a la persona: email, in_app y/o assistant. En <b>Estimaciones (planning)</b> indicas la <b>Respuesta esperada</b> y la <b>Ejecución esperada</b> en horas, usadas para la planificación.</p><p>El botón <b>Crear</b> / <b>Guardar cambios</b> se habilita cuando hay nombre. Si ocurre un error al guardar se muestra un mensaje en rojo dentro del diálogo.</p>",
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
      title: "Equipos",
      goto: "/admin/teams",
      body: '<p>La pantalla <b>Equipos</b> lista las agrupaciones de agentes que trabajan de forma cooperativa sobre un plan. Incluye tanto las <b>plantillas built-in</b> (mantenidas por la plataforma, identificadas con la insignia "Built-in") como los equipos propios de tu tenant.</p><p>Cada equipo se muestra como una tarjeta con su <b>nombre</b>, su <b>descripción</b> y el número de <b>miembros</b> que lo componen. Internamente, cada miembro de un equipo es un agente con un rol dentro del equipo, una posible marca de líder y una prioridad de asignación.</p><p>Pulsa <b>Ver detalle</b> en cualquier tarjeta para abrir la ficha del equipo, donde se consultan sus miembros y su composición. Si no aparece ningún equipo y esperabas los built-in, significa que aún no se ha ejecutado el seed de la plataforma; la pantalla lo indica con el comando correspondiente.</p>',
      fullPage: true,
    },
    {
      title: "Catálogo de tools (herramientas)",
      goto: "/admin/tools",
      body: '<p>El <b>Catálogo de tools</b> es el lugar central para explorar las herramientas que los agentes pueden usar y gestionar las personalizadas de tu tenant. Las tools se clasifican por tres facetas: <b>Función</b> (Archivos, Ejecución/Tests, Git, Red, Conocimiento, Notificaciones, Comandos shell, MCP, Orquestación, Personalizada), <b>Seguridad</b> (Segura, Aislada, Privilegiada) y <b>Origen</b> (Nativa, MCP, HTTP, Python, Contenedor).</p><p>En la parte superior dispones de un <b>buscador</b> por nombre o descripción y de tres selectores de faceta (Función, Seguridad, Origen), cada uno con la opción "Todas". Cuando hay filtros activos y ningún resultado coincide, puedes pulsar <b>Limpiar filtros</b>.</p><p>El listado se divide en dos grupos: <b>De plataforma (built-in)</b>, de solo lectura y marcadas con la insignia "Solo lectura"; y <b>Personalizadas del tenant</b>, que un administrador puede editar o borrar. Cada fila muestra el nombre, la descripción y las tres insignias de faceta con su tooltip explicativo. Si una tool aún no tiene motor en el runtime, se marca con "No disponible aún" para no inducir a error.</p><p>Si eres administrador del tenant verás el botón <b>Nueva tool</b> (arriba a la derecha) y, en las tools custom, los iconos de <b>editar</b> (lápiz) y <b>borrar</b> (papelera). El alta la cubrimos en el siguiente paso.</p>',
      fullPage: true,
    },
    {
      title: "Crear o editar una tool personalizada",
      goto: "/admin/tools",
      body: '<p>Pulsa <b>Nueva tool</b> (o el lápiz de una tool custom) para abrir el formulario; en esta captura lo abrimos con el botón <b>Nueva tool</b>. Solo se gestionan tools <b>personalizadas del tenant</b>; las built-in las mantiene la plataforma y son de solo lectura.</p><p>Indica el <b>Nombre</b> (obligatorio; se normaliza a slug y debe ser único en el tenant) y una <b>Descripción</b> que explique qué hace y cuándo usarla. A continuación define las tres facetas mediante selectores: <b>Función</b> (categoría), <b>Origen</b> (tipo de implementación: HTTP, Python, MCP o Contenedor; la opción "Nativa" no está disponible porque es exclusiva de la plataforma) y <b>Seguridad</b> (Segura, Aislada o Privilegiada).</p><p>El campo <b>Referencia de implementación</b> apunta al recurso concreto: la URL del endpoint, el dotted path de la función Python, el comando, etc.</p><p>El botón <b>Crear tool</b> / <b>Guardar cambios</b> se habilita cuando hay nombre. Si el nombre colisiona con una built-in o con otra tool del tenant, el sistema lo rechaza y muestra el aviso de duplicado. Para borrar una tool custom usa el icono de papelera, que abre una confirmación advirtiendo que la acción no se puede deshacer.</p>',
      action: async (page) => {
        await page
          .getByRole("button", { name: "Nueva tool" })
          .click()
          .catch(() => {});
        await page.waitForTimeout(500);
      },
      fullPage: true,
    },
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  test.setTimeout(360_000);
  await login(page);
  await generateManual(page, manual);
});
