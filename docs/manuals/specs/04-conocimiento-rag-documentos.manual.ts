import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef } from "../lib/manual";
import { seededPhpProjectId } from "../lib/seed-helper";

const PID = seededPhpProjectId();

// GENERADO desde el workflow de redacción. Editable a mano; reejecutable.
//
// NOTA sobre las capturas: la mayoría de pasos navegan a la MISMA ruta
// (`/admin/knowledge-bases`), que es una pantalla con listado + diálogos.
// Para que cada paso capture una pantalla DISTINTA y útil, cada paso que
// documenta un diálogo o un panel desplegable abre esa superficie con una
// `action` (clic) ANTES del pantallazo. Las acciones sobre superficies que
// SIEMPRE existen son tolerantes (`.catch(()=>{})`); las que requieren datos
// sembrados (p. ej. un documento ya subido para ver su ingestión) NO llevan
// catch: si el dato no existe, el paso queda registrado honestamente como
// "no disponible" en el PDF en lugar de mostrar una captura engañosa.
const manual: ManualDef = {
  order: "04",
  slug: "04-conocimiento-rag-documentos",
  title: "Conocimiento (RAG) y Documentos",
  audience:
    "Administradores de tenant (tenant_admin) y operadores que gestionan el conocimiento que consumen los agentes",
  intro:
    "<p>Este manual explica cómo gestionar el <b>conocimiento</b> de la plataforma: las <b>Knowledge Bases (KBs)</b>, sus <b>categorías</b> y los <b>documentos</b> que se suben e indexan para alimentar el RAG (Retrieval-Augmented Generation) de los agentes.</p><p>Una Knowledge Base es un contenedor de documentos indexados sobre un modelo de embedding fijo. Los documentos se suben, se procesan en un pipeline de ingestión (escaneo antivirus → parseo con Docling → troceado en fragmentos → embeddings → persistencia en pgvector) y, una vez indexados, sus fragmentos quedan disponibles para que los agentes recuperen contexto relevante durante sus ejecuciones. Las KBs se organizan en categorías y se conceden (grant) a proyectos y agentes, que son quienes finalmente las consumen.</p><p>Cubre las pantallas de listado y gestión de KBs, la gestión de categorías, el seguimiento en vivo de la ingestión de cada documento, la vista de citas con localización en página, la sub-sección de Knowledge Bases de cada proyecto y el acceso a documentos a través de los proyectos. La mayoría de acciones de creación, edición y borrado requieren rol <b>tenant_admin</b>.</p>",
  steps: [
    {
      title: "Listado de Knowledge Bases",
      goto: "/admin/knowledge-bases",
      body: "<p>Esta es la pantalla principal de <b>Knowledge Bases</b> del tenant. Cada KB agrupa documentos indexados y se asigna (grant) a uno o varios proyectos. El listado se presenta <b>agrupado por categoría</b>: primero las categorías built-in (sembradas por la plataforma), luego las del tenant y, al final, un grupo <b>Sin categoría</b>; cada cabecera muestra el color de la categoría y el número de KBs.</p><ul><li>Cada KB se muestra como una tarjeta con su nombre, una etiqueta <b>Built-in</b> si procede, el modelo de embedding utilizado y su descripción.</li><li>Al pulsar la fila (flecha a la izquierda) la tarjeta se <b>despliega</b> y aparece el panel de documentos de esa KB para subir y gestionar archivos sin salir de la pantalla.</li><li>El botón <b>Asignaciones</b> abre un diálogo con los proyectos y agentes que tienen acceso a la KB.</li></ul><p>Arriba a la derecha, con rol tenant_admin, dispone del botón <b>Categorías</b> (atajo a la gestión de categorías) y del botón <b>Crear KB</b>. Las KBs built-in son de solo lectura: solo permiten Grant y Asignaciones, sin editar ni borrar.</p><p><b>Modelo mental</b>: la KB es el contenedor; los documentos son el contenido; el grant es el permiso. Un agente solo recupera conocimiento de las KBs que su proyecto (o él mismo) tiene concedidas — subir un documento no lo hace visible para nadie hasta que la KB esté granted.</p>",
      fullPage: true,
    },
    {
      title: "Crear una Knowledge Base",
      goto: "/admin/knowledge-bases",
      // Abre el diálogo "Crear Knowledge Base" pulsando el botón "Crear KB".
      action: async (page) => {
        await page
          .getByRole("button", { name: "Crear KB" })
          .click()
          .catch(async () => {
            await page
              .locator('[data-testid="kbs-create-button"]')
              .click()
              .catch(() => {});
          });
        await page.waitForTimeout(500);
      },
      body: "<p>Pulse <b>Crear KB</b> (arriba a la derecha) para abrir el diálogo de creación. Una KB es un contenedor de documentos indexados; tras crearla se despliega en la lista para subir documentos y se le da acceso (grant) a los proyectos o agentes que la consumirán.</p><ul><li><b>Nombre</b> (obligatorio, máximo 120 caracteres): identifica la KB en el listado. Use nombres descriptivos del contenido («Documentación API interna», «Normativa PCI-DSS»), no del proyecto que la usará.</li><li><b>Categoría</b> (opcional): un selector con las categorías built-in y las del tenant; sirve para organizar el listado. El botón <b>+</b> contiguo abre un mini-diálogo para crear una categoría nueva sin salir del flujo (pidiendo slug, nombre y color).</li><li><b>Descripción</b> (opcional): admite formato Markdown. Es útil describir aquí el alcance y la fuente de los documentos, para que otros administradores sepan qué esperar de la KB.</li></ul><p>El <b>modelo de embedding</b> se asigna automáticamente al valor por defecto de la plataforma y <b>no se puede cambiar después</b>: todos los fragmentos de una KB deben vectorizarse con el mismo modelo, o las búsquedas por similitud dejarían de ser comparables. Si en el futuro necesita otro modelo, la vía es crear una KB nueva y reindexar en ella los documentos.</p><p>Pulse <b>Crear KB</b> para guardar; si hay error se mostrará un mensaje en el propio diálogo. La KB recién creada aparece en su grupo de categoría, lista para recibir documentos y grants.</p>",
      fullPage: true,
    },
    {
      title: "Conceder acceso (Grant) de una KB a un proyecto",
      goto: "/admin/knowledge-bases",
      // Abre el diálogo "Dar acceso a un proyecto" pulsando el botón "Grant"
      // de la primera KB del listado.
      action: async (page) => {
        await page
          .getByRole("button", { name: "Grant" })
          .first()
          .click()
          .catch(async () => {
            await page
              .locator('[data-testid^="kb-grant-"]')
              .first()
              .click()
              .catch(() => {});
          });
        await page.waitForTimeout(500);
      },
      body: "<p>Desde cada tarjeta del listado (con rol tenant_admin) el botón <b>Grant</b> abre el diálogo <i>Dar acceso a un proyecto</i>. Seleccione el proyecto destino con el buscador; tras conceder el acceso, ese proyecto verá la KB en su sub-sección Knowledge Bases y podrá subir documentos. Puede repetir el grant para varios proyectos sin cerrar el diálogo.</p><p>El grant es la pieza que conecta el conocimiento con quien lo consume: los agentes que trabajan en un proyecto solo recuperan contexto de las KBs concedidas a ese proyecto (más las concedidas a ellos individualmente desde su ficha). Sin grant, la KB existe pero nadie la lee.</p><p>Junto a Grant, cada tarjeta dispone (solo en KBs no built-in) de:</p><ul><li><b>Editar</b> (icono lápiz): permite cambiar nombre, categoría y descripción. El modelo de embedding se muestra pero es de solo lectura: para usar otro modelo hay que crear una KB nueva y reindexar.</li><li><b>Borrar</b> (icono papelera): acción <b>irreversible</b>. Borra la KB, todos sus documentos indexados y los grants a proyectos (los archivos en el almacenamiento de objetos no se tocan). Para confirmar debe teclear exactamente el nombre de la KB.</li></ul><p>Las KBs built-in no muestran los botones de editar ni borrar, ya que el backend las protege como de solo lectura para el tenant.</p>",
      fullPage: true,
    },
    {
      title: "Ver asignaciones (proyectos y agentes con acceso)",
      goto: "/admin/knowledge-bases",
      // Abre el diálogo "Asignaciones" de la primera KB del listado.
      action: async (page) => {
        await page
          .getByRole("button", { name: "Asignaciones" })
          .first()
          .click()
          .catch(async () => {
            await page
              .locator('[data-testid^="kb-assignments-"]')
              .first()
              .click()
              .catch(() => {});
          });
        await page.waitForTimeout(500);
      },
      body: "<p>El botón <b>Asignaciones</b> de cada KB abre un diálogo que muestra a quién se ha concedido acceso, en dos secciones:</p><ul><li><b>Proyectos</b>: lista de proyectos con grant. Cada fila tiene un botón de papelera para <b>revocar</b> el acceso de ese proyecto.</li><li><b>Agentes</b>: lista de agentes con grant, mostrando su rol y su ámbito (scope) mediante etiquetas. Cada fila permite revocar el acceso del agente.</li></ul><p>Si la KB no está concedida a nadie, el diálogo lo indica y recuerda cómo conceder acceso: a un proyecto con el botón <b>Grant</b> de la lista, y a un agente desde su detalle (sección Knowledge Bases → Grant KB). Estas asignaciones son las que determinan qué agentes pueden recuperar el conocimiento de la KB durante el RAG.</p><p>Este diálogo es también su herramienta de <b>auditoría de acceso</b>: antes de subir documentación sensible a una KB, compruebe aquí quién la verá. Revocar un grant surte efecto inmediato — los agentes del proyecto revocado dejan de recuperar fragmentos de esa KB en sus siguientes ejecuciones.</p><p><b>Buena práctica</b>: mantenga KBs separadas por nivel de confidencialidad en lugar de mezclar documentos públicos y sensibles en una sola; el grant opera a nivel de KB completa, no de documento.</p>",
      fullPage: true,
    },
    {
      title: "Subir documentos a una KB e indexarlos",
      goto: "/admin/knowledge-bases",
      // Despliega la primera KB (panel de documentos) y abre el diálogo de
      // subida pulsando "Subir documento".
      action: async (page) => {
        await page
          .locator('[data-testid^="kb-toggle-docs-"]')
          .first()
          .click()
          .catch(async () => {
            await page
              .getByRole("button", { name: /knowledge base|kb|library/i })
              .first()
              .click()
              .catch(() => {});
          });
        await page.waitForTimeout(600);
        await page
          .getByRole("button", { name: "Subir documento" })
          .first()
          .click()
          .catch(async () => {
            await page
              .locator('[data-testid^="kb-docs-upload-open-"]')
              .first()
              .click()
              .catch(() => {});
          });
        await page.waitForTimeout(500);
      },
      body: "<p>Para gestionar documentos, despliegue una KB en el listado (pulsando su fila) para abrir su <b>panel de documentos</b>. El panel muestra el número de documentos y un botón <b>Subir documento</b> (rol tenant_admin) que abre el diálogo de subida.</p><ul><li>El diálogo de subida acepta un <b>Archivo</b> (formatos admitidos: .pdf, .docx, .md, .txt, .html, .wav, .mp3 — los de audio se transcriben durante la ingestión) y un <b>Título</b> opcional (por defecto se usa el nombre del archivo).</li><li>Al subir, el documento entra en la cola de <b>ingestión</b> y atraviesa un pipeline de varias etapas: <b>escaneo antivirus</b> del archivo, <b>parseo</b> del contenido con Docling (extracción de texto, estructura y posiciones en página), <b>troceado</b> en fragmentos (chunks), generación de <b>embeddings</b> y <b>persistencia</b> en el índice vectorial.</li></ul><p>Cada documento muestra un <b>estado</b> mediante una etiqueta de color: <b>Pendiente</b> (en cola), <b>Procesando</b> (pipeline en marcha), <b>Indexado</b> (verde, listo para RAG), <b>Indexado vacío</b> (aviso: se procesó pero con 0 fragmentos, por lo que el agente no podrá recuperar nada; conviene subir un original con texto seleccionable o reindexar) y <b>Fallido</b> (con su mensaje de error). También se ven el tipo MIME, el tamaño y el número de páginas.</p><p><b>Aviso</b>: los PDF escaneados «solo imagen» son la causa más común del estado <i>Indexado vacío</i> — el parser no encuentra texto seleccionable que trocear. Prefiera siempre el documento original digital al escaneo.</p>",
      fullPage: true,
    },
    {
      title: "Reindexar, ver progreso y borrar documentos",
      goto: "/admin/knowledge-bases",
      // Despliega la primera KB para mostrar el panel de documentos con sus
      // filas y acciones (sin abrir ningún diálogo, a diferencia del paso
      // anterior que captura el formulario de subida).
      action: async (page) => {
        await page
          .locator('[data-testid^="kb-toggle-docs-"]')
          .first()
          .click()
          .catch(() => {});
        await page.waitForTimeout(800);
      },
      body: "<p>En el panel de documentos de cada KB (que se abre al desplegar la fila), dentro de la fila de cada documento, dispone de acciones según su estado:</p><ul><li><b>Progreso</b>: enlace que abre la página de detalle de ingestión del documento, donde puede seguir el avance del procesamiento en vivo (la vemos en el paso siguiente).</li><li><b>Reindexar</b> (icono de recarga, rol tenant_admin): vuelve a procesar el documento desde el archivo original. Está disponible cuando el documento ya terminó (estados Indexado, Indexado vacío o Fallido); útil para reintentar un fallo puntual, para reprocesar tras corregir el original o cuando el pipeline de ingestión ha mejorado. Se oculta mientras el documento está Pendiente o Procesando.</li><li><b>Eliminar</b> (icono papelera, rol tenant_admin): borra el documento de la KB y sus fragmentos del índice; los agentes dejan de poder recuperarlo de inmediato.</li></ul><p>Cuando un documento queda <b>Indexado vacío</b> se muestra un aviso explicando que no tiene fragmentos recuperables. El estado se refresca al consultar la lista, por lo que conviene volver a desplegar la KB para ver la progresión de Procesando a Indexado.</p><p><b>Rutina recomendada</b> tras una subida masiva: espere unos minutos, recargue el panel y revise que todos los documentos estén en verde. Los <i>Fallidos</i> muestran su mensaje de error bajo el título; los <i>Indexados vacíos</i> requieren un original mejor. Un documento que no está Indexado es invisible para el RAG aunque figure en la lista.</p>",
      fullPage: true,
    },
    {
      title: "Detalle de ingestión en vivo de un documento",
      goto: "/admin/knowledge-bases",
      // Requiere un documento existente: despliega la primera KB y pulsa el
      // enlace "Progreso" de su primer documento. SIN catch: si el tenant no
      // tiene documentos, el paso queda como "no disponible" (honesto).
      action: async (page) => {
        await page.locator('[data-testid^="kb-toggle-docs-"]').first().click({ timeout: 5_000 });
        await page.waitForTimeout(800);
        await page.locator('[data-testid^="kb-docs-progress-"]').first().click({ timeout: 5_000 });
        await page.waitForTimeout(1000);
      },
      body: "<p>El enlace <b>Progreso</b> de cada documento abre su página de <b>Ingestión</b> (<code>/admin/documents/{id}/ingestion</code>), pensada para seguir en vivo el pipeline <i>scan → parse → embed → persist</i>.</p><p>La cabecera muestra el título de la página y, a la derecha, la <b>etiqueta de estado</b> actual del documento (Pendiente, Procesando, Indexado, Indexado vacío o Fallido), que se actualiza en tiempo real.</p><p>La tarjeta <b>Eventos</b> es un registro en vivo: mientras el worker de ingestión procesa el documento, publica eventos que esta página recibe por WebSocket y añade a la lista con su marca de tiempo:</p><ul><li>Eventos de <b>progreso</b>: indican la etapa en curso (escaneo antivirus, parseo, embeddings…) con su detalle.</li><li>Eventos de <b>estado</b>: los cambios de estado del documento; el evento final de indexado incluye el número de <b>chunks</b> (fragmentos) generados — el dato que confirma que el RAG tendrá material que recuperar.</li></ul><p>Si abre esta página sobre un documento que ya terminó, no llegarán eventos nuevos (no hay pipeline corriendo) y la tarjeta lo explica según el caso: un documento <b>Indexado</b> indica que el proceso concluyó; un <b>Indexado vacío</b> explica que se procesó pero produjo 0 fragmentos (típico de PDFs solo-imagen) y sugiere subir un original con texto seleccionable o reindexar; un <b>Fallido</b> muestra su mensaje de error para diagnosticar.</p><p><b>Cuándo usar esta página</b>: para documentos grandes cuya ingestión tarda, para diagnosticar por qué un documento falló, y para confirmar cuántos fragmentos produjo una subida antes de dar el conocimiento por disponible.</p>",
      fullPage: true,
    },
    {
      title: "Citas del documento (localización en página)",
      goto: "/admin/knowledge-bases",
      // Requiere un documento existente: llega a su página de ingestión (como
      // el paso anterior) y de ahí navega a /citations del mismo documento.
      // SIN catch: sin documentos sembrados el paso queda "no disponible".
      action: async (page) => {
        await page.locator('[data-testid^="kb-toggle-docs-"]').first().click({ timeout: 5_000 });
        await page.waitForTimeout(800);
        await page.locator('[data-testid^="kb-docs-progress-"]').first().click({ timeout: 5_000 });
        await page.waitForTimeout(800);
        const citationsPath = new URL(page.url()).pathname.replace("/ingestion", "/citations");
        await page.goto(citationsPath, { waitUntil: "domcontentloaded" });
        await page.waitForTimeout(1000);
      },
      body: "<p>Cada documento indexado tiene además una vista de <b>Citas</b> (<code>/admin/documents/{id}/citations</code>) que muestra <b>dónde</b> está cada fragmento dentro del documento original. Es la base de la trazabilidad del RAG: cuando un agente responde apoyándose en un fragmento, esta vista permite localizar el pasaje exacto en su página.</p><p>La pantalla tiene dos partes:</p><ul><li>Un <b>lienzo de páginas</b>: cada página del documento se representa en proporción A4 y sobre ella se dibujan rectángulos con las <b>bounding boxes</b> de los fragmentos — las coordenadas normalizadas de posición que el parser Docling produce durante la ingestión.</li><li>La <b>lista de fragmentos (chunks)</b> con su contenido textual y su número de orden. Al seleccionar un fragmento, la vista se desplaza hasta la página que lo contiene y resalta su rectángulo.</li></ul><p>Esta correspondencia fragmento ↔ posición sirve para tres cosas: <b>verificar</b> que el troceado respeta la estructura del documento (que un fragmento no corta una tabla por la mitad), <b>auditar</b> de dónde sale una respuesta del agente, y <b>depurar</b> documentos que se indexan mal (fragmentos sin bbox o páginas vacías delatan problemas de parseo).</p><p>Los documentos sin coordenadas (por ejemplo, texto plano sin paginación) muestran sus fragmentos en la lista aunque no puedan dibujarse sobre páginas.</p>",
      fullPage: true,
    },
    {
      title: "Categorías de Knowledge Bases",
      goto: "/admin/knowledge-bases/categories",
      body: "<p>Esta pantalla gestiona las <b>categorías</b> que agrupan las KBs en el listado. Se accede desde el botón <b>Categorías</b> de la pantalla de Knowledge Bases o desde la migaja de pan superior.</p><ul><li><b>Built-in</b>: la plataforma siembra 5 categorías comunes a todos los tenants (stack, role, compliance, architecture, process). Son de <b>solo lectura</b>, se marcan con la etiqueta Built-in y no muestran botones de editar ni borrar.</li><li><b>Tenant</b>: las categorías propias que cree el tenant. Si no hay ninguna, se indica que puede usar las built-in o crear una nueva.</li></ul><p>Cada categoría se muestra como una tarjeta con su color, su nombre y su slug (identificador estable). Con rol tenant_admin, en las categorías propias aparecen los botones de editar y borrar. Arriba a la derecha está el botón <b>Nueva categoría</b>.</p><p>Las categorías son puramente organizativas — no afectan a permisos ni al RAG — pero se vuelven imprescindibles cuando el tenant acumula decenas de KBs: el listado principal agrupa por categoría y el color permite reconocer cada bloque de un vistazo. Las cinco built-in cubren los ejes de clasificación habituales (por stack tecnológico, por rol, por normativa, por arquitectura y por proceso); cree categorías propias solo cuando ninguna encaje.</p>",
      fullPage: true,
    },
    {
      title: "Crear, editar y borrar categorías",
      goto: "/admin/knowledge-bases/categories",
      // Abre el diálogo "Nueva categoría" pulsando el botón homónimo.
      action: async (page) => {
        await page
          .getByRole("button", { name: "Nueva categoría" })
          .click()
          .catch(async () => {
            await page
              .locator('[data-testid="kb-cat-create-button"]')
              .click()
              .catch(() => {});
          });
        await page.waitForTimeout(500);
      },
      body: "<p>Pulse <b>Nueva categoría</b> para abrir el diálogo de creación. Debe indicar:</p><ul><li><b>Slug</b>: identificador estable usado en filtros y URLs; solo admite minúsculas, números, guion y guion bajo (p. ej. <code>compliance-pci</code>).</li><li><b>Nombre</b>: el texto que se muestra en la interfaz (p. ej. <i>Compliance PCI-DSS</i>).</li><li><b>Color</b>: selector de color (o código hexadecimal) que identifica visualmente la categoría en el listado de KBs.</li></ul><p>Al <b>editar</b> una categoría propia puede cambiar el nombre y el color, pero <b>no el slug</b> (es fijo porque puede estar referenciado en filtros e integraciones). Al <b>borrar</b> una categoría, las KBs que pertenecían a ella no se eliminan: simplemente pasan a quedar <b>Sin categoría</b>, desde donde puede reasignarlas editando cada KB. Las categorías built-in no se pueden editar ni borrar.</p><p><b>Buena práctica</b>: acuerde la taxonomía de categorías antes de crear muchas KBs y use slugs cortos y predecibles; renombrar el nombre visible es barato, pero el slug le acompañará siempre.</p>",
      fullPage: true,
    },
    {
      title: "Knowledge Bases de un proyecto",
      goto: `/admin/projects/${PID}/knowledge-bases`,
      body: "<p>Cada proyecto tiene su propia sub-sección <b>Knowledge Bases</b> (aquí, la del proyecto de ejemplo «Hello World PHP»), que muestra únicamente las KBs <b>granted a ese proyecto</b>. Es la vista de trabajo cotidiana de quien opera un proyecto: qué conocimiento tienen disponible sus agentes y en qué estado están sus documentos, sin salir del contexto del proyecto.</p><p>Cada KB concedida aparece como una tarjeta con su nombre, su descripción y el <b>modelo de embedding</b> con el que indexa. Dentro de la tarjeta se listan sus documentos, cada uno con su etiqueta de estado (Pendiente, Procesando, Indexado, Fallido), su tipo MIME, su tamaño y su número de páginas, además del mensaje de error si falló. Cada fila ofrece las mismas acciones que el panel central: el enlace <b>Progreso</b> (detalle de ingestión en vivo), <b>Reindexar</b> (cuando el documento ya terminó) y <b>Eliminar</b>.</p><p>El botón <b>Subir documento</b> de cada tarjeta abre el diálogo de subida (archivo + título opcional) idéntico al de la pantalla central de KBs: los documentos que suba aquí quedan en la KB y por tanto visibles también para cualquier otro proyecto con grant sobre ella.</p><p>Si el proyecto no tiene ninguna KB concedida, la pantalla lo indica y enlaza al panel de Knowledge Bases para hacer el Grant. Recuerde la división de responsabilidades: la <b>creación</b> de KBs y los <b>grants</b> se administran en el panel central (<code>/admin/knowledge-bases</code>); aquí solo se gestiona lo que el proyecto ya ve.</p>",
      fullPage: true,
    },
    {
      title: "Documentos por proyecto",
      goto: "/admin/documents",
      body: "<p>La sección <b>Documentos</b> es el punto de entrada para localizar documentos a través de los proyectos. El sistema no mantiene un listado global de documentos: cada documento vive dentro de una Knowledge Base concedida a proyectos, por lo que esta pantalla muestra una rejilla con todos los <b>proyectos</b> del tenant.</p><ul><li>Cada tarjeta muestra el nombre del proyecto y su descripción.</li><li>Al hacer clic en un proyecto se navega a su sección <b>Knowledge Bases</b> (la del paso anterior), desde donde se accede a las KBs concedidas a ese proyecto y a sus documentos.</li></ul><p>Si el tenant aún no tiene proyectos, la pantalla lo indica e invita a crear uno desde <code>/admin/projects/new</code>. Recuerde que para que un proyecto vea documentos primero debe tener una KB concedida mediante Grant desde la pantalla de Knowledge Bases.</p><p><b>Resumen del flujo completo de conocimiento</b>: crear la KB (y categorizarla) → conceder acceso al proyecto o agente (Grant) → subir documentos → verificar que quedan <b>Indexados</b> (siguiendo la ingestión si hace falta) → a partir de ahí, los agentes del proyecto recuperan fragmentos relevantes automáticamente durante sus ejecuciones y sus respuestas pueden rastrearse hasta la página de origen con la vista de citas.</p>",
      fullPage: true,
    },
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  // Manual extenso (muchos pasos): captura + render de numerosos pantallazos.
  // Holgura amplia para que no agote el presupuesto del test bajo carga.
  test.setTimeout(900_000);
  await login(page);
  await generateManual(page, manual);
});
