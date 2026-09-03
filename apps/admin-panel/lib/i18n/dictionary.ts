/**
 * Diccionario central del panel (plan prod-16, `task_prod16_01`).
 *
 * Un `Record` por módulo, hecho a mano y sin librería: decisión D1 opción B del
 * plan. El panel es interno, sin SEO ni routing por locale, y el catálogo está
 * cerrado en ES+EN — next-intl/i18next serían sobrecoste. La ventaja concreta de
 * hacerlo así es que el tipado es exhaustivo: una clave sin traducción EN no
 * compila.
 *
 * ## Cómo añadir texto
 *
 * 1. Elige el namespace del módulo (créalo si no existe).
 * 2. Añade la clave con sus DOS idiomas.
 * 3. Úsala con `useT("login")` → `t("submit")`.
 *
 * No metas aquí texto que venga del backend ya bilingüe (`note_es`/`note_en`
 * del córtex, por ejemplo): eso se elige con `useLangOptional()`.
 *
 * ## Estado de la migración (2026-07-30)
 *
 * Migrado: `login`, el shell entero (cabecera + sidebar de 6 grupos y ~39
 * ítems), `select-tenant`, `no-access`, `users` y los mensajes de error de la
 * capa de API. Eso cubre `task_prod16_01`, `task_prod16_02` y la primera
 * pantalla de `task_prod16_03`.
 *
 * **Pendiente**: el resto de `task_prod16_03` (`tenants`, `tenant-stats`,
 * `backup/*`, `settings/*`, `projects/*`, `agents/*`) y todo
 * `task_prod16_04` (~250 ficheros: marketplace, guardrails, knowledge-bases,
 * llm-providers, model-prices, ollama, notifications, docs, assistant, tools,
 * memories, córtex…).
 *
 * `scripts/check-i18n.mjs` lleva la cuenta con DOS trinquetes —ternarios
 * `lang === "es" ? …` y castellano cableado en atributos de UI— y sus dos
 * allowlists sólo pueden MENGUAR. Lo que ya está migrado no aparece en ellas, y
 * por tanto está protegido contra regresión.
 */

import type { Dictionary } from "./types";

export const dictionary = {
  /**
   * Textos compartidos entre módulos.
   *
   * Sólo entra aquí lo que YA consume alguien. Una clave sin llamante es la
   * versión i18n del patrón "mecanismo entregado, cero llamantes" que este repo
   * arrastra (verificar-antes-de-implementar §5): envejece sin que nadie lo
   * note. El nombre del producto, en cambio, NO va al diccionario: un nombre
   * propio no se traduce.
   */
  common: {
    loading: { es: "Cargando…", en: "Loading…" },
    close: { es: "Cerrar", en: "Close" },
    /**
     * El BCP-47 con el que formatear fechas y números (`toLocaleString`).
     *
     * No es texto de UI, pero sí un dato POR IDIOMA, y su sitio natural es el
     * mismo: hasta prod-16 `task_prod16_03` había nueve `toLocaleString("es-ES")`
     * cableados por el panel, así que con el toggle en inglés la fecha del último
     * sync y el techo de tokens de plataforma seguían con formato castellano.
     * La alternativa —`lang === "es" ? "es-ES" : "en-GB"`— es justo el ternario
     * que `check-i18n` prohíbe, y con razón: es la misma decisión repetida.
     *
     * `en-GB` y no `en-US` porque el panel es interno y europeo: mantiene el día
     * delante, que es como lo lee quien también usa la cara castellana.
     */
    dateLocale: { es: "es-ES", en: "en-GB" },
  },

  /**
   * Mensajes de error de la capa de API (`lib/api-error.ts`).
   *
   * Son los textos con los que se sustituye el cuerpo crudo del backend cuando
   * éste no trae un `detail` legible: antes de prod-16 `task_prod16_05` las 14
   * copias de `errorText` pintaban `err.body` tal cual, así que un 502 de nginx
   * o un traceback de Python acababan en pantalla.
   *
   * Se redactan en segunda persona y sin jerga HTTP: quien los lee es un
   * operador, no quien depura el backend.
   */
  errors: {
    badRequest: {
      es: "La petición no es válida. Revisa los datos e inténtalo de nuevo.",
      en: "The request is not valid. Check the data and try again.",
    },
    unauthorized: {
      es: "Tu sesión no es válida o ha caducado. Vuelve a iniciar sesión.",
      en: "Your session is invalid or has expired. Please sign in again.",
    },
    forbidden: {
      es: "No tienes permiso para hacer esto.",
      en: "You do not have permission to do this.",
    },
    notFound: {
      es: "No se encontró lo que buscabas.",
      en: "What you were looking for could not be found.",
    },
    conflict: {
      es: "La operación choca con el estado actual. Recarga y vuelve a intentarlo.",
      en: "The operation conflicts with the current state. Reload and try again.",
    },
    invalidData: {
      es: "Algún dato no es correcto. Revisa el formulario.",
      en: "Some data is not correct. Please review the form.",
    },
    tooManyRequests: {
      es: "Demasiadas peticiones. Espera un momento y vuelve a intentarlo.",
      en: "Too many requests. Please wait a moment and try again.",
    },
    server: {
      es: "El servidor ha fallado. Si se repite, avisa a administración.",
      en: "The server failed. If it keeps happening, contact an administrator.",
    },
    network: {
      es: "No se pudo contactar con el servidor. Comprueba tu conexión.",
      en: "Could not reach the server. Check your connection.",
    },
    unexpected: {
      es: "Ha ocurrido un error inesperado.",
      en: "An unexpected error occurred.",
    },
    // El status va DENTRO del texto a propósito: sin él, un código raro (418,
    // 507…) daría un mensaje indistinguible del genérico y nadie podría
    // reportarlo.
    withStatus: {
      es: "El servidor respondió con un error {status}.",
      en: "The server replied with error {status}.",
    },
  },

  /**
   * Navegación del panel: los 6 grupos y sus ~40 ítems (`components/layout/admin-shell.tsx`).
   *
   * Las claves son estables e independientes del texto (`humanQueue`, no
   * `esperanTuDecision`) porque `NAV_GROUPS` las lleva en `labelKey` y los tests
   * de estructura del menú las comparan; cambiar un texto no debe tocar el test
   * de RBAC. El `data-testid` de cada enlace se sigue derivando del `href`, así
   * que los e2e no dependen de esto.
   *
   * Varias entradas son idénticas en los dos idiomas (Dashboard, Runs,
   * Marketplace, Settings, Guardrails, Backups, Auth/SSO, Knowledge Bases): son
   * los términos que la UI castellana ya usaba en inglés. Están declaradas en la
   * allowlist del test del diccionario para que no las confunda con un
   * copia-pega sin traducir.
   */
  nav: {
    // --- grupos ---
    groupTrabajo: { es: "Trabajo", en: "Work" },
    groupRecursos: { es: "Recursos", en: "Resources" },
    groupConfigTenant: { es: "Configuración del tenant", en: "Tenant settings" },
    groupPlataforma: { es: "Plataforma", en: "Platform" },
    groupCortex: { es: "Córtex", en: "Cortex" },
    groupAyuda: { es: "Ayuda", en: "Help" },

    // --- grupo Trabajo ---
    dashboard: { es: "Dashboard", en: "Dashboard" },
    inbox: { es: "Mis tareas", en: "My tasks" },
    humanQueue: { es: "Esperan tu decisión", en: "Awaiting your decision" },
    security: { es: "Seguridad", en: "Security" },
    board: { es: "Tablero", en: "Board" },
    office: { es: "La Oficina", en: "The Office" },
    runs: { es: "Runs", en: "Runs" },
    leaderboard: { es: "Rendimiento", en: "Performance" },
    approvals: { es: "Aprobaciones", en: "Approvals" },
    notificationsInbox: { es: "Bandeja", en: "Inbox" },
    assistant: { es: "Asistente", en: "Assistant" },

    // --- grupo Recursos ---
    agents: { es: "Agentes", en: "Agents" },
    tools: { es: "Catálogo", en: "Catalog" },
    humanAgents: { es: "Agentes humanos", en: "Human agents" },
    teams: { es: "Equipos", en: "Teams" },
    projects: { es: "Proyectos", en: "Projects" },
    knowledgeBases: { es: "Knowledge Bases", en: "Knowledge Bases" },
    memories: { es: "Memorias", en: "Memories" },
    documents: { es: "Documentos", en: "Documents" },

    // --- grupo Configuración del tenant ---
    guardrails: { es: "Guardrails", en: "Guardrails" },
    approvalPolicy: { es: "Validación humana", en: "Human validation" },
    notifications: { es: "Notificaciones", en: "Notifications" },
    evalQuality: { es: "Calidad (Evals)", en: "Quality (Evals)" },
    tenantStats: { es: "Estadísticas", en: "Statistics" },
    marketplace: { es: "Marketplace", en: "Marketplace" },
    settings: { es: "Settings", en: "Settings" },

    // --- grupo Plataforma ---
    users: { es: "Usuarios", en: "Users" },
    invitations: { es: "Invitaciones", en: "Invitations" },
    llmProviders: { es: "Proveedores LLM", en: "LLM providers" },
    ollama: { es: "Ollama & Embeddings", en: "Ollama & Embeddings" },
    platformDefaults: { es: "Valores por defecto", en: "Defaults" },
    modelPrices: { es: "Modelos & Precios", en: "Models & Prices" },
    sso: { es: "Auth/SSO", en: "Auth/SSO" },
    backup: { es: "Backups", en: "Backups" },
    backupDestinations: { es: "Destinos backup", en: "Backup destinations" },
    backupRestore: { es: "Restaurar backup", en: "Restore backup" },

    // --- grupo Córtex ---
    cortex: { es: "Córtex", en: "Cortex" },
    cortexMind: { es: "Panel de Mente", en: "Mind panel" },
    cortexIdentity: { es: "Identidad", en: "Identity" },

    // --- grupo Ayuda ---
    docs: { es: "Documentación", en: "Documentation" },
  },

  /**
   * Cabecera y sidebar (`components/layout/admin-header.tsx`, `admin-shell.tsx`).
   *
   * El nombre del producto ("Agentic Platform") NO está aquí: un nombre propio
   * no se traduce. Las etiquetas del badge de rol (`system_admin`/`admin`/`user`)
   * tampoco: son los identificadores del backend y el test
   * `admin-header-role-badge.test.tsx` los lee tal cual.
   */
  shell: {
    openMenu: { es: "Abrir menú", en: "Open menu" },
    closeMenu: { es: "Cerrar menú", en: "Close menu" },
    language: { es: "Idioma", en: "Language" },
    userMenu: { es: "Menú de usuario", en: "User menu" },
    accountOf: { es: "Cuenta de {name}", en: "{name}'s account" },
    myAccount: { es: "Mi cuenta", en: "My account" },
    profile: { es: "Perfil", en: "Profile" },
    logout: { es: "Cerrar sesión", en: "Sign out" },
    loggingOut: { es: "Cerrando sesión…", en: "Signing out…" },
  },

  /** `app/select-tenant/page.tsx`. */
  selectTenant: {
    title: { es: "Elige un espacio de trabajo", en: "Choose a workspace" },
    help: {
      es: "Tienes acceso a varios espacios. Selecciona con cuál quieres entrar.",
      en: "You have access to several workspaces. Pick the one you want to enter.",
    },
    errorList: {
      es: "No se pudo cargar la lista de espacios de trabajo.",
      en: "The list of workspaces could not be loaded.",
    },
    errorActivate: {
      es: "No se pudo activar ese espacio de trabajo. Inténtalo de nuevo.",
      en: "That workspace could not be activated. Please try again.",
    },
  },

  /** `app/no-access/page.tsx`. */
  noAccess: {
    title: { es: "Sin acceso a la plataforma", en: "No access to the platform" },
    body: {
      es: "No tienes permisos asignados en la plataforma. Contacta con el administrador para que te asigne acceso a un espacio de trabajo.",
      en: "You have no permissions assigned on the platform. Contact your administrator so they can grant you access to a workspace.",
    },
  },

  /**
   * `app/admin/users/page.tsx` — usuarios globales y sus memberships (ADR 0047).
   *
   * Los nombres de rol (`Tenant Admin`, `Tenant User`, `System Operator`) NO se
   * traducen: son los identificadores que expone el backend y lo que el operador
   * ve en logs y en la API. `plan_approver` sí, porque no tiene forma canónica en
   * inglés en el backend.
   *
   * Cuidado con activo/activa: en castellano el género cambia según el sustantivo
   * (un usuario activo, una membership activa) y en inglés no, así que hacen falta
   * dos claves aunque en EN coincidan.
   */
  users: {
    title: { es: "Usuarios", en: "Users" },
    description: {
      es: "Usuarios globales de la plataforma. El acceso a cada tenant lo dan las membership (usuario↔tenant + rol) que asigna el System Admin (ADR 0047).",
      en: "Platform-wide users. Access to each tenant comes exclusively from the memberships (user↔tenant + role) the System Admin grants here (ADR 0047).",
    },
    forbidden: {
      es: "Esta sección es exclusiva del System Admin de la plataforma.",
      en: "This section is reserved for the platform System Admin.",
    },
    searchPlaceholder: { es: "Buscar por email o nombre…", en: "Search by email or name…" },
    searchLabel: { es: "Buscar usuarios", en: "Search users" },
    loading: { es: "Cargando usuarios…", en: "Loading users…" },
    emptyNone: { es: "No hay usuarios en la plataforma.", en: "There are no users yet." },
    emptyNoMatch: {
      es: "Ningún usuario coincide con la búsqueda.",
      en: "No user matches your search.",
    },
    colUser: { es: "Usuario", en: "User" },
    colEmail: { es: "Email", en: "Email" },
    colType: { es: "Tipo", en: "Type" },
    colStatus: { es: "Estado", en: "Status" },
    colTenantAccess: { es: "Acceso a tenants", en: "Tenant access" },
    typeSystemAdmin: { es: "System Admin", en: "System Admin" },
    typeUser: { es: "Usuario", en: "User" },
    userActive: { es: "activo", en: "active" },
    userInactive: { es: "inactivo", en: "inactive" },
    manageTenants: { es: "Gestionar tenants", en: "Manage tenants" },

    dialogTitle: { es: "Acceso a tenants — {who}", en: "Tenant access — {who}" },
    dialogDescription: {
      es: "El acceso a cada tenant lo da una membership con un rol. Sin membership activa, el usuario no entra a ningún tenant (ADR 0047).",
      en: "Access to a tenant is granted by a membership with a role. Without an active membership the user cannot enter any tenant (ADR 0047).",
    },
    membershipsLoading: { es: "Cargando membership…", en: "Loading memberships…" },
    membershipsEmpty: {
      es: "El usuario no tiene acceso a ningún tenant. Asígnale uno abajo.",
      en: "This user has no tenant access. Grant one below.",
    },
    colTenant: { es: "Tenant", en: "Tenant" },
    colRole: { es: "Rol", en: "Role" },
    colActions: { es: "Acciones", en: "Actions" },
    roleOf: { es: "Rol de {tenant}", en: "Role for {tenant}" },
    deactivateMembership: { es: "Desactivar membership", en: "Deactivate membership" },
    activateMembership: { es: "Activar membership", en: "Activate membership" },
    membershipActive: { es: "activa", en: "active" },
    membershipInactive: { es: "inactiva", en: "inactive" },
    revokeAccess: { es: "Revocar acceso", en: "Revoke access" },

    assignTitle: { es: "Asignar acceso a un tenant", en: "Grant access to a tenant" },
    noTenantsAvailable: { es: "Sin tenants disponibles", en: "No tenants available" },
    pickTenant: { es: "Selecciona un tenant…", en: "Select a tenant…" },
    assigning: { es: "Asignando…", en: "Assigning…" },
    assign: { es: "Asignar", en: "Assign" },
    allTenantsAssigned: {
      es: "El usuario ya tiene acceso a todos los tenants existentes.",
      en: "This user already has access to every existing tenant.",
    },

    roleTenantAdmin: { es: "Tenant Admin", en: "Tenant Admin" },
    roleTenantUser: { es: "Tenant User", en: "Tenant User" },
    rolePlanApprover: { es: "Aprobador de planes", en: "Plan approver" },
    roleSystemOperator: { es: "System Operator", en: "System Operator" },
  },

  /** `app/login/page.tsx`. */
  login: {
    tagline: {
      es: "Panel de administración multi-tenant",
      en: "Multi-tenant administration panel",
    },
    cardTitle: { es: "Iniciar sesión", en: "Sign in" },
    mfaTitle: { es: "Verificación en dos pasos", en: "Two-step verification" },
    // "Email" se escribe igual en los dos idiomas; el test de diccionario lo
    // tiene en su allowlist de coincidencias legítimas.
    emailLabel: { es: "Email", en: "Email" },
    passwordLabel: { es: "Contraseña", en: "Password" },
    submit: { es: "Iniciar sesión", en: "Sign in" },
    submitting: { es: "Entrando…", en: "Signing in…" },
    errorInvalidCredentials: {
      es: "Email o contraseña incorrectos.",
      en: "Invalid email or password.",
    },
    errorRateLimited: {
      es: "Demasiados intentos. Espera un momento y vuelve a intentarlo.",
      en: "Too many attempts. Please wait and try again.",
    },
    errorUnreachable: {
      es: "No se pudo contactar con el servidor.",
      en: "Could not reach the server.",
    },

    // --- segundo factor (`components/login/mfa-challenge.tsx`) -------------
    // El paso de TOTP estaba entero en castellano cableado hasta
    // `task_prod16_02`: con el toggle en EN, quien tiene MFA activado leía
    // «Código de verificación» en el momento en que más se lee. No lo veía
    // ninguna de las dos guardas (no hay ternario, y su texto es JSX suelto,
    // no atributos) ni el test del login, que sólo renderiza el formulario de
    // password. `errorUnreachable` se COMPARTE con el paso anterior a
    // propósito: es el mismo fallo de red y dos claves acabarían divergiendo.
    mfaHelp: {
      es: "Introduce el código de tu app de autenticación (o un código de recuperación).",
      en: "Enter the code from your authenticator app (or a recovery code).",
    },
    mfaCodeLabel: { es: "Código de verificación", en: "Verification code" },
    mfaSubmit: { es: "Verificar", en: "Verify" },
    mfaSubmitting: { es: "Verificando…", en: "Verifying…" },
    mfaErrorInvalidCode: {
      es: "Código incorrecto. Prueba de nuevo o usa un código de recuperación.",
      en: "Incorrect code. Try again or use a recovery code.",
    },
    mfaErrorExpired: {
      es: "El desafío ha caducado. Vuelve a iniciar sesión.",
      en: "The challenge has expired. Please sign in again.",
    },

    // --- botones de SSO (`components/login/provider-buttons.tsx`) ----------
    // El separador estaba en castellano fijo y los cinco textos de respaldo de
    // marca en INGLÉS fijo, o sea el mismo defecto en los dos sentidos dentro
    // de la misma tarjeta. Los respaldos SÓLO se usan cuando el operador dejó
    // `button_label` vacío: su etiqueta manda, porque la escribió una persona
    // para su IdP. Microsoft, Google y GitHub publican su texto de «sign in»
    // traducido, así que traducirlo no rompe ninguna guía de marca.
    ssoDivider: { es: "o continúa con", en: "or continue with" },
    ssoWithMicrosoft: { es: "Iniciar sesión con Microsoft", en: "Sign in with Microsoft" },
    ssoWithGoogle: { es: "Iniciar sesión con Google", en: "Sign in with Google" },
    ssoWithGithub: { es: "Iniciar sesión con GitHub", en: "Sign in with GitHub" },
    ssoWithSso: { es: "Iniciar sesión con SSO", en: "Sign in with SSO" },
  },

  /**
   * `app/accept-invite/page.tsx` — canje de una invitación (ADR 0134).
   *
   * Ojo con los mensajes de error: el backend devuelve un 403 GENÉRICO para
   * todos los motivos de rechazo (token inventado, caducado, revocado, ya
   * canjeado, o para otro email) precisamente para no volver a abrir el oráculo
   * de enumeración que cerró el ADR. La UI **no debe inventar** un motivo
   * concreto que no sabe: dice qué hacer (pedir otra invitación), no qué pasó.
   */
  acceptInvite: {
    title: { es: "Aceptar invitación", en: "Accept invitation" },
    tagline: {
      es: "Crea tu cuenta con la invitación que te han enviado",
      en: "Create your account with the invitation you were sent",
    },
    emailLabel: { es: "Email", en: "Email" },
    emailHelp: {
      es: "Tiene que ser el mismo email al que se envió la invitación.",
      en: "It must be the same email the invitation was sent to.",
    },
    nameLabel: { es: "Nombre y apellidos", en: "Full name" },
    passwordLabel: { es: "Contraseña", en: "Password" },
    tokenLabel: { es: "Código de invitación", en: "Invitation code" },
    submit: { es: "Crear cuenta", en: "Create account" },
    submitting: { es: "Creando…", en: "Creating…" },
    successTitle: { es: "Cuenta creada", en: "Account created" },
    successBody: {
      es: "Ya puedes iniciar sesión con tu email y tu contraseña.",
      en: "You can now sign in with your email and password.",
    },
    goToLogin: { es: "Ir a iniciar sesión", en: "Go to sign in" },
    errorRejected: {
      es: "Esta invitación no es válida. Puede haber caducado, haber sido revocada o haberse usado ya. Pide una nueva al administrador.",
      en: "This invitation is not valid. It may have expired, been revoked or already been used. Ask your administrator for a new one.",
    },
    errorDuplicate: {
      es: "Ya existe una cuenta con ese email. Inicia sesión en lugar de crear una nueva.",
      en: "An account with that email already exists. Sign in instead of creating a new one.",
    },
    errorUnreachable: {
      es: "No se pudo contactar con el servidor.",
      en: "Could not reach the server.",
    },
  },

  /** `app/admin/invitations/page.tsx` — emisión y revocación (ADR 0134). */
  invitations: {
    title: { es: "Invitaciones", en: "Invitations" },
    description: {
      es: "El registro público está cerrado: un usuario nuevo solo entra con una invitación emitida aquí, que caduca y sirve una sola vez.",
      en: "Public sign-up is closed: a new user can only join with an invitation issued here, which expires and can be used once.",
    },
    forbidden: {
      es: "Esta sección es exclusiva del System Admin de la plataforma.",
      en: "This section is reserved for the platform System Admin.",
    },
    empty: { es: "No hay invitaciones todavía.", en: "No invitations yet." },
    issue: { es: "Emitir invitación", en: "Issue invitation" },
    issuing: { es: "Emitiendo…", en: "Issuing…" },
    emailLabel: { es: "Email", en: "Email" },
    tenantLabel: { es: "Espacio de trabajo", en: "Workspace" },
    roleLabel: { es: "Rol", en: "Role" },
    ttlLabel: { es: "Caduca en (horas)", en: "Expires in (hours)" },
    colEmail: { es: "Email", en: "Email" },
    colTenant: { es: "Espacio", en: "Workspace" },
    colRole: { es: "Rol", en: "Role" },
    colCode: { es: "Código", en: "Code" },
    colStatus: { es: "Estado", en: "Status" },
    colExpires: { es: "Caduca", en: "Expires" },
    colActions: { es: "Acciones", en: "Actions" },
    revoke: { es: "Revocar", en: "Revoke" },
    statusPending: { es: "Pendiente", en: "Pending" },
    statusRedeemed: { es: "Canjeada", en: "Redeemed" },
    statusRevoked: { es: "Revocada", en: "Revoked" },
    statusExpired: { es: "Caducada", en: "Expired" },
    /**
     * El aviso que acompaña al token recién emitido. No es decoración: el valor
     * en claro no se persiste en ninguna parte, así que si el admin cierra el
     * diálogo sin copiarlo, la única salida es revocar y emitir otra.
     */
    tokenOnceTitle: { es: "Copia el código ahora", en: "Copy the code now" },
    tokenOnceBody: {
      es: "Este código solo se muestra una vez: no se guarda en ninguna parte. Si lo pierdes, revoca la invitación y emite otra.",
      en: "This code is shown only once: it is stored nowhere. If you lose it, revoke the invitation and issue a new one.",
    },
    linkLabel: { es: "Enlace para el invitado", en: "Link for the invitee" },
    roleTenantAdmin: { es: "Tenant Admin", en: "Tenant Admin" },
    roleTenantUser: { es: "Tenant User", en: "Tenant User" },
    rolePlanApprover: { es: "Aprobador de planes", en: "Plan approver" },
    roleSystemOperator: { es: "System Operator", en: "System Operator" },
    errorDuplicate: {
      es: "Ya hay una invitación pendiente para ese email en ese espacio.",
      en: "There is already a pending invitation for that email in that workspace.",
    },
  },

  /** `app/admin/backup/page.tsx` — cadencia, ventana y retención del backup diario. */
  backup: {
    title: { es: "Programación de backups", en: "Backup schedule" },
    description: {
      es: "Cadencia (cron), ventana horaria y retención local del backup diario. Lectura abierta; edición solo System Admin.",
      en: "Cadence (cron), time window and local retention of the daily backup. Anyone may read it; only a System Admin may edit it.",
    },
    cardTitle: { es: "Configuración", en: "Configuration" },
    loading: { es: "Cargando…", en: "Loading…" },
    enabledLabel: { es: "Backup diario activado", en: "Daily backup enabled" },
    cronLabel: { es: "Cron (ventana horaria)", en: "Cron (time window)" },
    cronHelp: {
      es: '5 campos: minuto hora día-del-mes mes día-de-la-semana. Por defecto las 03:00 cada día ("0 3 * * *").',
      en: '5 fields: minute hour day-of-month month day-of-week. Defaults to 03:00 every day ("0 3 * * *").',
    },
    retentionLabel: { es: "Retención local (días)", en: "Local retention (days)" },
    retentionHelp: {
      es: "Los bundles más antiguos que esta ventana se eliminan tras un backup correcto (entre 1 y 3650 días).",
      en: "Bundles older than this window are deleted after a successful backup (between 1 and 3650 days).",
    },
    saved: { es: "Guardado.", en: "Saved." },
    save: { es: "Guardar", en: "Save" },
    saving: { es: "Guardando…", en: "Saving…" },
    roStatus: { es: "Estado", en: "Status" },
    roCron: { es: "Cron", en: "Cron" },
    roRetention: { es: "Retención", en: "Retention" },
    roEnabled: { es: "Activado", en: "Enabled" },
    roDisabled: { es: "Desactivado", en: "Disabled" },
    roDays: { es: "{n} días", en: "{n} days" },
  },

  /**
   * `app/admin/backup/destinations/page.tsx`.
   *
   * Los `field*` son los labels de la config NO secreta por tipo de destino
   * (`TYPE_FIELDS`). Varios coinciden en los dos idiomas porque son términos
   * técnicos que nadie traduce (Bucket, Host, Path…): están en la allowlist de
   * coincidencias legítimas de `i18n.test.ts`.
   */
  backupDestinations: {
    title: { es: "Destinos remotos de backup", en: "Remote backup destinations" },
    description: {
      es: "Sube cada backup correcto a destinos remotos (S3, B2, SFTP/NAS, rclone). Las credenciales viven en el secret seam de los workers — nunca aquí. Edición solo System Admin.",
      en: "Uploads every successful backup to remote destinations (S3, B2, SFTP/NAS, rclone). Credentials live in the workers' secret seam — never here. Editing is System Admin only.",
    },
    cardTitle: { es: "Destinos", en: "Destinations" },
    loading: { es: "Cargando…", en: "Loading…" },
    empty: { es: "No hay destinos configurados.", en: "No destinations configured." },
    add: { es: "Añadir destino", en: "Add destination" },
    saved: { es: "Guardado.", en: "Saved." },
    save: { es: "Guardar", en: "Save" },
    saving: { es: "Guardando…", en: "Saving…" },
    typeLabel: { es: "Tipo", en: "Type" },
    nameLabel: { es: "Nombre", en: "Name" },
    enabledLabel: { es: "Habilitado", en: "Enabled" },
    disabledLabel: { es: "Deshabilitado", en: "Disabled" },
    remove: { es: "Eliminar destino", en: "Remove destination" },
    credentialsNote: {
      es: "Las credenciales no se introducen aquí: se resuelven desde el secret seam (Vault/env) en el momento de subir o probar la conexión.",
      en: "Credentials are not entered here: they are resolved from the secret seam (Vault/env) when uploading or testing the connection.",
    },
    test: { es: "Probar conexión", en: "Test connection" },
    testing: { es: "Probando…", en: "Testing…" },
    testOk: { es: "OK", en: "OK" },
    testFail: { es: "Error", en: "Error" },
    typeS3: { es: "S3 (o compatible)", en: "S3 (or compatible)" },
    typeB2: { es: "Backblaze B2", en: "Backblaze B2" },
    typeSftp: { es: "SFTP / NAS", en: "SFTP / NAS" },
    typeRclone: { es: "rclone (genérico)", en: "rclone (generic)" },
    fieldBucket: { es: "Bucket", en: "Bucket" },
    fieldPrefix: { es: "Prefijo", en: "Prefix" },
    fieldEndpointUrl: { es: "Endpoint URL", en: "Endpoint URL" },
    fieldRegion: { es: "Región", en: "Region" },
    fieldRegionB2: { es: "Región (p. ej. us-west-002)", en: "Region (e.g. us-west-002)" },
    fieldHost: { es: "Host", en: "Host" },
    fieldUsername: { es: "Usuario", en: "Username" },
    fieldPort: { es: "Puerto", en: "Port" },
    fieldRemotePath: { es: "Ruta remota", en: "Remote path" },
    fieldHostKeyPolicy: {
      es: "Política host-key (reject/auto_add/warn)",
      en: "Host-key policy (reject/auto_add/warn)",
    },
    fieldRemote: {
      es: "Remote (nombre [sección] del rclone.conf)",
      en: "Remote (section name in rclone.conf)",
    },
    fieldPath: { es: "Path", en: "Path" },
  },

  /** `app/admin/backup/restore/page.tsx` — restore completo o por tenant. */
  backupRestore: {
    title: { es: "Restaurar desde backup", en: "Restore from backup" },
    description: {
      es: "Restaura el stack completo o un único tenant desde un backup. Operación larga y destructiva: corre como job en segundo plano y exige doble confirmación. Solo System Admin.",
      en: "Restores the whole stack or a single tenant from a backup. Long and destructive: it runs as a background job and requires double confirmation. System Admin only.",
    },
    forbidden: {
      es: "Solo un System Admin puede restaurar desde un backup.",
      en: "Only a System Admin can restore from a backup.",
    },
    listTitle: { es: "Backups disponibles", en: "Available backups" },
    loading: { es: "Cargando…", en: "Loading…" },
    listEmpty: { es: "No hay backups disponibles.", en: "No backups available." },
    encrypted: { es: "cifrado", en: "encrypted" },
    previewTitle: { es: "Preview del backup", en: "Backup preview" },
    previewLoading: { es: "Cargando preview…", en: "Loading preview…" },
    previewBackup: { es: "Backup", en: "Backup" },
    previewEncrypted: { es: "Cifrado", en: "Encrypted" },
    previewCreated: { es: "Creado", en: "Created" },
    previewTotalSize: { es: "Tamaño total", en: "Total size" },
    yes: { es: "Sí", en: "Yes" },
    no: { es: "No", en: "No" },
    artifacts: { es: "Artefactos", en: "Artifacts" },
    kindLegend: { es: "Tipo de restore", en: "Restore type" },
    kindFull: {
      es: "Restore completo (detiene el stack y restaura todo)",
      en: "Full restore (stops the stack and restores everything)",
    },
    kindPerTenant: {
      es: "Restore selectivo por tenant (solo sus datos)",
      en: "Selective per-tenant restore (only its data)",
    },
    tenantIdLabel: { es: "Tenant ID (UUID)", en: "Tenant ID (UUID)" },
    tenantTables: {
      es: "Tablas afectadas (solo las filas de este tenant):",
      en: "Affected tables (only this tenant's rows):",
    },
    openConfirm: { es: "Restaurar…", en: "Restore…" },
    progressTitle: { es: "Progreso del restore", en: "Restore progress" },
    jobState: { es: "Estado:", en: "Status:" },
    jobSuccess: { es: "Restore completado.", en: "Restore completed." },
    jobFailure: { es: "El restore falló.", en: "The restore failed." },
    confirmTitle: { es: "Confirmar restore destructivo", en: "Confirm destructive restore" },
    confirmBodyPerTenant: {
      es: "Vas a sobrescribir SOLO los datos de este tenant con los del backup. El resto de tenants no se ven afectados.",
      en: "You are about to overwrite ONLY this tenant's data with the backup's. Other tenants are not affected.",
    },
    confirmBodyFull: {
      es: "Vas a DETENER el stack y reemplazar la base de datos y los volúmenes con los del backup. Esta acción es destructiva.",
      en: "You are about to STOP the stack and replace the database and volumes with the backup's. This action is destructive.",
    },
    confirmPrompt: { es: "Para confirmar, teclea exactamente:", en: "To confirm, type exactly:" },
    cancel: { es: "Cancelar", en: "Cancel" },
    confirmSubmit: { es: "Confirmar y restaurar", en: "Confirm and restore" },
    enqueuing: { es: "Encolando…", en: "Enqueuing…" },
  },

  /** `app/admin/settings/security/page.tsx` — alta y baja del segundo factor TOTP. */
  settingsSecurity: {
    title: { es: "Seguridad", en: "Security" },
    description: {
      es: "Verificación en dos pasos para tu cuenta (TOTP — Google Authenticator, 1Password, Authy…).",
      en: "Two-step verification for your account (TOTP — Google Authenticator, 1Password, Authy…).",
    },
    cardTitle: { es: "Verificación en dos pasos", en: "Two-step verification" },
    on: {
      es: "Activada. Te pediremos un código al iniciar sesión.",
      en: "Enabled. We will ask you for a code when you sign in.",
    },
    recoveryLeft: {
      es: "Códigos de recuperación sin usar: {n}",
      en: "Unused recovery codes: {n}",
    },
    disable: { es: "Desactivar", en: "Turn off" },
    off: {
      es: "No activada. Con la plataforma expuesta a internet, actívala: protege tu cuenta aunque la contraseña se filtre.",
      en: "Not enabled. With the platform exposed to the internet, turn it on: it protects your account even if your password leaks.",
    },
    enroll: { es: "Activar verificación en dos pasos", en: "Turn on two-step verification" },
    step1: {
      es: "1 · Escanea el QR con tu app de autenticación",
      en: "1 · Scan the QR code with your authenticator app",
    },
    manualKey: {
      es: "¿No puedes escanear? Introduce la clave a mano:",
      en: "Cannot scan? Enter the key by hand:",
    },
    step2: {
      es: "2 · Guarda los códigos de recuperación (solo se muestran esta vez)",
      en: "2 · Save the recovery codes (they are shown only this once)",
    },
    step3: { es: "3 · Confirma con el código de la app", en: "3 · Confirm with the app's code" },
    codeLabel: { es: "Código", en: "Code" },
    confirm: { es: "Confirmar", en: "Confirm" },
    confirmError: {
      es: "Código incorrecto — comprueba la app e inténtalo de nuevo.",
      en: "Wrong code — check the app and try again.",
    },
  },

  /** `app/admin/settings/hourly-rate/page.tsx` — tarifa del cálculo de coste humano. */
  settingsHourlyRate: {
    title: { es: "Tarifa horaria del tenant", en: "Tenant hourly rate" },
    description: {
      es: "Multiplicador que el cálculo de coste humano (planes) usa por defecto. Si lo dejas vacío, se aplica el valor por defecto de plataforma (50 EUR/h).",
      en: "Multiplier the human-cost calculation (plans) uses by default. Leave it empty and the platform default applies (50 EUR/h).",
    },
    cardTitle: { es: "Configuración", en: "Configuration" },
    loading: { es: "Cargando…", en: "Loading…" },
    rateLabel: { es: "Tarifa por hora", en: "Rate per hour" },
    currencyLabel: { es: "Moneda", en: "Currency" },
    saved: { es: "Guardado.", en: "Saved." },
    save: { es: "Guardar", en: "Save" },
  },

  /**
   * `app/admin/tenant-stats/*` — dashboard de estadísticas + explorador de runs.
   *
   * Un namespace para las cinco piezas en que `task_prod16_08` partió la
   * pantalla (cabecera, cuerpo, segmentación de coste, explorador y visuales):
   * son una sola vista para el usuario y trocear también el diccionario sólo
   * repartiría las mismas claves por más ficheros.
   */
  tenantStats: {
    title: { es: "Estadísticas", en: "Statistics" },
    description: {
      es: "Cómo rinden tus agentes y qué consume tu tenant: tasa de éxito, tiempo y coste medios, agentes top/bottom, tendencia temporal, resumen de consumo y explorador de runs. Sólo tu tenant; costes en USD.",
      en: "How your agents perform and what your tenant consumes: success rate, mean time and cost, top/bottom agents, trend over time, consumption summary and runs explorer. Your tenant only; costs in USD.",
    },
    forbidden: {
      es: "Necesitas el rol tenant_admin para ver las estadísticas del tenant.",
      en: "You need the tenant_admin role to see the tenant statistics.",
    },
    dashboardError: {
      es: "No se pudo cargar el dashboard: {detail}",
      en: "Could not load the dashboard: {detail}",
    },

    windowLabel: { es: "Ventana:", en: "Window:" },
    currencyLabel: { es: "Moneda:", en: "Currency:" },

    runs: { es: "Runs", en: "Runs" },
    successRateWindow: { es: "Tasa de éxito ({days}d)", en: "Success rate ({days}d)" },
    meanDuration: { es: "Tiempo medio", en: "Mean time" },
    meanCost: { es: "Coste medio", en: "Mean cost" },
    totalCost: { es: "Coste total", en: "Total cost" },

    trendTitle: {
      es: "Tendencia de tasa de éxito (diaria)",
      en: "Success-rate trend (daily)",
    },
    sparklineLabel: { es: "Tasa de éxito por día", en: "Success rate per day" },
    sparklineEmptyLabel: { es: "Sin runs en la ventana", en: "No runs in the window" },

    consumptionTitle: { es: "Resumen de consumo", en: "Consumption summary" },
    tokensBreakdown: { es: "Tokens (in/out/cached)", en: "Tokens (in/out/cached)" },
    costliestTitle: { es: "Run más costoso", en: "Costliest run" },
    tokensSuffix: { es: "tokens", en: "tokens" },

    segTitle: {
      es: "Segmentación de coste: IA vs Humano",
      en: "Cost breakdown: AI vs human",
    },
    segBarLabel: {
      es: "Coste IA {ai}%, coste humano {human}%",
      en: "AI cost {ai}%, human cost {human}%",
    },
    segAi: { es: "Coste IA", en: "AI cost" },
    segHuman: { es: "Coste humano", en: "Human cost" },
    segHours: { es: "{hours} h registradas", en: "{hours} h logged" },
    segNote: {
      es: "El coste IA proviene de las executions; el coste humano es tarifa × horas de las sesiones de trabajo (human_work_sessions), convertido a USD. Ambos en USD canónico.",
      en: "AI cost comes from executions; human cost is rate × hours from the work sessions (human_work_sessions), converted to USD. Both in canonical USD.",
    },

    topAgents: { es: "Agentes top (tasa de éxito)", en: "Top agents (success rate)" },
    bottomAgents: { es: "Agentes bottom (tasa de éxito)", en: "Bottom agents (success rate)" },
    noRuns: { es: "Sin runs.", en: "No runs." },
    deletedAgent: { es: "(agente eliminado)", en: "(deleted agent)" },

    byAgentTitle: { es: "Por agente", en: "By agent" },
    colAgent: { es: "Agente", en: "Agent" },
    colRole: { es: "Rol", en: "Role" },
    colSuccess: { es: "Éxito", en: "Success" },

    explorerTitle: { es: "Explorador de runs", en: "Runs explorer" },
    filterRole: { es: "Rol (ej. backend)", en: "Role (e.g. backend)" },
    filterVerdict: { es: "Verdict (ej. done)", en: "Verdict (e.g. done)" },
    filterModel: { es: "Modelo", en: "Model" },
    filterMinCost: { es: "Coste mínimo USD", en: "Min cost USD" },
    runsError: {
      es: "No se pudo cargar el explorador: {detail}",
      en: "Could not load the explorer: {detail}",
    },
    runsEmpty: { es: "Sin runs para estos filtros.", en: "No runs for these filters." },
    colTimestamp: { es: "Timestamp", en: "Timestamp" },
    colPlan: { es: "Plan", en: "Plan" },
    colTask: { es: "Tarea", en: "Task" },
    colModel: { es: "Modelo", en: "Model" },
    colDuration: { es: "Duración", en: "Duration" },
    colTokens: { es: "Tokens", en: "Tokens" },
    colCostUsd: { es: "Coste USD", en: "Cost USD" },
    colCostConverted: { es: "Coste {currency}", en: "Cost {currency}" },
    colVerdict: { es: "Verdict", en: "Verdict" },
    colRetries: { es: "Reintentos", en: "Retries" },
    convertedTitle: {
      es: "Convertido a {currency} con la tasa del {date} (1 USD = {rate} {currency})",
      en: "Converted to {currency} at the {date} rate (1 USD = {rate} {currency})",
    },
    noRateTitle: {
      es: "Sin tasa de cambio para la fecha de este run",
      en: "No exchange rate for this run's date",
    },
    prev: { es: "Anterior", en: "Previous" },
    next: { es: "Siguiente", en: "Next" },
    pageN: { es: "Página {n}", en: "Page {n}" },

    currencyNote: {
      es: "Costes almacenados en {currency} canónico. El selector de moneda convierte cada run a la tasa de cambio de su propia fecha (solo visualización; el coste USD no cambia). Los tokens cacheados se muestran como 0 hasta que el runtime capture el recuento por llamada.",
      en: "Costs are stored in canonical {currency}. The currency selector converts each run at the exchange rate of its own date (display only; the USD cost does not change). Cached tokens show as 0 until the runtime captures the per-call count.",
    },
  },

  /**
   * `app/admin/agents/*` — catálogo de agentes y hub del agente.
   *
   * Los `role` (`backend_dev`, `qa`…) y los `scope` crudos NO se traducen: son
   * los identificadores del backend y lo que aparece en la API y en los logs.
   * Lo que se traduce es la ETIQUETA de cada scope (`scope*`), que es lo que se
   * pinta en el badge de la tarjeta.
   *
   * `emptyLocal` es la única clave de este namespace cuya cara castellana está
   * clavada por un test que no se ejecuta aquí: `e2e/agents-catalog.spec.ts:60`
   * la afirma con un regex. Cambiar ese texto rompe un spec de Playwright que
   * sólo corre con el stack levantado — es decir, rompe en sitio y en momento
   * en que nadie lo va a atribuir a este cambio.
   */
  agents: {
    home: { es: "Inicio", en: "Home" },
    agents: { es: "Agentes", en: "Agents" },
    agentFallback: { es: "Agente", en: "Agent" },

    // --- catálogo ---
    catalogTitle: { es: "Catálogo de agentes", en: "Agent catalogue" },
    catalogDescription: {
      es: "Built-ins de la plataforma, plantillas de tu tenant y agentes locales de proyecto.",
      en: "Platform built-ins, your tenant's templates and project-local agents.",
    },
    newAgent: { es: "Nuevo agente", en: "New agent" },
    loading: { es: "Cargando agentes…", en: "Loading agents…" },
    loadError: { es: "No se pudieron cargar los agentes", en: "Could not load agents" },

    filterMembership: { es: "Pertenencia", en: "Membership" },
    filterAll: { es: "Todos", en: "All" },
    filterInTeam: { es: "En equipo", en: "In a team" },
    filterNoTeam: { es: "Sin equipo", en: "No team" },
    filterTeam: { es: "Equipo", en: "Team" },
    filterAllTeams: { es: "Todos los equipos", en: "All teams" },

    scopeBuiltin: { es: "Built-in", en: "Built-in" },
    scopeTenantTemplate: { es: "Plantilla del tenant", en: "Tenant template" },
    scopeProjectLocal: { es: "Local del proyecto", en: "Project-local" },
    tabTemplates: { es: "Plantillas del Tenant", en: "Tenant templates" },
    tabLocal: { es: "Locales del Proyecto", en: "Project-local" },

    emptyTitle: { es: "Sin agentes", en: "No agents" },
    emptyBuiltins: {
      es: "No hay built-ins seedeados. Corre python -m api_server.seeds.",
      en: "No built-ins seeded. Run python -m api_server.seeds.",
    },
    emptyTemplates: {
      es: "Tu tenant aún no tiene plantillas de agente propias.",
      en: "Your tenant does not have its own agent templates yet.",
    },
    // Ojo: la cara ES la afirma `e2e/agents-catalog.spec.ts` (ver cabecera).
    emptyLocal: {
      es: "No hay agentes locales de proyecto. Forkea uno desde un built-in o plantilla.",
      en: "No project-local agents yet. Fork one from a built-in or a template.",
    },

    cardRole: { es: "Rol:", en: "Role:" },
    systemPrompt: { es: "System prompt", en: "System prompt" },
    forkedFrom: { es: "Copia de otro agente", en: "Forked from another agent" },

    // --- diálogo de alta ---
    newDescription: {
      es: "Crea una plantilla del tenant (reutilizable en todos los proyectos) o un agente local de un proyecto específico.",
      en: "Create a tenant template (reusable across every project) or an agent local to one specific project.",
    },
    fieldName: { es: "Nombre", en: "Name" },
    fieldRole: { es: "Rol", en: "Role" },
    fieldDescription: { es: "Descripción", en: "Description" },
    promptEsLabel: { es: "System prompt (ES)", en: "System prompt (ES)" },
    promptEnLabel: { es: "System prompt (EN)", en: "System prompt (EN)" },
    personaModelLegend: { es: "Persona (modelo)", en: "Persona (model)" },
    personaFullLegend: { es: "Persona (modelo y prompt)", en: "Persona (model and prompt)" },
    scopeLegend: { es: "Ámbito", en: "Scope" },
    scopeTemplateOption: {
      es: "Plantilla del tenant (reutilizable)",
      en: "Tenant template (reusable)",
    },
    scopeLocalOption: {
      es: "Local de un proyecto (requiere project_id)",
      en: "Local to one project (requires project_id)",
    },
    fieldProject: { es: "Proyecto", en: "Project" },
    projectHint: {
      es: "Sólo tus proyectos del tenant — escribe para buscar entre ellos.",
      en: "Only your tenant's projects — type to search among them.",
    },
    createError: { es: "Error al crear", en: "Could not create the agent" },
    cancel: { es: "Cancelar", en: "Cancel" },
    creating: { es: "Creando…", en: "Creating…" },
    create: { es: "Crear", en: "Create" },

    // --- hub del agente ---
    fork: { es: "Personalizar (crear copia)", en: "Customize (make a copy)" },
    edit: { es: "Editar", en: "Edit" },
    remove: { es: "Borrar", en: "Delete" },
    readOnlyBadge: { es: "read-only (built-in)", en: "read-only (built-in)" },
    loadFailed: {
      es: "No se pudo cargar el agente: {detail}.",
      en: "Could not load the agent: {detail}.",
    },
    unknownError: { es: "error desconocido", en: "unknown error" },
    backToCatalog: { es: "Volver al catálogo", en: "Back to the catalogue" },
    canReview: { es: "puede revisar", en: "can review" },
    isTemplate: { es: "plantilla", en: "template" },
    memoryScope: { es: "Memory scope", en: "Memory scope" },
    maxConcurrent: { es: "Max concurrent tasks", en: "Max concurrent tasks" },

    // --- diálogo de edición ---
    editTitle: { es: "Editar agente", en: "Edit agent" },
    editDescription: {
      es: 'Los campos de scope (project_id, forked_from_agent_id) son set-once. Para crear una copia de un agente, usa la acción "Hacer copia" (fork).',
      en: 'Scope fields (project_id, forked_from_agent_id) are set-once. To create a copy of an agent, use the "Make a copy" (fork) action.',
    },
    governedByOneTeam: {
      es: "Se gestiona desde el equipo «{team}»",
      en: "Managed from the «{team}» team",
    },
    governedByTeams: {
      es: "Se gestiona desde los equipos: {teams}",
      en: "Managed from these teams: {teams}",
    },
    canReviewTasks: { es: "Puede revisar tareas", en: "Can review tasks" },
    saveError: { es: "Error al guardar", en: "Could not save" },
    saving: { es: "Guardando…", en: "Saving…" },
    save: { es: "Guardar", en: "Save" },

    // --- diálogo de borrado ---
    deleteTitle: { es: "Borrar agente", en: "Delete agent" },
    deleteWarningLead: { es: "Esta acción es", en: "This action is" },
    deleteWarningStrong: { es: "irreversible", en: "irreversible" },
    deleteWarningTail: {
      es: ". Si el agente está asignado a tareas activas, el backend rechazará el borrado con 409.",
      en: ". If the agent is assigned to active tasks, the backend rejects the deletion with a 409.",
    },
    deleteConfirmPrompt: {
      es: "Teclea el nombre del agente para confirmar:",
      en: "Type the agent's name to confirm:",
    },
    deleteError: { es: "Error al borrar", en: "Could not delete" },
    deleting: { es: "Borrando…", en: "Deleting…" },
    deleteConfirm: { es: "Borrar definitivamente", en: "Delete permanently" },

    // --- diálogo de fork ---
    forkDescriptionLead: { es: "Crea una copia editable de", en: "Creates an editable copy of" },
    forkDescriptionMid: {
      es: "en uno de tus proyectos. La copia",
      en: "in one of your projects. It",
    },
    forkDescriptionStrong: { es: "hereda", en: "inherits" },
    forkDescriptionTail: {
      es: "el conocimiento, las tools y las skills del original y es independiente: editarla no afecta al agente de origen.",
      en: "the original's knowledge, tools and skills, and it is independent: editing it does not affect the source agent.",
    },
    forkNameLabel: { es: "Nombre de la copia", en: "Name of the copy" },
    // Las dos plantillas de la sugerencia de `lib/agents/fork-name.ts`. La
    // numerada existe porque hay un índice único (tenant, proyecto, nombre)
    // sobre los agentes vivos: forkear dos veces al mismo destino con el mismo
    // nombre choca, y el backend contesta 409 sin renombrar por su cuenta.
    forkCopySuffix: { es: "{name} (copia)", en: "{name} (copy)" },
    forkCopySuffixNumbered: { es: "{name} (copia {n})", en: "{name} (copy {n})" },
    forkNameHelp: {
      es: "Sugerimos un nombre libre en el destino. Puedes cambiarlo: el nombre identifica al agente y es con el que se le elige al montar equipos y planes.",
      en: "We suggest a name that is free at the destination. You can change it: the name identifies the agent and is what you pick it by when building teams and plans.",
    },
    // El 409 de los endpoints de fork. Se prefiere este texto al `detail` del
    // backend porque la UI sabe DOS cosas que el backend no puede poner en un
    // mensaje genérico: qué nombre se intentó y que el campo para arreglarlo
    // está justo encima.
    forkConflictName: {
      es: "Ya existe un agente llamado «{name}» en el destino. Cambia el nombre de la copia y vuelve a intentarlo.",
      en: "An agent named “{name}” already exists at the destination. Change the name of the copy and try again.",
    },
    forkProjectLabel: { es: "Proyecto destino", en: "Target project" },
    forkPickProject: { es: "— Selecciona —", en: "— Select —" },
    forkNoProjects: {
      es: "No tienes proyectos creados. Crea uno primero para poder personalizar.",
      en: "You have no projects yet. Create one first so you can customize.",
    },
    // `forkError` («Error al crear la copia») se retiró al pasar el diálogo a
    // `errorText`: era el respaldo de un `mutation.error.message ?? …` que nunca
    // se daba, y su único efecto real era tapar que el mensaje que SÍ salía era
    // el cuerpo crudo del backend.
    forkSubmit: { es: "Crear copia", en: "Create copy" },

    // --- sección "Tools del agente" (`agent-tools-section.tsx`) ---
    //
    // Los labels de categoría, nivel de seguridad y tipo de implementación NO
    // están aquí: los resuelve `lib/tools/taxonomy` en formato `{labelEs,
    // labelEn}` porque el catálogo lo alimenta el backend. Esta sección los
    // elige con `pickLang`, que es la otra mitad del i18n.
    toolsTitle: { es: "Tools del agente", en: "Agent tools" },
    toolsHelp: {
      es: "Marca las tools que este agente puede usar. Sin ninguna marcada, conserva el comportamiento por defecto (sin restricción por agente).",
      en: "Tick the tools this agent may use. With none ticked it keeps the default behaviour (no per-agent restriction).",
    },
    toolsSelectedCount: { es: "{n} seleccionadas.", en: "{n} selected." },
    toolsDiagnosticTitle: {
      es: "Verificación read-only: qué tools ve cada agente del proyecto.",
      en: "Read-only check: which tools each agent of the project sees.",
    },
    toolsDiagnostic: { es: "Diagnóstico", en: "Diagnostics" },
    discard: { es: "Descartar", en: "Discard" },
    saved: { es: "Guardado", en: "Saved" },
    toolsLoadError: {
      es: "No se pudieron cargar las tools: {detail}.",
      en: "The tools could not be loaded: {detail}.",
    },
    toolsSearchPlaceholder: {
      es: "Buscar tool por nombre, descripción o categoría…",
      en: "Search tools by name, description or category…",
    },
    toolsSearchLabel: {
      es: "Buscar tool por nombre, descripción o categoría",
      en: "Search tools by name, description or category",
    },
    toolsTabBasic: { es: "Básicas", en: "Basic" },
    toolsTabAdvanced: { es: "Avanzadas", en: "Advanced" },
    toolsEmptyBasicSearch: {
      es: "Ninguna tool básica coincide con la búsqueda.",
      en: "No basic tool matches your search.",
    },
    toolsEmptyBasic: {
      es: "No hay tools básicas (de plataforma) en el catálogo.",
      en: "There are no basic (platform) tools in the catalogue.",
    },
    toolsMcpNoteLead: {
      es: "Las tools MCP se configuran a nivel de proyecto (ADR 0128) — usa la",
      en: "MCP tools are configured at project level (ADR 0128) — use the",
    },
    toolsMcpNoteStrong: { es: "sección MCP del proyecto", en: "project's MCP section" },
    toolsMcpNoteTail: {
      es: ". Aquí solo se asignan tools custom (HTTP · Python · contenedor).",
      en: ". Only custom tools (HTTP · Python · container) are assigned here.",
    },
    toolsEmptyAdvancedSearch: {
      es: "Ninguna tool avanzada coincide con la búsqueda.",
      en: "No advanced tool matches your search.",
    },
    toolsEmptyAdvancedLead: {
      es: "No hay tools custom. Créalas en el",
      en: "No custom tools. Create them in the",
    },
    toolsCatalogLink: { es: "catálogo de tools", en: "tool catalogue" },
    toolsEmptyAdvancedTail: {
      es: ". (Las tools MCP se configuran en el proyecto.)",
      en: ". (MCP tools are configured in the project.)",
    },
    toolsSelectAll: { es: "Seleccionar todas", en: "Select all" },
    toolsUnselectAll: { es: "Quitar todas", en: "Unselect all" },
    toolsSelectAllAria: {
      es: "Seleccionar todas las tools de {category}",
      en: "Select every {category} tool",
    },
    toolsUnselectAllAria: {
      es: "Quitar todas las tools de {category}",
      en: "Unselect every {category} tool",
    },
    toolSecurityAria: { es: "Seguridad: {label}. {help}", en: "Security: {label}. {help}" },
    toolImplAria: {
      es: "Implementación: {label}. {help}",
      en: "Implementation: {label}. {help}",
    },
    toolNotWiredTooltip: {
      es: "El runtime aún no puede ejecutar esta tool: el agente la vería pero fallaría al invocarla.",
      en: "The runtime cannot execute this tool yet: the agent would see it but every call would fail.",
    },
    toolNotWiredAria: { es: "No ejecutable en runtime", en: "Not runtime-wired" },
    toolNotWiredBadge: { es: "No ejecutable", en: "Not wired" },

    // --- sección "Skills del agente" (`agent-skills-section.tsx`) ---
    skillsSearchPlaceholder: {
      es: "Buscar skill por nombre, descripción o categoría…",
      en: "Search skills by name, description or category…",
    },
    skillsSearchLabel: {
      es: "Buscar skill por nombre, descripción o categoría",
      en: "Search skills by name, description or category",
    },
  },

  /**
   * `app/admin/llm-providers/*` — catálogo global de proveedores LLM (ADR 0021/0028).
   *
   * Los `kind` (Claude Agent SDK, GitHub Copilot, Azure AI Foundry, Ollama) NO
   * están aquí: son nombres de producto y viven en el `KIND_LABEL` de
   * `llm-provider-types.ts`. Tampoco los `status` que devuelve el backend
   * (`ok`, `auth_error`…): esos son identificadores, y lo que se traduce es la
   * etiqueta que se pinta por cada uno (`status*`).
   *
   * Cuidado con los secretos: ninguna clave de aquí debe llegar a contener un
   * valor de credencial. `secretConfigured` es el placeholder de un input
   * write-only — el valor real no sale nunca de Vault (ADR 0028).
   */
  llmProviders: {
    title: { es: "Proveedores LLM", en: "LLM providers" },
    description: {
      es: "Catálogo global de proveedores LLM (ADR 0021/0028). Configuración platform-global, solo System Admin. Las credenciales se guardan únicamente en Vault.",
      en: "Global catalogue of LLM providers (ADR 0021/0028). Platform-global configuration, System Admin only. Credentials are stored in Vault and nowhere else.",
    },
    forbidden: {
      es: "Esta sección es exclusiva del System Admin de la plataforma.",
      en: "This section is reserved for the platform System Admin.",
    },
    create: { es: "Nuevo proveedor", en: "New provider" },
    loading: { es: "Cargando proveedores…", en: "Loading providers…" },
    empty: {
      es: "No hay proveedores configurados. Crea el primero con «Nuevo proveedor».",
      en: "No providers configured. Create the first one with «New provider».",
    },

    colKind: { es: "Tipo", en: "Type" },
    colSlug: { es: "Slug", en: "Slug" },
    colName: { es: "Nombre", en: "Name" },
    endpoint: { es: "Endpoint", en: "Endpoint" },
    colCredential: { es: "Credencial", en: "Credential" },
    colStatus: { es: "Estado", en: "Status" },
    colConnection: { es: "Conexión", en: "Connection" },
    colActions: { es: "Acciones", en: "Actions" },

    credentialSet: { es: "configurada", en: "configured" },
    credentialUnset: { es: "sin credencial", en: "no credential" },
    active: { es: "activo", en: "active" },
    inactive: { es: "inactivo", en: "inactive" },
    activateProvider: { es: "Activar proveedor", en: "Activate provider" },
    deactivateProvider: { es: "Desactivar proveedor", en: "Deactivate provider" },

    testing: { es: "probando…", en: "testing…" },
    testConnection: { es: "Probar conexión", en: "Test connection" },
    syncModels: { es: "Sincronizar modelos", en: "Sync models" },
    syncModelsTitle: {
      es: "Sincronizar modelos (descubre /v1/models)",
      en: "Sync models (discovers /v1/models)",
    },
    syncedCount: { es: "{n} modelos sincronizados", en: "{n} models synced" },
    authorizeDeviceFlow: {
      es: "Autorizar con GitHub (Device Flow)",
      en: "Authorize with GitHub (Device Flow)",
    },
    edit: { es: "Editar", en: "Edit" },
    delete: { es: "Eliminar", en: "Delete" },

    // Etiqueta legible de cada `status` clasificado por el backend.
    statusOk: { es: "conexión OK", en: "connection OK" },
    statusAuthError: { es: "error de autenticación", en: "authentication error" },
    statusConnectionError: { es: "error de conexión", en: "connection error" },
    statusConfigError: { es: "configuración incompleta", en: "incomplete configuration" },
    statusUpstreamError: { es: "error del proveedor", en: "provider error" },

    // --- diálogo de alta / edición ---
    formCreateTitle: { es: "Nuevo proveedor", en: "New provider" },
    formEditTitle: { es: "Editar proveedor", en: "Edit provider" },
    fieldSlug: { es: "Slug (único)", en: "Slug (unique)" },
    slugHintLead: {
      es: "Handle único para distinguir proveedores del mismo tipo (p. ej.",
      en: "Unique handle to tell apart providers of the same kind (e.g.",
    },
    slugHintTail: {
      es: "). Minúsculas, números y guiones.",
      en: "). Lowercase letters, digits and hyphens.",
    },
    endpointApim: { es: "Endpoint APIM (gateway)", en: "APIM endpoint (gateway)" },
    endpointOllama: { es: "Endpoint Ollama", en: "Ollama endpoint" },
    claudeAuthMode: { es: "Modo de autenticación", en: "Authentication mode" },
    claudeApiKeyOption: { es: "API key (Anthropic)", en: "API key (Anthropic)" },
    claudeSubscriptionOption: {
      es: "Suscripción Pro/Max (claude setup-token)",
      en: "Pro/Max subscription (claude setup-token)",
    },
    claudeApiKeyLabel: { es: "API key (sk-ant-…)", en: "API key (sk-ant-…)" },
    claudeSubscriptionLabel: {
      es: "Token de suscripción (de «claude setup-token»)",
      en: "Subscription token (from «claude setup-token»)",
    },
    copilotTokenLabel: {
      es: "Token OAuth (o usa el Device Flow desde la lista)",
      en: "OAuth token (or use the Device Flow from the list)",
    },
    azureApiKeyLabel: {
      es: "API key (subscription APIM)",
      en: "API key (subscription APIM)",
    },
    ollamaBearerLabel: {
      es: "Bearer token (Ollama Cloud, opcional)",
      en: "Bearer token (Ollama Cloud, optional)",
    },
    secretConfigured: { es: "•••••••• (configurado)", en: "•••••••• (configured)" },
    credentialHintKeep: {
      es: "Hay una credencial configurada. Déjalo vacío para conservarla; escribe un valor para rotarla.",
      en: "A credential is configured. Leave it empty to keep it; type a value to rotate it.",
    },
    credentialHintNone: {
      es: "No hay credencial configurada. Escribe un valor para guardarla en Vault.",
      en: "No credential configured. Type a value to store it in Vault.",
    },
    credentialHintCreate: {
      es: "Se guardará únicamente en Vault (nunca en la base de datos ni en respuestas de la API).",
      en: "It will be stored in Vault only (never in the database nor in API responses).",
    },
    fieldActive: { es: "Proveedor activo", en: "Provider active" },
    cancel: { es: "Cancelar", en: "Cancel" },
    save: { es: "Guardar", en: "Save" },
    saving: { es: "Guardando…", en: "Saving…" },
    submitCreate: { es: "Crear", en: "Create" },

    // --- diálogo del Device Flow de Copilot ---
    deviceFlowTitle: {
      es: "Autorizar GitHub Copilot — {name}",
      en: "Authorize GitHub Copilot — {name}",
    },
    deviceFlowIntro: {
      es: "Inicia el Device Flow de GitHub: te mostraremos un código y un enlace. Tras autorizar en GitHub, el token se acuña y se guarda únicamente en Vault — nunca aparece aquí.",
      en: "Start GitHub's Device Flow: we will show you a code and a link. Once you authorize on GitHub the token is minted and stored in Vault only — it never appears here.",
    },
    deviceFlowStart: { es: "Iniciar Device Flow", en: "Start Device Flow" },
    deviceFlowStarting: { es: "Iniciando…", en: "Starting…" },
    deviceFlowUserCode: { es: "Código de usuario", en: "User code" },
    deviceFlowOpen: { es: "Abrir {uri}", en: "Open {uri}" },
    deviceFlowWaiting: {
      es: "Esperando autorización en GitHub…",
      en: "Waiting for authorization on GitHub…",
    },
    deviceFlowSlowDown: {
      es: "(GitHub pidió esperar más)",
      en: "(GitHub asked us to wait longer)",
    },
    deviceFlowAuthorized: {
      es: "Autorizado. El token de Copilot se guardó en Vault para este proveedor.",
      en: "Authorized. The Copilot token was stored in Vault for this provider.",
    },
    deviceFlowExpired: {
      es: "El código expiró. Vuelve a iniciar el Device Flow.",
      en: "The code expired. Start the Device Flow again.",
    },
    deviceFlowDenied: {
      es: "La autorización fue denegada en GitHub.",
      en: "Authorization was denied on GitHub.",
    },
    deviceFlowDone: { es: "Hecho", en: "Done" },
    deviceFlowRetry: { es: "Reintentar", en: "Retry" },
  },

  /**
   * `app/admin/model-prices/*` — catálogo global de precios (USD canónico).
   *
   * Los `source` (`manual`, `litellm`…) y las `modality` NO se traducen: son
   * los valores del enum del backend y lo que se guarda. Sí se traducen la
   * UNIDAD (`unit*`) y el estado del diff del sync (`diffStatus*`), que son
   * etiquetas de presentación.
   *
   * "Provider" aparece con dos sentidos distintos y a propósito con dos claves:
   * `colFamily` es la FAMILIA del feed de LiteLLM (`anthropic`) y `colProvider`
   * es el proveedor LLM de plataforma configurado (ADR 0028). Confundirlos fue
   * lo que hizo falta aclarar en la propia UI con "(provider)" y "(plataforma)".
   */
  modelPrices: {
    title: { es: "Modelos & Precios", en: "Models & Prices" },
    description: {
      es: "Catálogo global de precios de modelos (USD canónico, con soporte de prompt caching). Lectura abierta; edición solo System Admin.",
      en: "Global model price catalogue (canonical USD, with prompt-caching support). Anyone may read it; only a System Admin may edit it.",
    },
    syncOpen: { es: "Sincronizar precios", en: "Sync prices" },
    create: { es: "Nuevo precio", en: "New price" },

    // --- aviso de alcance del sync (task_psa_02) ---
    scopeLead: { es: "Sincronizando solo:", en: "Syncing only:" },
    scopeTail: {
      es: "(familias de los proveedores LLM activos — ADR 0028). El resto del feed se omite.",
      en: "(families of the active LLM providers — ADR 0028). The rest of the feed is skipped.",
    },
    scopeEmptyLead: {
      es: "No hay proveedores LLM activos; nada que sincronizar. Activa al menos un proveedor en",
      en: "There are no active LLM providers; nothing to sync. Enable at least one provider in",
    },
    scopeEmptyTail: {
      es: "para que el sync de precios traiga sus familias.",
      en: "so the price sync brings in its families.",
    },

    // --- filtros ---
    filterFamily: { es: "Familia (provider)", en: "Family (provider)" },
    filterModel: { es: "Modelo", en: "Model" },
    filterModality: { es: "Modalidad", en: "Modality" },
    filterAllModalities: { es: "Todas", en: "All" },
    filterProvider: { es: "Proveedor (plataforma)", en: "Provider (platform)" },
    filterAllProviders: { es: "Todos", en: "All" },
    filterCurrentOnly: { es: "Solo vigentes", en: "Current only" },
    filterApply: { es: "Filtrar", en: "Filter" },
    filterReset: { es: "Limpiar", en: "Clear" },

    // --- tabla ---
    loading: { es: "Cargando catálogo…", en: "Loading the catalogue…" },
    loadError: { es: "No se pudo cargar el catálogo", en: "The catalogue could not be loaded" },
    emptyTitle: { es: "Catálogo vacío", en: "Empty catalogue" },
    emptyDescription: {
      es: "El catálogo está vacío para estos filtros.",
      en: "The catalogue is empty for these filters.",
    },
    colFamily: { es: "Familia", en: "Family" },
    colModel: { es: "Modelo", en: "Model" },
    colModality: { es: "Modalidad", en: "Modality" },
    colProvider: { es: "Proveedor", en: "Provider" },
    colInput: { es: "Input", en: "Input" },
    colOutput: { es: "Output", en: "Output" },
    colCache: { es: "Cache", en: "Cache" },
    colUnit: { es: "Unidad", en: "Unit" },
    colSource: { es: "Fuente", en: "Source" },
    colValidity: { es: "Vigencia", en: "Validity" },
    colActions: { es: "Acciones", en: "Actions" },
    unlinked: { es: "sin asociar", en: "unlinked" },
    current: { es: "vigente", en: "current" },
    history: { es: "Histórico", en: "History" },
    edit: { es: "Editar", en: "Edit" },
    supersede: { es: "Superseder", en: "Supersede" },

    // --- diálogo de alta / edición ---
    formCreateTitle: { es: "Nuevo precio", en: "New price" },
    formEditTitle: { es: "Editar precio", en: "Edit price" },
    formUsdNote: {
      es: "Precios en USD canónico, {unit}. El precio de caché (prompt caching) es opcional; si se omite, el sistema usa ~10% del input.",
      en: "Prices in canonical USD, {unit}. The cache price (prompt caching) is optional; when omitted the system uses ~10% of the input price.",
    },
    fieldProvider: { es: "Provider", en: "Provider" },
    fieldInput: { es: "Input (USD)", en: "Input (USD)" },
    fieldOutput: { es: "Output (USD)", en: "Output (USD)" },
    fieldCached: { es: "Cache input (USD, opcional)", en: "Cache input (USD, optional)" },
    fieldContextWindow: { es: "Context window", en: "Context window" },
    cancel: { es: "Cancelar", en: "Cancel" },
    saving: { es: "Guardando…", en: "Saving…" },
    save: { es: "Guardar", en: "Save" },
    submitCreate: { es: "Crear", en: "Create" },

    // --- diálogo del sync ---
    syncTitle: { es: "Sincronizar precios (LiteLLM)", en: "Sync prices (LiteLLM)" },
    syncFeedNote: {
      es: "Lee el JSON público de precios de LiteLLM como fuente de datos (no como runtime — ADR 0021). Esta es una previsualización: nada se escribe hasta que confirmes. Una subida de precio >10% exige confirmación explícita.",
      en: "Reads LiteLLM's public price JSON as a data feed (never as a runtime — ADR 0021). This is a preview: nothing is written until you confirm. A price rise above 10% requires an explicit confirmation.",
    },
    syncDialogScopeTail: {
      es: "(familias de los proveedores LLM activos). El resto del feed se omite.",
      en: "(families of the active LLM providers). The rest of the feed is skipped.",
    },
    syncDialogScopeEmpty: {
      es: "No hay proveedores LLM activos; el sync no traerá nada.",
      en: "There are no active LLM providers; the sync will bring in nothing.",
    },
    syncCalculating: { es: "Calculando diff…", en: "Computing the diff…" },
    syncAdded: { es: "{n} nuevos", en: "{n} new" },
    syncUpdated: { es: "{n} actualizados", en: "{n} updated" },
    syncIncreased: { es: "{n} subidas >10%", en: "{n} rises >10%" },
    syncRemoved: { es: "{n} descontinuados", en: "{n} discontinued" },
    syncUnchanged: { es: "{n} sin cambios", en: "{n} unchanged" },
    syncSkippedFamily: {
      es: "{n} fuera de familias activas",
      en: "{n} outside the active families",
    },
    syncColModel: { es: "Modelo", en: "Model" },
    syncColStatus: { es: "Estado", en: "Status" },
    syncColInput: { es: "Input (ant. → nuevo)", en: "Input (old → new)" },
    syncColOutput: { es: "Output (ant. → nuevo)", en: "Output (old → new)" },
    syncManualSkipped: { es: "(manual, no se pisa)", en: "(manual, not overwritten)" },
    syncNoChanges: {
      es: "El catálogo ya está al día — nada que aplicar.",
      en: "The catalogue is already up to date — nothing to apply.",
    },
    syncConfirmWarning: {
      es: "Hay {n} subida(s) de precio superior(es) al 10%. Revisa los cambios y confirma explícitamente para aplicarlos.",
      en: "There are {n} price rise(s) above 10%. Review the changes and confirm explicitly to apply them.",
    },
    syncConfirmCheckbox: {
      es: "Confirmo que he revisado las subidas >10% y deseo aplicarlas.",
      en: "I confirm I have reviewed the rises above 10% and want to apply them.",
    },
    syncApplying: { es: "Aplicando…", en: "Applying…" },
    syncApply: { es: "Aplicar cambios", en: "Apply changes" },

    // Etiqueta de cada `status` del diff (el status en sí es del backend).
    diffStatusAdded: { es: "nuevo", en: "new" },
    diffStatusUpdated: { es: "actualizado", en: "updated" },
    diffStatusIncreased: { es: "subida >10%", en: "rise >10%" },
    diffStatusRemoved: { es: "descontinuado", en: "discontinued" },
    diffStatusUnchanged: { es: "sin cambios", en: "unchanged" },

    // Etiqueta de cada `unit` (el valor en sí es del backend).
    unitPer1mTokens: { es: "por 1M tokens", en: "per 1M tokens" },
    unitPer1kTokens: { es: "por 1K tokens", en: "per 1K tokens" },
    unitPerRequest: { es: "por petición", en: "per request" },
    unitPerImage: { es: "por imagen", en: "per image" },
    unitPerSecond: { es: "por segundo", en: "per second" },
    unitPerMinute: { es: "por minuto", en: "per minute" },

    // --- diálogo del histórico ---
    historyTitle: { es: "Histórico de precios", en: "Price history" },
    historyEmpty: {
      es: "Sin historial de precios para esta clave.",
      en: "No price history for this key.",
    },
    historyFrom: { es: "Desde", en: "From" },
    historyTo: { es: "Hasta", en: "To" },
    historyChartNote: {
      es: "(USD, precio-en-el-tiempo)",
      en: "(USD, price over time)",
    },
    historyChartLabel: {
      es: "Gráfica de precio en el tiempo",
      en: "Price-over-time chart",
    },
  },

  /**
   * `app/admin/knowledge-bases/` — la pantalla y sus cuatro diálogos, más el
   * panel de documentos que cuelga de cada fila (prod-16 `task_prod16_04`).
   *
   * Es el namespace más grande del diccionario por una razón concreta: el guard
   * `check-i18n.mjs` sólo marcaba **3 atributos** en este módulo, y eso lo hacía
   * parecer un lote pequeño. Los 3 eran la punta de ~2.100 líneas de castellano
   * cableado en cinco ficheros. La mitad del texto vive en superficies plegadas
   * (los diálogos, el panel de documentos), que es justo donde un guard basado
   * en atributos no llega y donde un `useT()` olvidado no se ve hasta que
   * alguien despliega una fila en producción.
   *
   * "Grant" se queda en inglés en los dos idiomas a propósito: es el término
   * con el que el backend, la API y el resto del panel nombran la concesión de
   * acceso (`/knowledge-bases/{id}/projects`), y traducirlo sólo en esta
   * pantalla rompería la correspondencia con lo que el operador lee en los logs.
   */
  knowledgeBases: {
    home: { es: "Inicio", en: "Home" },
    title: { es: "Knowledge Bases", en: "Knowledge Bases" },
    description: {
      es: "Bases de conocimiento del tenant. Cada KB agrupa documentos indexados y se asigna (grant) a uno o más proyectos.",
      en: "Knowledge bases for this tenant. Each KB groups indexed documents and is granted to one or more projects.",
    },
    categoriesLink: { es: "Categorías", en: "Categories" },
    createButton: { es: "Crear KB", en: "New KB" },
    loading: { es: "Cargando KBs…", en: "Loading KBs…" },
    errorTitle: { es: "Error", en: "Error" },
    empty: {
      es: "Aún no hay KBs en este tenant. Crea la primera para empezar a indexar documentos.",
      en: "No KBs in this tenant yet. Create the first one to start indexing documents.",
    },
    uncategorized: { es: "Sin categoría", en: "Uncategorized" },

    // --- fila de KB ------------------------------------------------------
    builtinBadge: { es: "Built-in", en: "Built-in" },
    assignments: { es: "Asignaciones", en: "Assignments" },
    assignmentsTitle: {
      es: "Ver qué proyectos y agentes tienen grant",
      en: "See which projects and agents have a grant",
    },
    grant: { es: "Grant", en: "Grant" },
    grantTitle: { es: "Dar acceso a un proyecto", en: "Give a project access" },

    // --- selector de categoría (compartido por alta y edición) ------------
    noCategoryOption: { es: "— Sin categoría —", en: "— Uncategorized —" },
    categoryGroupBuiltin: { es: "Built-in", en: "Built-in" },
    categoryGroupTenant: { es: "Tenant", en: "Tenant" },
    newCategoryTitle: { es: "Crear categoría nueva", en: "Create a new category" },

    // --- alta ------------------------------------------------------------
    createTitle: { es: "Crear Knowledge Base", en: "Create knowledge base" },
    createDescription: {
      es: "Una KB es un contenedor de documentos indexados. Tras crearla, despliégala en esta misma lista para subir documentos, y dale acceso (grant) a los proyectos o agentes que la consumirán.",
      en: "A KB is a container of indexed documents. Once created, expand it in this same list to upload documents, and grant access to the projects or agents that will use it.",
    },
    nameLabel: { es: "Nombre", en: "Name" },
    categoryLabel: { es: "Categoría", en: "Category" },
    categoryHelp: {
      es: "Las categorías ayudan a organizar el listado. Opcional.",
      en: "Categories help organize the list. Optional.",
    },
    descriptionLabel: { es: "Descripción", en: "Description" },
    cancel: { es: "Cancelar", en: "Cancel" },
    creating: { es: "Creando…", en: "Creating…" },
    createSubmit: { es: "Crear KB", en: "Create KB" },
    createError: { es: "Error al crear", en: "Could not create it" },

    // --- edición ---------------------------------------------------------
    editTitle: { es: "Editar Knowledge Base", en: "Edit knowledge base" },
    embeddingLabel: { es: "Modelo de embedding", en: "Embedding model" },
    // ADR 0155: el texto anterior («el modelo es fijo por KB») describía un
    // selector por KB que el código nunca honró. Lo que es fijo es el modelo de
    // la PLATAFORMA; esto es el sello de con cuál se generaron estos vectores.
    embeddingHelp: {
      es: "La plataforma indexa con un único modelo. Este es el modelo con el que se generaron los vectores de esta KB.",
      en: "The platform indexes with a single model. This is the model that produced this KB's vectors.",
    },
    embeddingStale: { es: "Reindexado pendiente", en: "Reindex pending" },
    embeddingStaleHelp: {
      es: "Esta KB se indexó con {stamp} y la plataforma usa ahora {active}. Sus vectores no compiten en la búsqueda vectorial y no admite documentos nuevos hasta reindexarla.",
      en: "This KB was indexed with {stamp} and the platform now uses {active}. Its vectors do not take part in vector search and it will not accept new documents until you reindex it.",
    },
    save: { es: "Guardar", en: "Save" },
    saving: { es: "Guardando…", en: "Saving…" },
    saveError: { es: "Error al guardar", en: "Could not save it" },

    // --- mini-diálogo inline de categoría nueva ---------------------------
    inlineCatTitle: { es: "Nueva categoría", en: "New category" },
    inlineCatDescription: {
      es: "Crea una categoría para organizar tus KBs. El slug es el identificador estable que se usa en filtros y URLs.",
      en: "Create a category to organize your KBs. The slug is the stable identifier used in filters and URLs.",
    },
    slugLabel: { es: "Slug", en: "Slug" },
    slugPlaceholder: { es: "ej. compliance-pci", en: "e.g. compliance-pci" },
    catNamePlaceholder: { es: "ej. Compliance PCI-DSS", en: "e.g. Compliance PCI-DSS" },
    colorLabel: { es: "Color", en: "Color" },
    inlineCatSubmit: { es: "Crear", en: "Create" },
    inlineCatError: { es: "Error al crear la categoría", en: "Could not create the category" },

    // --- borrado con confirmación por nombre ------------------------------
    deleteTitle: { es: "Borrar Knowledge Base", en: "Delete knowledge base" },
    deleteDescriptionPre: {
      es: "Borra la KB, todos sus documentos indexados y los grants a proyectos. La acción es ",
      en: "Deletes the KB, all its indexed documents and its project grants. The action is ",
    },
    deleteDescriptionStrong: { es: "irreversible", en: "irreversible" },
    deleteDescriptionPost: {
      es: ". Los documentos en MinIO no se tocan.",
      en: ". The documents in MinIO are left untouched.",
    },
    deleteConfirmPrompt: {
      es: "Para confirmar, teclea el nombre de la KB:",
      en: "To confirm, type the KB name:",
    },
    deleting: { es: "Borrando…", en: "Deleting…" },
    deleteSubmit: { es: "Borrar definitivamente", en: "Delete permanently" },
    deleteError: { es: "Error al borrar", en: "Could not delete it" },

    // --- grant a proyecto -------------------------------------------------
    grantDialogTitle: { es: "Dar acceso a un proyecto", en: "Give a project access" },
    grantDescription: {
      es: 'Después del grant, el proyecto verá esta KB en su sub-sección "Knowledge Bases" y podrá subir documentos. Puedes hacer grant a varios proyectos repitiendo esta acción.',
      en: 'After the grant, the project will see this KB in its "Knowledge Bases" sub-section and will be able to upload documents. You can grant several projects by repeating this action.',
    },
    grantKbPrefix: { es: "KB:", en: "KB:" },
    grantProjectLabel: { es: "Proyecto destino", en: "Target project" },
    grantSuccessNamed: {
      es: 'Acceso otorgado a "{name}".',
      en: 'Access granted to "{name}".',
    },
    grantSuccess: {
      es: "Acceso otorgado al proyecto.",
      en: "Access granted to the project.",
    },
    grantError: { es: "Error al otorgar acceso", en: "Could not grant access" },
    granting: { es: "Otorgando…", en: "Granting…" },
    grantSubmit: { es: "Otorgar acceso", en: "Grant access" },
    close: { es: "Cerrar", en: "Close" },

    // --- panel de documentos ---------------------------------------------
    docsTitle: { es: "Documentos ({n})", en: "Documents ({n})" },
    docsUpload: { es: "Subir documento", en: "Upload document" },
    docsLoading: { es: "Cargando documentos…", en: "Loading documents…" },
    docsEmpty: {
      es: "Esta KB aún no tiene documentos. Sube el primero para indexarlo.",
      en: "This KB has no documents yet. Upload the first one to index it.",
    },
    // Lenguaje de persona, no jerga del pipeline (KB Q6): el estado técnico
    // sigue en `data-status`, que es lo que consultan los e2e.
    docStatusPending: { es: "Procesando…", en: "Processing…" },
    docStatusProcessing: { es: "Procesando…", en: "Processing…" },
    docStatusIndexed: { es: "Listo", en: "Ready" },
    docStatusIndexedEmpty: { es: "Sin contenido aprovechable", en: "No usable content" },
    docStatusFailed: { es: "Error", en: "Error" },
    docEmptyHint: {
      es: "Procesado pero sin fragmentos (0 chunks): el agente no puede recuperar nada de este documento. Sube un original con texto seleccionable o reindexa.",
      en: "Processed but with no fragments (0 chunks): the agent cannot retrieve anything from this document. Upload an original with selectable text, or reindex it.",
    },
    docProgress: { es: "Progreso", en: "Progress" },
    docReindexTitle: {
      es: "Reindexar (vuelve a procesar el documento)",
      en: "Reindex (processes the document again)",
    },
    docDelete: { es: "Eliminar", en: "Delete" },
    uploadTitle: { es: "Subir documento a la KB", en: "Upload a document to the KB" },
    uploadFileLabel: { es: "Archivo", en: "File" },
    uploadTitleLabel: { es: "Título (opcional)", en: "Title (optional)" },
    uploadTitlePlaceholder: {
      es: "Por defecto: nombre del archivo",
      en: "Defaults to the file name",
    },
    uploading: { es: "Subiendo…", en: "Uploading…" },
    uploadSubmit: { es: "Subir", en: "Upload" },

    // --- diálogo de asignaciones -----------------------------------------
    assignmentsDialogTitle: { es: "Asignaciones — {name}", en: "Assignments — {name}" },
    assignmentsEmpty: {
      es: "Esta KB no está granteada a ningún proyecto ni agente todavía. Añade un grant aquí debajo.",
      en: "This KB is not granted to any project or agent yet. Add a grant below.",
    },
    assignmentsProjects: { es: "Proyectos", en: "Projects" },
    assignmentsAgents: { es: "Agentes", en: "Agents" },
    grantToProject: { es: "Conceder a proyecto", en: "Grant to a project" },
    chooseProject: { es: "Elige un proyecto…", en: "Choose a project…" },
    grantAction: { es: "Conceder", en: "Grant" },
    advancedAgentGrant: {
      es: "Avanzado: conceder a un agente concreto",
      en: "Advanced: grant to a specific agent",
    },
    chooseAgent: { es: "Elige un agente…", en: "Choose an agent…" },
  },

  /**
   * `app/admin/knowledge-bases/categories/` — las categorías con que se agrupan
   * las KBs (prod-16 `task_prod16_04`).
   *
   * Namespace propio y no claves dentro de `knowledgeBases` porque es una
   * pantalla distinta con su propio CRUD; compartir namespace obligaría a
   * prefijar cada clave (`catCreateTitle`, `catDeleteTitle`…) para no chocar con
   * las de la KB, que es la señal de que son dos cosas.
   */
  kbCategories: {
    home: { es: "Inicio", en: "Home" },
    kbsCrumb: { es: "Knowledge Bases", en: "Knowledge Bases" },
    crumb: { es: "Categorías", en: "Categories" },
    title: { es: "Categorías de KBs", en: "KB categories" },
    description: {
      es: "Organiza tus knowledge bases en grupos. Las built-in vienen sembradas por la plataforma y son comunes a todos los tenants.",
      en: "Organize your knowledge bases into groups. The built-in ones are seeded by the platform and shared by every tenant.",
    },
    createButton: { es: "Nueva categoría", en: "New category" },
    loading: { es: "Cargando categorías…", en: "Loading categories…" },
    errorTitle: { es: "Error", en: "Error" },
    builtinSection: { es: "Built-in ({n})", en: "Built-in ({n})" },
    tenantSection: { es: "Tenant ({n})", en: "Tenant ({n})" },
    builtinBadge: { es: "Built-in", en: "Built-in" },
    emptyCustom: {
      es: "No has creado categorías propias todavía. Usa las built-in o crea una nueva.",
      en: "You have not created any categories of your own yet. Use the built-in ones or create a new one.",
    },

    // --- alta -------------------------------------------------------------
    createTitle: { es: "Nueva categoría", en: "New category" },
    createDescription: {
      es: "El slug es el identificador estable que se usa en filtros y URLs (sólo a-z, 0-9, `_`, `-`). El nombre es el texto que se muestra en la UI.",
      en: "The slug is the stable identifier used in filters and URLs (only a-z, 0-9, `_`, `-`). The name is the text shown in the UI.",
    },
    slugLabel: { es: "Slug", en: "Slug" },
    slugPlaceholder: { es: "ej. compliance-pci", en: "e.g. compliance-pci" },
    nameLabel: { es: "Nombre", en: "Name" },
    namePlaceholder: { es: "ej. Compliance PCI-DSS", en: "e.g. Compliance PCI-DSS" },
    colorLabel: { es: "Color", en: "Color" },
    cancel: { es: "Cancelar", en: "Cancel" },
    creating: { es: "Creando…", en: "Creating…" },
    createSubmit: { es: "Crear categoría", en: "Create category" },
    createError: { es: "Error al crear", en: "Could not create it" },

    // --- edición ----------------------------------------------------------
    editTitle: { es: "Editar categoría", en: "Edit category" },
    editDescription: {
      es: "El slug no se puede cambiar — está sembrado en filtros y posibles integraciones.",
      en: "The slug cannot be changed — it is baked into filters and possible integrations.",
    },
    save: { es: "Guardar", en: "Save" },
    saving: { es: "Guardando…", en: "Saving…" },
    saveError: { es: "Error al guardar", en: "Could not save it" },

    // --- borrado ----------------------------------------------------------
    deleteTitle: { es: "Borrar categoría", en: "Delete category" },
    deleteDescription: {
      es: "Las KBs que pertenecían a esta categoría quedarán sin categoría (no se borran).",
      en: "The KBs that belonged to this category will be left without a category (they are not deleted).",
    },
    // El nombre va en <strong> y el slug en <code>, así que la frase se parte
    // en vez de interpolarse: una traducción no puede llevar marcado dentro.
    deleteConfirmPre: {
      es: "¿Borrar la categoría ",
      en: "Delete the category ",
    },
    deleting: { es: "Borrando…", en: "Deleting…" },
    deleteSubmit: { es: "Borrar", en: "Delete" },
    deleteError: { es: "Error al borrar", en: "Could not delete it" },
  },

  /**
   * `lib/memory/honesty.ts` — los textos de "no disponible aún" del subsistema
   * de memoria (Plan 06.17 `task_06_17_06`).
   *
   * Vive en el diccionario aunque su llamante NO sea un componente: `translate`
   * es una función pura que recibe el idioma, así que un módulo de lógica sin
   * React puede usarla igual. Eso es lo que permitió retirar de aquí los
   * ternarios sin cambiar la firma pública de `memoryDetectorState(…, lang)`.
   */
  memoryHonesty: {
    unavailable: { es: "No disponible aún", en: "Not available yet" },
    detectorNote: {
      es: "Ninguna memoria tiene aún embedding (el back-fill no ha corrido o el embebedor está caído), así que la similitud y el umbral no operan todavía.",
      en: "No memory has an embedding yet (the back-fill has not run or the embedder is down), so similarity and the threshold do not operate yet.",
    },
    privateScopeWarning: {
      es: "Con scope «privada», el agente IA no memoriza nada entre ejecuciones: el Memorizer omite estos runs (skip_private). Elige otro scope si quieres que recuerde.",
      en: "With «private» scope, the AI agent does not memorize anything across runs: the Memorizer skips these runs (skip_private). Pick another scope if you want it to remember.",
    },
    placeholderField: {
      es: "Campo placeholder: no está cableado todavía y no tiene efecto.",
      en: "Placeholder field: not wired yet, no effect.",
    },
  },

  /**
   * `lib/capability/hub.ts` — los badges de estado de las cuatro secciones del
   * Hub de capacidades (Saber / Recordar / Ser / Hacer, ADR 0054/0055).
   *
   * Las formas con `{n}` llevan clave singular y plural separadas: el castellano
   * y el inglés no pluralizan igual ("2 KBs asignadas" vs "2 KBs assigned") y
   * derivarlo con una regla común acabaría en "1 memorias".
   */
  capabilityHub: {
    saberEmpty: { es: "Sin conocimiento asignado", en: "No knowledge assigned" },
    saberOne: { es: "{n} KB asignada", en: "{n} KB assigned" },
    saberMany: { es: "{n} KBs asignadas", en: "{n} KBs assigned" },
    recordarPrivate: { es: "Privada: no memoriza", en: "Private: not remembering" },
    recordarEmpty: { es: "Sin memoria todavía", en: "No memory yet" },
    recordarNoProject: { es: "{n} en memoria · sin proyecto", en: "{n} in memory · no project" },
    recordarOne: { es: "{n} memoria", en: "{n} memory" },
    recordarMany: { es: "{n} memorias", en: "{n} memories" },
    serNotApplicable: { es: "No aplica", en: "Not applicable" },
    serUnconfigured: { es: "Modelo no configurado", en: "Model not configured" },
    serConfigured: { es: "Modelo configurado", en: "Model configured" },
    hacerUnrestricted: { es: "Sin restricción por agente", en: "No per-agent restriction" },
    hacerEmpty: { es: "Sin acciones efectivas", en: "No effective actions" },
    hacerOne: { es: "{n} acción efectiva", en: "{n} effective action" },
    hacerMany: { es: "{n} acciones efectivas", en: "{n} effective actions" },
  },

  /**
   * Los COMPONENTES de `components/capability/*` (plan prod-16,
   * `task_prod16_04`): el hub SABER/RECORDAR/SER/HACER, la sección Persona, el
   * selector proveedor/modelo/temperatura y la tarjeta del modelo de chat.
   *
   * Namespace aparte de `capabilityHub` a propósito: aquél traduce los RESÚMENES
   * que calcula `lib/capability/hub.ts` (lógica pura, testeable sin React); éste,
   * el chrome de los componentes. Mezclarlos obligaría al módulo puro a arrastrar
   * claves que no usa.
   *
   * Los cuatro ficheros comparten los mismos cuatro campos de modelo
   * (`fieldProvider`, `fieldModel`, `fieldTemperature`, `fieldReasoning`) porque
   * son literalmente el mismo campo pintado en tres sitios. Antes de migrar
   * estaban escritos tres veces: `provider-model-selects.tsx` y
   * `chat-model-section.tsx` con un `const t = (es, en) => …` local —un
   * diccionario privado por fichero, que es el patrón que este plan retira— y
   * `capability-hub.tsx` con ternarios sueltos.
   */
  capability: {
    // --- campos de modelo, compartidos por los tres controles ---------------
    fieldProvider: { es: "Proveedor", en: "Provider" },
    fieldModel: { es: "Modelo", en: "Model" },
    fieldTemperature: { es: "Temperatura", en: "Temperature" },
    fieldReasoning: { es: "Razonamiento", en: "Reasoning" },
    fieldSelectPlaceholder: { es: "— Selecciona —", en: "— Select —" },
    fieldModelNamePlaceholder: { es: "nombre del modelo", en: "model name" },
    reasoningOff: { es: "Desactivado", en: "Off" },
    temperatureNotApplicable: {
      es: "No aplica a Claude (el SDK no la expone)",
      en: "Not applicable to Claude (the SDK does not expose it)",
    },

    // --- hub ----------------------------------------------------------------
    hubDescription: {
      es: "Las cuatro vías de capacidad y su estado real. Asigna desde cada sección.",
      en: "The four capability paths and their real state. Assign from each section.",
    },
    hubLoadError: {
      es: "No se pudo cargar la capacidad",
      en: "Could not load capability",
    },
    serModelOrigin: { es: "Origen del modelo", en: "Model origin" },
    hacerUnrestrictedDetail: {
      es: "Este nivel no restringe tools por agente; el set efectivo lo fija cada agente.",
      en: "This level does not restrict tools per agent; the effective set is set by each agent.",
    },
    checklistTitle: { es: "Pasos para capacitar", en: "Steps to enable" },

    // --- tarjeta del modelo de chat ----------------------------------------
    chatModelTitle: { es: "Modelo del chat", en: "Chat model" },
    chatModelDescription: {
      es:
        "El proveedor y modelo con el que el equipo RESPONDE en el chat de " +
        "planificación. Vacío = usa el modelo de ejecución. Un proveedor no-agéntico " +
        "(Ollama/Azure) hace el chat más rápido que claude_sdk.",
      en:
        "The provider + model the team REPLIES with in the planning chat. Empty = use " +
        "the execution model. A non-agentic provider (Ollama/Azure) makes the chat " +
        "faster than claude_sdk.",
    },
    chatModelInheritsReadonly: {
      es: "Hereda el modelo de ejecución.",
      en: "Inherits the execution model.",
    },
    chatModelInherit: {
      es: "Heredar el modelo de ejecución",
      en: "Inherit the execution model",
    },
    saving: { es: "Guardando…", en: "Saving…" },
    /** El título de la tarjeta hace de etiqueta del botón para que no diverjan. */
    saveTitled: { es: "Guardar {title}", en: "Save {title}" },

    // --- sección Persona (SER) ---------------------------------------------
    personaTitle: { es: "SER · Persona", en: "BE · Persona" },
    personaDescription: {
      es: "Quién es el agente: proveedor, modelo, temperatura y el prompt efectivo (rol + modo).",
      en: "Who the agent is: provider, model, temperature and the effective prompt (role + mode).",
    },
    personaNotConfigured: { es: "No configurado", en: "Not configured" },
    personaCombineWithMode: { es: "Combinar con el modo", en: "Combine with mode" },
    personaRoleOnly: { es: "Solo el rol", en: "Role only" },
    personaRoleLabel: { es: "Rol", en: "Role" },
    personaCustomUnavailable: {
      es: 'El modo personalizado está "{label}".',
      en: 'The custom mode is "{label}".',
    },
    personaNoPrompt: {
      es: "Sin system prompt definido. Edita la persona para añadir uno (es/en).",
      en: "No system prompt defined. Edit the persona to add one (es/en).",
    },
    personaPromptOriginFlat: {
      es: "Prompt heredado del campo plano legacy; edita la persona para migrarlo a es/en.",
      en: "Prompt inherited from the legacy flat field; edit the persona to migrate it to es/en.",
    },
    personaPromptsHelp: {
      es:
        "System prompt por idioma (ES + EN). Es la fuente única que muestran la " +
        "tarjeta y el prompt efectivo.",
      en:
        "System prompt per language (ES + EN). Single source shown by the card and " +
        "the effective prompt.",
    },
    /**
     * Los dos tooltips NO se traducen: nombran una ruta del JSON que se guarda
     * (`model_config.system_prompts.es`), y un identificador traducido deja de
     * poder buscarse. Sólo cambia el verbo que lo introduce.
     */
    personaPromptStoredEs: {
      es: "Se guarda en model_config.system_prompts.es",
      en: "Stored in model_config.system_prompts.es",
    },
    personaPromptStoredEn: {
      es: "Se guarda en model_config.system_prompts.en",
      en: "Stored in model_config.system_prompts.en",
    },
  },

  /** `lib/cortex-curiosity.ts` — budget diario de búsquedas del córtex. */
  cortexCuriosity: {
    /**
     * Respaldo del banner de honestidad del córtex (ADR 0075 §6), cuando `/mind`
     * aún no ha respondido y por tanto no hay `note_es`/`note_en` que enseñar.
     *
     * El banner NO es removible: nunca se pintan diales de afecto sin él. Por eso
     * el respaldo tiene que existir en los dos idiomas — la nota que llega del
     * backend ya viene bilingüe, así que dejar sólo el respaldo en castellano
     * hacía que el MISMO aviso cambiara de idioma según hubiera cargado o no.
     */
    honestyFallback: {
      es: "Modelo computacional de afecto, no sentimientos reales.",
      en: "Computational model of affect, not real feelings.",
    },
    budgetNoCap: {
      es: "{used} búsquedas hoy · sin cupo configurado",
      en: "{used} searches today · no cap configured",
    },
    budgetUsage: {
      es: "{used} de {cap} búsquedas hoy ({pct} %)",
      en: "{used} of {cap} searches today ({pct}%)",
    },
    /**
     * Respaldo del aviso honesto de la tarjeta de AUTONOMÍA (ADR 0078), hermano
     * de `honestyFallback` pero para otra tarjeta y otro aviso: aquélla explica
     * los diales de afecto, ésta explica que hay bucles gastando dinero solos.
     *
     * El aviso llega bilingüe del backend (`AUTONOMY_NOTE_ES`/`_EN` en
     * `schemas/cortex_autonomy.py`), así que en el camino feliz este texto no se
     * ve. Existe porque el camino infeliz sí pasaba: con las dos notas vacías
     * —backend viejo, respuesta recortada— el panel pintaba un `<p>` en blanco y
     * seguía enseñando el kill-switch y el gasto SIN el aviso que los explica,
     * que es justo lo que el ADR 0075 §6 declara no removible.
     */
    autonomyHonestyFallback: {
      es:
        "El córtex investiga temas por su cuenta dentro de límites de coste que tú" +
        " controlas; es un comportamiento programado, no curiosidad consciente.",
      en:
        "The cortex researches topics on its own within cost limits you control;" +
        " this is a programmed behaviour, not conscious curiosity.",
    },
  },

  /**
   * Panel de Mente del córtex (Córtex F2, ADR 0075) — `components/cortex/
   * mind-panel.tsx` y la pantalla `app/admin/cortex/mind/`.
   *
   * El panel nació cableado en castellano salvo el aviso honesto, y por eso su
   * casilla del plan seguía abierta: el requisito «ES+EN» de la fase no se
   * cumplía. Aquí está el copy entero, incluido el del aviso — que NO es
   * removible (ADR 0075 §6): sin él no se pintan diales de afecto.
   *
   * Lo que NO entra aquí: la nota `note_es`/`note_en` que redacta el backend.
   * Esa llega en DATOS y se elige con `honestNote(...)`/`pickLang`; el respaldo
   * de cuando viene vacía vive en `cortexCuriosity.honestyFallback`, que es
   * quien ya lo tenía y a quien apuntan sus tests.
   */
  cortexMind: {
    title: { es: "Panel de Mente", en: "Mind Panel" },
    description: {
      es: "El estado afectivo del córtex en vivo: emoción (PAD), mood, sensaciones (drives), evolución temporal y episodios. Es una simulación computacional, no sentimientos reales.",
      en: "The córtex's affective state, live: emotion (PAD), mood, drives, evolution over time and episodes. It is a computational simulation, not real feelings.",
    },
    noAccessTitle: { es: "Panel de Mente no disponible", en: "Mind Panel unavailable" },
    noAccessDescription: {
      es: "El Panel de Mente es exclusivo del System Owner (el dueño del despliegue). Tu cuenta no tiene ese rol.",
      en: "The Mind Panel belongs to the System Owner (the deployment's owner). Your account does not have that role.",
    },
    loadError: {
      es: "No se pudo cargar el estado del córtex: {detail}",
      en: "Could not load the córtex state: {detail}",
    },
    // --- el aviso honesto (ADR 0075 §6) ---------------------------------
    honestyLabel: { es: "Aviso de honestidad:", en: "Honesty notice:" },
    honestyTail: {
      es: "Lo que ves es una simulación determinista del afecto del córtex; no se vende como emociones ni consciencia reales.",
      en: "What you see is a deterministic simulation of the córtex's affect; it is not offered as real emotions or consciousness.",
    },
    // --- diales PAD ------------------------------------------------------
    padTitle: { es: "Emoción (PAD) y mood", en: "Emotion (PAD) and mood" },
    valence: { es: "Valencia", en: "Valence" },
    arousal: { es: "Activación", en: "Arousal" },
    dominance: { es: "Dominancia", en: "Dominance" },
    intensity: { es: "Intensidad", en: "Intensity" },
    noState: {
      es: "Aún no hay estado afectivo que enseñar.",
      en: "No affective state to show yet.",
    },
    // --- drives ("sensaciones") -----------------------------------------
    drivesTitle: { es: "Sensaciones (drives)", en: "Drives (needs)" },
    curiosity: { es: "Curiosidad", en: "Curiosity" },
    bonding: { es: "Vínculo", en: "Bonding" },
    coherence: { es: "Coherencia", en: "Coherence" },
    competence: { es: "Competencia", en: "Competence" },
    // --- curva de mood ---------------------------------------------------
    moodChartTitle: {
      es: "Mood en el tiempo (valencia del mood)",
      en: "Mood over time (mood valence)",
    },
    moodChartAria: {
      es: "Evolución de la valencia del mood en el tiempo",
      en: "Mood valence over time",
    },
    moodChartError: {
      es: "No se pudo cargar la serie temporal.",
      en: "Could not load the time series.",
    },
    moodChartEmpty: {
      es: "Aún no hay snapshots afectivos. Conversa con el córtex para empezar a registrar su mood.",
      en: "No affective snapshots yet. Talk to the córtex to start recording its mood.",
    },
    // --- episodios -------------------------------------------------------
    episodesTitle: { es: "Episodios recientes", en: "Recent episodes" },
    episodesError: {
      es: "No se pudieron cargar los episodios.",
      en: "Could not load the episodes.",
    },
    episodesEmpty: {
      es: "Sin episodios emocionales todavía.",
      en: "No emotional episodes yet.",
    },
    episodeReasonLabel: { es: "Motivo:", en: "Reason:" },
    episodeNoReason: { es: "Sin motivo registrado.", en: "No reason recorded." },
  },

  /**
   * Identidad evolutiva del córtex (Córtex F3, ADR 0074/0077): el resumen del
   * diff (`lib/cortex-identity.ts`), la tarjeta (`components/cortex/
   * identity-card.tsx`), el timeline y la pantalla de edición.
   *
   * El **aviso honesto** (`honestyNote`) es la clave que cierra la casilla F3.6:
   * era un `const HONESTY_NOTE` en castellano dentro de la pantalla, así que el
   * «(ES+EN)» del enunciado no se cumplía. Vive aquí, en una sola clave, y la
   * usan la tarjeta y la pantalla: dos copias del mismo aviso es como uno de los
   * dos se queda atrás.
   */
  cortexIdentity: {
    versionLabel: { es: "versión {n}", en: "version {n}" },
    unset: { es: "sin definir", en: "unset" },
    changesOne: { es: "{label}: {n} ajuste", en: "{label}: {n} change" },
    changesMany: { es: "{label}: {n} ajustes", en: "{label}: {n} changes" },
    rewritten: { es: "{label} reescrita", en: "{label} rewritten" },
    noChanges: { es: "sin cambios", en: "no changes" },
    // --- copy honesto (regla de producto de la fase) ----------------------
    honestyNote: {
      es: "La identidad del córtex es un modelo computacional que evoluciona — no es consciencia ni un «yo» real.",
      en: "The córtex's identity is a computational model that evolves — it is not consciousness or a real self.",
    },
    // --- tarjeta + pantalla ----------------------------------------------
    title: { es: "Identidad del córtex", en: "Córtex identity" },
    description: {
      es: "Co-diseña quién es tu córtex: su nombre, sus valores y su narrativa. Es un modelo computacional que evoluciona, no consciencia.",
      en: "Co-design who your córtex is: its name, its values and its narrative. It is a computational model that evolves, not consciousness.",
    },
    noAccessTitle: { es: "Identidad no disponible", en: "Identity unavailable" },
    noAccessDescription: {
      es: "La identidad del córtex es exclusiva del System Owner (el dueño del despliegue). Tu cuenta no tiene ese rol.",
      en: "The córtex identity belongs to the System Owner (the deployment's owner). Your account does not have that role.",
    },
    loading: { es: "Cargando identidad…", en: "Loading identity…" },
    loadError: {
      es: "No se pudo cargar la identidad del córtex.",
      en: "Could not load the córtex identity.",
    },
    unnamed: { es: "Sin nombre todavía", en: "Not named yet" },
    valuesTitle: { es: "Valores", en: "Core values" },
    goalsTitle: { es: "Objetivos de aprendizaje", en: "Learning goals" },
    narrativeTitle: { es: "Narrativa", en: "Narrative" },
    narrativeEmpty: {
      es: "Todavía no hay narrativa: la reflexión la irá escribiendo.",
      en: "No narrative yet: reflection will write it over time.",
    },
    editLink: { es: "Editar identidad", en: "Edit identity" },
    onboardingTitle: {
      es: "Aún no le has dado identidad a tu córtex",
      en: "You have not given your córtex an identity yet",
    },
    onboardingBody: {
      es: "Ponle un nombre y unos valores. A partir de ahí, la reflexión periódica irá puliendo su narrativa y sus rasgos con el tiempo.",
      en: "Give it a name and some values. From there, periodic reflection will refine its narrative and traits over time.",
    },
    // --- co-construcción: el córtex se propone a sí mismo (F3.3) -----------
    proposeCta: { es: "Que se proponga él", en: "Let it propose itself" },
    proposeRunning: { es: "Pensándolo…", en: "Thinking…" },
    proposeHelp: {
      es: "El córtex redactará una propuesta de nombre, valores y narrativa. No se guarda nada hasta que la aceptes.",
      en: "The córtex will draft a proposal of name, values and narrative. Nothing is saved until you accept it.",
    },
    proposalTitle: { es: "Lo que propone", en: "What it proposes" },
    proposalAccept: { es: "Aceptar y guardar", en: "Accept and save" },
    proposalAccepting: { es: "Guardando…", en: "Saving…" },
    proposalDiscard: { es: "Descartar", en: "Discard" },
    proposalEditHint: {
      es: "Puedes editar los campos de abajo antes de aceptar: se guarda lo que quede en el formulario.",
      en: "You can edit the fields below before accepting: what gets saved is what the form holds.",
    },
    proposalError: {
      es: "No se pudo generar la propuesta.",
      en: "Could not generate the proposal.",
    },
    proposalAlready: {
      es: "Tu córtex ya tiene identidad: la propuesta no vuelve a lanzarse.",
      en: "Your córtex already has an identity: the proposal is not run again.",
    },
    // --- rasgos Big-Five (radar) -----------------------------------------
    traitsTitle: {
      es: "Rasgos derivados por la reflexión",
      en: "Traits derived by reflection",
    },
    traitsHint: {
      es: "Los rasgos Big-Five y el ánimo base los ajusta la reflexión periódica de forma acotada; no se editan a mano.",
      en: "The Big Five traits and the mood baseline are adjusted within bounds by periodic reflection; they are not edited by hand.",
    },
    traitOpenness: { es: "Apertura", en: "Openness" },
    traitConscientiousness: { es: "Responsabilidad", en: "Conscientiousness" },
    traitExtraversion: { es: "Extraversión", en: "Extraversion" },
    traitAgreeableness: { es: "Amabilidad", en: "Agreeableness" },
    traitNeuroticism: { es: "Neuroticismo", en: "Neuroticism" },
    radarAria: {
      es: "Radar de rasgos Big-Five: {detail}",
      en: "Big Five trait radar: {detail}",
    },
    // --- timeline de versiones -------------------------------------------
    timelineTitle: { es: "Cómo ha ido cambiando", en: "How it has changed" },
    timelineSubtitle: {
      es: "Una versión por cambio, con lo que se movió — modelo computacional, no memoria de un yo",
      en: "One version per change, with what moved — a computational model, not the memory of a self",
    },
    timelineLoading: { es: "Cargando el histórico…", en: "Loading the history…" },
    timelinePendingLead: {
      es: "El histórico de versiones todavía no está disponible en este despliegue (falta el endpoint",
      en: "The version history is not available in this deployment yet (the endpoint",
    },
    timelinePendingTail: {
      es: "). Los cambios sí se están guardando: aparecerán aquí en cuanto el endpoint esté.",
      en: " is missing). The changes are being stored: they will show up here once the endpoint lands.",
    },
    timelineError: {
      es: "No se pudo cargar el histórico de identidad.",
      en: "Could not load the identity history.",
    },
    timelineEmpty: {
      es: "Aún no hay versiones anteriores: esta identidad es la primera. Cada reflexión (o cada cambio tuyo) dejará aquí su rastro.",
      en: "No earlier versions yet: this identity is the first one. Every reflection (or change of yours) will leave its trace here.",
    },
    byReflection: { es: "reflexión", en: "reflection" },
    byOwner: { es: "tú", en: "you" },
    byOnboarding: { es: "onboarding co-diseñado", en: "co-designed onboarding" },
    // --- formulario de la pantalla ---------------------------------------
    nameLabel: { es: "Nombre", en: "Name" },
    namePlaceholder: {
      es: "Cómo se llama tu córtex (p. ej. «Atlas»)",
      en: "What your córtex is called (e.g. “Atlas”)",
    },
    valuesLabel: { es: "Valores (uno por línea)", en: "Core values (one per line)" },
    valuesPlaceholder: {
      es: "honestidad\ncuriosidad\nrigor",
      en: "honesty\ncuriosity\nrigour",
    },
    narrativeLabel: { es: "Narrativa (en primera persona)", en: "Narrative (first person)" },
    narrativePlaceholder: {
      es: "Quién soy, qué me importa, cómo ayudo al owner…",
      en: "Who I am, what I care about, how I help the owner…",
    },
    narrativeHint: {
      es: "La reflexión periódica reescribe esta narrativa con el tiempo; aquí puedes darle un punto de partida.",
      en: "Periodic reflection rewrites this narrative over time; here you can give it a starting point.",
    },
    languageLabel: { es: "Idioma", en: "Language" },
    goalsLabel: {
      es: "Objetivos de aprendizaje (uno por línea)",
      en: "Learning goals (one per line)",
    },
    goalsPlaceholder: {
      es: "entender mejor mis proyectos\nrecordar mis preferencias",
      en: "understand my projects better\nremember my preferences",
    },
    save: { es: "Guardar", en: "Save" },
    createIdentity: { es: "Crear identidad", en: "Create identity" },
    saved: { es: "Identidad guardada (versión {n}).", en: "Identity saved (version {n})." },
    reflectNow: { es: "Reflexionar ahora", en: "Reflect now" },
    reflectQueued: {
      es: "Reflexión en marcha; los cambios aparecerán en breve.",
      en: "Reflection under way; the changes will show up shortly.",
    },
    reflectFailed: {
      es: "No se pudo encolar la reflexión ahora mismo.",
      en: "The reflection could not be queued right now.",
    },
    ownerModelTitle: { es: "Lo que sabe de ti", en: "What it knows about you" },
    ownerModelHint: {
      es: "Modelo computacional del owner — lo deriva la reflexión, no se edita a mano",
      en: "Computational model of the owner — derived by reflection, not edited by hand",
    },
    ownerModelEmpty: {
      es: "Aún no ha aprendido nada duradero sobre ti. Conversa con el córtex y pulsa «Reflexionar ahora»: lo que destile aparecerá aquí (y lo usará en cada turno).",
      en: "It has not learned anything lasting about you yet. Talk to the córtex and press “Reflect now”: whatever it distils will show up here (and it will use it every turn).",
    },
  },

  /** `lib/persona/persona.ts` — validación de cliente del `model_config`. */
  persona: {
    errorProvider: { es: "Selecciona un proveedor.", en: "Select a provider." },
    errorModelEmpty: { es: "El modelo no puede estar vacío.", en: "Model cannot be empty." },
    errorTemperature: {
      es: "La temperatura debe estar entre {min} y {max}.",
      en: "Temperature must be between {min} and {max}.",
    },
  },

  /**
   * Despliegue del marketplace en proyectos (ADR 0142, fase 2 del plan
   * `marketplace-v2-despliegue`).
   *
   * Un solo namespace para las TRES puertas —ficha de la instalación, wizard de
   * proyecto y pestañas del proyecto— porque las tres enseñan el mismo estado
   * leyendo la misma entidad (decisión D4): tener un namespace por pantalla
   * sería la primera forma de que sus textos empiecen a divergir.
   *
   * Lo que NO entra aquí: `kind` y `trust_level` de un listing, que se pintan
   * crudos como ya hace el catálogo (son vocabulario del backend, no texto de
   * UI), y los avisos del despliegue, que los redacta el servicio y llegan en
   * castellano dentro de la respuesta.
   */
  marketplaceDeploy: {
    // --- el banner de actualización (task_mkt2_12) ------------------------
    updateAvailable: {
      es: "Versión {version} disponible (tienes la {installed}).",
      en: "Version {version} available (you have {installed}).",
    },
    updateMajor: { es: "cambio de versión mayor", en: "major version change" },
    updateAsksMore: {
      es: "Esta versión pide MÁS permisos que la que tienes instalada:",
      en: "This version asks for MORE permissions than the one you have installed:",
    },
    updateAsksLess: {
      es: "Esta versión pide menos permisos que la instalada; no hay nada que decidir.",
      en: "This version asks for fewer permissions than the installed one; nothing to decide.",
    },
    updateDeltaAdded: { es: "nuevo: {type}", en: "new: {type}" },
    updateDeltaChanged: {
      es: "{type}: de {from} a {to}",
      en: "{type}: from {from} to {to}",
    },
    updateApply: { es: "Actualizar", en: "Update" },
    updateReviewAndApply: {
      es: "Revisar permisos y actualizar",
      en: "Review permissions and update",
    },
    updateAllowMajor: {
      es: "Ver el salto de versión mayor",
      en: "Show the major version jump",
    },

    // --- el mismo aviso, en el CATÁLOGO (task_mkt2_12) --------------------
    // La ficha avisa de UNA instalación; esto avisa de todas a la vez, que es
    // lo que se ve sin entrar en ninguna. Y no dice «Actualizar» en ningún
    // sitio: desde una lista sólo se puede llevar al lugar donde el delta de
    // permisos cabe en pantalla, que es la ficha.
    catalogUpdatesTitle: {
      es: "{n} instalación(es) con una versión más nueva disponible",
      en: "{n} installation(s) with a newer version available",
    },
    catalogUpdatesConsent: {
      es: "{n} de ellas piden permisos que no tienes concedidos: se revisan en su ficha antes de aplicar nada.",
      en: "{n} of them ask for permissions you have not granted: review them on their page before applying anything.",
    },
    catalogUpdatesRow: {
      es: "{name}: de la {installed} a la {version}",
      en: "{name}: from {installed} to {version}",
    },
    catalogUpdatesRowMajor: {
      es: "{name}: tienes la {installed} y existe la {version}",
      en: "{name}: you have {installed} and {version} exists",
    },
    catalogUpdatesNeedsConsent: {
      es: "pide permisos nuevos",
      en: "asks for new permissions",
    },
    catalogUpdatesOpen: { es: "Abrir la instalación", en: "Open the installation" },
    catalogUpdateChip: {
      es: "actualizable a {version}",
      en: "update available: {version}",
    },
    catalogUpdateChipMany: {
      es: "{n} instalaciones actualizables",
      en: "{n} installations can be updated",
    },
    // --- el formulario del `config_schema` -------------------------------
    configTitle: { es: "Configuración", en: "Configuration" },
    configHelp: {
      es: "Estos valores son de ESTE proyecto: la misma capacidad puede desplegarse en otro con otros.",
      en: "These values belong to THIS project: the same capability can be deployed elsewhere with different ones.",
    },
    noConfigNeeded: {
      es: "Esta capacidad no pide configuración.",
      en: "This capability needs no configuration.",
    },
    secretHelp: {
      es: "Sólo un puntero a Vault ({prefix}…). El secreto no se guarda en la configuración del despliegue.",
      en: "A Vault pointer only ({prefix}…). The secret is never stored in the deployment configuration.",
    },
    rolesTitle: { es: "Roles que la reciben", en: "Roles that receive it" },
    rolesHelp: {
      es: "El manifest sugiere y tú confirmas o ajustas. Los roles vienen pre-marcados desde sus «targets».",
      en: "The manifest suggests and you confirm or adjust. Roles come pre-checked from its «targets».",
    },
    rolesEmptyWarning: {
      es: "Sin ningún rol marcado no se asigna a ningún agente: el despliegue queda registrado y se puede repetir con roles.",
      en: "With no role checked nothing is assigned to any agent: the deployment is recorded and can be repeated with roles.",
    },

    // --- errores de validación (códigos de `deployment-types.ts`) --------
    errRequired: { es: "«{field}»: campo requerido.", en: "«{field}»: required field." },
    errType: { es: "«{field}»: se esperaba {detail}.", en: "«{field}»: expected {detail}." },
    errEnum: { es: "«{field}»: admitidos {detail}.", en: "«{field}»: allowed {detail}." },
    errItemEnum: {
      es: "«{field}»: alguna entrada no está entre {detail}.",
      en: "«{field}»: some entry is not among {detail}.",
    },
    errMinItems: {
      es: "«{field}»: elige al menos {detail}.",
      en: "«{field}»: choose at least {detail}.",
    },
    errMin: { es: "«{field}»: debe ser >= {detail}.", en: "«{field}»: must be >= {detail}." },
    errMax: { es: "«{field}»: debe ser <= {detail}.", en: "«{field}»: must be <= {detail}." },
    errSecretNotVaultPointer: {
      es: "«{field}»: debe ser un puntero a Vault; un secreto en claro no se guarda.",
      en: "«{field}»: must be a Vault pointer; a plaintext secret is not stored.",
    },
    errSecretPointerEmpty: {
      es: "«{field}»: el puntero a Vault está vacío.",
      en: "«{field}»: the Vault pointer is empty.",
    },
    errUnknown: {
      es: "«{field}»: no existe en el esquema de esta versión.",
      en: "«{field}»: not declared by this version's schema.",
    },

    // --- ficha de la instalación ----------------------------------------
    installationTitle: { es: "Instalación del marketplace", en: "Marketplace installation" },
    installationDescription: {
      es: "Instalar añade la capacidad al fondo del tenant; desplegar es lo que se la entrega a un proyecto.",
      en: "Installing adds the capability to the tenant's pool; deploying is what hands it to a project.",
    },
    permissionsLink: { es: "Permisos y consentimiento", en: "Permissions and consent" },
    notFound: {
      es: "No se encontró esa instalación en este tenant.",
      en: "That installation was not found in this tenant.",
    },
    deploymentsTitle: { es: "Despliegues", en: "Deployments" },
    deployedInCount: {
      es: "Desplegado en {n} proyecto(s)",
      en: "Deployed to {n} project(s)",
    },
    deployedNone: {
      es: "Todavía no está desplegado en ningún proyecto.",
      en: "Not deployed to any project yet.",
    },
    deployTo: { es: "Desplegar a…", en: "Deploy to…" },
    pickProjects: { es: "Elige los proyectos destino", en: "Pick the target projects" },
    noProjects: { es: "Este tenant no tiene proyectos.", en: "This tenant has no projects." },
    alreadyDeployedHere: { es: "ya desplegado", en: "already deployed" },
    retire: { es: "Retirar", en: "Retire" },
    retireConfirm: {
      es: "¿Retirar este despliegue? Se deshace exactamente lo que creó; lo que hayas asignado a mano no se toca.",
      en: "Retire this deployment? Exactly what it created is undone; anything you assigned by hand is left alone.",
    },
    statusActive: { es: "activo", en: "active" },
    statusDisabled: { es: "deshabilitado", en: "disabled" },
    statusRetired: { es: "retirado", en: "retired" },

    // --- resultado de desplegar -----------------------------------------
    submitDeploy: { es: "Desplegar", en: "Deploy" },
    deploying: { es: "Desplegando…", en: "Deploying…" },
    cancel: { es: "Cancelar", en: "Cancel" },
    resultOk: { es: "Desplegado en «{project}».", en: "Deployed to «{project}»." },
    resultAlready: {
      es: "«{project}» ya lo tenía desplegado: no se ha cambiado nada.",
      en: "«{project}» already had it deployed: nothing was changed.",
    },
    resultFailed: { es: "«{project}»: no se pudo desplegar.", en: "«{project}»: deploy failed." },
    warningsTitle: { es: "Avisos del despliegue", en: "Deployment warnings" },
    oauthPending: {
      es: "El servidor declara OAuth: la entrada nace sin conexión. Complétala con «Conectar» en la pestaña MCP del proyecto.",
      en: "The server declares OAuth: the entry starts unconnected. Finish it with «Connect» on the project's MCP tab.",
    },

    // --- pestañas del proyecto (activación local) ------------------------
    availableTitle: { es: "Disponibles en tu tenant", en: "Available in your tenant" },
    availableHelp: {
      es: "Instaladas en el tenant y todavía no activadas en este proyecto.",
      en: "Installed in the tenant and not yet enabled on this project.",
    },
    availableEmpty: {
      es: "No queda nada instalado por activar en este proyecto.",
      en: "Nothing installed is left to enable on this project.",
    },
    activate: { es: "Activar", en: "Enable" },
    deployedHereTitle: {
      es: "Del marketplace, ya en este proyecto",
      en: "From the marketplace, already here",
    },
    openInstallation: { es: "Ver la instalación", en: "Open the installation" },

    // --- paso «Capacidades» del wizard de proyecto -----------------------
    wizardStepTitle: { es: "Capacidades", en: "Capabilities" },
    wizardStepHelp: {
      es: "Marca lo que este proyecto debe recibir. Se despliega justo después de crearlo, y un fallo no impide que el proyecto nazca.",
      en: "Check what this project should receive. It is deployed right after creating it, and a failure does not stop the project from being created.",
    },
    wizardNothingInstalled: {
      es: "Tu tenant no tiene nada instalado del marketplace. Podrás desplegar capacidades más tarde desde la ficha del proyecto.",
      en: "Your tenant has nothing installed from the marketplace. You will be able to deploy capabilities later from the project page.",
    },
    wizardResultsTitle: { es: "Resultado del despliegue", en: "Deployment result" },
    wizardGoToProject: { es: "Ir al proyecto", en: "Go to the project" },
    back: { es: "Atrás", en: "Back" },
    next: { es: "Siguiente", en: "Next" },
  },

  /**
   * La revisión de un listing, contada al AUTOR (ADR 0142 D6, `task_mkt2_10`).
   *
   * Publicar un listing privado NO lo publica: lo deja en `pending_review` a la
   * espera de que un System Admin lo mire. La UI decía «Listing publicado. Ya
   * aparece en tu catálogo privado», y era falso dos veces — porque además la
   * cláusula de visibilidad del catálogo es `published OR propio`, así que
   * mientras espera no lo ve NADIE salvo su tenant autor, ni siquiera aquéllos
   * con los que se comparta por un grant. Este namespace es lo que hace que la
   * pantalla diga eso en vez de una felicitación.
   *
   * Los cuatro `status*` son el vocabulario COMPARTIDO con la cola del System
   * Admin: `review/review-i18n.ts` los toma de aquí en vez de tener su copia,
   * para que el autor lea del estado de su listing exactamente la misma palabra
   * que usa quien lo revisa.
   */
  marketplaceReview: {
    statusPendingReview: { es: "Pendiente de revisión", en: "Pending review" },
    statusPublished: { es: "Publicado", en: "Published" },
    statusRejected: { es: "Rechazado", en: "Rejected" },
    statusDraft: { es: "Borrador", en: "Draft" },

    // --- antes de pulsar «Publicar» ---------------------------------------
    beforePublish: {
      es: "«Publicar» no publica todavía: el listing entra en la cola de revisión de un System Admin de la plataforma.",
      en: "«Publish» does not publish yet: the listing joins a platform System Admin's review queue.",
    },

    // --- después de pulsarlo ----------------------------------------------
    queuedTitle: {
      es: "Enviado a revisión — todavía no está publicado.",
      en: "Submitted for review — not published yet.",
    },
    queuedWho: {
      es: "Lo aprueba o lo rechaza un System Admin de la plataforma; no hay un plazo comprometido.",
      en: "A platform System Admin approves or rejects it; there is no committed deadline.",
    },
    queuedMeanwhile: {
      es: "Mientras espera, el listing sólo es visible para tu tenant: no aparece en el catálogo de nadie más, ni siquiera de los tenants con los que lo compartas.",
      en: "While it waits, the listing is visible only to your tenant: it shows up in nobody else's catalog, not even in that of the tenants you share it with.",
    },
    publishedTitle: {
      es: "Aprobado y visible en el catálogo.",
      en: "Approved and visible in the catalog.",
    },
    pendingSince: { es: "En cola desde el {date}", en: "Queued since {date}" },

    // --- un rechazo --------------------------------------------------------
    rejectionReason: { es: "Motivo del rechazo", en: "Rejection reason" },
    rejectionMissing: {
      es: "El rechazo llegó sin motivo. Pregunta al System Admin antes de reenviarlo.",
      en: "The rejection arrived with no reason. Ask the System Admin before resubmitting.",
    },
    rejectedFix: {
      es: "Corrige lo que dice el motivo y vuelve a publicar: eso lo devuelve a la cola.",
      en: "Fix what the reason says and publish again: that puts it back in the queue.",
    },
  },

  /**
   * Catálogo de tools — `/admin/tools` (prod-16 `task_prod16_04`).
   *
   * Lo que NO está aquí, y es lo importante de esta pantalla: las etiquetas de
   * las TRES facetas de ADR 0049 (Función / Seguridad / Origen). Esas viven en
   * `lib/tools/taxonomy.ts` como pares `labelEs`/`labelEn` porque son la fuente
   * ÚNICA que comparten el catálogo, la asignación y el diagnóstico: duplicarlas
   * aquí volvería a abrir la divergencia que el ADR 0049 cerró. Se resuelven con
   * `label()`/`pickLang`, que es la otra mitad del i18n (texto bilingüe en
   * datos), no con claves.
   *
   * Los nombres de las facetas SÍ están (`facetCategory`…): eso es el rótulo del
   * `<Select>`, texto de UI que se conoce al compilar.
   */
  tools: {
    title: { es: "Catálogo de tools", en: "Tools catalog" },
    description: {
      es: "Explora las tools de la plataforma y gestiona las personalizadas de tu tenant. Las built-in son de solo lectura.",
      en: "Browse the platform tools and manage your tenant's custom ones. Built-ins are read-only.",
    },
    newTool: { es: "Nueva tool", en: "New tool" },

    // --- facetas y búsqueda ---
    searchPlaceholder: {
      es: "Buscar por nombre o descripción…",
      en: "Search by name or description…",
    },
    searchAriaLabel: {
      es: "Buscar tool por nombre o descripción",
      en: "Search tool by name or description",
    },
    facetCategory: { es: "Función", en: "Function" },
    facetSecurity: { es: "Seguridad", en: "Security" },
    facetImpl: { es: "Origen", en: "Origin" },
    facetAll: { es: "Todas", en: "All" },

    // --- estados de la lista ---
    errorTitle: {
      es: "No se pudo cargar el catálogo de tools",
      en: "The tools catalog could not be loaded",
    },
    emptyFiltered: {
      es: "Ninguna tool coincide con los filtros",
      en: "No tool matches the filters",
    },
    emptyFilteredHelp: {
      es: "Ajusta o limpia los filtros para ver más resultados.",
      en: "Adjust or clear the filters to see more results.",
    },
    emptyCatalog: { es: "No hay tools en el catálogo", en: "There are no tools in the catalog" },
    emptyCatalogHelp: {
      es: "Crea una tool personalizada para empezar.",
      en: "Create a custom tool to get started.",
    },
    clearFilters: { es: "Limpiar filtros", en: "Clear filters" },

    // --- los dos grupos ---
    groupBuiltin: { es: "De plataforma (built-in)", en: "Platform (built-in)" },
    groupBuiltinHint: {
      es: "Mantenidas por la plataforma · solo lectura",
      en: "Maintained by the platform · read-only",
    },
    groupCustom: { es: "Personalizadas del tenant", en: "Tenant custom tools" },
    groupCustomHintEditable: { es: "Editables · custom + MCP", en: "Editable · custom + MCP" },
    groupCustomHint: { es: "Custom + MCP", en: "Custom + MCP" },
    groupEmpty: {
      es: "Ninguna tool en este grupo con los filtros actuales.",
      en: "No tool in this group with the current filters.",
    },

    // --- la fila ---
    unwiredBadge: { es: "No disponible aún", en: "Not available yet" },
    unwiredTooltip: {
      es: "Sin motor en el runtime todavía: asignarla no la haría ejecutable.",
      en: "No runtime engine yet: assigning it would not make it executable.",
    },
    unwiredAriaLabel: {
      es: "No disponible aún: sin motor en el runtime.",
      en: "Not available yet: no runtime engine.",
    },
    // El `{label}` lo aporta la taxonomía; el rótulo de la faceta, el
    // diccionario. Un `aria-label` mitad y mitad es justo lo que prod-16 cierra.
    badgeCategoryAria: { es: "Función: {label}. {help}", en: "Function: {label}. {help}" },
    badgeSecurityAria: { es: "Seguridad: {label}. {help}", en: "Security: {label}. {help}" },
    badgeImplAria: { es: "Origen: {label}. {help}", en: "Origin: {label}. {help}" },
    readOnlyBadge: { es: "Solo lectura", en: "Read-only" },
    editAria: { es: "Editar {name}", en: "Edit {name}" },
    deleteAria: { es: "Borrar {name}", en: "Delete {name}" },

    // --- diálogo de alta / edición ---
    dialogEditTitle: { es: "Editar tool", en: "Edit tool" },
    dialogCreateTitle: { es: "Nueva tool personalizada", en: "New custom tool" },
    dialogDescription: {
      es: "Las built-in las mantiene la plataforma; aquí gestionas las custom de tu tenant. El nombre se normaliza a slug y debe ser único.",
      en: "Built-ins are maintained by the platform; here you manage your tenant's custom ones. The name is normalised to a slug and must be unique.",
    },
    fieldName: { es: "Nombre", en: "Name" },
    fieldNamePlaceholder: { es: "p. ej. deploy_preview", en: "e.g. deploy_preview" },
    fieldDescription: { es: "Descripción", en: "Description" },
    fieldDescriptionPlaceholder: {
      es: "Qué hace y cuándo usarla",
      en: "What it does and when to use it",
    },
    fieldRef: { es: "Referencia de implementación", en: "Implementation reference" },
    fieldRefPlaceholder: {
      es: "URL del endpoint, dotted path de la función, comando…",
      en: "Endpoint URL, function dotted path, command…",
    },
    duplicateError: {
      es: "Ya existe una tool con ese nombre (o colisiona con una built-in).",
      en: "A tool with that name already exists (or collides with a built-in).",
    },
    cancel: { es: "Cancelar", en: "Cancel" },
    saving: { es: "Guardando…", en: "Saving…" },
    saveChanges: { es: "Guardar cambios", en: "Save changes" },
    createTool: { es: "Crear tool", en: "Create tool" },

    // --- diálogo de borrado ---
    deleteTitle: { es: "Borrar tool", en: "Delete tool" },
    deleteDescriptionPrefix: { es: "Se eliminará ", en: "This will remove " },
    deleteDescriptionSuffix: {
      es: " del catálogo del tenant. Esta acción no se puede deshacer.",
      en: " from the tenant catalog. This action cannot be undone.",
    },
    deleting: { es: "Borrando…", en: "Deleting…" },
    delete: { es: "Borrar", en: "Delete" },
  },

  /**
   * Diagnóstico de tools por agente — pestaña «Tools por agente» del proyecto
   * (prod-16 `task_prod16_04`).
   *
   * Pantalla de VERIFICACIÓN: quien la abre está comprobando si un agente
   * ejecuta lo que cree que ejecuta. Dejarla en castellano con el toggle en EN
   * mete ruido justo donde alguien lee con cuidado.
   *
   * Las etiquetas de las tools (Contenedor / Privilegiada / …) NO están aquí:
   * son la taxonomía de ADR 0049 y se resuelven con `label()`, igual que en el
   * catálogo y en la asignación.
   */
  agentToolsDiagnostic: {
    crumb: { es: "Tools por agente", en: "Tools by agent" },
    title: { es: "Diagnóstico de tools por agente", en: "Tool diagnostics by agent" },
    description: {
      es: "Lectura read-only de qué tools (builtin, MCP, http_endpoint, python_function, docker_command) ejecuta de verdad cada agente del proyecto.",
      en: "Read-only view of which tools (builtin, MCP, http_endpoint, python_function, docker_command) each project agent actually runs.",
    },
    bannerStrong: { es: "Solo lectura — verificación.", en: "Read-only — verification." },
    bannerBody: {
      es: " El diagnóstico refleja lo que el runtime ejecuta de verdad; para cambiar asignaciones edita las tools en la ficha del agente. (La sección del marketplace, al final de la página, sí activa capacidades en este proyecto.)",
      en: " The diagnostic reflects what the runtime actually runs; to change assignments edit the tools on the agent page. (The marketplace section, at the end of the page, does enable capabilities on this project.)",
    },
    mcpTitle: { es: "MCP servers del proyecto", en: "Project MCP servers" },
    mcpEmpty: {
      es: "Este proyecto no tiene MCP servers configurados.",
      en: "This project has no MCP servers configured.",
    },
    agentsEmpty: {
      es: "Este proyecto no tiene agentes project-scoped declarados. Los agentes globales del tenant se pueden usar pero no aparecen aquí.",
      en: "This project declares no project-scoped agents. The tenant's global agents can still be used but do not show up here.",
    },
    // El párrafo se parte porque lleva `<code>agent_tools</code>` en medio: el
    // nombre de la tabla no se traduce y tiene que seguir siendo `<code>`.
    noAssignmentsPrefix: {
      es: "Este agente no tiene tools asignadas vía ",
      en: "This agent has no tools assigned through ",
    },
    noAssignmentsSuffix: {
      es: ". Sin asignaciones, el agente conserva el comportamiento por defecto del runtime (sin restricción por agente); no significa que ejecute todo el catálogo.",
      en: ". With no assignments the agent keeps the runtime's default behaviour (no per-agent restriction); it does not mean it runs the whole catalog.",
    },
    notWiredBadge: { es: "No disponible aún", en: "Not available yet" },
  },

  /**
   * Equipos: lista, detalle, los cuatro diálogos y la adopción de un built-in
   * (prod-16 `task_prod16_04`).
   *
   * Un solo namespace para los siete ficheros porque comparten vocabulario
   * («Adoptar», «Proyecto destino», «— Selecciona —») y partirlo por fichero es
   * la primera forma de que esos textos empiecen a divergir entre la lista y el
   * detalle, que es justo lo que un usuario nota.
   *
   * Lo que NO entra: los títulos de `ChatModelSection`, que ya viajan bilingües
   * como `{es, en}` en sus props (los resuelve `pickLang` dentro del
   * componente), y las etiquetas de `MEMORY_SCOPE_OPTIONS`, que viven en
   * `lib/memory/constants.ts` y las comparte la ficha del agente.
   */
  teams: {
    // --- lista ---
    title: { es: "Equipos", en: "Teams" },
    description: {
      es: "Built-ins de la plataforma, plantillas de tu tenant y equipos locales de proyecto.",
      en: "Platform built-ins, your tenant's templates and project-local teams.",
    },
    loadingList: { es: "Cargando equipos…", en: "Loading teams…" },
    listErrorTitle: { es: "No se pudieron cargar los equipos", en: "Could not load teams" },
    tabBuiltin: { es: "Built-in", en: "Built-in" },
    tabTemplate: { es: "Plantillas del Tenant", en: "Tenant templates" },
    tabLocal: { es: "Locales del Proyecto", en: "Project-local" },
    emptyBuiltin: {
      es: "No hay equipos built-in seedeados. Corre python -m api_server.seeds.",
      en: "No built-in teams seeded. Run python -m api_server.seeds.",
    },
    emptyTemplate: {
      es: "Tu tenant aún no tiene equipos propios. Adopta un built-in para empezar.",
      en: "Your tenant has no teams of its own yet. Adopt a built-in to get started.",
    },
    emptyLocal: {
      es: "No hay equipos locales de proyecto. Adopta un equipo a un proyecto o créalo desde el wizard de proyecto.",
      en: "There are no project-local teams. Adopt a team into a project or create it from the project wizard.",
    },
    builtinBadge: { es: "Built-in", en: "Built-in" },
    adoptedBadge: { es: "Adoptado", en: "Adopted" },
    memberOne: { es: "{n} miembro", en: "{n} member" },
    memberMany: { es: "{n} miembros", en: "{n} members" },
    viewDetail: { es: "Ver detalle", en: "View details" },
    adopt: { es: "Adoptar", en: "Adopt" },

    // --- detalle ---
    fallbackName: { es: "Equipo", en: "Team" },
    fallbackDescription: { es: "Detalle del equipo.", en: "Team details." },
    back: { es: "Volver", en: "Back" },
    edit: { es: "Editar", en: "Edit" },
    delete: { es: "Borrar", en: "Delete" },
    adoptCustomize: { es: "Adoptar / Personalizar", en: "Adopt / Customize" },
    loadingTeam: { es: "Cargando equipo…", en: "Loading team…" },
    detailErrorTitle: { es: "No se pudo cargar el equipo:", en: "Could not load team:" },
    modelAdoptHintPrefix: {
      es: "Este equipo es una plantilla de la plataforma (solo lectura). Para fijar su modelo, effort o cualquier otra configuración, ",
      en: "This team is a platform template (read-only). To pin its model, effort or any other setting, ",
    },
    modelAdoptHintStrong: { es: "adóptalo", en: "adopt it" },
    modelAdoptHintSuffix: {
      es: ": la copia de tu organización es totalmente editable y sus agentes la heredan.",
      en: ": your organisation's copy is fully editable and its agents inherit from it.",
    },
    memoryPolicyLabel: {
      es: "Política de memoria del equipo",
      en: "Team memory policy",
    },
    memoryPolicyHelp: {
      es: 'Gobierna la memoria de los agentes del equipo. "Sin política" = cada agente usa su propio scope. Las lecciones (semantic) viajan a este nivel; lo puntual de cada proyecto (episodic) se queda en su proyecto.',
      en: 'Governs the memory of the team\'s agents. "No policy" = each agent uses its own scope. Lessons (semantic) travel to this level; what is specific to a project (episodic) stays in its project.',
    },
    memoryPolicyNone: { es: "Sin política (heredar)", en: "No policy (inherit)" },
    membersHeading: { es: "Miembros ({n})", en: "Members ({n})" },
    addMember: { es: "Añadir miembro", en: "Add member" },
    addMemberDisabled: {
      es: "Los equipos built-in no son editables. Fórkea para personalizar.",
      en: "Built-in teams are not editable. Fork it to customize.",
    },
    membersEmpty: {
      es: "El equipo no tiene miembros todavía.",
      en: "The team has no members yet.",
    },
    unknownAgent: { es: "(agente)", en: "(agent)" },
    forkedBadge: { es: "Forked", en: "Forked" },
    linkedBadge: { es: "Linked", en: "Linked" },
    leaderBadge: { es: "Líder", en: "Leader" },
    priorityBadge: { es: "Prioridad {n}", en: "Priority {n}" },

    // --- alta de miembro ---
    addMemberTitle: { es: "Añadir miembro al equipo", en: "Add a member to the team" },
    addMemberDescription: {
      es: "Elige un agente del catálogo y decide si lo añades por referencia (linked) o como una copia editable (forked).",
      en: "Pick an agent from the catalog and decide whether to add it by reference (linked) or as an editable copy (forked).",
    },
    agentLabel: { es: "Agente", en: "Agent" },
    selectPlaceholder: { es: "— Selecciona —", en: "— Select —" },
    modeLegend: { es: "Modo", en: "Mode" },
    modeLinkedHelp: {
      es: " — el equipo usa el agente por referencia. Si el origen evoluciona, el equipo lo ve.",
      en: " — the team uses the agent by reference. If the source evolves, the team sees it.",
    },
    modeForkedHelp: {
      es: " — clona el agente en un proyecto como copia editable. Independiente del original.",
      en: " — clones the agent into a project as an editable copy. Independent from the original.",
    },
    projectLabel: { es: "Proyecto destino", en: "Target project" },
    addMemberNoProjects: {
      es: "No tienes proyectos creados. Crea uno primero para poder forkear.",
      en: "You have no projects. Create one first to be able to fork.",
    },
    errSelectAgent: { es: "Selecciona un agente.", en: "Select an agent." },
    errSelectProject: {
      es: "Selecciona un proyecto destino para el fork.",
      en: "Select a target project for the fork.",
    },
    adding: { es: "Añadiendo…", en: "Adding…" },
    add: { es: "Añadir", en: "Add" },

    // --- edición de miembro ---
    editMemberTitle: { es: "Editar miembro", en: "Edit member" },
    editMemberDescPrefix: { es: "Metadata de ", en: "Metadata for " },
    editMemberDescSuffix: {
      es: " en este equipo: si es líder, su rol y su prioridad de asignación.",
      en: " in this team: whether it leads it, its role and its assignment priority.",
    },
    isLeader: { es: "Líder del equipo", en: "Team leader" },
    roleInTeam: { es: "Rol en el equipo", en: "Role in the team" },
    roleInTeamPlaceholder: { es: "p. ej. Tech Lead", en: "e.g. Tech Lead" },
    priorityLabel: {
      es: "Prioridad de asignación (0–1000)",
      en: "Assignment priority (0–1000)",
    },

    // --- edición y borrado del equipo ---
    editTeamTitle: { es: "Editar equipo", en: "Edit team" },
    editTeamDescription: {
      es: "Cambia el nombre o la descripción. Los miembros se gestionan desde la lista principal.",
      en: "Change the name or the description. Members are managed from the main list.",
    },
    nameLabel: { es: "Nombre", en: "Name" },
    descriptionLabel: { es: "Descripción", en: "Description" },
    deleteTitle: { es: "Borrar equipo", en: "Delete team" },
    deleteDescPrefix: { es: "Esta acción es ", en: "This action is " },
    deleteDescStrong: { es: "irreversible", en: "irreversible" },
    deleteDescSuffix: {
      es: ". Los agentes miembros NO se borran — solo desaparece su pertenencia a este equipo.",
      en: ". Member agents are NOT deleted — only their membership of this team goes away.",
    },
    deleteConfirmPrompt: {
      es: "Para confirmar, teclea el nombre del equipo:",
      en: "To confirm, type the team name:",
    },
    deleting: { es: "Borrando…", en: "Deleting…" },
    deleteConfirm: { es: "Borrar definitivamente", en: "Delete permanently" },

    // --- adopción de un built-in ---
    adoptTitle: { es: "Adoptar / Personalizar equipo", en: "Adopt / Customize team" },
    adoptDescription: {
      es: 'Crea una copia editable de "{name}". Sus agentes se forkean (persona + tools + skills) y el equipo original built-in no se toca.',
      en: 'Creates an editable copy of "{name}". Its agents are forked (persona + tools + skills) and the original built-in team is untouched.',
    },
    adoptDefaultName: { es: "{name} (copia)", en: "{name} (copy)" },
    adoptNameLabel: { es: "Nombre del equipo", en: "Team name" },
    adoptTargetLegend: { es: "Destino", en: "Target" },
    adoptTargetTenant: { es: "Catálogo del tenant", en: "Tenant catalog" },
    adoptTargetTenantHelp: {
      es: "El equipo y sus agentes viven a nivel de tenant (reutilizable en cualquier proyecto).",
      en: "The team and its agents live at the tenant level (reusable across projects).",
    },
    adoptTargetProject: { es: "Un proyecto", en: "A project" },
    /**
     * H9b: adoptar con destino «un proyecto» ya no se queda a medias — desde
     * `POST /teams/{id}/adopt` la adopción ADEMÁS repunta `projects.team_id` al
     * equipo nuevo. El texto sólo hablaba de la atadura, así que quien lo leía
     * seguía creyendo que después tenía que ir al proyecto a seleccionarlo a
     * mano (y quien no lo hacía se quedaba con el equipo anterior).
     */
    adoptTargetProjectHelp: {
      es: "El equipo y sus agentes quedan atados a un proyecto concreto, y ese proyecto pasa a usarlo: no hace falta asignarlo después a mano.",
      en: "The team and its agents are tied to a specific project, and that project starts using it: no need to assign it by hand afterwards.",
    },
    adoptNoProjects: {
      es: "No tienes proyectos. Crea uno primero o adopta al catálogo del tenant.",
      en: "You have no projects. Create one first or adopt into the tenant catalog.",
    },
    adoptModelLegend: { es: "Modelo del equipo (opcional)", en: "Team model (optional)" },
    adoptPinModel: {
      es: "Fijar un modelo por defecto (si no, hereda de proyecto/plataforma)",
      en: "Pin a default model (otherwise it inherits from project/platform)",
    },
    adoptError: { es: "Error al adoptar el equipo", en: "Failed to adopt the team" },
    adopting: { es: "Adoptando…", en: "Adopting…" },

    // --- compartidas por varios diálogos ---
    cancel: { es: "Cancelar", en: "Cancel" },
    saving: { es: "Guardando…", en: "Saving…" },
    save: { es: "Guardar", en: "Save" },
  },

  /**
   * Dashboard de guardrails del tenant (`/admin/guardrails`, prod-16
   * `task_prod16_04`).
   *
   * Los `guardrail_type` y los `hook_point` NO se traducen: son los slugs del
   * backend y el operador los busca tal cual en logs y en la configuración. Las
   * ACCIONES sí, porque el panel ya las mostraba traducidas al castellano y
   * dejarlas a medias es peor que no traducirlas.
   */
  guardrails: {
    title: { es: "Guardrails", en: "Guardrails" },
    description: {
      es: "Eventos de guardrails sobre el trabajo de tu tenant. El detalle está enmascarado: el secreto / PII que disparó el guardrail nunca se almacena.",
      en: "Guardrail events over your tenant's work. The detail is masked: the secret / PII that fired the guardrail is never stored.",
    },
    forbidden: {
      es: "Necesitas el rol tenant_admin para ver el dashboard de guardrails.",
      en: "You need the tenant_admin role to see the guardrails dashboard.",
    },
    loadError: {
      es: "No se pudo cargar el dashboard:",
      en: "The dashboard could not be loaded:",
    },
    windowLabel: { es: "Ventana:", en: "Window:" },
    eventsInWindow: { es: "Eventos ({n}d)", en: "Events ({n}d)" },
    dailyTrend: { es: "Tendencia diaria", en: "Daily trend" },
    byType: { es: "Por tipo", en: "By type" },
    bySeverity: { es: "Por severidad", en: "By severity" },
    noEvents: { es: "Sin eventos.", en: "No events." },
    recentEvents: { es: "Eventos recientes", en: "Recent events" },
    noRecentEvents: { es: "Sin eventos recientes.", en: "No recent events." },
    colType: { es: "Tipo", en: "Type" },
    colHook: { es: "Hook", en: "Hook" },
    colSeverity: { es: "Severidad", en: "Severity" },
    colAction: { es: "Acción", en: "Action" },
    colDetail: { es: "Detalle (enmascarado)", en: "Detail (masked)" },
    colWhen: { es: "Cuándo", en: "When" },
    sparklineEmptyAria: { es: "Sin eventos en la ventana", en: "No events in the window" },
    sparklineAria: { es: "Eventos por día", en: "Events per day" },
    // Acciones de `guardrail_events.action` (el slug crudo es el fallback).
    actionBlock: { es: "bloquear", en: "block" },
    actionRedact: { es: "enmascarar", en: "redact" },
    actionWarn: { es: "avisar", en: "warn" },
    actionRetryWithFeedback: { es: "reintentar", en: "retry" },
    actionEscalateToHuman: { es: "escalar", en: "escalate" },
    actionTransform: { es: "transformar", en: "transform" },
  },

  /**
   * Ollama & Embeddings — superficie de System Admin (`/admin/ollama`, ADR 0056;
   * prod-16 `task_prod16_04`).
   *
   * Lo que NO se traduce y es deliberado: los nombres de modelo
   * (`nomic-embed-text`) y el `detail` que devuelve el backend tras un pull o un
   * borrado — ese texto lo redacta el api-server, no el panel.
   */
  ollama: {
    title: { es: "Ollama & Embeddings", en: "Ollama & Embeddings" },
    description: {
      es: "Gestión del Ollama del stack (ADR 0056): modelo de embeddings activo + descubrimiento, y administración de modelos (listar / pull / borrar). Solo System Admin.",
      en: "Management of the stack's Ollama (ADR 0056): active embedding model + discovery, and model administration (list / pull / delete). System Admin only.",
    },
    forbidden: {
      es: "Esta sección es exclusiva del System Admin de la plataforma.",
      en: "This section is for the platform's System Admin only.",
    },
    refresh: { es: "Actualizar", en: "Refresh" },

    // --- sección 1: embeddings ---
    embeddingsHeading: { es: "Embeddings", en: "Embeddings" },
    loadingEmbeddings: { es: "Cargando embeddings…", en: "Loading embeddings…" },
    activeModel: { es: "Modelo activo:", en: "Active model:" },
    requiredDim: { es: "Dim requerida:", en: "Required dim:" },
    reachable: { es: "Ollama accesible", en: "Ollama reachable" },
    unreachable: { es: "Ollama no accesible", en: "Ollama not reachable" },
    colEmbedder: { es: "Embedder instalado", en: "Installed embedder" },
    colDim: { es: "Dim", en: "Dim" },
    colCompatible: { es: "Compatible (768)", en: "Compatible (768)" },
    colActive: { es: "Activo", en: "Active" },
    yes: { es: "sí", en: "yes" },
    no: { es: "no", en: "no" },
    noEmbeddersInstalled: {
      es: "No hay embedders del catálogo instalados todavía.",
      en: "No catalog embedders installed yet.",
    },
    embeddersUnreachable: {
      es: "Sin conexión con Ollama: no se pueden listar los embedders instalados.",
      en: "No connection to Ollama: the installed embedders cannot be listed.",
    },
    recommendedHelp: {
      es: "Recomendados (768 dims, compatibles) — instálalos desde «Modelos Ollama»:",
      en: "Recommended (768 dims, compatible) — install them from «Ollama models»:",
    },

    // --- sección 2: modelos ---
    modelsHeading: { es: "Modelos Ollama", en: "Ollama models" },
    pullLabel: { es: "Descargar (pull) un modelo", en: "Download (pull) a model" },
    pullPlaceholder: { es: "p. ej. nomic-embed-text", en: "e.g. nomic-embed-text" },
    pulling: { es: "Descargando…", en: "Downloading…" },
    pull: { es: "Pull", en: "Pull" },
    loadingModels: { es: "Cargando modelos…", en: "Loading models…" },
    modelsUnreachable: {
      es: "Sin conexión con Ollama. Comprueba que el servicio del stack está levantado (modo CPU/GPU, ADR 0056).",
      en: "No connection to Ollama. Check that the stack's service is up (CPU/GPU mode, ADR 0056).",
    },
    modelsEmpty: {
      es: "No hay modelos instalados. Descarga uno con «Pull».",
      en: "No models installed. Download one with «Pull».",
    },
    colModel: { es: "Modelo", en: "Model" },
    colSize: { es: "Tamaño", en: "Size" },
    colActions: { es: "Acciones", en: "Actions" },
    deleteAria: { es: "Borrar {name}", en: "Delete {name}" },
  },

  /**
   * Ficha de coste de un plan (`plan-cost-section.tsx`) — carril D.
   *
   * Sólo el AVISO de estimación con el modelo por defecto. El resto de la ficha
   * (cabeceras de tabla, «Total», «ID», «Modelo») sigue cableada: son términos
   * que se escriben igual en los dos idiomas y migrarlos exige tocar la
   * allowlist `identicalOnPurpose` de `i18n.test.ts`, que es de otro carril.
   * Lo que NO puede pasar es que texto NUEVO nazca cableado, y eso es lo que
   * esta entrada evita.
   *
   * Por qué el aviso existe: `resolve_plan_task_models` devuelve `{}` cuando el
   * proyecto no tiene equipo (sin agentes por rol no hay cadena agente → equipo
   * → proyecto → plataforma que resolver, ADR 0065), y entonces TODAS las
   * tareas se tarifican con el modelo por defecto. La tabla se ve igual de
   * medida en los dos casos, así que quien la lee no distingue «40 EUR con
   * Opus» de «no sé con qué modelo, te lo cobro como gpt-4o».
   *
   * El texto dice «la causa habitual» y no «este proyecto no tiene equipo»
   * porque el panel NO puede afirmarlo: con lo que hoy devuelve
   * `/cost-breakdown` sólo se ve que ninguna fila trae un modelo distinto del
   * por defecto, y eso también pasa —legítimamente— si el equipo existe y todos
   * sus agentes heredan justo el modelo por defecto. Afirmar la causa sería
   * cambiar un número engañoso por un diagnóstico falso.
   */
  planCost: {
    defaultOnlyTitle: {
      es: "Ninguna tarea tiene modelo propio: todas se estiman con el modelo por defecto «{model}».",
      en: "No task has a model of its own: all of them are estimated with the default model «{model}».",
    },
    defaultOnlyCause: {
      es: "La causa habitual es que el proyecto no tenga equipo asignado: sin equipo no hay agente por rol del que heredar el modelo (ADR 0065), y entonces estas cifras son de relleno, no medidas.",
      en: "The usual cause is a project with no team assigned: with no team there is no per-role agent to inherit the model from (ADR 0065), and then these figures are filler, not measured.",
    },
    defaultOnlyLink: {
      es: "Revisar el equipo del proyecto",
      en: "Check the project's team",
    },

    /*
     * El resto del desglose, que llevaba desde la pasada anterior a medias.
     *
     * Este fichero es el aviso más incómodo de todos los que lleva anotado el
     * plan: **usa `useT()` y no tiene ni un atributo con castellano**, así que
     * las DOS guardas lo daban por migrado… y seguía pintando el título de la
     * tarjeta, el texto de carga, el estado vacío, las dos cabeceras de tabla y
     * los dos totales en castellano fijo. Un fichero a medio migrar es
     * indistinguible de uno migrado para un guard que mira patrones.
     */
    title: { es: "Desglose de coste", en: "Cost breakdown" },
    calculating: { es: "Calculando…", en: "Computing…" },
    empty: {
      es: "El plan aún no tiene tareas para calcular el coste.",
      en: "The plan has no tasks to compute a cost from yet.",
    },
    humanHeading: {
      es: "Coste humano · {currency} · {rate} {currency}/h",
      en: "Human cost · {currency} · {rate} {currency}/h",
    },
    aiHeading: {
      es: "Coste IA · {currency} · modelo por defecto",
      en: "AI cost · {currency} · default model",
    },
    // El identificador de la tarea y la fila de total: se escriben igual en los
    // dos idiomas.
    colId: { es: "ID", en: "ID" },
    total: { es: "Total", en: "Total" },
    colTask: { es: "Tarea", en: "Task" },
    colHours: { es: "Horas", en: "Hours" },
    colCost: { es: "Coste", en: "Cost" },
    colComplexity: { es: "Compl.", en: "Cplx." },
    colModel: { es: "Modelo", en: "Model" },
    colCostMin: { es: "Coste mín", en: "Min cost" },
    colCostMax: { es: "Coste máx", en: "Max cost" },
    totalRange: { es: "Total (rango)", en: "Total (range)" },
    missingModels: {
      es: "Modelos sin precio en el catálogo: {models}",
      en: "Models with no price in the catalog: {models}",
    },
  },
  /**
   * `app/admin/settings/page.tsx` — el índice de categorías del tenant.
   *
   * Sólo el MARCO vive aquí. La etiqueta y la descripción de cada categoría las
   * sirve el backend bilingües (`label_es`/`label_en`, `description_es`/
   * `description_en` en `api_server/settings_registry.py`) y se eligen con
   * `pickLang`: son datos, no texto compilado, y duplicarlas aquí reabriría la
   * divergencia entre el registry y el panel.
   *
   * `title` dice "Settings" en los dos idiomas a propósito: es la etiqueta que
   * el usuario acaba de pulsar en la sidebar (`nav.settings`), y cambiarla al
   * entrar haría dudar de si se ha llegado a otra pantalla.
   */
  settingsIndex: {
    title: { es: "Settings", en: "Settings" },
    description: {
      es: "Configuración del tenant — agrupada por categoría.",
      en: "Tenant settings — grouped by category.",
    },
    loading: { es: "Cargando registry…", en: "Loading registry…" },
    errorTitle: {
      es: "No se pudo cargar el registry",
      en: "The registry could not be loaded",
    },
    dedicatedPage: { es: "página dedicada", en: "dedicated page" },
    settingCountOne: { es: "1 ajuste", en: "1 setting" },
    settingCountMany: { es: "{n} ajustes", en: "{n} settings" },
  },

  /**
   * `app/admin/settings/memories/page.tsx` — umbral y candidatos del detector.
   *
   * `thresholdFallback` y `limitFallback` son los nombres que se pintan mientras
   * el registry no ha llegado (o si dejara de traer ese ajuste). No son una copia
   * del registry para ahorrarse el `pickLang`: cuando el dato está, gana el dato.
   *
   * `saved` / `saving` / `saveError` sustituyen a un `status` que guardaba el
   * MENSAJE y decidía el color con `status.startsWith("Error")`. Eso no sobrevive
   * a la traducción —en inglés el mensaje empieza por "Could not"— así que el
   * estado pasó a ser un discriminante y el texto se deriva de él.
   */
  settingsMemories: {
    title: { es: "Memorias", en: "Memories" },
    description: {
      es: "Cómo el sistema detecta memorias similares para que el operador las fusione o descarte.",
      en: "How the system spots similar memories so the operator can merge or discard them.",
    },
    detectorTitle: { es: "Detector de similares", en: "Similar-memory detector" },
    thresholdFallback: { es: "Umbral de similitud", en: "Similarity threshold" },
    limitFallback: { es: "Número de candidatos", en: "Candidate count" },
    save: { es: "Guardar", en: "Save" },
    saving: { es: "Guardando…", en: "Saving…" },
    saved: { es: "Guardado", en: "Saved" },
    saveError: { es: "Error al guardar: {detail}", en: "Could not save: {detail}" },
    saveErrorUnknown: { es: "Error al guardar", en: "Could not save" },
  },

  /**
   * `app/admin/settings/platform-defaults/page.tsx` — ajustes de plataforma sin
   * página propia (System Admin).
   *
   * Mismo reparto que `settingsIndex`: marco aquí, etiquetas y descripciones de
   * cada categoría y ajuste del `platform_settings_registry` vía `pickLang`.
   *
   * Los NOMBRES de los ajustes (`max_review_retries`, `model.default_config`) se
   * pintan crudos junto a su etiqueta y no van al diccionario: son las claves con
   * las que el operador los busca en la BD y en los logs.
   */
  platformDefaults: {
    title: { es: "Valores por defecto de plataforma", en: "Platform defaults" },
    description: {
      es: "Ajustes globales de la plataforma sin página propia (modelo por defecto de agentes, límites de ejecución, RAG, mantenimiento…). Solo System Admin.",
      en: "Platform-wide settings with no page of their own (default agent model, execution limits, RAG, maintenance…). System Admin only.",
    },
    forbidden: {
      es: "Esta sección es exclusiva del System Admin de la plataforma.",
      en: "This section is reserved for the platform System Admin.",
    },
    loading: { es: "Cargando ajustes…", en: "Loading settings…" },
    saved: { es: "Guardado ✓", en: "Saved ✓" },
    save: { es: "Guardar", en: "Save" },
    saving: { es: "Guardando…", en: "Saving…" },
    boolOn: { es: "Activado", en: "Enabled" },
    boolOff: { es: "Desactivado", en: "Disabled" },
    boolHintOn: { es: "(desmarca para desactivar)", en: "(untick to disable)" },
    boolHintOff: { es: "(marca para activar)", en: "(tick to enable)" },
    provider: { es: "Proveedor", en: "Provider" },
    model: { es: "Modelo", en: "Model" },
    temperature: { es: "Temperatura", en: "Temperature" },
    noSyncedModels: {
      es: "Sin modelos sincronizados — sincroniza el proveedor o escribe el nombre.",
      en: "No synced models — sync the provider or type the name in.",
    },
    pickProviderFirst: {
      es: "Elige un proveedor para ver sus modelos.",
      en: "Pick a provider to see its models.",
    },
  },

  /**
   * `app/admin/settings/platform-defaults/cortex-model-section.tsx` — el modelo
   * del córtex (`cortex.default_model`), que sólo ve el System Owner.
   *
   * Vive en el mismo namespace-por-pantalla que el resto de `platform-defaults`
   * porque se renderiza DENTRO de ella, pero separado: es config del owner y no
   * del admin, y sus textos no se comparten con ningún otro ajuste.
   *
   * `reasoningOff` existe porque `lib/model-selection.reasoningLabel` tiene el
   * "Desactivado" castellano como valor por defecto de su parámetro. El llamante
   * le pasa la traducción, igual que ya hacen `components/capability/*` — así el
   * helper sigue sin depender de React ni del idioma.
   */
  cortexModel: {
    title: { es: "Modelo del córtex", en: "Cortex model" },
    description: {
      es: "Modelo que usa el córtex del System Owner para deliberar. Es independiente del modelo de los agentes y del asistente, no se hereda por tenant, y solo lo configura el System Owner. Sin un modelo aquí, el córtex no responde.",
      en: "The model the System Owner's cortex uses to deliberate. It is independent from the agent and assistant models, is not inherited per tenant, and only the System Owner sets it. With no model here, the cortex does not answer.",
    },
    currentModel: { es: "Modelo actual:", en: "Current model:" },
    invalid: {
      es: "(no válido: el proveedor o el modelo ya no existen)",
      en: "(not valid: the provider or the model no longer exist)",
    },
    unset: {
      es: "Sin modelo configurado. El córtex no responderá hasta que elijas uno.",
      en: "No model configured. The cortex will not answer until you pick one.",
    },
    noProviders: {
      es: "No hay proveedores LLM activos. Configura uno en «Proveedores LLM» antes de elegir el modelo del córtex.",
      en: "There are no active LLM providers. Set one up under «LLM providers» before choosing the cortex model.",
    },
    provider: { es: "Proveedor", en: "Provider" },
    model: { es: "Modelo", en: "Model" },
    reasoning: { es: "Razonamiento", en: "Reasoning" },
    reasoningOff: { es: "Desactivado", en: "Off" },
    pickProvider: { es: "— Selecciona un proveedor —", en: "— Pick a provider —" },
    pickProviderFirst: { es: "— Elige primero un proveedor —", en: "— Pick a provider first —" },
    noModels: {
      es: "— Sin modelos (sincronízalos en Proveedores LLM) —",
      en: "— No models (sync them under LLM providers) —",
    },
    pickModel: { es: "— Selecciona un modelo —", en: "— Pick a model —" },
    savedOk: { es: "Modelo del córtex guardado.", en: "Cortex model saved." },
    clear: { es: "Quitar modelo", en: "Remove model" },
    save: { es: "Guardar modelo", en: "Save model" },
    saving: { es: "Guardando…", en: "Saving…" },
  },
  /**
   * `app/admin/projects/page.tsx` — el listado de proyectos del tenant.
   *
   * `errorTitle` corrige un caso de libro del hallazgo frontend-9: el fichero
   * decía `errorTitle="Could not load projects"` —en inglés— dentro de una
   * pantalla por lo demás en castellano. La mezcla no era una traducción a
   * medias, era un descuido, y el guard de atributos no lo veía porque el
   * literal no tiene una sola palabra castellana.
   */
  projectsList: {
    title: { es: "Proyectos", en: "Projects" },
    description: {
      es: "Proyectos activos del tenant. Las plantillas se eligen al crear.",
      en: "The tenant's active projects. Templates are picked when creating one.",
    },
    newProject: { es: "Crear proyecto", en: "New project" },
    loading: { es: "Cargando proyectos…", en: "Loading projects…" },
    errorTitle: {
      es: "No se pudieron cargar los proyectos",
      en: "The projects could not be loaded",
    },
    emptyBody: {
      es: "Este tenant aún no tiene proyectos. Empieza desde una plantilla.",
      en: "This tenant has no projects yet. Start from a template.",
    },
    emptyCta: { es: "Crear el primero", en: "Create the first one" },
    noDescription: { es: "Sin descripción.", en: "No description." },
  },

  /**
   * `app/admin/projects/[id]/memories/page.tsx` — memoria `project_shared`.
   *
   * `badgeEmbedding` dice "embedding" en los dos idiomas: es el nombre de la
   * columna del backend y lo que el operador busca en los logs.
   *
   * El nombre del tipo de memoria (`episodic`/`semantic`) SÍ se traduce, porque
   * el badge lo lee un humano; el valor crudo sigue en `data-type`, que es lo
   * que consultan los tests y quien inspecciona el DOM.
   */
  projectMemories: {
    breadcrumb: { es: "Memoria", en: "Memory" },
    title: { es: "Memoria del proyecto", en: "Project memory" },
    description: {
      es: "Lo que el equipo recuerda en el scope del proyecto (project_shared). La creación y el borrado se hacen desde la pantalla de Memoria del equipo.",
      en: "What the team remembers in the project scope (project_shared). Creating and deleting happen on the team Memory screen.",
    },
    cardTitle: { es: "Memoria del proyecto ({n})", en: "Project memory ({n})" },
    errorTitle: {
      es: "No se pudo cargar la memoria del proyecto",
      en: "The project's memory could not be loaded",
    },
    empty: {
      es: "Sin memoria de proyecto todavía. Cierra tareas con un scope project_shared para que el equipo recuerde entre runs.",
      en: "No project memory yet. Close tasks with a project_shared scope so the team remembers between runs.",
    },
    badgeProject: { es: "Proyecto", en: "Project" },
    badgeEmbedding: { es: "embedding", en: "embedding" },
    typeEpisodic: { es: "Episódica", en: "Episodic" },
    typeSemantic: { es: "Semántica", en: "Semantic" },
  },

  /**
   * `app/admin/projects/[id]/dep-cache/page.tsx` — invalidar la caché de deps.
   *
   * El NOMBRE del runtime no está aquí: lo sirve `GET /runtime-templates`
   * bilingüe y se resuelve con `runtimeLabel()` (que es `pickLang` por dentro).
   * Duplicarlo como claves reabriría la divergencia que ese endpoint cerró — el
   * mismo criterio que ya se aplicó a la taxonomía de tools del ADR 0049.
   *
   * Hay dos claves para el recuento porque «1 entradas invalidadas» es
   * incorrecto en castellano y «1 entries» en inglés: la concordancia de número
   * no se arregla con una plantilla sola.
   */
  depCache: {
    title: { es: "Caché de dependencias", en: "Dependency cache" },
    description: {
      es: "Invalida la caché del dep-cache para forzar al worker-test a reinstalar las dependencias en el siguiente run. Útil cuando sospechas que la caché está corrupta.",
      en: "Invalidate the dep-cache to force worker-test to reinstall the dependencies on the next run. Useful when you suspect the cache is corrupt.",
    },
    cardTitle: { es: "Runtimes con caché", en: "Runtimes with a cache" },
    colRuntime: { es: "Runtime", en: "Runtime" },
    colMount: { es: "Punto de montaje", en: "Mount point" },
    colActions: { es: "Acciones", en: "Actions" },
    colResult: { es: "Resultado", en: "Result" },
    invalidate: { es: "Invalidar", en: "Invalidate" },
    invalidating: { es: "Invalidando…", en: "Invalidating…" },
    errorTitle: {
      es: "No se pudo cargar el catálogo de runtimes",
      en: "The runtime catalog could not be loaded",
    },
    invalidatedCountOne: { es: "1 entrada invalidada", en: "1 entry invalidated" },
    invalidatedCountMany: { es: "{n} entradas invalidadas", en: "{n} entries invalidated" },
  },
  /**
   * Las cuatro políticas de memoria (`MemoryScope` del backend, ADR 0055/0071).
   *
   * Namespace propio y COMPARTIDO, no una clave por pantalla, porque el catálogo
   * lo consumen dos fichas distintas —la del equipo y la del agente— desde una
   * constante única (`lib/memory/constants.ts`). Duplicarlo en `teams` y en
   * `agents` sería exactamente la divergencia que esa constante existe para
   * evitar: el día que se añada un scope habría que acordarse de dos sitios.
   *
   * Los VALORES (`private`, `team_shared`…) no están aquí: son el enum del
   * backend, viajan en la API y se ven en la BD. Aquí sólo su etiqueta.
   */
  memoryScope: {
    private: { es: "Privada", en: "Private" },
    teamShared: { es: "Compartida con equipo", en: "Shared with team" },
    projectShared: { es: "Compartida con proyecto", en: "Shared with project" },
    global: { es: "Global del tenant", en: "Tenant-wide" },
  },
  /**
   * `components/ui/entity-combobox.tsx` y sus tres wrappers (proyecto, equipo,
   * KB).
   *
   * Namespace propio y COMPARTIDO porque el componente lo consumen cuatro
   * módulos distintos (`memories`, `agents`, `knowledge-bases` y el grant de
   * KB): duplicar «Buscar por nombre…» en cada uno sería la divergencia que
   * este diccionario existe para evitar.
   *
   * Estos textos eran **valores por defecto de parámetro**
   * (`placeholder = "Selecciona…"`), no atributos JSX, así que el guard de
   * atributos sólo veía UNO de los siete. Con el toggle en EN, tres pantallas ya
   * migradas seguían pintando «Busca un equipo por nombre…» dentro del
   * formulario. Es el mismo aviso de siempre: el contador mide su patrón, no la
   * deuda.
   */
  combobox: {
    select: { es: "Selecciona…", en: "Select…" },
    search: { es: "Buscar…", en: "Search…" },
    searchByName: { es: "Buscar por nombre…", en: "Search by name…" },
    clear: { es: "Quitar selección", en: "Clear selection" },
    searchError: { es: "Error al buscar", en: "Search failed" },
    noMatch: { es: 'Nada coincide con "{query}".', en: 'Nothing matches "{query}".' },
    empty: { es: "Sin resultados.", en: "No results." },
    projectPlaceholder: {
      es: "Busca un proyecto por nombre…",
      en: "Search for a project by name…",
    },
    teamPlaceholder: { es: "Busca un equipo por nombre…", en: "Search for a team by name…" },
    kbPlaceholder: {
      es: "Busca una knowledge base por nombre…",
      en: "Search for a knowledge base by name…",
    },
    teamMembers: { es: "{n} miembros", en: "{n} members" },
  },
  /**
   * `app/admin/memories/page.tsx` — la memoria del equipo.
   *
   * Las etiquetas cortas de scope (`Todas`, `Equipo`) NO reutilizan el
   * namespace `memoryScope`: aquél es el catálogo largo de la política de un
   * agente («Compartida con equipo») y aquí son las pestañas de un filtro
   * segmentado, donde el texto largo no cabe. Son dos textos distintos para el
   * mismo enum, no una duplicación.
   *
   * El badge `embedding` no está aquí: es la misma palabra en los dos idiomas y
   * además nombra una columna de la BD.
   */
  memories: {
    title: { es: "Memoria del equipo", en: "Team memory" },
    description: {
      es: "Lo que el Memorizer y los humanos persisten para futuros agentes. Filtrable por scope; las globales sólo las edita un tenant_admin.",
      en: "What the Memorizer and humans persist for future agents. Filterable by scope; only a tenant_admin can edit the global ones.",
    },
    scopeAll: { es: "Todas", en: "All" },
    scopePrivate: { es: "Privada", en: "Private" },
    scopeTeam: { es: "Equipo", en: "Team" },
    scopeProject: { es: "Proyecto", en: "Project" },
    scopeGlobal: { es: "Global", en: "Global" },
    typeEpisodic: { es: "Episódica", en: "Episodic" },
    typeSemantic: { es: "Semántica", en: "Semantic" },
    empty: { es: "No hay memorias en este filtro.", en: "No memories match this filter." },
    delete: { es: "Eliminar", en: "Delete" },
    similarAria: {
      es: "Ver {count} memorias similares",
      en: "See {count} similar memories",
    },
    similarBadgeOne: { es: "1 similar", en: "1 similar" },
    similarBadgeMany: { es: "{count} similares", en: "{count} similar" },
    similarTitle: { es: "Memorias similares", en: "Similar memories" },
    similarDescription: {
      es: "Candidatos a duplicado encontrados por similitud coseno del embedding. “Fusionar” combina el contenido del candidato en esta memoria (la actual sobrevive). “Descartar” hace soft-delete del candidato.",
      en: "Duplicate candidates found by cosine similarity of the embedding. “Merge” folds the candidate’s content into this memory (the current one survives). “Discard” soft-deletes the candidate.",
    },
    similarTarget: { es: "Memoria actual (target)", en: "Current memory (target)" },
    similarLoading: { es: "Buscando candidatos…", en: "Searching for candidates…" },
    similarEmpty: {
      es: "No hay candidatos por encima del umbral configurado.",
      en: "No candidates above the configured threshold.",
    },
    similarPercent: { es: "{pct}% similitud", en: "{pct}% similarity" },
    merge: { es: "Fusionar", en: "Merge" },
    discard: { es: "Descartar", en: "Discard" },
    createTitle: { es: "Nueva memoria manual", en: "New manual memory" },
    fieldContent: { es: "Contenido", en: "Content" },
    fieldScope: { es: "Scope", en: "Scope" },
    fieldType: { es: "Tipo", en: "Type" },
    fieldTeam: { es: "Equipo", en: "Team" },
    fieldProject: { es: "Proyecto", en: "Project" },
    fieldTags: { es: "Etiquetas", en: "Tags" },
    tagsPlaceholder: { es: "separadas por comas", en: "comma-separated" },
    submit: { es: "Guardar memoria", en: "Save memory" },
  },
  /**
   * SSO empresarial OIDC (`settings/sso/`): la pantalla, la tarjeta de la URL
   * base pública, la ficha de la config y el diálogo de alta/edición.
   *
   * Un namespace para las CUATRO piezas y no uno por fichero: son una sola
   * pantalla partida por `task_prod16_08`, y separar las claves obligaría a
   * decidir en cuál vive «Guardar» cada vez que el troceo se mueva.
   *
   * Los términos del protocolo NO se traducen y por eso no están aquí:
   * `Issuer`, `Client ID`, `Scopes`, `NameID`, `ACS`. Traducirlos sería peor
   * ayuda — el operador los busca literalmente en la consola de su IdP.
   */
  ssoOidc: {
    title: { es: "SSO empresarial (OIDC)", en: "Enterprise SSO (OIDC)" },
    description: {
      es: "Inicio de sesión único por tenant. Se añade junto al login local — activarlo no lo reemplaza ni lo desactiva.",
      en: "Single sign-on per tenant. It is added alongside local login — enabling it neither replaces nor disables it.",
    },
    configure: { es: "Configurar OIDC", en: "Configure OIDC" },
    samlLinkQuestion: {
      es: "¿Tu IdP habla SAML 2.0 en lugar de OIDC?",
      en: "Does your IdP speak SAML 2.0 instead of OIDC?",
    },
    samlLinkText: { es: "Configura SAML aquí", en: "Configure SAML here" },
    loading: { es: "Cargando…", en: "Loading…" },
    emptyBefore: {
      es: "Este tenant aún no tiene SSO configurado. Pulsa",
      en: "This tenant has no SSO configured yet. Press",
    },
    emptyAfter: {
      es: "para conectarlo con tu proveedor de identidad.",
      en: "to connect it to your identity provider.",
    },
    confirmDelete: {
      es: "¿Borrar la configuración OIDC de este tenant?",
      en: "Delete this tenant’s OIDC configuration?",
    },
    // --- ficha de la configuración ---
    badgeEnabled: { es: "activo", en: "active" },
    badgeDisabled: { es: "inactivo", en: "inactive" },
    badgeSecret: { es: "secreto: {source}", en: "secret: {source}" },
    badgeNoSecret: { es: "sin secreto", en: "no secret" },
    sourceVault: { es: "Vault", en: "Vault" },
    sourceEncrypted: { es: "cifrado en reposo", en: "encrypted at rest" },
    enable: { es: "Activar", en: "Enable" },
    disable: { es: "Desactivar", en: "Disable" },
    edit: { es: "Editar", en: "Edit" },
    delete: { es: "Eliminar", en: "Delete" },
    // --- tarjeta de la URL base pública y la callback derivada ---
    cbTitle: {
      es: "URL base pública de la aplicación",
      en: "Application public base URL",
    },
    cbIntro1: {
      es: "La URL pública única de la plataforma (p. ej.",
      en: "The single public URL of the platform (e.g.",
    },
    cbIntro2: { es: "). De ella se derivan la", en: "). From it the" },
    cbIntroSsoCallback: { es: "callback de SSO", en: "SSO callback" },
    cbIntro3: { es: "y el", en: "and the" },
    cbIntroSamlAcs: { es: "ACS de SAML", en: "SAML ACS" },
    cbIntro4: {
      es: "como rutas — el puerto queda detrás de tu gateway en producción. Es global (una para toda la plataforma).",
      en: "are derived as paths — the port stays behind your gateway in production. It is global (one for the whole platform).",
    },
    cbCurrentBase: { es: "Base pública actual:", en: "Current public base:" },
    cbBaseLabel: { es: "URL base pública", en: "Public base URL" },
    cbBasePlaceholder: { es: "https://tu-dominio.com", en: "https://your-domain.com" },
    cbPrefixLabel: {
      es: "Prefijo de API (reverse proxy)",
      en: "API path prefix (reverse proxy)",
    },
    cbPrefixPlaceholder: {
      es: "/api  (vacío si el API cuelga de la raíz)",
      en: "/api  (empty if the API hangs from the root)",
    },
    cbPrefixHelp1: {
      es: "Si publicas single-origin (SPA en",
      en: "If you publish single-origin (SPA at",
    },
    cbPrefixHelp2: { es: "y API bajo", en: "and API under" },
    cbPrefixHelp3: { es: "tras Caddy/nginx), pon", en: "behind Caddy/nginx), set" },
    cbPrefixHelp4: {
      es: ". Vacío si el api-server cuelga de la raíz del dominio. Se inserta entre el origen y la ruta del callback.",
      en: ". Leave it empty if the api-server hangs from the domain root. It is inserted between the origin and the callback path.",
    },
    cbCallbackLabel: {
      es: "URL de callback / redirect (a registrar en el IdP)",
      en: "Callback / redirect URL (to register at the IdP)",
    },
    cbCopyAria: { es: "Copiar URL de callback", en: "Copy callback URL" },
    cbWarnBefore: {
      es: "Sigue usando el valor de arranque",
      en: "It is still using the bootstrap value",
    },
    cbWarnAfter: {
      es: "(apunta al api-server local, no a tu dominio público). Pon arriba tu URL pública real antes de registrar la callback en el IdP.",
      en: "(it points at the local api-server, not at your public domain). Set your real public URL above before registering the callback at the IdP.",
    },
    copy: { es: "Copiar", en: "Copy" },
    copied: { es: "Copiado", en: "Copied" },
    save: { es: "Guardar", en: "Save" },
    saving: { es: "Guardando…", en: "Saving…" },
    // --- diálogo de alta / edición ---
    dialogEditTitle: {
      es: "Editar configuración OIDC",
      en: "Edit OIDC configuration",
    },
    templateLabel: { es: "Plantilla de proveedor", en: "Provider template" },
    templateLoading: { es: "Cargando plantillas…", en: "Loading templates…" },
    templateNone: {
      es: "— Elige un proveedor (opcional) —",
      en: "— Pick a provider (optional) —",
    },
    templateHelp: {
      es: "Pre-rellena issuer, scopes y mapeo de claims con valores verificados. Después puedes ajustarlos manualmente.",
      en: "Pre-fills issuer, scopes and claim mappings with verified values. You can adjust them by hand afterwards.",
    },
    paramLabel: { es: "Parámetro: {name}", en: "Parameter: {name}" },
    displayNameLabel: { es: "Nombre visible (opcional)", en: "Display name (optional)" },
    issuerHelp: {
      es: "El descubrimiento OIDC consulta",
      en: "OIDC discovery queries",
    },
    secretKeepHint: {
      es: " (dejar vacío para conservar el actual)",
      en: " (leave empty to keep the current one)",
    },
    secretPlaceholder: {
      es: "secreto del cliente OIDC",
      en: "OIDC client secret",
    },
    secretHelp: {
      es: "Se cifra en reposo antes de guardarse; el sistema nunca lo devuelve en claro.",
      en: "It is encrypted at rest before being stored; the system never returns it in clear.",
    },
    scopesLabel: {
      es: "Scopes (separados por espacios)",
      en: "Scopes (space-separated)",
    },
    enabledLabel: {
      es: "Activar este proveedor en el login (añadido al login local, no lo reemplaza)",
      en: "Enable this provider at login (added to local login, it does not replace it)",
    },
    cancel: { es: "Cancelar", en: "Cancel" },
    create: { es: "Crear", en: "Create" },
    saveChanges: { es: "Guardar cambios", en: "Save changes" },
  },
  /**
   * SSO empresarial SAML 2.0 (`settings/sso/saml/`): pantalla, metadatos del
   * SP, ficha y diálogo, con el mismo criterio que `ssoOidc`.
   *
   * Namespace aparte y no una rama de `ssoOidc` porque son dos pantallas con
   * rutas distintas: compartir namespace obligaría a prefijar cada clave para
   * saber a cuál pertenece, que es tener dos namespaces con más ruido.
   */
  ssoSaml: {
    title: { es: "SSO empresarial (SAML 2.0)", en: "Enterprise SSO (SAML 2.0)" },
    description: {
      es: "Inicio de sesión único SAML por tenant. Se añade junto al login local y al SSO OIDC — activarlo no reemplaza ni desactiva ninguno.",
      en: "SAML single sign-on per tenant. It is added alongside local login and OIDC SSO — enabling it neither replaces nor disables either.",
    },
    configure: { es: "Configurar SAML", en: "Configure SAML" },
    oidcLinkQuestion: {
      es: "¿Tu IdP habla OIDC en lugar de SAML?",
      en: "Does your IdP speak OIDC instead of SAML?",
    },
    oidcLinkText: { es: "Configura OIDC aquí", en: "Configure OIDC here" },
    loading: { es: "Cargando…", en: "Loading…" },
    emptyBefore: {
      es: "Este tenant aún no tiene SAML configurado. Pulsa",
      en: "This tenant has no SAML configured yet. Press",
    },
    emptyAfter: {
      es: "para conectarlo con tu proveedor de identidad.",
      en: "to connect it to your identity provider.",
    },
    confirmDelete: {
      es: "¿Borrar la configuración SAML de este tenant?",
      en: "Delete this tenant’s SAML configuration?",
    },
    // --- ficha de la configuración ---
    badgeEnabled: { es: "activo", en: "active" },
    badgeDisabled: { es: "inactivo", en: "inactive" },
    badgeKey: { es: "clave SP: {source}", en: "SP key: {source}" },
    badgeNoKey: { es: "sin clave SP", en: "no SP key" },
    badgeSigned: { es: "AuthnRequest firmado", en: "Signed AuthnRequest" },
    sourceVault: { es: "Vault", en: "Vault" },
    sourceEncrypted: { es: "cifrado en reposo", en: "encrypted at rest" },
    enable: { es: "Activar", en: "Enable" },
    disable: { es: "Desactivar", en: "Disable" },
    edit: { es: "Editar", en: "Edit" },
    delete: { es: "Eliminar", en: "Delete" },
    // --- metadatos del SP ---
    spTitle: { es: "Metadatos del SP (este sistema)", en: "SP metadata (this system)" },
    spIntro1: { es: "Estos valores son", en: "These values are" },
    spIntroGlobal: { es: "globales", en: "global" },
    spIntro2: {
      es: "(una sola identidad de SP para toda la plataforma). Regístralos en tu proveedor de identidad SAML: la",
      en: "(a single SP identity for the whole platform). Register them at your SAML identity provider: the SP",
    },
    spIntroEntityId: { es: "Entity ID", en: "Entity ID" },
    spIntro3: { es: "del SP y la", en: "and the" },
    spIntroAcs: { es: "URL de ACS", en: "ACS URL" },
    spIntro4: {
      es: "(Assertion Consumer Service) a la que el IdP enviará la respuesta.",
      en: "(Assertion Consumer Service) the IdP will post its response to.",
    },
    spConfiguredBase: { es: "Base pública configurada:", en: "Configured public base:" },
    spWarnBefore: {
      es: "Sigue usando la base por defecto",
      en: "It is still using the default base",
    },
    spWarnMiddle: {
      es: "(un marcador de posición, ni siquiera coincide con el api-server de desarrollo). Configura",
      en: "(a placeholder that does not even match the development api-server). Set",
    },
    spWarnAfter: {
      es: "con tu URL pública antes de registrar el ACS en el IdP.",
      en: "to your public URL before registering the ACS at the IdP.",
    },
    copy: { es: "Copiar", en: "Copy" },
    copied: { es: "Copiado", en: "Copied" },
    copyAria: { es: "Copiar {label}", en: "Copy {label}" },
    loadingValue: { es: "Cargando…", en: "Loading…" },
    // --- diálogo de alta / edición ---
    dialogEditTitle: { es: "Editar configuración SAML", en: "Edit SAML configuration" },
    metadataLabel: { es: "Metadatos del IdP (XML)", en: "IdP metadata (XML)" },
    metadataPlaceholder: {
      es: "Pega aquí el EntityDescriptor del IdP, o sube el archivo de metadatos…",
      en: "Paste the IdP EntityDescriptor here, or upload the metadata file…",
    },
    metadataUpload: { es: "Subir XML", en: "Upload XML" },
    metadataParse: { es: "Extraer datos", en: "Extract data" },
    metadataParsing: { es: "Analizando…", en: "Parsing…" },
    metadataHelp: {
      es: "Extrae automáticamente Entity ID, URL de SSO y certificado del IdP. Después puedes ajustarlos manualmente.",
      en: "Automatically extracts the Entity ID, SSO URL and certificate of the IdP. You can adjust them by hand afterwards.",
    },
    metadataParseError: {
      es: "No se pudieron extraer los metadatos: {detail}",
      en: "The metadata could not be extracted: {detail}",
    },
    displayNameLabel: { es: "Nombre visible (opcional)", en: "Display name (optional)" },
    entityIdLabel: { es: "IdP Entity ID", en: "IdP Entity ID" },
    ssoUrlLabel: { es: "URL de SSO del IdP", en: "IdP SSO URL" },
    certLabel: {
      es: "Certificado de firma del IdP (X.509)",
      en: "IdP signing certificate (X.509)",
    },
    certPlaceholder: {
      es: "MIID… (cuerpo base64 del certificado o PEM completo)",
      en: "MIID… (base64 certificate body or full PEM)",
    },
    certHelp: {
      es: "Con este certificado se verifica la firma de las aserciones del IdP.",
      en: "This certificate verifies the signature of the assertions from the IdP.",
    },
    nameIdLabel: { es: "Formato de NameID", en: "NameID format" },
    nameIdEmail: { es: "emailAddress (recomendado)", en: "emailAddress (recommended)" },
    nameIdPersistent: { es: "persistent", en: "persistent" },
    nameIdTransient: { es: "transient", en: "transient" },
    nameIdUnspecified: { es: "unspecified", en: "unspecified" },
    attrEmailLabel: { es: "Atributo de email (opcional)", en: "Email attribute (optional)" },
    attrFullNameLabel: { es: "Atributo de nombre (opcional)", en: "Name attribute (optional)" },
    spKeyIntro: {
      es: "Clave del SP — solo necesaria si firmas el AuthnRequest o cifras las aserciones.",
      en: "SP key — only needed if you sign the AuthnRequest or encrypt the assertions.",
    },
    spCertLabel: {
      es: "Certificado público del SP (X.509)",
      en: "SP public certificate (X.509)",
    },
    spCertPlaceholder: {
      es: "MIID… (certificado público del SP)",
      en: "MIID… (SP public certificate)",
    },
    spKeyLabel: { es: "Clave privada del SP (PEM)", en: "SP private key (PEM)" },
    spKeyKeepHint: {
      es: " (dejar vacío para conservar la actual)",
      en: " (leave empty to keep the current one)",
    },
    spKeyPlaceholder: {
      es: "Pega aquí la clave privada del SP en formato PEM…",
      en: "Paste the SP private key in PEM format here…",
    },
    spKeyHelp: {
      es: "Se cifra en reposo antes de guardarse; el sistema nunca la devuelve en claro.",
      en: "It is encrypted at rest before being stored; the system never returns it in clear.",
    },
    flagAuthnSigned: {
      es: "Firmar el AuthnRequest saliente (requiere clave del SP)",
      en: "Sign the outgoing AuthnRequest (requires the SP key)",
    },
    flagAssertionsSigned: {
      es: "Exigir aserciones firmadas por el IdP (recomendado)",
      en: "Require assertions signed by the IdP (recommended)",
    },
    flagAssertionsEncrypted: {
      es: "Exigir aserciones cifradas (requiere clave del SP)",
      en: "Require encrypted assertions (requires the SP key)",
    },
    flagNameIdEncrypted: {
      es: "Exigir NameID cifrado (requiere clave del SP)",
      en: "Require an encrypted NameID (requires the SP key)",
    },
    enabledLabel: {
      es: "Activar este proveedor en el login (añadido al login local y a OIDC, no los reemplaza)",
      en: "Enable this provider at login (added to local login and OIDC, it does not replace them)",
    },
    cancel: { es: "Cancelar", en: "Cancel" },
    create: { es: "Crear", en: "Create" },
    saveChanges: { es: "Guardar cambios", en: "Save changes" },
    saving: { es: "Guardando…", en: "Saving…" },
  },
  /**
   * Webhooks ENTRANTES del proyecto (`projects/[id]/incoming-webhooks`).
   *
   * Los dos catálogos de la pantalla —orígenes y acciones— viven aquí y no en
   * las constantes del fichero: el `value` (`github`, `create_task`) es el enum
   * del backend y no se traduce, la etiqueta sí. Las constantes guardan la
   * CLAVE, que es lo que hace que añadir un origen nuevo no pueda olvidarse el
   * inglés (no compila).
   */
  incomingWebhooks: {
    breadcrumbCurrent: { es: "Webhooks entrantes", en: "Incoming webhooks" },
    title: {
      es: "Webhooks entrantes del proyecto",
      en: "Project incoming webhooks",
    },
    description: {
      es: "Eventos que herramientas externas (GitHub, Jira, Sentry…) envían a este proyecto. Se verifica la firma HMAC antes de actuar.",
      en: "Events that external tools (GitHub, Jira, Sentry…) send to this project. The HMAC signature is verified before acting.",
    },
    forbiddenBefore: { es: "Necesitas rol", en: "You need the" },
    forbiddenAfter: {
      es: "para gestionar webhooks entrantes.",
      en: "role to manage incoming webhooks.",
    },
    add: { es: "Añadir webhook", en: "Add webhook" },
    loading: { es: "Cargando…", en: "Loading…" },
    emptyBefore: {
      es: "Este proyecto aún no acepta webhooks entrantes. Pulsa",
      en: "This project does not accept incoming webhooks yet. Press",
    },
    emptyAfter: {
      es: "para configurar el primero.",
      en: "to set up the first one.",
    },
    confirmDelete: {
      es: "¿Borrar esta configuración de webhook entrante?",
      en: "Delete this incoming webhook configuration?",
    },
    confirmRotate: {
      es: "Rotar el secreto invalida el actual de inmediato. Tendrás que actualizar el proveedor externo con el nuevo valor. ¿Continuar?",
      en: "Rotating the secret invalidates the current one immediately. You will have to update the external provider with the new value. Continue?",
    },
    // --- catálogo de orígenes (el `value` es el enum del backend) ---
    originGithub: { es: "GitHub", en: "GitHub" },
    originGitlab: { es: "GitLab", en: "GitLab" },
    originJira: { es: "Jira", en: "Jira" },
    originSentry: { es: "Sentry", en: "Sentry" },
    originLinear: { es: "Linear", en: "Linear" },
    originGeneric: { es: "Genérico (HMAC bare-hex)", en: "Generic (bare-hex HMAC)" },
    // --- catálogo de acciones ---
    actionCreateTask: { es: "Crear tarea", en: "Create task" },
    actionComment: { es: "Comentar tarea", en: "Comment on a task" },
    actionEscalate: { es: "Escalar tarea", en: "Escalate a task" },
    // --- banner del secreto, que se enseña UNA vez ---
    secretTitle: {
      es: "🔑 Secreto de firma para «{name}»",
      en: "🔑 Signing secret for “{name}”",
    },
    secretHintBefore: { es: "Cópialo ahora —", en: "Copy it now —" },
    secretHintStrong: { es: "no se volverá a mostrar", en: "it will not be shown again" },
    secretHintAfter: {
      es: ". Pégalo en el secreto del webhook del proveedor externo para que firme sus eventos.",
      en: ". Paste it into the webhook secret at the external provider so that it signs its events.",
    },
    close: { es: "Cerrar", en: "Close" },
    copy: { es: "Copiar", en: "Copy" },
    copied: { es: "Copiado", en: "Copied" },
    // --- ficha de una configuración ---
    badgeEnabled: { es: "activo", en: "active" },
    badgeDisabled: { es: "desactivado", en: "disabled" },
    mappingCountOne: { es: "1 mapeo", en: "1 mapping" },
    mappingCountMany: { es: "{n} mapeos", en: "{n} mappings" },
    lastDelivery: { es: "última entrega:", en: "last delivery:" },
    never: { es: "nunca", en: "never" },
    edit: { es: "Editar", en: "Edit" },
    rotate: { es: "Rotar secreto", en: "Rotate secret" },
    delete: { es: "Eliminar", en: "Delete" },
    showDeliveries: { es: "Ver entregas recientes", en: "Show recent deliveries" },
    hideDeliveries: { es: "Ocultar entregas recientes", en: "Hide recent deliveries" },
    // --- panel de entregas recientes ---
    loadingDeliveries: { es: "Cargando entregas…", en: "Loading deliveries…" },
    deliveriesEmpty: { es: "Sin entregas todavía.", en: "No deliveries yet." },
    verified: { es: "verificado", en: "verified" },
    rejected: { es: "rechazado", en: "rejected" },
    noEventType: { es: "(sin tipo)", en: "(no type)" },
    // --- diálogo de alta / edición ---
    dialogCreateTitle: { es: "Nuevo webhook entrante", en: "New incoming webhook" },
    dialogEditTitle: { es: "Editar webhook entrante", en: "Edit incoming webhook" },
    originLabel: { es: "Origen", en: "Origin" },
    originLockedHint: {
      es: "El origen no se puede cambiar tras crear (la URL pública lo incluye).",
      en: "The origin cannot be changed after creation (the public URL includes it).",
    },
    nameLabel: { es: "Nombre", en: "Name" },
    namePlaceholder: { es: "CI en acme/api", en: "CI on acme/api" },
    enabledLabel: {
      es: "Activo (un webhook desactivado rechaza todos los eventos)",
      en: "Active (a disabled webhook rejects every event)",
    },
    mappingsLabel: { es: "Mapeos evento → acción", en: "Event → action mappings" },
    addMapping: { es: "Añadir mapeo", en: "Add mapping" },
    mappingsEmpty: {
      es: "Sin mapeos. Los eventos verificados se registran pero no disparan ninguna acción.",
      en: "No mappings. Verified events are recorded but trigger no action.",
    },
    removeMapping: { es: "Quitar mapeo", en: "Remove mapping" },
    targetTaskPlaceholder: {
      es: "UUID de la tarea destino",
      en: "UUID of the target task",
    },
    cancel: { es: "Cancelar", en: "Cancel" },
    saving: { es: "Guardando…", en: "Saving…" },
    create: { es: "Crear", en: "Create" },
    saveChanges: { es: "Guardar cambios", en: "Save changes" },
  },
  /**
   * Comandos & runtime del proyecto (`projects/[id]/commands`).
   *
   * Dos allowlists deny-by-default —binarios de `shell_exec` y FQDN de las
   * tools HTTP— más el runtime por defecto. El texto explica en qué consiste
   * «deny-by-default», así que traducirlo a medias es peor que no traducirlo:
   * quien lee la mitad en su idioma da por entendida la otra mitad.
   *
   * `Deny-by-default`, `shell_exec`, `run_*`, `FQDN` y `Allowlist` NO están
   * aquí: son términos del sistema que se escriben igual en los dos idiomas y
   * que el operador busca literales en la documentación.
   */
  projectCommands: {
    breadcrumbCurrent: { es: "Comandos & runtime", en: "Commands & runtime" },
    title: { es: "Comandos & runtime", en: "Commands & runtime" },
    description: {
      es: "Autoriza qué comandos del stack pueden lanzar los agentes y elige el runtime de ejecución.",
      en: "Authorise which stack commands the agents may run, and pick the execution runtime.",
    },
    loadError: {
      es: "No se pudo cargar la configuración del proyecto",
      en: "The project configuration could not be loaded",
    },
    // --- allowlist de comandos ---
    commandsTitle: { es: "Comandos autorizados", en: "Allowed commands" },
    privilegedBadge: { es: "Privilegiada", en: "Privileged" },
    commandsHint: {
      es: "solo ejecuta los binarios de esta lista. Una lista vacía significa que no puede ejecutar nada. Usa los presets por stack o añade comandos uno a uno.",
      en: "only runs the binaries in this list. An empty list means it can run nothing at all. Use the stack presets, or add commands one by one.",
    },
    presetsLabel: { es: "Presets por stack", en: "Stack presets" },
    presetPhp: { es: "PHP", en: "PHP" },
    presetNode: { es: "Node", en: "Node" },
    presetDotnet: { es: ".NET", en: ".NET" },
    presetPython: { es: "Python", en: "Python" },
    presetRead: { es: "Lectura", en: "Read-only" },
    commandsEmptyBefore: {
      es: "Sin comandos autorizados.",
      en: "No allowed commands.",
    },
    commandsEmptyAfter: {
      es: "no podrá ejecutar nada hasta que añadas alguno.",
      en: "will not be able to run anything until you add one.",
    },
    removeChip: { es: "Quitar {name}", en: "Remove {name}" },
    addCommandLabel: { es: "Añadir comando", en: "Add command" },
    addCommandPlaceholder: { es: "p. ej. composer", en: "e.g. composer" },
    add: { es: "Añadir", en: "Add" },
    addCommandHintBefore: {
      es: "Usa el basename del binario (",
      en: "Use the basename of the binary (",
    },
    addCommandHintAfter: {
      es: ") o una ruta relativa al workspace (",
      en: ") or a path relative to the workspace (",
    },
    // --- allowlist de dominios ---
    domainsTitle: { es: "Dominios de red autorizados", en: "Allowed network domains" },
    domainsHintBefore: {
      es: "las tools HTTP del agente (",
      en: "the agent HTTP tools (",
    },
    domainsHintAfter: {
      es: ", descargas) solo alcanzan estos FQDN. Una lista vacía significa que el agente no puede salir a la red.",
      en: ", downloads) can only reach these FQDNs. An empty list means the agent cannot reach the network at all.",
    },
    domainsAllowlistLabel: { es: "Allowlist de dominios", en: "Domain allowlist" },
    domainsEmpty: {
      es: "Sin dominios autorizados: las tools HTTP no pueden salir a la red.",
      en: "No allowed domains: the HTTP tools cannot reach the network.",
    },
    addDomainLabel: { es: "Añadir dominio", en: "Add domain" },
    addDomainPlaceholder: { es: "p. ej. api.github.com", en: "e.g. api.github.com" },
    addDomainHintBefore: { es: "FQDN exacto (", en: "Exact FQDN (" },
    addDomainHintAfter: {
      es: "), sin esquema ni ruta.",
      en: "), with no scheme and no path.",
    },
    // --- runtime por defecto ---
    runtimeTitle: { es: "Runtime por defecto", en: "Default runtime" },
    runtimeHintBefore: {
      es: "El runtime template en el que se ejecutan los",
      en: "The runtime template in which the",
    },
    runtimeHintMiddle: {
      es: "(tests, lint, build…). Déjalo",
      en: "run (tests, lint, build…). Leave it",
    },
    runtimeHintEmphasis: { es: "vacío", en: "empty" },
    runtimeHintAfter: {
      es: "para usar el runtime por defecto de cada tool (backward-compatible).",
      en: "to use the default runtime of each tool (backward-compatible).",
    },
    runtimeTemplateLabel: { es: "Runtime template", en: "Runtime template" },
    runtimeNone: {
      es: "Sin runtime por defecto (defaults por-tool)",
      en: "No default runtime (per-tool defaults)",
    },
    runtimeCatalogError: {
      es: "No se pudo cargar el catálogo de runtimes.",
      en: "The runtime catalog could not be loaded.",
    },
    // --- guardado ---
    saving: { es: "Guardando…", en: "Saving…" },
    saveChanges: { es: "Guardar cambios", en: "Save changes" },
    saved: { es: "Guardado", en: "Saved" },
    saveError: { es: "Error al guardar", en: "Could not save" },
  },
  /**
   * `app/admin/assistant/*` — el asistente personal (chat + identidad).
   *
   * Aqui vive tambien lo que estaba cableado en `lib/assistant.ts`, un modulo
   * PURO: las ocho etiquetas y descripciones del catalogo de herramientas y los
   * cinco mensajes de validacion del formulario. Ninguno de los dos grupos
   * estaba en un atributo ni en un ternario, asi que las dos guardas veian el
   * modulo a cero teniendo ~30 textos sin traducir.
   *
   * El NOMBRE del asistente no esta aqui: lo elige el operador y viaja en la
   * identidad (`AssistantIdentity.name`).
   */
  assistant: {
    title: { es: "Asistente personal", en: "Personal assistant" },
    description: {
      es: "Pregunta por el estado global de tu tenant: proyectos, planes, actividad, presupuesto y carga de agentes.",
      en: "Ask about your tenant at a glance: projects, plans, activity, budget and agent workload.",
    },
    voiceMode: { es: "Modo voz", en: "Voice mode" },
    voiceClose: { es: "Cerrar voz", en: "Close voice" },
    identityLink: { es: "Identidad", en: "Identity" },
    threadLabel: { es: "Hilo:", en: "Thread:" },
    threadNew: { es: "Nuevo hilo", en: "New thread" },
    emptyTitle: { es: "Empieza una conversación", en: "Start a conversation" },
    emptyDescription: {
      es: "Por ejemplo: «¿Qué planes tengo pendientes de aprobación?»",
      en: "For example: \u201CWhich plans are waiting for my approval?\u201D",
    },
    thinking: { es: "Pensando…", en: "Thinking…" },
    thinkingWith: { es: "Pensando… ({note})", en: "Thinking… ({note})" },
    progressRound: { es: "ronda {n}{tools}", en: "round {n}{tools}" },
    inputAria: { es: "Mensaje para el asistente", en: "Message for the assistant" },
    inputPlaceholder: { es: "Escribe tu pregunta…", en: "Type your question…" },
    send: { es: "Enviar", en: "Send" },
    roundsOne: { es: "1 ronda", en: "1 round" },
    roundsMany: { es: "{n} rondas", en: "{n} rounds" },
    noAccessTitle: { es: "Asistente no disponible", en: "Assistant not available" },
    noAccessMember: {
      es: "El asistente personal es exclusivo para administradores del tenant y debe estar habilitado para tu organización.",
      en: "The personal assistant is only for tenant administrators, and it must be enabled for your organization.",
    },
    noAccessDisabled: {
      es: "El asistente está deshabilitado para tu organización. Como administrador del tenant puedes habilitarlo en Ajustes.",
      en: "The assistant is disabled for your organization. As a tenant administrator you can enable it in settings.",
    },
    goToSettings: { es: "Ir a Ajustes", en: "Go to settings" },
    settingsTitle: { es: "Identidad del asistente", en: "Assistant identity" },
    settingsDescription: {
      es: "Personaliza el nombre, el tono, el idioma y las herramientas de tu asistente personal.",
      en: "Customize your personal assistant\u2019s name, tone, language and tools.",
    },
    goToChat: { es: "Ir al chat", en: "Go to chat" },
    enabledLabel: { es: "Asistente habilitado", en: "Assistant enabled" },
    enabledHelp: {
      es: "Activa el asistente personal para tu organización. Mientras esté desactivado, nadie de tu tenant podrá usarlo y esta configuración permanece bloqueada.",
      en: "Turn the personal assistant on for your organization. While it is off nobody in your tenant can use it and this configuration stays locked.",
    },
    on: { es: "Activado", en: "On" },
    off: { es: "Desactivado", en: "Off" },
    configTitle: { es: "Configuración", en: "Configuration" },
    loadingIdentity: { es: "Cargando identidad…", en: "Loading identity…" },
    locked: {
      es: "Habilita el asistente para configurarlo.",
      en: "Enable the assistant to configure it.",
    },
    fieldName: { es: "Nombre", en: "Name" },
    namePlaceholder: { es: "Asistente", en: "Assistant" },
    fieldAvatar: { es: "URL del avatar (opcional)", en: "Avatar URL (optional)" },
    fieldTone: { es: "Tono", en: "Tone" },
    tonePlaceholder: { es: "profesional y conciso", en: "professional and concise" },
    fieldLanguage: { es: "Idioma", en: "Language" },
    fieldSystemPrompt: {
      es: "Instrucciones adicionales (opcional)",
      en: "Extra instructions (optional)",
    },
    systemPromptPlaceholder: {
      es: "Sustituye el cuerpo del prompt por defecto. La identidad (nombre, tono, idioma) se conserva.",
      en: "Replaces the body of the default prompt. The identity (name, tone, language) is kept.",
    },
    toolsLegend: { es: "Herramientas disponibles", en: "Available tools" },
    toolsHelp: {
      es: "Datos de solo lectura que el asistente puede consultar para responderte.",
      en: "Read-only data the assistant may look up in order to answer you.",
    },
    identitySaved: { es: "Identidad guardada.", en: "Identity saved." },
    saving: { es: "Guardando…", en: "Saving…" },
    save: { es: "Guardar", en: "Save" },
    /* Validación del formulario — antes literales de `lib/assistant.ts`. */
    errorNameRequired: { es: "El nombre es obligatorio.", en: "The name is required." },
    errorNameTooLong: {
      es: "El nombre no puede superar {max} caracteres.",
      en: "The name cannot be longer than {max} characters.",
    },
    errorToneRequired: { es: "El tono es obligatorio.", en: "The tone is required." },
    errorToneTooLong: {
      es: "El tono no puede superar {max} caracteres.",
      en: "The tone cannot be longer than {max} characters.",
    },
    errorAvatarTooLong: {
      es: "La URL no puede superar {max} caracteres.",
      en: "The URL cannot be longer than {max} characters.",
    },
    errorPromptTooLong: {
      es: "El prompt no puede superar {max} caracteres.",
      en: "The prompt cannot be longer than {max} characters.",
    },
    errorLanguage: { es: "Idioma no soportado.", en: "Unsupported language." },
    /* Catálogo de herramientas — antes literales de `lib/assistant.ts`. */
    toolProjectsLabel: { es: "Estado de proyectos", en: "Project status" },
    toolProjectsDescription: {
      es: "Conteo y estado consolidado de todos los proyectos del tenant.",
      en: "Counts and consolidated status of every project in the tenant.",
    },
    toolPlansLabel: { es: "Resumen de planes", en: "Plan summary" },
    toolPlansDescription: {
      es: "Planes cross-proyecto agrupados por estado, incluyendo los pendientes de aprobación.",
      en: "Cross-project plans grouped by status, including those awaiting approval.",
    },
    toolActivityLabel: { es: "Actividad reciente", en: "Recent activity" },
    toolActivityDescription: {
      es: "Tareas no terminales más recientes y total de tareas abiertas del tenant.",
      en: "Most recent non-terminal tasks and the tenant\u2019s total of open tasks.",
    },
    toolBudgetLabel: { es: "Estado de presupuesto", en: "Budget status" },
    toolBudgetDescription: {
      es: "Gasto del periodo actual frente al presupuesto del tenant y sus proyectos.",
      en: "Current-period spend against the budget of the tenant and its projects.",
    },
    toolWorkloadLabel: { es: "Carga de agentes humanos", en: "Human agent workload" },
    toolWorkloadDescription: {
      es: "Tareas humanas activas y sesiones de trabajo de un usuario esta semana.",
      en: "Active human tasks and a user\u2019s work sessions this week.",
    },
    toolPendingLabel: {
      es: "Asignaciones humanas pendientes",
      en: "Pending human assignments",
    },
    toolPendingDescription: {
      es: "Tareas humanas sin aceptar desde hace más de N horas (por defecto 24h).",
      en: "Human tasks unaccepted for more than N hours (24h by default).",
    },
    toolKnowledgeLabel: { es: "Buscar en el conocimiento", en: "Search the knowledge" },
    toolKnowledgeDescription: {
      es: "Busca pasajes relevantes en las bases de conocimiento del tenant (documentación, guías).",
      en: "Finds relevant passages in the tenant\u2019s knowledge bases (documentation, guides).",
    },
    toolRememberLabel: { es: "Recordar sobre ti", en: "Remember about you" },
    toolRememberDescription: {
      es: "Deja que el asistente guarde datos personales duraderos (tu nombre, preferencias, gustos) y los recuerde en futuras conversaciones.",
      en: "Lets the assistant store durable personal facts (your name, preferences, tastes) and recall them in later conversations.",
    },
  },
  /**
   * `app/admin/assistant/settings/model-cards.tsx` — las dos tarjetas de modelo.
   *
   * Namespace aparte del de `assistant` porque son DOS superficies con dueños
   * distintos: la de arriba la administra un Tenant Admin y la de abajo sólo un
   * System Admin. Los nombres de proveedor y de modelo NO estan aqui: son
   * identificadores del catalogo (mismo criterio que `ollama`).
   */
  assistantModel: {
    tenantTitle: { es: "Modelo LLM", en: "LLM model" },
    tenantLocked: {
      es: "Habilita el asistente para elegir su modelo.",
      en: "Enable the assistant to choose its model.",
    },
    loading: { es: "Cargando modelo…", en: "Loading model…" },
    effectiveOverride: {
      es: "Modelo actual (override del tenant):",
      en: "Current model (tenant override):",
    },
    effectiveInherited: {
      es: "Heredando el modelo por defecto de la plataforma:",
      en: "Inheriting the platform default model:",
    },
    effectiveNone: {
      es: "No hay modelo configurado. El asistente no responderá hasta que elijas uno o el System Admin configure un modelo por defecto.",
      en: "No model configured. The assistant will not answer until you pick one or a System Admin sets a default.",
    },
    noProvidersTenant: {
      es: "No hay proveedores LLM activos. Pide a un System Admin que configure uno.",
      en: "There are no active LLM providers. Ask a System Admin to configure one.",
    },
    noProvidersPlatform: {
      es: "No hay proveedores LLM activos. Configura uno antes de fijar un modelo por defecto.",
      en: "There are no active LLM providers. Configure one before setting a default model.",
    },
    saved: { es: "Modelo guardado.", en: "Model saved." },
    backToDefault: { es: "Volver al modelo por defecto", en: "Back to the default model" },
    saving: { es: "Guardando…", en: "Saving…" },
    saveModel: { es: "Guardar modelo", en: "Save model" },
    platformTitle: {
      es: "Modelo por defecto de la plataforma",
      en: "Platform default model",
    },
    platformDescription: {
      es: "El modelo que usan los asistentes de los tenants que no han elegido uno propio. Solo lo configura un System Admin.",
      en: "The model used by the assistants of tenants that have not picked their own. Only a System Admin configures it.",
    },
    platformCurrent: { es: "Default actual:", en: "Current default:" },
    platformInvalid: {
      es: " (no válido: el proveedor o el modelo ya no existen)",
      en: " (invalid: the provider or the model no longer exists)",
    },
    platformNone: {
      es: "Sin modelo por defecto configurado.",
      en: "No default model configured.",
    },
    platformSaved: { es: "Modelo por defecto guardado.", en: "Default model saved." },
    clearDefault: { es: "Quitar default", en: "Clear default" },
    saveDefault: { es: "Guardar default", en: "Save default" },
    fieldProvider: { es: "Proveedor", en: "Provider" },
    pickProvider: { es: "— Selecciona un proveedor —", en: "— Select a provider —" },
    fieldModel: { es: "Modelo", en: "Model" },
    pickProviderFirst: {
      es: "— Elige primero un proveedor —",
      en: "— Choose a provider first —",
    },
    noModels: {
      es: "— Sin modelos (sincronízalos en Proveedores LLM) —",
      en: "— No models (sync them in LLM providers) —",
    },
    pickModel: { es: "— Selecciona un modelo —", en: "— Select a model —" },
    fieldReasoning: { es: "Razonamiento", en: "Reasoning" },
    reasoningOff: { es: "Desactivado", en: "Off" },
  },
  /**
   * Knowledge Bases del proyecto (`projects/[id]/knowledge-bases`).
   *
   * Una cosa que NO está aquí y no es un olvido: el nombre de la KB implícita
   * («Documentos de {proyecto}») es un DATO que se persiste y con el que se
   * hace find-or-create. Traducirlo partiría la idempotencia — quien subiese
   * con el toggle en inglés crearía una KB nueva en vez de reutilizar la del
   * castellano —, así que vive en `implicitKbName()` y se enseña literal.
   */
  projectKbs: {
    breadcrumbCurrent: { es: "Knowledge Bases", en: "Knowledge Bases" },
    title: { es: "Knowledge Bases del proyecto", en: "Project knowledge bases" },
    description: {
      es: "Las KBs granted al proyecto, sus documentos y el progreso de la ingestión.",
      en: "The KBs granted to the project, their documents and the ingestion progress.",
    },
    loading: { es: "Cargando…", en: "Loading…" },
    emptyBefore: {
      es: "Ninguna KB está granted a este proyecto todavía. Concede una desde el",
      en: "No KB is granted to this project yet. Grant one from the",
    },
    emptyLink: { es: "panel de Knowledge Bases", en: "Knowledge Bases panel" },
    emptyParenBefore: { es: "(botón", en: "(the" },
    emptyParenAfter: {
      es: ") — o súbele documentos directamente allí desplegando la KB.",
      en: "button) — or upload documents there directly by expanding the KB.",
    },
    // --- «Añadir conocimiento» en un paso ---
    addKnowledge: { es: "Añadir conocimiento", en: "Add knowledge" },
    addKnowledgeHint: {
      es: "Sube documentos en un paso: van a la KB «{name}» (se crea sola la primera vez, ya activada para este proyecto).",
      en: "Upload documents in one step: they go to the “{name}” KB (created on first use, already enabled for this project).",
    },
    uploadProgress: {
      es: "Subiendo {index}/{total}: {file}",
      en: "Uploading {index}/{total}: {file}",
    },
    ingestingOne: { es: "1 documento en ingesta.", en: "1 document being ingested." },
    ingestingMany: { es: "{n} documentos en ingesta.", en: "{n} documents being ingested." },
    // --- catálogo del tenant con toggle ---
    catalogTitle: { es: "Catálogo de conocimiento", en: "Knowledge catalog" },
    catalogHint: {
      es: "Activa una KB para que este proyecto (y sus agentes) puedan leerla; desactívala para revocar el acceso.",
      en: "Enable a KB so this project (and its agents) can read it; disable it to revoke access.",
    },
    enable: { es: "Activar", en: "Enable" },
    disable: { es: "Desactivar", en: "Disable" },
    // --- ficha de una KB y sus documentos ---
    staleReindex: {
      es: "· reindexado pendiente (plataforma: {model})",
      en: "· reindex pending (platform: {model})",
    },
    uploadDocument: { es: "Subir documento", en: "Upload document" },
    loadingDocuments: { es: "Cargando documentos…", en: "Loading documents…" },
    documentsEmpty: {
      es: "Esta KB aún no tiene documentos.",
      en: "This KB has no documents yet.",
    },
    statusPending: { es: "Pendiente", en: "Pending" },
    statusProcessing: { es: "Procesando", en: "Processing" },
    statusIndexed: { es: "Indexado", en: "Indexed" },
    statusFailed: { es: "Fallido", en: "Failed" },
    progress: { es: "Progreso", en: "Progress" },
    reindexTitle: {
      es: "Reindexar (vuelve a procesar el documento)",
      en: "Reindex (processes the document again)",
    },
    delete: { es: "Eliminar", en: "Delete" },
    // --- diálogo de subida ---
    dialogTitle: { es: "Subir documento a la KB", en: "Upload a document to the KB" },
    fileLabel: { es: "Archivo", en: "File" },
    titleLabel: { es: "Título (opcional)", en: "Title (optional)" },
    titlePlaceholder: {
      es: "Por defecto: nombre del archivo",
      en: "Defaults to the file name",
    },
    cancel: { es: "Cancelar", en: "Cancel" },
    uploading: { es: "Subiendo…", en: "Uploading…" },
    upload: { es: "Subir", en: "Upload" },
  },
  /**
   * Wizard de alta de proyecto (`projects/new`).
   *
   * El paso 3 («Capacidades») NO tiene claves aquí: ya se traducía con
   * `marketplaceDeploy`, que es de quien es el paso. Duplicar sus textos para
   * «tenerlos juntos» sería crear la divergencia que el namespace evita.
   *
   * Una de estas claves nació al revés que las demás: el error de carga de
   * plantillas estaba cableado en INGLÉS («Could not load templates»), o sea
   * que la pantalla ya estaba mitad y mitad antes de tocarla — y en el sentido
   * que nadie mira, porque el idioma por defecto es el castellano.
   */
  projectWizard: {
    titleStep1: {
      es: "Crear proyecto — elige plantilla",
      en: "New project — pick a template",
    },
    titleStep2: { es: "Crear proyecto — personaliza", en: "New project — customise" },
    titleStep3Prefix: { es: "Crear proyecto — ", en: "New project — " },
    stepOf: { es: "Paso {step} de {total}.", en: "Step {step} of {total}." },
    cancel: { es: "Cancelar", en: "Cancel" },
    // --- paso 1: plantilla ---
    blankTitle: { es: "Proyecto en blanco", en: "Blank project" },
    blankHint: {
      es: "Empieza sin plantilla. No se concede ninguna base de conocimiento por defecto.",
      en: "Start with no template. No knowledge base is granted by default.",
    },
    blankStart: { es: "Empezar en blanco", en: "Start blank" },
    loadingTemplates: { es: "Cargando plantillas…", en: "Loading templates…" },
    templatesError: {
      es: "No se pudieron cargar las plantillas:",
      en: "Could not load templates:",
    },
    useTemplate: { es: "Usar plantilla", en: "Use template" },
    // --- paso 2: detalles ---
    detailsTitle: { es: "Detalles del proyecto", en: "Project details" },
    nameLabel: { es: "Nombre", en: "Name" },
    descriptionLabel: { es: "Descripción", en: "Description" },
    runtimeLabel: { es: "Runtime por defecto", en: "Default runtime" },
    runtimeNone: {
      es: "Sin runtime por defecto (defaults por-tool)",
      en: "No default runtime (per-tool defaults)",
    },
    runtimeError: {
      es: "No se pudo cargar el catálogo de runtimes.",
      en: "The runtime catalog could not be loaded.",
    },
    /**
     * La plantilla declara un runtime que el catálogo NO sirve. El id se
     * conserva —caer a «sin runtime» sería elegir por el operador el valor
     * peligroso: «sin runtime» es `python-pytest` (H1)— y se avisa para que
     * decida él. Sólo se dice cuando el catálogo ha CONTESTADO: no saber qué
     * sirve no es saber que ese runtime no está.
     */
    runtimeUnknown: {
      es: "El runtime «{id}» que declara la plantilla no está en el catálogo de la plataforma. Se conserva tal cual: revísalo antes de crear el proyecto o elige otro.",
      en: 'The runtime "{id}" declared by the template is not in the platform catalog. It is kept as-is: check it before creating the project, or pick another one.',
    },
    teamLabel: { es: "Equipo", en: "Team" },
    teamNone: { es: "Sin equipo", en: "No team" },
    teamHint: {
      es: "El equipo gobierna qué agentes ejecutan las tareas y la política de memoria. También puedes asignarlo/cambiarlo luego desde la ficha del proyecto.",
      en: "The team governs which agents run the tasks, and the memory policy. You can also assign or change it later from the project page.",
    },
    applyKbGrants: {
      es: "Conceder las bases de conocimiento de la plantilla",
      en: "Grant the knowledge bases of the template",
    },
    applyKbGrantsHint: {
      es: "Si lo desmarcas, el proyecto adopta la plantilla pero no recibe ninguna KB por defecto.",
      en: "If you clear it, the project adopts the template but receives no KB by default.",
    },
    changeTemplate: { es: "Cambiar plantilla", en: "Change template" },
    back: { es: "Volver", en: "Back" },
    creating: { es: "Creando…", en: "Creating…" },
    create: { es: "Crear proyecto", en: "Create project" },
    // --- panel de vista previa ---
    previewTitle: { es: "Vista previa", en: "Preview" },
    previewTemplate: { es: "Plantilla", en: "Template" },
    previewBlank: {
      es: "Proyecto en blanco (sin plantilla)",
      en: "Blank project (no template)",
    },
    forkTeam: {
      es: "Personalizar el equipo para este proyecto",
      en: "Customise the team for this project",
    },
    forkTeamHint: {
      es: "Crea una copia editable del equipo (agentes propios del proyecto). Si no, se referencia el equipo de la plantilla (compartido; no editable si es built-in).",
      en: "Creates an editable copy of the team (agents owned by the project). Otherwise the team of the template is referenced (shared; not editable if it is built-in).",
    },
    // H6 del recorrido E2E (2026-08-29): la plantilla trae un equipo de
    // PLATAFORMA, cuyos agentes son del tenant `Platform`. El chat y el
    // despacho filtran por el tenant del proyecto, así que referenciarlo deja
    // el proyecto con cero agentes utilizables. La copia deja de ser opcional.
    forkTeamRequired: {
      es: "Este equipo es de la plataforma: sus agentes pertenecen al tenant Platform y tu proyecto no puede usarlos. Por eso el proyecto se crea con su propia copia del equipo; sin ella no podría planificar ni ejecutar tareas.",
      en: "This is a platform team: its agents belong to the Platform tenant and your project cannot use them. That is why the project is created with its own copy of the team; without it the project could neither plan nor run tasks.",
    },
    previewPolicy: { es: "Política humana", en: "Human policy" },
    previewRepository: { es: "Repositorio", en: "Repository" },
  },
  /**
   * `components/voice/voice-call-shell.tsx` — la videollamada de voz.
   *
   * Namespace propio y no dentro de `assistant` porque la shell la comparten el
   * asistente personal y el córtex: meterlo en `assistant` obligaría al córtex a
   * pedir textos de un módulo que no es el suyo.
   *
   * Dos textos de aquí no estaban en un atributo ni en un ternario, sino en un
   * TIPO: `gender: "Mujer" | "Hombre"` era castellano cableado en la union de
   * literales de `VoiceOption`, y el selector de voz decía «Mujer · Dora» con el
   * toggle en EN. Cuarto ejemplo del mismo aviso del plan: las guardas miden su
   * patrón, no la deuda.
   *
   * Los NOMBRES de las voces (Dora, Alex, Heart…) no están aquí: son los ids del
   * catálogo de Kokoro que el servidor valida contra su allowlist.
   */
  voiceCall: {
    statusLobby: { es: "Listo para llamar", en: "Ready to call" },
    statusConnecting: { es: "Conectando…", en: "Connecting…" },
    statusReady: {
      es: "En llamada — mantén pulsado el micro para hablar",
      en: "In call — press and hold the mic to talk",
    },
    statusRecording: { es: "Escuchándote…", en: "Listening…" },
    statusThinking: { es: "Pensando…", en: "Thinking…" },
    statusSpeaking: { es: "Hablando…", en: "Speaking…" },
    statusError: { es: "Error", en: "Error" },
    genderFemale: { es: "Mujer", en: "Female" },
    genderMale: { es: "Hombre", en: "Male" },
    langSpanish: { es: "Español", en: "Spanish" },
    langEnglishUs: { es: "Inglés (EE. UU.)", en: "English (US)" },
    langEnglishUk: { es: "Inglés (Reino Unido)", en: "English (UK)" },
    errorPlayback: {
      es: "No se pudo reproducir la respuesta de voz.",
      en: "The voice answer could not be played.",
    },
    errorAutoplay: {
      es: "El navegador bloqueó la reproducción — pulsa «Iniciar llamada» de nuevo.",
      en: "The browser blocked playback — press “Start call” again.",
    },
    errorMic: {
      es: "Micrófono no disponible o permiso denegado.",
      en: "Microphone unavailable or permission denied.",
    },
    you: { es: "Tú", en: "You" },
    thinkingOf: { es: "{name} está pensando…", en: "{name} is thinking…" },
    start: { es: "Iniciar llamada", en: "Start call" },
    back: { es: "Volver", en: "Back" },
    talkRelease: { es: "Suelta para enviar", en: "Release to send" },
    talkHold: { es: "Mantén pulsado para hablar", en: "Press and hold to talk" },
    voiceAria: { es: "Voz", en: "Voice" },
    hangup: { es: "Colgar", en: "Hang up" },
  },
  /**
   * Chat del proyecto (`projects/[id]/chat`): la pantalla, el selector de modo,
   * el feed, el composer y el botón de generar plan.
   *
   * Los tres modos built-in traían YA sus dos caras (`labelEs`/`labelEn` en
   * `chat-types.ts`)… y el selector pintaba siempre `labelEs`. Es el peor caso
   * de los que este plan busca: la traducción existía, estaba escrita, y el
   * render la ignoraba — ninguna de las dos guardas puede ver eso, porque no hay
   * ni ternario ni atributo. Al pasar el catálogo a claves, la cara inglesa deja
   * de poder quedarse sin llamante.
   */
  projectChat: {
    breadcrumbCurrent: { es: "Chat", en: "Chat" },
    loading: { es: "Cargando chat…", en: "Loading chat…" },
    errorTitle: { es: "Error cargando conversaciones", en: "Error loading conversations" },
    noConversationsTitle: {
      es: "No hay conversaciones en este proyecto",
      en: "This project has no conversations",
    },
    startConversation: { es: "Empezar una conversación", en: "Start a conversation" },
    title: { es: "Chat del proyecto", en: "Project chat" },
    defaultDescription: {
      es: "Conversación con el equipo del proyecto",
      en: "A conversation with the project team",
    },
    clearChat: { es: "Vaciar chat", en: "Clear chat" },
    conversationPickerLabel: { es: "Conversación:", en: "Conversation:" },
    newConversation: { es: "Nueva conversación", en: "New conversation" },
    deleteConversation: { es: "Eliminar conversación", en: "Delete conversation" },
    activeMode: { es: "Modo activo:", en: "Active mode:" },
    thinking: { es: "El equipo está pensando…", en: "The team is thinking…" },
    thinkingHint: { es: "(esto puede tardar)", en: "(this may take a while)" },
    confirmClearDescription: {
      es: "Se borrarán todos los mensajes de esta conversación. No se puede deshacer.",
      en: "Every message in this conversation will be deleted. This cannot be undone.",
    },
    confirmClearLabel: { es: "Vaciar", en: "Clear" },
    confirmDeleteDescription: {
      es: "Se eliminará esta conversación y todos sus mensajes. No se puede deshacer.",
      en: "This conversation and all its messages will be deleted. This cannot be undone.",
    },
    confirmDeleteLabel: { es: "Eliminar", en: "Delete" },
    // --- etiquetas del historial (helper puro `lib/conversation-history`) ---
    untitledConversation: { es: "Conversación sin título", en: "Untitled conversation" },
    untitledConversationAt: {
      es: "Conversación · {stamp}",
      en: "Conversation · {stamp}",
    },
    // --- selector de modo ---
    modeSelectorAria: { es: "Modo de chat", en: "Chat mode" },
    modePlanning: { es: "Planning", en: "Planning" },
    modePlanningHint: {
      es: "El equipo construye un plan estructurado",
      en: "The team builds a structured plan",
    },
    modeDiscussion: { es: "Discusión", en: "Discussion" },
    modeDiscussionHint: {
      es: "Ronda abierta de ideas y opiniones",
      en: "An open round of ideas and opinions",
    },
    modeExecution: { es: "Ejecución", en: "Execution" },
    /**
     * El hint decía «El equipo ejecuta tareas del plan aprobado» y el modo NO
     * ejecuta nada (ADR 0162): `chat/responder.py` bifurca sólo en `planning`,
     * y `discussion` y `execution` caen los dos en `_simple_reply` — una única
     * llamada al LLM, sin tools. Lo que este modo hace de verdad es hablar del
     * trabajo; la ejecución se arranca desde el plan, y por eso el texto cita
     * el rótulo exacto de ese botón (`planDetail.lifecycleStart`) en vez de
     * parafrasearlo: así el operador sabe qué buscar y `labels-honesty.test.ts`
     * puede atar los dos textos.
     */
    modeExecutionHint: {
      es: "El equipo coordina y comenta el trabajo; aquí no se ejecuta nada. La ejecución se arranca en el plan, con «Empezar ejecución»",
      en: "The team coordinates and comments on the work; nothing is executed here. Execution is started from the plan, with “Start execution”",
    },
    // --- feed ---
    feedLoading: { es: "Cargando mensajes…", en: "Loading messages…" },
    feedEmpty: {
      es: "La conversación está vacía. Empieza a escribir para comenzar.",
      en: "The conversation is empty. Start typing to begin.",
    },
    summaryTitleOne: {
      es: "🗂️ Resumen de 1 mensaje anterior",
      en: "🗂️ Summary of 1 earlier message",
    },
    summaryTitleMany: {
      es: "🗂️ Resumen de {n} mensajes anteriores",
      en: "🗂️ Summary of {n} earlier messages",
    },
    summaryHide: { es: "ocultar", en: "hide" },
    summaryShow: { es: "ver resumen", en: "show summary" },
    summaryNote: {
      es: "El equipo lee este resumen en lugar de esos mensajes. Los originales siguen más arriba en la conversación.",
      en: "The team reads this summary instead of those messages. The originals are still further up in the conversation.",
    },
    /**
     * Marca del eco optimista (H7, `chat/chat-echo.ts`): el mensaje ya se ve,
     * pero todavía no ha vuelto del servidor. Sin esta marca el eco se haría
     * pasar por mensaje entregado — mentira pequeña, y justo la que hacía dudar
     * al usuario de si había pulsado «Enviar» dos veces.
     */
    sending: { es: "enviando…", en: "sending…" },
    /**
     * El envío que FALLÓ. `postMessage` no tenía `onError` y `onSettled`
     * retiraba el eco pasara lo que pasase: un POST fallido borraba de la
     * pantalla el mensaje recién escrito sin decir nada. El eco se queda, pero
     * deja de decir «enviando…» —que ya no es cierto— y ofrece reintentar.
     */
    sendFailed: { es: "no se pudo enviar", en: "could not be sent" },
    retrySend: { es: "Reintentar", en: "Retry" },
    sendErrorPrefix: {
      es: "No se pudo enviar el mensaje:",
      en: "The message could not be sent:",
    },
    // --- generar plan y composer ---
    generatePlan: { es: "Generar Plan", en: "Generate plan" },
    composerEdit: { es: "Editar", en: "Edit" },
    composerPreview: { es: "Vista previa", en: "Preview" },
    composerNothingToPreview: {
      es: "Sin contenido para previsualizar.",
      en: "Nothing to preview.",
    },
    composerPlaceholder: {
      es: "Escribe un mensaje. Usa @ para mencionar a un agente. Soporta markdown.",
      en: "Type a message. Use @ to mention an agent. Markdown is supported.",
    },
    send: { es: "Enviar", en: "Send" },
  },
  /**
   * `app/admin/notifications/*` — las tres pestañas de configuración.
   *
   * **Lo que NO entra, y es deliberado**: los nombres de transporte
   * (`telegram`, `slack`, `email`) y los `event_type` (`task_blocked`) son el
   * enum del backend, viajan en la API y son lo que el operador busca en los
   * logs. Y las ETIQUETAS de los eventos tampoco están aquí: el backend sirve el
   * catálogo bilingüe (`label_es`/`label_en`, NOTIF-3) y se resuelve con
   * `pickLang`, que es el mismo criterio del ADR 0049 para la taxonomía de
   * tools. Duplicarlas como claves reabriría la divergencia que ese endpoint
   * cerró — y era peor todavía: la matriz leía SIEMPRE `label_es`.
   */
  notifications: {
    title: { es: "Notificaciones", en: "Notifications" },
    description: {
      es: "Configuración de canales y preferencias en 3 capas: plataforma, tenant y usuario.",
      en: "Channel and preference configuration in 3 layers: platform, tenant and user.",
    },
    tabChannels: { es: "Canales", en: "Channels" },
    tabPreferences: { es: "Preferencias", en: "Preferences" },
    tabPlatform: { es: "Plataforma", en: "Platform" },
    /* Pestaña «Plataforma» */
    platformTitle: {
      es: "Transportes habilitados globalmente",
      en: "Globally enabled transports",
    },
    platformHint: {
      es: "Un tenant solo puede configurar canales de los transportes habilitados aquí.",
      en: "A tenant can only configure channels of the transports enabled here.",
    },
    /* Pestaña «Canales» */
    newChannel: { es: "Nuevo canal", en: "New channel" },
    editChannel: { es: "Editar canal", en: "Edit channel" },
    deleteChannel: { es: "Eliminar canal", en: "Delete channel" },
    channelsEmpty: {
      es: "Aún no hay canales configurados. Pulsa «Nuevo canal» para añadir uno.",
      en: "No channels configured yet. Press “New channel” to add one.",
    },
    confirmDelete: {
      es: "¿Eliminar el canal «{name}»?",
      en: "Delete the channel “{name}”?",
    },
    channelActive: { es: "activo", en: "active" },
    channelInactive: { es: "inactivo", en: "inactive" },
    secretPrefix: { es: "secreto: {source}", en: "secret: {source}" },
    noSecret: { es: "sin secreto", en: "no secret" },
    secretSourceVault: { es: "Vault", en: "Vault" },
    secretSourceEncrypted: { es: "cifrado en reposo", en: "encrypted at rest" },
    /* Diálogo de canal */
    fieldScope: { es: "Ámbito", en: "Scope" },
    scopeTenant: { es: "Tenant (compartido)", en: "Tenant (shared)" },
    scopeUser: { es: "Usuario (solo yo)", en: "User (only me)" },
    fieldTransport: { es: "Transporte", en: "Transport" },
    fieldName: { es: "Nombre", en: "Name" },
    fieldConfig: { es: "Config (JSON, sin secretos)", en: "Config (JSON, no secrets)" },
    configInvalid: {
      es: "El config no es un JSON válido.",
      en: "The config is not valid JSON.",
    },
    fieldSecretCreate: { es: "Secreto (opcional)", en: "Secret (optional)" },
    fieldSecretEdit: {
      es: "Secreto (dejar vacío para conservar el actual)",
      en: "Secret (leave empty to keep the current one)",
    },
    secretPlaceholder: {
      es: "token del bot / contraseña / clave",
      en: "bot token / password / key",
    },
    secretHint: {
      es: "Se cifra en reposo antes de guardarse; el sistema nunca lo devuelve en claro.",
      en: "It is encrypted at rest before being stored; the system never returns it in the clear.",
    },
    enabledLabel: { es: "Canal activo", en: "Channel active" },
    cancel: { es: "Cancelar", en: "Cancel" },
    create: { es: "Crear", en: "Create" },
    save: { es: "Guardar", en: "Save" },
    saving: { es: "Guardando…", en: "Saving…" },
    /* Pestaña «Preferencias» */
    routingTitle: { es: "Reglas de enrutado", en: "Routing rules" },
    routingEmpty: {
      es: "Configura al menos un canal para ajustar qué eventos llegan por qué transporte.",
      en: "Configure at least one channel to choose which events arrive through which transport.",
    },
    colEvent: { es: "Evento", en: "Event" },
    ruleYes: { es: "sí", en: "yes" },
    ruleNo: { es: "no", en: "no" },
  },
  /**
   * `app/admin/notifications/inbox/page.tsx` — el histórico de envíos.
   *
   * Namespace aparte porque es otra pantalla con otra ruta, no una pestaña más.
   * Los `status` (`queued`, `dead_letter`…) no se traducen: son el enum del
   * backend y lo que el operador filtra y busca en los logs.
   */
  notificationsInbox: {
    title: { es: "Bandeja de notificaciones", en: "Notification inbox" },
    description: {
      es: "Histórico de notificaciones enviadas a tus canales, con estado y reintento manual.",
      en: "History of the notifications sent to your channels, with status and manual retry.",
    },
    scopeGroupAria: { es: "Ámbito del inbox", en: "Inbox scope" },
    scopeTenant: { es: "Tenant", en: "Tenant" },
    scopePlatform: { es: "Plataforma", en: "Platform" },
    unreadBadge: { es: "{n} sin leer", en: "{n} unread" },
    statusLabel: { es: "Estado", en: "Status" },
    statusAll: { es: "Todos", en: "All" },
    unreadOnly: { es: "Solo sin leer", en: "Unread only" },
    markAllRead: { es: "Marcar todo como leído", en: "Mark all as read" },
    empty: {
      es: "No hay notificaciones que coincidan con el filtro.",
      en: "No notifications match the filter.",
    },
    unreadDot: { es: "sin leer", en: "unread" },
    attempt: { es: "intento {n}", en: "attempt {n}" },
    retry: { es: "Reintentar", en: "Retry" },
    markRead: { es: "Marcar leído", en: "Mark read" },
    range: { es: "{range} de {total}", en: "{range} of {total}" },
    prev: { es: "Anterior", en: "Previous" },
    next: { es: "Siguiente", en: "Next" },
  },
  /**
   * `app/admin/docs/*` — el visor de documentación cross-proyecto.
   *
   * Doce ficheros y ninguno aparecía en la allowlist de ternarios: aquí no había
   * ni uno. La deuda estaba en texto JSX suelto —los seis estados de cada panel
   * (idle, hint, loading, error, vacío, resultados)—, que es la forma que las
   * dos guardas no ven y la que más pesa en una pantalla de este tipo.
   *
   * Los nombres de carpeta canónica (`05-architecture-decisions`) y las refs git
   * (`HEAD~1`) no están aquí: son identificadores del repo.
   */
  docs: {
    breadcrumbHome: { es: "Inicio", en: "Home" },
    title: { es: "Documentación", en: "Documentation" },
    description: {
      es: "Explora la documentación de cada proyecto. Filtra por categoría o tipo, busca en el texto y marca documentos para encontrarlos rápido.",
      en: "Browse each project’s documentation. Filter by category or type, search the text and star docs to find them fast.",
    },
    tabExplore: { es: "Explorar", en: "Explore" },
    tabBookmarks: { es: "Marcadores", en: "Bookmarks" },
    /* Barra lateral */
    sidebarAria: { es: "Árbol de documentación", en: "Documentation tree" },
    projectsHeading: { es: "Proyectos", en: "Projects" },
    projectsLoading: { es: "Cargando proyectos…", en: "Loading projects…" },
    projectsError: {
      es: "No se pudieron cargar los proyectos.",
      en: "The projects could not be loaded.",
    },
    projectsEmpty: {
      es: "No tienes proyectos accesibles.",
      en: "You have no accessible projects.",
    },
    treeLoading: { es: "Cargando árbol…", en: "Loading tree…" },
    treeError: { es: "No se pudo cargar el árbol.", en: "The tree could not be loaded." },
    treeFilteredEmpty: {
      es: "Ningún documento coincide con los filtros.",
      en: "No document matches the filters.",
    },
    treeEmpty: {
      es: "Sin documentos en este proyecto.",
      en: "No documents in this project.",
    },
    /* Buscador */
    searchTabFulltext: { es: "Texto", en: "Text" },
    searchTabSemantic: { es: "Semántica", en: "Semantic" },
    searchPlaceholderFulltext: {
      es: "Buscar en la documentación…",
      en: "Search the documentation…",
    },
    searchPlaceholderSemantic: { es: "Búsqueda semántica…", en: "Semantic search…" },
    searchPlaceholderNoProject: {
      es: "Selecciona un proyecto para buscar",
      en: "Select a project to search",
    },
    searchAria: { es: "Buscar en la documentación", en: "Search the documentation" },
    searchIdle: {
      es: "Selecciona un proyecto en el árbol para buscar en su documentación.",
      en: "Select a project in the tree to search its documentation.",
    },
    searchHint: {
      es: "Escribe al menos {n} caracteres para buscar.",
      en: "Type at least {n} characters to search.",
    },
    searchLoading: { es: "Buscando…", en: "Searching…" },
    searchError: {
      es: "No se pudo completar la búsqueda.",
      en: "The search could not be completed.",
    },
    searchEmpty: {
      es: "Nada coincide con «{query}».",
      en: "Nothing matches “{query}”.",
    },
    hitScoreTitle: { es: "Similitud coseno", en: "Cosine similarity" },
    /* Filtros por faceta */
    filtersHeading: { es: "Filtros", en: "Filters" },
    filtersClear: { es: "Limpiar", en: "Clear" },
    facetCategory: { es: "Categoría", en: "Category" },
    facetType: { es: "Tipo", en: "Type" },
    /* Marcadores */
    recencyAll: { es: "Todos", en: "All" },
    recencyToday: { es: "Hoy", en: "Today" },
    recency7: { es: "7 días", en: "7 days" },
    recency30: { es: "30 días", en: "30 days" },
    bookmarksEmpty: {
      es: "Aún no has marcado documentos. Usa la estrella junto a un documento para guardarlo aquí.",
      en: "You have not starred any document yet. Use the star next to a document to keep it here.",
    },
    bookmarksEmptyWindow: {
      es: "Ningún documento marcado en este periodo.",
      en: "No document starred in this period.",
    },
    bookmarkRemove: { es: "Quitar de marcadores", en: "Remove from bookmarks" },
    bookmarkAdd: { es: "Marcar documento", en: "Star document" },
    /* Panel de lectura */
    viewerEmpty: {
      es: "Selecciona un documento en el árbol de la izquierda para empezar.",
      en: "Select a document in the tree on the left to start.",
    },
    modeRead: { es: "Documento", en: "Document" },
    modeDiff: { es: "Comparar", en: "Compare" },
    contentLoading: { es: "Cargando documento…", en: "Loading document…" },
    docNotFound: {
      es: "El documento no existe o no es accesible.",
      en: "The document does not exist or is not accessible.",
    },
    contentError: {
      es: "No se pudo cargar el documento.",
      en: "The document could not be loaded.",
    },
    tocAria: { es: "Tabla de contenidos", en: "Table of contents" },
    tocHeading: { es: "En esta página", en: "On this page" },
    tocEmpty: { es: "Sin secciones.", en: "No sections." },
    mermaidLoading: { es: "Renderizando diagrama…", en: "Rendering diagram…" },
    /* Comparar versiones */
    diffEmpty: {
      es: "Selecciona un documento para comparar dos versiones.",
      en: "Select a document to compare two versions.",
    },
    diffBaseLabel: { es: "Versión base", en: "Base version" },
    diffBaseAria: { es: "Ref git de la versión base", en: "Git ref of the base version" },
    diffHeadLabel: { es: "Versión nueva", en: "New version" },
    diffHeadAria: { es: "Ref git de la versión nueva", en: "Git ref of the new version" },
    diffSubmit: { es: "Comparar", en: "Compare" },
    diffIdle: {
      es: "Introduce dos referencias git y pulsa «Comparar» para ver los cambios.",
      en: "Enter two git refs and press “Compare” to see the changes.",
    },
    diffLoading: { es: "Calculando diferencias…", en: "Computing the diff…" },
    diffError: {
      es: "No se pudieron calcular las diferencias.",
      en: "The diff could not be computed.",
    },
    diffUnchanged: { es: "No hay diferencias entre", en: "There are no differences between" },
  },
  /**
   * Catálogo de categorías y tipos de documento (`lib/docs-filters.ts`).
   *
   * Namespace propio y COMPARTIDO: lo consumen el panel de facetas y la lista de
   * marcadores desde una constante única. Los VALORES (`05-architecture-decisions`,
   * `adr`) no están aquí — son las carpetas canónicas del repo, no texto.
   */
  docFacets: {
    categoryOverview: { es: "Visión general", en: "Overview" },
    categoryGettingStarted: { es: "Primeros pasos", en: "Getting started" },
    categoryGuides: { es: "Guías", en: "Guides" },
    categoryReference: { es: "Referencia", en: "Reference" },
    categoryAdr: { es: "Decisiones (ADR)", en: "Decisions (ADR)" },
    categoryRunbooks: { es: "Runbooks", en: "Runbooks" },
    categoryChangelog: { es: "Changelog", en: "Changelog" },
    categoryOther: { es: "Otros", en: "Other" },
    typeAdr: { es: "ADR", en: "ADR" },
    typeChangelog: { es: "Changelog", en: "Changelog" },
    typeRunbook: { es: "Runbook", en: "Runbook" },
    typeReadme: { es: "README / índice", en: "README / index" },
    typeDoc: { es: "Documento", en: "Document" },
  },
  /**
   * `app/admin/marketplace/page.tsx` — catálogo, instaladas y compartir.
   *
   * Los `kind`, `trust_level` y `status` de un LISTING no están aquí: son el
   * enum del backend, se muestran tal cual y es lo que el operador ve en la API.
   * Sí lo están las etiquetas de estado de una INSTALACIÓN, porque ahí el panel
   * ya escribía «Habilitada» y no `enabled`.
   */
  marketplace: {
    title: { es: "Marketplace", en: "Marketplace" },
    description: {
      es: "Explora el catálogo, gestiona lo instalado, tus listings privados y los recursos compartidos entre tenants.",
      en: "Browse the catalog, manage what is installed, your private listings and what is shared across tenants.",
    },
    privateLink: { es: "Privadas", en: "Private" },
    publish: { es: "Publicar", en: "Publish" },
    tabCatalog: { es: "Catálogo", en: "Catalog" },
    tabInstalled: { es: "Instaladas", en: "Installed" },
    tabShares: { es: "Compartir", en: "Sharing" },
    catalogEmpty: {
      es: "El catálogo está vacío. Publica tu primera skill o tool interna para empezar.",
      en: "The catalog is empty. Publish your first internal skill or tool to get started.",
    },
    badgePrivate: { es: "privado", en: "private" },
    badgeGlobal: { es: "global", en: "global" },
    calloutTitle: {
      es: "\u00bfTienes una skill o tool interna?",
      en: "Do you have an internal skill or tool?",
    },
    calloutBody: {
      es: "Publícala como listing privado de tu tenant. Solo tu organización la verá; el manifest se valida al publicar.",
      en: "Publish it as a private listing of your tenant. Only your organization will see it; the manifest is validated on publish.",
    },
    calloutCta: { es: "Publicar en el marketplace", en: "Publish to the marketplace" },
    installedEmpty: {
      es: "Este tenant no tiene nada instalado todavía.",
      en: "This tenant has nothing installed yet.",
    },
    installStatusEnabled: { es: "Habilitada", en: "Enabled" },
    installStatusDisabled: { es: "Deshabilitada", en: "Disabled" },
    installStatusRevoked: { es: "Revocada", en: "Revoked" },
    permissions: { es: "Permisos", en: "Permissions" },
    revoke: { es: "Revocar", en: "Revoke" },
    uninstall: { es: "Desinstalar", en: "Uninstall" },
    // `task_mk_00`: instalar desde el catálogo. Hasta 2026-09-03 el panel no
    // sabía crear una instalación: sólo listarlas.
    install: { es: "Instalar", en: "Install" },
    installing: { es: "Instalando…", en: "Installing…" },
    installed: { es: "Instalada", en: "Installed" },
    installStatusAnalyzing: { es: "en análisis", en: "under analysis" },
    installStatusBlocked: { es: "bloqueada", en: "blocked" },
    shareCardTitle: {
      es: "Compartir un listing privado con otro tenant",
      en: "Share a private listing with another tenant",
    },
    shareExplainer: {
      es: "Compartir es opt-in y explícito: el tenant destino ve e instala el listing solo mediante este grant, y el System Admin audita cada acción. Revocar retira la visibilidad de inmediato.",
      en: "Sharing is opt-in and explicit: the target tenant sees and installs the listing only through this grant, and a System Admin audits every action. Revoking removes visibility immediately.",
    },
    shareListingLabel: { es: "Listing privado", en: "Private listing" },
    sharePickListing: { es: "Selecciona un listing…", en: "Select a listing…" },
    shareNoPrivateBefore: {
      es: "No tienes listings privados que compartir. Publica uno en",
      en: "You have no private listings to share. Publish one in",
    },
    shareNoPrivateLink: { es: "Marketplace privado", en: "Private marketplace" },
    shareTargetLabel: { es: "Tenant destino (UUID)", en: "Target tenant (UUID)" },
    shareSubmit: { es: "Compartir", en: "Share" },
    shareSubmitting: { es: "Compartiendo…", en: "Sharing…" },
    sharesTitle: {
      es: "Grants activos creados por tu tenant",
      en: "Active grants created by your tenant",
    },
    sharesEmpty: {
      es: "Por defecto no compartes nada. Crea un grant arriba para compartir un listing privado.",
      en: "By default you share nothing. Create a grant above to share a private listing.",
    },
    revokeShare: { es: "Revocar share", en: "Revoke share" },
  },
  /**
   * `app/admin/marketplace/private/page.tsx` — publicar un listing del tenant.
   *
   * Los manifests de EJEMPLO no están aquí y es deliberado: son contenido que el
   * operador pega y edita —YAML que el backend parsea—, no texto de UI. Y la
   * ayuda de formato lista NOMBRES DE CAMPO del manifest (`entrypoint`,
   * `network_policy`): sólo se traduce la glosa entre paréntesis.
   */
  marketplacePrivate: {
    title: { es: "Marketplace privado", en: "Private marketplace" },
    description: {
      es: "Publica las skills y tools internas de tu tenant como listings privados. Solo tu tenant las ve; el manifest se valida al publicar.",
      en: "Publish your tenant\u2019s internal skills and tools as private listings. Only your tenant sees them; the manifest is validated on publish.",
    },
    backToCatalog: { es: "Volver al catálogo", en: "Back to the catalog" },
    publishCardTitle: { es: "Publicar listing privado", en: "Publish a private listing" },
    kindLabel: { es: "Tipo", en: "Kind" },
    kindSkill: { es: "Skill (SKILL.md)", en: "Skill (SKILL.md)" },
    kindTool: { es: "Tool (manifest YAML)", en: "Tool (YAML manifest)" },
    kindMcp: { es: "MCP server (manifest YAML)", en: "MCP server (YAML manifest)" },
    helpSkillSummary: {
      es: "Un SKILL.md es Markdown con un frontmatter YAML (entre líneas ---) seguido del cuerpo en prosa.",
      en: "A SKILL.md is Markdown with a YAML frontmatter (between --- lines) followed by the prose body.",
    },
    helpToolSummary: {
      es: "Un tool es un documento YAML plano (sin cuerpo Markdown).",
      en: "A tool is a flat YAML document (no Markdown body).",
    },
    helpMcpSummary: {
      es: "Un MCP server usa el mismo YAML que un tool, con kind: mcp_server (debe coincidir con el tipo elegido).",
      en: "An MCP server uses the same YAML as a tool, with kind: mcp_server (it must match the kind you chose).",
    },
    fieldsRequired: { es: "Campos obligatorios", en: "Required fields" },
    fieldsOptional: { es: "Opcionales", en: "Optional" },
    fieldVersion: { es: "version (semver, p. ej. 1.0.0)", en: "version (semver, e.g. 1.0.0)" },
    fieldVersionShort: { es: "version (semver)", en: "version (semver)" },
    fieldEntrypoint: { es: "entrypoint (módulo:función)", en: "entrypoint (module:function)" },
    fieldDependencies: { es: "dependencies (lista)", en: "dependencies (list)" },
    fieldExamples: {
      es: "examples (lista de { title, prompt })",
      en: "examples (list of { title, prompt })",
    },
    fieldKindDefault: { es: "kind (por defecto tool)", en: "kind (defaults to tool)" },
    helpDoubtsBefore: {
      es: "\u00bfDudas con el formato? Consulta la",
      en: "Unsure about the format? See the",
    },
    helpDoubtsLink: { es: "guía de publicación", en: "publishing guide" },
    authorLabel: { es: "Autor (opcional)", en: "Author (optional)" },
    authorPlaceholder: { es: "Equipo Plataforma", en: "Platform Team" },
    manifestLabel: { es: "Manifest", en: "Manifest" },
    useExample: { es: "Usar ejemplo", en: "Use example" },
    exampleHint: {
      es: "Pulsa «Usar ejemplo» para insertar un manifest {kind} válido y editarlo desde ahí.",
      en: "Press \u201CUse example\u201D to insert a valid {kind} manifest and edit from there.",
    },
    versionHint: {
      es: "El nombre y la versión se leen del manifest. Una versión duplicada se rechaza.",
      en: "The name and version are read from the manifest. A duplicate version is rejected.",
    },
    publish: { es: "Publicar", en: "Publish" },
    publishing: { es: "Publicando…", en: "Publishing…" },
    publishFailedTitle: { es: "No se pudo publicar", en: "Could not publish" },
    publishFailedHint: {
      es: "Corrige el manifest según el mensaje y vuelve a publicar. No se ha creado ningún listing.",
      en: "Fix the manifest per the message and publish again. No listing was created.",
    },
    listTitle: { es: "Catálogo privado del tenant", en: "The tenant\u2019s private catalog" },
    listEmpty: {
      es: "Este tenant todavía no ha publicado ningún listing privado.",
      en: "This tenant has not published any private listing yet.",
    },
    startWithExample: { es: "Empezar con un ejemplo", en: "Start from an example" },
    unpublish: { es: "Despublicar", en: "Unpublish" },
  },
  /**
   * `app/admin/marketplace/installations/[id]/permissions/page.tsx`.
   *
   * Los VALORES de `network_policy` (`none`, `restricted`, `open`) van sin
   * traducir dentro de la ayuda porque son el enum que se guarda y que el
   * operador ve en el audit log — el mismo criterio que `guardrail_type`.
   */
  marketplaceConsent: {
    title: { es: "Consentimiento de permisos", en: "Permission consent" },
    description: {
      es: "Aprueba o deniega cada permiso que esta tool/skill solicita. La instalación no se habilita hasta que todos los permisos requeridos estén concedidos.",
      en: "Approve or deny each permission this tool/skill requests. The installation is not enabled until every required permission is granted.",
    },
    installStatusEnabled: { es: "Habilitada", en: "Enabled" },
    installStatusDisabled: {
      es: "Deshabilitada (pendiente de consentimiento)",
      en: "Disabled (awaiting consent)",
    },
    installStatusRevoked: { es: "Revocada", en: "Revoked" },
    permAllowedDomains: { es: "Dominios permitidos", en: "Allowed domains" },
    permAllowedPaths: { es: "Rutas permitidas", en: "Allowed paths" },
    permNetworkPolicy: { es: "Política de red", en: "Network policy" },
    helpAllowedDomains: {
      es: "La tool solo podrá hacer peticiones HTTP a estos dominios, siempre a través del proxy de salida de la plataforma.",
      en: "The tool will only be able to make HTTP requests to these domains, always through the platform egress proxy.",
    },
    helpAllowedPaths: {
      es: "Rutas del workspace a las que la tool tendrá acceso.",
      en: "Workspace paths the tool will have access to.",
    },
    helpNetworkPolicy: {
      es: "none = sin red. restricted = red interna sin salida. open = salida a internet SOLO a través del proxy con allowlist de la plataforma (registries públicos de paquetes y git) — nunca internet crudo; cada uso queda registrado en el audit log.",
      en: "none = no network. restricted = internal network with no egress. open = internet egress ONLY through the platform\u2019s allowlisted proxy (public package registries and git) \u2014 never raw internet; every use is recorded in the audit log.",
    },
    stateGranted: { es: "Concedido", en: "Granted" },
    stateDenied: { es: "Denegado", en: "Denied" },
    statePending: { es: "Pendiente", en: "Pending" },
    notRequiredBefore: { es: "Este listing es", en: "This listing is" },
    notRequiredAfter: {
      es: ": no requiere consentimiento granular (fricción mínima). Los permisos se aplican según la política de confianza.",
      en: ": it needs no granular consent (minimum friction). Permissions are applied per the trust policy.",
    },
    empty: {
      es: "Este listing no solicita ningún permiso.",
      en: "This listing requests no permissions.",
    },
    approve: { es: "Aprobar", en: "Approve" },
    deny: { es: "Denegar", en: "Deny" },
    hintNone: {
      es: "Selecciona Aprobar/Denegar en cada permiso y guarda las decisiones.",
      en: "Choose Approve/Deny on each permission and save the decisions.",
    },
    hintStaged: {
      es: "{n} decisión(es) sin guardar (marcadas con *).",
      en: "{n} unsaved decision(s) (marked with *).",
    },
    saving: { es: "Guardando…", en: "Saving…" },
    submit: { es: "Guardar decisiones", en: "Save decisions" },
  },

  /**
   * El HUB del proyecto (`app/admin/projects/[id]/page.tsx`) — prod-16
   * `task_prod16_03`.
   *
   * Sólo el MARCO: cabecera, rejilla de sub-secciones y los dos diálogos. Las
   * seis piezas que la página monta dentro tienen namespace propio
   * (`projectGit`, `projectReviewPreview`, `projectRuntimeServices`,
   * `projectGovernance`, `previewLauncher`), porque son componentes con vida
   * propia y una de ellas la comparte la ficha del plan.
   */
  projectHub: {
    fallbackTitle: { es: "Proyecto", en: "Project" },
    edit: { es: "Editar", en: "Edit" },
    delete: { es: "Borrar", en: "Delete" },
    loadError: {
      es: "No se pudo cargar el proyecto: {detail}",
      en: "Could not load the project: {detail}",
    },
    backToList: { es: "Volver al listado", en: "Back to the list" },
    statusLabel: { es: "Estado:", en: "Status:" },
    templateBadge: { es: "plantilla", en: "template" },
    teamLabel: { es: "Equipo:", en: "Team:" },
    execModelTitle: { es: "Modelo del proyecto", en: "Project model" },
    execModelDescription: {
      es:
        "Proveedor + modelo por defecto del proyecto, que heredan los agentes sin " +
        "modelo propio. Vacío = heredar del nivel superior (equipo → plataforma).",
      en:
        "The project's default provider + model, inherited by agents without their " +
        "own. Empty = inherit from the level above (team → platform).",
    },
    sectionsHeading: { es: "Secciones", en: "Sections" },
    sectionChat: { es: "Chat", en: "Chat" },
    sectionChatDesc: {
      es: "Conversación con los agentes del proyecto.",
      en: "Conversation with the project's agents.",
    },
    sectionPlans: { es: "Planes", en: "Plans" },
    sectionPlansDesc: {
      es: "Planes de construcción + Kanban de sus tareas.",
      en: "Build plans + the Kanban of their tasks.",
    },
    sectionTasks: { es: "Tasks", en: "Tasks" },
    sectionTasksDesc: {
      es: "Todas las tareas del proyecto, incluidas las que no tienen plan.",
      en: "Every task in the project, including those with no plan.",
    },
    sectionKbs: { es: "Knowledge Bases", en: "Knowledge Bases" },
    sectionKbsDesc: {
      es: "Bases de conocimiento + documentos indexados.",
      en: "Knowledge bases + indexed documents.",
    },
    sectionMemories: { es: "Memoria", en: "Memory" },
    sectionMemoriesDesc: {
      es: "Lo que el equipo recuerda en el scope del proyecto (project_shared).",
      en: "What the team remembers in the project scope (project_shared).",
    },
    sectionMcp: { es: "MCP servers", en: "MCP servers" },
    sectionMcpDesc: {
      es: "Servidores MCP a los que se conectan los agentes.",
      en: "MCP servers the agents connect to.",
    },
    sectionToolsDiagnostic: { es: "Tools por agente", en: "Tools by agent" },
    sectionToolsDiagnosticDesc: {
      es: "Diagnóstico read-only de tools wired a cada agente.",
      en: "Read-only diagnostic of the tools wired to each agent.",
    },
    sectionCommands: { es: "Comandos & runtime", en: "Commands & runtime" },
    sectionCommandsDesc: {
      es: "Comandos autorizados (shell_exec) + runtime por defecto del stack.",
      en: "Allowed commands (shell_exec) + the stack's default runtime.",
    },
    sectionDepCache: { es: "Caché de dependencias", en: "Dependency cache" },
    sectionDepCacheDesc: {
      es: "Invalidar caché de deps por runtime.",
      en: "Invalidate the dependency cache per runtime.",
    },
    sectionWebhooks: { es: "Webhooks entrantes", en: "Incoming webhooks" },
    sectionWebhooksDesc: {
      es: "Eventos de GitHub, Jira, Sentry… que disparan acciones.",
      en: "Events from GitHub, Jira, Sentry… that trigger actions.",
    },
    editTitle: { es: "Editar proyecto", en: "Edit project" },
    editDescription: {
      es:
        "Cambia los campos básicos. La configuración avanzada (MCP, KBs, etc.) se edita " +
        "desde sus respectivas sub-secciones.",
      en:
        "Change the basic fields. Advanced configuration (MCP, KBs, etc.) is edited from " +
        "its own sub-sections.",
    },
    fieldName: { es: "Nombre", en: "Name" },
    fieldDescription: { es: "Descripción", en: "Description" },
    fieldStatus: { es: "Estado", en: "Status" },
    fieldTeam: { es: "Equipo", en: "Team" },
    statusActive: { es: "Activo", en: "Active" },
    statusPaused: { es: "Pausado", en: "Paused" },
    statusArchived: { es: "Archivado", en: "Archived" },
    noTeam: { es: "Sin equipo", en: "No team" },
    teamHint: {
      es:
        "El equipo del proyecto gobierna qué agentes ejecutan sus tareas y la política de " +
        "memoria (ADR 0071).",
      en:
        "The project's team governs which agents run its tasks and the memory policy " +
        "(ADR 0071).",
    },
    // H9a del recorrido E2E (2026-08-29): `GET /teams` mezcla los built-in de
    // plataforma con las copias del tenant. Los primeros no son asignables
    // porque sus agentes son de otro tenant y ni el chat ni el despacho los ven.
    teamGroupTenant: { es: "Equipos de este tenant", en: "Teams in this tenant" },
    teamGroupPlatform: {
      es: "Equipos de la plataforma (no asignables)",
      en: "Platform teams (not assignable)",
    },
    teamPlatformWarning: {
      es:
        "Este equipo es de la plataforma: sus agentes pertenecen al tenant Platform y este " +
        "proyecto no puede usarlos, así que el equipo no responde en el chat ni recibe " +
        "tareas. Ve a Equipos y usa «+ Adoptar» con destino este proyecto para tener una " +
        "copia propia.",
      en:
        "This is a platform team: its agents belong to the Platform tenant and this project " +
        "cannot use them, so the team neither answers in the chat nor receives tasks. Go to " +
        "Teams and use “+ Adopt” targeting this project to get your own copy.",
    },
    cancel: { es: "Cancelar", en: "Cancel" },
    save: { es: "Guardar", en: "Save" },
    saving: { es: "Guardando…", en: "Saving…" },
    deleteTitle: { es: "Borrar proyecto", en: "Delete project" },
    deleteDescriptionIntro: { es: "Esta acción es ", en: "This action is " },
    deleteDescriptionStrong: { es: "irreversible", en: "irreversible" },
    deleteDescriptionRest: {
      es: ". Borra el proyecto, sus planes, tareas y conversaciones. Los repos git en disco NO se tocan.",
      en: ". It deletes the project, its plans, tasks and conversations. The git repos on disk are NOT touched.",
    },
    deleteConfirmPrompt: {
      es: "Para confirmar, teclea el nombre del proyecto:",
      en: "To confirm, type the project name:",
    },
    deleteConfirm: { es: "Borrar definitivamente", en: "Delete permanently" },
    deleting: { es: "Borrando…", en: "Deleting…" },
  },

  /**
   * Configuración del repositorio Git del proyecto (ADR 0072) — prod-16
   * `task_prod16_03`.
   *
   * Los nombres de los proveedores (GitHub, GitLab, Azure DevOps) NO están
   * aquí: son nombres propios y no se traducen, así que siguen siendo literales
   * del `<option>`. Sí entra «Genérico», que es la única de las cuatro que es
   * una palabra y no una marca.
   */
  projectGit: {
    title: { es: "Repositorio Git", en: "Git repository" },
    description: {
      es:
        "Remoto + credenciales (PAT/SSH). El secreto se guarda en Vault y nunca se muestra; " +
        "al guardar se encola el clone. Deja la credencial vacía para conservar la ya guardada.",
      en:
        "Remote + credentials (PAT/SSH). The secret is kept in Vault and never shown; saving " +
        "queues the clone. Leave the credential empty to keep the stored one.",
    },
    providerLabel: { es: "Proveedor", en: "Provider" },
    providerGeneric: { es: "Genérico", en: "Generic" },
    branchLabel: { es: "Rama por defecto", en: "Default branch" },
    remoteUrlLabel: { es: "URL del remoto", en: "Remote URL" },
    authModeLabel: { es: "Autenticación", en: "Authentication" },
    authNone: {
      es: "Sin auth (público / preconfigurado)",
      en: "No auth (public / pre-configured)",
    },
    authPat: { es: "PAT (HTTPS)", en: "PAT (HTTPS)" },
    authSsh: { es: "Clave SSH", en: "SSH key" },
    usernameLabel: { es: "Usuario (opcional)", en: "Username (optional)" },
    tokenLabel: { es: "Token (PAT)", en: "Token (PAT)" },
    tokenPlaceholder: { es: "••• (vacío = conservar)", en: "••• (empty = keep)" },
    sshKeyLabel: { es: "Clave SSH privada", en: "Private SSH key" },
    sshKeyPlaceholder: {
      es: "(pegar clave privada; vacío = conservar la guardada)",
      en: "(paste the private key; empty = keep the stored one)",
    },
    flowHeading: { es: "Flujo git del plan", en: "Plan git flow" },
    flowDescription: {
      es:
        "Cómo se publican las ramas y qué pasa al cerrar el plan. Por defecto: los agentes " +
        "empujan la rama del plan tarea a tarea, el humano valida al cerrar y se abre un PR " +
        "(sin merge directo).",
      en:
        "How branches get published and what happens when the plan closes. By default: agents " +
        "push the plan branch task by task, a human validates at closing time and a PR is " +
        "opened (no direct merge).",
    },
    branchPushLabel: { es: "Push de la rama", en: "Branch push" },
    branchPushIncremental: { es: "Incremental (cada tarea)", en: "Incremental (every task)" },
    branchPushFinal: { es: "Solo al cerrar el plan", en: "Only when the plan closes" },
    planValidationLabel: { es: "Validación del plan", en: "Plan validation" },
    planValidationHuman: { es: "Validación humana", en: "Human validation" },
    planValidationAuto: { es: "Auto-aprobar", en: "Auto-approve" },
    pushPolicyLabel: { es: "Al cerrar el plan", en: "When the plan closes" },
    pushPolicyForbidden: { es: "No hacer nada", en: "Do nothing" },
    pushPolicyPr: { es: "Abrir PR (revisión humana)", en: "Open a PR (human review)" },
    saveOk: {
      es:
        "Guardado. Sincronización con el remoto encolada — el resultado aparece abajo en unos " +
        "segundos.",
      en: "Saved. Sync with the remote queued — the result shows up below in a few seconds.",
    },
    syncQueued: { es: "Sincronización encolada.", en: "Sync queued." },
    lastSyncLabel: { es: "Última sincronización:", en: "Last sync:" },
    lastSyncOk: { es: "correcta", en: "succeeded" },
    lastSyncFailed: { es: "con error", en: "failed" },
    alignmentCreated: {
      es: "Rama por defecto local creada desde el remoto.",
      en: "Local default branch created from the remote.",
    },
    alignmentFastForwarded: {
      es: "Rama por defecto local actualizada al remoto.",
      en: "Local default branch fast-forwarded to the remote.",
    },
    alignmentUpToDate: {
      es: "Rama por defecto local al día con el remoto.",
      en: "Local default branch is up to date with the remote.",
    },
    /**
     * H3 (recorrido E2E 2026-08-29). `align_default_branch` devuelve
     * `remote_empty` cuando `refs/remotes/origin/<rama configurada>` no
     * resuelve, y eso pasa por DOS motivos indistinguibles: el remoto está
     * vacío, o su rama por defecto es otra —el caso real: el formulario
     * precarga `main` y el repositorio usa `master`—. El texto afirmaba «repo
     * vacío» y recomendaba un push inicial: con la rama mal configurada, ese
     * push crea en el remoto una rama que no debería existir. Lo fija
     * `tests/integration/test_remote_empty_is_not_only_an_empty_repo.py`.
     */
    alignmentRemoteEmpty: {
      es:
        "El remoto no tiene la rama «{branch}». O el repositorio está vacío, o su rama por " +
        "defecto es otra (p. ej. «master» donde aquí pone «main»): comprueba cuál usa el " +
        "remoto antes de hacer un push inicial, o crearás allí una rama que no debería " +
        "existir. Mientras esa rama no exista, el PR del plan no podrá abrirse.",
      en:
        "The remote has no «{branch}» branch. Either the repository is empty or its default " +
        "branch is a different one (e.g. «master» where this field says «main»): check which " +
        "one the remote uses before pushing an initial commit, or you will create a branch " +
        "there that should not exist. Until that branch exists, the plan's PR cannot be opened.",
    },
    alignmentDiverged: {
      es:
        "La base local NO comparte historia con la rama por defecto del remoto — el PR del " +
        "plan fallará con «no history in common». Reconcilia el repo (rebasa la rama del plan " +
        "sobre el remoto) o revisa la rama por defecto configurada.",
      en:
        "The local base shares NO history with the remote's default branch — the plan's PR " +
        "will fail with «no history in common». Reconcile the repo (rebase the plan branch " +
        "onto the remote) or review the configured default branch.",
    },
    sync: { es: "Sincronizar", en: "Sync" },
    syncing: { es: "Sincronizando…", en: "Syncing…" },
    save: { es: "Guardar repositorio", en: "Save repository" },
    saving: { es: "Guardando…", en: "Saving…" },
  },

  /**
   * App-preview de validación humana (`review_image`/`review_port`, ADR 0063) —
   * prod-16 `task_prod16_03`.
   */
  projectReviewPreview: {
    title: { es: "App-preview de validación humana", en: "Human-validation app preview" },
    description: {
      es:
        "Cuando un plan llega a validación humana, la plataforma puede levantar la app del " +
        "proyecto para que el revisor la pruebe en vivo. La imagen la construye y publica la " +
        "CI del propio proyecto (la plataforma solo la referencia — ADR 0063). Sin imagen " +
        "configurada, la sesión de review funciona igual (checklist + veredicto), solo que " +
        "sin app en vivo.",
      en:
        "When a plan reaches human validation, the platform can bring up the project's app so " +
        "the reviewer tries it live. The image is built and published by the project's own CI " +
        "(the platform only references it — ADR 0063). With no image configured the review " +
        "session still works (checklist + verdict), just without a live app.",
    },
    imageLabel: { es: "Imagen del app-preview", en: "App-preview image" },
    imageHint: {
      es:
        "Tag de imagen Docker auto-servible (su CMD arranca un servidor HTTP; el código del " +
        "plan se monta en /workspace). En dev vale un tag local (`docker build -t ...`); en " +
        "producción, la referencia del registry que publica tu CI. Vacío = app-preview " +
        "desactivada.",
      en:
        "Self-serving Docker image tag (its CMD starts an HTTP server; the plan's code is " +
        "mounted at /workspace). In dev a local tag works (`docker build -t ...`); in " +
        "production, the registry reference your CI publishes. Empty = app preview disabled.",
    },
    portLabel: { es: "Puerto", en: "Port" },
    portHint: {
      es: "Puerto HTTP interno (vacío = 8080).",
      en: "Internal HTTP port (empty = 8080).",
    },
    portInvalid: { es: "Puerto inválido (1-65535).", en: "Invalid port (1-65535)." },
    save: { es: "Guardar app-preview", en: "Save app preview" },
    saved: { es: "Guardado.", en: "Saved." },
  },

  /**
   * Servicios de respaldo + variables de entorno + imagen de runtime (ADR 0129)
   * — prod-16 `task_prod16_03`.
   *
   * Los tipos del catálogo (`mysql`, `postgres`, `redis`…) NO están aquí: son
   * los identificadores que viajan al backend, y se pintan tal cual.
   */
  projectRuntimeServices: {
    title: { es: "Servicios e imagen de runtime", en: "Backing services and runtime image" },
    description: {
      es:
        "Servicios de respaldo (base de datos, caché, colas) que la plataforma levanta como " +
        "sidecars endurecidos junto al runtime del proyecto, para que sus tests y el " +
        "app-preview arranquen. Los servicios del catálogo derivan su cadena de conexión " +
        "automáticamente (`DATABASE_URL`, `REDIS_URL`, …); para una imagen arbitraria, fija " +
        "tú la conexión en las variables de entorno. Aíslados en una red interna por " +
        "tarea/sesión (ADR 0129).",
      en:
        "Backing services (database, cache, queues) the platform brings up as hardened " +
        "sidecars next to the project's runtime, so its tests and the app preview can start. " +
        "Catalog services derive their connection string automatically (`DATABASE_URL`, " +
        "`REDIS_URL`, …); for an arbitrary image, set the connection yourself in the " +
        "environment variables. Isolated on an internal per-task/session network (ADR 0129).",
    },
    servicesLabel: { es: "Servicios", en: "Services" },
    servicesEmpty: { es: "Sin servicios declarados.", en: "No services declared." },
    addService: { es: "Añadir servicio", en: "Add service" },
    serviceTypeLabel: { es: "Tipo de servicio", en: "Service type" },
    serviceImageOption: { es: "imagen…", en: "image…" },
    serviceVersionLabel: { es: "Versión", en: "Version" },
    serviceVersionPlaceholder: {
      es: "versión (ej. 8.4) — vacío = por defecto",
      en: "version (e.g. 8.4) — empty = default",
    },
    serviceImageLabel: { es: "Imagen", en: "Image" },
    serviceAliasLabel: { es: "Alias (hostname)", en: "Alias (hostname)" },
    serviceAliasPlaceholder: {
      es: "alias/hostname (vacío = tipo)",
      en: "alias/hostname (empty = type)",
    },
    removeService: { es: "Quitar servicio", en: "Remove service" },
    envLabel: { es: "Variables de entorno", en: "Environment variables" },
    envHint: {
      es:
        "Inyectadas en el contenedor principal (tests / app-preview). Sobrescriben la " +
        "connection-env derivada si repites la clave. No es Vault: no pongas secretos de " +
        "producción aquí.",
      en:
        "Injected into the main container (tests / app preview). They override the derived " +
        "connection env if you repeat a key. This is not Vault: do not put production secrets " +
        "here.",
    },
    envKeyLabel: { es: "Clave", en: "Key" },
    envValueLabel: { es: "Valor", en: "Value" },
    removeEnv: { es: "Quitar variable", en: "Remove variable" },
    addEnv: { es: "Añadir variable", en: "Add variable" },
    runtimeImageLabel: {
      es: "Imagen de runtime custom (opcional)",
      en: "Custom runtime image (optional)",
    },
    runtimeImageHintBefore: {
      es:
        "Solo si necesitas paquetes/extensiones de sistema no cubiertos por los comandos del " +
        "proyecto. Básala en un runtime-template de la plataforma (p.ej. ",
      en:
        "Only if you need system packages/extensions the project's commands do not cover. " +
        "Base it on a platform runtime template (e.g. ",
    },
    runtimeImageHintAfter: {
      es:
        ") e instala lo que falte; la publica tu CI (la plataforma no la construye, ADR 0129). " +
        "Vacío = usa el runtime por defecto del proyecto.",
      en:
        ") and install what is missing; your CI publishes it (the platform does not build it, " +
        "ADR 0129). Empty = use the project's default runtime.",
    },
    save: { es: "Guardar servicios", en: "Save services" },
    saved: { es: "Guardado.", en: "Saved." },
    invalidAlias: {
      es: "Alias inválido: {alias} (usa [a-z][a-z0-9-]*).",
      en: "Invalid alias: {alias} (use [a-z][a-z0-9-]*).",
    },
    duplicateAlias: { es: "Alias duplicado: {alias}.", en: "Duplicate alias: {alias}." },
    imageNeedsTag: {
      es: "Una imagen de servicio requiere un tag.",
      en: "A service image needs a tag.",
    },
    invalidImage: { es: "Imagen inválida: {image}.", en: "Invalid image: {image}." },
    imageNeedsAlias: {
      es: "Una imagen de servicio requiere un alias.",
      en: "A service image needs an alias.",
    },
    tooManyServices: { es: "Máximo 8 servicios.", en: "At most 8 services." },
    invalidEnvKey: {
      es: "Variable inválida: {key} (usa [A-Z][A-Z0-9_]*).",
      en: "Invalid variable: {key} (use [A-Z][A-Z0-9_]*).",
    },
    invalidRuntimeImage: {
      es: "Imagen de runtime inválida: {image}.",
      en: "Invalid runtime image: {image}.",
    },
  },

  /**
   * Límites y gobierno del proyecto (`task_wf_35`) — prod-16 `task_prod16_03`.
   *
   * Comparten namespace la sección (`components/projects/governance-section.tsx`)
   * y su módulo puro (`lib/project-governance.ts`), que redacta los problemas
   * de validación y guarda los tres catálogos. El módulo es puro: resuelve con
   * `translate(lang, …)` y recibe el idioma como parámetro OBLIGATORIO.
   */
  projectGovernance: {
    title: { es: "Límites y gobierno del proyecto", en: "Project limits and governance" },
    runBudgetHeading: { es: "Presupuesto de un run", en: "Per-run budget" },
    runBudgetDescription: {
      es:
        "El techo de UNA ejecución de agente en este proyecto. Vacío = hereda el de la " +
        "plataforma. Un valor por encima del techo de plataforma se recorta a ese techo (no " +
        "es un error); un valor de cero o negativo se rechaza, porque se descartaría en " +
        "silencio y creerías haber capado el gasto.",
      en:
        "The ceiling for ONE agent run in this project. Empty = inherit the platform's. A " +
        "value above the platform ceiling is clamped to it (that is not an error); zero or " +
        "negative is rejected, because it would be dropped silently and you would believe you " +
        "had capped the spend.",
    },
    budgetMaxIterations: { es: "Iteraciones por run", en: "Iterations per run" },
    budgetMaxTokens: { es: "Tokens por run", en: "Tokens per run" },
    budgetMaxCostUsd: { es: "Coste por run (USD)", en: "Cost per run (USD)" },
    budgetMaxWallClock: { es: "Tiempo de reloj por run (s)", en: "Wall-clock per run (s)" },
    budgetMaxToolCalls: { es: "Llamadas a tools por run", en: "Tool calls per run" },
    ceilingPlaceholder: { es: "plataforma: {ceiling}", en: "platform: {ceiling}" },
    spendHeading: { es: "Presupuesto de gasto", en: "Spend budget" },
    spendDescription: {
      es: "El techo ACUMULADO del proyecto por periodo. Al agotarse, el proyecto se pausa.",
      en: "The project's CUMULATIVE ceiling per period. When it runs out, the project pauses.",
    },
    amountLabel: { es: "Importe", en: "Amount" },
    amountPlaceholder: { es: "sin límite", en: "no limit" },
    currencyLabel: { es: "Moneda", en: "Currency" },
    periodLabel: { es: "Periodo", en: "Period" },
    periodNone: { es: "Sin límite de gasto", en: "No spend limit" },
    periodDaily: { es: "Diario", en: "Daily" },
    periodWeekly: { es: "Semanal", en: "Weekly" },
    periodMonthly: { es: "Mensual", en: "Monthly" },
    periodCustom: { es: "Personalizado", en: "Custom" },
    startDayLabel: { es: "Día de inicio del periodo", en: "Period start day" },
    lengthLabel: { es: "Duración (días)", en: "Length (days)" },
    humanReviewHeading: { es: "Revisión de tareas humanas", en: "Human task review" },
    reviewAutoApprove: { es: "Auto-aprobar al entregar", en: "Auto-approve on submit" },
    reviewAutoApproveHint: {
      es: "Entregar la tarea la da por hecha. Adecuado para tareas de «firma».",
      en: "Submitting the task marks it done. Right for «sign-off» tasks.",
    },
    reviewPeer: { es: "Revisión de otra persona", en: "Review by another person" },
    reviewPeerHint: {
      es: "La tarea queda en revisión y se asigna a un segundo humano, que aprueba o rechaza.",
      en: "The task goes to review and is assigned to a second human, who approves or rejects.",
    },
    guardrailsHeading: { es: "Guardrails del proyecto", en: "Project guardrails" },
    guardrailsDescriptionBefore: {
      es:
        "Capa de guardrails que se fusiona sobre la de plataforma. Vacío = solo la de " +
        "plataforma. Se valida con el mismo parser que usa el worker, así que lo que se " +
        "guarde aquí es lo que se aplicará. Forma: ",
      en:
        "Guardrail layer merged on top of the platform's. Empty = the platform's only. It is " +
        "validated with the same parser the worker uses, so what you save here is what gets " +
        "applied. Shape: ",
    },
    guardrailsDescriptionHooks: { es: " — hooks válidos: ", en: " — valid hooks: " },
    problemNotANumber: {
      es: "«{field}» tiene que ser un número.",
      en: "«{field}» must be a number.",
    },
    problemNotPositive: {
      es: "«{field}» tiene que ser mayor que cero.",
      en: "«{field}» must be greater than zero.",
    },
    problemGuardrailsNotObject: {
      es: "Los guardrails tienen que ser un objeto JSON.",
      en: "Guardrails must be a JSON object.",
    },
    problemGuardrailsNotJson: {
      es: "Los guardrails no son JSON válido.",
      en: "Guardrails are not valid JSON.",
    },
    problemAmountNotANumber: {
      es: "El importe del presupuesto tiene que ser un número.",
      en: "The budget amount must be a number.",
    },
    problemAmountNegative: {
      es: "El importe del presupuesto no puede ser negativo.",
      en: "The budget amount cannot be negative.",
    },
    problemAmountNeedsCurrency: {
      es: "Un importe necesita moneda (código de 3 letras).",
      en: "An amount needs a currency (3-letter code).",
    },
    problemCurrencyLength: {
      es: "La moneda es un código de 3 letras (EUR, USD…).",
      en: "The currency is a 3-letter code (EUR, USD…).",
    },
    problemCustomNeedsBoth: {
      es: "Un periodo personalizado necesita día de inicio y duración.",
      en: "A custom period needs a start day and a length.",
    },
    problemCustomOnly: {
      es: "El día de inicio y la duración solo aplican a un periodo personalizado.",
      en: "The start day and the length only apply to a custom period.",
    },
    save: { es: "Guardar límites", en: "Save limits" },
    saving: { es: "Guardando…", en: "Saving…" },
    saved: { es: "Guardado.", en: "Saved." },
  },

  /**
   * Lanzador de app-preview on-demand (ADR 0130) — prod-16 `task_prod16_03`.
   *
   * Lo montan DOS pantallas (el hub del proyecto y la ficha del plan) y el
   * texto cambia según cuál: por eso el título y la descripción tienen una
   * clave por scope en vez de llegar por props. Antes llegaban por props, y
   * cada llamante pasaba su literal castellano — o sea que migrar el componente
   * no habría traducido ni una de las dos pantallas.
   */
  previewLauncher: {
    titleProject: { es: "Preview de la app (proyecto)", en: "App preview (project)" },
    titlePlan: { es: "Preview de la app (este plan)", en: "App preview (this plan)" },
    descriptionProject: {
      es:
        "Levanta la app del proyecto (rama por defecto) en un contenedor efímero durante 24h " +
        "para probarla en vivo. Reutiliza la imagen de app-preview del proyecto (ADR 0130). " +
        "No cambia el estado de ningún plan.",
      en:
        "Brings up the project's app (default branch) in an ephemeral container for 24h so " +
        "you can try it live. It reuses the project's app-preview image (ADR 0130). It " +
        "changes no plan's state.",
    },
    descriptionPlan: {
      es:
        "Levanta la app de la rama de este plan en un contenedor efímero durante 24h para " +
        "probarla en vivo. Reutiliza la imagen de app-preview del proyecto (ADR 0130). No " +
        "cambia el estado de ningún plan.",
      en:
        "Brings up the app from this plan's branch in an ephemeral container for 24h so you " +
        "can try it live. It reuses the project's app-preview image (ADR 0130). It changes no " +
        "plan's state.",
    },
    openApp: { es: "Abrir app", en: "Open app" },
    launch: { es: "Levantar preview", en: "Launch preview" },
    relaunch: { es: "Relanzar preview", en: "Relaunch preview" },
    launching: { es: "Levantando…", en: "Bringing it up…" },
    provisioning: { es: "Provisionando el contenedor…", en: "Provisioning the container…" },
    expires: { es: "Expira: {at}", en: "Expires: {at}" },
    slow: {
      es: "El preview está tardando más de lo normal. Reintenta en unos segundos.",
      en: "The preview is taking longer than usual. Retry in a few seconds.",
    },
  },

  /**
   * El editor markdown con pestañas (`components/ui/markdown-textarea.tsx`) —
   * prod-16 `task_prod16_03`.
   *
   * Es un primitivo COMPARTIDO: lo montan 22 pantallas, varias de ellas ya
   * «migradas». Su barra de pestañas y su ayuda estaban cableadas en
   * castellano, así que con el toggle en EN todas esas pantallas enseñaban
   * «Editar / Vista previa» dentro de un diálogo por lo demás inglés. Ninguna
   * de las dos guardas de `check-i18n` lo veía como deuda de sus pantallas:
   * miran ficheros, no pantallas.
   */
  markdownTextarea: {
    tablistLabel: { es: "Modo del editor markdown", en: "Markdown editor mode" },
    tabEdit: { es: "Editar", en: "Edit" },
    tabPreview: { es: "Vista previa", en: "Preview" },
    hintLead: { es: "Soporta markdown:", en: "Markdown supported:" },
    hintTail: { es: ", listas, tablas y encabezados ", en: ", lists, tables and headings " },
    emptyPreview: {
      es: "Sin contenido para previsualizar.",
      en: "Nothing to preview yet.",
    },
  },

  /**
   * Roles de agente — catálogo COMPARTIDO (prod-16 `task_prod16_03`).
   *
   * Vive fuera de la pantalla que lo escribió porque lo consumen DOS: la
   * política rol→tool de `projects/[id]/mcp-servers` y el formulario de
   * despliegue del marketplace (`components/marketplace/deployment-config-form`).
   * Ese segundo consumidor ya estaba migrado y aun así pintaba los diez roles en
   * castellano, porque los sacaba de `ROLE_LABEL` — una constante con TEXTO. Es
   * el mismo caso que `memoryScope`: la constante guarda ahora la CLAVE y cada
   * consumidor la resuelve con el idioma activo, que es lo que impide que
   * traducir una pantalla deje la otra a medias.
   *
   * Ocho de los diez se escriben igual en los dos idiomas porque son los nombres
   * que la UI castellana ya usaba en inglés (los mismos que `agents.*` y `nav.*`
   * tienen en la allowlist de `i18n.test.ts`).
   */
  agentRole: {
    projectManager: { es: "Project Manager", en: "Project Manager" },
    architect: { es: "Arquitecto", en: "Architect" },
    backendDev: { es: "Backend Dev", en: "Backend Dev" },
    frontendDev: { es: "Frontend Dev", en: "Frontend Dev" },
    qa: { es: "QA", en: "QA" },
    reviewer: { es: "Reviewer", en: "Reviewer" },
    devops: { es: "DevOps", en: "DevOps" },
    security: { es: "Security", en: "Security" },
    technicalWriter: { es: "Technical Writer", en: "Technical Writer" },
    specialist: { es: "Especialista", en: "Specialist" },
  },

  /**
   * `projects/[id]/mcp-servers/*` — los nueve ficheros (prod-16 `task_prod16_03`).
   *
   * La `ATTR_ALLOWLIST` sólo le veía 5 atributos en 3 ficheros de ~1.900 líneas.
   * El grueso está en texto JSX suelto (el panel de tools descubiertas, la
   * tarjeta de credencial gestionada, el aviso OAuth) y en los catálogos de
   * `mcp-server-types.ts`, que es un módulo puro donde ninguna de las dos
   * guardas mira. Quinto ejemplo del mismo aviso: el contador mide su patrón, no
   * la deuda.
   */
  mcpServers: {
    // --- pantalla ---------------------------------------------------------
    // «MCP servers» es el nombre del subsistema y no se traduce: es lo que el
    // operador lee en la sidebar y en la documentación del protocolo.
    breadcrumbCurrent: { es: "MCP servers", en: "MCP servers" },
    title: { es: "MCP servers del proyecto", en: "Project MCP servers" },
    description: {
      es: "Servidores MCP (Model Context Protocol) que los agentes de este proyecto podrán usar como tools.",
      en: "MCP (Model Context Protocol) servers that the agents of this project will be able to use as tools.",
    },
    addButton: { es: "Añadir MCP server", en: "Add MCP server" },
    deleteConfirm: {
      es: "¿Borrar este MCP server del proyecto?",
      en: "Delete this MCP server from the project?",
    },
    emptyBefore: {
      es: "Este proyecto aún no tiene MCP servers configurados. Pulsa",
      en: "This project has no MCP servers configured yet. Click",
    },
    emptyAfter: { es: "para declarar el primero.", en: "to declare the first one." },
    edit: { es: "Editar", en: "Edit" },
    delete: { es: "Eliminar", en: "Delete" },

    // --- banner de vuelta del consentimiento OAuth (ADR 0127) -------------
    // Frases ENTERAS por caso, no un prefijo y un sufijo que se concatenan:
    // partir una oración en trozos es lo que la vuelve intraducible cuando el
    // otro idioma cambia el orden, y aquí el trozo variable es un nombre propio.
    oauthBannerConnected: {
      es: "✓ Conexión OAuth completada. El token quedó guardado y se refrescará automáticamente.",
      en: "✓ OAuth connection completed. The token was stored and will be refreshed automatically.",
    },
    oauthBannerConnectedFor: {
      es: "✓ Conexión OAuth completada para «{server}». El token quedó guardado y se refrescará automáticamente.",
      en: "✓ OAuth connection completed for “{server}”. The token was stored and will be refreshed automatically.",
    },
    oauthBannerError: {
      es: "No se pudo completar la conexión OAuth.",
      en: "The OAuth connection could not be completed.",
    },
    oauthBannerErrorFor: {
      es: "No se pudo completar la conexión OAuth de «{server}».",
      en: "The OAuth connection for “{server}” could not be completed.",
    },
    oauthBannerRetry: {
      es: "Vuelve a intentarlo con «Conectar».",
      en: "Try again with “Connect”.",
    },

    // --- ficha «Conexión OAuth» de un server ya guardado ------------------
    oauthTitle: { es: "Conexión OAuth", en: "OAuth connection" },
    oauthProviderFallback: { es: "el proveedor", en: "the provider" },
    oauthChecking: { es: "comprobando…", en: "checking…" },
    oauthConnectedBadge: { es: "Conectado", en: "Connected" },
    oauthDisconnectedBadge: { es: "No conectado", en: "Not connected" },
    oauthConnectedHelp: {
      es: "Autorizado con {provider}. La plataforma refresca el token automáticamente.",
      en: "Authorized with {provider}. The platform refreshes the token automatically.",
    },
    oauthDisconnectedHelp: {
      es: "Autoriza el acceso a {provider} una sola vez; la plataforma guardará y refrescará el token.",
      en: "Authorize access to {provider} once; the platform will store and refresh the token.",
    },
    oauthExpires: { es: " · caduca {date}", en: " · expires {date}" },
    oauthRedirecting: { es: "Redirigiendo…", en: "Redirecting…" },
    oauthReconnect: { es: "Reconectar", en: "Reconnect" },
    oauthConnect: { es: "Conectar", en: "Connect" },
    oauthStatusUnavailable: {
      es: "No se pudo consultar el estado de conexión (el flujo OAuth puede no estar disponible todavía).",
      en: "The connection status could not be read (the OAuth flow may not be available yet).",
    },

    // --- diálogo de alta/edición ------------------------------------------
    dialogTitle: { es: "Configurar MCP server", en: "Configure MCP server" },
    submitCreate: { es: "Crear", en: "Create" },
    submitSave: { es: "Guardar cambios", en: "Save changes" },
    saving: { es: "Guardando…", en: "Saving…" },
    cancel: { es: "Cancelar", en: "Cancel" },
    templateLabel: { es: "Plantilla rápida", en: "Quick template" },
    templateLoading: { es: "Cargando catálogo…", en: "Loading catalog…" },
    templateNone: {
      es: "— Elige una plantilla (opcional) —",
      en: "— Pick a template (optional) —",
    },
    templateHelp: {
      es: "Aplica una configuración verificada (GitHub, Jira, Google Drive, Slack, etc.). El candado 🔒 indica que la integración necesita credenciales — el campo aparecerá en Opciones avanzadas.",
      en: "Applies a verified configuration (GitHub, Jira, Google Drive, Slack, …). The 🔒 padlock means the integration needs credentials — the field shows up under Advanced options.",
    },
    nameLabel: { es: "Nombre", en: "Name" },
    nameHelp: {
      es: "Identificador del server dentro del proyecto. Solo letras, números,",
      en: "Identifier of the server within the project. Letters, digits,",
    },
    transportLabel: { es: "Transporte", en: "Transport" },
    transportStdio: { es: "stdio (subproceso local)", en: "stdio (local subprocess)" },
    commandLabel: { es: "Comando", en: "Command" },
    argsLabel: { es: "Argumentos (uno por línea)", en: "Arguments (one per line)" },
    envLabel: { es: "Variables de entorno", en: "Environment variables" },
    envEmpty: {
      es: "No hay variables. Pulsa “Añadir” para declarar una.",
      en: "No variables. Click “Add” to declare one.",
    },
    headersLabel: { es: "Cabeceras", en: "Headers" },
    headersEmpty: {
      es: "No hay cabeceras. Pulsa “Añadir” para declarar una.",
      en: "No headers. Click “Add” to declare one.",
    },
    kvAdd: { es: "Añadir", en: "Add" },
    kvRemove: { es: "Quitar", en: "Remove" },
    kvKey: { es: "clave", en: "key" },
    kvValue: { es: "valor", en: "value" },

    // --- aviso de plantilla OAuth dentro del diálogo ----------------------
    oauthNoteTitle: {
      es: "🔗 Este servidor se conecta por OAuth",
      en: "🔗 This server connects over OAuth",
    },
    oauthNoteIntro: {
      es: "No necesitas pegar ningún token.",
      en: "You do not need to paste any token.",
    },
    oauthNoteSaveStrong: { es: "Guarda", en: "Save" },
    oauthNoteMiddle: { es: "el server y pulsa", en: "the server and click" },
    oauthNoteConnectStrong: { es: "«Conectar»", en: "“Connect”" },
    oauthNoteTail: {
      es: "en su ficha: te llevará a {provider} para autorizar una vez, y la plataforma refrescará el token sola.",
      en: "on its card: it will take you to {provider} to authorize once, and the platform will refresh the token for you.",
    },

    // --- opciones avanzadas ------------------------------------------------
    advancedTitle: { es: "Opciones avanzadas", en: "Advanced options" },
    advancedHasCredential: { es: "credencial", en: "credential" },
    // El resumen colapsado. En castellano el sustantivo va delante («timeout
    // 30s»); en inglés detrás («30s timeout»), que es como se lee.
    advancedTimeoutSummary: { es: "timeout {seconds}s", en: "{seconds}s timeout" },
    authManagedTitle: {
      es: "🔒 Esta integración requiere credencial",
      en: "🔒 This integration requires a credential",
    },
    authManagedIntro: {
      es: "El sistema ya sabe dónde guardar el secreto. Pide al",
      en: "The system already knows where to store the secret. Ask your",
    },
    authManagedRole: { es: "administrador del tenant", en: "tenant administrator" },
    authManagedAdd: { es: "que añada", en: "to add" },
    authManagedFallbackKeys: { es: "la credencial", en: "the credential" },
    authManagedTail: {
      es: "en Vault antes del primer uso. Mientras no esté, las llamadas a este MCP devolverán un error de autenticación tipado (no se cae el sistema).",
      en: "to Vault before the first use. Until it is there, calls to this MCP return a typed authentication error (nothing crashes).",
    },
    authGuideLink: { es: "Ver guía de configuración →", en: "See the setup guide →" },
    authShowDetails: { es: "Detalles técnicos", en: "Technical details" },
    authHideDetails: { es: "← Ocultar detalles técnicos", en: "← Hide technical details" },
    authRefLabelTemplate: { es: "Ruta del secreto en Vault", en: "Vault path of the secret" },
    authRefLabel: {
      es: "Credencial del servidor (opcional)",
      en: "Server credential (optional)",
    },
    authRefPlaceholder: {
      es: "vault:secret/data/mcp/<servicio>/<proyecto>",
      en: "vault:secret/data/mcp/<service>/<project>",
    },
    authRefHelpTemplate: {
      es: "El sistema rellena esta ruta automáticamente al aplicar una plantilla. Solo edítala si tu Vault tiene una convención distinta.",
      en: "The system fills this path in automatically when you apply a template. Only edit it if your Vault uses a different convention.",
    },
    authRefHelp: {
      es: "Solo para MCPs que necesitan API key / token. El admin del tenant guarda el secreto en Vault y aquí solo se referencia con la ruta vault:…",
      en: "Only for MCPs that need an API key / token. The tenant admin stores the secret in Vault and this only references it with the vault:… path.",
    },
    timeoutLabel: { es: "Timeout (segundos)", en: "Timeout (seconds)" },
    timeoutHelp: {
      es: "Tiempo máximo por llamada. 30s va bien para la mayoría; sube a 120s para MCPs lentos como Docling o Puppeteer.",
      en: "Maximum time per call. 30s is fine for most; raise it to 120s for slow MCPs such as Docling or Puppeteer.",
    },

    // --- probar conexión + importación selectiva de tools -----------------
    testTitle: { es: "Probar conexión", en: "Test connection" },
    testButton: { es: "Probar", en: "Test" },
    testing: { es: "Probando…", en: "Testing…" },
    testHelp: {
      es: "Abre una sesión one-shot contra el servidor y lista las tools que expone. No guarda nada.",
      en: "Opens a one-shot session against the server and lists the tools it exposes. Nothing is saved.",
    },
    testConnectedTo: { es: "Conectado a", en: "Connected to" },
    testNoName: { es: "(sin nombre)", en: "(unnamed)" },
    // «tool» es la jerga del dominio que la UI castellana ya escribía en inglés
    // (igual que `nav.runs` o `tenantStats.runs`); lo que cambia entre idiomas
    // es el resto de la frase, no esta palabra.
    testToolOne: { es: "tool", en: "tool" },
    testToolMany: { es: "tools", en: "tools" },
    testSelectTool: { es: "Seleccionar {tool}", en: "Select {tool}" },
    importing: { es: "Importando…", en: "Importing…" },
    importButtonOne: {
      es: "Importar {count} tool al catálogo",
      en: "Import {count} tool to the catalog",
    },
    importButtonMany: {
      es: "Importar {count} tools al catálogo",
      en: "Import {count} tools to the catalog",
    },
    importSuccess: {
      es: "Importadas {count} al catálogo (Origen MCP, nivel “Aislada”).",
      en: "{count} imported to the catalog (Origin MCP, “Isolated” level).",
    },

    // --- política rol→tool (ADR 0128 fase 4) ------------------------------
    rolesTitle: { es: "Acceso por rol a las tools MCP", en: "Role-based access to MCP tools" },
    rolesOptional: { es: "opcional", en: "optional" },
    rolesHelpBefore: {
      es: "Las tools MCP las aporta el proyecto: cualquier agente del proyecto puede usarlas. Aquí puedes restringir cada tool MCP a ciertos roles. Sin ningún rol marcado, la tool queda",
      en: "MCP tools come from the project: any agent in the project can use them. Here you can restrict each MCP tool to certain roles. With no role ticked, the tool stays",
    },
    rolesHelpStrong: { es: "abierta a todos", en: "open to everyone" },
    rolesHelpAfter: { es: "(por defecto).", en: "(the default)." },
    rolesDiscard: { es: "Descartar", en: "Discard" },
    rolesSave: { es: "Guardar", en: "Save" },
    rolesSaved: { es: "Guardado", en: "Saved" },
    rolesEmptyBefore: {
      es: "Este proyecto aún no tiene tools MCP importadas. Configura un MCP server arriba y usa",
      en: "This project has no MCP tools imported yet. Configure an MCP server above and use",
    },
    rolesEmptyAfter: {
      es: "para importar sus tools al catálogo; luego podrás afinar aquí qué roles las usan.",
      en: "to import its tools into the catalog; then you can tune here which roles may use them.",
    },
    rolesOpenToAll: { es: "Abierta a todos", en: "Open to all" },
    rolesCount: { es: "{count} roles", en: "{count} roles" },
    roleCanUse: { es: "{role} puede usar {tool}", en: "{role} can use {tool}" },

    // --- catálogo de categorías del selector de plantillas ----------------
    categoryDocs: { es: "Documentos", en: "Documents" },
    categoryScm: { es: "Control de versiones", en: "Version control" },
    categoryData: { es: "Bases de datos", en: "Databases" },
    categoryFiles: { es: "Archivos", en: "Files" },
    categoryComms: { es: "Comunicación", en: "Communication" },
    categoryIssues: { es: "Issue trackers", en: "Issue trackers" },
    categoryObservability: { es: "Observabilidad", en: "Observability" },
    categorySearch: { es: "Búsqueda web", en: "Web search" },
    categoryBrowser: { es: "Navegador", en: "Browser" },
    categoryMeta: { es: "Meta / Agent helpers", en: "Meta / Agent helpers" },
    categoryOther: { es: "Otros", en: "Other" },
  },

  /**
   * Estados de un plan — catálogo COMPARTIDO (prod-16 `task_prod16_03`).
   *
   * Existía DOS veces con el mismo contenido: `plans/page.tsx` y
   * `plans/[planId]/plan-spec-types.ts` tenían cada uno su `STATUS_LABEL`
   * copiado. Dos listas del mismo enum del backend divergen en cuanto alguien
   * añade un estado, así que al traducirlas se quedan en una sola: el mapa vive
   * en `plan-spec-types.ts` y guarda la CLAVE, y el listado lo importa.
   *
   * El orden es el del workflow (CLAUDE.md §«Estados Válidos del Frontmatter»).
   */
  planStatus: {
    draft: { es: "Borrador", en: "Draft" },
    pendingApproval: { es: "Pendiente de aprobación", en: "Pending approval" },
    approved: { es: "Aprobado", en: "Approved" },
    inProgress: { es: "En progreso", en: "In progress" },
    blocked: { es: "Bloqueado", en: "Blocked" },
    pendingHumanValidation: {
      es: "Pendiente validación humana",
      en: "Pending human validation",
    },
    completed: { es: "Completado", en: "Completed" },
    rejected: { es: "Rechazado", en: "Rejected" },
    cancelled: { es: "Cancelado", en: "Cancelled" },
    archived: { es: "Archivado", en: "Archived" },
  },

  /** `projects/[id]/plans/page.tsx` — el listado de planes del proyecto. */
  plansList: {
    breadcrumbCurrent: { es: "Planes", en: "Plans" },
    title: { es: "Planes del proyecto", en: "Project plans" },
    description: {
      es: "Cada plan agrupa fases, tareas y dependencias listas para sincronizar al Kanban.",
      en: "Each plan groups phases, tasks and dependencies ready to sync to the Kanban.",
    },
    generateFromChat: { es: "Generar desde chat", en: "Generate from chat" },
    filterAriaLabel: { es: "Filtrar planes por estado", en: "Filter plans by status" },
    filterAll: { es: "Todos", en: "All" },
    loading: { es: "Cargando planes…", en: "Loading plans…" },
    errorTitle: { es: "Error al cargar los planes", en: "Could not load the plans" },
    emptyNoPlans: {
      es: "Este proyecto aún no tiene planes. Empieza una conversación en el chat para generar uno.",
      en: "This project has no plans yet. Start a conversation in the chat to generate one.",
    },
    emptyFiltered: { es: "Ningún plan en este estado.", en: "No plans in this status." },
    noDescription: { es: "Sin descripción", en: "No description" },
  },

  /**
   * `projects/[id]/plans/[planId]/*` — las quince piezas del detalle de plan,
   * más los dos diagramas de `lib/` que monta (prod-16 `task_prod16_03`).
   *
   * La `ATTR_ALLOWLIST` le veía **6 atributos en 5 ficheros** de ~2.900 líneas:
   * casi todo su castellano es texto JSX suelto y frases de ayuda, que es donde
   * ninguna de las dos guardas mira. Entra el módulo entero —incluidos el
   * diálogo de rechazo, el de sincronización al Kanban y el editor del spec—
   * porque son pantallas de DECISIÓN: quien las abre está firmando, rechazando o
   * materializando trabajo, y leerlas a medias en otro idioma es el peor sitio
   * donde dejar la deuda.
   */
  planDetail: {
    // --- página ------------------------------------------------------------
    loading: { es: "Cargando plan…", en: "Loading plan…" },
    errorTitle: { es: "Error cargando el plan", en: "Could not load the plan" },
    cancel: { es: "Cancelar", en: "Cancel" },
    saving: { es: "Guardando…", en: "Saving…" },
    saveChanges: { es: "Guardar cambios", en: "Save changes" },

    // --- cabecera de estado (task_wf_30) -----------------------------------
    statusLoading: { es: "Cargando estado…", en: "Loading status…" },
    statusProgress: { es: "Progreso", en: "Progress" },
    statusNoTasks: { es: "sin tareas todavía", en: "no tasks yet" },
    statusOpenOne: { es: "· {count} abierta", en: "· {count} open" },
    statusOpenMany: { es: "· {count} abiertas", en: "· {count} open" },
    // «Pull request» es el nombre del objeto en GitHub/GitLab: el operador lo
    // busca con ese nombre en la plataforma git, no traducido.
    statusPr: { es: "Pull request", en: "Pull request" },
    statusPrFallback: { es: "ver PR", en: "view PR" },
    statusPrError: { es: "No se pudo abrir: {error}", en: "Could not open it: {error}" },
    statusPrNone: { es: "Todavía sin PR", en: "No PR yet" },
    statusCost: { es: "Coste real / estimado", en: "Actual / estimated cost" },
    // Abreviatura de «estimado»/«estimated»: coincide en los dos idiomas.
    statusCostEstimatedSuffix: { es: "est.", en: "est." },
    statusOverEstimate: { es: "por encima", en: "over budget" },
    statusFootnoteOne: {
      es: "{tokens} tokens · {runs} run · estimación humana {cost}",
      en: "{tokens} tokens · {runs} run · human estimate {cost}",
    },
    statusFootnoteMany: {
      es: "{tokens} tokens · {runs} runs · estimación humana {cost}",
      en: "{tokens} tokens · {runs} runs · human estimate {cost}",
    },

    // --- semáforo de preflight (task_wf_72) --------------------------------
    preflightTitle: { es: "Antes de aprobar", en: "Before approving" },
    preflightBlockersOne: { es: "{count} problema serio", en: "{count} serious problem" },
    preflightBlockersMany: { es: "{count} problemas serios", en: "{count} serious problems" },
    preflightClean: {
      es: "Las {count} tareas tienen rol asignable y criterios de aceptación, y el grafo no tiene ciclos.",
      en: "All {count} tasks have an assignable role and acceptance criteria, and the graph has no cycles.",
    },
    preflightSeverityBlocker: { es: "serio", en: "serious" },
    preflightSeverityWarning: { es: "aviso", en: "warning" },
    preflightCriticalPath: {
      es: "Camino crítico: {length} de {total} tareas en serie",
      en: "Critical path: {length} of {total} tasks in series",
    },
    preflightParallelism: { es: "Paralelismo máximo: {count}", en: "Max parallelism: {count}" },
    preflightCost: {
      es: "Estimado: {hours} h ({cost} {currency}) · IA {aiMin}–{aiMax} USD",
      en: "Estimated: {hours} h ({cost} {currency}) · AI {aiMin}–{aiMax} USD",
    },

    // --- ciclo de vida ------------------------------------------------------
    lifecycleTitle: { es: "Ciclo de vida del plan", en: "Plan lifecycle" },
    lifecycleSendToApproval: { es: "Enviar a aprobación", en: "Send for approval" },
    lifecycleApprove: { es: "Aprobar plan", en: "Approve plan" },
    lifecycleApproveAndStart: { es: "Aprobar y arrancar", en: "Approve and start" },
    lifecycleStart: { es: "Empezar ejecución", en: "Start execution" },
    lifecycleUnblock: { es: "Desbloquear plan", en: "Unblock plan" },
    lifecycleHelpDraft: {
      es: "El plan está en borrador. Envíalo a aprobación para revisarlo y aprobarlo.",
      en: "The plan is a draft. Send it for approval to review and sign it off.",
    },
    lifecycleHelpApproveAndStart: {
      es: "El plan espera aprobación. «Aprobar y arrancar» lo firma y lo pone en marcha en un paso; «Aprobar plan» solo lo firma. Si el plan necesita dos firmas, ambos botones dejan la primera y esperan a la segunda.",
      en: "The plan is awaiting approval. “Approve and start” signs it and puts it in motion in one step; “Approve plan” only signs it. If the plan needs two signatures, both buttons leave the first one and wait for the second.",
    },
    lifecycleHelpSecondSignature: {
      es: "El plan tiene la primera firma y espera la segunda, que debe dar otra persona.",
      en: "The plan has the first signature and is waiting for the second, which another person must give.",
    },
    lifecycleHelpBlocked: {
      es: "El plan está bloqueado: ninguna tarea abierta puede avanzar. «Desbloquear plan» lo reactiva y re-encola todas sus tareas bloqueadas (reinicia sus reintentos).",
      en: "The plan is blocked: no open task can move forward. “Unblock plan” reactivates it and re-queues all its blocked tasks (resetting their retries).",
    },
    lifecycleHelpApproved: {
      es: "El plan está aprobado. «Empezar ejecución» lo marca en curso y crea las tareas en el Kanban.",
      en: "The plan is approved. “Start execution” marks it in progress and creates its tasks in the Kanban.",
    },

    // --- validación humana (ADR 0062) --------------------------------------
    validationTitle: {
      es: "Validación humana — probar la app",
      en: "Human validation — try the app",
    },
    validationIntroBefore: { es: "El plan está en", en: "The plan is in" },
    validationIntroMiddle: {
      es: ": los agentes han terminado y la aplicación se ha",
      en: ": the agents have finished and the application has been",
    },
    validationIntroStrong: {
      es: "levantado en un contenedor de revisión",
      en: "brought up in a review container",
    },
    validationIntroAfter: {
      es: ". Ábrela para probarla y, si todo está bien, aprueba el plan.",
      en: ". Open it to try it out and, if all is well, approve the plan.",
    },
    validationSearching: {
      es: "Buscando la sesión de revisión…",
      en: "Looking for the review session…",
    },
    validationNone: {
      es: "Aún no hay una sesión de revisión levantada para este plan.",
      en: "There is no review session up for this plan yet.",
    },
    validationOpenApp: { es: "Abrir app para probar", en: "Open the app to try it" },
    validationOpenConsole: {
      es: "Consola de revisión (terminal + logs + checklist)",
      en: "Review console (terminal + logs + checklist)",
    },
    validationProxyNote: {
      es: "El enlace abre la app servida por el review-runtime a través del proxy firmado del api-server (no se publica ningún puerto). La sesión caduca el {date}.",
      en: "The link opens the app served by the review-runtime through the api-server signed proxy (no port is published). The session expires on {date}.",
    },
    validationReject: { es: "Rechazar", en: "Reject" },
    validationMsgApproved: { es: "Plan aprobado ✓", en: "Plan approved ✓" },
    validationMsgRejected: { es: "Plan rechazado", en: "Plan rejected" },
    validationMsgError: {
      es: "Error al registrar el veredicto",
      en: "Could not record the verdict",
    },
    validationVerdictApproved: { es: "Aprobado", en: "Approved" },
    validationVerdictRejected: { es: "Rechazado", en: "Rejected" },
    rejectDialogTitle: { es: "Rechazar plan", en: "Reject plan" },
    rejectDialogHelp: {
      es: "El motivo llega a los agentes como feedback del rework — cuanto más concreto (qué está mal, dónde y qué se espera), mejor corrige el equipo. Tras rechazar podrás generar tareas correctivas desde el motivo y aceptarlas en este mismo plan.",
      en: "The reason reaches the agents as rework feedback — the more concrete it is (what is wrong, where, and what is expected), the better the team fixes it. After rejecting you can generate corrective tasks from the reason and accept them into this same plan.",
    },
    rejectPlaceholder: {
      es: "P. ej.: El filtro de Content-Type application/json es global; debe acotarse al grupo api/v1…",
      en: "E.g.: The application/json Content-Type filter is global; it should be scoped to the api/v1 group…",
    },
    rejectDefaultReason: {
      es: "Rechazado desde el panel de validación (sin motivo).",
      en: "Rejected from the validation panel (no reason given).",
    },

    // --- retrospectiva (task_wf_34) ----------------------------------------
    retroTitle: { es: "Retrospectiva", en: "Retrospective" },
    retroFootnote: {
      es: "Escrita automáticamente al cerrarse el plan y guardada en la memoria del proyecto: los agentes del siguiente plan la recuerdan.",
      en: "Written automatically when the plan closed and stored in the project memory: the agents of the next plan remember it.",
    },

    // --- deep links ---------------------------------------------------------
    deepLinksTitle: { es: "Paneles del plan", en: "Plan panels" },
    deepLinkEscalatedTitle: {
      es: "Tareas escaladas y bloqueadas",
      en: "Escalated and blocked tasks",
    },
    deepLinkEscalatedHelp: {
      es: "Tareas esperando una acción humana (aprobar, reintentar, desbloquear) — incluye las bloqueadas por reintentos agotados y el desbloqueo del plan.",
      en: "Tasks waiting for a human action (approve, retry, unblock) — including those blocked by exhausted retries, and unblocking the plan.",
    },
    deepLinkReviewTitle: { es: "Sesión de review", en: "Review session" },
    deepLinkReviewHelp: {
      es: "El plan está en validación humana — abre la review-runtime con stack + tests.",
      en: "The plan is in human validation — opens the review-runtime with stack + tests.",
    },
    deepLinkReviewPending: {
      es: "La sesión de review aparecerá aquí cuando el plan pase a",
      en: "The review session will show up here once the plan moves to",
    },

    // --- correcciones del rechazo (ADR 0107) -------------------------------
    correctionsTitle: { es: "Correcciones del rechazo", en: "Rejection fixes" },
    correctionsReasonLabel: { es: "Motivo del validador", en: "Validator reason" },
    correctionsNoReason: {
      es: "El plan fue rechazado sin sesión de review con motivo: no hay nada desde lo que generar correcciones automáticas.",
      en: "The plan was rejected without a review session carrying a reason: there is nothing to generate automatic fixes from.",
    },
    correctionsGenerateHelp: {
      es: "Genera tareas correctivas a partir del motivo: se añaden al plan como propuestas y podrás revisarlas antes de aceptarlas. Al aceptar, se crean en el Kanban y el plan vuelve a estar en curso — mismo plan, misma rama git.",
      en: "Generate corrective tasks from the reason: they are added to the plan as proposals and you can review them before accepting. On acceptance they are created in the Kanban and the plan goes back in progress — same plan, same git branch.",
    },
    correctionsGenerating: {
      es: "Generando tareas correctivas…",
      en: "Generating corrective tasks…",
    },
    correctionsGenerate: { es: "Generar tareas correctivas", en: "Generate corrective tasks" },
    correctionsEmptyGeneration: {
      es: "El modelo no propuso tareas usables. Reintenta o crea las tareas a mano.",
      en: "The model proposed no usable tasks. Retry, or create the tasks by hand.",
    },
    correctionsProposedHelp: {
      es: "Tareas correctivas propuestas — desmarca las que no quieras materializar:",
      en: "Proposed corrective tasks — untick the ones you do not want to materialize:",
    },
    correctionsMetaRole: { es: "rol: {role}", en: "role: {role}" },
    correctionsMetaComplexity: { es: "complejidad: {value}", en: "complexity: {value}" },
    correctionsMetaDependsOn: { es: "depende de: {ids}", en: "depends on: {ids}" },
    correctionsAccepting: { es: "Aceptando…", en: "Accepting…" },
    correctionsAccept: {
      es: "Aceptar correcciones ({count})",
      en: "Accept fixes ({count})",
    },
    correctionsAcceptedBadge: { es: "aceptada", en: "accepted" },
    correctionsAcceptedTail: {
      es: "— las tareas están en el Kanban y el plan sigue su ciclo.",
      en: "— the tasks are in the Kanban and the plan carries on with its cycle.",
    },

    // --- sincronizar al Kanban (task_03_27) --------------------------------
    syncTitle: { es: "Sincronizar al Kanban", en: "Sync to the Kanban" },
    syncNotApprovedBefore: {
      es: "Solo se pueden materializar tareas de un plan",
      en: "Tasks can only be materialized from a plan that is",
    },
    syncNotApprovedStrong: { es: "aprobado", en: "approved" },
    syncNotApprovedAfter: {
      es: "o en curso. Aprueba el plan primero.",
      en: "or in progress. Approve the plan first.",
    },
    syncEmpty: {
      es: "El plan aún no tiene tareas para materializar.",
      en: "The plan has no tasks to materialize yet.",
    },
    syncHelp: {
      es: "Materializa las tareas del plan como tarjetas del Kanban. Puedes sincronizar el plan completo, una fase concreta o una selección.",
      en: "Materializes the plan tasks as Kanban cards. You can sync the whole plan, one phase, or a selection.",
    },
    syncScopeTotal: { es: "Plan completo ({count} tareas)", en: "Whole plan ({count} tasks)" },
    syncScopePhase: { es: "Una fase", en: "One phase" },
    syncScopeSelection: { es: "Selección custom", en: "Custom selection" },
    syncing: { es: "Sincronizando…", en: "Syncing…" },
    syncConfirm: { es: "Sincronizar", en: "Sync" },
    syncResultBefore: { es: "Materializadas", en: "Materialized" },
    syncResultMiddle: { es: "tareas nuevas,", en: "new tasks," },
    syncResultAfter: { es: "ya existían.", en: "already existed." },
    syncResultDeps: { es: "{count} dependencias creadas.", en: "{count} dependencies created." },

    // --- diff de código de la rama (ADR 0099) ------------------------------
    codeDiffTitle: { es: "Diff de código de la rama", en: "Branch code diff" },
    codeDiffCalculating: { es: "Calculando diff…", en: "Computing the diff…" },
    codeDiffNoBranch: {
      es: "El plan aún no tiene rama materializada (ningún commit todavía).",
      en: "The plan has no materialized branch yet (no commits so far).",
    },
    codeDiffUnchangedBefore: { es: "La rama", en: "Branch" },
    codeDiffUnchangedMiddle: {
      es: "no aporta cambios sobre",
      en: "brings no changes over",
    },
    codeDiffFiles: { es: "{count} fichero(s)", en: "{count} file(s)" },
    codeDiffTruncated: {
      es: "diff truncado para esta vista — el resumen por fichero es completo",
      en: "diff truncated for this view — the per-file summary is complete",
    },

    // --- comentarios (task_03_21) ------------------------------------------
    commentsTitle: { es: "Comentarios", en: "Comments" },
    commentOnTask: { es: "Sobre tarea", en: "On task" },
    commentOnPhase: { es: "Sobre fase {ref}", en: "On phase {ref}" },
    commentOnPlan: { es: "Sobre el plan", en: "On the plan" },
    commentOnATask: { es: "Sobre una tarea", en: "On a task" },
    commentsEmpty: { es: "Aún no hay comentarios.", en: "No comments yet." },
    commentPlaceholder: { es: "Escribe tu comentario…", en: "Write your comment…" },
    commentSubmit: { es: "Comentar", en: "Comment" },

    // --- secciones presentacionales ----------------------------------------
    // «Gantt» es el nombre del diagrama (un apellido): no se traduce.
    ganttTitle: { es: "Gantt", en: "Gantt" },
    ganttAriaLabel: {
      es: "Diagrama de Gantt con línea crítica",
      en: "Gantt chart with the critical path",
    },
    dagTitle: { es: "Grafo de dependencias", en: "Dependency graph" },
    dagAriaLabel: { es: "Grafo DAG de tareas del plan", en: "DAG graph of the plan tasks" },
    diagramEmpty: { es: "Sin tareas para representar.", en: "No tasks to draw." },
    summaryTitle: { es: "Resumen", en: "Summary" },
    summaryEmpty: {
      es: "Este plan aún no tiene resumen. La sección se rellenará cuando el equipo termine la conversación de planning.",
      en: "This plan has no summary yet. The section fills in once the team finishes the planning conversation.",
    },
    scopeIn: { es: "En alcance", en: "In scope" },
    scopeOut: { es: "Fuera de alcance", en: "Out of scope" },
    decisions: { es: "Decisiones", en: "Decisions" },
    risks: { es: "Riesgos", en: "Risks" },
    estimatesTitle: { es: "Estimaciones", en: "Estimates" },
    estimateDuration: { es: "Duración", en: "Duration" },
    estimateEffort: { es: "Esfuerzo (persona-días)", en: "Effort (person-days)" },
    estimateCostHuman: { es: "Coste humano", en: "Human cost" },
    estimateCostAi: { es: "Coste IA", en: "AI cost" },
    phasesTitle: { es: "Fases", en: "Phases" },
    phaseFallback: { es: "Fase {n}", en: "Phase {n}" },
    tasksTitle: { es: "Tareas ({count})", en: "Tasks ({count})" },
    // El identificador de la tarea: se escribe igual en los dos idiomas.
    colId: { es: "ID", en: "ID" },
    colTitle: { es: "Título", en: "Title" },
    colRole: { es: "Rol", en: "Role" },
    colComplexity: { es: "Compl.", en: "Cplx." },
    colDependsOn: { es: "Depende de", en: "Depends on" },
    taskOriginCorrection: { es: "corrección", en: "fix" },

    // --- editor del spec (task_wf_42) --------------------------------------
    specEditOpen: { es: "Editar tareas", en: "Edit tasks" },
    specEditorTitle: { es: "Editar tareas ({count})", en: "Edit tasks ({count})" },
    specEditorEmpty: {
      es: "El plan no tiene tareas. Añade la primera.",
      en: "The plan has no tasks. Add the first one.",
    },
    specAddTask: { es: "Añadir tarea", en: "Add task" },
    specFieldDescription: { es: "Descripción", en: "Description" },
    specFieldComplexity: { es: "Complejidad", en: "Complexity" },
    specComplexityPlaceholder: { es: "media", en: "medium" },
    specFieldHours: { es: "Horas estimadas", en: "Estimated hours" },
    specFieldCriteria: {
      es: "Criterios de aceptación (uno por línea)",
      en: "Acceptance criteria (one per line)",
    },
    specNoOtherTasks: {
      es: "No hay otras tareas en el plan.",
      en: "There are no other tasks in the plan.",
    },
    specRemoveTask: { es: "Quitar la tarea {id}", en: "Remove task {id}" },
    specProblemNoId: {
      es: "Toda tarea necesita un identificador.",
      en: "Every task needs an identifier.",
    },
    specProblemDuplicateId: {
      es: "El identificador «{id}» está repetido.",
      en: "Identifier “{id}” is duplicated.",
    },
    specProblemNoTitle: {
      es: "La tarea «{id}» no tiene título.",
      en: "Task “{id}” has no title.",
    },
    specProblemNoIdFallback: { es: "sin id", en: "no id" },
    specErrorCycle: {
      es: "Dependencia circular: {chain}. Quita una de esas dependencias para romper el ciclo.",
      en: "Circular dependency: {chain}. Remove one of those dependencies to break the cycle.",
    },
    specErrorCycleShort: {
      es: "Hay una dependencia circular entre las tareas.",
      en: "There is a circular dependency between the tasks.",
    },
    specErrorNotEditable: {
      es: "Este plan ya no admite cambios en su especificación.",
      en: "This plan no longer accepts changes to its specification.",
    },
  },

  /**
   * Estados de una TAREA — catálogo compartido (prod-16 `task_prod16_03`).
   *
   * Namespace propio y no dentro de `projectTasks` porque el mismo enum lo pinta
   * también `app/admin/board` con su propio `COLUMNS` copiado: ese fichero es de
   * otro lote, pero cuando le toque tiene el catálogo ya escrito y no volverá a
   * duplicar el texto. «Backlog» y «Ready» se escriben igual en los dos idiomas
   * porque son los nombres que la UI castellana ya usaba en inglés.
   */
  /**
   * `components/ui/view-toggle.tsx` — el conmutador lista/Kanban
   * (prod-16 `task_prod16_03`).
   *
   * Lo montan EXACTAMENTE las dos pantallas de este lote (el listado de planes y
   * el Kanban de tareas), y sus tres textos estaban en castellano fijo sin que
   * ninguna de las dos guardas los viera: «Cambiar vista» y «Lista» no llevan
   * tilde y ninguna de las dos palabras está en la lista del detector. Sexto
   * ejemplo del mismo aviso — el contador mide su patrón, no la deuda.
   */
  viewToggle: {
    ariaLabel: { es: "Cambiar vista", en: "Change view" },
    list: { es: "Lista", en: "List" },
    // «Kanban» es el nombre del tablero: no se traduce.
    kanban: { es: "Kanban", en: "Kanban" },
  },

  taskStatus: {
    backlog: { es: "Backlog", en: "Backlog" },
    ready: { es: "Ready", en: "Ready" },
    inProgress: { es: "En curso", en: "In progress" },
    awaitingHumanApproval: { es: "Pendiente aprobación", en: "Pending approval" },
    inReview: { es: "Revisión", en: "Review" },
    blocked: { es: "Bloqueada", en: "Blocked" },
    done: { es: "Hecho", en: "Done" },
    cancelled: { es: "Cancelada", en: "Cancelled" },
  },

  /**
   * Catálogo COMPARTIDO de prioridad, hermano de `taskStatus`.
   *
   * Nació el 2026-08-20 porque el tablero pintaba `{task.priority}` crudo —
   * `medium`, `high`— como texto de UI en los dos idiomas, y las dos salidas
   * fáciles eran peores que ésta: importar `projectTasks.priority*` desde el
   * tablero mete el vocabulario de una pantalla dentro de otra, y escribir los
   * cuatro textos otra vez en `board` deja DOS copias del mismo vocabulario. Eso
   * segundo es la forma exacta del hallazgo g6 de este repo: dos copias que
   * dejan de coincidir, y nadie se entera hasta que una gatea y la otra no.
   *
   * Las claves son los valores del enum del backend, para que el llamante no
   * tenga que mantener un mapa aparte.
   */
  taskPriority: {
    low: { es: "Baja", en: "Low" },
    medium: { es: "Media", en: "Medium" },
    high: { es: "Alta", en: "High" },
    critical: { es: "Crítica", en: "Critical" },
  },

  /** `projects/[id]/tasks/page.tsx` — el Kanban de tareas del proyecto. */
  projectTasks: {
    // «Tasks» es el rótulo que la UI castellana ya usaba en inglés, igual que
    // «Runs» o «Backlog».
    breadcrumbCurrent: { es: "Tasks", en: "Tasks" },
    title: { es: "Tasks del proyecto", en: "Project tasks" },
    description: {
      es: "Todas las tareas — incluyendo las que no están asociadas a un plan. Filtra por plan para evitar mezclar contextos.",
      en: "All tasks — including those not attached to a plan. Filter by plan to avoid mixing contexts.",
    },
    createButton: { es: "Crear tarea", en: "Create task" },
    filterAriaLabel: { es: "Filtrar tareas por plan", en: "Filter tasks by plan" },
    filterAll: { es: "Todas", en: "All" },
    filterNoPlan: { es: "Sin plan", en: "No plan" },
    loading: { es: "Cargando tareas…", en: "Loading tasks…" },
    errorTitle: { es: "Error al cargar las tareas", en: "Could not load the tasks" },
    emptyNoTasks: {
      es: "Este proyecto no tiene tareas todavía.",
      en: "This project has no tasks yet.",
    },
    emptyFiltered: {
      es: "Ninguna tarea coincide con el filtro.",
      en: "No task matches the filter.",
    },
    rowNoPlan: { es: "Sin plan asignado", en: "No plan assigned" },
    colEmpty: { es: "Sin tareas", en: "No tasks" },
    cardNoPlan: { es: "sin plan", en: "no plan" },
    // El motivo de que un arrastre se revierta. Estaba en un helper puro del
    // propio fichero, o sea fuera del alcance de las dos guardas.
    moveErrorDepsOne: {
      es: "No se puede mover: {count} dependencia sin completar.",
      en: "Cannot move it: {count} dependency is not done.",
    },
    moveErrorDepsMany: {
      es: "No se puede mover: {count} dependencias sin completar.",
      en: "Cannot move it: {count} dependencies are not done.",
    },
    moveErrorIllegal: {
      es: "Movimiento no permitido: no es una transición válida desde el estado actual.",
      en: "Move not allowed: it is not a valid transition from the current state.",
    },
    createDialogDescription: {
      es: "Las tareas pueden colgar de un plan existente o vivir como tareas libres del proyecto (sin plan).",
      en: "Tasks can hang off an existing plan, or live as free project tasks (with no plan).",
    },
    fieldTitle: { es: "Título", en: "Title" },
    fieldDescription: { es: "Descripción", en: "Description" },
    // «Plan» se escribe igual en los dos idiomas.
    fieldPlan: { es: "Plan", en: "Plan" },
    planNoneOption: { es: "Sin plan (tarea libre)", en: "No plan (free task)" },
    fieldPriority: { es: "Prioridad", en: "Priority" },
    createError: { es: "Error al crear la tarea", en: "Could not create the task" },
    cancel: { es: "Cancelar", en: "Cancel" },
    creating: { es: "Creando…", en: "Creating…" },
  },

  /**
   * `components/tasks/task-detail-sheet.tsx` + `task-review-criteria.tsx`
   * (prod-16 `task_prod16_03`).
   *
   * Es una ficha COMPARTIDA: la montan el Kanban del proyecto, el tablero por
   * plan (`app/admin/board`) y —vía `TaskHumanActions`— el panel de tareas
   * escaladas. Ninguno de esos tres ficheros la contaba como su deuda, porque
   * `check-i18n` mira ficheros y no pantallas: era la mitad castellana de tres
   * pantallas a la vez.
   */
  taskDetail: {
    fallbackTitle: { es: "Tarea", en: "Task" },
    criteriaHeading: { es: "Criterios de aceptación", en: "Acceptance criteria" },
    criteriaGenerate: { es: "Generar con IA", en: "Generate with AI" },
    criteriaRegenerate: { es: "Regenerar con IA", en: "Regenerate with AI" },
    criteriaGenerating: { es: "Generando…", en: "Generating…" },
    criteriaEdit: { es: "Editar", en: "Edit" },
    criteriaEmpty: { es: "Sin criterios de aceptación.", en: "No acceptance criteria." },
    criteriaGenerateError: {
      es: "No se pudieron generar los criterios:",
      en: "The criteria could not be generated:",
    },
    criteriaSaveError: {
      es: "No se pudieron guardar los criterios:",
      en: "The criteria could not be saved:",
    },
    criterionPlaceholder: {
      es: "Condición concreta y verificable…",
      en: "A concrete, verifiable condition…",
    },
    criterionTextLabel: { es: "Enunciado del criterio", en: "The criterion's statement" },
    criterionRemove: { es: "Quitar criterio", en: "Remove criterion" },
    criterionAdd: { es: "+ Añadir criterio", en: "+ Add criterion" },

    /**
     * ADR 0162 — declarar CÓMO se comprueba cada criterio.
     *
     * El worker sólo ejecuta los criterios que son un dict con `runtime` y
     * `command`, y hasta el 2026-08-29 el editor emitía siempre una cadena: no
     * había ningún camino humano para declarar que una tarea se verifica
     * ejecutando algo. Estas claves son ese camino, más los rótulos que hacen
     * visible cuántos criterios se comprueban de verdad.
     *
     * Los textos dicen lo que PASA, no lo que sería bonito que pasara: el aviso
     * del comando y el de la señal esperada están escritos contra dos defectos
     * medidos, no como ayuda genérica.
     */
    checkDeclare: { es: "Declarar cómo se comprueba", en: "Declare how it is checked" },
    checkUndeclare: { es: "Quitar la declaración", en: "Remove the declaration" },
    checkHeading: { es: "Cómo se comprueba", en: "How it is checked" },
    checkTypeLabel: { es: "Tipo de comprobación", en: "Kind of check" },
    checkTypeAutomated: {
      es: "Automática — la ejecuta el runtime de tests",
      en: "Automated — run by the test runtime",
    },
    checkTypeManual: {
      es: "Manual — la comprueba una persona",
      en: "By hand — a person checks it",
    },
    checkRuntimeLabel: { es: "Runtime de tests", en: "Test runtime" },
    checkRuntimeNone: { es: "Elige un runtime…", en: "Pick a runtime…" },
    checkRuntimeLoading: { es: "Cargando el catálogo…", en: "Loading the catalog…" },
    checkRuntimeUnknown: {
      es: "Runtime que ya no está en el catálogo ({id})",
      en: "A runtime no longer in the catalog ({id})",
    },
    checkRuntimeError: {
      es: "No se pudo cargar el catálogo de runtimes. Vuelve a abrir el editor antes de guardar: un runtime que no exista hace fallar la tarea al ejecutarse.",
      en: "The runtime catalog could not be loaded. Reopen the editor before saving: a runtime that does not exist makes the task fail when it runs.",
    },
    checkCommandLabel: { es: "Comando", en: "Command" },
    checkCommandPlaceholder: {
      es: "Por ejemplo: vendor/bin/phpunit --testsuite Unit",
      en: "For example: vendor/bin/phpunit --testsuite Unit",
    },
    checkCommandHint: {
      es: "Se ejecuta dentro del contenedor del runtime, sobre el worktree de la tarea. Cuidado con los filtros que puedan no casar con nada: un comando que no ejecuta ningún test sale con código 0 y se registra como verde. La señal esperada es lo que lo destapa.",
      en: "It runs inside the runtime container, over the task's worktree. Beware of filters that may match nothing: a command that runs no tests exits with code 0 and is recorded as green. The expected signal is what exposes that.",
    },
    checkSignalLabel: { es: "Señal esperada", en: "Expected signal" },
    // El texto anterior decía que otras señales «todavía no se evalúan». Desde
    // la ola 2 del ADR 0162 sí se evalúan (`shared_test_runtimes/signals.py`), y
    // esa frase desanimaba justo de escribir la única que cierra el falso verde.
    // Lo que sigue siendo cierto —y hay que decirlo, o promete un bloqueo que no
    // existe— es que la señal se INFORMA y no decide: el veredicto sale del
    // código de salida hasta que se firme la opción C.
    checkSignalHint: {
      es: 'Se evalúa por comprobación y se le enseña al revisor. Escribe "exit_code == 0 and tests > 0" para no dar por buena una ejecución que no corrió ningún test. Informa: no bloquea la tarea por sí sola.',
      en: 'Evaluated per check and shown to the reviewer. Write "exit_code == 0 and tests > 0" so a run that executed no tests is not taken as good. It reports: on its own it does not block the task.',
    },
    checkReasonLabel: {
      es: "Por qué esto no se puede comprobar a máquina",
      en: "Why this cannot be checked by a machine",
    },
    checkReasonPlaceholder: {
      es: "Por ejemplo: hay que mirar el PDF generado a ojo.",
      en: "For example: someone has to look at the generated PDF.",
    },
    checkManualHint: {
      es: "Una comprobación manual no la ejecuta nadie: queda escrita para que conste que esta tarea no se verificó sola, y quién tiene que mirarla.",
      en: "A check done by hand is not run by anyone: it is written down so it is on record that this task did not verify itself, and that someone has to look.",
    },
    checkStateAutomated: { es: "Comprobación automática", en: "Automated check" },
    checkStateManual: { es: "Comprobación manual", en: "Checked by hand" },
    checkStateUndeclared: { es: "Sin comprobación", en: "No check" },
    checkUndeclaredHint: {
      es: "«Sin comprobación» significa que nadie ejecuta nada y nadie ha dicho por qué; edita el criterio para declararlo.",
      en: "“No check” means nobody runs anything and nobody said why; edit the criterion to declare it.",
    },

    /*
     * El resumen de cobertura. Sustituye a `checkSummary` («{automated} de
     * {total} criterios se comprueban solos»), que hacía dos cosas mal:
     * metía «declarado manual» y «sin declarar» en el mismo saco del «no», y
     * leía como una carencia lo que en una tarea de análisis o de documentación
     * es lo correcto. Los textos enumeran lo que hay —las categorías vacías no
     * se pintan— y no califican: informan, no acusan.
     *
     * Sin plurales a propósito: la capa i18n interpola y no flexiona, así que
     * cada texto tiene que leerse igual de bien con 1 que con 7.
     */
    coverageLabel: { es: "Cómo se comprueban:", en: "How they are checked:" },
    coverageAutomated: { es: "{count} en automático", en: "{count} automated" },
    coverageManual: { es: "{count} a mano", en: "{count} by hand" },
    coverageUndeclared: { es: "{count} sin comprobación", en: "{count} with no check" },
    detailCommand: { es: "Ejecuta:", en: "Runs:" },
    detailReason: { es: "Motivo:", en: "Why:" },
    errorCriterionTextRequired: {
      es: "Escribe el enunciado: un criterio sin texto se descarta al guardar y se llevaría la declaración por delante.",
      en: "Write the statement: a criterion with no text is dropped on save, and it would take the declaration with it.",
    },
    errorCriterionRuntimeRequired: {
      es: "Elige el runtime que ejecuta el comando.",
      en: "Pick the runtime that runs the command.",
    },
    errorCriterionCommandRequired: {
      es: "Escribe el comando que comprueba el criterio.",
      en: "Write the command that checks the criterion.",
    },
    errorCriterionReasonRequired: {
      es: "Explica por qué no se puede comprobar a máquina.",
      en: "Explain why it cannot be checked by a machine.",
    },
    cancel: { es: "Cancelar", en: "Cancel" },
    save: { es: "Guardar", en: "Save" },
    compareTitle: {
      es: "Comparar criterios de aceptación",
      en: "Compare acceptance criteria",
    },
    compareCurrent: { es: "Actuales", en: "Current" },
    compareProposed: { es: "Propuestos", en: "Proposed" },
    compareAccept: { es: "Aceptar cambios", en: "Accept changes" },
    dependsOn: { es: "Depende de", en: "Depends on" },
    // «Runs» es la jerga que la UI castellana ya escribía en inglés (igual que
    // `nav.runs` y `tenantStats.runs`).
    runsHeading: { es: "Runs", en: "Runs" },
    runsLoading: { es: "Cargando runs…", en: "Loading runs…" },
    runsEmpty: {
      es: "Esta tarea no tiene ejecuciones todavía.",
      en: "This task has no executions yet.",
    },
    commentsHeading: { es: "Comentarios", en: "Comments" },
    commentsOnlyForPlanTasks: {
      es: "Los comentarios están disponibles para tareas de un plan.",
      en: "Comments are available for tasks that belong to a plan.",
    },
    commentsLoadError: {
      es: "No se pudieron cargar los comentarios:",
      en: "The comments could not be loaded:",
    },
    commentsEmpty: { es: "Aún no hay comentarios.", en: "No comments yet." },
    commentPlaceholder: {
      es: "Escribe un comentario para el equipo (lo verá el agente)…",
      en: "Write a comment for the team (the agent will read it)…",
    },
    commentSubmit: { es: "Comentar", en: "Comment" },
    reviewHeading: { es: "Veredicto del reviewer", en: "Reviewer verdict" },
    reviewFailed: { es: "{failed} de {total} sin cumplir", en: "{failed} of {total} not met" },
    reviewAllPassed: { es: "{total} criterios cumplidos", en: "{total} criteria met" },
    reviewPassedIcon: { es: "cumplido", en: "met" },
    reviewFailedIcon: { es: "sin cumplir", en: "not met" },
    reviewEscalated: {
      es: "El reviewer escaló la tarea a un humano ({reason}).",
      en: "The reviewer escalated this task to a human ({reason}).",
    },
  },

  /**
   * `components/tasks/task-human-actions.tsx` — las cinco acciones humanas
   * sobre una tarea (prod-16 `task_prod16_03`).
   *
   * Namespace propio y no dentro de `taskDetail` porque el componente lo montan
   * DOS pantallas distintas (la ficha de la tarea y el panel de tareas
   * escaladas del plan) y su texto no depende de ninguna de las dos.
   */
  taskActions: {
    approve: { es: "Aprobar manualmente", en: "Approve manually" },
    retry: { es: "Reintentar", en: "Retry" },
    /**
     * Se llamaba «Reasignar con guía» y no reasigna nada (ADR 0162).
     * `task_lifecycle.py` mapea `reassign_with_guidance` a
     * `("backlog", "human_action", True)`: la tarea vuelve al backlog, la guía
     * queda anotada y se SUMA un reintento — el agente asignado no se toca. El
     * rótulo prometía lo único que la acción no hace, así que quien quería
     * cambiar de agente lo pulsaba y se encontraba con la misma tarea, el mismo
     * agente y un reintento menos de presupuesto. Cambiar de agente se hace
     * ahora en el formulario de edición de la tarea (`taskEdit`).
     */
    reassign: { es: "Devolver al backlog con guía", en: "Send back to the backlog" },
    block: { es: "Bloquear con motivo", en: "Block with a reason" },
    cancel: { es: "Cancelar", en: "Cancel" },
    reassignDescription: {
      es: "Devuelve la tarea al backlog con instrucciones específicas para el siguiente intento. No cambia el agente asignado y consume un reintento del presupuesto de la tarea. La guía queda en el historial.",
      en: "Sends the task back to the backlog with specific instructions for the next attempt. It does not change the assigned agent, and it uses up one retry from the task's budget. The guidance is kept in the history.",
    },
    reassignLabel: { es: "Guía para el agente", en: "Guidance for the agent" },
    reassignPlaceholder: {
      es: "Por ejemplo: 'Intenta otro enfoque usando la librería X en vez de Y.'",
      en: "For example: “Try another approach using library X instead of Y.”",
    },
    reassignSubmit: { es: "Devolver al backlog", en: "Send it back" },
    blockDescription: {
      es: "Marca la tarea como bloqueada por una causa externa (falta de acceso, dependencia pendiente, decisión de producto…). El motivo queda visible en el historial.",
      en: "Marks the task as blocked by an external cause (missing access, a pending dependency, a product decision…). The reason stays visible in the history.",
    },
    blockLabel: { es: "Motivo del bloqueo", en: "Reason for blocking" },
    blockPlaceholder: {
      es: "Por ejemplo: 'Esperando credencial de la API del cliente.'",
      en: "For example: “Waiting for the customer API credential.”",
    },
    blockSubmit: { es: "Bloquear", en: "Block" },
  },

  /**
   * `components/tasks/task-edit-dialog.tsx` — el formulario de edición de tarea
   * (ADR 0162).
   *
   * Namespace propio y no dentro de `projectTasks` por la misma razón que
   * `taskActions`: el diálogo lo montan DOS pantallas (el Kanban del proyecto y
   * la ficha compartida, que a su vez cuelga del tablero por plan) y su
   * vocabulario no es de ninguna de las dos.
   *
   * Las etiquetas de campo NO se reaprovechan de `projectTasks.field*` aunque
   * varias digan lo mismo: aquéllas son las del diálogo de ALTA, que edita tres
   * campos, y compartirlas ataría el texto de dos formularios que van a
   * divergir. Los VALORES de prioridad sí salen del catálogo compartido
   * (`taskPriority`), que es donde el vocabulario del enum tiene que vivir una
   * sola vez. Los de complejidad no tienen entrada aquí a propósito: `xs`…`xl`
   * son códigos de talla, no prosa, y se pintan en mayúsculas tal cual — meter
   * cinco claves con la misma cadena en los dos idiomas sólo añadiría cinco
   * excepciones a la guarda de copia-pega de `i18n.test.ts`.
   */
  taskEdit: {
    open: { es: "Editar", en: "Edit" },
    dialogTitle: { es: "Editar la tarea", en: "Edit the task" },
    dialogDescription: {
      es: "Cambia los datos de la tarea. El estado no se toca desde aquí: se mueve arrastrando la tarjeta en el tablero, que es donde la máquina de estados puede negarse.",
      en: "Change the task's data. Its status is not edited here: it moves by dragging the card on the board, which is where the state machine can refuse.",
    },
    loading: { es: "Cargando la tarea…", en: "Loading the task…" },
    loadError: { es: "No se pudo cargar la tarea:", en: "The task could not be loaded:" },
    saveError: {
      es: "No se pudieron guardar los cambios:",
      en: "The changes could not be saved:",
    },
    fieldTitle: { es: "Título", en: "Title" },
    fieldDescription: { es: "Descripción", en: "Description" },
    fieldPriority: { es: "Prioridad", en: "Priority" },
    fieldPlan: { es: "Plan del que cuelga", en: "Parent plan" },
    planNone: { es: "Sin plan (tarea libre)", en: "No plan (free task)" },
    plansLoading: { es: "Cargando los planes…", en: "Loading the plans…" },
    planUnknown: { es: "Plan desconocido ({id})", en: "Unknown plan ({id})" },
    fieldComplexity: { es: "Complejidad estimada", en: "Estimated complexity" },
    complexityNone: { es: "Sin estimar", en: "Not estimated" },
    fieldMaxRetries: { es: "Reintentos máximos", en: "Maximum retries" },
    maxRetriesHint: {
      es: "Entre {min} y {max}. Al agotarlos la tarea se bloquea y espera a un humano.",
      en: "Between {min} and {max}. Once they run out the task blocks and waits for a human.",
    },
    fieldAssignee: { es: "Agente que la implementa", en: "Implementing agent" },
    fieldReviewer: { es: "Agente que la revisa", en: "Reviewing agent" },
    agentNone: {
      es: "Sin fijar (lo decide el orquestador)",
      en: "Not set (the orchestrator decides)",
    },
    agentUnknown: { es: "Agente desconocido ({id})", en: "Unknown agent ({id})" },
    agentsLoading: { es: "Cargando los agentes…", en: "Loading the agents…" },
    agentsError: {
      es: "No se pudo cargar el catálogo de agentes: los dos desplegables quedan como estaban.",
      en: "The agent catalogue could not be loaded: both dropdowns keep their current value.",
    },
    errorTitleEmpty: {
      es: "El título no puede quedar vacío.",
      en: "The title cannot be left empty.",
    },
    errorTitleTooLong: {
      es: "El título no puede pasar de {max} caracteres.",
      en: "The title cannot be longer than {max} characters.",
    },
    errorRetriesNotInteger: {
      es: "Los reintentos máximos son un número entero.",
      en: "Maximum retries must be a whole number.",
    },
    errorRetriesRange: {
      es: "Los reintentos máximos van de {min} a {max}.",
      en: "Maximum retries go from {min} to {max}.",
    },
    warnReviewerIsAssignee: {
      es: "El revisor sería el mismo agente que implementa: nadie miraría este trabajo con ojos ajenos. El servidor lo acepta; el materializador de planes se niega a emparejarlos.",
      en: "The reviewer would be the same agent that implements: nobody would look at this work with fresh eyes. The server accepts it; the plan materialiser refuses to pair them.",
    },
    noChanges: { es: "No hay nada que guardar todavía.", en: "There is nothing to save yet." },
    cancel: { es: "Cancelar", en: "Cancel" },
    save: { es: "Guardar cambios", en: "Save changes" },
    saving: { es: "Guardando…", en: "Saving…" },
  },

  /**
   * `components/shared/state-block.tsx` — el triple estado (cargando / error /
   * vacío) que montan **21 ficheros** de `app/`.
   *
   * Estaba sin migrar y ninguna de las dos guardas lo veía, por dos razones a la
   * vez: los tres literales eran valores por defecto de props
   * (`loadingLabel = "Cargando…"`), no atributos JSX, así que `check-i18n` le
   * contaba CERO; y el guard mira ficheros, no pantallas, así que las 21
   * pantallas que lo montan —varias de ellas ya «migradas»— tampoco cargaban con
   * esa deuda. Con el toggle en EN veintiuna pantallas seguían diciendo
   * «Cargando…».
   */
  stateBlock: {
    loading: { es: "Cargando…", en: "Loading…" },
    empty: { es: "Sin resultados", en: "No results" },
    errorTitle: { es: "No se pudo cargar", en: "Could not load" },
  },

  /**
   * `components/shared/data-table.tsx` — la fila de «no hay nada» de la tabla
   * declarativa. Mismo escondite que `stateBlock`: valor por defecto de una
   * prop, invisible para las dos guardas, y encima sin entrada en la allowlist,
   * o sea deuda que no figuraba ni como pendiente.
   */
  dataTable: {
    empty: { es: "Sin resultados.", en: "No results." },
  },

  /**
   * `components/shared/list-toolbar.tsx` — el buscador de la cabecera de lista.
   *
   * Su `searchPlaceholder = "Buscar…"` tampoco lo veía `check-i18n`, y por un
   * motivo que conviene dejar escrito: el patrón de atributos es
   * SENSIBLE A LA CAJA, así que `searchPlaceholder="…"` no casa con
   * `placeholder="…"`. Cualquier prop compuesta (`searchPlaceholder`,
   * `emptyPlaceholder`, `filterLabel`…) es un punto ciego del detector.
   */
  listToolbar: {
    searchPlaceholder: { es: "Buscar…", en: "Search…" },
  },

  /**
   * `components/layout/tenant-picker.tsx` — el selector de tenant de la
   * cabecera (prod-16 `task_prod16_03`).
   *
   * Este namespace es la respuesta a la mitad `tenants` del enunciado de la
   * casilla, y corrige una nota anterior del plan. El 2026-08-01 se escribió
   * que «`tenants` NO existe como pantalla … esa casilla del enunciado no tiene
   * destino»: la primera mitad es cierta (no hay `app/admin/tenants/`), la
   * segunda no. La gestión de tenants tiene DOS superficies —las memberships,
   * en el diálogo de `users`, y este picker, que lista los tenants, cambia el
   * activo y **crea el primero**, que es la única vía de UI para arrancar un
   * tenant desde cero.
   *
   * Y es el caso de libro del aviso que el plan repite: la `ATTR_ALLOWLIST` le
   * veía **1 atributo**, y lo monta `AdminHeader`, o sea TODAS las pantallas del
   * System Admin. Su desplegable y su diálogo de alta enteros salían en
   * castellano dentro de pantallas por lo demás inglesas.
   */
  tenantPicker: {
    allTenants: { es: "Todos los tenants", en: "All tenants" },
    // «portfolio» es el término con el que el propio código nombra la vista sin
    // tenant activo (`portfolio view`), y el panel ya lo escribía en inglés.
    portfolioHint: { es: "(portfolio)", en: "(portfolio)" },
    empty: {
      es: "Aún no hay tenants. Crea el primero abajo.",
      en: "There are no tenants yet. Create the first one below.",
    },
    create: { es: "Crear tenant", en: "Create tenant" },
    createDescription: {
      es: "Un tenant es el espacio aislado de un equipo o departamento. Tras crearlo quedará seleccionado como tenant activo.",
      en: "A tenant is the isolated space of a team or a department. Once created it becomes the active tenant.",
    },
    nameLabel: { es: "Nombre", en: "Name" },
    namePlaceholder: { es: "Equipo de Plataforma", en: "Platform team" },
    // «Slug» es el nombre del campo del backend y lo que el operador teclea.
    slugLabel: { es: "Slug", en: "Slug" },
    slugHelp: {
      es: "Identificador en minúsculas, sólo letras, números y guiones.",
      en: "Lowercase identifier: letters, numbers and hyphens only.",
    },
    slugInvalid: {
      es: "Formato inválido: empieza por letra/número, sin espacios.",
      en: "Invalid format: start with a letter or a number, no spaces.",
    },
    slugTaken: {
      es: "Ese slug ya existe, elige otro.",
      en: "That slug already exists, pick another one.",
    },
    cancel: { es: "Cancelar", en: "Cancel" },
    creating: { es: "Creando…", en: "Creating…" },
  },

  /**
   * `app/admin/board` — el doble Kanban gerencial (prod-16 `task_prod16_03`).
   *
   * La `ATTR_ALLOWLIST` le veía **4 atributos** en 643 líneas y ninguno de los
   * cuatro era lo que se lee. Las ocho columnas NO viven aquí: salen del
   * catálogo compartido `taskStatus`, que es el mismo que usa
   * `projects/[id]/tasks`. El board tenía la tercera copia de esa lista con el
   * texto dentro; con el texto en un solo sitio, traducir una pantalla ya no
   * deja la otra a medias.
   *
   * Los dos mensajes de arrastre rechazado dicen casi lo mismo que
   * `projectTasks.moveError*` y **no se comparten a propósito**: los de aquí
   * nombran la columna de destino («No se puede mover a «Ready»…»), que es
   * información que el otro no da. Unificarlos significa decidir si el Kanban
   * del proyecto gana el nombre de la columna, y eso toca un fichero de otro
   * carril: queda anotado en el plan, no resuelto a medias.
   */
  board: {
    title: { es: "Tablero", en: "Board" },
    description: {
      es: "Planes (gerencial) arriba, tareas (operativa) abajo. Arrastra una tarea entre columnas para cambiar su estado.",
      en: "Plans (management) on top, tasks (operational) below. Drag a task between columns to change its status.",
    },
    truncated: {
      es: "El tablero muestra un máximo de 2000 filas por listado; hay más elementos que no se están mostrando. Usa los filtros por proyecto/estado para acotar.",
      en: "The board shows at most 2000 rows per listing; there are more items that are not being shown. Use the project/status filters to narrow it down.",
    },
    plansHeading: { es: "Planes", en: "Plans" },
    plansCountOne: { es: "{count} plan", en: "{count} plan" },
    plansCountMany: { es: "{count} planes", en: "{count} plans" },
    plansLoading: { es: "Cargando planes…", en: "Loading plans…" },
    plansError: { es: "No se pudieron cargar los planes:", en: "The plans could not be loaded:" },
    plansEmpty: {
      es: "Este tenant aún no tiene planes. Crea un plan desde el chat de planning de un proyecto para empezar.",
      en: "This tenant has no plans yet. Create one from a project's planning chat to get started.",
    },
    unblockPlan: { es: "Desbloquear", en: "Unblock" },
    // «Tareas» / «Tasks»: aquí es el rótulo de la sección inferior, no el de la
    // sub-sección del proyecto (que la UI castellana ya escribía en inglés).
    tasksHeading: { es: "Tareas", en: "Tasks" },
    tasksCountOne: { es: "{count} tarea", en: "{count} task" },
    tasksCountMany: { es: "{count} tareas", en: "{count} tasks" },
    live: { es: "Tiempo real", en: "Live" },
    noSelection: {
      es: "Selecciona un plan para ver sus tareas.",
      en: "Select a plan to see its tasks.",
    },
    colLoading: { es: "Cargando…", en: "Loading…" },
    colEmpty: { es: "Sin tareas", en: "No tasks" },
    lockedOne: {
      es: "Bloqueada por 1 dependencia sin completar",
      en: "Blocked by 1 unfinished dependency",
    },
    lockedMany: {
      es: "Bloqueada por {count} dependencias sin completar",
      en: "Blocked by {count} unfinished dependencies",
    },
    lockedAria: { es: "Bloqueada por dependencias", en: "Blocked by dependencies" },
    unlocked: { es: "Todas las dependencias completadas", en: "All dependencies completed" },
    unlockedAria: { es: "Dependencias completadas", en: "Dependencies completed" },
    moveDepsOne: {
      es: "No se puede mover a «{status}»: 1 dependencia sin completar.",
      en: "Cannot move it to “{status}”: 1 dependency is not done.",
    },
    moveDepsMany: {
      es: "No se puede mover a «{status}»: {count} dependencias sin completar.",
      en: "Cannot move it to “{status}”: {count} dependencies are not done.",
    },
    moveIllegal: {
      es: "Movimiento no permitido a «{status}»: no es una transición válida desde el estado actual de la tarea.",
      en: "Move to “{status}” is not allowed: it is not a valid transition from the task's current state.",
    },
  },

  /**
   * `app/admin/plans/[id]/escalated` — el panel de tareas escaladas de un plan
   * (prod-16 `task_prod16_03`).
   *
   * La `ATTR_ALLOWLIST` le veía **2 atributos** en 346 líneas. Las cinco
   * acciones humanas de cada fila ya eran bilingües (`taskActions`, migrado con
   * el lote de `tasks/*`): lo que faltaba era el marco que las rodea, o sea la
   * pantalla entera menos los botones.
   *
   * **`title` lleva «del plan» y `breadcrumbCurrent` no**, igual que en
   * `plansList`, `projectTasks`, `projectKbs`, `mcpServers` e
   * `incomingWebhooks`: la miga de pan nombra la pantalla en corto y el `h1` la
   * nombra con su ámbito. Aquí no es sólo estilo — el borrador de este
   * namespace daba el MISMO texto a las dos, y la pantalla pinta las dos a la
   * vez, así que «Tareas escaladas» salía dos veces y no había forma de
   * referirse a una sola (`getByText` encuentra dos elementos). El ámbito es el
   * PLAN y no el proyecto porque la miga de pan de encima ya lleva su título.
   */
  escalatedTasks: {
    breadcrumbProject: { es: "Proyecto", en: "Project" },
    breadcrumbCurrent: { es: "Tareas escaladas", en: "Escalated tasks" },
    title: { es: "Tareas escaladas del plan", en: "Escalated tasks of the plan" },
    description: {
      es: "Tareas del plan que llegaron al límite de reintentos del revisor automático y esperan decisión humana.",
      en: "Tasks of this plan that reached the automatic reviewer's retry limit and are waiting for a human decision.",
    },
    unblockPlan: { es: "Desbloquear plan", en: "Unblock plan" },
    addFreeTask: { es: "Añadir tarea libre", en: "Add a free task" },
    loading: { es: "Cargando tareas escaladas…", en: "Loading escalated tasks…" },
    empty: {
      es: "Sin tareas escaladas en este plan.",
      en: "No escalated tasks in this plan.",
    },
    retriesOne: { es: "{count} reintento", en: "{count} retry" },
    retriesMany: { es: "{count} reintentos", en: "{count} retries" },
    historyOne: { es: "Ver historial ({count} evento)", en: "View history ({count} event)" },
    historyMany: { es: "Ver historial ({count} eventos)", en: "View history ({count} events)" },
    dialogTitle: {
      es: "Añadir tarea libre al plan",
      en: "Add a free task to the plan",
    },
    dialogDescription: {
      es: "Crea una tarea plan-scoped que no esté atada a ningún checkbox de la spec. Útil cuando el humano detecta trabajo nuevo durante la validación del plan.",
      en: "Creates a plan-scoped task that is not tied to any checkbox of the spec. Useful when a human spots new work during the plan's validation.",
    },
    fieldTitle: { es: "Título", en: "Title" },
    fieldDescription: { es: "Descripción", en: "Description" },
    cancel: { es: "Cancelar", en: "Cancel" },
    creating: { es: "Creando…", en: "Creating…" },
    submit: { es: "Añadir tarea", en: "Add task" },
  },
} as const satisfies Dictionary;

/** La forma exacta del diccionario, para derivar las claves válidas. */
export type DictionaryShape = typeof dictionary;

/** Namespaces existentes (`"common" | "login"`). */
export type NamespaceName = keyof DictionaryShape;

/** Claves válidas de un namespace concreto. */
export type MessageKey<N extends NamespaceName> = keyof DictionaryShape[N] & string;
