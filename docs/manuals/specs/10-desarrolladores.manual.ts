import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef, Step } from "../lib/manual";
import { seededPhpProjectId } from "../lib/seed-helper";

// El proyecto demo "Hello World PHP" se siembra con lib/seed-demo-data.mjs antes
// de generar; sus sub-páginas (webhooks entrantes, MCP servers) se capturan con
// contenido real. Sin seed, esos pasos se omiten.
const PID = seededPhpProjectId();

// Pasos que dependen de un proyecto sembrado (rutas con [id]).
const projectSteps: Step[] = PID
  ? [
      {
        title: "Webhooks entrantes: configuración por proyecto en el panel",
        goto: `/admin/projects/${PID}/incoming-webhooks`,
        body: "<p>Además de la vía programática del tutorial (<code>POST /projects/{project_id}/incoming-webhooks</code>), el panel de administración ofrece una <b>pantalla de gestión visual</b> de los webhooks entrantes de cada proyecto, en Proyecto → Webhooks entrantes. Requiere rol <b>Tenant Admin</b> (la pantalla completa va tras un <code>RoleGuard</code> y el backend aplica RBAC + RLS de tenant y proyecto).</p><p>Cada configuración es una <b>tarjeta</b> con: el badge de su <b>origen</b> (GitHub, GitLab, Jira, Sentry, Linear o Genérico), el nombre, el estado <i>habilitado/deshabilitado</i>, la <b>URL completa del endpoint público</b> que debes registrar en la herramienta externa (el <code>incoming_path</code> relativo que devuelve el backend, prefijado con la URL base de la API de tu instalación) y la fecha del último evento recibido.</p><ul><li><b>Editar</b>: cambia nombre, estado y reglas de mapeo (el origen es fijo tras crear).</li><li><b>Rotar secreto</b>: genera un secreto de firma nuevo y lo muestra una única vez; el anterior queda inválido de inmediato — recuerda actualizar el emisor externo en el mismo momento.</li><li><b>Eliminar</b>: baja lógica (soft-delete) de la configuración.</li><li><b>Entregas recientes</b>: despliega el registro de las últimas entregas recibidas — id de entrega del emisor, tipo de evento, si la firma se <i>verificó</i> y el timestamp de recepción. Es la primera parada para depurar por qué un webhook no dispara acciones.</li></ul><p>Cuando creas o rotas, el <b>secreto de firma se muestra UNA sola vez</b> en un banner copiable en la parte superior; después, el listado solo devuelve metadatos. Es el mismo principio de secretos de toda la plataforma: si lo pierdes, no se puede recuperar — se rota.</p>",
        fullPage: true,
      },
      {
        title: "Crear un webhook entrante: origen y reglas de mapeo (diálogo)",
        goto: `/admin/projects/${PID}/incoming-webhooks`,
        action: async (page) => {
          await page
            .getByTestId("webhook-add-button")
            .click()
            .catch(() => {});
          await page.waitForTimeout(600);
        },
        body: "<p>El botón de añadir abre el diálogo de creación. Los campos base son el <b>origen</b> (el catálogo cerrado: GitHub, GitLab, Jira, Sentry, Linear o «Genérico (HMAC bare-hex)» para cualquier emisor propio), un <b>nombre</b> descriptivo y la casilla <b>habilitado</b>.</p><p>El corazón de la configuración son las <b>reglas de mapeo</b> (<code>action_mappings</code>): la lista ordenada que traduce eventos entrantes en acciones de la plataforma. Cada regla tiene:</p><ul><li><b>Tipo de evento</b>: el <code>event_type</code> del payload a casar (o <code>*</code> como comodín para cualquier evento).</li><li><b>Acción</b>: qué hace la plataforma al recibirlo — <b>crear tarea</b>, <b>comentar tarea</b> o <b>escalar tarea</b>.</li><li><b>Plantillas opcionales</b> de título y cuerpo, para componer el texto de la tarea o del comentario a partir del evento.</li><li><b>Tarea destino</b> opcional, para las acciones de comentar/escalar sobre una tarea concreta.</li></ul><p>Puedes añadir y quitar filas de reglas libremente antes de guardar. Al <b>crear</b>, la respuesta incluye el <code>signing_secret</code> en claro una única vez (banner copiable): configúralo inmediatamente en la herramienta emisora junto con la URL del endpoint. A partir de ahí, cada POST entrante se verifica contra ese secreto (HMAC-SHA256 sobre el body crudo, comparación en tiempo constante) siguiendo el orden de checks fail-closed descrito en la sección de Webhooks del portal.</p>",
        fullPage: false,
      },
      {
        title: "Servidores MCP del proyecto",
        goto: `/admin/projects/${PID}/mcp-servers`,
        body: "<p>Esta pantalla (Proyecto → MCP servers) gestiona los <b>servidores MCP</b> (Model Context Protocol) configurados en un proyecto: procesos o endpoints externos que exponen <i>tools</i> adicionales que los agentes del proyecto pueden usar (consultar un Jira, leer un Google Drive, lanzar un navegador…). La configuración vive en la propiedad <code>mcp_servers</code> del proyecto y se persiste reemplazando el array completo vía <code>PUT /projects/{id}</code>; el backend la valida con Pydantic y rechaza configuraciones inválidas (nombres duplicados, campos que no casan con el transporte, referencias de credencial sin el prefijo <code>vault:</code>).</p><p>Cada servidor es una <b>tarjeta</b> con su nombre, el badge del <b>transporte</b> — <code>stdio</code> (subproceso local), <code>sse</code> (stream HTTP) o <code>streamable_http</code> —, su indicador de autenticación y los botones de <b>editar</b> y <b>eliminar</b>. Según el transporte, la tarjeta muestra el comando y argumentos (stdio) o la URL (sse/streamable_http), además del timeout configurado.</p><p>Para integradores, esta es la vía recomendada de extender las capacidades de los agentes con sistemas propios: empaqueta tu integración como servidor MCP estándar y regístrala aquí; los agentes la descubren con namespacing <code>&lt;server&gt;.&lt;tool&gt;</code> (ADR 0052), sin tocar el código de la plataforma.</p>",
        fullPage: true,
      },
      {
        title: "Añadir un servidor MCP: plantillas, Vault y prueba de conexión (diálogo)",
        goto: `/admin/projects/${PID}/mcp-servers`,
        action: async (page) => {
          await page
            .getByTestId("mcp-add-button")
            .click()
            .catch(() => {});
          await page.waitForTimeout(600);
        },
        body: "<p>El diálogo de alta arranca con un <b>selector de plantilla</b> alimentado por el catálogo verificado del backend (<code>GET /mcp-catalog</code>, 22 plantillas: GitHub, GitLab, Jira, Confluence, Google Drive, Gmail, Calendar, Slack, Teams, Discord, Notion, PostgreSQL, Sentry, Grafana, Brave, Tavily, Puppeteer, Memory, Sequential Thinking…), agrupadas por categoría (documentos, control de versiones, bases de datos, comunicación, issue trackers, observabilidad, búsqueda web, navegador…). Elegir una plantilla rellena automáticamente transporte, comando/argumentos o URL, variables de entorno estáticas y — si la plantilla declara secretos — el <code>auth_ref</code> ya <b>renderizado con el UUID del proyecto</b>: la ruta exacta de Vault donde tu administrador debe depositar la credencial, sin riesgo de teclearla mal.</p><p>Los campos también se pueden rellenar a mano: <b>nombre</b> (único en el proyecto), <b>transporte</b>, <b>comando + argumentos</b> (stdio) o <b>URL</b> (sse/streamable_http), tablas clave/valor de <b>env</b> y <b>headers</b>, el <b>timeout</b> en segundos y, en la sección avanzada, la credencial gestionada o el <code>auth_ref</code> crudo (siempre con prefijo <code>vault:</code> — la credencial en sí <b>nunca</b> se pega en esta configuración).</p><p>El botón <b>«Probar conexión»</b> conecta con el servidor y abre un panel inline con el resultado: nombre y versión del server, el número de <b>tools descubiertos</b> y su lista (cada tool con su descripción). Si el servidor falla, verás el error tipado para diagnosticarlo antes de guardar.</p><p>Desde ese mismo panel puedes <b>importar tools al catálogo del proyecto</b>: marca las que te interesen y pulsa «Importar N tools al catálogo». Se registran con origen <i>MCP</i>, con el nombre namespaced <code>&lt;server&gt;.&lt;tool&gt;</code> (para que <code>github.read_file</code> no se confunda con el <code>read_file</code> nativo) y en el nivel de riesgo <b>«Aislada»</b>, de modo que quedan sujetas al flujo normal de asignación y aprobación de herramientas antes de que un agente pueda usarlas.</p>",
        fullPage: false,
      },
    ]
  : [];

// GENERADO desde el workflow de redacción. Editable a mano; reejecutable.
const manual: ManualDef = {
  order: "10",
  slug: "10-desarrolladores",
  title: "Manual de desarrolladores: portal, API, SDKs, tutoriales y webhooks",
  audience:
    "Desarrolladores e integradores técnicos (con rol Tenant Admin para acuñar tokens y configurar webhooks)",
  intro:
    "<p>Este manual explica cómo integrar sistemas externos con la plataforma agéntica multi-tenant a través del <b>Portal de desarrollador</b>, del <b>visor de documentación</b> del panel de administración y de las <b>pantallas de integración por proyecto</b> (webhooks entrantes y servidores MCP).</p><p>Recorrerás la página de inicio del portal, la referencia de la API pública v1 (OpenAPI 3.1 y Swagger UI), los SDKs oficiales de Python y TypeScript, los tutoriales paso a paso (acuñar un token, llamar a la API y configurar un webhook) y la guía de webhooks entrantes con firma HMAC. Después, dentro del área autenticada, verás la gestión visual de los webhooks entrantes de un proyecto (configuraciones, secreto mostrado una sola vez, rotación y registro de entregas) y la configuración de servidores MCP (catálogo de plantillas, credenciales vía Vault, prueba de conexión e importación de tools descubiertos).</p><p>El Portal de desarrollador es público (no requiere sesión) y no hace llamadas a la API: es una capa fina que enlaza el contrato OpenAPI, los READMEs de los SDKs y la documentación canónica bajo <code>/docs</code>. Las pantallas por proyecto, en cambio, requieren sesión con rol <b>Tenant Admin</b>.</p><p>Dos constantes de seguridad que verás repetidas en todo el manual: los <b>secretos se muestran una única vez</b> (token de API, secreto de firma de webhook) y después solo circulan metadatos; y el <b>aislamiento entre tenants</b> lo garantiza PostgreSQL RLS — un identificador de otro tenant devuelve un 404 limpio, indistinguible de un recurso inexistente.</p>",
  steps: [
    {
      title: "Visor de documentación (panel de administración)",
      goto: "/admin/docs",
      body: "<p>Esta pantalla, dentro del área autenticada <code>/admin</code>, es el <b>visor de documentación</b> que permite explorar la documentación de cualquier proyecto del tenant en un solo lugar. La cabecera muestra el título <b>Documentación</b> con una breve descripción.</p><p>La columna izquierda tiene dos pestañas: <b>Explorar</b> y <b>Marcadores</b>. En <b>Explorar</b> encontrarás, de arriba a abajo: un buscador instantáneo de texto sobre los documentos, un panel de filtros por <b>categoría</b> y por <b>tipo</b> (que se activa al seleccionar un proyecto), y un árbol navegable de carpetas y archivos. Al seleccionar un documento, su ruta queda fijada en la URL (<code>?project=&lt;id&gt;&amp;path=&lt;ruta&gt;</code>), de modo que el enlace es compartible y sobrevive a recargas.</p><p>Puedes marcar cualquier documento con la estrella desde el árbol, desde un resultado de búsqueda o desde la cabecera del visor; los marcados aparecen en la pestaña <b>Marcadores</b> (con un contador) y se guardan localmente en el navegador por tenant. El panel principal de la derecha renderiza el documento seleccionado en Markdown con su tabla de contenidos.</p>",
      fullPage: true,
    },
    {
      title: "Portal de desarrollador: inicio",
      goto: "/developers",
      body: "<p>Esta es la página de entrada del <b>Portal de desarrollador</b>, una zona pública (fuera de <code>/admin</code>) que reúne todo lo necesario para integrar con la API pública v1. La cabecera incluye la marca <b>Portal de desarrollador</b> y una barra de navegación con: <b>Inicio</b>, <b>API Reference</b>, <b>SDKs</b>, <b>Tutoriales</b> y <b>Webhooks</b>.</p><p>El cuerpo presenta cuatro tarjetas enlazadas que resumen y dan acceso a cada sección: <b>API Reference</b> (contrato OpenAPI 3.1 y Swagger UI, autenticación por <code>X-API-Token</code>, scopes, rate limit y paginación), <b>SDKs oficiales</b> (clientes tipados de Python y TypeScript generados desde el OpenAPI), <b>Tutoriales</b> (tres pasos guiados) y <b>Webhooks entrantes</b> (orígenes soportados, firma HMAC-SHA256 y orden de checks).</p><p>Debajo, la sección <b>Documentación canónica</b> recuerda que la documentación completa del producto vive bajo <code>/docs</code> con la estructura de 7 carpetas y enlaza las referencias más útiles para integrar: la referencia API pública (<code>docs/04-reference/public-api.md</code>), la guía de integración (<code>docs/03-guides/api-publica-y-webhooks.md</code>) y los runbooks operativos (<code>docs/06-runbooks/</code>).</p>",
      fullPage: true,
    },
    {
      title: "API Reference: contrato OpenAPI y autenticación",
      goto: "/developers/api-reference",
      body: "<p>Esta página documenta la <b>API pública v1</b> (<code>/api/v1</code>), una fachada fina y versionada sobre el dominio. La primera tarjeta, <b>Contrato OpenAPI 3.1 + Swagger UI</b>, enlaza los dos recursos públicos que conviene leer antes de acuñar el primer token: <code>/api/v1/openapi.json</code> (el contrato crudo, consumido por los SDKs) y <code>/api/v1/docs</code> (el Swagger UI interactivo para explorar y probar). Debes sustituir la ruta por la URL pública de tu instalación.</p><p>La tarjeta <b>Autenticación, scope y aislamiento</b> explica las reglas clave: el token viaja siempre en la cabecera <code>X-API-Token</code> (nunca como query param); los <code>GET</code> requieren scope <code>read</code> y los <code>POST</code> requieren <code>write</code> (un <code>write</code> no concede <code>read</code> implícito); el aislamiento entre tenants lo garantiza PostgreSQL RLS (un id ajeno devuelve un <code>404</code> limpio); hay un rate limit por token (100 req/min por defecto) con cabeceras <code>X-RateLimit-*</code>; y todas las listas son paginadas con <code>limit</code>/<code>offset</code>. Incluye un ejemplo <code>curl</code>.</p><p>Más abajo, la tabla <b>Endpoints (scope mínimo)</b> lista las rutas disponibles (projects, plans, tasks, conversations y knowledge bases) con su método y scope requerido, y la tabla <b>Códigos de estado</b> describe el significado de 200/201, 400, 401, 403, 404 y 429.</p>",
      fullPage: true,
    },
    {
      title: "SDKs oficiales: Python y TypeScript",
      goto: "/developers/sdks",
      body: "<p>Esta página describe los dos <b>SDKs oficiales</b> tipados sobre la API pública v1. Ambos se generan desde el contrato OpenAPI 3.1 en proceso (sin servidor vivo), de modo que siempre casan con el servidor: modelos generados más un cliente fino que fija el <code>X-API-Token</code> una vez y eleva errores tipados.</p><p>La tarjeta <b>SDK Python — agentic-platform-sdk</b> indica sus dependencias de runtime (<code>httpx</code> + <code>pydantic</code> v2), muestra el bloque de <b>Instalación</b> (<code>pip install</code> desde el monorepo o desde el registro interno) y un <b>Quickstart</b> con código de ejemplo para listar, obtener y crear proyectos y para consultar plans, tasks (project-scoped) y knowledge bases (tenant-scoped). Una respuesta non-2xx eleva <code>ApiError</code> con <code>status_code</code> y <code>body</code> para ramificar por 401, 403, 404 o 429.</p><p>La tarjeta <b>SDK TypeScript — @agentic-platform/sdk</b> destaca que tiene cero dependencias de runtime (usa <code>fetch</code> nativo, Node 18+ o navegador) y muestra su instalación y quickstart equivalentes. La última tarjeta, <b>Regeneración (reproducibilidad)</b>, explica cómo regenerar los modelos tras cambiar el contrato ejecutando los scripts <code>generate</code> de cada SDK.</p>",
      fullPage: true,
    },
    {
      title: "Tutoriales: token, llamada a la API y webhook",
      goto: "/developers/tutorials",
      body: "<p>Esta página recoge <b>tres tutoriales paso a paso</b> para pasar de cero a integrado. Requieren el stack en marcha y el rol <b>Tenant Admin</b> (acuñar tokens y gestionar webhooks lo exigen). Recuerda sustituir <code>platform.example.com</code> por la URL pública de tu instalación.</p><p>El paso <b>1 · Acuñar un X-API-Token</b> muestra cómo crear la credencial por tenant con <code>POST /auth/api-tokens</code>, indicando que el token claro se devuelve una sola vez (hay que guardarlo) y explicando los campos: <code>scopes</code> (<code>read</code> solo permite GET, añade <code>write</code> para crear), y los opcionales <code>rate_limit</code>, <code>expires_at</code> e <code>ip_allowlist</code>; la revocación se hace con <code>DELETE /auth/api-tokens/{token_id}</code>, efectiva de inmediato.</p><p>El paso <b>2 · Llamar a la API v1</b> muestra ejemplos <code>curl</code> para listar y crear proyectos con el token en la cabecera <code>X-API-Token</code>, recordando las reglas de scope y los códigos 404/429. El paso <b>3 · Configurar un webhook entrante</b> muestra cómo crear la configuración con <code>POST /projects/{project_id}/incoming-webhooks</code>, que devuelve el <code>incoming_path</code> y el <code>signing_secret</code> en claro una sola vez. La página enlaza a las secciones de SDKs y Webhooks para profundizar.</p>",
      fullPage: true,
    },
    {
      title: "Webhooks entrantes: orígenes, firma HMAC y checks",
      goto: "/developers/webhooks",
      body: "<p>Esta página documenta los <b>webhooks entrantes</b>: un tool externo hace POST de un evento firmado con HMAC y el sistema lo verifica y lo mapea a una acción. El endpoint es público, por lo que <b>la propia firma HMAC actúa como autenticación</b>.</p><p>La tarjeta <b>Orígenes soportados (catálogo cerrado)</b> incluye una tabla con cada <code>origin</code> (github, gitlab, jira, sentry, linear y generic), la cabecera de firma que espera y los eventos típicos que generan acciones (crear tarea, comentar o escalar). Todas las firmas son HMAC-SHA256 sobre el body crudo, comparadas en tiempo constante; el endpoint público es <code>POST /webhooks/incoming/{origin}/{config_id}</code>.</p><p>La tarjeta <b>Orden de checks (fail-closed)</b> enumera los seis pasos que sigue el sistema: límite de tamaño del body (413), resolver la config (404), rate limit por config (429), verificar la firma HMAC (401, sin acción si falla), mapear y actuar, y persistir de forma idempotente. La última tarjeta, <b>Verificar la firma manualmente (genérico)</b>, muestra cómo calcular la firma con <code>openssl</code> y enviarla con <code>curl</code>, e indica que la rotación del secreto se hace con <code>POST …/incoming-webhooks/{config_id}/rotate-secret</code>, que invalida el secreto anterior de inmediato.</p>",
      fullPage: true,
    },
    ...projectSteps,
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  test.setTimeout(300_000);
  await login(page);
  await generateManual(page, manual);
});
