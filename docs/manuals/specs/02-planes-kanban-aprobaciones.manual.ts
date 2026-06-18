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
      // PASO 1 — Visión general del tablero + fila de planes y selección.
      // Fusiona los antiguos pasos 1 y 2 (ambos /admin/board, fullPage, idénticos):
      // describían la estructura del doble Kanban y la fila de planes sin ninguna
      // interacción que los diferenciara. Aquí seleccionamos explícitamente el
      // PRIMER plan (clic en su tarjeta) para que la captura muestre la fila de
      // planes con una tarjeta resaltada y el tablero de tareas ya poblado debajo.
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
      body: "<p>La pantalla <b>Tablero</b> es la vista operativa central y respeta el principio del <b>doble Kanban</b>: nunca mezcla tareas de varios planes en un tablero plano. Se divide en dos secciones apiladas verticalmente.</p><ul><li><b>Arriba (gerencial)</b>: la sección <b>Planes</b>, una rejilla de tarjetas, una por plan/proyecto activo del tenant. A la derecha del título verás el contador total (p. ej. <code>3 planes</code>). Cada tarjeta indica el <b>nombre</b> del plan, una insignia con el <b>equipo</b> asignado (si lo tiene), una breve <b>descripción</b> y una insignia de <b>estado</b> (por ejemplo <code>active</code>).</li><li><b>Abajo (operativa)</b>: el tablero de <b>Tareas</b> del plan seleccionado, organizado en columnas por estado.</li></ul><p>Al cargar la pantalla se selecciona automáticamente el primer plan. Para cambiar de plan, <b>haz clic sobre su tarjeta</b>: la tarjeta seleccionada se resalta con un borde destacado y, debajo, el tablero de tareas se actualiza para mostrar únicamente las tareas de ese plan. Si el tenant aún no tiene planes, aparece un mensaje invitándote a crear un proyecto desde una plantilla.</p>",
      fullPage: true,
    },
    {
      // PASO 2 — Tablero de tareas: columnas, tarjetas, mover y tiempo real.
      // Fusiona los antiguos pasos 3 y 4 (ambos /admin/board, fullPage, idénticos):
      // describían las columnas por estado y la mecánica de arrastrar/tiempo real.
      // Para que la captura DIFIERA del paso 1, seleccionamos el ÚLTIMO plan de la
      // fila (otra tarjeta resaltada y, normalmente, otro conjunto de tareas).
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
      body: "<p>La sección inferior <b>Tareas</b> muestra las tareas del plan seleccionado (su nombre aparece junto al título) distribuidas en columnas, una por estado. Las columnas son, en orden: <b>Backlog</b>, <b>Ready</b>, <b>En curso</b>, <b>Pendiente de aprobación</b>, <b>Revisión</b>, <b>Bloqueada</b>, <b>Hecho</b> y <b>Cancelada</b>.</p><p>Cada columna lleva una insignia con su nombre y un contador de tareas; las columnas vacías indican <code>Sin tareas</code>. Cada tarjeta de tarea muestra su <b>título</b>, una insignia de <b>prioridad</b> (baja, media, alta o crítica) y, si existe, un fragmento de su descripción.</p><p>Para cambiar el estado de una tarea, <b>arrástrala</b> desde su columna actual y <b>suéltala</b> sobre la columna destino (la columna destino se resalta mientras arrastras). El cambio se aplica de forma inmediata (optimista): la tarjeta salta de columna al instante. Si el cambio falla en el servidor, la tarjeta vuelve a su columna original y aparece un <b>banner de error</b> sobre el tablero. La insignia <b>Tiempo real</b> confirma que el tablero se actualiza en vivo: si otro usuario o un agente cambia el estado de una tarea, o crea una nueva, verás la actualización sin recargar. Nota de comportamiento: al <b>aprobar</b> una tarea pendiente vuelve a <i>Backlog</i> y al <b>rechazarla</b> pasa a <i>Bloqueada</i>.</p>",
      fullPage: true,
    },
    {
      // PASO 3 — Cola de aprobaciones (vista + resolución).
      // Fusiona los antiguos pasos 5 y 6 (ambos /admin/board → /admin/approvals,
      // fullPage, idénticos): en un entorno sin solicitudes pendientes ambos
      // capturaban el mismo estado vacío. No hay una interacción fiable que abra
      // un diálogo distinto (las tarjetas y sus botones Aprobar/Rechazar solo
      // existen si hay solicitudes reales), así que se documentan en un único paso
      // que describe tanto la bandeja como la mecánica de resolución.
      title: "Cola de aprobaciones: revisar, aprobar o rechazar",
      goto: "/admin/approvals",
      body: "<p>La pantalla <b>Aprobaciones</b> es la bandeja de revisión: lista todas las <b>solicitudes de aprobación humana pendientes</b> que un revisor debe resolver para que la ejecución del agente continúe. En la cabecera aparece el título <code>Aprobaciones</code> y, debajo, la etiqueta <b>Pendientes</b> con una insignia que indica cuántas hay. Si no hay nada pendiente verás un estado vacío con el mensaje <i>Sin aprobaciones pendientes</i>; si falla la carga, se muestra un bloque de error.</p><p>Cada solicitud se muestra como una tarjeta con la <b>categoría</b> de la acción (p. ej. push, despliegue, acceso a secretos), una insignia con su <b>estado</b> y la fecha en que se <b>solicitó</b>. Debajo, en un bloque de código, se detalla la <b>acción concreta</b> que el agente quiere ejecutar (en formato JSON), para que puedas revisar exactamente qué se va a hacer.</p><p>Dispones de un campo de texto <b>Motivo (opcional)</b> para dejar constancia de tu decisión, y dos botones: <b>Aprobar</b> (permite que la ejecución continúe) y <b>Rechazar</b> (la deniega). Al pulsar cualquiera de los dos, la solicitud se resuelve y desaparece de la lista de pendientes; si la operación falla, se muestra un mensaje de error en la propia tarjeta.</p>",
      fullPage: true,
    },
    {
      // PASO 4 — Validación humana: plantilla Sandbox (todo automático).
      // Antes era el paso 7 (/admin/approval-policy sin interacción → capturaba la
      // primera plantilla por defecto). Ahora hacemos clic explícito en la tarjeta
      // "Sandbox" para mostrar la fila de plantillas con Sandbox seleccionada y su
      // tabla de categorías (todas en "Auto") debajo.
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
      body: "<p>La pantalla <b>Validación humana</b> define qué tipos de acciones puede ejecutar un agente automáticamente y cuáles exigen aprobación de una persona. Todo se gobierna mediante <b>plantillas predefinidas</b> (presets): <b>Sandbox</b>, <b>Desarrollo</b>, <b>Producción</b> y <b>Cliente Externo</b>.</p><p>En la fila superior se muestra una tarjeta por plantilla, con su <b>nombre</b>, una <b>descripción</b> y una insignia que resume cuántas categorías requieren intervención humana. Aquí seleccionamos <b>Sandbox</b>, la plantilla más permisiva: su insignia muestra <i>Todo automático</i> porque ninguna categoría exige validación. Al hacer clic en una tarjeta, esta se resalta con un borde destacado y la tabla inferior se recalcula con las decisiones de esa plantilla.</p>",
      fullPage: true,
    },
    {
      // PASO 5 — Validación humana: plantilla Producción + ajuste + aplicar.
      // Antes era el paso 8 (/admin/approval-policy sin interacción → misma captura
      // que el 7). Ahora clicamos la tarjeta "Producción" (tabla de categorías con
      // varias en "Humano", distinta del paso Sandbox), invertimos una categoría
      // (para que aparezca la insignia "Override" + "Cambios sin guardar") y abrimos
      // el selector de proyecto del panel "Aplicar a un proyecto".
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
      body: "<p>Bajo las plantillas, una tabla lista las <b>13 categorías</b> de acciones sensibles (cambios de código, commit, push, HTTP GET/POST externo, acceso a secretos, migración de datos, despliegue a producción, aprovisionar infraestructura, rotación de secretos, comunicación externa, exportar PII y gestión de usuarios). Cada fila muestra la categoría, una pista, y un botón que alterna su decisión entre <b>Auto</b> (verde) y <b>Humano</b> (ámbar). Aquí partimos de la plantilla <b>Producción</b> e invertimos la categoría <b>Push</b>: la celda que difiere de la plantilla base se marca con una insignia <b>Override</b>.</p><p>En el panel <b>Aplicar a un proyecto</b>, despliega el selector <b>Proyecto</b> y elige uno. Verás un <b>Resumen</b> (cuántas categorías quedan en auto y cuántas en humano) y una insignia <i>Cambios sin guardar</i> si hay ajustes pendientes. Pulsa <b>Aplicar política</b> para guardar la configuración resultante en ese proyecto; un mensaje confirma el éxito o muestra el error. Si el tenant no tiene proyectos, se te indica que crees uno antes de poder guardar.</p>",
      fullPage: true,
    },
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  test.setTimeout(300_000);
  await login(page);
  await generateManual(page, manual);
});
