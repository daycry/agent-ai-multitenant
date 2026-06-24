import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef } from "../lib/manual";

// GENERADO desde el workflow de redacción. Editable a mano; reejecutable.
const manual: ManualDef = {
  order: "10",
  slug: "10-desarrolladores",
  title: "Manual de desarrolladores: portal, API, SDKs, tutoriales y webhooks",
  audience:
    "Desarrolladores e integradores técnicos (con rol Tenant Admin para acuñar tokens y configurar webhooks)",
  intro:
    "<p>Este manual explica cómo integrar sistemas externos con la plataforma agéntica multi-tenant a través del <b>Portal de desarrollador</b> y del <b>visor de documentación</b> del panel de administración.</p><p>Recorrerás la página de inicio del portal, la referencia de la API pública v1 (OpenAPI 3.1 y Swagger UI), los SDKs oficiales de Python y TypeScript, los tutoriales paso a paso (acuñar un token, llamar a la API y configurar un webhook) y la guía de webhooks entrantes con firma HMAC. También se cubre el visor de documentación canónica del producto dentro del área <code>/admin</code>.</p><p>El Portal de desarrollador es público (no requiere sesión) y no hace llamadas a la API: es una capa fina que enlaza el contrato OpenAPI, los READMEs de los SDKs y la documentación canónica bajo <code>/docs</code>.</p>",
  steps: [
    {
      title: "Visor de documentación (panel de administración)",
      goto: "/admin/docs",
      body: "<p>Esta pantalla, dentro del área autenticada <code>/admin</code>, es el <b>visor de documentación</b> que permite explorar la documentación de cualquier proyecto del tenant en un solo lugar. La cabecera muestra el título <b>Documentación</b> con una breve descripción.</p><p>La columna izquierda tiene dos pestañas: <b>Explorar</b> y <b>Marcadores</b>. En <b>Explorar</b> encontrarás, de arriba a abajo: un buscador instantáneo de texto sobre los documentos, un panel de filtros por <b>categoría</b> y por <b>tipo</b> (que se activa al seleccionar un proyecto), y un árbol navegable de carpetas y archivos. Al seleccionar un documento, su ruta queda fijada en la URL (<code>?project=<id>&path=<ruta></code>), de modo que el enlace es compartible y sobrevive a recargas.</p><p>Puedes marcar cualquier documento con la estrella desde el árbol, desde un resultado de búsqueda o desde la cabecera del visor; los marcados aparecen en la pestaña <b>Marcadores</b> (con un contador) y se guardan localmente en el navegador por tenant. El panel principal de la derecha renderiza el documento seleccionado en Markdown con su tabla de contenidos.</p>",
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
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  test.setTimeout(300_000);
  await login(page);
  await generateManual(page, manual);
});
