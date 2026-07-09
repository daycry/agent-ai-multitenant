import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef } from "../lib/manual";

// GENERADO desde el workflow de redacción. Editable a mano; reejecutable.
const manual: ManualDef = {
  order: "09",
  slug: "09-administracion-del-sistema",
  title: "Administración del Sistema",
  audience: "Administrador del Sistema (System Admin)",
  intro:
    "<p>Este manual está dirigido al <b>Administrador del Sistema (System Admin)</b> de la plataforma agéntica multi-tenant. Reúne las pantallas de administración global que NO pertenecen a un tenant concreto: vigilancia de la salud del stack, gestión de usuarios y su acceso a tenants, catálogo de proveedores LLM (ADR 0021/0028), administración del Ollama del stack, catálogo de precios de modelos (con su histórico y su sincronizador), valores por defecto de plataforma y las herramientas de copia de seguridad (programación, destinos remotos y restauración).</p><p><b>Todas estas pantallas requieren rol System Admin.</b> El backend protege cada endpoint con <code>require_system_admin</code> sobre una sesión con privilegios globales (BYPASSRLS) y, en la interfaz, un <code>RoleGuard</code> oculta la superficie a otros roles. Si accedes sin ese rol, verás un aviso de sección exclusiva o, en algunas pantallas (programación de backups, destinos), una vista de solo lectura. Las credenciales y secretos nunca se muestran ni se guardan en base de datos: viven en Vault o en el secret seam de los workers. Este principio se repite en todo el manual: la interfaz solo sabe si una credencial está «configurada», jamás su valor.</p><p>Además de las pantallas de listado, este manual documenta los <b>diálogos de trabajo</b> más importantes del System Admin: la asignación de membership de un usuario, el formulario de alta/edición de un proveedor LLM, el histórico de precios de un modelo y la previsualización (dry-run) del sincronizador de precios.</p><p>La pantalla de Estadísticas por tenant se incluye aquí por contexto operativo, aunque su acceso es de <b>tenant_admin</b> (no System Admin) y muestra únicamente los datos del tenant activo. Los ajustes por-tenant (índice de Settings, SSO OIDC/SAML, tarifa horaria) se documentan en el manual 08; la calidad de evals, en el manual 06.</p>",
  steps: [
    {
      title: "Salud de servicios del stack (Dashboard)",
      goto: "/admin/dashboard",
      body: "<p>El dashboard es la primera pantalla tras iniciar sesión y, para el System Admin, su bloque clave es <b>«Salud de servicios»</b>: un panel que interroga al endpoint <code>/admin/system-health</code> (exclusivo de System Admin) y se refresca automáticamente cada 30 segundos. El backend sondea en paralelo los servicios de infraestructura del stack y devuelve, por cada uno, un estado <code>ok</code>, <code>degraded</code> o <code>down</code> con un detalle saneado (nunca la excepción cruda, para no filtrar información interna; los códigos HTTP sí se muestran porque son una señal pública y gruesa).</p><ul><li><b>postgres</b>: ejecuta un <code>SELECT 1</code> con timeout corto sobre la sesión administrativa.</li><li><b>redis</b>: un <code>PING</code> al broker/cache.</li><li><b>vault</b>: petición HTTP a <code>/v1/sys/health</code> (el almacén de secretos).</li><li><b>minio</b>: petición HTTP a <code>/minio/health/live</code> (object storage de documentos).</li><li><b>clamav</b>: comprobación TCP al puerto 3310 del antivirus.</li><li><b>docling-serve</b>: petición HTTP a <code>/health</code> (el conversor documental de la ingesta).</li><li><b>ollama</b>: petición HTTP a <code>/api/version</code> (embeddings y modelos locales).</li><li><b>egress-proxy</b>: comprobación TCP al proxy de salida a internet de los agentes.</li></ul><p>El <b>estado global</b> del stack se considera caído solo si PostgreSQL está caído (es el servicio del que depende todo lo demás); el resto de servicios degradados se señalizan individualmente con su icono de color (verde/ámbar/rojo). Ante un servicio <code>down</code>, contrasta con <code>docker compose ps</code> en el host y consulta los runbooks de <code>docs/06-runbooks/</code>.</p><p>Sobre el panel de salud, las <b>tarjetas KPI</b> muestran los recuentos de agentes, equipos y proyectos del tenant activo. Cada contador se pide en paralelo y degrada de forma elegante: si su endpoint falla, la tarjeta muestra «—» en lugar de romper la página.</p>",
      fullPage: true,
    },
    {
      title: "Usuarios de la plataforma",
      goto: "/admin/users",
      body: "<p>Esta pantalla gestiona los <b>usuarios globales</b> de la plataforma. Los usuarios no pertenecen a un tenant: el acceso a cada tenant lo otorgan exclusivamente las <i>membership</i> (usuario + tenant + rol) que asigna el System Admin desde aquí (ADR 0047, deny-by-default: sin membership activa el usuario no entra a ningún tenant). No existe una pantalla separada de «tenants»: la relación usuario↔tenant se administra íntegramente desde esta vista.</p><ul><li>Una <b>barra de búsqueda</b> filtra los usuarios por email o nombre.</li><li>La <b>tabla</b> muestra por cada usuario: nombre, email, tipo (<code>System Admin</code> o <code>Usuario</code>) y estado (activo/inactivo).</li><li>El botón <b>«Gestionar tenants»</b> abre un diálogo para administrar las membership de ese usuario (se documenta en el paso siguiente).</li></ul><p>Ten presente el modelo de permisos de dos niveles: el flag <b>System Admin</b> es global (da acceso a las pantallas de este manual y a la sesión BYPASSRLS del backend), mientras que el <b>rol de membership</b> gobierna lo que el usuario puede hacer <i>dentro</i> de cada tenant. Un usuario corriente puede ser Tenant Admin de un tenant y simple Tenant User de otro.</p>",
      fullPage: true,
    },
    {
      title: "Diálogo «Gestionar tenants»: membership de un usuario",
      goto: "/admin/users",
      action: async (page) => {
        await page
          .locator('[data-testid^="user-memberships-open-"]')
          .first()
          .click()
          .catch(() => {});
        await page.waitForTimeout(600);
      },
      body: "<p>El diálogo de membership es la herramienta central del control de acceso multi-tenant. Su tabla superior lista los tenants a los que el usuario ya tiene acceso, con tres columnas de trabajo por fila:</p><ul><li><b>Rol</b>: un desplegable con los tres roles de membership — <b>Tenant Admin</b> (administra el tenant: agentes, proyectos, ajustes, tokens de API, webhooks), <b>Tenant User</b> (usa la plataforma sin administrarla) y <b>System Operator</b> (perfil operativo). Cambiar la selección persiste el rol al momento.</li><li><b>Estado</b>: un badge activo/inactivo que conmuta con un clic. Desactivar una membership corta el acceso del usuario a ese tenant <i>sin borrar</i> la relación (útil para suspensiones temporales).</li><li><b>Revocar</b>: el icono de papelera elimina la membership definitivamente.</li></ul><p>El formulario inferior <b>«Asignar acceso a un tenant»</b> ofrece un desplegable con los tenants en los que el usuario aún no tiene membership y otro con el rol inicial; el botón <b>«Asignar»</b> crea la relación. Si la operación falla (por ejemplo, por una condición de carrera con otro administrador), el error del backend se muestra dentro del propio diálogo sin cerrar el contexto de trabajo.</p><p>Recuerda el principio deny-by-default (ADR 0047): un usuario recién creado, sin membership, puede autenticarse pero no ve ningún tenant. Esta es la palanca con la que se implementa el alta y la baja de personal en departamentos/equipos.</p>",
      fullPage: false,
    },
    {
      title: "Proveedores LLM",
      goto: "/admin/llm-providers",
      body: "<p>Catálogo global de proveedores LLM (ADR 0021/0028). Solo se admiten los cuatro caminos cerrados: <b>Claude Agent SDK</b>, <b>GitHub Copilot</b>, <b>Azure AI Foundry (APIM)</b> y <b>Ollama</b>. La configuración es global de plataforma y las credenciales se guardan únicamente en Vault; la interfaz nunca muestra el secreto, solo si la credencial está «configurada».</p><p>La <b>tabla</b> lista cada proveedor con su tipo, slug, nombre, endpoint, estado de la credencial, estado activo/inactivo y el resultado de la última prueba de conexión. Acciones por fila:</p><ul><li><b>Probar conexión</b> (icono de enchufe): comprueba el proveedor y muestra OK o un error clasificado.</li><li><b>Sincronizar modelos</b> (icono de refrescar): descubre los modelos que sirve el proveedor y los vuelca en su catálogo para los selectores de modelo.</li><li><b>Autorizar con GitHub</b> (solo Copilot): abre el diálogo de Device Flow.</li><li><b>Editar</b> y <b>Eliminar</b>; el badge de estado activa/desactiva el proveedor sin abrir el diálogo.</li></ul><p>El botón <b>«Nuevo proveedor»</b> abre el formulario de alta, que se documenta en detalle en el paso siguiente. En el diálogo de <b>GitHub Copilot</b> se inicia el Device Flow, que muestra un código de usuario y un enlace de verificación; tras autorizar en GitHub, el token se acuña y se guarda solo en Vault.</p><p>Consejo operativo: tras dar de alta o rotar la credencial de un proveedor, ejecuta siempre <b>Probar conexión</b> y después <b>Sincronizar modelos</b>. Sin la sincronización, los selectores de modelo del resto de la plataforma (valores por defecto, agentes, precios) no ofrecerán los modelos de ese proveedor.</p>",
      fullPage: true,
    },
    {
      title: "Alta y edición de un proveedor LLM (formulario)",
      goto: "/admin/llm-providers",
      action: async (page) => {
        await page
          .getByTestId("provider-create-open")
          .click()
          .catch(() => {});
        await page.waitForTimeout(600);
      },
      body: "<p>El formulario de proveedor tiene tres bloques: identidad, endpoint y credencial. En <b>identidad</b> se elige el <b>Tipo</b> (el catálogo cerrado de cuatro: Claude Agent SDK, GitHub Copilot, Azure AI Foundry y Ollama — al editar un proveedor existente el tipo queda bloqueado), el <b>Nombre</b> visible (p. ej. «Claude (prod)») y el <b>Slug</b> único en minúsculas, números y guiones. El slug es el handle que distingue dos proveedores del mismo tipo, como <code>ollama-local</code> frente a <code>ollama-cloud</code>.</p><p>El campo de <b>endpoint</b> depende del tipo: para <b>Claude SDK</b> no se muestra (el SDK conoce su endpoint); para <b>Azure Foundry</b> es el «Endpoint APIM (gateway)» y es obligatorio (p. ej. <code>https://apim.example.com/openai</code>); para <b>Ollama</b> es el «Endpoint Ollama», también obligatorio (p. ej. <code>http://localhost:11434</code>).</p><p>El bloque de <b>credencial</b> también cambia según el tipo:</p><ul><li><b>Claude SDK</b>: un selector de «Modo de autenticación» con dos opciones — <b>API key</b> de Anthropic (<code>sk-ant-…</code>) o <b>Suscripción Pro/Max</b>, cuyo token se obtiene ejecutando <code>claude setup-token</code> en una máquina con la sesión iniciada.</li><li><b>GitHub Copilot</b>: un token OAuth pegado a mano, o (recomendado) dejarlo vacío y usar el botón de <b>Device Flow</b> desde la lista de proveedores.</li><li><b>Azure Foundry</b>: la API key de la subscription APIM, obligatoria al crear.</li><li><b>Ollama</b>: un bearer token <i>opcional</i> (solo necesario para Ollama Cloud; el Ollama del stack no requiere credencial).</li></ul><p>La credencial es <b>de solo escritura</b>: se guarda únicamente en Vault (nunca en la base de datos ni en respuestas de la API). Al editar, el placeholder «•••••••• (configurado)» indica que ya existe una; dejar el campo vacío la conserva y escribir un valor la rota. La casilla <b>«Proveedor activo»</b> controla si el proveedor participa en la resolución de modelos; el botón <b>Crear/Guardar</b> se habilita solo cuando los campos obligatorios del tipo elegido están completos.</p>",
      fullPage: false,
    },
    {
      title: "Ollama y Embeddings",
      goto: "/admin/ollama",
      body: "<p>Administra el Ollama del stack (ADR 0056). La pantalla tiene dos secciones.</p><p><b>Embeddings</b> (solo lectura/descubrimiento): muestra el modelo de embeddings <i>activo</i>, la dimensión requerida (768), si Ollama es accesible, qué modelos instalados son embedders válidos y compatibles, y una lista de modelos recomendados. El modelo activo se fija por variable de entorno en la instalación; cambiarlo con bases de conocimiento existentes es un trabajo de re-indexado aparte. El botón <b>«Actualizar»</b> recarga los datos.</p><p><b>Modelos Ollama</b>: lista los modelos instalados con su tamaño. Para <b>descargar (pull)</b> un modelo escribe su nombre (p. ej. <code>nomic-embed-text</code>) y pulsa <b>«Pull»</b>; para borrarlo usa el icono de papelera de su fila. Si Ollama no está accesible se muestra un aviso para revisar que el servicio del stack está levantado. Toda la pantalla es exclusiva del System Admin.</p>",
      fullPage: true,
    },
    {
      title: "Modelos y Precios",
      goto: "/admin/model-prices",
      body: "<p>Catálogo global de precios de modelos en <b>USD canónico</b>, con soporte de <i>prompt caching</i> (precio de lectura de caché). La lectura del catálogo está abierta a cualquier usuario autenticado; la edición (crear, editar, superseder, sincronizar) es solo System Admin.</p><p>En la parte superior, un aviso indica el alcance del sincronizador: solo importa las familias de los proveedores LLM <i>activos</i>; si no hay ninguno activo, se invita a activar uno en Proveedores LLM. El bloque de <b>filtros</b> permite acotar por familia (provider), modelo, modalidad, proveedor de plataforma asociado y el toggle <b>«Solo vigentes»</b>; pulsa <b>«Filtrar»</b> o <b>«Limpiar»</b>.</p><p>La <b>tabla</b> muestra familia, modelo, modalidad, proveedor asociado, precios de input/output/caché, unidad, fuente y vigencia. Acciones por fila: <b>Histórico</b> (icono de reloj) abre un diálogo con la línea temporal de precios y una gráfica; <b>Editar</b> y <b>Superseder</b> (cerrar el periodo vigente) solo para System Admin. El botón <b>«Nuevo precio»</b> abre el formulario de alta manual: proveedor/familia, modelo, modalidad, precios de input/output/caché en USD, unidad, ventana de contexto y fuente.</p><p>Este catálogo es la base del <b>cálculo de costes</b> de toda la plataforma: cada run de agente multiplica sus tokens por el precio vigente del modelo que usó, y los dashboards de consumo (Estadísticas del tenant, detalle de ejecución) se alimentan de aquí. Mantenerlo al día — idealmente con el sincronizador del paso correspondiente — es lo que hace fiables esas cifras.</p>",
      fullPage: true,
    },
    {
      title: "Histórico de precios de un modelo",
      goto: "/admin/model-prices",
      action: async (page) => {
        await page
          .locator('[data-testid^="price-history-"]')
          .first()
          .click()
          .catch(() => {});
        await page.waitForTimeout(600);
      },
      body: "<p>El icono de reloj de cada fila abre el diálogo de <b>histórico</b> del modelo: la línea temporal completa de sus precios, con un registro por cada periodo de vigencia. Cada fila del histórico muestra los precios de input/output/caché y las fechas en las que ese precio estuvo (o está) vigente; el registro actual aparece marcado como vigente.</p><p>Bajo la tabla, una <b>gráfica de evolución</b> dibuja la trayectoria del precio a lo largo del tiempo, lo que permite detectar de un vistazo subidas o bajadas del proveedor y correlacionarlas con variaciones en el coste de los planes.</p><p>El histórico es <b>inmutable por diseño</b>: los precios antiguos no se editan ni se borran, se <i>superseden</i> (se cierra su periodo de vigencia y se abre uno nuevo). Así, los costes ya calculados de runs pasados siguen siendo auditables contra el precio que estaba vigente en su momento.</p>",
      fullPage: false,
    },
    {
      title: "Sincronizar precios: previsualización dry-run",
      goto: "/admin/model-prices",
      action: async (page) => {
        await page
          .getByTestId("price-sync-open")
          .click()
          .catch(() => {});
        await page.waitForTimeout(900);
      },
      body: "<p>El botón <b>«Sincronizar precios»</b> abre el diálogo de sincronización con el feed de precios. Es un flujo en dos tiempos deliberado: primero un <b>dry-run</b> que calcula el diff completo sin escribir nada, y solo después — si confirmas — la aplicación de los cambios.</p><p>La cabecera del diálogo recuerda el <b>alcance</b>: solo se importan las familias de los proveedores LLM <i>activos</i> (se listan explícitamente); si una familia del feed no tiene proveedor activo aparece como <i>omitida</i>. Debajo, el <b>resumen del diff</b> agrupa los cambios y la tabla detalla, modelo a modelo: si es <b>nuevo</b>, si se <b>actualiza</b> (con el porcentaje de variación de input y de output calculado contra el precio vigente) y si el registro local está marcado como <b>manual</b> (introducido a mano por un administrador). Si no hay ningún cambio, el diálogo lo dice y no hay nada que aplicar.</p><p>La <b>puerta de confirmación</b> es el mecanismo de seguridad clave: si algún modelo sube más de un <b>10%</b>, el botón «Aplicar» permanece deshabilitado hasta que marques la casilla de confirmación explícita, que te obliga a reconocer la subida antes de que afecte al cálculo de costes de todos los tenants. «Cancelar» abandona sin efectos; los errores del backend (al calcular el diff o al aplicar) se muestran dentro del diálogo.</p>",
      fullPage: false,
    },
    {
      title: "Valores por defecto de plataforma",
      goto: "/admin/settings/platform-defaults",
      body: "<p>Edita los ajustes globales de la plataforma que no tienen página propia. Los ajustes se agrupan en <b>tarjetas por categoría</b> guiadas por el registro del backend, sin valores cableados en la interfaz; el registro actual incluye:</p><ul><li><b>Modelos</b>: el <i>modelo por defecto de agentes</i> (ADR 0055), base de la cadena de herencia plataforma → proyecto → agente.</li><li><b>Ejecución</b>: los <i>reintentos máximos de revisión</i> y los <i>límites de tiempo</i> soft y hard (en segundos) de los runs.</li><li><b>Planes</b>: el <i>umbral de doble firma</i> para aprobaciones.</li><li><b>RAG</b>: el <i>reranker</i> de la recuperación aumentada.</li><li><b>Mantenimiento</b>: la <i>rotación de credenciales</i> y el <i>escalado de tareas humanas</i>.</li></ul><p>Cada ajuste muestra su etiqueta, una descripción y su clave técnica. El control depende del tipo: una casilla para los booleanos (Activado/Desactivado), un campo numérico con mínimo y máximo para enteros, un campo de texto para decimales, y un control de <b>configuración de modelo</b> (proveedor por <i>kind</i>, modelo y temperatura) para el modelo por defecto de agentes. Cada control tiene su propio botón <b>«Guardar»</b>, que valida el valor por tipo y lo persiste; al elegir un proveedor, el desplegable de modelo se rellena con los modelos sincronizados de ese proveedor (o puedes escribir el nombre si no hay sincronizados).</p><p>Mientras un ajuste no se toca, rige el <b>valor por defecto del código</b> (el backend lo señala con <code>is_default</code>): esta pantalla solo persiste los valores que un System Admin cambia expresamente, de modo que una instalación recién desplegada funciona sin configurar nada aquí. Solo System Admin.</p>",
      fullPage: true,
    },
    {
      title: "Programación de backups",
      goto: "/admin/backup",
      body: "<p>Configura el <b>backup diario</b> del stack. La lectura está abierta a cualquier miembro autenticado (que ve una vista de solo lectura con los valores actuales); la edición es solo System Admin. Los cambios surten efecto en la siguiente ejecución sin reiniciar nada.</p><p>El formulario tiene tres campos: la casilla <b>«Backup diario activado»</b>; el campo <b>«Cron»</b> (cinco campos: minuto, hora, día del mes, mes y día de la semana; por defecto <code>0 3 * * *</code>, las 03:00 cada día); y <b>«Retención local (días)»</b>, entre 1 y 3650, que indica cuánto se conservan los bundles antes de eliminarse tras un backup correcto. El botón <b>«Guardar»</b> se habilita solo cuando hay cambios y los valores son válidos; un cron inválido o una retención fuera de rango devuelve un error con el detalle.</p>",
      fullPage: true,
    },
    {
      title: "Destinos remotos de backup",
      goto: "/admin/backup/destinations",
      body: "<p>Gestiona los <b>destinos remotos</b> a los que se sube cada backup correcto y verificado: S3 (o compatible), Backblaze B2, SFTP/NAS y rclone. La edición es solo System Admin; un miembro normal ve una lista de solo lectura.</p><p>Cada destino es una tarjeta con su <b>tipo</b>, un <b>nombre</b>, la casilla <b>«Habilitado»</b> y los campos de configuración propios del tipo (bucket, prefijo, endpoint, región, host, usuario, ruta remota, remote, path…). <b>Importante:</b> las credenciales (claves de acceso, contraseñas, claves privadas, blob de rclone) nunca se introducen ni se muestran aquí; se resuelven desde el secret seam de los workers (Vault/env) al subir o probar. Usa <b>«Añadir destino»</b> para crear uno nuevo, el icono de papelera para eliminarlo, <b>«Probar conexión»</b> para verificar la conectividad de un destino y <b>«Guardar»</b> para persistir la lista (los campos obligatorios marcados con * deben estar completos).</p>",
      fullPage: true,
    },
    {
      title: "Restaurar desde backup",
      goto: "/admin/backup/restore",
      body: "<p>Restaura el stack completo o un único tenant desde un backup. Es una operación <b>larga y destructiva</b>: el backend la encola como job en segundo plano y la pantalla sondea su progreso. Exclusiva del System Admin.</p><p>El flujo es: <b>1)</b> selecciona un backup de la lista <b>«Backups disponibles»</b> (muestra id, si está cifrado, tamaño y ubicaciones local/remotas). <b>2)</b> revisa el <b>Preview</b> con el manifest, los artefactos y si admite restauración por tenant. <b>3)</b> elige el <b>tipo de restore</b>: completo (detiene el stack y restaura todo) o selectivo por tenant (indicando el Tenant ID UUID; muestra las tablas afectadas). <b>4)</b> pulsa <b>«Restaurar…»</b> para abrir el diálogo de <b>doble confirmación</b>, donde debes teclear exactamente el token indicado (el id del bundle para un restore completo, o <code>&lt;tenant_id&gt;@&lt;backup_id&gt;</code> para uno por tenant); el backend re-deriva y valida ese token. <b>5)</b> una vez encolado, la tarjeta <b>«Progreso del restore»</b> muestra el estado del job hasta completarse (SUCCESS) o fallar (FAILURE) con el detalle.</p>",
      fullPage: true,
    },
    {
      title: "Estadísticas del tenant",
      goto: "/admin/tenant-stats",
      body: "<p>Dashboard de rendimiento y consumo del tenant. A diferencia del resto del manual, esta pantalla requiere rol <b>tenant_admin</b> (no System Admin) y muestra únicamente datos del tenant activo; la comparativa entre tenants es una superficie aparte. Los costes están en USD canónico, con un selector de moneda solo para visualización (USD/EUR/GBP).</p><ul><li><b>Selectores</b> de ventana temporal (30/90/365 días) y de moneda de visualización.</li><li><b>Tarjetas de cabecera</b>: tasa de éxito, número de runs, tiempo medio y coste medio, más una gráfica de tendencia de la tasa de éxito diaria.</li><li><b>Resumen de consumo</b>: coste total, runs, tokens (input/output/cached), la segmentación de coste IA vs humano y el run más costoso.</li><li><b>Agentes top y bottom</b> por tasa de éxito y una tabla de desglose por agente.</li><li><b>Explorador de runs</b>: tabla paginada y filtrable (por rol, verdict, modelo y coste mínimo) con una fila por ejecución (timestamp, plan, tarea, agente, rol, modelo, duración, tokens, coste, verdict y reintentos).</li></ul>",
      fullPage: true,
    },
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  test.setTimeout(300_000);
  await login(page);
  await generateManual(page, manual);
});
