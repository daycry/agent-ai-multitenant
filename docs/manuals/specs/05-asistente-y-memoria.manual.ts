import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef } from "../lib/manual";
import { seededPhpProjectId } from "../lib/seed-helper";

const PID = seededPhpProjectId();

// GENERADO desde el workflow de redacción. Editable a mano; reejecutable.
//
// NOTA sobre las capturas: la pantalla de Identidad del asistente es una sola
// página larga con varias tarjetas (toggle, formulario, herramientas, modelo
// LLM, default de plataforma). Para que cada paso muestre la tarjeta de la que
// habla, esos pasos usan `fullPage: false` + scroll a la tarjeta en la
// `action`. Las acciones sobre superficies que siempre existen son tolerantes
// (`.catch(()=>{})`); las que requieren datos (p. ej. una memoria con
// duplicados detectados) NO llevan catch, de modo que sin datos el paso queda
// registrado honestamente como "no disponible" en el PDF.
const manual: ManualDef = {
  order: "05",
  slug: "05-asistente-y-memoria",
  title: "Asistente y Memoria",
  audience:
    "Administradores de tenant (Tenant Admin) y, en lo relativo al modelo por defecto de la plataforma, System Admin. También usuarios del tenant que consultan y crean memorias del equipo.",
  intro:
    "<p>Este manual cubre en profundidad el <b>Asistente personal</b> de la plataforma y la <b>Memoria de los agentes</b>. El asistente es un chat exclusivo para administradores del tenant que responde sobre el estado global de tu organización (proyectos, planes, actividad, presupuesto y carga de trabajo) apoyándose en herramientas de solo lectura sobre datos reales. Aprenderás a usar el chat e interpretar sus respuestas, a habilitar el asistente, a personalizar su identidad (nombre, avatar, tono, idioma, instrucciones), a elegir qué herramientas puede consultar y a configurar el modelo LLM que usa — incluido el modelo por defecto de la plataforma que administran los System Admin.</p><p>La segunda parte explica la <b>Memoria del equipo</b>: lo que el sistema (el componente Memorizer) y las personas persisten para futuros agentes, organizado por ámbito o <i>scope</i> (privada, equipo, proyecto y global) y por tipo (episódica o semántica). Verás cómo consultar, filtrar, crear y borrar memorias, cómo detectar y fusionar duplicados, cómo inspeccionar la memoria de un proyecto concreto y cómo ajustar el detector de similares en Ajustes.</p><p>Ambas piezas comparten una idea de diseño: la <b>honestidad de estado</b>. La interfaz nunca aparenta que algo funciona si no puede funcionar — un asistente sin modelo lo dice, una memoria sin embedding se marca «No disponible aún», y un detector de similares sin material aparece deshabilitado con su explicación.</p>",
  steps: [
    {
      title: "Asistente personal — el chat",
      goto: "/admin/assistant",
      body: "<p>Esta es la pantalla principal del <b>Asistente personal</b>, un chat pensado para que el administrador del tenant consulte el estado global de su organización sin recorrer pantalla a pantalla: proyectos, planes, actividad reciente, presupuesto y carga de los agentes humanos. En la cabecera, junto al título, hay un botón <b>Identidad</b> que lleva a la configuración del asistente (los siguientes pasos).</p><p>El área de conversación muestra inicialmente un estado vacío con un ejemplo de pregunta (por ejemplo «¿Qué planes tengo pendientes de aprobación?»). Para preguntar, escribe tu consulta en el campo de texto inferior y pulsa <b>Enviar</b> (el botón se activa solo cuando hay texto; los mensajes tienen un límite de longitud que el campo aplica automáticamente). Mientras el asistente trabaja verás el indicador <i>Pensando…</i>.</p><p>Tus mensajes aparecen a la derecha tal cual los escribes; las respuestas del asistente, a la izquierda, se renderizan con formato (tablas de estado, listas, negritas), porque el asistente responde en Markdown. Cada respuesta puede mostrar debajo dos metadatos útiles:</p><ul><li>Las etiquetas de las <b>herramientas</b> que consultó para responder (por ejemplo «Estado de proyectos» o «Resumen de planes») — te dicen en qué datos se apoya la respuesta.</li><li>El número de <b>rondas</b> de razonamiento que necesitó: una pregunta simple se resuelve en una ronda; una que cruza varios datos puede requerir varias.</li></ul><p>Las respuestas se basan exclusivamente en datos reales de <b>solo lectura</b> del tenant: el asistente no puede modificar nada (no aprueba planes, no lanza tareas), solo informar.</p><p>El asistente es exclusivo para administradores del tenant y debe estar habilitado. Si no tienes acceso, verás el mensaje «Asistente no disponible»; si eres administrador pero está desactivado, aparecerá un botón <b>Ir a Ajustes</b> para habilitarlo — es el primer paso de configuración, que vemos a continuación.</p>",
      fullPage: true,
    },
    {
      title: "Habilitar el asistente para tu organización",
      goto: "/admin/assistant/settings",
      body: "<p>La pantalla <b>Identidad del asistente</b> (accesible desde el botón <b>Identidad</b> del chat) es donde el administrador del tenant configura el asistente; en su cabecera hay un botón <b>Ir al chat</b> para volver a la conversación. La primera tarjeta, que enfoca esta captura, contiene el interruptor <b>Asistente habilitado</b>.</p><p>El interruptor enciende o apaga el asistente para <b>toda tu organización</b>:</p><ul><li>Mientras esté <b>desactivado</b>, nadie del tenant podrá usar el chat y el resto de la configuración de esta página permanece bloqueada con el aviso «Habilita el asistente para configurarlo».</li><li>Al <b>activarlo</b>, se desbloquean el formulario de identidad, la selección de herramientas y la tarjeta de modelo LLM, y el chat pasa a estar disponible para los administradores del tenant.</li></ul><p>El estado se muestra junto al interruptor («Activado» / «Desactivado») y cualquier error al guardar el cambio aparece debajo en rojo.</p><p>Conviene entender el modelo de permisos completo: el interruptor solo pueden verlo y accionarlo los <b>administradores del tenant</b>; los miembros normales no tienen acceso al asistente en ningún caso. Y el control es efectivo en el servidor, no solo en la interfaz: con el asistente apagado, el backend rechaza las peticiones de chat aunque alguien intente saltarse la pantalla.</p><p><b>Orden recomendado de puesta en marcha</b>: 1) habilitar aquí, 2) configurar identidad y herramientas (pasos siguientes), 3) elegir modelo LLM, 4) probar en el chat con una pregunta sencilla.</p>",
      action: async (page) => {
        await page
          .getByTestId("assistant-enabled-toggle")
          .scrollIntoViewIfNeeded()
          .catch(() => {});
        await page.waitForTimeout(400);
      },
      fullPage: false,
    },
    {
      title: "Identidad: nombre, avatar, tono, idioma e instrucciones",
      goto: "/admin/assistant/settings",
      body: "<p>Con el asistente habilitado, la tarjeta <b>Configuración</b> (enfocada en esta captura) muestra el formulario de identidad — la personalidad con la que el asistente se presenta y responde:</p><ul><li><b>Nombre</b> (obligatorio, hasta 120 caracteres): cómo se llama tu asistente. Es el campo más visible: aparece en sus presentaciones y da personalidad propia al asistente de cada organización.</li><li><b>URL del avatar</b> (opcional): una imagen para representarlo.</li><li><b>Tono</b> (obligatorio): una descripción corta del registro con el que debe responder, p. ej. «profesional y conciso» o «cercano y didáctico».</li><li><b>Idioma</b>: Español o English — el idioma en el que redactará sus respuestas.</li><li><b>Instrucciones adicionales</b> (opcional, hasta 8000 caracteres, con contador): un texto en Markdown que <b>sustituye el cuerpo del prompt por defecto</b> del asistente, conservando la identidad (nombre, tono e idioma). Es la vía para darle directrices propias de tu organización: qué priorizar, qué terminología usar, qué formatos de respuesta prefieres.</li></ul><p>Cada campo valida al enviar y muestra su error en rojo debajo si es inválido. Pulsa <b>Guardar</b> para aplicar; verás la confirmación «Identidad guardada».</p><p><b>Buena práctica</b>: empieza con las instrucciones adicionales vacías (el prompt por defecto está bien afinado) y añádelas solo cuando detectes patrones que quieras corregir — por ejemplo, «cuando informes de presupuesto, muestra siempre el % consumido sobre el total».</p>",
      action: async (page) => {
        await page
          .getByTestId("assistant-identity-form")
          .scrollIntoViewIfNeeded()
          .catch(async () => {
            await page
              .getByTestId("assistant-name")
              .scrollIntoViewIfNeeded()
              .catch(() => {});
          });
        await page.waitForTimeout(400);
      },
      fullPage: false,
    },
    {
      title: "Herramientas del asistente (qué datos puede consultar)",
      goto: "/admin/assistant/settings",
      body: "<p>Dentro del mismo formulario de identidad, el bloque <b>Herramientas disponibles</b> (enfocado en esta captura) define con casillas qué datos de <b>solo lectura</b> puede consultar el asistente para fundamentar sus respuestas:</p><ul><li><b>Estado de proyectos</b> — el inventario de proyectos del tenant y su situación.</li><li><b>Resumen de planes</b> — los planes y sus estados (borrador, pendiente de aprobación, en curso…).</li><li><b>Actividad reciente</b> — qué ha pasado últimamente en la organización.</li><li><b>Estado de presupuesto</b> — consumo frente a límites.</li><li><b>Carga de agentes humanos</b> — cuántas tareas tiene cada persona.</li><li><b>Asignaciones humanas pendientes</b> — tareas esperando aceptación de una persona.</li><li><b>«Recordar sobre ti»</b> — permite al asistente guardar datos personales duraderos que le cuentes (preferencias, contexto), para usarlos en conversaciones futuras.</li></ul><p>Cada casilla lleva su descripción en lenguaje llano. Desmarcar una herramienta la retira del repertorio del asistente: si le preguntas por algo que ya no puede consultar, te dirá que no dispone de ese dato en lugar de inventarlo.</p><p>Las etiquetas de herramientas que viste bajo las respuestas del chat (paso 1) se corresponden exactamente con este catálogo: son la trazabilidad de qué consultó el asistente en cada respuesta.</p><p><b>Caso de uso</b>: si tu organización prefiere que el asistente no exponga datos de presupuesto en el chat, basta desmarcar «Estado de presupuesto» y guardar — sin tocar permisos ni roles.</p>",
      action: async (page) => {
        await page
          .locator('[data-testid="assistant-tools"]')
          .scrollIntoViewIfNeeded()
          .catch(() => {});
        await page.waitForTimeout(400);
      },
      fullPage: false,
    },
    {
      title: "Modelo LLM del asistente",
      goto: "/admin/assistant/settings",
      body: "<p>Más abajo en la misma pantalla está la tarjeta <b>Modelo LLM</b> (enfocada en esta captura), donde el administrador del tenant elige qué proveedor y modelo de IA usa su asistente. Esta configuración es un <i>override</i>: si no defines nada, el asistente hereda el modelo por defecto de la plataforma.</p><p>Un texto de estado indica siempre la situación real del <b>modelo actual</b>: si es un override del tenant (mostrando proveedor y modelo), si está heredando el default de la plataforma, o si no hay ninguno configurado — en cuyo caso el asistente <b>no responderá</b> hasta que elijas uno o un System Admin configure el default.</p><p>Para fijar el override, selecciona primero un <b>Proveedor</b> en el desplegable y después un <b>Modelo</b> de su lista; si el proveedor lo soporta, aparece además un selector de <b>Razonamiento</b> (esfuerzo de razonamiento del modelo: Desactivado, low, medium, high…). Si el proveedor elegido no tiene modelos, el propio desplegable lo indica: hay que sincronizarlos desde «Proveedores LLM» (área del System Admin). Si no hay proveedores LLM activos en absoluto, la tarjeta te indica que pidas a un System Admin que configure uno.</p><p>Pulsa <b>Guardar modelo</b> para aplicar el override (se activa cuando hay proveedor y modelo elegidos); verás «Modelo guardado». Si ya tienes un override propio, aparece además el botón <b>Volver al modelo por defecto</b>, que lo elimina y vuelve a heredar el de la plataforma.</p><p><b>Cuándo usar un override</b>: cuando tu tenant quiere un modelo distinto al corporativo por coste, privacidad o calidad — por ejemplo, un modelo local de Ollama para que las consultas del asistente no salgan de la infraestructura.</p>",
      action: async (page) => {
        await page
          .getByTestId("assistant-model-card")
          .scrollIntoViewIfNeeded()
          .catch(() => {});
        await page.waitForTimeout(400);
      },
      fullPage: false,
    },
    {
      title: "Modelo por defecto de la plataforma (System Admin)",
      goto: "/admin/assistant/settings",
      body: "<p>Al final de la pantalla, y <b>solo visible para usuarios con rol System Admin</b>, está la tarjeta <b>Modelo por defecto de la plataforma</b> (enfocada en esta captura). Define el proveedor + modelo que heredan los asistentes de <b>todos los tenants</b> que no han configurado un override propio.</p><p>Su funcionamiento es paralelo al de la tarjeta anterior: un texto de estado muestra el default actual (o su ausencia), y los desplegables de <b>Proveedor</b>, <b>Modelo</b> y, si procede, <b>Razonamiento</b> permiten elegir la combinación. Los botones <b>Guardar default</b> y <b>Quitar default</b> aplican o eliminan la configuración.</p><p>Dos situaciones merecen atención:</p><ul><li>Si el default guardado <b>deja de ser válido</b> — porque el proveedor se desactivó o el modelo ya no está en su catálogo — la tarjeta muestra una advertencia para que lo corrijas: los tenants que heredaban ese modelo se quedan sin asistente operativo hasta entonces.</li><li>Si <b>no hay default</b> y un tenant tampoco tiene override, su asistente no responde; la tarjeta de modelo del tenant se lo indica con claridad.</li></ul><p>La cadena de resolución completa es: <b>override del tenant → default de plataforma → sin modelo (asistente inactivo)</b>. Como System Admin, configurar un default razonable es la manera de que los tenants tengan asistente funcional «de fábrica» sin obligar a cada administrador a entender proveedores y modelos.</p>",
      action: async (page) => {
        await page
          .getByTestId("assistant-default-model-card")
          .scrollIntoViewIfNeeded()
          .catch(() => {});
        await page.waitForTimeout(400);
      },
      fullPage: false,
    },
    {
      title: "Memoria del equipo — vista general y scopes",
      goto: "/admin/memories",
      body: "<p>La pantalla <b>Memoria del equipo</b> permite inspeccionar lo que el sistema (el componente <b>Memorizer</b>, que destila lecciones tras las ejecuciones de los agentes) y las personas persisten para futuros agentes. Cada memoria tiene un <b>scope</b> (ámbito) y un <b>tipo</b>, identificados con etiquetas de color.</p><p>Los cuatro scopes, de menor a mayor alcance, determinan <b>quién recuerda</b>:</p><ul><li><b>Privada</b> (gris) — de un usuario humano concreto. Un agente de IA ni la escribe ni la lee: es el cuaderno personal de una persona.</li><li><b>Equipo</b> (azul) — compartida por los agentes de un equipo; el lugar natural de las lecciones de trabajo en grupo.</li><li><b>Proyecto</b> (color de marca) — contexto de un proyecto concreto: decisiones, convenciones, particularidades del repo.</li><li><b>Global</b> (naranja, deliberadamente llamativo) — afecta a toda la organización: tocarla influye en todos los agentes del tenant.</li></ul><p>El <b>tipo</b> distingue la naturaleza del recuerdo: <b>Episódica</b> es un hecho concreto del pasado («el deploy del 12/06 falló por X»); <b>Semántica</b> es conocimiento durable («los tests de integración requieren la variable Y»). El Memorizer tiende a destilar lo episódico en lecciones semánticas.</p><p>Debajo del formulario de creación, la lista muestra las memorias existentes con un contador. Cada fila lleva sus etiquetas de scope y tipo, sus etiquetas (tags) en fuente monoespaciada, una marca <b>embedding</b> verde si tiene vector semántico (o «No disponible aún» con su explicación si no lo tiene — sin embedding no puede participar en la detección de duplicados) y, si procede, un contador «N similares» en amarillo. El botón de la papelera elimina la memoria (borrado lógico).</p><p>Cualquier usuario del tenant puede leer; las memorias <b>globales</b> solo puede crearlas y gestionarlas un administrador del tenant.</p>",
      fullPage: true,
    },
    {
      title: "Crear una memoria manual",
      goto: "/admin/memories",
      body: "<p>En la parte superior de la pantalla está el formulario <b>Nueva memoria manual</b> (enfocado en esta captura), la vía para que las personas aporten conocimiento directamente al sistema de memoria — sin esperar a que el Memorizer lo destile de una ejecución:</p><ul><li><b>Contenido</b>: el texto de la memoria, con soporte y previsualización de Markdown. Redáctalo pensando en el agente que lo leerá en el futuro: concreto, autocontenido y accionable.</li><li><b>Scope</b>: Privada, Equipo, Proyecto o Global. En esta captura hemos elegido «Equipo», lo que hace aparecer el selector de <b>Equipo</b> con buscador; al elegir «Proyecto» aparece el selector de <b>Proyecto</b> equivalente. Privada y Global no requieren destino adicional.</li><li><b>Tipo</b>: Semántica (conocimiento durable, la opción por defecto y la más habitual al escribir a mano) o Episódica (un hecho puntual con fecha).</li><li><b>Etiquetas</b>: palabras clave separadas por comas, útiles para reconocer memorias afines de un vistazo en la lista.</li></ul><p>El botón <b>Guardar memoria</b> se habilita cuando hay contenido y, si el scope lo exige, un equipo o proyecto seleccionado. Cualquier usuario puede crear memorias privadas, de equipo o de proyecto; las <b>globales</b> requieren rol de administrador del tenant (el servidor rechaza el intento en caso contrario y el error se muestra bajo el formulario).</p><p><b>Casos de uso típicos</b>: registrar una decisión de arquitectura que los agentes deben respetar (scope Proyecto), documentar una convención del equipo (scope Equipo) o fijar una política transversal de la organización (scope Global, con mesura: todos los agentes la cargarán).</p>",
      action: async (page) => {
        await page
          .locator('[data-testid="memory-scope-select"]')
          .selectOption("team_shared")
          .catch(() => {});
        await page
          .locator('[data-testid="memory-create-form"]')
          .scrollIntoViewIfNeeded()
          .catch(() => {});
        await page.waitForTimeout(400);
      },
      fullPage: false,
    },
    {
      title: "Filtrar la memoria por scope",
      goto: "/admin/memories",
      body: "<p>La cabecera de la lista incluye un <b>filtro por scope</b> en forma de control segmentado: <b>Todas</b>, <b>Privada</b>, <b>Equipo</b>, <b>Proyecto</b> y <b>Global</b>. En esta captura está seleccionado el filtro <b>Proyecto</b>, y el título de la tarjeta refleja el filtro activo junto con el número de memorias que cumplen el criterio.</p><p>El filtro consulta al servidor (no oculta filas localmente), así que el contador es fiable: si «Global (0)», tu organización no tiene ninguna memoria global. Cuando ninguna memoria coincide, la lista muestra el estado vacío «No hay memorias en este filtro».</p><p>Usos habituales del filtro:</p><ul><li><b>Auditar lo global</b>: revisar periódicamente el scope Global — son las memorias con más alcance y donde un contenido obsoleto hace más daño.</li><li><b>Depurar un comportamiento</b>: si los agentes de un proyecto repiten un error, filtra por Proyecto y comprueba qué están recordando (o qué les falta por recordar).</li><li><b>Higiene de memoria</b>: localizar memorias episódicas antiguas que ya no aportan y borrarlas, dejando solo las lecciones semánticas vigentes.</li></ul><p>Recuerda que la vista respeta los permisos de lectura: las memorias privadas de otras personas no aparecen — la privacidad del scope Privada es efectiva a nivel de datos, no cosmética.</p>",
      action: async (page) => {
        await page
          .getByTestId("memories-scope-project_shared")
          .click()
          .catch(() => {});
        await page.waitForTimeout(600);
      },
      fullPage: true,
    },
    {
      title: "Memorias similares: fusionar o descartar duplicados",
      goto: "/admin/memories",
      // Requiere una memoria con candidatos a duplicado (badge "N similares").
      // SIN catch: si no hay duplicados detectados en el entorno, el paso queda
      // registrado como "no disponible" (honesto) en lugar de capturar otra cosa.
      body: "<p>Cuando una memoria con embedding tiene otras memorias muy parecidas, su fila muestra el contador amarillo <b>«N similares»</b>. Al pulsarlo se abre el diálogo <b>Memorias similares</b> que ves en esta captura, la herramienta para mantener la memoria del tenant libre de duplicados.</p><p>El diálogo muestra arriba la <b>memoria actual</b> (la que sobrevivirá, llamada <i>target</i>) y debajo la lista de <b>candidatos a duplicado</b>, encontrados por similitud coseno entre embeddings. Cada candidato muestra su <b>porcentaje de similitud</b> y su contenido completo, con dos acciones:</p><ul><li><b>Fusionar</b> — combina el contenido del candidato dentro de la memoria actual y elimina el candidato. Úsalo cuando ambas memorias aportan matices distintos de la misma lección: no se pierde información.</li><li><b>Descartar</b> — elimina el candidato (borrado lógico) sin tocar la memoria actual. Úsalo cuando el candidato es redundante o ha quedado obsoleto.</li></ul><p>Si ningún candidato supera el umbral configurado, el diálogo lo indica («No hay candidatos por encima del umbral configurado»). Qué se considera «similar» — el umbral y cuántos candidatos se ofrecen — se ajusta en Ajustes → Memorias, que vemos en el último paso.</p><p><b>Por qué importa</b>: los duplicados diluyen el RAG de memoria (dos versiones casi idénticas compiten por los mismos huecos de contexto) y multiplican el mantenimiento. Una pasada periódica de fusión mantiene la memoria compacta y coherente.</p>",
      action: async (page) => {
        await page
          .locator('[data-testid^="memory-similar-badge-"]')
          .first()
          .click({ timeout: 5_000 });
        await page.waitForTimeout(600);
      },
      fullPage: true,
    },
    {
      title: "Memoria de un proyecto",
      goto: `/admin/projects/${PID}/memories`,
      body: "<p>Cada proyecto tiene su propia vista <b>Memoria del proyecto</b> (aquí, la del proyecto de ejemplo «Hello World PHP»), accesible desde el hub del proyecto. Muestra exclusivamente las memorias del scope <b>Proyecto</b> (project_shared) de ese proyecto: lo que el equipo «recuerda» al trabajar en él.</p><p>Es la sección RECORDAR de la pregunta «¿con qué cuenta este proyecto?»: cuando un agente ejecuta una tarea del proyecto, estas memorias forman parte del contexto que puede recuperar. La tarjeta muestra el contador total y cada fila lleva su etiqueta de tipo (<b>Episódica</b> o <b>Semántica</b>), sus etiquetas y su marca de <b>embedding</b> — con la misma honestidad de estado que la pantalla global: una memoria sin embedding se marca «No disponible aún» en lugar de fingir que participa en la detección de similares.</p><p>Esta vista es de <b>solo lectura</b>: para crear o borrar memorias se usa la pantalla global de <b>Memoria del equipo</b> (eligiendo scope «Proyecto» y el proyecto destino). Así la gestión queda centralizada y esta página conserva su propósito: inspeccionar rápidamente el contexto memorizado de un proyecto sin salir de él.</p><p><b>Cuándo consultarla</b>: antes de lanzar un plan importante (¿qué lecciones previas aplicarán los agentes?), al incorporar a alguien al proyecto (es un resumen destilado de su historia) o al depurar por qué un agente insiste en una convención — probablemente la está leyendo de aquí.</p>",
      fullPage: true,
    },
    {
      title: "Ajustes de memorias — detector de similares",
      goto: "/admin/settings/memories",
      body: "<p>Esta pantalla de <b>Memorias</b> (dentro de Ajustes) configura cómo el sistema detecta memorias similares para que el operador pueda fusionarlas o descartarlas — los controles que alimentan el diálogo de duplicados que vimos dos pasos atrás. La tarjeta <b>Detector de similares</b> contiene dos controles.</p><p>El primero es el <b>Umbral de similitud</b>, un deslizador que define a partir de qué grado de parecido (similitud coseno entre embeddings) dos memorias se consideran candidatas a duplicado; junto a la etiqueta se muestra el valor actual (por ejemplo 0,85) y debajo una breve descripción. Los rangos mínimo y máximo provienen del propio sistema. Un umbral <b>alto</b> (≈0,95) solo señala duplicados casi literales; uno <b>bajo</b> (≈0,70) agrupa memorias apenas emparentadas y genera ruido — si al revisar duplicados ves parejas que no lo son, súbelo.</p><p>El segundo es el <b>Número de candidatos</b> (límite), un campo numérico que indica cuántas memorias similares como máximo se ofrecen al revisar duplicados de una memoria dada.</p><p>Tras ajustar ambos valores, pulsa <b>Guardar</b>; verás «Guardando…» y luego «Guardado», o un mensaje de error en caso de fallo. Los cambios aplican a todo el tenant.</p><p>Importante — honestidad de estado: el detector solo funciona si al menos una memoria tiene <b>embedding</b> (vector semántico). Si ninguna lo tiene, los controles aparecen deshabilitados, la tarjeta se marca «No disponible aún» y una nota explica el motivo, para no aparentar que el filtrado está activo cuando no puede estarlo. Los embeddings se generan automáticamente cuando el servicio de embeddings de la plataforma está operativo.</p>",
      fullPage: true,
    },
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  // Manual ampliado (12 pasos entre asistente y memoria).
  test.setTimeout(600_000);
  await login(page);
  await generateManual(page, manual);
});
