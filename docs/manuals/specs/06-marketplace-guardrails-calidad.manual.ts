import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef } from "../lib/manual";

// GENERADO desde el workflow de redacción. Editable a mano; reejecutable.
//
// NOTA sobre capturas: la pantalla del Marketplace (/admin/marketplace) usa
// PESTAÑAS (Catálogo / Instaladas / Compartir) renderizadas como un mismo
// `goto`. Si un paso no CLICA su pestaña, captura la pestaña por defecto
// (Catálogo) y dos pasos salen idénticos. Por eso los pasos de pestaña traen
// una `action` que pulsa su pestaña ANTES del pantallazo. El paso del
// consentimiento granular NAVEGA mediante clic en su botón real («Permisos»
// de una instalación), porque su ruta lleva un [id] que solo existe en el
// entorno vivo. Todas las acciones son tolerantes (.catch(()=>{})) para no
// romper la generación si un selector no existe en este entorno.
//
// task_mkt2_13 (ADR 0142): el paso «Configuración guiada de la tool
// Playwright» SE RETIRÓ con la pantalla que documentaba. La configuración ya
// no se pide al instalar —los valores, como la base_url del sitio bajo
// prueba, son del PROYECTO y al instalar los proyectos ni existen— sino al
// desplegar la capacidad en cada proyecto. Ese formulario se documenta en el
// manual de proyectos, donde vive.
const manual: ManualDef = {
  order: "06",
  slug: "06-marketplace-guardrails-calidad",
  title: "Marketplace, Guardrails y Calidad",
  audience: "Tenant Admin (administrador de tenant) y miembros del tenant con permisos de lectura",
  intro:
    "<p>Este manual cubre las tres superficies del panel de administración relacionadas con la extensión y el control de calidad de la plataforma agéntica: el <b>Marketplace</b> (catálogo de skills, tools y servidores MCP; instalaciones con sus puertas de seguridad; consentimiento granular de permisos; listings privados del tenant y recursos compartidos entre tenants), el <b>dashboard de Guardrails</b> (observabilidad de las políticas de seguridad que se disparan sobre el trabajo del tenant) y el <b>dashboard de Calidad (Evals)</b> (cómo puntúan los agentes a lo largo del tiempo).</p><p>Todas estas pantallas son <b>multi-tenant</b>: cada tenant ve únicamente sus propios datos (catálogo global más sus listings privados, sus instalaciones, sus eventos de guardrails y sus runs de evaluación), aislados por RLS en base de datos. Las acciones de escritura (publicar, instalar, consentir permisos, revocar, compartir) requieren el rol <b>tenant_admin</b>; el resto de miembros pueden consultar en modo lectura. El backend es siempre la fuente de verdad de permisos: aunque un botón fuera visible, la operación solo se aplica si el rol lo permite.</p><p>Un concepto atraviesa todo el capítulo del Marketplace: el <b>nivel de confianza</b> (<code>verified</code>, <code>community</code>, <code>experimental</code>) de cada listing. El nivel no decide si algo se puede instalar — todo el catálogo es instalable — sino <b>cuántas puertas de seguridad</b> impone la instalación: firma criptográfica, análisis estático, prueba en sandbox y consentimiento explícito por permiso. Este manual explica la semántica real de cada puerta.</p>",
  steps: [
    {
      title: "Marketplace: pestaña Catálogo y niveles de confianza",
      goto: "/admin/marketplace",
      // El Marketplace abre en la pestaña Catálogo por defecto. La pulsamos de
      // forma explícita por si un paso anterior dejó otra activa.
      action: async (page) => {
        await page
          .getByTestId("marketplace-tab-catalog")
          .click()
          .catch(async () => {
            await page
              .getByRole("tab", { name: "Catálogo" })
              .click()
              .catch(() => {});
          });
        await page.waitForTimeout(500);
      },
      body: "<p>El Marketplace es el área donde un Tenant Admin gestiona el catálogo de extensiones de su organización. La cabecera muestra el título <b>Marketplace</b> con dos botones de acción: <b>Privadas</b> (lleva al marketplace privado del tenant) y <b>Publicar</b> (atajo al formulario de publicación, visible solo para tenant_admin). Bajo la cabecera hay tres pestañas: <b>Catálogo</b>, <b>Instaladas</b> y <b>Compartir</b>; la de <b>Catálogo</b> está activa por defecto.</p><p>El catálogo muestra todos los listings visibles para tu tenant: los del <b>catálogo global</b> (etiqueta <code>global</code>, visibles para todos los tenants) y los <b>listings privados propios</b> (etiqueta <code>privado</code>, aislados por RLS: ningún otro tenant los ve jamás). Cada listing aparece como una tarjeta con su nombre, su tipo (<code>skill</code>, <code>tool</code> o <code>mcp_server</code>), su versión, su nivel de confianza y, si la tiene, una descripción.</p><p>El <b>nivel de confianza</b> es la pieza central de la política de seguridad del Marketplace. Gobierna las <i>guardas</i> que se aplican al instalar, no la disponibilidad:</p><ul><li><b>verified</b> (verde): revisado y <b>firmado criptográficamente</b> por el equipo de plataforma (firma Ed25519 sobre el manifest exacto). Instala con fricción mínima: sin consentimiento por permiso y sin sandbox, aunque el análisis estático se ejecuta igualmente como defensa en profundidad (tolera hallazgos hasta severidad media).</li><li><b>community</b> (azul): publicado por terceros, sin firma de plataforma. Requiere <b>siempre</b> consentimiento explícito permiso a permiso, análisis estático (solo tolera hallazgos de severidad baja) y una prueba de humo en sandbox.</li><li><b>experimental</b> (ámbar): sin ninguna vejación previa. Máximas guardas: consentimiento por permiso, sandbox y análisis estático en el que <b>cualquier hallazgo bloquea</b> la instalación.</li></ul><p>Si eres tenant_admin verás arriba un aviso destacado («¿Tienes una skill o tool interna?») que invita a publicar tus propias skills o tools internas, con el botón <b>Publicar en el marketplace</b>. El catálogo <b>no configura nada</b>: instalar una capacidad solo la añade al fondo de tu tenant (y consiente sus permisos). Los valores concretos —por ejemplo, contra qué URL corre Playwright sus pruebas— se piden más tarde, al <b>desplegar</b> la capacidad en un proyecto, porque son distintos en cada proyecto y al instalar los proyectos aún no existen. Si el catálogo está vacío, se muestra un mensaje invitando a publicar la primera skill o tool interna.</p>",
      fullPage: true,
    },
    {
      title: "Marketplace: pestaña Instaladas y puertas de la instalación",
      goto: "/admin/marketplace",
      // Clic en la pestaña "Instaladas" para capturar ese panel y no el de
      // Catálogo (que es el activo por defecto).
      action: async (page) => {
        await page
          .getByTestId("marketplace-tab-installed")
          .click()
          .catch(async () => {
            await page
              .getByRole("tab", { name: "Instaladas" })
              .click()
              .catch(() => {});
          });
        await page.waitForTimeout(500);
      },
      body: "<p>La pestaña <b>Instaladas</b> lista todo lo que el tenant tiene instalado. Cada instalación se muestra como una tarjeta con el identificador del listing, su versión y una etiqueta de estado: <b>Habilitada</b> (operativa), <b>Deshabilitada</b> (instalada pero inactiva — típicamente pendiente de consentimiento) o <b>Revocada</b> (retirada por un administrador).</p><p>Es importante entender qué ocurre <b>al instalar</b>. La instalación no es un simple alta: pasa por una cadena de puertas de seguridad que fallan en cerrado, y todo queda registrado en una pista de auditoría inmutable:</p><ul><li><b>Análisis estático</b>: la instalación fresca ejecuta un escaneo del código del artefacto con <code>bandit</code> (Python) y <code>semgrep</code>. Si algún hallazgo supera la severidad tolerada por el nivel de confianza del listing (media para <code>verified</code>, baja para <code>community</code>, ninguna para <code>experimental</code>), el servidor responde <b>422 «install blocked by static analysis»</b>, no se crea ninguna instalación y el intento queda auditado con los hallazgos bloqueantes.</li><li><b>Consentimiento</b>: un listing <code>community</code> o <code>experimental</code> se instala <b>siempre en estado Deshabilitada y sin ningún permiso concedido</b>. Solo pasa a Habilitada cuando el administrador concede uno a uno todos los permisos solicitados en la pantalla de consentimiento (siguiente paso). Un listing <code>verified</code> instala directamente Habilitada.</li></ul><p>Para cada instalación dispones del botón <b>Permisos</b>, que abre la pantalla de consentimiento granular donde se revisan y deciden los permisos de esa instalación. Si eres tenant_admin verás además dos acciones: <b>Revocar</b> (retira la instalación dejándola en estado revocado; el botón se desactiva si ya está revocada) y <b>Desinstalar</b> (elimina la instalación por completo). Ambas operaciones se confirman en el servidor, quedan auditadas y la lista se refresca automáticamente. Si el tenant no tiene nada instalado, se muestra el mensaje correspondiente.</p>",
      fullPage: true,
    },
    {
      title: "Consentimiento granular de permisos de una instalación",
      goto: "/admin/marketplace",
      // La ruta real es /admin/marketplace/installations/{id}/permissions con
      // el id vivo de una instalación: llegamos como el usuario, pulsando el
      // botón "Permisos" de la primera tarjeta de la pestaña Instaladas.
      action: async (page) => {
        await page
          .getByTestId("marketplace-tab-installed")
          .click()
          .catch(() => {});
        await page.waitForTimeout(500);
        await page
          .locator('[data-testid^="installed-consent-"]')
          .first()
          .click()
          .catch(() => {});
        await page.waitForTimeout(900);
      },
      body: "<p>La pantalla <b>Consentimiento de permisos</b> es donde un tenant_admin aprueba o deniega, uno a uno, cada permiso que una tool o skill solicita. La cabecera muestra el estado actual de la instalación (<b>Habilitada</b>, <b>Deshabilitada (pendiente de consentimiento)</b> o <b>Revocada</b>). La regla es estricta: una instalación <code>community</code> o <code>experimental</code> <b>no se habilita hasta que TODOS los permisos solicitados estén concedidos</b>; denegar cualquiera de ellos la mantiene deshabilitada. Cada decisión queda auditada en el servidor (acciones <code>consent</code> / <code>consent_denied</code>). Si el listing es <code>verified</code>, la pantalla lo indica con una tarjeta: no requiere consentimiento granular (fricción mínima) y los permisos se aplican según la política de confianza.</p><p>Cada permiso solicitado aparece como una tarjeta con su nombre, su estado (<b>Concedido</b>, <b>Denegado</b> o <b>Pendiente</b>), el valor solicitado en fuente monoespaciada y una <b>ayuda inline</b> que explica el riesgo real que estás aprobando:</p><ul><li><b>Dominios permitidos</b> (<code>allowed_domains</code>): la tool solo podrá hacer peticiones HTTP a esos dominios, y siempre a través del proxy de salida de la plataforma — nunca acceso directo a la red.</li><li><b>Rutas permitidas</b> (<code>allowed_paths</code>): las rutas del workspace a las que la tool tendrá acceso.</li><li><b>Política de red</b> (<code>network_policy</code>): <code>none</code> = sin red; <code>restricted</code> = red interna sin salida; <code>open</code> = salida a internet <b>SOLO a través del proxy con allowlist de la plataforma</b> (registries públicos de paquetes y git). Es clave entenderlo: <code>open</code> <b>no es internet crudo</b> — todo el tráfico pasa por el proxy con lista blanca y cada uso queda registrado en el audit log.</li></ul><p>El flujo de decisión es en dos tiempos: pulsa <b>Aprobar</b> o <b>Denegar</b> en cada permiso (la decisión queda <i>en borrador</i>, marcada con un asterisco «*» junto al estado) y, cuando tengas todas las decisiones tomadas, pulsa <b>Guardar decisiones</b> para enviarlas en lote al servidor. El texto al pie te recuerda cuántas decisiones tienes sin guardar. Tras guardar, la pantalla refleja el nuevo estado de la instalación: si todos los permisos quedaron concedidos, pasa a Habilitada.</p><p>Cualquier miembro del tenant puede <b>leer</b> esta pantalla; los botones de decisión solo aparecen para tenant_admin, y el backend impone la misma regla aunque se intentara por API.</p>",
      fullPage: true,
    },
    {
      title: "Marketplace: pestaña Compartir (cross-tenant)",
      goto: "/admin/marketplace",
      // Clic en la pestaña "Compartir" para capturar el formulario de grants
      // cross-tenant en lugar del Catálogo.
      action: async (page) => {
        await page
          .getByTestId("marketplace-tab-shares")
          .click()
          .catch(async () => {
            await page
              .getByRole("tab", { name: "Compartir" })
              .click()
              .catch(() => {});
          });
        await page.waitForTimeout(500);
      },
      body: "<p>La pestaña <b>Compartir</b> gestiona los <b>grants cross-tenant</b>: la única vía por la que un listing privado de tu tenant puede llegar a otro tenant. La frontera multi-tenant nunca se relaja de forma implícita: compartir es siempre <b>opt-in y explícito</b>, el tenant destino solo ve e instala el listing mientras el grant esté vivo, y cada acción (crear o revocar un share) queda <b>auditada por el System Admin</b>. Revocar un grant retira la visibilidad de inmediato.</p><p>Si eres tenant_admin, en la tarjeta superior puedes crear un grant en tres pasos:</p><ul><li>Selecciona un <b>Listing privado</b> del desplegable. Solo aparecen tus listings privados: un listing del catálogo global ya es visible para todos los tenants, así que no hay nada que compartir de él.</li><li>Introduce el <b>UUID del tenant destino</b> (te lo debe facilitar el administrador del otro tenant). Compartir con tu propio tenant se rechaza con un error explícito.</li><li>Pulsa <b>Compartir</b>. Si algo falla (listing inexistente, tenant destino inválido), el mensaje de error del servidor se muestra bajo el formulario.</li></ul><p>Si no tienes listings privados, un aviso te enlaza al Marketplace privado para publicar uno primero. Debajo, la sección <b>Grants activos creados por tu tenant</b> lista los shares que has otorgado, indicando el listing y el tenant destino; cada grant tiene un botón <b>Revocar</b> para retirarlo al instante. Por defecto no se comparte nada.</p><p>Dos garantías de diseño completan el cuadro: un share <b>nombra</b> el listing pero no lo copia (el destino siempre lee la versión vigente del origen), y ni las firmas ni ningún secreto viajan por el wire — el listing solo expone el hecho de estar firmado, nunca la firma.</p>",
      fullPage: true,
    },
    {
      title: "Marketplace privado: publicar un listing (skill)",
      goto: "/admin/marketplace/private",
      // El formulario de publicación arranca con el manifest vacío. Pulsamos
      // "Usar ejemplo" para que la captura muestre un manifest válido relleno
      // (skill por defecto), que es la pantalla útil que documenta el paso.
      action: async (page) => {
        await page
          .getByTestId("private-use-example")
          .click()
          .catch(async () => {
            await page
              .getByRole("button", { name: "Usar ejemplo" })
              .first()
              .click()
              .catch(() => {});
          });
        await page.waitForTimeout(500);
      },
      body: "<p>Esta pantalla permite publicar las skills y tools internas del tenant como <b>listings privados</b>: solo tu tenant los ve, aislados por RLS a nivel de base de datos (otro tenant nunca los verá, ni siquiera por API). La cabecera incluye un botón <b>Volver al catálogo</b>.</p><p>Si eres tenant_admin verás el formulario <b>Publicar listing privado</b>. Primero eliges el <b>Tipo</b>: <code>skill</code> (documento SKILL.md), <code>tool</code> (manifest YAML) o <code>mcp_server</code> (manifest YAML con <code>kind: mcp_server</code>). Según el tipo, un bloque de ayuda inline muestra el resumen del formato y las listas de campos <b>obligatorios</b> y <b>opcionales</b> que el validador espera, además de un enlace a la guía de publicación de la documentación.</p><p>A continuación rellenas el <b>Autor</b> (opcional) y pegas el <b>Manifest</b> en el área de texto. El botón <b>Usar ejemplo</b> inserta un manifest válido del tipo elegido — un ejemplo que el validador acepta tal cual — para que partas de una base que funciona y la edites (es lo que muestra la captura). El nombre y la versión del listing se leen del propio manifest, no se piden por separado; publicar una versión duplicada de un mismo nombre se rechaza.</p><p>Al pulsar <b>Publicar</b>, el backend valida el manifest con los parsers de plataforma: si falla, se muestra un recuadro de error con el <b>mensaje exacto del validador</b> (qué campo falta o qué está mal formado) y no se crea ningún listing; si tiene éxito, aparece el mensaje «Listing publicado». El nivel de confianza (<code>community</code>), la fuente privada y el <code>tenant_id</code> se derivan siempre en el servidor — nunca del cliente.</p><p>Más abajo, la sección <b>Catálogo privado del tenant</b> lista tus listings publicados, cada uno con su tipo, versión, etiqueta <code>privado</code> y un botón <b>Despublicar</b> (solo tenant_admin) que lo retira del catálogo. Si aún no hay ninguno, un botón «Empezar con un ejemplo» te precarga el formulario.</p>",
      fullPage: true,
    },
    {
      title: "Marketplace privado: formatos de manifest para tool y MCP server",
      goto: "/admin/marketplace/private",
      // Cambiamos el tipo a "tool" y precargamos su ejemplo para capturar la
      // ayuda de formato específica de tools (distinta de la de skills).
      action: async (page) => {
        await page
          .getByTestId("private-kind-select")
          .selectOption("tool")
          .catch(() => {});
        await page.waitForTimeout(300);
        await page
          .getByTestId("private-use-example")
          .click()
          .catch(() => {});
        await page.waitForTimeout(500);
      },
      body: "<p>El mismo formulario de publicación se adapta al tipo de manifest elegido. La captura muestra el modo <b>Tool</b>, con su bloque de ayuda y su ejemplo insertado. Conviene conocer los tres formatos:</p><ul><li><b>Skill (SKILL.md)</b>: Markdown con un frontmatter YAML entre líneas <code>---</code> seguido del cuerpo en prosa. Obligatorios: <code>name</code>, <code>description</code>, <code>version</code> (semver, p. ej. 1.0.0). Opcionales: <code>dependencies</code> (lista), <code>permissions</code> (allowed_domains / allowed_paths / network_policy) y <code>examples</code> (lista de título + prompt).</li><li><b>Tool (YAML)</b>: documento YAML plano, sin cuerpo Markdown. Obligatorios: <code>name</code>, <code>version</code> (semver), <code>description</code>, <code>entrypoint</code> (módulo:función) e <code>implementation.runtime</code>. Opcionales: <code>kind</code> (por defecto tool), <code>implementation.module</code> / <code>implementation.reference</code>, <code>dependencies</code>, <code>input_schema</code>, <code>output_schema</code> y <code>permissions</code>.</li><li><b>MCP server (YAML)</b>: el mismo YAML que una tool pero con <code>kind: mcp_server</code> obligatorio y coincidente con el tipo elegido en el desplegable.</li></ul><p>Los permisos que declares en el manifest (<code>allowed_domains</code>, <code>allowed_paths</code>, <code>network_policy</code> con vocabulario <code>none | restricted | open</code>) son exactamente los que después aparecerán en la pantalla de consentimiento cuando alguien instale el listing: declara solo lo que la tool realmente necesita, porque cada permiso extra es una decisión más que el administrador instalador tendrá que aprobar.</p><p>Si el manifest no valida, el recuadro rojo «No se pudo publicar» muestra el mensaje del parser indicando el campo problemático; corrige y vuelve a publicar — no se crea ningún listing hasta que valide.</p>",
      fullPage: true,
    },
    {
      title: "Dashboard de Guardrails",
      goto: "/admin/guardrails",
      body: "<p>El dashboard de <b>Guardrails</b> ofrece observabilidad de las políticas de seguridad declarativas que se disparan sobre el trabajo de tu tenant: detección de secretos, PII, contenido bloqueado, etc. Los guardrails se evalúan en cuatro puntos del ciclo de cada agente — <code>pre_llm</code>, <code>post_llm</code>, <code>pre_tool</code> y <code>post_tool</code> — y cada disparo queda registrado como un evento tenant-scoped. Es una vista solo para <b>tenant_admin</b>; sin ese rol verás un aviso indicando que lo necesitas.</p><p>Una garantía de privacidad preside la pantalla: el detalle de cada evento está <b>enmascarado en origen</b>. El secreto o dato personal que disparó el guardrail <b>nunca se almacena</b> — el registrador lo enmascara antes de persistir —, por lo que este dashboard jamás puede mostrar el valor crudo, ni siquiera a un administrador.</p><p>La estructura de la pantalla, de arriba abajo:</p><ul><li><b>Ventana</b>: selector temporal de 7, 30 o 90 días que reencuadra todos los números.</li><li><b>Eventos</b>: tarjeta con el total de eventos de la ventana, junto a la <b>Tendencia diaria</b> representada como un sparkline (línea plana si no hubo eventos).</li><li><b>Por tipo</b> y <b>Por severidad</b>: dos desgloses en barras horizontales con su recuento. La severidad se ordena de <code>critical</code> a <code>info</code>, cada una con su color.</li><li><b>Eventos recientes</b>: tabla con el tipo de guardrail, el hook donde saltó, la severidad, la <b>acción aplicada</b> — bloquear, enmascarar, avisar, reintentar (con feedback al agente), escalar (a humano) o transformar —, el detalle enmascarado y la marca temporal.</li></ul><p>Usos típicos: detectar un pico de eventos tras desplegar un cambio de prompts, identificar qué tipo de guardrail está frenando más trabajo, o verificar que una política recién configurada realmente se está disparando. Si un guardrail crítico se dispara con frecuencia, revisa la configuración del proyecto afectado antes de relajar la política.</p>",
      fullPage: true,
    },
    {
      title: "Dashboard de Calidad (Evals)",
      goto: "/admin/eval-quality",
      body: "<p>El dashboard de <b>Calidad (Evals)</b> muestra cómo puntúan los agentes de tu tenant a lo largo del tiempo, agregado desde las ejecuciones de evaluación (runs de evals con juez LLM sobre datasets golden). Es una vista solo para <b>tenant_admin</b>; sin ese rol verás un aviso indicándolo. Todo es tenant-scoped: solo ves tus propios runs y resultados — la comparativa entre tenants es una superficie aparte reservada al System Admin.</p><p>Arriba hay un selector de <b>Ventana</b> (30, 90 o 365 días). A continuación, tarjetas de cabecera con el <b>Pass rate</b> global de la ventana (porcentaje de ítems de evaluación superados) y el número de <b>Runs</b>, más un sparkline con la <b>Tendencia de pass rate diaria</b>.</p><p>Después se presentan cuatro desgloses en barras de porcentaje, cada una con su pass rate y el detalle de ítems superados sobre el total (p. ej. <i>18/20</i>):</p><ul><li><b>Por agente</b>: qué agente rinde mejor o peor; útil para detectar un agente cuyo rol o prompt necesita revisión.</li><li><b>Por release de prompt</b>: compara versiones de prompt entre sí — la vista clave para validar que una nueva release no degrada la calidad antes de generalizarla.</li><li><b>Por dataset (benchmark)</b>: el rendimiento sobre cada banco de pruebas golden del tenant.</li><li><b>Por criterio</b>: el pass rate de cada criterio individual del juez (corrección, formato, seguridad…), que localiza <i>en qué</i> falla un agente y no solo <i>cuánto</i>.</li></ul><p>Al final, la tabla <b>Historial de runs</b> lista las últimas ejecuciones con su dataset, agente, release de prompt, estado (completado, en ejecución, pendiente, fallido o cancelado), pass rate con su fracción, coste en USD y fecha de finalización. Una nota al pie recuerda que los costes se muestran en <b>USD canónico</b>: la conversión a la moneda del tenant depende del sistema de tipos de cambio, aún no disponible.</p>",
      fullPage: true,
    },
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  test.setTimeout(300_000);
  await login(page);
  await generateManual(page, manual);
});
