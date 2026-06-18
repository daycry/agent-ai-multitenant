import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef } from "../lib/manual";

// GENERADO desde el workflow de redacción. Editable a mano; reejecutable.
const manual: ManualDef = {
  order: "08",
  slug: "08-ajustes-y-sso",
  title: "Ajustes del tenant y SSO empresarial",
  audience: "Administrador de tenant (tenant_admin) y administrador del sistema (system_admin)",
  intro:
    "<p>Este manual cubre la sección <b>Ajustes</b> del panel de administración: el punto central donde se configura el comportamiento del tenant. Desde aquí se accede a las categorías de configuración (memorias, costes, etc.), a la integración de inicio de sesión único empresarial (<b>SSO</b>) tanto por OIDC como por SAML 2.0, y a la <b>tarifa horaria</b> que alimenta el cálculo de coste humano de los planes.</p><p>La mayoría de las acciones de escritura (crear, editar, activar o borrar configuraciones de SSO, guardar la tarifa) requieren el rol <b>tenant_admin</b>; cualquier miembro del tenant puede consultar los valores. La edición de la URL base pública de la plataforma está reservada al <b>system_admin</b>. El SSO siempre se <b>añade</b> al login local con email y contraseña: activarlo no lo sustituye ni lo desactiva.</p>",
  steps: [
    {
      title: "Índice de Ajustes",
      goto: "/admin/settings",
      body: "<p>Esta es la pantalla de entrada a la configuración del tenant. La plataforma carga dinámicamente un registro de categorías y muestra una <b>tarjeta por categoría</b> en una rejilla, cada una con su icono, su nombre en español, una descripción y un indicador (el número de ajustes que contiene, o la etiqueta <code>página dedicada</code> si la categoría abre una pantalla propia).</p><ul><li>Categorías típicas: <b>Memorias</b> (gestión de la memoria de los agentes) y <b>Costes</b> (que enlaza con la tarifa horaria dedicada).</li><li>Al pulsar una tarjeta navegas a la pantalla de esa categoría: si tiene página externa te lleva a ella (por ejemplo la tarifa horaria), y si no, a una pantalla autogenerada en <code>/admin/settings/<categoría></code>.</li></ul><p>Si el registro no se puede cargar, la pantalla muestra un bloque de error en lugar de la rejilla. Es el punto de partida para todas las opciones de este manual.</p>",
      fullPage: true,
    },
    {
      title: "SSO empresarial con OIDC",
      goto: "/admin/settings/sso",
      body: "<p>Aquí un <b>tenant_admin</b> conecta el tenant con un proveedor de identidad mediante <b>OpenID Connect (OIDC)</b>. Como hay como mucho una configuración OIDC por tenant, la pantalla gestiona una sola entrada: crear, editar, activar/desactivar o borrar.</p><p>Arriba verás la tarjeta <b>URL base pública de la aplicación</b>, de la que se deriva la <b>URL de callback/redirect</b> que debes registrar en el IdP; puedes copiarla con el botón <b>Copiar</b>. Solo el <b>system_admin</b> puede editar esa URL base (campo + botón <b>Guardar</b>); el resto la ve en modo lectura. Si la base sigue siendo el valor de arranque, aparece un aviso para que pongas tu dominio público real antes de registrar la callback.</p><p>Si aún no hay configuración verás un estado vacío con el botón <b>Configurar OIDC</b>. Una vez creada, una tarjeta muestra el nombre, los distintivos de estado (<b>activo/inactivo</b>) y de secreto (en Vault o cifrado en reposo), el <b>Issuer</b>, el <b>Client ID</b> y los <b>scopes</b>, junto a los botones <b>Activar/Desactivar</b>, <b>editar</b> (lápiz) y <b>eliminar</b> (papelera, con confirmación). También hay un enlace para configurar <b>SAML</b> si tu IdP usa ese protocolo.</p>",
      fullPage: true,
    },
    {
      title: "Configurar o editar OIDC (diálogo)",
      goto: "/admin/settings/sso",
      // Abre el diálogo de configuración OIDC (botón "Configurar OIDC").
      action: async (page) => {
        await page
          .getByTestId("sso-create-button")
          .click()
          .catch(async () => {
            await page
              .getByRole("button", { name: "Configurar OIDC" })
              .click()
              .catch(() => {});
          });
        await page.waitForTimeout(600);
      },
      body: "<p>Al pulsar <b>Configurar OIDC</b> (o el lápiz para editar) se abre un diálogo con el formulario. En la parte superior puedes elegir una <b>Plantilla de proveedor</b> (Azure AD/Entra, Google, Okta, Auth0, GitHub, GitLab, Apple, Facebook); al seleccionarla se pre-rellenan automáticamente el issuer, los scopes y el mapeo de claims con valores verificados, que luego puedes ajustar a mano.</p><ul><li>Si la plantilla lo requiere, aparecen <b>parámetros específicos</b> (por ejemplo el <code>tenant</code> de Azure o el <code>domain</code> de Okta) que completan el issuer.</li><li><b>Nombre visible</b> (opcional), <b>Issuer</b> (sobre el que se consulta <code>/.well-known/openid-configuration</code>), <b>Client ID</b> y <b>Client secret</b>. El secreto se cifra en reposo y nunca se devuelve en claro; al editar, deja el campo vacío para conservar el actual.</li><li><b>Scopes</b> separados por espacios (por defecto <code>openid email profile</code>) y una casilla para <b>activar</b> el proveedor en el login.</li></ul><p>Al crear, el secreto es obligatorio; al editar es opcional. Pulsa <b>Crear</b> o <b>Guardar cambios</b> para confirmar, o <b>Cancelar</b> para descartar. Los errores devueltos por el backend se muestran dentro del diálogo.</p>",
      fullPage: true,
    },
    {
      title: "SSO empresarial con SAML 2.0",
      goto: "/admin/settings/sso/saml",
      body: "<p>Pantalla equivalente a la de OIDC pero para el protocolo <b>SAML 2.0</b>. También admite una sola configuración por tenant y se añade junto al login local y al SSO OIDC sin reemplazarlos. Un <b>tenant_admin</b> puede crear, editar, activar/desactivar o borrar la configuración.</p><p>La tarjeta <b>Metadatos del SP</b> muestra los valores globales que debes registrar en tu proveedor de identidad: el <b>SP Entity ID</b> y la <b>URL de ACS</b> (Assertion Consumer Service), cada uno con su botón <b>Copiar</b>. Si la URL de ACS sigue usando la base por defecto, aparece un aviso para que configures la URL pública real antes de registrar el ACS en el IdP.</p><p>Si no hay configuración, el estado vacío ofrece el botón <b>Configurar SAML</b>. Una vez creada, la tarjeta resume el estado (activo/inactivo), si hay clave del SP, si el AuthnRequest va firmado, y muestra el <b>IdP Entity ID</b>, la <b>SSO URL</b> y el <b>formato de NameID</b>, con los botones <b>Activar/Desactivar</b>, editar y eliminar. Hay además un enlace para configurar OIDC en su lugar.</p>",
      fullPage: true,
    },
    {
      title: "Configurar o editar SAML (diálogo)",
      goto: "/admin/settings/sso/saml",
      // Abre el diálogo de configuración SAML (botón "Configurar SAML").
      action: async (page) => {
        await page
          .getByTestId("saml-create-button")
          .click()
          .catch(async () => {
            await page
              .getByRole("button", { name: "Configurar SAML" })
              .click()
              .catch(() => {});
          });
        await page.waitForTimeout(600);
      },
      body: "<p>El diálogo de SAML empieza por el bloque <b>Metadatos del IdP (XML)</b>: puedes pegar el <code>EntityDescriptor</code> del proveedor o pulsar <b>Subir XML</b> para cargar el archivo, y luego <b>Extraer datos</b> para que el sistema rellene automáticamente el Entity ID, la URL de SSO, el certificado y el formato de NameID.</p><ul><li>Campos editables: <b>Nombre visible</b> (opcional), <b>IdP Entity ID</b>, <b>URL de SSO del IdP</b> y <b>Certificado de firma del IdP (X.509)</b>, con el que se verifican las aserciones.</li><li><b>Formato de NameID</b> mediante un selector (emailAddress recomendado, persistent, transient o unspecified) y mapeo de atributos opcionales de <b>email</b> y <b>nombre</b>.</li><li>Bloque del <b>SP</b>: certificado público y clave privada en PEM (solo necesarios si firmas el AuthnRequest o cifras aserciones); la clave se cifra en reposo y nunca se devuelve. Al editar, déjala vacía para conservar la actual.</li><li>Casillas de seguridad: firmar el AuthnRequest saliente, exigir aserciones firmadas (recomendado), exigir aserciones cifradas y exigir NameID cifrado, más la casilla para <b>activar</b> el proveedor.</li></ul><p>Pulsa <b>Crear</b> o <b>Guardar cambios</b> para confirmar (Entity ID, URL de SSO y certificado del IdP son obligatorios) o <b>Cancelar</b> para salir.</p>",
      fullPage: true,
    },
    {
      title: "Tarifa horaria del tenant",
      goto: "/admin/settings/hourly-rate",
      body: "<p>Esta pantalla define la <b>tarifa por hora</b> que el cálculo de coste humano de los planes usa por defecto. Es la categoría de <b>Costes</b> con página dedicada a la que se llega desde el índice de Ajustes.</p><ul><li>Campo <b>Tarifa por hora</b>: un número (paso 0,01, entre 0 y 10000). Si lo dejas vacío, la plataforma aplica su valor por defecto de <b>50 EUR/h</b>.</li><li>Campo <b>Moneda</b>: código de tres letras (por defecto <code>EUR</code>), que se convierte automáticamente a mayúsculas.</li></ul><p>El botón <b>Guardar</b> solo se habilita cuando hay cambios respecto al valor almacenado. Al guardar correctamente verás el mensaje <b>Guardado</b>; si hay un error de validación o permisos se muestra el mensaje del backend. Cualquier miembro del tenant puede consultar la tarifa, pero solo un <b>tenant_admin</b> puede persistirla (en caso contrario el servidor responde 403). Al cambiarla, los desgloses de coste de los planes se recalculan.</p>",
      fullPage: true,
    },
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  test.setTimeout(300_000);
  await login(page);
  await generateManual(page, manual);
});
