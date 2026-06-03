# Seguridad y autenticación en CodeIgniter 4

Guía práctica de seguridad para aplicaciones CodeIgniter 4 que usan el paquete
`daycry/auth` como capa de autenticación y autorización. Cubre autenticadores,
modelo de grupos y permisos, autenticación de API, rate limiting, protecciones
del framework (CSRF, CSP, cookies) y buenas prácticas de gestión de secretos.
Referencia para agentes que generan o revisan código de seguridad en CI4.

## Capa de autenticación: daycry/auth

`daycry/auth` se configura en `app/Config/Auth.php`. Provee varios
autenticadores intercambiables:

- `session` — autenticador por defecto para la capa web (login con formulario,
  sesión de servidor).
- `jwt` — tokens JWT firmados para APIs sin estado.
- `access_token` — tokens de acceso de larga duración (cabecera `X-API-KEY` o
  similar), útiles para integraciones máquina-a-máquina.
- `guest` — usuario anónimo, sin sesión.

Regla práctica: elige un único autenticador por grupo de rutas. No mezcles
session y access_token sobre el mismo endpoint salvo necesidad explícita. Si
configuras un autenticador (p. ej. `jwt`) pero ninguna ruta lo usa, es
superficie de ataque muerta: cablealo intencionadamente en un grupo de rutas o
elimínalo de la configuración.

### Validadores de contraseña

`daycry/auth` permite encadenar validadores en `Config/Auth.php`:

- `Composition` — longitud y complejidad mínimas.
- `Dictionary` — rechaza contraseñas en listas de filtraciones conocidas.
- `NothingPersonal` — impide reutilizar email/usuario dentro de la contraseña.

Activa al menos estos tres en cualquier entorno con login de usuarios.

### Tablas de autenticación

El paquete crea sus propias tablas (nombres por defecto, ajustables en
configuración): `users`, identidades de usuario, grupos, permisos, las tablas
pivote grupo-usuario y permiso-usuario, registros de login, tokens "remember me"
y logs de auditoría. No escribas en ellas a mano: usa la API del paquete
(`auth()->user()`, providers y modelos del paquete).

### Registro de intentos de login

Configura el registro de intentos **solo en fallo** (failure-only) para
producción: evita guardar credenciales válidas y reduce ruido. Si necesitas
bloqueo por IP o throttling de login, actívalo explícitamente; por defecto puede
no estar habilitado.

## Modelo de grupos y permisos

`daycry/auth` modela autorización con grupos y permisos:

- **Grupos**: un grupo por defecto (p. ej. `user`) más grupos de mayor
  privilegio (p. ej. un grupo de administración). Asigna grupos por rol de
  negocio, no por usuario individual.
- **Permisos**: usa un esquema namespaced `{recurso}.{accion}`
  (p. ej. `articles.edit`, `users.delete`). Comprueba permisos con la API del
  paquete antes de ejecutar acciones sensibles.

### Comprobación en filtros (Filters de CI4)

Encadena las comprobaciones en la capa de Filters de CodeIgniter 4, declarados
en `app/Config/Filters.php` y aplicados por grupo de rutas:

- `auth:session` — gate web por defecto (o `auth:jwt` / `auth:access_token` en
  APIs).
- Un filtro de grupo que exige pertenencia a un grupo concreto (gate de
  administración).
- Un filtro propio de contexto que valida segmentos de la URL y comprueba el
  permiso `{recurso}.{accion}` correspondiente antes de dejar pasar la petición.

Un filtro de autorización propio típico, en su método `before()`, debe:

1. Resolver el contexto desde la URI (p. ej. el segmento que identifica el
   recurso) y rechazar si falta.
2. Verificar que el usuario pertenece al grupo requerido
   (`auth()->user()->getGroups()`).
3. Si recibe argumentos del filtro, validar que el usuario posee el permiso
   `{recurso}.{accion}` que casa con esos argumentos; si no, redirigir o
   devolver 403.

Centraliza esta lógica en el filtro, no en cada controlador.

## Autenticación de API y rate limiting

- Para APIs, usa `access_token` (cabecera) o `jwt` (Bearer). No expongas
  endpoints de escritura sin autenticación.
- Aplica **rate limiting por método/endpoint** (p. ej. un límite de peticiones
  por minuto, configurable). `daycry/auth` y los Throttler de CI4 permiten
  acotar por usuario o por IP.
- Mantén el contexto de autorización (grupo/permiso) también en las rutas de
  API: que un token válido no salte el modelo de permisos.

## Protecciones del framework

### CSRF

Activa la protección CSRF en `app/Config/Security.php`. Para apps con sesión, el
modo basado en sesión (`csrfProtection = 'session'`) es el recomendado. Incluye
el token en todos los formularios y peticiones POST/PUT/DELETE de la capa web.
Las APIs autenticadas por token no requieren CSRF (no usan cookies de sesión).

### CSP (Content Security Policy)

Habilita y gestiona la CSP de CI4 (`app/Config/ContentSecurityPolicy.php` +
`Config\App::$CSPEnabled`). Define orígenes permitidos para scripts, estilos e
imágenes; evita `unsafe-inline` salvo necesidad puntual y documentada. Centraliza
la política en un único punto en lugar de cabeceras dispersas.

### Cookies

Configura cookies en `app/Config/Cookie.php`: `secure = true` (solo HTTPS),
`httponly = true` (no accesibles desde JS) y `samesite` adecuado (`Lax` o
`Strict`). La cookie de sesión nunca debe ser legible por JavaScript.

## Gestión de secretos (buenas prácticas)

Principio no negociable: **ningún secreto literal en el código fuente ni en
configuración versionada**. Esto incluye claves de API, claves de firma JWT,
contraseñas de base de datos, claves de monitorización y tokens de servicios
externos.

- Lee todos los secretos desde variables de entorno (`.env` en desarrollo, un
  gestor de secretos en producción). En CI4, accede vía `env('CLAVE')` o
  `getenv()`, con valores por defecto seguros (nunca el secreto real).
- El fichero `.env` va en `.gitignore`; commitea solo un `.env.example` con
  claves vacías o de ejemplo.
- No dupliques un mismo secreto en varios sitios (p. ej. en config y a la vez en
  `phpunit.xml.dist`): si una clave aparece en tests, parametrízala por entorno y
  rótala junto con la de producción.
- No dejes rutas absolutas de máquina local ni binarios de desarrollo cableados
  en config versionada (rompen en la imagen de despliegue Linux): hazlos
  env-driven.
- Cualquier modo de bypass de autenticación de desarrollo (un "skip auth" que
  fabrica una sesión) debe ser **imposible de activar** fuera de desarrollo.
  Añade una aserción de arranque que falle si el bypass está activo cuando
  `CI_ENVIRONMENT === 'production'`.
- Cuando un secreto haya estado en el control de versiones, considéralo
  comprometido: rótalo tras sacarlo del repositorio.

## Checklist de revisión de seguridad

Antes de aprobar un cambio que toque la superficie de seguridad:

- [ ] No hay secretos literales en el diff (claves, tokens, contraseñas).
- [ ] Las rutas nuevas declaran su filtro de autenticación/autorización.
- [ ] Los permisos siguen el esquema `{recurso}.{accion}` y se comprueban.
- [ ] CSRF activo en la capa web; cookies con `secure`/`httponly`/`samesite`.
- [ ] Ningún autenticador configurado queda sin usar (o se justifica).
- [ ] Cualquier bypass de auth de dev no puede activarse en producción.
- [ ] Los secretos se leen de entorno, con `.env` fuera del repositorio.
