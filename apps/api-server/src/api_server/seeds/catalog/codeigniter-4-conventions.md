# Convenciones de stack: CodeIgniter 4

Guía práctica para aplicaciones **CodeIgniter 4 (PHP 8.2+)** organizadas como
**HMVC**, con **Doctrine ORM** vía `daycry/doctrine`, plantillas **Twig** vía
`daycry/twig`, autenticación con `daycry/auth`, internacionalización **EN/ES** y
una cadena de calidad PHP-CS-Fixer + PHPStan + Psalm + PHPUnit. Es la referencia
para agentes que generan o revisan código sobre este stack. Los ejemplos son
neutros (módulos `News`, `Pages`, etc.); adáptalos al dominio real del proyecto.

## Arquitectura HMVC por módulos

La aplicación sigue el layout del _App Starter Kit_ de CI4 pero con **módulos
HMVC** autocargados por PSR-4 bajo `app/Modules/`. El mapa PSR-4 vive en
`composer.json` (`autoload.psr-4`) y es la fuente autoritativa de las raíces de
autoload. Agrupa los módulos por **zonas** según su responsabilidad, por
ejemplo una zona de administración (`Admin\*`) y una zona de contenido público.

Anatomía canónica de un módulo:

```
Modules/{Zona}/{Modulo}/
  Config/        Routes.php, Registrar.php (rutas Twig), {Modulo}Validation.php
  Controllers/   {Modulo}.php (CRUD web), Api.php (REST), {Modulo}Config.php
  Database/      Migrations/ (por módulo), Seeds/
  Models/        Entity/*.php (entidades Doctrine), Repositories/*.php
  Traits/        lógica de listado/utilidades específica del módulo
  Views/         *.twig + partials/*.twig
```

Reglas que el código nuevo debe respetar:

1. Coloca el código en el módulo y la zona correctos según su anatomía.
2. Usa el patrón **Config + Items** para módulos de contenido nuevos (un único
   _singleton_ `*Config` + N entidades de ítem).
3. Usa los helpers de routing dirigido por configuración en lugar de escribir a
   mano las rutas CRUD repetitivas.
4. El contenido multi-idioma va en columnas JSON `{"es": "...", "en": "..."}`,
   nunca en filas separadas por idioma.

## Jerarquía de controladores

Mantén controladores finos que delegan en servicios. Una jerarquía típica:

- `BaseController` — inyecta servicios compartidos (Doctrine EntityManager,
  Twig, cifrado, idioma, helpers).
- `BaseApiController` — base REST mínima.
- `BaseContentModuleController` — base de CRUD de contenido (`setModule`,
  `getConfiguration`, listado de módulos buscables, etc.).
- Los módulos de contenido suelen exponer tres controladores: uno web (CRUD),
  uno `Api` (REST) y uno `Config` (zona de configuración).

El input se valida con el componente Validation de CI4 (clases
`{Modulo}Validation`), no en el cuerpo del controlador.

## Routing dirigido por configuración

CI4 declara rutas en ficheros `Config/Routes.php` por módulo. Para CRUDs
repetitivos, centraliza la generación de rutas en una clase de configuración
neutra (por ejemplo una `Config\Routing` propia con métodos
`getRoutesDatatables()` / `getRoutesBlocks()`) que un `Registrar` consume, en
lugar de duplicar el mismo bloque de rutas en cada módulo. Mantén las rutas
prefijadas por locale (`/en/...`, `/es/...`) coherentes con la política i18n.

```php
// app/Modules/News/Config/Routes.php (ejemplo neutro)
$routes->group('news', ['namespace' => 'News\Controllers'], static function ($routes) {
    $routes->get('/', 'News::index');
    $routes->get('(:segment)', 'News::show/$1');
    $routes->post('/', 'News::create');
});
```

## Capa de datos con Doctrine ORM

Doctrine 3.x se integra vía `daycry/doctrine` con **attribute mapping**.

- Mapea entidades con atributos `#[ORM\Entity]`, `#[ORM\Table]`, `#[ORM\Column]`.
- Define una `BaseEntity` como `MappedSuperclass` con los campos comunes (id
  UUID, `created_at`, `updated_at`, `deleted_at` para soft-delete) y reúsala.
- Usa **UUID** como clave primaria con `ramsey/uuid-doctrine`.
- Para consultar JSON usa funciones DQL de `scienta/doctrine-json-functions`
  (`JSON_EXTRACT`, `JSON_SET`).
- Las consultas viven en los **Repositories** (QueryBuilder/DQL), no en los
  controladores. Evita SQL crudo salvo necesidad de rendimiento medida.
- Cuidado con N+1: usa `JOIN` + `addSelect` o fetch dirigido.
- `flush()` una vez por unidad de trabajo, nunca dentro de un bucle.
- Activa el **Second-Level Cache (SLC)** (PSR-6) para entidades de lectura
  intensiva, definiendo regiones de caché.
- Las migraciones (`php spark` / Doctrine migrations) deben ser **reversibles**:
  implementa el `down()` de verdad y no edites una migración ya aplicada en
  otros entornos; crea una nueva.

```php
#[ORM\Entity(repositoryClass: NewsRepository::class)]
#[ORM\Table(name: 'news')]
class News extends BaseEntity
{
    #[ORM\Column(type: 'json')]
    private array $title = ['es' => '', 'en' => ''];
}
```

Regenera los proxies de Doctrine con el comando `spark` correspondiente
(`php spark` con la tarea de proxies) tras cambios de mapeo.

## Internacionalización (EN/ES)

La aplicación es bilingüe: **inglés (en)** y **español (es)** únicamente. Toda
cadena de interfaz y todo campo de contenido debe existir en ambos locales.

Configuración i18n de CI4:

- `defaultLocale = 'en'`
- `negotiateLocale = false`
- `supportedLocales = ['en', 'es']`

Paquetes: ficheros de idioma nativos de CI4 + `codeigniter4/translations` +
`daycry/codeigniter-language`. Los ficheros de idioma por locale viven bajo la
convención del framework (`Locales.php`, `Validation.php`, `Response.php`, etc.)
y las traducciones se resuelven con `lang()`.

Contenido multi-idioma (no cadenas de UI) en columnas JSON `{"es": "...",
"en": "..."}`. Una macro Twig de campo (`_field.twig`) renderiza estos valores:
los _selects_ traducidos como un único `<select>` con atributos `data-{locale}`
por opción, y los no-selects como un control por idioma con visibilidad por
pestañas de idioma (`language-tabs.js`).

Reglas para agentes:

1. Nunca añadas un campo de contenido sin entradas `es` y `en`.
2. Nunca _hardcodees_ cadenas de UI: usa `lang()` y ficheros de idioma.
3. Al añadir una cadena de UI, añádela a AMBOS locales en el mismo cambio.

## Vistas, formularios y bloques (Twig)

Plantillas con Twig 3 (`daycry/twig`) y _partials_ compartidos en
`app/Views/partials`, entre ellos una macro de campo central
(`input-forms/_field.twig`), `blocks.twig`, `datatable.twig`,
`form-section.twig`, `seo.twig`, `language-tabs.twig`.

- **Sistema de bloques**: bloques de contenido reutilizables y compartidos entre
  módulos, con renderizado parcial por AJAX y _repeaters_ para grupos de campos
  dinámicos.
- **Macro DataTables**: listados ordenables/filtrables con acciones masivas vía
  AJAX, servidos en _server-side_ con `hermawan/codeigniter4-datatables`.

## Frontend y assets

El pipeline de assets vive bajo `public/assets/`:

- JS de núcleo en `public/assets/js/core/` (validación de formularios, init de
  editores, pestañas de idioma, acciones masivas). Librerías de terceros bajo
  `public/assets/third-party/`.
- Editor de texto enriquecido **TinyMCE 7.x** (cuida el `z-index` dentro de
  modales), **Select2**, **DataTables**, **Bootstrap 5.2.x** (versión fijada),
  **jQuery 3.x**.
- **Versionado de assets** vía `michalsn/minifier` y un `versions.json` para
  _cache-busting_.

Mantén el JS organizado por responsabilidad y evita scripts inline en las
plantillas; usa los módulos de `js/core/`.

## Estándar de código y toolchain

El contrato de calidad se codifica en los **scripts de Composer** de
`composer.json`. Triple capa de gates + mutation testing.

Herramientas:

- **Estilo**: PHP-CS-Fixer (`friendsofphp/php-cs-fixer ^3`) con el estándar de
  CI4 (`codeigniter/coding-standard` + `nexusphp/cs-config`); config
  `.php-cs-fixer.dist.php`, apuntando a `app/` y `tests/`.
- **Análisis estático**: PHPStan (`phpstan/phpstan ^2`) con `phpstan.neon` y
  _baseline_; Psalm (`vimeo/psalm`) con _baseline_ — sólo fallan los problemas
  NUEVOS.
- **Modernización**: Rector 2 (`rector/rector ^2`) con `rector.php` apuntando a
  PHP 8.2 (DEAD_CODE / CODE_QUALITY / EARLY_RETURN / TYPE_DECLARATION).
- **Duplicación**: phpcpd (`systemsdk/phpcpd`).
- **Deps no usadas**: `icanhazstring/composer-unused`.
- **Mutación**: Infection (`infection/infection`) con `infection.json.dist`.

Scripts de Composer (el contrato):

| Script           | Definición                                               | Cuándo                           |
| ---------------- | -------------------------------------------------------- | -------------------------------- |
| `@ci`            | `@quality` + `@test`                                     | Lo que corre el pipeline en push |
| `@quality`       | `@cs-check` + `@static-analysis` + `@deduplicate` + deps | Gates rápidos pre-commit         |
| `@fix`           | `@cs-fixer` + `@rector-fix`                              | Auto-arreglo de lo arreglable    |
| `@test`          | `phpunit --testsuite Unit,Integration`                   | Run de tests por defecto         |
| `@test-E2E`      | `phpunit --testsuite E2E`                                | Suite de navegador (Selenium)    |
| `@test-coverage` | `@test` + cobertura HTML                                 | Reporte de cobertura             |
| `@mutation`      | `infection --testsuite=Unit,Integration`                 | Gate de mutación (lento)         |

Convenciones:

- PHP `^8.2`; `declare(strict_types=1)` en los ficheros nuevos; mapeo Doctrine
  por atributos.
- Ejecuta `@fix` y luego `@quality` antes de commitear; `@ci` es el gate de
  merge.
- **No** edites los _baselines_ para silenciar problemas preexistentes: sólo los
  problemas nuevos de PHPStan/Psalm deben fallar.

## Autenticación y seguridad

La autenticación se gestiona con **`daycry/auth`** (config `Config/Auth.php`):

- Authenticators: **session** (por defecto), **JWT**, **access-token** y guest.
- **Grupos y permisos** para autorización; filtros de CI4 para gatear rutas.
- **2FA** por email, _magic-link_ y cookies _remember-me_ según necesidad.
- **Rate-limiting** por método en los endpoints de API.

Endurecimiento web de CI4: configura cabeceras de seguridad y **CSP**, política
de **Cookies** y protección **CSRF**; valida y normaliza todo input; usa el
componente de cifrado del framework para datos sensibles.

Gestión de secretos (principio innegociable):

- **Nunca** _hardcodees_ secretos, API keys ni credenciales en el código ni en
  clases de configuración versionadas.
- Lee los secretos de variables de entorno / `.env` (fuera del control de
  versiones) o de un gestor de secretos.
- No commitees `.env` ni ficheros con credenciales reales.

Si integras un proveedor de SSO/OAuth2 externo, aíslalo tras una interfaz y
configura sus credenciales por entorno, nunca embebidas en el repositorio.

## Testing con PHPUnit

Tres suites separadas declaradas en `phpunit.xml.dist`:

- **Unit**: lógica aislada con dependencias dobladas (mocks/stubs), sin DB.
- **Integration**: tests que tocan la base de datos y servicios reales; usa una
  DB de test aislada (transacciones que se revierten o BD efímera).
- **E2E**: pruebas de navegador con Selenium/Chrome
  (`daycry/phpunit-extension-selenium`, `php-webdriver/webdriver`).

Buenas prácticas:

- Activa el _strict mode_ de PHPUnit.
- Para tests de cliente HTTP de terceros, graba/reproduce con VCR
  (`daycry/phpunit-extension-vcr`) en lugar de pegar a la red real.
- Apunta a subir la cobertura de la capa de dominio/servicios de forma sostenida
  (`@test-coverage`) y endurece con mutation testing (`@mutation`) en las zonas
  críticas.
- Nunca metas claves de API reales en los tests; usa fixtures/fakes
  (`fakerphp/faker`) y configuración de entorno de test.

## Catálogo de dependencias (referencia)

Dependencias de runtime habituales (`require`):

| Paquete                            | Rol                                                       |
| ---------------------------------- | --------------------------------------------------------- |
| `codeigniter4/framework`           | Framework web HMVC (PHP ^8.2).                            |
| `codeigniter4/translations`        | Traducciones del framework.                               |
| `daycry/auth`                      | Auth session/JWT/access-token/guest, grupos/permisos, 2FA |
| `daycry/codeigniter-language`      | Capa helper de i18n.                                      |
| `daycry/doctrine`                  | Integración Doctrine ORM 3.x (attribute mapping, SLC).    |
| `daycry/twig`                      | Plantillas Twig 3 con extensiones.                        |
| `guzzlehttp/guzzle`                | Cliente HTTP.                                             |
| `hermawan/codeigniter4-datatables` | DataTables server-side.                                   |
| `michalsn/minifier`                | Minificación/versionado de assets.                        |
| `ramsey/uuid-doctrine`             | Tipo UUID para Doctrine.                                  |
| `scienta/doctrine-json-functions`  | Funciones DQL `JSON_EXTRACT` / `JSON_SET`.                |
| `tinymce/tinymce`                  | Editor de texto enriquecido.                              |
| `twbs/bootstrap`                   | Framework CSS (versión fijada).                           |

Dev/calidad (`require-dev`): `codeigniter/coding-standard`,
`codeigniter/phpstan-codeigniter`, `daycry/phpunit-extension-selenium`,
`daycry/phpunit-extension-vcr`, `fakerphp/faker`, `friendsofphp/php-cs-fixer`,
`icanhazstring/composer-unused`, `infection/infection`, `mikey179/vfsstream`,
`nexusphp/cs-config`, `php-webdriver/webdriver`, `phpstan/phpstan`,
`rector/rector`, `systemsdk/phpcpd`, `vimeo/psalm`. Se sugiere
`roave/security-advisories` para bloquear versiones inseguras.

Mantén `composer.lock` commiteado y prefiere versiones estables salvo que un
paquete concreto exija `dev`.
