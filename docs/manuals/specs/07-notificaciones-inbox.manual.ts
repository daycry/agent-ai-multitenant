import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef } from "../lib/manual";

// GENERADO desde el workflow de redacción. Editable a mano; reejecutable.
//
// Cada paso captura una pantalla DISTINTA: los pasos que documentan una
// pestaña la CLICAN antes del pantallazo (Canales/Preferencias/Plataforma,
// Activas/Histórico) y los que documentan un diálogo lo ABREN (Nuevo canal,
// Entregar tarea — este último solo existe si hay una asignación aceptada en
// el entorno). Todas las `action` son tolerantes (.catch) para no romper la
// generación si un selector no existe en este entorno.
const manual: ManualDef = {
  order: "07",
  slug: "07-notificaciones-inbox",
  title: "Notificaciones e Inbox",
  audience:
    "Administradores de tenant (Tenant Admin), System Admin y cualquier usuario con tareas humanas asignadas",
  intro:
    "<p>Este manual cubre todo lo relacionado con las <b>notificaciones</b> y las <b>bandejas de entrada</b> de la plataforma. Aprenderás a configurar los canales y preferencias de notificación en tres capas (plataforma, tenant y usuario), a consultar el histórico de notificaciones enviadas con su ciclo de vida completo y su reintento manual, y a gestionar tu bandeja personal de tareas humanas: aceptar, rechazar, entregar y escalar trabajo asignado, además de consultar tus métricas personales.</p><p>Las tres secciones son independientes: la <b>configuración de notificaciones</b> (qué transportes existen, qué canales concretos hay y qué eventos llegan por cada uno), la <b>bandeja de notificaciones</b> (el inbox in-app con el histórico de envíos y su estado) y la <b>bandeja personal de tareas</b> (tu inbox de validación y trabajo humano). Algunas acciones requieren permisos de Tenant Admin o System Admin; la propia interfaz muestra u oculta los controles según tu rol, pero el backend es siempre la fuente de verdad (RBAC + RLS): aunque un control fuera visible, la operación solo se aplica si tu rol lo permite.</p>",
  steps: [
    {
      title: "Configuración de Notificaciones — pestaña Canales",
      goto: "/admin/notifications",
      // Pantalla inicial: la cabecera "Notificaciones" + la pestaña Canales,
      // que es la abierta por defecto. La pulsamos por si otro paso la cambió.
      body: "<p>Esta pantalla, encabezada por <b>Notificaciones</b>, centraliza la configuración de canales y preferencias en <b>tres capas</b> que se corresponden con las tres pestañas situadas bajo el título:</p><ul><li><b>Canales</b>: pestaña abierta por defecto; define los canales concretos (Telegram, Email, Slack, etc.) de tu tenant o personales tuyos.</li><li><b>Preferencias</b>: reglas de enrutado que deciden qué eventos del sistema llegan por qué transporte.</li><li><b>Plataforma</b>: solo visible para el <b>System Admin</b>; habilita globalmente qué transportes pueden usarse en toda la instalación.</li></ul><p>El modelo de capas funciona en cascada: la plataforma decide qué transportes existen; el tenant (o cada usuario) crea canales concretos de esos transportes; y las preferencias afinan qué evento viaja por cada canal. Un tenant nunca puede crear un canal de un transporte que la plataforma no haya habilitado.</p><p>En la pestaña <b>Canales</b> ves la lista de los canales ya configurados. Cada canal se muestra como una tarjeta con su <b>nombre</b> y una serie de etiquetas: el <b>tipo de transporte</b> (p. ej. <code>telegram</code>), el <b>ámbito</b> (<code>tenant</code> = compartido por toda la organización, o <code>user</code> = personal, solo tuyo), el estado (<b>activo</b> / <b>inactivo</b>) y el estado del <b>secreto</b>: si tiene, se indica dónde vive (<i>Vault</i> o <i>cifrado en reposo</i>); si no, aparece la etiqueta <b>sin secreto</b> en ámbar como recordatorio de que probablemente falte el token.</p><p>Si todavía no hay canales, se muestra un mensaje invitando a pulsar <b>Nuevo canal</b>. Los Tenant Admin disponen, en cada tarjeta, de botones para <b>editar</b> (icono de lápiz) y <b>eliminar</b> (icono de papelera); al eliminar se pide confirmación explícita con el nombre del canal.</p>",
      action: async (page) => {
        await page
          .getByTestId("tab-channels")
          .click()
          .catch(async () => {
            await page
              .getByRole("tab", { name: "Canales" })
              .click()
              .catch(() => {});
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
      body: '<p>Pulsa <b>Nuevo canal</b> (arriba a la derecha, solo Tenant Admin) para abrir el diálogo de creación que ves en la captura. Los campos, en orden:</p><ul><li><b>Ámbito</b>: <i>Tenant (compartido)</i> — el canal avisa a toda la organización — o <i>Usuario (solo yo)</i> — un canal personal que solo te notifica a ti.</li><li><b>Transporte</b>: el desplegable solo ofrece los transportes que el System Admin ha habilitado en la pestaña Plataforma.</li><li><b>Nombre</b>: un nombre descriptivo (p. ej. «Ops bot»).</li><li><b>Config</b>: un objeto JSON con la configuración <b>no secreta</b> del transporte (p. ej. <code>{ "chat_id": "12345" }</code> para Telegram). Si el JSON no es válido, la interfaz lo advierte y no envía nada.</li><li><b>Secreto</b> (opcional): el token del bot, contraseña o clave del transporte, en un campo de tipo contraseña.</li></ul><p>El secreto recibe un tratamiento especial: se <b>cifra en reposo</b> antes de guardarse (o se referencia en Vault) y el sistema <b>nunca lo devuelve en claro</b> — ni en la API ni en la interfaz; después de guardar solo verás la etiqueta que indica que existe y dónde vive. Por eso, al <b>editar</b> un canal existente, dejar el campo Secreto vacío significa «conservar el actual». Al editar tampoco puedes cambiar el ámbito ni el transporte (esos dos campos desaparecen del formulario): si te equivocaste, elimina el canal y créalo de nuevo.</p><p>La casilla <b>Canal activo</b> determina si el canal está operativo: un canal inactivo conserva su configuración pero no recibe envíos. Confirma con <b>Crear</b> (alta) o <b>Guardar</b> (edición); el botón queda deshabilitado si falta el nombre. Los errores devueltos por el backend (por ejemplo, un transporte deshabilitado a nivel de plataforma) se muestran dentro del propio diálogo.</p>',
      action: async (page) => {
        await page
          .getByTestId("channel-create-button")
          .click()
          .catch(async () => {
            await page
              .getByRole("button", { name: "Nuevo canal" })
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
      body: "<p>La pestaña <b>Preferencias</b> muestra una <b>matriz de enrutado</b>: en las filas, los tipos de evento que el sistema emite hoy — <code>task_blocked</code> (una tarea se ha bloqueado y necesita intervención), <code>plan_approved</code> (un plan ha sido aprobado), <code>review_needed</code> (hay una revisión pendiente de humano) y <code>budget_alert</code> (se ha superado un umbral de presupuesto) — y en las columnas, los transportes que tu tenant tiene realmente configurados como canal (solo aparecen columnas de transportes con al menos un canal creado).</p><p>Cada celda es una casilla que activa o desactiva (opt-in / opt-out) la entrega de ese evento por ese transporte. La semántica por defecto es <b>todo activado</b>: si no existe ninguna regla para una combinación evento-transporte, la casilla aparece marcada y el evento se entrega. Al desmarcar una casilla se crea una regla de exclusión con ámbito de usuario; al volver a marcarla, la regla se actualiza. La regla más específica gana en el despachador.</p><p>Los Tenant Admin pueden cambiar las casillas; el resto de usuarios ven el estado de cada combinación en modo de solo lectura (<i>sí</i> / <i>no</i>). Si al guardar una regla el backend devuelve un error, se muestra bajo la matriz.</p><p>Si aún no hay ningún canal configurado, la matriz no puede mostrarse y la pestaña te invita a crear primero al menos un canal en la pestaña Canales.</p>",
      action: async (page) => {
        await page
          .getByTestId("tab-preferences")
          .click()
          .catch(async () => {
            await page
              .getByRole("tab", { name: "Preferencias" })
              .click()
              .catch(() => {});
          });
        await page.waitForTimeout(500);
      },
      fullPage: true,
    },
    {
      title: "Pestaña Plataforma — transportes habilitados globalmente (System Admin)",
      goto: "/admin/notifications",
      // Clicamos la pestaña Plataforma (solo presente para System Admin).
      body: "<p>Esta pestaña solo es visible para el <b>System Admin</b> — para cualquier otro rol ni siquiera aparece en la barra de pestañas. Presenta la lista de <b>transportes disponibles</b> en la plataforma (Telegram, Email, Slack, etc.), cada uno con una casilla que indica si está habilitado globalmente.</p><p>Marcar o desmarcar un transporte determina qué pueden hacer los tenants: un tenant <b>solo puede crear canales de los transportes habilitados aquí</b> — el desplegable de transporte del diálogo «Nuevo canal» se limita a esta lista. Deshabilitar un transporte no borra los canales ya existentes de los tenants, pero impide crear nuevos de ese tipo.</p><p>Tras ajustar las casillas, pulsa <b>Guardar</b> para aplicar los cambios globalmente; si el backend rechaza la operación, el error se muestra junto al botón. Este es el primer eslabón de la cadena de tres capas: conviene habilitar únicamente los transportes que la organización realmente opera (con sus credenciales y bots dados de alta), para que los tenants no configuren canales que nunca podrán entregar.</p>",
      action: async (page) => {
        await page
          .getByTestId("tab-platform")
          .click()
          .catch(async () => {
            await page
              .getByRole("tab", { name: "Plataforma" })
              .click()
              .catch(() => {});
          });
        await page.waitForTimeout(500);
      },
      fullPage: true,
    },
    {
      title: "Bandeja de notificaciones — histórico, marcar leído, reintentar y paginar",
      goto: "/admin/notifications/inbox",
      // Una sola vista (sin pestañas ni diálogos): listado + barra de filtros +
      // paginación.
      body: "<p>La <b>Bandeja de notificaciones</b> es el inbox in-app: muestra el histórico de notificaciones enviadas a los canales de tu tenant, con su estado y la posibilidad de reintento manual. Cada notificación atraviesa un <b>ciclo de vida</b> que la bandeja refleja con colores: <code>queued</code> (encolada, pendiente de envío), <code>sent</code> (enviada al transporte), <code>delivered</code> (entrega confirmada), <code>failed</code> (fallo puntual), <code>retrying</code> (reintentando automáticamente) y <code>dead_letter</code> (fallida definitivamente tras agotar los reintentos automáticos).</p><p>En la barra superior encontrarás: un contador de <b>sin leer</b>, un filtro por <b>Estado</b> (Todos o cualquiera de los seis estados anteriores), una casilla <b>Solo sin leer</b> y el botón <b>Marcar todo como leído</b> (deshabilitado cuando no queda nada pendiente). Cambiar cualquiera de los filtros reinicia la paginación a la primera página.</p><p>Cada entrada de la lista indica el <b>tipo de evento</b> (p. ej. <code>task_blocked</code>), el <b>transporte</b> por el que salió, el <b>estado</b> con su color, el número de <b>intento</b> si es mayor que 1, el posible <b>mensaje de error</b> del transporte y la fecha de creación. Las notificaciones no leídas se destacan con un punto y un borde lateral de color, y ofrecen el botón <b>Marcar leído</b>. El marcador de leído es <b>por usuario</b>: cada administrador tiene su propio inbox, y marcar algo como leído no afecta a los demás. Por RLS, nunca verás notificaciones de otro tenant.</p><p>Las notificaciones en estado <b>dead_letter</b> muestran, solo para Tenant Admin, un botón <b>Reintentar</b> que vuelve a encolar el envío reutilizando el canal original — útil tras corregir la causa del fallo (p. ej. renovar el token del bot en el canal). El reintento crea un nuevo registro enlazado al original.</p><p>Al pie de la lista hay un contador de resultados (p. ej. <i>1–25 de 120</i>) y los botones <b>Anterior</b> y <b>Siguiente</b> para navegar entre páginas de 25 elementos.</p>",
      fullPage: true,
    },
    {
      title: "Bandeja personal — Tareas asignadas a mí (pestaña Activas y sus acciones)",
      goto: "/admin/inbox",
      // Clicamos la pestaña Activas (default) para asegurar la vista de tarjetas
      // con sus acciones contextuales.
      body: "<p>La pantalla <b>Tareas asignadas a mí</b> es la bandeja personal de cualquier usuario logueado — no es exclusiva de administradores. Lista las tareas humanas que tienes asignadas y activas, y se organiza en dos pestañas: <b>Activas</b> e <b>Histórico</b>. Solo ves tus propias asignaciones, nunca las de otros usuarios ni de otros tenants (el filtrado se impone en el servidor).</p><p>En <b>Activas</b>, cada tarea aparece como una tarjeta con su <b>título</b>, una breve descripción, el <b>proyecto</b> y <b>plan</b> asociados, el <b>estado</b> de la tarea (Asignada, En curso, En revisión) y, si procede, el <b>plazo de aceptación</b> en formato relativo (p. ej. <i>Aceptar en ~5 h</i>, o <i>Plazo de aceptación vencido</i> destacado en rojo si ya expiró).</p><p>Las acciones de cada tarjeta dependen del estado de la asignación:</p><ul><li><b>Aceptar</b> (solo mientras está pendiente de aceptación): confirmas que te haces cargo; la asignación pasa a aceptada y la tarea entra <i>en curso</i>.</li><li><b>Rechazar</b> (solo pendiente de aceptación): abre un diálogo donde debes escribir una <b>justificación obligatoria</b>; la tarea pasa a bloqueada para que un administrador la reasigne, y tu justificación queda registrada.</li><li><b>Marcar completada</b> (solo tras aceptar): abre el formulario de entrega estructurado (siguiente paso).</li><li><b>Escalar al admin</b> (disponible siempre): abre un diálogo con un <b>motivo opcional</b>; bloquea la tarea y notifica a los administradores del tenant. Úsalo cuando estás bloqueado por algo que no puedes resolver tú.</li></ul><p>Si una acción falla, el error del servidor se muestra dentro de la propia tarjeta afectada, sin interrumpir el resto. Si no tienes tareas, se muestra un mensaje indicando que cuando te asignen una tarea humana aparecerá aquí.</p>",
      action: async (page) => {
        await page
          .getByTestId("inbox-tab-active")
          .click()
          .catch(async () => {
            await page
              .getByRole("tab", { name: "Activas" })
              .click()
              .catch(() => {});
          });
        await page.waitForTimeout(500);
      },
      fullPage: true,
    },
    {
      title: "Entregar una tarea — formulario de entrega estructurado",
      goto: "/admin/inbox",
      // Abrimos el formulario de entrega pulsando "Marcar completada" en la
      // primera tarjeta aceptada. Si en este entorno no hay ninguna asignación
      // aceptada, la captura muestra la bandeja (la explicación sigue valiendo).
      body: "<p>Al pulsar <b>Marcar completada</b> en una tarea aceptada se abre el diálogo <b>Entregar tarea</b>: el formulario de entrega estructurado con el que reportas el trabajo realizado. Al enviarlo, la tarea pasa a <i>revisión</i> y el sistema registra una <b>sesión de trabajo</b> con tus datos, que alimenta tanto el histórico como tus métricas personales.</p><p>El formulario recoge tres bloques:</p><ul><li><b>Resultado / output</b>: un área de texto con vista previa Markdown donde describes qué hiciste y el resultado. Es el campo recomendado — es lo primero que leerá quien revise la tarea.</li><li><b>Adjuntos</b>: evidencias del trabajo. Con los botones <b>Añadir URL</b>, <b>Añadir archivo</b> y <b>Añadir captura</b> agregas filas; cada una tiene un tipo (URL / Archivo / Captura), una <b>etiqueta</b> descriptiva y su destino: una URL para los enlaces, o una referencia de almacén de objetos (p. ej. <code>minio://deliverables/informe.pdf</code>) para archivos y capturas. Un contador indica cuántos adjuntos están completos (etiqueta + destino); los incompletos no se envían. Cada fila puede eliminarse con su papelera.</li><li><b>Horas trabajadas</b> (opcional): un número no negativo (admite decimales, paso 0,25) que alimenta el cálculo de coste humano del plan. Si escribes un valor inválido, el formulario lo señala y bloquea el envío.</li></ul><p>El botón <b>Entregar y enviar a revisión</b> solo se habilita cuando hay un resultado escrito <b>o</b> al menos un adjunto completo: entregar «nada» no aporta trazabilidad y el formulario lo impide. Los errores del servidor se muestran dentro del diálogo; <b>Cancelar</b> cierra sin enviar.</p><p>Tras la entrega, la tarea desaparece de Activas (queda <i>en revisión</i> para el flujo de validación humana del plan) y la sesión de trabajo aparece en tu pestaña Histórico con sus horas, comentarios y número de adjuntos.</p>",
      action: async (page) => {
        await page
          .getByTestId("inbox-tab-active")
          .click()
          .catch(() => {});
        await page.waitForTimeout(400);
        await page
          .locator('[data-testid^="inbox-complete-"]')
          .first()
          .click()
          .catch(() => {});
        await page.waitForTimeout(700);
      },
      fullPage: true,
    },
    {
      title: "Pestaña Histórico — métricas personales y tareas pasadas",
      goto: "/admin/inbox",
      // Clicamos la pestaña Histórico para capturar métricas + tareas pasadas.
      body: "<p>La pestaña <b>Histórico</b> muestra tus datos personales en dos bloques, ambos estrictamente limitados a tu propio usuario y tenant (el servidor no devuelve datos de nadie más).</p><p>Primero, <b>Mis métricas</b>: cuatro indicadores calculados sobre tu actividad real:</p><ul><li><b>Tiempo medio de aceptación</b>: cuánto tardas de media en aceptar una asignación desde que te llega (con el número de asignaciones aceptadas como referencia).</li><li><b>Tiempo medio de ejecución</b>: la duración media de tus sesiones de trabajo entregadas.</li><li><b>Aprobadas a la primera</b>: el porcentaje de tus tareas que superan la revisión sin rechazos.</li><li><b>Horas medias registradas</b>: la media de horas que declaras por entrega.</li></ul><p>Mientras no haya datos suficientes, cada indicador muestra <i>Sin datos aún</i>. Estas métricas no son decorativas: alimentan las estimaciones futuras del Project Manager agente, que las usa para calibrar plazos y asignaciones.</p><p>Debajo, <b>Tareas pasadas</b> lista las sesiones de trabajo que has entregado, cada una con la tarea, el proyecto y plan, la fecha de entrega, las <b>horas registradas</b> (como etiqueta destacada), los comentarios que escribiste al entregar y el número de adjuntos aportados. Si aún no has entregado nada, se muestra un mensaje indicándolo.</p>",
      action: async (page) => {
        await page
          .getByTestId("inbox-tab-history")
          .click()
          .catch(async () => {
            await page
              .getByRole("tab", { name: "Histórico" })
              .click()
              .catch(() => {});
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
