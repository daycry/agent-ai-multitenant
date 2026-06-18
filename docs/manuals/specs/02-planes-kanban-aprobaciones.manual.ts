import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef } from "../lib/manual";

// GENERADO desde el workflow de redacción. Editable a mano; reejecutable.
const manual: ManualDef = {
  order: "02",
  slug: "02-planes-kanban-aprobaciones",
  title: "Planes, Kanban y Aprobaciones",
  audience:
    "Gestores de proyecto, revisores y operadores de tenant que supervisan el avance del trabajo agéntico y autorizan acciones sensibles.",
  intro:
    "<p>Este manual explica cómo seguir y gobernar el trabajo de los equipos de agentes desde el panel de administración: el <b>doble Kanban</b> de planes y tareas, la <b>cola de aprobaciones</b> de acciones sensibles y la configuración de las <b>políticas de validación humana</b> por proyecto.</p><p>Aprenderás a leer el tablero, mover tareas entre estados, atender solicitudes de aprobación pendientes y elegir qué categorías de acciones requieren intervención humana. El objetivo es que entiendas, paso a paso, qué muestra cada pantalla, qué elementos contiene y qué acciones puedes ejecutar.</p>",
  steps: [
    {
      title: "El tablero (doble Kanban): visión general",
      goto: "/admin/board",
      body: "<p>La pantalla <b>Tablero</b> es la vista operativa central y respeta el principio del <b>doble Kanban</b>: nunca mezcla tareas de varios planes en un tablero plano. Se divide en dos secciones apiladas verticalmente.</p><ul><li><b>Arriba (gerencial)</b>: la fila de <b>Planes</b>, una rejilla de tarjetas, una por plan/proyecto activo del tenant.</li><li><b>Abajo (operativa)</b>: el tablero de <b>Tareas</b> del plan seleccionado, organizado en columnas por estado.</li></ul><p>En la cabecera verás el título <code>Tablero</code> y la indicación de que puedes arrastrar una tarea entre columnas para cambiar su estado. Si el tenant aún no tiene planes, aparece un mensaje invitándote a crear un proyecto desde una plantilla.</p>",
      fullPage: true,
    },
    {
      title: "Fila de planes: seleccionar el plan a operar",
      goto: "/admin/board",
      body: "<p>La sección superior <b>Planes</b> muestra una tarjeta por cada plan activo. A la derecha del título verás el contador total (p. ej. <code>3 planes</code>). Cada tarjeta indica el <b>nombre</b> del plan, una insignia con el <b>equipo</b> asignado (si lo tiene), una breve <b>descripción</b> y una insignia de <b>estado</b> (por ejemplo <code>active</code>).</p><p>Al cargar la pantalla se selecciona automáticamente el primer plan. Para cambiar de plan, <b>haz clic sobre su tarjeta</b>: la tarjeta seleccionada se resalta con un borde destacado y, debajo, el tablero de tareas se actualiza para mostrar únicamente las tareas de ese plan.</p>",
      fullPage: true,
    },
    {
      title: "Tablero de tareas: columnas por estado",
      goto: "/admin/board",
      body: "<p>La sección inferior <b>Tareas</b> muestra las tareas del plan seleccionado (su nombre aparece junto al título) distribuidas en columnas, una por estado. Las columnas son, en orden: <b>Backlog</b>, <b>Ready</b>, <b>En curso</b>, <b>Pendiente de aprobación</b>, <b>Revisión</b>, <b>Bloqueada</b>, <b>Hecho</b> y <b>Cancelada</b>.</p><p>Cada columna lleva una insignia con su nombre y un contador de tareas. Cada tarjeta de tarea muestra su <b>título</b>, una insignia de <b>prioridad</b> (baja, media, alta o crítica) y, si existe, un fragmento de su descripción. Las columnas vacías indican <code>Sin tareas</code>. Una insignia <b>Tiempo real</b> confirma que el tablero se actualiza en vivo.</p>",
      fullPage: true,
    },
    {
      title: "Mover tareas y actualización en tiempo real",
      goto: "/admin/board",
      body: "<p>Para cambiar el estado de una tarea, <b>arrástrala</b> desde su columna actual y <b>suéltala</b> sobre la columna destino. La columna sobre la que se va a soltar se resalta mientras arrastras. El cambio se aplica de forma inmediata (optimista): la tarjeta salta de columna al instante.</p><p>Si el cambio falla en el servidor, la tarjeta vuelve a su columna original y aparece un <b>banner de error</b> sobre el tablero explicando el motivo. El tablero también escucha eventos en <b>tiempo real</b>: si otro usuario o un agente cambia el estado de una tarea, o crea una nueva, verás la actualización sin recargar la página. Nota de comportamiento: al <b>aprobar</b> una tarea pendiente vuelve a <i>Backlog</i> y al <b>rechazarla</b> pasa a <i>Bloqueada</i>.</p>",
      fullPage: true,
    },
    {
      title: "Cola de aprobaciones: solicitudes pendientes",
      goto: "/admin/approvals",
      body: "<p>La pantalla <b>Aprobaciones</b> es la bandeja de revisión: lista todas las <b>solicitudes de aprobación humana pendientes</b> que un revisor debe resolver para que la ejecución del agente continúe. En la cabecera aparece el título <code>Aprobaciones</code> y, debajo, la etiqueta <b>Pendientes</b> con una insignia que indica cuántas hay.</p><p>Cada solicitud se muestra como una tarjeta. Si no hay nada pendiente, verás un estado vacío con el mensaje <i>Sin aprobaciones pendientes</i>. Si falla la carga, se muestra un bloque de error. La lista se actualiza al resolver cada solicitud.</p>",
      fullPage: true,
    },
    {
      title: "Resolver una solicitud: aprobar o rechazar",
      goto: "/admin/approvals",
      body: "<p>Cada tarjeta de aprobación muestra la <b>categoría</b> de la acción (p. ej. push, despliegue, acceso a secretos), una insignia con su <b>estado</b> y la fecha en que se <b>solicitó</b>. Debajo, en un bloque de código, se detalla la <b>acción concreta</b> que el agente quiere ejecutar (en formato JSON), para que puedas revisar exactamente qué se va a hacer.</p><p>Dispones de un campo de texto <b>Motivo (opcional)</b> para dejar constancia de tu decisión, y dos botones: <b>Aprobar</b> (permite que la ejecución continúe) y <b>Rechazar</b> (la deniega). Al pulsar cualquiera de los dos, la solicitud se resuelve y desaparece de la lista de pendientes. Si la operación falla, se muestra un mensaje de error en la propia tarjeta.</p>",
      fullPage: true,
    },
    {
      title: "Validación humana: elegir una plantilla (preset)",
      goto: "/admin/approval-policy",
      body: "<p>La pantalla <b>Validación humana</b> define qué tipos de acciones puede ejecutar un agente automáticamente y cuáles exigen aprobación de una persona. Todo se gobierna mediante <b>plantillas predefinidas</b> (presets): <b>Sandbox</b>, <b>Desarrollo</b>, <b>Producción</b> y <b>Cliente Externo</b>.</p><p>En la fila superior se muestra una tarjeta por plantilla, con su <b>nombre</b>, una <b>descripción</b> y una insignia que resume cuántas categorías requieren intervención humana (desde <i>Todo automático</i> en Sandbox hasta varias categorías con validación en Producción). Al abrir la pantalla se selecciona la primera plantilla; <b>haz clic en una tarjeta</b> para elegir otra como base.</p>",
      fullPage: true,
    },
    {
      title: "Validación humana: ajustar categorías y aplicar al proyecto",
      goto: "/admin/approval-policy",
      body: "<p>Bajo las plantillas, una tabla lista las <b>13 categorías</b> de acciones sensibles (cambios de código, commit, push, HTTP externo, acceso a secretos, migración de datos, despliegue a producción, aprovisionar infraestructura, rotación de secretos, comunicación externa, exportar PII y gestión de usuarios). Cada fila muestra la categoría, una pista, y un botón que alterna su decisión entre <b>Auto</b> (verde) y <b>Humano</b> (ámbar). Las celdas que difieran de la plantilla base se marcan con una insignia <b>Override</b>.</p><p>En el panel <b>Aplicar a un proyecto</b>, despliega el selector <b>Proyecto</b> y elige uno. Verás un <b>Resumen</b> (cuántas categorías quedan en auto y cuántas en humano) y una insignia <i>Cambios sin guardar</i> si hay ajustes pendientes. Pulsa <b>Aplicar política</b> para guardar la configuración resultante en ese proyecto; un mensaje confirma el éxito o muestra el error. Si el tenant no tiene proyectos, se te indica que crees uno antes de poder guardar.</p>",
      fullPage: true,
    },
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  test.setTimeout(300_000);
  await login(page);
  await generateManual(page, manual);
});
