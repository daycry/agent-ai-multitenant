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
    forkCopySuffix: { es: "{name} (copia)", en: "{name} (copy)" },
    forkProjectLabel: { es: "Proyecto destino", en: "Target project" },
    forkPickProject: { es: "— Selecciona —", en: "— Select —" },
    forkNoProjects: {
      es: "No tienes proyectos creados. Crea uno primero para poder personalizar.",
      en: "You have no projects yet. Create one first so you can customize.",
    },
    forkError: { es: "Error al crear la copia", en: "Could not create the copy" },
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

  /** `lib/cortex-curiosity.ts` — budget diario de búsquedas del córtex. */
  cortexCuriosity: {
    budgetNoCap: {
      es: "{used} búsquedas hoy · sin cupo configurado",
      en: "{used} searches today · no cap configured",
    },
    budgetUsage: {
      es: "{used} de {cap} búsquedas hoy ({pct} %)",
      en: "{used} of {cap} searches today ({pct}%)",
    },
  },

  /** `lib/cortex-identity.ts` — resumen legible del diff de identidad. */
  cortexIdentity: {
    versionLabel: { es: "versión {n}", en: "version {n}" },
    unset: { es: "sin definir", en: "unset" },
    changesOne: { es: "{label}: {n} ajuste", en: "{label}: {n} change" },
    changesMany: { es: "{label}: {n} ajustes", en: "{label}: {n} changes" },
    rewritten: { es: "{label} reescrita", en: "{label} rewritten" },
    noChanges: { es: "sin cambios", en: "no changes" },
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
} as const satisfies Dictionary;

/** La forma exacta del diccionario, para derivar las claves válidas. */
export type DictionaryShape = typeof dictionary;

/** Namespaces existentes (`"common" | "login"`). */
export type NamespaceName = keyof DictionaryShape;

/** Claves válidas de un namespace concreto. */
export type MessageKey<N extends NamespaceName> = keyof DictionaryShape[N] & string;
