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
} as const satisfies Dictionary;

/** La forma exacta del diccionario, para derivar las claves válidas. */
export type DictionaryShape = typeof dictionary;

/** Namespaces existentes (`"common" | "login"`). */
export type NamespaceName = keyof DictionaryShape;

/** Claves válidas de un namespace concreto. */
export type MessageKey<N extends NamespaceName> = keyof DictionaryShape[N] & string;
