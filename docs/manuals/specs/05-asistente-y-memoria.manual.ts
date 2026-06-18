import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef } from "../lib/manual";

// GENERADO desde el workflow de redacción. Editable a mano; reejecutable.
const manual: ManualDef = {
  order: "05",
  slug: "05-asistente-y-memoria",
  title: "Asistente y Memoria",
  audience:
    "Administradores de tenant (Tenant Admin) y, en lo relativo al modelo por defecto de la plataforma, System Admin. También usuarios del tenant que consultan y crean memorias del equipo.",
  intro:
    "<p>Este manual cubre el <b>Asistente personal</b> de la plataforma y la <b>Memoria de los agentes</b>. El asistente es un chat exclusivo para administradores del tenant que responde sobre el estado global de tu organización (proyectos, planes, actividad, presupuesto y carga de trabajo) apoyándose en herramientas de solo lectura. Aprenderás a usar el chat, a habilitar el asistente, a personalizar su identidad (nombre, tono, idioma, instrucciones y herramientas) y a elegir el modelo LLM que usa.</p><p>La segunda parte explica la <b>Memoria del equipo</b>: lo que el sistema (el componente Memorizer) y las personas persisten para futuros agentes, organizado por ámbito o <i>scope</i> (privada, equipo, proyecto y global) y por tipo (episódica o semántica). Verás cómo consultar, filtrar, crear y borrar memorias, detectar y fusionar duplicados, y cómo ajustar el detector de similares.</p>",
  steps: [
    {
      title: "Asistente personal — el chat",
      goto: "/admin/assistant",
      body: "<p>Esta es la pantalla principal del <b>Asistente personal</b>, un chat pensado para que el administrador del tenant consulte el estado global de su organización: proyectos, planes, actividad, presupuesto y carga de agentes. En la cabecera, junto al título, hay un botón <b>Identidad</b> que lleva a la configuración del asistente.</p><p>El área de conversación muestra inicialmente un estado vacío con un ejemplo de pregunta (por ejemplo «¿Qué planes tengo pendientes de aprobación?»). Para preguntar, escribe tu consulta en el campo de texto inferior y pulsa <b>Enviar</b> (el botón se activa solo cuando hay texto). Mientras el asistente trabaja verás el indicador <i>Pensando…</i>.</p><p>Cada respuesta del asistente puede mostrar debajo unas etiquetas con las <b>herramientas</b> que consultó para responderte (por ejemplo «Estado de proyectos» o «Resumen de planes») y el número de <b>rondas</b> de razonamiento que usó. Las respuestas se basan en datos reales de solo lectura del tenant.</p><p>El asistente es exclusivo para administradores del tenant y debe estar habilitado. Si no tienes acceso, verás el mensaje «Asistente no disponible»; si eres administrador pero está desactivado, aparecerá un botón <b>Ir a Ajustes</b> para habilitarlo.</p>",
      fullPage: true,
    },
    {
      title: "Habilitar el asistente e identidad",
      goto: "/admin/assistant/settings",
      body: "<p>Esta pantalla de <b>Identidad del asistente</b> es donde el administrador del tenant configura el asistente. En la cabecera hay un botón <b>Ir al chat</b> para volver a la conversación.</p><p>La primera tarjeta contiene el interruptor <b>Asistente habilitado</b>: actívalo para encender el asistente personal en toda tu organización. Mientras esté desactivado, nadie del tenant podrá usarlo y el resto de la configuración permanece bloqueada con el aviso «Habilita el asistente para configurarlo».</p><p>Una vez habilitado, la tarjeta <b>Configuración</b> muestra el formulario de identidad con estos campos: <b>Nombre</b> (obligatorio, hasta 120 caracteres), <b>URL del avatar</b> (opcional), <b>Tono</b> (obligatorio, p.ej. «profesional y conciso»), <b>Idioma</b> (Español o English) e <b>Instrucciones adicionales</b> (un texto opcional de hasta 8000 caracteres que sustituye el cuerpo del prompt por defecto, conservando nombre, tono e idioma; un contador muestra los caracteres usados).</p><p>Más abajo, en <b>Herramientas disponibles</b>, marcas con casillas qué datos de solo lectura puede consultar el asistente: Estado de proyectos, Resumen de planes, Actividad reciente, Estado de presupuesto, Carga de agentes humanos, Asignaciones humanas pendientes y «Recordar sobre ti» (que permite al asistente guardar datos personales duraderos). Pulsa <b>Guardar</b> para aplicar; verás «Identidad guardada» al terminar. Los campos inválidos muestran su error en rojo.</p>",
      fullPage: true,
    },
    {
      title: "Modelo LLM del asistente",
      goto: "/admin/assistant/settings",
      body: "<p>En la misma pantalla de Identidad, más abajo, está la tarjeta <b>Modelo LLM</b>, donde el administrador del tenant elige qué proveedor y modelo de IA usa su asistente. Esta configuración es un <i>override</i> que, si no defines nada, hereda el modelo por defecto de la plataforma.</p><p>Un texto de estado indica el <b>modelo actual</b>: si es un override del tenant, si está heredando el modelo por defecto de la plataforma, o si no hay ninguno configurado (en ese caso el asistente no responderá hasta elegir uno). Para fijarlo, selecciona primero un <b>Proveedor</b> en el desplegable y después un <b>Modelo</b> de la lista. Si el proveedor no tiene modelos, el sistema indica que hay que sincronizarlos desde «Proveedores LLM» (área del System Admin).</p><p>Pulsa <b>Guardar modelo</b> para aplicar el override (el botón se activa cuando hay proveedor y modelo elegidos). Si ya tienes un override propio, aparece además el botón <b>Volver al modelo por defecto</b> para eliminarlo y heredar de nuevo el de la plataforma. Si no hay proveedores LLM activos, se te indicará que pidas a un System Admin que configure uno.</p><p>Solo los usuarios con rol System Admin verán, al final de la pantalla, la tarjeta <b>Modelo por defecto de la plataforma</b>: ahí se fija el modelo (proveedor + modelo) que heredan los asistentes de los tenants que no han elegido uno propio, con botones <b>Guardar default</b> y <b>Quitar default</b>. Si el default guardado deja de ser válido (el proveedor o el modelo ya no existen), se muestra una advertencia.</p>",
      fullPage: true,
    },
    {
      title: "Memoria del equipo",
      goto: "/admin/memories",
      body: "<p>La pantalla <b>Memoria del equipo</b> permite inspeccionar lo que el sistema (el componente Memorizer) y las personas persisten para futuros agentes. Cada memoria tiene un <b>scope</b> (ámbito) y un <b>tipo</b>, identificados con etiquetas de color: scope <i>Privada</i> (uso individual), <i>Equipo</i> (compartida con el team), <i>Proyecto</i> (contexto del proyecto) y <i>Global</i> (afecta a toda la organización, marcada en naranja). El tipo puede ser <i>Episódica</i> (un hecho concreto del pasado) o <i>Semántica</i> (conocimiento durable).</p><p>Arriba se encuentra el formulario <b>Nueva memoria manual</b>: escribe el <b>Contenido</b> (admite Markdown), elige <b>Scope</b> y <b>Tipo</b>, y añade <b>Etiquetas</b> separadas por comas. Si eliges scope «Equipo» aparece un selector de <b>Equipo</b>; si eliges «Proyecto», un selector de <b>Proyecto</b>. Cualquier usuario puede crear memorias privadas, de equipo o de proyecto; solo un administrador del tenant puede crear memorias globales. Pulsa <b>Guardar memoria</b> para crearla.</p><p>Debajo, la lista muestra las memorias existentes con un contador y un <b>filtro por scope</b> (Todas, Privada, Equipo, Proyecto, Global). Cada fila lleva sus etiquetas de scope y tipo, una marca <b>embedding</b> si tiene vector semántico (o «No disponible aún» si no lo tiene) y, si procede, un contador «N similares» en amarillo. El botón de la papelera elimina la memoria (borrado lógico).</p><p>Al pulsar el contador de similares se abre el diálogo <b>Memorias similares</b>, que lista candidatos a duplicado por similitud coseno, con su porcentaje. Allí puedes <b>Fusionar</b> (combina el contenido del candidato en la memoria actual, que sobrevive) o <b>Descartar</b> (elimina el candidato).</p>",
      fullPage: true,
    },
    {
      title: "Ajustes de memorias — detector de similares",
      goto: "/admin/settings/memories",
      body: "<p>Esta pantalla de <b>Memorias</b> (dentro de Ajustes) configura cómo el sistema detecta memorias similares para que el operador pueda fusionarlas o descartarlas. La tarjeta <b>Detector de similares</b> contiene dos controles.</p><p>El primero es el <b>Umbral de similitud</b>, un deslizador (slider) que define a partir de qué grado de parecido dos memorias se consideran candidatas a duplicado; junto a la etiqueta se muestra el valor actual (por ejemplo 0,85) y debajo una breve descripción. Los rangos mínimo y máximo provienen del propio sistema.</p><p>El segundo es el <b>Número de candidatos</b> (límite), un campo numérico que indica cuántas memorias similares como máximo se ofrecen al revisar duplicados. Tras ajustar ambos valores, pulsa <b>Guardar</b>; verás «Guardando…» y luego «Guardado», o un mensaje de error en caso de fallo.</p><p>Importante: el detector solo funciona si al menos una memoria tiene <b>embedding</b> (vector semántico). Si ninguna lo tiene, los controles aparecen deshabilitados y se marca «No disponible aún» con una nota explicativa, para no aparentar que el filtrado está activo cuando no puede estarlo.</p>",
      fullPage: true,
    },
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  test.setTimeout(300_000);
  await login(page);
  await generateManual(page, manual);
});
