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
    "<p>Este manual está dirigido al <b>Administrador del Sistema (System Admin)</b> de la plataforma agéntica multi-tenant. Reúne las pantallas de administración global que NO pertenecen a un tenant concreto: gestión de usuarios y su acceso a tenants, catálogo de proveedores LLM (ADR 0021/0028), administración del Ollama del stack, catálogo de precios de modelos, valores por defecto de plataforma y las herramientas de copia de seguridad (programación, destinos remotos y restauración).</p><p><b>Todas estas pantallas requieren rol System Admin.</b> El backend protege cada endpoint con <code>require_system_admin</code> sobre una sesión con privilegios globales (BYPASSRLS) y, en la interfaz, un <code>RoleGuard</code> oculta la superficie a otros roles. Si accedes sin ese rol, verás un aviso de sección exclusiva o, en algunas pantallas (programación de backups, destinos), una vista de solo lectura. Las credenciales y secretos nunca se muestran ni se guardan en base de datos: viven en Vault o en el secret seam de los workers.</p><p>La pantalla de Estadísticas por tenant se incluye aquí por contexto operativo, aunque su acceso es de <b>tenant_admin</b> (no System Admin) y muestra únicamente los datos del tenant activo.</p>",
  steps: [
    {
      title: "Usuarios de la plataforma",
      goto: "/admin/users",
      body: "<p>Esta pantalla gestiona los <b>usuarios globales</b> de la plataforma. Los usuarios no pertenecen a un tenant: el acceso a cada tenant lo otorgan exclusivamente las <i>membership</i> (usuario + tenant + rol) que asigna el System Admin desde aquí (ADR 0047, deny-by-default: sin membership activa el usuario no entra a ningún tenant).</p><ul><li>Una <b>barra de búsqueda</b> filtra los usuarios por email o nombre.</li><li>La <b>tabla</b> muestra por cada usuario: nombre, email, tipo (<code>System Admin</code> o <code>Usuario</code>) y estado (activo/inactivo).</li><li>El botón <b>«Gestionar tenants»</b> abre un diálogo para administrar las membership de ese usuario.</li></ul><p>En el diálogo de membership puedes: ver los tenants a los que el usuario tiene acceso con su rol y estado; cambiar el rol de una membership (Tenant Admin, Tenant User o System Operator); activar o desactivar una membership pulsando su badge de estado; revocar el acceso con el icono de papelera; y, en el formulario inferior <b>«Asignar acceso a un tenant»</b>, elegir un tenant disponible y un rol y pulsar <b>«Asignar»</b>.</p>",
      fullPage: true,
    },
    {
      title: "Proveedores LLM",
      goto: "/admin/llm-providers",
      body: "<p>Catálogo global de proveedores LLM (ADR 0021/0028). Solo se admiten los cuatro caminos cerrados: <b>Claude Agent SDK</b>, <b>GitHub Copilot</b>, <b>Azure AI Foundry (APIM)</b> y <b>Ollama</b>. La configuración es global de plataforma y las credenciales se guardan únicamente en Vault; la interfaz nunca muestra el secreto, solo si la credencial está «configurada».</p><p>La <b>tabla</b> lista cada proveedor con su tipo, slug, nombre, endpoint, estado de la credencial, estado activo/inactivo y el resultado de la última prueba de conexión. Acciones por fila:</p><ul><li><b>Probar conexión</b> (icono de enchufe): comprueba el proveedor y muestra OK o un error clasificado.</li><li><b>Sincronizar modelos</b> (icono de refrescar): descubre los modelos que sirve el proveedor y los vuelca en su catálogo para los selectores de modelo.</li><li><b>Autorizar con GitHub</b> (solo Copilot): abre el diálogo de Device Flow.</li><li><b>Editar</b> y <b>Eliminar</b>; el badge de estado activa/desactiva el proveedor sin abrir el diálogo.</li></ul><p>El botón <b>«Nuevo proveedor»</b> abre el formulario de alta: tipo, nombre, slug único (minúsculas, números y guiones), endpoint (obligatorio para Azure Foundry y Ollama) y el campo de credencial que cambia según el tipo (token OAuth, API key de APIM o bearer token de Ollama). Al editar, dejar el campo de credencial vacío conserva el secreto actual; escribir un valor lo rota. En el diálogo de <b>GitHub Copilot</b> se inicia el Device Flow, que muestra un código de usuario y un enlace de verificación; tras autorizar en GitHub, el token se acuña y se guarda solo en Vault.</p>",
      fullPage: true,
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
      body: "<p>Catálogo global de precios de modelos en <b>USD canónico</b>, con soporte de <i>prompt caching</i> (precio de lectura de caché). La lectura del catálogo está abierta a cualquier usuario autenticado; la edición (crear, editar, superseder, sincronizar) es solo System Admin.</p><p>En la parte superior, un aviso indica el alcance del sincronizador: solo importa las familias de los proveedores LLM <i>activos</i>; si no hay ninguno activo, se invita a activar uno en Proveedores LLM. El bloque de <b>filtros</b> permite acotar por familia (provider), modelo, modalidad, proveedor de plataforma asociado y el toggle <b>«Solo vigentes»</b>; pulsa <b>«Filtrar»</b> o <b>«Limpiar»</b>.</p><p>La <b>tabla</b> muestra familia, modelo, modalidad, proveedor asociado, precios de input/output/caché, unidad, fuente y vigencia. Acciones por fila: <b>Histórico</b> (icono de reloj) abre un diálogo con la línea temporal de precios y una gráfica; <b>Editar</b> y <b>Superseder</b> (cerrar el periodo vigente) solo para System Admin. El botón <b>«Nuevo precio»</b> abre el formulario de alta. El botón <b>«Sincronizar precios»</b> abre un diálogo de previsualización (dry-run) con el diff de cambios (nuevos, actualizados, subidas >10%, descontinuados); nada se escribe hasta confirmar, y una subida superior al 10% exige marcar una casilla de confirmación explícita antes de aplicar.</p>",
      fullPage: true,
    },
    {
      title: "Valores por defecto de plataforma",
      goto: "/admin/settings/platform-defaults",
      body: "<p>Edita los ajustes globales de la plataforma que no tienen página propia (modelo por defecto de agentes según ADR 0055, límites de ejecución, RAG, mantenimiento, etc.). Los ajustes se agrupan en <b>tarjetas por categoría</b> guiadas por el registro del backend, sin valores cableados en la interfaz.</p><p>Cada ajuste muestra su etiqueta, una descripción y su clave técnica. El control depende del tipo: una casilla para los booleanos (Activado/Desactivado), un campo numérico con mínimo y máximo para enteros, un campo de texto para decimales, y un control de <b>configuración de modelo</b> (proveedor por <i>kind</i>, modelo y temperatura) para el modelo por defecto de agentes. Cada control tiene su propio botón <b>«Guardar»</b>, que valida el valor por tipo y lo persiste; al elegir un proveedor, el desplegable de modelo se rellena con los modelos sincronizados de ese proveedor (o puedes escribir el nombre si no hay sincronizados). Solo System Admin.</p>",
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
      body: "<p>Restaura el stack completo o un único tenant desde un backup. Es una operación <b>larga y destructiva</b>: el backend la encola como job en segundo plano y la pantalla sondea su progreso. Exclusiva del System Admin.</p><p>El flujo es: <b>1)</b> selecciona un backup de la lista <b>«Backups disponibles»</b> (muestra id, si está cifrado, tamaño y ubicaciones local/remotas). <b>2)</b> revisa el <b>Preview</b> con el manifest, los artefactos y si admite restauración por tenant. <b>3)</b> elige el <b>tipo de restore</b>: completo (detiene el stack y restaura todo) o selectivo por tenant (indicando el Tenant ID UUID; muestra las tablas afectadas). <b>4)</b> pulsa <b>«Restaurar…»</b> para abrir el diálogo de <b>doble confirmación</b>, donde debes teclear exactamente el token indicado (el id del bundle para un restore completo, o <code><tenant_id>@<backup_id></code> para uno por tenant); el backend re-deriva y valida ese token. <b>5)</b> una vez encolado, la tarjeta <b>«Progreso del restore»</b> muestra el estado del job hasta completarse (SUCCESS) o fallar (FAILURE) con el detalle.</p>",
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
