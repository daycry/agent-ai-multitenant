import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef } from "../lib/manual";

// GENERADO desde el workflow de redacción. Editable a mano; reejecutable.
//
// NOTA sobre las capturas: la mayoría de pasos navegan a la MISMA ruta
// (`/admin/knowledge-bases`), que es una pantalla con listado + diálogos.
// Para que cada paso capture una pantalla DISTINTA y útil, cada paso que
// documenta un diálogo o un panel desplegable abre esa superficie con una
// `action` (clic) ANTES del pantallazo. Todas las acciones son tolerantes
// (`.catch(()=>{})`) para no romper la generación si un selector no existe
// en el entorno donde se ejecuta (p. ej. tenant sin KBs todavía).
const manual: ManualDef = {
  order: "04",
  slug: "04-conocimiento-rag-documentos",
  title: "Conocimiento (RAG) y Documentos",
  audience:
    "Administradores de tenant (tenant_admin) y operadores que gestionan el conocimiento que consumen los agentes",
  intro:
    "<p>Este manual explica cómo gestionar el <b>conocimiento</b> de la plataforma: las <b>Knowledge Bases (KBs)</b>, sus <b>categorías</b> y los <b>documentos</b> que se suben e indexan para alimentar el RAG (Retrieval-Augmented Generation) de los agentes.</p><p>Una Knowledge Base es un contenedor de documentos indexados sobre un modelo de embedding fijo. Los documentos se suben, se procesan (ingestión) y, una vez indexados, sus fragmentos quedan disponibles para que los agentes recuperen contexto. Las KBs se organizan en categorías y se conceden (grant) a proyectos y agentes, que son quienes finalmente las consumen.</p><p>Cubre las pantallas de listado y gestión de KBs, la gestión de categorías y el acceso a documentos a través de los proyectos, incluyendo la subida, el estado de indexación y el reindexado. La mayoría de acciones de creación, edición y borrado requieren rol <b>tenant_admin</b>.</p>",
  steps: [
    {
      title: "Listado de Knowledge Bases",
      goto: "/admin/knowledge-bases",
      body: "<p>Esta es la pantalla principal de <b>Knowledge Bases</b> del tenant. Cada KB agrupa documentos indexados y se asigna (grant) a uno o varios proyectos. El listado se presenta <b>agrupado por categoría</b>: primero las categorías built-in (sembradas por la plataforma), luego las del tenant y, al final, un grupo <b>Sin categoría</b>; cada cabecera muestra el color de la categoría y el número de KBs.</p><ul><li>Cada KB se muestra como una tarjeta con su nombre, una etiqueta <b>Built-in</b> si procede, el modelo de embedding utilizado y su descripción.</li><li>Al pulsar la fila (flecha a la izquierda) la tarjeta se <b>despliega</b> y aparece el panel de documentos de esa KB para subir y gestionar archivos sin salir de la pantalla.</li><li>El botón <b>Asignaciones</b> abre un diálogo con los proyectos y agentes que tienen acceso a la KB.</li></ul><p>Arriba a la derecha, con rol tenant_admin, dispone del botón <b>Categorías</b> (atajo a la gestión de categorías) y del botón <b>Crear KB</b>. Las KBs built-in son de solo lectura: solo permiten Grant y Asignaciones, sin editar ni borrar.</p>",
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
      body: "<p>Pulse <b>Crear KB</b> (arriba a la derecha) para abrir el diálogo de creación. Una KB es un contenedor de documentos indexados; tras crearla se despliega en la lista para subir documentos y se le da acceso (grant) a los proyectos o agentes que la consumirán.</p><ul><li><b>Nombre</b> (obligatorio, máximo 120 caracteres): identifica la KB en el listado.</li><li><b>Categoría</b> (opcional): un selector con las categorías built-in y las del tenant; sirve para organizar el listado. El botón <b>+</b> contiguo abre un mini-diálogo para crear una categoría nueva sin salir del flujo (pidiendo slug, nombre y color).</li><li><b>Descripción</b> (opcional): admite formato Markdown.</li></ul><p>El <b>modelo de embedding</b> se asigna automáticamente al valor por defecto de la plataforma y no se puede cambiar después. Pulse <b>Crear KB</b> para guardar; si hay error se mostrará un mensaje en el propio diálogo.</p>",
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
      body: "<p>Desde cada tarjeta del listado (con rol tenant_admin) el botón <b>Grant</b> abre el diálogo <i>Dar acceso a un proyecto</i>. Seleccione el proyecto destino con el buscador; tras conceder el acceso, ese proyecto verá la KB en su sub-sección Knowledge Bases y podrá subir documentos. Puede repetir el grant para varios proyectos sin cerrar el diálogo.</p><p>Junto a Grant, cada tarjeta dispone (solo en KBs no built-in) de:</p><ul><li><b>Editar</b> (icono lápiz): permite cambiar nombre, categoría y descripción. El modelo de embedding se muestra pero es de solo lectura: para usar otro modelo hay que crear una KB nueva y reindexar.</li><li><b>Borrar</b> (icono papelera): acción <b>irreversible</b>. Borra la KB, todos sus documentos indexados y los grants a proyectos (los archivos en el almacenamiento de objetos no se tocan). Para confirmar debe teclear exactamente el nombre de la KB.</li></ul><p>Las KBs built-in no muestran los botones de editar ni borrar, ya que el backend las protege como de solo lectura para el tenant.</p>",
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
      body: "<p>El botón <b>Asignaciones</b> de cada KB abre un diálogo que muestra a quién se ha concedido acceso, en dos secciones:</p><ul><li><b>Proyectos</b>: lista de proyectos con grant. Cada fila tiene un botón de papelera para <b>revocar</b> el acceso de ese proyecto.</li><li><b>Agentes</b>: lista de agentes con grant, mostrando su rol y su ámbito (scope) mediante etiquetas. Cada fila permite revocar el acceso del agente.</li></ul><p>Si la KB no está concedida a nadie, el diálogo lo indica y recuerda cómo conceder acceso: a un proyecto con el botón <b>Grant</b> de la lista, y a un agente desde su detalle (pestaña Knowledge Bases → Grant KB). Estas asignaciones son las que determinan qué agentes pueden recuperar el conocimiento de la KB durante el RAG.</p>",
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
      body: "<p>Para gestionar documentos, despliegue una KB en el listado (pulsando su fila) para abrir su <b>panel de documentos</b>. El panel muestra el número de documentos y un botón <b>Subir documento</b> (rol tenant_admin) que abre el diálogo de subida.</p><ul><li>El diálogo de subida acepta un <b>Archivo</b> (formatos admitidos: .pdf, .docx, .md, .txt, .html, .wav, .mp3) y un <b>Título</b> opcional (por defecto se usa el nombre del archivo).</li><li>Al subir, el documento entra en la cola de <b>ingestión</b> y se procesa para extraer su texto y generar los fragmentos (chunks) e índices que usará el RAG.</li></ul><p>Cada documento muestra un <b>estado</b> mediante una etiqueta de color: <b>Pendiente</b>, <b>Procesando</b>, <b>Indexado</b> (verde, listo para RAG), <b>Indexado vacío</b> (aviso: se procesó pero con 0 fragmentos, por lo que el agente no podrá recuperar nada; conviene subir un original con texto seleccionable o reindexar) y <b>Fallido</b> (con su mensaje de error). También se ven el tipo MIME, el tamaño y el número de páginas.</p>",
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
      body: "<p>En el panel de documentos de cada KB (que se abre al desplegar la fila), dentro de la fila de cada documento, dispone de acciones según su estado:</p><ul><li><b>Progreso</b>: enlace que abre la página de detalle de ingestión del documento, donde puede seguir el avance del procesamiento.</li><li><b>Reindexar</b> (icono de recarga, rol tenant_admin): vuelve a procesar el documento. Está disponible cuando el documento ya terminó (estados Indexado, Indexado vacío o Fallido); útil para reintentar un fallo o reprocesar tras corregir el original. Se oculta mientras el documento está Pendiente o Procesando.</li><li><b>Eliminar</b> (icono papelera, rol tenant_admin): borra el documento de la KB.</li></ul><p>Cuando un documento queda <b>Indexado vacío</b> se muestra un aviso explicando que no tiene fragmentos recuperables. El estado se refresca al consultar la lista, por lo que conviene volver a desplegar la KB para ver la progresión de Procesando a Indexado.</p>",
      fullPage: true,
    },
    {
      title: "Categorías de Knowledge Bases",
      goto: "/admin/knowledge-bases/categories",
      body: "<p>Esta pantalla gestiona las <b>categorías</b> que agrupan las KBs en el listado. Se accede desde el botón <b>Categorías</b> de la pantalla de Knowledge Bases o desde la migaja de pan superior.</p><ul><li><b>Built-in</b>: la plataforma siembra 5 categorías comunes a todos los tenants (stack, role, compliance, architecture, process). Son de <b>solo lectura</b>, se marcan con la etiqueta Built-in y no muestran botones de editar ni borrar.</li><li><b>Tenant</b>: las categorías propias que cree el tenant. Si no hay ninguna, se indica que puede usar las built-in o crear una nueva.</li></ul><p>Cada categoría se muestra como una tarjeta con su color, su nombre y su slug (identificador estable). Con rol tenant_admin, en las categorías propias aparecen los botones de editar y borrar. Arriba a la derecha está el botón <b>Nueva categoría</b>.</p>",
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
      body: "<p>Pulse <b>Nueva categoría</b> para abrir el diálogo de creación. Debe indicar:</p><ul><li><b>Slug</b>: identificador estable usado en filtros y URLs; solo admite minúsculas, números, guion y guion bajo (p. ej. <code>compliance-pci</code>).</li><li><b>Nombre</b>: el texto que se muestra en la interfaz (p. ej. <i>Compliance PCI-DSS</i>).</li><li><b>Color</b>: selector de color (o código hexadecimal) que identifica visualmente la categoría en el listado de KBs.</li></ul><p>Al <b>editar</b> una categoría propia puede cambiar el nombre y el color, pero <b>no el slug</b> (es fijo porque puede estar referenciado en filtros e integraciones). Al <b>borrar</b> una categoría, las KBs que pertenecían a ella no se eliminan: simplemente pasan a quedar <b>Sin categoría</b>. Las categorías built-in no se pueden editar ni borrar.</p>",
      fullPage: true,
    },
    {
      title: "Documentos por proyecto",
      goto: "/admin/documents",
      body: "<p>La sección <b>Documentos</b> es el punto de entrada para localizar documentos a través de los proyectos. El sistema no mantiene un listado global de documentos: cada documento vive dentro de una Knowledge Base de un proyecto, por lo que esta pantalla muestra una rejilla con todos los <b>proyectos</b> del tenant.</p><ul><li>Cada tarjeta muestra el nombre del proyecto y su descripción.</li><li>Al hacer clic en un proyecto se navega a su sección <b>Knowledge Bases</b>, desde donde se accede a las KBs concedidas a ese proyecto y a sus documentos.</li></ul><p>Si el tenant aún no tiene proyectos, la pantalla lo indica e invita a crear uno desde <code>/admin/projects/new</code>. Recuerde que para que un proyecto vea documentos primero debe tener una KB concedida mediante Grant desde la pantalla de Knowledge Bases.</p>",
      fullPage: true,
    },
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  // Manual extenso (muchos pasos): captura + render de >25 pantallazos. Holgura
  // amplia para que no agote el presupuesto del test bajo carga.
  test.setTimeout(900_000);
  await login(page);
  await generateManual(page, manual);
});
