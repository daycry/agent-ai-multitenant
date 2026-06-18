import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef } from "../lib/manual";

// GENERADO desde el workflow de redacción. Editable a mano; reejecutable.
const manual: ManualDef = {
  order: "06",
  slug: "06-marketplace-guardrails-calidad",
  title: "Marketplace, Guardrails y Calidad",
  audience: "Tenant Admin (administrador de tenant) y miembros del tenant con permisos de lectura",
  intro:
    "<p>Este manual cubre las tres superficies del panel de administración relacionadas con la extensión y el control de calidad de la plataforma agéntica: el <b>Marketplace</b> (catálogo de skills y tools, instalaciones, listings privados del tenant y recursos compartidos entre tenants), el <b>dashboard de Guardrails</b> (observabilidad de las políticas de seguridad que se disparan sobre el trabajo del tenant) y el <b>dashboard de Calidad (Evals)</b> (cómo puntúan los agentes a lo largo del tiempo).</p><p>Todas estas pantallas son <b>multi-tenant</b>: cada tenant ve únicamente sus propios datos (catálogo global más sus listings privados, sus eventos de guardrails y sus runs de evaluación). Las acciones de escritura (publicar, instalar, revocar, compartir) requieren el rol <b>tenant_admin</b>; el resto de miembros pueden consultar en modo lectura.</p>",
  steps: [
    {
      title: "Marketplace: pestaña Catálogo",
      goto: "/admin/marketplace",
      body: "<p>El Marketplace es el área donde un Tenant Admin gestiona el catálogo de extensiones de su organización. La cabecera muestra el título <b>Marketplace</b> con dos botones de acción: <b>Privadas</b> (lleva al marketplace privado) y <b>Publicar</b> (atajo para publicar un listing, visible solo para tenant_admin).</p><p>Bajo la cabecera hay tres pestañas: <b>Catálogo</b>, <b>Instaladas</b> y <b>Compartir</b>. La pestaña <b>Catálogo</b> está activa por defecto y muestra todos los listings disponibles: los del catálogo global (etiqueta <code>global</code>) y los listings privados propios del tenant (etiqueta <code>privado</code>).</p><p>Cada listing aparece como una tarjeta con su nombre, su tipo (<code>skill</code>, <code>tool</code> o <code>mcp_server</code>), su versión y su nivel de confianza (<code>verified</code>, <code>community</code> o <code>experimental</code>). Si eres tenant_admin verás arriba un aviso destacado que invita a publicar tus propias skills o tools internas, con un botón <b>Publicar en el marketplace</b>.</p><p>El listing especial de Playwright muestra además un botón <b>Configurar</b> para abrir su configuración guiada. Si el catálogo está vacío, se muestra un mensaje invitando a publicar la primera skill o tool interna.</p>",
      fullPage: true,
    },
    {
      title: "Marketplace: pestaña Instaladas",
      goto: "/admin/marketplace",
      body: "<p>La pestaña <b>Instaladas</b> lista todo lo que el tenant tiene instalado. Cada instalación se muestra como una tarjeta con el identificador del listing, su versión y una etiqueta de estado: <b>Habilitada</b>, <b>Deshabilitada</b> o <b>Revocada</b>.</p><p>Para cada instalación dispones del botón <b>Permisos</b>, que abre la pantalla de consentimiento granular donde se revisan los permisos concedidos y denegados de esa instalación.</p><p>Si eres tenant_admin verás además dos acciones: <b>Revocar</b> (retira la instalación dejándola en estado revocado; se deshabilita si ya está revocada) y <b>Desinstalar</b> (elimina la instalación por completo). Ambas operaciones se confirman en el servidor y la lista se refresca automáticamente. Si el tenant no tiene nada instalado, se muestra el mensaje correspondiente.</p>",
      fullPage: true,
    },
    {
      title: "Marketplace: pestaña Compartir (cross-tenant)",
      goto: "/admin/marketplace",
      body: "<p>La pestaña <b>Compartir</b> gestiona los grants cross-tenant: permite que tu tenant comparta uno de sus listings privados con otro tenant. Compartir es siempre <b>opt-in y explícito</b>: el tenant destino solo ve e instala el listing mediante el grant, y cada acción queda auditada por el System Admin. Revocar retira la visibilidad de inmediato.</p><p>Si eres tenant_admin, en la tarjeta superior puedes crear un grant: selecciona un <b>Listing privado</b> del desplegable (solo aparecen tus listings privados, ya que los globales son visibles para todos), introduce el <b>UUID del tenant destino</b> y pulsa <b>Compartir</b>. Si no tienes listings privados, un aviso te enlaza al Marketplace privado para publicar uno.</p><p>Debajo, la sección <b>Grants activos creados por tu tenant</b> lista los shares que has otorgado, indicando el listing y el tenant destino. Cada grant tiene un botón <b>Revocar</b> para retirarlo. Por defecto no se comparte nada.</p>",
      fullPage: true,
    },
    {
      title: "Marketplace privado del tenant",
      goto: "/admin/marketplace/private",
      body: "<p>Esta pantalla permite publicar las skills y tools internas del tenant como <b>listings privados</b>: solo tu tenant los ve, aislados por RLS. La cabecera incluye un botón <b>Volver al catálogo</b>.</p><p>Si eres tenant_admin verás el formulario <b>Publicar listing privado</b>. Primero eliges el <b>Tipo</b>: <code>skill</code> (SKILL.md), <code>tool</code> (manifest YAML) o <code>mcp_server</code> (manifest YAML). Según el tipo, un bloque de ayuda inline muestra el resumen del formato y los campos obligatorios y opcionales esperados.</p><p>A continuación rellenas el <b>Autor</b> (opcional) y pegas el <b>Manifest</b> en el área de texto. El botón <b>Usar ejemplo</b> inserta un manifest válido de ese tipo para que partas de una base que funciona. El nombre y la versión se leen del propio manifest; una versión duplicada se rechaza.</p><p>Al pulsar <b>Publicar</b>, el backend valida el manifest: si falla, se muestra un recuadro de error con el mensaje exacto del validador y no se crea ningún listing; si tiene éxito, aparece un mensaje de confirmación. Más abajo, la sección <b>Catálogo privado del tenant</b> lista tus listings publicados, cada uno con su tipo, versión, etiqueta <code>privado</code> y un botón <b>Despublicar</b> (solo tenant_admin).</p>",
      fullPage: true,
    },
    {
      title: "Dashboard de Guardrails",
      goto: "/admin/guardrails",
      body: "<p>El dashboard de <b>Guardrails</b> ofrece observabilidad de las políticas de seguridad que se disparan sobre el trabajo de tu tenant. El detalle de cada evento está <b>enmascarado</b>: el secreto o PII que disparó el guardrail nunca se almacena, por lo que esta pantalla nunca muestra el valor crudo. Es una vista solo para <b>tenant_admin</b>; sin ese rol verás un aviso indicando que lo necesitas.</p><p>Arriba hay un selector de <b>Ventana</b> temporal (7, 30 o 90 días). Debajo, una tarjeta con el total de <b>Eventos</b> de la ventana y una <b>Tendencia diaria</b> representada como un sparkline.</p><p>Después se muestran dos desgloses: <b>Por tipo</b> de guardrail y <b>Por severidad</b> (de <code>critical</code> a <code>info</code>), ambos como barras horizontales con su recuento. Por último, la tabla <b>Eventos recientes</b> lista cada evento con su tipo, hook (pre_llm, post_llm, pre_tool, post_tool), severidad, acción aplicada (bloquear, enmascarar, avisar, reintentar, escalar, transformar), el detalle enmascarado y la marca temporal.</p>",
      fullPage: true,
    },
    {
      title: "Dashboard de Calidad (Evals)",
      goto: "/admin/eval-quality",
      body: "<p>El dashboard de <b>Calidad (Evals)</b> muestra cómo puntúan los agentes de tu tenant a lo largo del tiempo, agregado desde las ejecuciones de evaluación (EvalRun / EvalResult). Es una vista solo para <b>tenant_admin</b>; sin ese rol verás un aviso indicándolo. Todo es tenant-scoped: solo ves tus propios runs.</p><p>Arriba hay un selector de <b>Ventana</b> (30, 90 o 365 días). A continuación, tarjetas de cabecera con el <b>Pass rate</b> global y el número de <b>Runs</b>, más un sparkline de <b>Tendencia de pass rate diaria</b>.</p><p>Después se presentan cuatro desgloses en barras de porcentaje: <b>Por agente</b>, <b>Por release de prompt</b>, <b>Por dataset (benchmark)</b> y <b>Por criterio</b> del juez. Cada barra muestra el pass rate y el detalle de ítems superados sobre el total.</p><p>Al final, la tabla <b>Historial de runs</b> lista las últimas ejecuciones con su dataset, agente, release, estado (completado, en ejecución, pendiente, fallido o cancelado), pass rate, coste en USD y fecha de finalización. Una nota recuerda que los costes están en USD canónico, ya que la conversión a moneda del tenant aún no está disponible.</p>",
      fullPage: true,
    },
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  test.setTimeout(300_000);
  await login(page);
  await generateManual(page, manual);
});
