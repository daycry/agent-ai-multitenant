import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef } from "../lib/manual";

// GENERADO desde el workflow de redacción. Editable a mano; reejecutable.
//
// Cada paso captura una pantalla DISTINTA: los pasos que documentan una
// pestaña la CLICAN antes del pantallazo (Canales/Preferencias/Plataforma,
// Activas/Histórico) y los que documentan un diálogo lo ABREN (Nuevo canal,
// Entregar tarea). Todas las `action` son tolerantes (.catch) para no romper
// la generación si un selector no existe en este entorno.
const manual: ManualDef = {
  order: "07",
  slug: "07-notificaciones-inbox",
  title: "Notificaciones e Inbox",
  audience:
    "Administradores de tenant (Tenant Admin), System Admin y cualquier usuario con tareas humanas asignadas",
  intro:
    "<p>Este manual cubre todo lo relacionado con las <b>notificaciones</b> y las <b>bandejas de entrada</b> de la plataforma. Aprenderás a configurar los canales y preferencias de notificación en tres capas (plataforma, tenant y usuario), a consultar el histórico de notificaciones enviadas con su estado y reintento manual, y a gestionar tu bandeja personal de tareas humanas asignadas.</p><p>Las tres secciones son independientes: la <b>configuración de notificaciones</b> (qué transportes existen y a quién avisan), la <b>bandeja de notificaciones</b> (el inbox in-app con el histórico de envíos) y la <b>bandeja personal de tareas</b> (tu inbox de validación y trabajo humano). Algunas acciones requieren permisos de Tenant Admin o System Admin; la propia interfaz muestra u oculta los controles según tu rol.</p>",
  steps: [
    {
      title: "Configuración de Notificaciones — pestaña Canales",
      goto: "/admin/notifications",
      // Pantalla inicial: la cabecera "Notificaciones" + la pestaña Canales,
      // que es la abierta por defecto. No hace falta interacción.
      body: "<p>Esta pantalla, encabezada por <b>Notificaciones</b>, centraliza la configuración de canales y preferencias en <b>tres capas</b> mediante pestañas situadas bajo el título:</p><ul><li><b>Canales</b>: pestaña abierta por defecto; define los canales concretos (Telegram, Email, Slack, etc.) de tu tenant o personales.</li><li><b>Preferencias</b>: reglas de enrutado que deciden qué eventos llegan por qué transporte.</li><li><b>Plataforma</b>: solo visible para el <b>System Admin</b>; habilita globalmente qué transportes pueden usarse.</li></ul><p>En la pestaña <b>Canales</b> ves la lista de los canales ya configurados. Cada canal se muestra como una tarjeta con su <b>nombre</b> y una serie de etiquetas: el <b>tipo de transporte</b> (p. ej. <code>telegram</code>), el <b>ámbito</b> (<code>tenant</code> o <code>user</code>), el estado (<b>activo</b> / <b>inactivo</b>) y si tiene <b>secreto</b> configurado (indicando si está en <i>Vault</i> o <i>cifrado en reposo</i>) o aparece <b>sin secreto</b>. Si todavía no hay canales, se muestra un mensaje invitando a pulsar <b>Nuevo canal</b>. Los Tenant Admin disponen, en cada tarjeta, de botones para <b>editar</b> (icono de lápiz) y <b>eliminar</b> (icono de papelera); al eliminar se pide confirmación.</p><p>El backend es siempre la fuente de verdad de permisos (RBAC + RLS): aunque veas un botón, la acción solo se aplica si tu rol lo permite.</p>",
      action: async (page) => {
        await page
          .getByRole("tab", { name: "Canales" })
          .click()
          .catch(async () => {
            await page
              .getByRole("button", { name: "Canales" })
              .click()
              .catch(async () => {
                await page
                  .getByText("Canales", { exact: true })
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
      title: "Crear o editar un canal de notificación",
      goto: "/admin/notifications",
      // Abrimos el diálogo "Nuevo canal" para capturar el formulario, no la
      // lista. Botón solo visible para Tenant Admin (data-testid channel-create-button).
      body: '<p>Pulsa <b>Nuevo canal</b> (arriba a la derecha, solo Tenant Admin) para abrir el diálogo de creación que ves en la captura. En él rellenas: <b>Ámbito</b> (<i>Tenant (compartido)</i> o <i>Usuario (solo yo)</i>), <b>Transporte</b> (solo se ofrecen los transportes habilitados por la plataforma), <b>Nombre</b> del canal, <b>Config</b> en formato JSON (sin secretos; p. ej. <code>{ "chat_id": "12345" }</code>) y un <b>Secreto</b> opcional (token del bot, contraseña o clave).</p><p>El secreto se cifra en reposo antes de guardarse y el sistema nunca lo devuelve en claro. La casilla <b>Canal activo</b> determina si el canal está operativo. Al editar un canal existente no puedes cambiar ámbito ni transporte, y dejar el secreto vacío conserva el actual. Confirma con <b>Crear</b> o <b>Guardar</b>; si el JSON de config es inválido, la interfaz lo advierte.</p>',
      action: async (page) => {
        await page
          .getByRole("button", { name: "Nuevo canal" })
          .click()
          .catch(async () => {
            await page
              .getByTestId("channel-create-button")
              .click()
              .catch(() => {});
          });
        await page.waitForTimeout(600);
      },
      fullPage: true,
    },
    {
      title: "Pestaña Preferencias — reglas de enrutado evento-canal",
      goto: "/admin/notifications",
      // Clicamos la pestaña Preferencias para capturar la matriz de enrutado.
      body: "<p>La pestaña <b>Preferencias</b> muestra una <b>matriz de enrutado</b>: en las filas los tipos de evento del sistema (<code>task_blocked</code>, <code>plan_approved</code>, <code>review_needed</code>, <code>budget_alert</code>) y en las columnas los transportes que tu tenant tiene configurados. Cada celda es una casilla que activa o desactiva (opt-in / opt-out) la entrega de ese evento por ese transporte.</p><p>Por defecto todos los eventos llegan (casilla marcada) salvo que crees una regla que lo desactive. Los Tenant Admin pueden cambiar las casillas; el resto de usuarios ven el estado en modo de solo lectura (<i>sí</i> / <i>no</i>). Si aún no hay ningún canal configurado, la pestaña te invita a crear primero al menos un canal.</p>",
      action: async (page) => {
        await page
          .getByRole("tab", { name: "Preferencias" })
          .click()
          .catch(async () => {
            await page
              .getByRole("button", { name: "Preferencias" })
              .click()
              .catch(async () => {
                await page
                  .getByText("Preferencias", { exact: true })
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
      title: "Pestaña Plataforma — transportes habilitados globalmente (System Admin)",
      goto: "/admin/notifications",
      // Clicamos la pestaña Plataforma (solo presente para System Admin).
      body: "<p>Esta pestaña solo es visible para el <b>System Admin</b>. Presenta la lista de <b>transportes disponibles</b> en la plataforma con una casilla por cada uno (Telegram, Email, Slack, etc.). Marcar o desmarcar un transporte determina si los tenants pueden configurar canales de ese tipo: un tenant solo puede crear canales de transportes habilitados aquí.</p><p>Tras ajustar las casillas, pulsa <b>Guardar</b> para aplicar los cambios globalmente. Si no eres System Admin, esta pestaña no aparece y no puedes alterar la configuración de plataforma.</p>",
      action: async (page) => {
        await page
          .getByRole("tab", { name: "Plataforma" })
          .click()
          .catch(async () => {
            await page
              .getByRole("button", { name: "Plataforma" })
              .click()
              .catch(async () => {
                await page
                  .getByText("Plataforma", { exact: true })
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
      title: "Bandeja de notificaciones — histórico, marcar leído, reintentar y paginar",
      goto: "/admin/notifications/inbox",
      // Una sola vista (sin pestañas ni diálogos): listado + barra de filtros +
      // paginación. Fusiona lo que antes eran dos pasos idénticos.
      body: "<p>La <b>Bandeja de notificaciones</b> muestra el histórico de notificaciones enviadas a los canales de tu tenant, con su estado y la posibilidad de reintento manual. En la barra superior verás un contador de <b>sin leer</b>, un filtro por <b>Estado</b> (Todos, <code>queued</code>, <code>sent</code>, <code>delivered</code>, <code>failed</code>, <code>retrying</code>, <code>dead_letter</code>), una casilla <b>Solo sin leer</b> y un botón <b>Marcar todo como leído</b> (se desactiva si no hay pendientes).</p><p>Cada entrada de la lista indica el <b>tipo de evento</b>, el <b>transporte</b>, el <b>estado</b> (con color según resultado), el número de intento si es mayor que 1, el posible mensaje de error y la fecha de creación. Las notificaciones no leídas se destacan con un punto y un borde lateral, y ofrecen el botón <b>Marcar leído</b>; el marcador de leído es por usuario, de modo que cada administrador tiene su propio inbox.</p><p>Las notificaciones en estado <b>dead_letter</b> (fallidas tras agotar reintentos) muestran, solo para Tenant Admin, un botón <b>Reintentar</b> que vuelve a encolar el envío reutilizando el canal original. Al pie de la lista hay un contador de resultados (p. ej. <i>1–25 de 120</i>) y los botones <b>Anterior</b> y <b>Siguiente</b> para navegar entre páginas de 25 elementos. Los filtros de estado y de no leídos reinician la paginación.</p>",
      fullPage: true,
    },
    {
      title: "Bandeja personal — Tareas asignadas a mí (pestaña Activas y sus acciones)",
      goto: "/admin/inbox",
      // Clicamos la pestaña Activas (default) para asegurar la vista de tarjetas
      // con sus acciones contextuales. Fusiona el "listado" y las "acciones",
      // que son la misma vista sin interacción que las diferencie.
      body: "<p>La pantalla <b>Tareas asignadas a mí</b> es la bandeja personal de cualquier usuario logueado: lista las tareas humanas que tienes asignadas y activas. Se organiza en dos pestañas, <b>Activas</b> e <b>Histórico</b>. En <b>Activas</b>, cada tarea aparece como una tarjeta con su <b>título</b>, una breve descripción, el <b>proyecto</b> y <b>plan</b> asociados, el <b>estado</b> de la tarea (Asignada, En curso, En revisión) y, si procede, el <b>plazo de aceptación</b> (p. ej. <i>Aceptar en ~5 h</i> o <i>Plazo de aceptación vencido</i>).</p><p>Cada tarjeta ofrece acciones según su estado. Cuando la tarea está pendiente de aceptación puedes <b>Aceptar</b> (pasa a aceptada y la tarea entra en curso) o <b>Rechazar</b>; al rechazar se abre un diálogo donde debes escribir una <b>justificación obligatoria</b> y la tarea pasa a bloqueada para que un administrador la reasigne. Una vez aceptada, aparece <b>Marcar completada</b>, que abre el formulario de entrega. En cualquier momento dispones de <b>Escalar al admin</b>, que abre un diálogo con un <b>motivo opcional</b>, bloquea la tarea y notifica a los administradores del tenant. Si una acción falla, el error se muestra dentro de la propia tarjeta. Si no tienes tareas, se muestra un mensaje indicándolo; solo ves tus propias asignaciones, nunca las de otros usuarios o tenants.</p>",
      action: async (page) => {
        await page
          .getByRole("tab", { name: "Activas" })
          .click()
          .catch(async () => {
            await page
              .getByRole("button", { name: "Activas" })
              .click()
              .catch(async () => {
                await page
                  .getByText("Activas", { exact: true })
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
      title: "Pestaña Histórico — métricas personales y tareas pasadas",
      goto: "/admin/inbox",
      // Clicamos la pestaña Histórico para capturar métricas + tareas pasadas.
      body: "<p>La pestaña <b>Histórico</b> muestra tus datos personales en dos bloques. Primero, <b>Mis métricas</b>: cuatro indicadores con tu <b>tiempo medio de aceptación</b>, tu <b>tiempo medio de ejecución</b>, el <b>porcentaje de tareas aprobadas a la primera</b> y tus <b>horas medias registradas</b>. Estas métricas alimentan las estimaciones futuras del Project Manager agente.</p><p>Debajo, <b>Tareas pasadas</b> lista las sesiones de trabajo que has entregado, cada una con la tarea, el proyecto y plan, la fecha de entrega, las horas registradas, los comentarios y el número de adjuntos. Si aún no has entregado nada, se muestra un mensaje indicándolo. Todos estos datos están limitados estrictamente a tu propio usuario y tenant.</p>",
      action: async (page) => {
        await page
          .getByRole("tab", { name: "Histórico" })
          .click()
          .catch(async () => {
            await page
              .getByRole("button", { name: "Histórico" })
              .click()
              .catch(async () => {
                await page
                  .getByText("Histórico", { exact: true })
                  .first()
                  .click()
                  .catch(() => {});
              });
          });
        await page.waitForTimeout(700);
      },
      fullPage: true,
    },
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  test.setTimeout(300_000);
  await login(page);
  await generateManual(page, manual);
});
