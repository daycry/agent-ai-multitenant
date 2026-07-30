import { test } from "@playwright/test";
import { credsFromEnv, login } from "../lib/auth";
import { generateManual, ManualDef } from "../lib/manual";

/**
 * Manual 00 — Introducción y primeros pasos.
 * Documenta el FLUJO DE LOGIN REAL paso a paso: pantalla de login → credenciales
 * → envío → panel principal (dashboard). No pre-autentica: ejecuta el login como
 * parte del manual y espera a que el dashboard cargue antes de capturarlo.
 * Después recorre la estructura del panel: navegación lateral completa, barra
 * superior (tenant, rol, idioma), menú de usuario y documentación integrada.
 */
const c = credsFromEnv();

const manual: ManualDef = {
  slug: "00-introduccion-y-primeros-pasos",
  order: "00",
  title: "Introducción y primeros pasos",
  audience: "Cualquier usuario del panel (administrador de tenant)",
  intro: `
    <p>La <b>Plataforma Agéntica Multi-Tenant</b> permite construir y orquestar
    equipos de agentes de IA que trabajan sobre tus proyectos de software: un
    Project Manager, un Arquitecto, desarrolladores backend/frontend, QA,
    Reviewer… colaborando sobre <b>planes</b> y <b>tareas</b> reales, con código
    versionado en git y ejecución aislada en contenedores. Toda la operativa se
    hace desde el <b>panel de administración</b> que documenta este manual.</p>
    <p>Este primer manual te lleva paso a paso desde el inicio de sesión hasta el
    panel principal y te enseña a orientarte: qué muestra el dashboard, cómo se
    organiza la <b>navegación lateral</b> en grupos, qué contiene la <b>barra
    superior</b> (organización activa, rol, selector de idioma, menú de usuario)
    y dónde encontrar la documentación integrada.</p>
    <p>También explica la organización <b>multi-tenant</b>: tus datos viven
    aislados dentro de tu <b>tenant</b> (organización) y nunca se mezclan con los
    de otros. El aislamiento se aplica en la propia base de datos (Row-Level
    Security), no solo en la interfaz.</p>`,
  steps: [
    {
      title: "Abrir el panel: pantalla de inicio de sesión",
      goto: "/login",
      body: `<p>Abre la URL del panel en tu navegador (la que te haya facilitado
        tu administrador; en una instalación local suele ser
        <code>http://localhost:8080</code>). Lo primero que verás es la pantalla
        de <b>inicio de sesión</b>: la marca de la plataforma a la izquierda y el
        formulario de acceso con dos campos, <b>email</b> y <b>contraseña</b>, y
        el botón <code>Sign in</code>.</p>
        <ul>
          <li>Si tu organización tiene <b>SSO</b> (Google, Microsoft, SAML…)
            configurado, verás además los botones de cada proveedor bajo el
            formulario. Con SSO no necesitas contraseña local: pulsa el botón de
            tu proveedor y autentícate allí.</li>
          <li>El acceso está protegido contra fuerza bruta: tras varios intentos
            fallidos seguidos, el sistema limita temporalmente los reintentos
            desde tu dirección. Espera unos minutos y vuelve a intentarlo.</li>
          <li>Si no tienes credenciales todavía, pídelas a tu administrador de
            tenant: las cuentas las crea la propia organización (no hay
            auto-registro público).</li>
        </ul>
        <p><b>Buena práctica</b>: guarda la URL del panel en marcadores y accede
        siempre por HTTPS en entornos de producción.</p>`,
    },
    {
      title: "Introducir las credenciales",
      body: `<p>Escribe tu <b>email</b> corporativo y tu <b>contraseña</b> en los
        campos del formulario. La contraseña se muestra siempre enmascarada
        mientras la tecleas y en el servidor se almacena de forma segura (hash
        <code>argon2id</code>), nunca en claro: ni los administradores pueden
        verla.</p>
        <ul>
          <li>El campo email valida el formato antes de enviar; si dejas algún
            campo vacío, el formulario no se envía.</li>
          <li>Las credenciales viajan cifradas (HTTPS) y la sesión resultante usa
            tokens de corta duración que el panel renueva automáticamente.</li>
        </ul>
        <p>Cuando ambos campos estén rellenos, pulsa <code>Sign in</code> para
        continuar.</p>`,
      action: async (page) => {
        // Rellenar JUSTO antes de capturar (el formulario mostrará tus datos).
        await page.locator("#email").fill(c.email);
        await page.locator("#password").fill(c.password);
      },
    },
    {
      title: "Acceder al panel (el dashboard se carga tras autenticar)",
      body: `<p>Al pulsar <code>Sign in</code>, la plataforma valida tus
        credenciales y resuelve a qué <b>organización (tenant)</b> perteneces.
        Hay dos escenarios:</p>
        <ul>
          <li><b>Una sola organización</b>: entras directamente al panel de esa
            organización, sin pasos intermedios.</li>
          <li><b>Varias organizaciones</b> (por ejemplo, si eres administrador de
            sistema o colaboras con varios equipos): se muestra un <b>selector de
            tenant</b> para que elijas en cuál quieres trabajar en esta sesión.
            Podrás cambiar más tarde sin cerrar sesión.</li>
        </ul>
        <p>Tras resolver el tenant llegas al <b>panel principal</b> (dashboard),
        que es la pantalla de aterrizaje de todas las sesiones. A partir de aquí,
        todo lo que veas y hagas queda acotado a la organización elegida.</p>`,
      settleMs: 1800,
      // Login robusto (re-rellena + envía + espera a /admin), idéntico al resto
      // de manuales. La captura se toma ya en el dashboard, no en el login.
      action: async (page) => {
        await login(page, c);
      },
    },
    {
      title: "El panel principal (dashboard)",
      goto: "/admin/dashboard",
      settleMs: 1500,
      body: `<p>El <b>Dashboard</b> es la vista de estado general de tu tenant.
        Se organiza en dos bloques:</p>
        <ul>
          <li><b>Fila de indicadores (KPI)</b>, arriba: cuatro tarjetas con los
            números clave del tenant — <b>Servicios</b> (cuántos servicios del
            stack están monitorizados y su estado global), <b>Agentes
            visibles</b> (los agentes de IA disponibles: built-in de la
            plataforma, del tenant y locales de proyecto), <b>Equipos</b>
            (plantillas de equipo + equipos propios) y <b>Proyectos</b> (los
            proyectos activos, excluyendo plantillas).</li>
          <li><b>Salud de servicios</b>, debajo: una tarjeta por servicio del
            stack (API, base de datos, cola de tareas, almacenamiento…) con un
            icono y una etiqueta de estado: <code>ok</code> en verde,
            <code>degraded</code> en ámbar y <code>down</code> en rojo. Si un
            servicio aporta detalle adicional (versión, latencia…), se muestra
            bajo su nombre.</li>
        </ul>
        <p>La cabecera indica el <b>estado general</b> del sistema y recuerda que
        la información se <b>auto-refresca cada 30 segundos</b>: no necesitas
        recargar la página para ver si un servicio se recupera.</p>
        <p><b>Para qué sirve</b>: es tu primera parada para diagnosticar
        problemas. Si un agente no responde o una ingesta falla, comprueba aquí
        si algún servicio está <code>degraded</code> o <code>down</code> antes de
        buscar causas más complejas.</p>`,
    },
    {
      title: "La navegación lateral: grupos y secciones",
      goto: "/admin/dashboard",
      settleMs: 1200,
      // Expande todos los grupos visibles del menú para que la captura muestre
      // el árbol de navegación completo (los grupos recuerdan su estado en
      // localStorage, así que algunos pueden venir plegados).
      action: async (page) => {
        const groupIds = ["trabajo", "recursos", "config-tenant", "plataforma", "cortex", "ayuda"];
        for (const id of groupIds) {
          try {
            const btn = page.getByTestId(`nav-group-${id}`);
            if ((await btn.count()) > 0 && (await btn.getAttribute("aria-expanded")) === "false") {
              await btn.click();
              await page.waitForTimeout(150);
            }
          } catch {
            /* grupo no visible para este rol — se ignora */
          }
        }
      },
      body: `<p>La <b>barra lateral izquierda</b> es el mapa de toda la
        aplicación. Está organizada en <b>grupos plegables</b>; haz clic en el
        título de un grupo para expandirlo o contraerlo (el panel recuerda tu
        preferencia). La sección activa se resalta con una franja de color a la
        izquierda, y el grupo que la contiene se auto-expande al cargar.</p>
        <ul>
          <li><b>Trabajo</b> — la operativa del día a día: <i>Dashboard</i>
            (estado general), <i>Mis tareas</i> (bandeja personal de trabajo
            asignado), <i>Tablero</i> (el doble Kanban de planes y tareas),
            <i>Runs</i> (histórico de ejecuciones de agentes), <i>Aprobaciones</i>
            (solicitudes de validación humana pendientes), <i>Bandeja</i>
            (notificaciones recibidas) y <i>Asistente</i> (el asistente personal
            del tenant, visible para administradores).</li>
          <li><b>Recursos</b> (administradores) — el catálogo del tenant:
            <i>Agentes</i>, <i>Catálogo</i> de herramientas, <i>Agentes
            humanos</i>, <i>Equipos</i>, <i>Proyectos</i>, <i>Knowledge Bases</i>
            (bases de conocimiento RAG), <i>Memorias</i> y <i>Documentos</i>.</li>
          <li><b>Configuración del tenant</b> (administradores) — el gobierno de
            la organización: <i>Guardrails</i>, <i>Validación humana</i>
            (políticas de aprobación), <i>Notificaciones</i>, <i>Calidad
            (Evals)</i>, <i>Estadísticas</i>, <i>Marketplace</i> y
            <i>Settings</i>.</li>
          <li><b>Plataforma</b> — solo visible para el <b>administrador de
            sistema</b>: usuarios globales, proveedores LLM, Ollama &amp;
            Embeddings, valores por defecto, modelos y precios, Auth/SSO y
            copias de seguridad (backups, destinos y restauración).</li>
          <li><b>Ayuda</b> — acceso a la <i>Documentación</i> integrada.</li>
        </ul>
        <p>Lo que ves depende de tu <b>rol</b>: un usuario estándar solo ve el
        grupo <i>Trabajo</i> y <i>Ayuda</i>; los grupos administrativos aparecen
        únicamente si tu rol lo permite (y el servidor vuelve a comprobar cada
        permiso: ocultar el menú es comodidad, no la barrera de seguridad).</p>
        <p>En pantallas pequeñas la barra lateral se convierte en un <b>cajón
        deslizante</b>: ábrelo con el botón de menú (☰) de la esquina superior
        izquierda.</p>`,
    },
    {
      title: "La barra superior: organización, rol e idioma",
      goto: "/admin/dashboard",
      settleMs: 1200,
      fullPage: false,
      body: `<p>La <b>barra superior</b> está presente en todas las pantallas del
        panel y concentra tu contexto de sesión, de izquierda a derecha en su
        zona derecha:</p>
        <ul>
          <li><b>Organización (tenant) actual</b> — una etiqueta con el nombre de
            la organización en la que estás trabajando. Para un administrador de
            sistema es un <b>selector desplegable</b> con el que puede cambiar de
            tenant (e incluso crear uno nuevo); para el resto de roles es una
            etiqueta informativa fija.</li>
          <li><b>Insignia de rol</b> — muestra tu nivel de permisos con un código
            de color: <code>system_admin</code> en ámbar (administración global
            de la plataforma), <code>admin</code> en azul (administrador del
            tenant) y <code>user</code> en gris (usuario estándar). Así sabes de
            un vistazo con qué "sombrero" estás operando.</li>
          <li><b>Selector de idioma ES / EN</b> — cambia el idioma de la interfaz
            al instante, sin recargar. La plataforma soporta español e inglés; tu
            elección se conserva entre sesiones.</li>
          <li><b>Menú de usuario</b> — tu avatar con la inicial, nombre y email
            (lo vemos en el paso siguiente).</li>
        </ul>
        <p>No hay selector de tema en la barra: el panel usa el tema visual
        configurado de la aplicación.</p>`,
    },
    {
      title: "El menú de usuario: perfil y cierre de sesión",
      goto: "/admin/dashboard",
      settleMs: 1200,
      fullPage: false,
      action: async (page) => {
        await page
          .getByTestId("user-menu")
          .click()
          .catch(() => {});
        await page.waitForTimeout(400);
      },
      body: `<p>En el extremo derecho de la barra superior está tu <b>menú de
        usuario</b>: un avatar circular con la inicial de tu nombre acompañado,
        en pantallas anchas, de tu nombre y tu email. Haz clic para desplegarlo.</p>
        <ul>
          <li>La cabecera del menú repite tu <b>identidad</b> (nombre y email),
            útil para confirmar con qué cuenta has entrado.</li>
          <li><b>Perfil</b> — te lleva a los ajustes (<i>Settings</i>), donde
            puedes revisar tu información y las preferencias del tenant.</li>
          <li><b>Cerrar sesión</b> — invalida tu sesión en el servidor, limpia el
            token y la organización activa del navegador y te devuelve a la
            pantalla de login. La siguiente persona que use el equipo no verá
            nada tuyo.</li>
        </ul>
        <p><b>Buena práctica</b>: en equipos compartidos, cierra siempre la
        sesión al terminar; no basta con cerrar la pestaña.</p>`,
    },
    {
      title: "Tu organización (tenant) activa",
      goto: "/admin/dashboard",
      settleMs: 1200,
      // Abre el selector de tenant del top bar para mostrar el cambio de
      // organización (vista distinta del dashboard del paso anterior).
      action: async (page) => {
        await page
          .getByRole("button", { name: /Demo Manuales/ })
          .first()
          .click()
          .catch(() => {});
        await page.waitForTimeout(500);
      },
      body: `<p>Todo lo que ves en el panel pertenece a tu <b>tenant</b> activo.
        La plataforma es <b>multi-tenant</b>: cada organización tiene sus propios
        proyectos, agentes, equipos, bases de conocimiento, memorias y
        configuración, completamente aislados de los demás. El aislamiento no es
        cosmético: se aplica en la base de datos con <b>Row-Level Security</b>,
        de modo que una consulta de un tenant no puede tocar filas de otro ni por
        error de programación.</p>
        <p>Si tu usuario pertenece a <b>varias organizaciones</b>:</p>
        <ul>
          <li>Tras el login verás un <b>selector de tenant</b> para elegir en
            cuál trabajar.</li>
          <li>En cualquier momento puedes cambiar de organización desde el
            selector de la barra superior (visible en la captura): al elegir
            otro tenant, todo el panel — proyectos, tableros, agentes — pasa a
            mostrar los datos de esa organización.</li>
          <li>El administrador de sistema puede, además, <b>crear un tenant
            nuevo</b> desde este mismo selector.</li>
        </ul>
        <p><b>Aviso</b>: antes de crear proyectos o lanzar trabajo, comprueba en
        la barra superior que estás en la organización correcta — es el error de
        contexto más común.</p>`,
    },
    {
      title: "La documentación integrada",
      goto: "/admin/docs",
      settleMs: 1500,
      body: `<p>El grupo <b>Ayuda</b> de la navegación lateral da acceso a la
        sección <b>Documentación</b>: el explorador de la documentación generada
        para cada proyecto de tu tenant. Los equipos de agentes incluyen un
        <i>Technical Writer</i> que mantiene la documentación al cierre de cada
        plan, y aquí es donde la consultas sin salir del panel.</p>
        <ul>
          <li>Puedes <b>filtrar por categoría o tipo</b> de documento (visión
            general, guías, referencia, decisiones de arquitectura, runbooks,
            changelog…).</li>
          <li>La <b>búsqueda en texto</b> localiza documentos por su contenido,
            no solo por el título.</li>
          <li>Puedes <b>marcar documentos</b> como favoritos para volver a ellos
            rápidamente.</li>
        </ul>
        <p><b>Caso de uso</b>: tras completarse un plan, entra aquí para leer el
        changelog y la referencia actualizada antes de validar el resultado. Con
        esto termina el recorrido introductorio; los siguientes manuales
        profundizan en <b>Proyectos</b> (manual 01) y en <b>Planes, Kanban y
        Aprobaciones</b> (manual 02).</p>`,
    },
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  test.setTimeout(240_000);
  // NO pre-login: este manual documenta el flujo de login completo.
  await generateManual(page, manual);
});
