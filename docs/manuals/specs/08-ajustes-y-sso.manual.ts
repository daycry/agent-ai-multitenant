import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef } from "../lib/manual";

// GENERADO desde el workflow de redacción. Editable a mano; reejecutable.
//
// Los pasos de diálogo (Configurar OIDC / Configurar SAML) solo abren el
// formulario si el tenant aún NO tiene esa configuración creada (el botón de
// crear desaparece cuando existe una); las `action` son tolerantes (.catch)
// para que en ese caso la captura muestre la tarjeta de configuración
// existente sin romper la generación.
const manual: ManualDef = {
  order: "08",
  slug: "08-ajustes-y-sso",
  title: "Ajustes del tenant y SSO empresarial",
  audience: "Administrador de tenant (tenant_admin) y administrador del sistema (system_admin)",
  intro:
    "<p>Este manual cubre la sección <b>Ajustes</b> del panel de administración: el punto central donde se configura el comportamiento del tenant. Desde aquí se accede a las categorías de configuración (memorias, costes…), a la integración de inicio de sesión único empresarial (<b>SSO</b>) tanto por <b>OIDC</b> como por <b>SAML 2.0</b>, a la <b>tarifa horaria</b> que alimenta el cálculo de coste humano de los planes y — para el System Admin — a los <b>valores por defecto de plataforma</b>.</p><p>La mayoría de las acciones de escritura (crear, editar, activar o borrar configuraciones de SSO, guardar la tarifa o los ajustes de memorias) requieren el rol <b>tenant_admin</b>; cualquier miembro del tenant puede consultar los valores. La edición de la URL base pública de la plataforma y de los valores por defecto globales está reservada al <b>system_admin</b>. Dos principios atraviesan todo el capítulo: los <b>secretos</b> (client secret de OIDC, clave privada del SP de SAML) se cifran en reposo o se referencian en Vault y el sistema <b>nunca</b> los devuelve en claro; y el SSO siempre se <b>añade</b> al login local con email y contraseña — activarlo no lo sustituye ni lo desactiva, de modo que nunca puedes quedarte fuera de la plataforma por una configuración de SSO defectuosa.</p>",
  steps: [
    {
      title: "Índice de Ajustes",
      goto: "/admin/settings",
      body: "<p>Esta es la pantalla de entrada a la configuración del tenant. No es una lista estática: la plataforma carga dinámicamente un <b>registro de categorías</b> desde el backend y muestra una <b>tarjeta por categoría</b> en una rejilla, cada una con su icono, su nombre en español, una descripción y un indicador que puede ser de dos tipos:</p><ul><li>El <b>número de ajustes</b> que contiene la categoría (p. ej. «2 ajustes»), cuando sus opciones se editan en una pantalla autogenerada.</li><li>La etiqueta <code>página dedicada</code>, cuando la categoría abre una pantalla propia con su interfaz específica (es el caso de <b>Costes</b>, que enlaza con la tarifa horaria).</li></ul><p>Categorías típicas: <b>Memorias</b> (cómo el sistema detecta memorias similares de los agentes; ver paso siguiente) y <b>Costes</b> (la tarifa horaria del tenant). Al ser un registro dinámico, futuras categorías aparecerán aquí automáticamente sin necesidad de aprenderse rutas nuevas.</p><p>Al pulsar una tarjeta navegas a la pantalla de esa categoría: si tiene página externa te lleva a ella, y si no, a la pantalla autogenerada en <code>/admin/settings/&lt;categoría&gt;</code>. Si el registro no se puede cargar (backend caído o sin permisos), la pantalla muestra un bloque de error en lugar de la rejilla. Es el punto de partida para todas las opciones de este manual.</p>",
      fullPage: true,
    },
    {
      title: "Ajustes de Memorias — detector de similares",
      goto: "/admin/settings/memories",
      body: "<p>La categoría <b>Memorias</b> controla el <b>detector de memorias similares</b>: el mecanismo que, cuando los agentes acumulan memorias parecidas, se las presenta al operador para que las fusione o descarte, evitando que la memoria del tenant se llene de duplicados. La pantalla tiene dos controles cuyos rangos y descripciones vienen del propio registro del backend (nada está cableado en la interfaz):</p><ul><li><b>Umbral de similitud</b>: un deslizador (típicamente entre 0,50 y 0,99) que define cuán parecidas deben ser dos memorias para considerarlas candidatas a duplicado. Un umbral alto (p. ej. 0,95) solo detecta duplicados casi exactos; uno bajo genera más candidatos pero con más falsos positivos. El valor actual se muestra junto a la etiqueta.</li><li><b>Número de candidatos</b>: cuántas memorias similares como máximo se recuperan y presentan en cada detección.</li></ul><p>La pantalla practica la <b>honestidad de estado</b>: el detector solo puede operar si al menos una memoria del tenant tiene embedding vectorial. Si ninguna lo tiene todavía, verás la insignia y la nota <b>«No disponible aún»</b> y los controles aparecen deshabilitados — en lugar de fingir que un ajuste filtra algo que técnicamente no puede filtrar.</p><p>Pulsa <b>Guardar</b> para persistir ambos valores; junto al botón verás el estado de la operación (<i>Guardando…</i>, <i>Guardado</i> o el mensaje de error). Como en el resto de ajustes, cualquier miembro puede consultar y solo un tenant_admin puede persistir.</p>",
      fullPage: true,
    },
    {
      title: "SSO empresarial con OIDC",
      goto: "/admin/settings/sso",
      body: "<p>Aquí un <b>tenant_admin</b> conecta el tenant con sus proveedores de identidad mediante <b>OpenID Connect (OIDC)</b>. Desde la migración 0115 la plataforma admite <b>varios proveedores SSO simultáneos</b> (varios OIDC y/o SAML a la vez): puedes tener, por ejemplo, Google Y Microsoft habilitados al mismo tiempo, y cada configuración habilitada pinta su <b>propio botón</b> en la pantalla de login, junto al formulario de contraseña. A partir de la <b>segunda</b> configuración, el <b>Nombre visible</b> es obligatorio — sin él los botones del login serían indistinguibles, y el sistema rechaza el alta. La pantalla gestiona cada entrada por separado: crear, editar, activar/desactivar o borrar. Bajo la cabecera, un enlace lleva a la pantalla hermana de <b>SAML</b> por si tu IdP habla ese protocolo en lugar de OIDC.</p><p>Arriba verás la tarjeta <b>URL base pública de la aplicación</b>. Es un valor <b>global de la plataforma</b> (una sola URL para todos los tenants, p. ej. <code>https://tu-dominio.com</code>) del que se derivan la <b>URL de callback/redirect</b> de OIDC y el ACS de SAML. La tarjeta contiene:</p><ul><li>El campo <b>URL base pública</b> con su botón <b>Guardar</b> — editable <b>solo por el system_admin</b>; el resto de roles la ven en modo lectura.</li><li>El campo <b>Prefijo de API (reverse proxy)</b>: si publicas la plataforma single-origin (SPA en la raíz y API bajo <code>/api</code> tras Caddy/nginx), pon aquí <code>/api</code>; déjalo vacío si el api-server cuelga de la raíz del dominio. El prefijo se inserta entre el origen y la ruta del callback.</li><li>La <b>URL de callback / redirect</b> derivada, en solo lectura y con botón <b>Copiar</b>: es exactamente la URL que debes registrar en tu IdP como redirect URI autorizada.</li></ul><p>Si la base sigue siendo el valor de arranque (apunta al api-server local, no a un dominio público), aparece un <b>aviso en ámbar</b> pidiéndote que configures tu URL pública real <i>antes</i> de registrar la callback en el IdP — de lo contrario el flujo de login rebotaría contra una dirección inalcanzable.</p><p>Si aún no hay configuración verás un estado vacío con el botón <b>Configurar OIDC</b>. Cada configuración creada se resume en una tarjeta: el nombre visible, los distintivos de estado (<b>activo/inactivo</b>) y de secreto (con su ubicación: <i>Vault</i> o <i>cifrado en reposo</i>; o <b>sin secreto</b> en ámbar), el <b>Issuer</b>, el <b>Client ID</b> y los <b>scopes</b>, junto a los botones <b>Activar/Desactivar</b> (conmuta el proveedor en el login sin tocar el resto de campos), <b>editar</b> (lápiz) y <b>eliminar</b> (papelera, con confirmación).</p><p><b>Advertencia honesta sobre el segundo factor (MFA)</b>: el backend de la plataforma soporta TOTP y WebAuthn, pero el <b>enrolamiento 2FA con código QR para el login con usuario y contraseña todavía NO tiene interfaz</b> — el SSO multi-proveedor está completo; el MFA local está pendiente de UI. Si vas a exponer el login a internet pública, la recomendación hasta que llegue el MFA local es: <b>SSO corporativo</b> (tu IdP ya aporta su propio segundo factor) más una <b>política de contraseñas fuertes</b> para las cuentas locales que queden, apoyada en el rate-limit de login que la plataforma trae activo por defecto.</p>",
      fullPage: true,
    },
    {
      title: "Configurar o editar OIDC (diálogo)",
      goto: "/admin/settings/sso",
      // Abre el diálogo de configuración OIDC (botón "Configurar OIDC"). Si ya
      // existe una config, el botón no está y la captura muestra la tarjeta.
      action: async (page) => {
        await page
          .getByTestId("sso-create-button")
          .click()
          .catch(async () => {
            await page
              .getByRole("button", { name: "Configurar OIDC" })
              .click()
              .catch(async () => {
                await page
                  .getByTestId("sso-edit-button")
                  .click()
                  .catch(() => {});
              });
          });
        await page.waitForTimeout(600);
      },
      body: "<p>Al pulsar <b>Configurar OIDC</b> (o el lápiz para editar) se abre el diálogo con el formulario. En la parte superior puedes elegir una <b>Plantilla de proveedor</b> (Azure AD/Entra, Google, Okta, Auth0, GitHub, GitLab, Apple, Facebook): al seleccionarla se pre-rellenan automáticamente el issuer, los scopes y el mapeo de claims con valores verificados para ese IdP, que después puedes ajustar a mano. Si la plantilla incluye notas específicas del proveedor, se muestran bajo el selector.</p><ul><li>Si la plantilla lo requiere, aparecen <b>parámetros específicos</b> (por ejemplo el <code>tenant</code> de Azure o el <code>domain</code> de Okta) que completan el patrón del issuer en tiempo real a medida que los escribes.</li><li><b>Nombre visible</b>: cómo se llamará el botón de este proveedor en la pantalla de login. Es opcional en la primera configuración y <b>obligatorio a partir de la segunda</b> (con varios proveedores habilitados es lo único que permite distinguir sus botones; el backend rechaza un segundo alta sin él).</li><li><b>Issuer</b>: la URL raíz del IdP; el descubrimiento OIDC consulta <code>&lt;issuer&gt;/.well-known/openid-configuration</code> para obtener los endpoints, así que debe ser exacta.</li><li><b>Client ID</b> y <b>Client secret</b>: las credenciales de la aplicación que registraste en el IdP. El secreto se cifra en reposo y el sistema nunca lo devuelve en claro; al crear es <b>obligatorio</b>, al editar es opcional — dejar el campo vacío conserva el actual.</li><li><b>Scopes</b> separados por espacios (por defecto <code>openid email profile</code>).</li><li>La casilla <b>Activar este proveedor en el login</b>: recuerda que se añade al login local, no lo reemplaza.</li></ul><p>Pulsa <b>Crear</b> o <b>Guardar cambios</b> para confirmar, o <b>Cancelar</b> para descartar. El botón de confirmación permanece deshabilitado hasta que issuer, Client ID y (al crear) el secreto están rellenos. Los errores devueltos por el backend se muestran dentro del diálogo sin cerrar el formulario.</p><p>Flujo recomendado de puesta en marcha: registra la aplicación en tu IdP con la URL de callback copiada de la pantalla anterior; crea aquí la configuración <b>sin activar</b>; verifica con un usuario de prueba; y actívala solo entonces. Si algo va mal, el login local sigue funcionando en todo momento.</p>",
      fullPage: true,
    },
    {
      title: "SSO empresarial con SAML 2.0",
      goto: "/admin/settings/sso/saml",
      body: "<p>Pantalla equivalente a la de OIDC pero para el protocolo <b>SAML 2.0</b>, habitual en entornos corporativos con ADFS, Okta o Shibboleth. Desde la migración 0115 también aquí se admiten <b>varias configuraciones simultáneas</b> — y pueden convivir con las de OIDC: todo se añade junto al login local sin reemplazarlo. Igual que en OIDC, el <b>Nombre visible</b> es obligatorio a partir de la segunda configuración habilitada. Un matiz propio de SAML con varias configuraciones: en el flujo <b>SP-initiated</b> (el usuario pulsa el botón en nuestro login) el sistema siempre sabe qué proveedor inició el flujo y resuelve sin ambigüedad; en cambio, el flujo <b>IdP-initiated</b> (el usuario arranca desde el portal de su IdP, sin RelayState nuestro) <b>exige desambiguación</b> cuando hay más de un SAML habilitado — la plataforma lo rechaza con un error explícito en vez de adivinar el proveedor. Si tu organización depende del acceso IdP-initiated, mantén un solo SAML habilitado o inicia siempre la sesión desde nuestra pantalla de login. Un <b>tenant_admin</b> puede crear, editar, activar/desactivar o borrar cada configuración; un enlace bajo la cabecera lleva de vuelta a OIDC.</p><p>La tarjeta <b>Metadatos del SP (este sistema)</b> muestra los valores que tu proveedor de identidad necesita conocer sobre esta plataforma. Son <b>globales</b> (una sola identidad de Service Provider para toda la plataforma):</p><ul><li><b>SP Entity ID</b>: el identificador de entidad con el que la plataforma se presenta ante el IdP.</li><li><b>URL de ACS</b> (Assertion Consumer Service): la dirección a la que el IdP enviará la respuesta SAML tras autenticar al usuario.</li></ul><p>Ambos valores tienen botón <b>Copiar</b> para registrarlos en el IdP sin errores de transcripción, y debajo se indica la base pública de la que derivan. Si la URL de ACS sigue usando la <b>base por defecto</b> — un marcador de posición de arranque que ni siquiera apunta al api-server de desarrollo — aparece un aviso en ámbar: configura la URL base pública real (variable <code>SSO_REDIRECT_BASE_URL</code> / tarjeta de URL base de la pantalla OIDC) <i>antes</i> de registrar el ACS en el IdP, o el flujo de login nunca podrá volver a la plataforma.</p><p>Si no hay configuración, el estado vacío ofrece el botón <b>Configurar SAML</b>. Una vez creada, la tarjeta resume el estado con distintivos: <b>activo/inactivo</b>, si hay <b>clave del SP</b> (y dónde vive: Vault o cifrada en reposo) o «sin clave SP», y si el <b>AuthnRequest va firmado</b>. Debajo se listan el <b>IdP Entity ID</b>, la <b>SSO URL</b> del IdP y el <b>formato de NameID</b>, con los botones <b>Activar/Desactivar</b>, editar (lápiz) y eliminar (papelera, con confirmación).</p>",
      fullPage: true,
    },
    {
      title: "Configurar o editar SAML (diálogo)",
      goto: "/admin/settings/sso/saml",
      // Abre el diálogo de configuración SAML (botón "Configurar SAML"). Si ya
      // existe una config, el botón no está y abrimos la edición.
      action: async (page) => {
        await page
          .getByTestId("saml-create-button")
          .click()
          .catch(async () => {
            await page
              .getByRole("button", { name: "Configurar SAML" })
              .click()
              .catch(async () => {
                await page
                  .getByTestId("saml-edit-button")
                  .click()
                  .catch(() => {});
              });
          });
        await page.waitForTimeout(600);
      },
      body: "<p>El diálogo de SAML empieza por el bloque <b>Metadatos del IdP (XML)</b>, la vía rápida de configuración: puedes pegar el <code>EntityDescriptor</code> del proveedor en el área de texto o pulsar <b>Subir XML</b> para cargar el archivo de metadatos, y luego <b>Extraer datos</b> para que el sistema rellene automáticamente el Entity ID, la URL de SSO, el certificado y — si el XML lo declara — el formato de NameID. Si el XML no se puede analizar, el error se muestra bajo los botones y puedes rellenar los campos a mano.</p><ul><li>Campos del IdP: <b>Nombre visible</b> (opcional), <b>IdP Entity ID</b>, <b>URL de SSO del IdP</b> y <b>Certificado de firma del IdP (X.509)</b> — con este certificado se verifica la firma de las aserciones que envía el IdP. Estos tres últimos son obligatorios.</li><li><b>Formato de NameID</b> mediante un selector cerrado: <i>emailAddress</i> (recomendado), <i>persistent</i>, <i>transient</i> o <i>unspecified</i>; y el mapeo opcional de atributos de <b>email</b> y <b>nombre</b> (p. ej. <code>displayName</code>) si tu IdP los emite con nombres no estándar.</li><li>Bloque del <b>SP</b>: certificado público y clave privada en PEM — solo necesarios si firmas el AuthnRequest o exiges aserciones/NameID cifrados. La clave privada se cifra en reposo y el sistema nunca la devuelve; al editar, dejar el campo vacío conserva la actual.</li><li>Casillas de seguridad: <b>firmar el AuthnRequest saliente</b> (requiere clave del SP), <b>exigir aserciones firmadas por el IdP</b> (recomendado y activado por defecto), <b>exigir aserciones cifradas</b> y <b>exigir NameID cifrado</b> (ambas requieren clave del SP); más la casilla para <b>activar</b> el proveedor en el login.</li></ul><p>Pulsa <b>Crear</b> o <b>Guardar cambios</b> para confirmar (el botón se habilita cuando Entity ID, URL de SSO y certificado del IdP están rellenos) o <b>Cancelar</b> para salir sin guardar. Los errores del backend se muestran dentro del diálogo.</p><p>Recomendación de seguridad: mantén siempre activada la exigencia de aserciones firmadas; sin ella, cualquier respuesta SAML sin verificar podría suplantar a un usuario. Activa el cifrado de aserciones solo si tu IdP lo soporta y has cargado la clave del SP.</p>",
      fullPage: true,
    },
    {
      title: "Tarifa horaria del tenant",
      goto: "/admin/settings/hourly-rate",
      body: "<p>Esta pantalla define la <b>tarifa por hora</b> que el cálculo de coste humano de los planes usa por defecto: cuando un miembro registra horas al entregar una tarea, el sistema multiplica esas horas por esta tarifa para componer el desglose de coste del plan. Es la categoría de <b>Costes</b> con página dedicada a la que se llega desde el índice de Ajustes.</p><ul><li>Campo <b>Tarifa por hora</b>: un número con paso 0,01, entre 0 y 10000. Si lo dejas vacío, la plataforma aplica su valor por defecto de <b>50 EUR/h</b>.</li><li>Campo <b>Moneda</b>: código de tres letras (por defecto <code>EUR</code>), que se convierte automáticamente a mayúsculas mientras escribes.</li></ul><p>El botón <b>Guardar</b> solo se habilita cuando hay cambios respecto al valor almacenado (no puedes «guardar» sin haber tocado nada). Al guardar correctamente verás el mensaje <b>Guardado</b>; si hay un error de validación o de permisos, se muestra el mensaje exacto del backend.</p><p>Cualquier miembro del tenant puede consultar la tarifa, pero solo un <b>tenant_admin</b> puede persistirla (en caso contrario el servidor responde 403). Al cambiarla, los desgloses de coste de los planes se recalculan automáticamente con el nuevo valor — ten en cuenta que afecta a las estimaciones visibles de todos los planes del tenant.</p>",
      fullPage: true,
    },
    {
      title: "Valores por defecto de plataforma (System Admin)",
      goto: "/admin/settings/platform-defaults",
      body: "<p>Esta pantalla es <b>exclusiva del System Admin</b>: edita los ajustes globales de la plataforma que no tienen página propia — el modelo por defecto de los agentes, límites de ejecución, parámetros de RAG, mantenimiento… Cualquier otro rol ve un aviso indicando que la sección es exclusiva del System Admin. Como el índice de Ajustes, está guiada por un <b>registro del backend</b>: los grupos, tipos y límites de cada ajuste vienen del servidor, así que la pantalla crece sola cuando se registran ajustes nuevos.</p><p>Los ajustes se agrupan en tarjetas por categoría. Cada ajuste muestra su nombre en español, su descripción, su <b>clave técnica</b> en fuente monoespaciada (útil para correlacionar con documentación y soporte) y un control acorde a su tipo:</p><ul><li><b>Booleano</b>: una casilla Activado/Desactivado.</li><li><b>Entero</b> y <b>decimal</b>: un campo numérico con los mínimos y máximos que impone el registro.</li><li><b>Configuración de modelo</b>: el ajuste más importante — el <b>modelo por defecto de los agentes</b>. Se elige un <b>proveedor concreto</b> del catálogo de proveedores LLM sincronizados (cada opción muestra su nombre y su tipo: claude_sdk, ollama…) y después un <b>modelo</b> de la lista de ese proveedor. Este valor es la raíz de la cadena de herencia de modelo: plataforma → proyecto → agente, donde cada nivel puede sobreescribir al anterior.</li></ul><p>Cada ajuste tiene su propio botón <b>Guardar</b>: los cambios se validan y persisten uno a uno (verás «Guardado ✓» o el mensaje de error junto al ajuste), de modo que un valor inválido nunca bloquea el resto de la pantalla.</p><p>Al final, si además del rol de System Admin eres el <b>System Owner</b> del despliegue, aparece una sección adicional para el modelo del córtex (la mente del asistente del dueño de la plataforma); los System Admin normales no la ven.</p>",
      fullPage: true,
    },
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  test.setTimeout(300_000);
  await login(page);
  await generateManual(page, manual);
});
