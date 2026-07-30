import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef } from "../lib/manual";

// Manual 15 — Tutorial completo: proyecto Laravel con GitHub, agentes y MCP.
// Paralelo al manual 14 (CI4) con las diferencias REALES de Laravel: no hay
// equipo Laravel built-in (se adopta Backend/API y se especializa con una
// persona/skill Laravel listas para copiar), runtime php-phpunit o php-pest,
// comandos artisan, y troubleshooting propio (.env, key:generate, storage).
const manual: ManualDef = {
  order: "15",
  slug: "15-tutorial-proyecto-laravel",
  title: "Tutorial: proyecto Laravel con GitHub, agentes y MCP",
  audience: "Tenant admin / project owner que arranca un proyecto Laravel real",
  intro:
    "<p>Este tutorial monta, de cero y de punta a punta, un proyecto <b>Laravel</b> respaldado por un repositorio de <b>GitHub</b>, con un equipo de agentes <b>especializado en Laravel</b> (verás cómo: la plataforma no trae un equipo Laravel de serie, así que adoptaremos el equipo <i>Backend / API</i> built-in y lo especializaremos con una persona y una skill Laravel listas para copiar), y conectado por MCP a <b>Context7</b> (documentación de Laravel 11.x al día) y a <b>Atlassian</b> (Confluence + Jira). Al final tendrás un plan ejecutándose, el trabajo llegando como Pull Request a tu GitHub y la documentación fluyendo a Confluence.</p><p>El flujo es el mismo probado del manual 14: <b>requisitos → proyecto → equipo (＋especialización) → MCPs → tools → skills → primer plan → seguimiento → PR</b>. Donde Laravel difiere de CI4 (runtime de tests, comandos artisan, .env y APP_KEY, permisos de storage) este manual lo cubre con su paso propio. Sustituye <code>tuorg/turepo</code> y <code>tuempresa.atlassian.net</code> por los tuyos; los secretos van SIEMPRE en Vault.</p>",
  steps: [
    {
      title: "Requisitos previos (una sola vez)",
      goto: "/admin/dashboard",
      body: "<p>Idénticos a cualquier proyecto con GitHub + Atlassian:</p><ul><li><b>Proveedor LLM activo</b> (Plataforma → Proveedores LLM, botón «Probar» en verde). Cualquiera de los cuatro caminos del catálogo sirve, y puedes cambiarlo cuando quieras — el <i>vigía de credenciales</i> avisa si alguno caduca.</li><li><b>PAT de GitHub</b> con permiso <code>repo</code> sobre <code>tuorg/turepo</code> (push de ramas de plan + PRs automáticos).</li><li><b>API token de Atlassian</b> con escritura en tu espacio de Confluence y tu proyecto de Jira.</li><li><b>El repositorio GitHub</b> con la rama <code>main</code> inicializada. Consejo Laravel: si el repo ya contiene un esqueleto (<code>laravel new</code> commiteado), los agentes parten de él; si está vacío, la primera tarea del plan será crearlo — ambas rutas funcionan, pero dilo explícitamente en el plan (regla de tareas autocontenidas).</li></ul>",
      fullPage: true,
    },
    {
      title: "Crear el proyecto Laravel",
      goto: "/admin/projects",
      body: "<p>En <b>Recursos → Proyectos</b> → <b>«Nuevo proyecto»</b>:</p><ul><li><b>Nombre</b>: p. ej. <code>API Laravel</code>.</li><li><b>Stack</b>: <code>php</code>.</li><li><b>Runtime template</b>: <code>php-phpunit</code> si tu suite usa PHPUnit (el default de Laravel), o <code>php-pest</code> si usas <b>Pest</b> — ambos runtimes existen en el catálogo y traen las extensiones PHP necesarias (<code>intl</code> incluida). El runtime es el contenedor efímero donde los agentes ejecutan composer/artisan/tests vía <code>stack_exec</code>; los workers nunca ejecutan código de usuario.</li><li><b>Git remoto</b>: <code>https://github.com/tuorg/turepo.git</code>, rama <code>main</code>, PAT en Vault (ruta que indica el formulario).</li><li><b>Comandos permitidos</b> — la lista Laravel completa: <code>composer</code>, <code>php</code>, <code>vendor/bin/phpunit</code> (o <code>vendor/bin/pest</code>). Con <code>php</code> permitido, los agentes pueden ejecutar <code>php artisan …</code> (migraciones, make:*, key:generate, route:list) — artisan ES <code>php</code>, no necesita entrada propia.</li><li><b>Dominios permitidos</b>: vacío por ahora; <code>mcp.context7.com</code> se añade en el paso de Context7.</li></ul><p>Cada plan será una rama <code>plan/&lt;id&gt;-&lt;slug&gt;</code> en tu GitHub con commits trazados (<code>Plan-Id</code>/<code>Task-Id</code>/<code>Execution-Id</code>) y PR automático al completarse.</p>",
      fullPage: true,
    },
    {
      title: "Adoptar el equipo Backend / API…",
      goto: "/admin/teams",
      body: "<p>La plataforma trae 6 equipos built-in (CodeIgniter 4, Full-Stack Web, Backend / API, Research & Spec, DevOps & Platform, Data) — <b>no hay equipo Laravel de serie</b>, y no pasa nada: los equipos built-in genéricos + una especialización de persona/skill dan el mismo resultado. Elige según tu proyecto:</p><ul><li><b>Equipo Backend / API</b> — para una API Laravel (nuestro caso): PM, arquitecto, backend devs, QA, reviewer, writer.</li><li><b>Equipo Full-Stack Web</b> — si tu Laravel lleva frontend Blade/Livewire/Inertia con entidad propia.</li></ul><p>En <b>Recursos → Equipos</b>, pestaña built-in, pulsa <b>«Adoptar»</b> sobre <i>Backend / API</i> (ADR 0066: la adopción crea TU copia editable sin tocar el original) y asócialo al proyecto <code>API Laravel</code>. La <b>asignación por rol</b> (ADR 0091) despachará cada tarea al agente de su rol.</p>",
      fullPage: true,
    },
    {
      title: "…y especializarlo en Laravel (persona + prompt)",
      goto: "/admin/agents",
      body: "<p>Ahora conviertes agentes genéricos en agentes Laravel. Abre la ficha de cada dev/arquitecto/QA de TU copia del equipo → sección <b>Persona</b> → <b>Editar</b>, y AÑADE al system prompt un bloque de especialización (listo para copiar, ajústalo a tu versión):</p><pre>Especialización Laravel (11.x):\n- Sigue las convenciones del framework: Eloquent para el acceso a datos\n  (cuidado con N+1: usa with()/loadMissing()), FormRequest para la\n  validación, Policies para autorización, y recursos API (JsonResource)\n  para las respuestas.\n- Estructura: código de dominio en app/, rutas en routes/api.php,\n  migraciones SIEMPRE con down() funcional, seeders idempotentes.\n- Genera artefactos con artisan (php artisan make:model -mfs,\n  make:controller --api, make:request) en vez de crear ficheros a mano.\n- Tests: Feature tests contra la API (RefreshDatabase con sqlite en\n  memoria), Unit para lógica pura. Un endpoint sin test no está hecho.\n- Config por .env + config(); JAMÁS leas env() fuera de config/*.</pre><p>Hazlo al menos para el <b>Backend Dev</b> y el <b>Arquitecto</b> (los que escriben código); el QA agradece un bloque análogo orientado a Feature tests. La persona viaja como primer bloque del system prompt de TODOS sus runs — es la vía correcta para identidad permanente (los hábitos compartibles van en skills, dos pasos más abajo).</p>",
      fullPage: true,
    },
    {
      title: "MCP 1/2 — Context7 (docs de Laravel al día)",
      goto: "/admin/projects",
      body: "<p>Context7 sirve la documentación REAL de Laravel 11.x (y de cualquier paquete: Sanctum, Livewire, Horizon…) — los agentes consultan la API vigente en vez de recordar la de Laravel 8. En tu proyecto → pestaña <b>MCP servers</b> → <b>«Añadir MCP server»</b>:</p><ul><li><b>Nombre</b>: <code>context7</code> · <b>Transporte</b>: <code>streamable_http</code> · <b>URL</b>: <code>https://mcp.context7.com/mcp</code></li><li><b>Credencial</b>: opcional (rate limits sin cuenta); con API key, en Vault como <code>CONTEXT7_API_KEY</code>.</li></ul><p><b>⚠️ El gotcha del egress</b> (idéntico al manual 14): añade <code>mcp.context7.com</code> a los <b>dominios permitidos</b> del proyecto o el transporte morirá con <code>403 Filtered</code> (el proxy de salida es deny-by-default; el step <code>mcp_wire</code> del run te lo dirá con esas palabras).</p><p><b>«Probar»</b> → <code>resolve-library-id</code> + <code>get-library-docs</code> → <b>«Importar tools»</b> → quedan como <code>context7.resolve-library-id</code> y <code>context7.get-library-docs</code>.</p>",
      fullPage: true,
    },
    {
      title: "MCP 2/2 — Atlassian (Confluence + Jira)",
      goto: "/admin/projects",
      body: '<p>Server self-hosted con API token (el remoto oficial de Atlassian usa OAuth interactivo — no vale para runs autónomos). Sidecar en la red de agentes:</p><pre>services:\n  mcp-atlassian:\n    image: ghcr.io/sooperset/mcp-atlassian:latest\n    environment:\n      JIRA_URL: https://tuempresa.atlassian.net\n      CONFLUENCE_URL: https://tuempresa.atlassian.net/wiki\n      JIRA_USERNAME: bot@tuempresa.com\n      JIRA_API_TOKEN: ${ATLASSIAN_API_TOKEN}\n      CONFLUENCE_USERNAME: bot@tuempresa.com\n      CONFLUENCE_API_TOKEN: ${ATLASSIAN_API_TOKEN}\n    command: ["--transport", "streamable-http", "--port", "9000"]\n    networks: [agentic-agents]</pre><p>Declara en el proyecto: nombre <code>atlassian</code>, transporte <code>streamable_http</code>, URL <code>http://mcp-atlassian:9000/mcp</code> (hostname interno de la red de agentes → no pasa por el proxy de egress; declararlo en el proyecto ES la autorización). <b>«Probar»</b> → <b>«Importar tools»</b> → <code>atlassian.confluence_create_page</code>, <code>atlassian.jira_transition_issue</code>, <code>atlassian.jira_search</code>… Flujo validado e2e en esta plataforma con un run real.</p>',
      fullPage: true,
    },
    {
      title: "Asignar las tools MCP por mínimo privilegio",
      goto: "/admin/agents",
      body: "<p>Importar no basta: cada agente solo ve sus tools asignadas. Ficha del agente → <b>Tools</b> → pestaña <b>Avanzadas</b>:</p><ul><li><b>Backend Dev / Arquitecto</b>: <code>context7.resolve-library-id</code> + <code>context7.get-library-docs</code>.</li><li><b>Technical Writer</b>: <code>atlassian.confluence_create_page</code> (+ <code>context7.*</code> si documenta la API).</li><li><b>Project Manager</b>: <code>atlassian.jira_search</code> + <code>atlassian.jira_transition_issue</code>.</li></ul><p>Menos tools por agente = menos superficie y mejor foco del modelo. El síntoma de saltarse este paso: <code>unknown tool: atlassian.…</code> en el timeline del run.</p><p>Sobre otros MCP: valen las mismas recomendaciones del manual 14 — GitHub (plantilla del catálogo) si llevas el backlog en Issues, MCP propios de empresa como sidecars FastMCP, y <b>evita los templates stdio históricos</b> (binarios no empaquetados). Prefiere siempre servers HTTP.</p>",
      fullPage: true,
    },
    {
      title: "Skills Laravel: hábitos compartidos del equipo",
      goto: "/admin/agents",
      body: '<p>Crea estas skills (<code>POST /api/skills</code>, tenant admin) y asígnalas en la sección <b>Skills</b> de las fichas — las skills son hábitos COMPARTIBLES entre agentes (la persona era identidad de uno):</p><p><b>«Docs al día con Context7»</b> (devs + arquitecto + QA):</p><pre>Antes de usar una API de Laravel o de un paquete (Sanctum, Livewire,\nHorizon…) de la que no estés 100% seguro, resuelve la librería con\ncontext7.resolve-library-id y consulta context7.get-library-docs con el\ntopic concreto (p. ej. "eloquent relationships", "sanctum spa\nauthentication"). Prefiere SIEMPRE la firma de la documentación a tu\nmemoria.</pre><p><b>«El camino Laravel»</b> (devs):</p><pre>Al implementar en este proyecto Laravel: genera artefactos con artisan\n(make:model -mfs, make:controller --api, make:request), toda migración\nlleva down() funcional, valida con FormRequest, responde con\nJsonResource, y cada endpoint nuevo lleva su Feature test (sqlite en\nmemoria + RefreshDatabase). Ejecuta la suite con stack_exec antes de\ndar una tarea por terminada.</pre><p><b>«Sincronizar Jira al cerrar»</b> (PM):</p><pre>Cuando una tarea con issue de Jira asociada quede hecha, transiciónala\ncon atlassian.jira_transition_issue e incluye en el comentario el\nenlace al resultado (PR, página de Confluence).</pre><p>Recuerda: la skill no otorga la tool — asigna AMBAS (el campo <code>required_tools</code> documenta la dependencia). Mecanismo validado e2e: un fragment de skill indujo el uso de una tool que la tarea nunca nombraba.</p>',
      fullPage: true,
    },
    {
      title: "El primer plan Laravel: qué pedir",
      goto: "/admin/board",
      body: "<p>Un primer plan que demuestra todo el circuito (cópialo en el chat de planning y ajusta):</p><pre>Crea una API de gestión de tareas en Laravel 11:\n1. Esqueleto del proyecto (composer create-project laravel/laravel .)\n   con .env.example completo y APP_KEY generada en el entorno de tests.\n2. Modelo Task (título, descripción, estado, fecha límite) con\n   migración (down() incluido), factory y seeder.\n3. Endpoints REST /api/tasks (index con filtros, store, show, update,\n   destroy) con FormRequest, JsonResource y auth Sanctum.\n4. Feature tests de todos los endpoints (sqlite en memoria).\n5. Publica la guía de instalación y uso de la API como página de\n   Confluence en el espacio DOCS usando atlassian.confluence_create_page\n   y deja la issue LAR-1 de Jira en Done con\n   atlassian.jira_transition_issue.</pre><p>Fíjate en las reglas de convergencia aplicadas: cada tarea <b>crea sus propios insumos</b> (el esqueleto primero), las tools MCP van <b>con nombre exacto</b>, y los criterios de aceptación que genere el planner serán <b>verificables</b> (la self-review certifica contra ellos viendo el código y las tool calls del run). Revisa las tareas generadas antes de aprobar: es tu momento barato de corregir el rumbo.</p>",
      fullPage: true,
    },
    {
      title: "Aprobar, ejecutar y seguir en vivo",
      goto: "/admin/office",
      body: "<p>Aprueba el plan (gate humano explícito) e inicia la ejecución. El seguimiento es idéntico al manual 14 y merece los mismos tres marcadores:</p><ul><li><b>La Oficina</b> (esta captura): los agentes del equipo Backend/API sentados en la mesa del plan Laravel, con su estado real — ⌨️ trabajando, 🔍 revisando, 🚪 esperándote, 💫 en bucle (síntoma de tarea mal definida).</li><li><b>Runs</b>: el timeline de cada ejecución con el step <code>mcp_wire</code> (¿conectaron Context7 y Atlassian?), cada <code>tool_call</code> con argumentos, el coste por llamada al modelo, y el botón <b>🎬 Replay</b> para reproducirlo paso a paso.</li><li><b>Esperan tu decisión</b>: si algo se para en un gate (validación del plan, acción sensible, run escalado), aparece ahí ordenado por antigüedad.</li></ul><p>Al terminar las tareas, el plan pasa a validación humana: prueba la API (los Feature tests en verde en el run de QA, la página de Confluence publicada, LAR-1 en Done) y valida. El <b>PR automático</b> llegará a <code>tuorg/turepo</code> con toda la trazabilidad.</p>",
      fullPage: true,
    },
    {
      title: "Troubleshooting específico de Laravel",
      goto: "/admin/runs",
      body: "<p>Los tropiezos típicos de Laravel en la plataforma, por síntoma:</p><ul><li><b><code>No application encryption key has been specified</code></b> → falta <code>APP_KEY</code>: la tarea de esqueleto debe incluir <code>php artisan key:generate</code> (o un <code>.env.testing</code> con clave fija para los tests). Si los tests fallan con esto, la tarea 1 no fue autocontenida.</li><li><b>Tests que tocan la base de datos real</b> → exige en el plan (o en la skill «El camino Laravel») sqlite en memoria + <code>RefreshDatabase</code>; el runtime no levanta un MySQL para los tests.</li><li><b><code>storage/</code> o <code>bootstrap/cache</code> sin permisos</b> → añade a la tarea del esqueleto la creación de esos directorios con permisos de escritura (el worktree se crea limpio en cada run).</li><li><b><code>composer create-project</code> lentísimo o cortado</b> → el runtime cachea dependencias entre runs (dep-cache), pero la primera vez es pesada: dale a la tarea de esqueleto complejidad suficiente (no «xs») para que el presupuesto de wall-clock alcance.</li><li><b><code>403 Filtered</code> con Context7</b> → dominio fuera del egress del proyecto (paso de Context7).</li><li><b><code>unknown tool</code></b> → tool importada pero no asignada a ESE agente.</li><li><b>El PR no llega</b> → <code>pr_error</code> en la ficha del plan: rama base inexistente o PAT sin <code>repo</code>.</li></ul><p>Más fondo: <code>docs/03-guides/recetas-mcp-tools-skills.md</code>, el manual 14 (mismo circuito con CI4) y el manual 13 (runs y supervisión).</p>",
      fullPage: true,
    },
  ],
};

test(manual.title, async ({ page }) => {
  test.setTimeout(600_000);
  await login(page);
  await generateManual(page, manual);
});
