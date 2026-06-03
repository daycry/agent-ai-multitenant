# Arquitectura CodeIgniter 4: HMVC y routing

Guía práctica de arquitectura para aplicaciones CodeIgniter 4 (PHP 8.1+)
organizadas como **HMVC** (módulos autónomos), con **Doctrine ORM** vía
`daycry/doctrine`, vistas Twig vía `daycry/twig` y enrutado config-driven.
Referencia para agentes que diseñan, generan o revisan la estructura de un
proyecto CI4 modular.

## Layout HMVC y autoload PSR-4

Sobre el App Starter Kit de CodeIgniter 4, los módulos se ubican bajo
`app/Modules/` y se autocargan por **PSR-4**. El mapa de autoload vive en
`composer.json` (`autoload.psr-4`); trátalo como la fuente autoritativa de las
raíces de namespace.

Agrupa los módulos por **zona** según su responsabilidad, por ejemplo:

- **Admin/** (`Admin\*`): módulos del backoffice (gestión de usuarios, idiomas,
  cookies, CSP, traducciones, configuración del sitio).
- **Site/** (o el namespace público de tu app): módulos de contenido (Home,
  Pages, News, Contact, Services, etc.).
- **Standalone**: utilitarios transversales (Dashboard, Login, Monitoring,
  Docs).

Regla: el número de carpetas de módulo puede no coincidir con el número de
namespaces top-level si varios módulos comparten prefijo. `composer.json` manda.

## Anatomía canónica de un módulo

Cada módulo es una unidad HMVC autocontenida. Estructura recomendada:

```
Modules/{Zone}/{Module}/
  Config/        Routes.php, Registrar.php (rutas Twig), {Module}Validation.php
  Controllers/   {Module}.php (CRUD web), Api.php (REST), {Module}Config.php (zona config)
  Database/      Migrations/ (por módulo), Seeds/
  Models/        Entity/*.php (entidades Doctrine), Repositories/*.php (consultas)
  Traits/        lógica de listado/comportamiento específica del módulo
  Views/         *.twig + partials/*.twig
```

Reglas de frontera de módulo:

1. Mantén el código nuevo dentro del módulo correcto según su zona/anatomía.
2. Cada módulo registra sus paths de Twig en su `Config/Registrar.php`.
3. El acoplamiento entre módulos pasa por servicios/librerías compartidos, no
   por dependencias directas entre controladores.

## Jerarquía de controladores

Define una jerarquía de bases para no repetir cableado:

- `BaseController`: inyecta los servicios compartidos (Doctrine
  `EntityManager`, Twig, Encryption, Language, helpers).
- `BaseApiController`: base REST mínima.
- Una base de contenido (p. ej. `BaseContentModuleController`) que centralice
  las operaciones comunes de los módulos de contenido (resolver el módulo
  actual, cargar su configuración, listar módulos buscables, etc.).

Un módulo de contenido típico expone tres controladores: web CRUD, `Api` (REST)
y `Config` (zona de configuración). Los nuevos módulos de contenido heredan de
la base de contenido y siguen el patrón **Config + Items** descrito abajo.

## Patrón Config + Items

Cada módulo de contenido separa dos responsabilidades:

- **Config**: ajustes singulares del módulo (visibilidad, SEO, opciones de
  layout, textos fijos). Una entidad/registro de configuración por módulo y
  contexto.
- **Items**: las entradas de contenido propiamente dichas (noticias, páginas,
  servicios…), modeladas como una colección.

Esta separación permite reusar el mismo controlador de configuración y las
mismas vistas de formulario entre módulos heterogéneos.

## Routing config-driven (helpers generadores)

Cada módulo trae su propio `Config/Routes.php`. Para las rutas repetitivas de
CRUD/listados/bloques, NO las escribas a mano: usa **helpers generadores** que
las producen de forma uniforme. Patrón recomendado (clase de configuración
neutra, p. ej. `App\Config\Routing` o un servicio propio):

### Generador de rutas DataTables

```php
public static function getRoutesDatatables(RouteCollection &$routes, string $module): void
{
    $routes->post('(:hash)/delete', $module . '::delete/$1/$2');
    $routes->post('(:hash)/delete', $module . '::delete/$1');
    $routes->post('(:hash)/visibility', $module . '::visibility/$1');
    $routes->post('list/order', $module . '::updateOrder');
}
```

Así cada módulo obtiene `delete`, `visibility` y `list/order` gratis; el
controlador sólo implementa esas acciones.

### Generador de rutas de bloques

```php
public static function getRoutesBlocks(RouteCollection &$routes, string $module): void
{
    $routes->group('blocks', static function ($routes) use ($module): void {
        $routes->get('list/(:hash)', $module . '::blocksList/$1/$2');
        $routes->get('list', $module . '::blocksList/$1');
        $routes->post('(:hash)/edit', $module . '::getBlock/$1/$2');
        $routes->post('validate', $module . '::validateBlock/$1');
        $routes->post('partial/(:segment)', $module . '::partialBlock/$1/$2');
        $routes->post('partial/(:segment)/element', $module . '::partialElementTableBlock/$1/$2');
    });
}
```

Regla de arquitectura: rutas CRUD/bloques escritas a mano son una **desviación**
del estándar y deben rechazarse en review. Mantén también en esa clase de
configuración los datos transversales del routing (constantes de path como
`/admin/content/`, `/admin/config/`, mapa de módulo→getter de configuración),
sin secretos ni dominios hardcoded.

## Rutas con prefijo de locale

Prefija todas las rutas con el placeholder `{locale}` → `/en/...`, `/es/...`,
coherente con la política bilingüe EN/ES. La raíz `/` redirige a la entrada
adecuada (p. ej. `/login`). Las rutas del paquete de autenticación
(`daycry/auth`) se delegan al propio paquete.

## Cadena de filtros

Usa los `Filters` de CI4 para las gates transversales. Ejemplos de filtros
genéricos:

- `auth:session`: gate web por defecto (`auth:jwt` / `auth:access_token` para
  APIs), provisto por `daycry/auth`.
- `group:{grupo}`: gate por grupo/rol.
- Un filtro de contexto propio que, en `before()`, fije el locale, valide la
  presencia del segmento de contexto en la URI y compruebe el permiso
  `{recurso}.{permiso}` del usuario antes de continuar (si falla,
  `redirect()->back()`).

Mantén la lógica de los filtros mínima y declarativa; la autorización de detalle
vive en grupos/permisos de `daycry/auth`, no en condicionales dispersos.

## Doctrine como capa de persistencia

CI4 trae modelos nativos, pero este stack usa **Doctrine ORM 3.x** vía
`daycry/doctrine` por el mapeo por atributos, los lifecycle callbacks y el
Second-Level Cache. Patrones de arquitectura relevantes:

- **`BaseEntity` como `#[ORM\MappedSuperclass]`**: concentra `id`/`uuid`,
  timestamps de auditoría (`created_at`/`updated_at`) y soft-delete
  (`deleted_at`), de modo que toda entidad hereda UUID + auditoría +
  comportamiento de resolución de bloques.
- **UUID** como identificador (p. ej. `ramsey/uuid`), no autoincrementales.
- **Repositorios** para las consultas; los controladores no construyen DQL.
- **Second-Level Cache (SLC)** con regiones nombradas (lecturas pesadas de
  menú/config vs. colecciones), respaldado por Redis; los repositorios
  invalidan al mutar. Trade-off: rendimiento de lectura vs. complejidad de
  invalidación.
- **Migraciones reversibles**: revisa el diff a mano e implementa `down()` de
  verdad. Regenera proxies con `php spark` cuando cambie el mapeo.

El detalle del modelado de datos vive en la KB de Doctrine; aquí basta con
respetar `BaseEntity`, UUID, repositorios y SLC como decisiones de arquitectura.

## Contenido multi-idioma en columnas JSON

El contenido traducible se guarda en **columnas JSON** con la forma
`{"es": "...", "en": "..."}`, nunca en filas separadas por idioma. Esto encaja
con la política EN/ES y permite consultarlo con funciones JSON de DQL (p. ej.
las de Scienta para Doctrine). El detalle de i18n (locales, ficheros de idioma,
negociación) vive en la KB de internacionalización.

## Vistas, formularios y bloques (Twig)

Vistas con **Twig 3** (`daycry/twig`) y partials compartidos en
`app/Views/partials`. Patrones reutilizables:

- Un macro central de campo de formulario (p. ej. `_field.twig`) que unifique el
  render de inputs, labels, errores y pestañas de idioma.
- Partials transversales: cabecera, navbar, datatable, sección de formulario,
  SEO, pestañas de idioma.
- **Sistema de bloques**: bloques de contenido reutilizables entre módulos,
  con rutas genéricas (ver el generador de rutas de bloques), render parcial
  por AJAX y partials repetidores para grupos de campos dinámicos.
- **Macro DataTables**: listados ordenables/filtrables con acciones masivas vía
  AJAX.

## Servicios, librerías, helpers y CLI

Encapsula la lógica transversal fuera de los controladores:

- **Services**: construcción de menú con caché (respaldada por SLC), gestor de
  rendimiento (monitor de queries, delegación de builders `orX` para
  DataTables).
- **Librerías**: builder de navegación por grupo, utilidades de strings
  (generación de tokens, normalización, validación de slug, JSON), gestión de
  multimedia.
- **Helpers**: helper de traducción que envuelve el lookup de `lang()`.
- **Comandos `php spark`**: tareas de mantenimiento (estadísticas/limpieza de
  rendimiento, verificación de imágenes, regeneración de proxies de Doctrine,
  purga de sesiones).

## Reglas de arquitectura que los agentes DEBEN respetar

1. Código nuevo dentro del módulo correcto según su zona y anatomía.
2. Usa el patrón **Config + Items** para módulos de contenido nuevos.
3. Usa los **helpers de routing config-driven** en vez de escribir rutas CRUD a
   mano.
4. Contenido multi-idioma en **columnas JSON** `{"es","en"}`, nunca en filas
   separadas.
5. Toda entidad hereda de `BaseEntity` (UUID + auditoría + soft-delete).
6. Persistencia en repositorios Doctrine; controladores finos que delegan en
   servicios.
7. Sin secretos, dominios ni claves hardcoded en clases de configuración: usa
   variables de entorno/gestor de secretos.
