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

## Instalar el esqueleto sobre un workspace que ya existe

`composer create-project codeigniter4/framework .` **exige que el directorio
esté COMPLETAMENTE VACÍO**, no «sin `.git`». Cualquier entrada lo aborta,
ocultas incluidas:

```text
Project directory "." is not empty.
```

De esa premisa sale todo lo demás. Medido el 2026-08-31 en el proyecto «Hello
World CI4 v3»: el agente quitó `.git` y el comando **siguió** fallando
—quedaban `README.md` y `composer.log`—; sólo entró cuando el directorio quedó
a cero. Sin la premisa delante, la estrategia que se deduce es «vaciar el
directorio», y en el segundo intento eso se llevó `app/` con 85 ficheros que
eran el deliverable ya commiteado de la tarea anterior. El camino que sí existe
es el de abajo.

**El worktree de una tarea casi nunca está vacío.** Tres motivos, y ninguno es
una anomalía del proyecto:

- Trae el `README` (y lo que lleve) de la rama base: un repositorio recién
  creado ya tiene contenido.
- En un **reintento** trae el deliverable entero de la tarea anterior, ya
  commiteado en la rama del plan.
- Trae el `.git` del worktree. El worker lo **esconde** mientras corre el agente
  (ADR 0163) para que no puedas borrarlo, así que no lo verás en `list_files`;
  eso desatasca el primer andamiaje sobre un worktree virgen y nada más. El
  resto de entradas siguen ahí y siguen bloqueando `create-project`.

### Procedimiento: andamiar en un temporal y mover

1. **Comprueba antes si ya hay CodeIgniter**: `list_files` con `path: "."` (una
   cadena vacía también vale: significa `.`). Si aparecen `app/`, `system/`,
   `spark` y un `composer.json` que requiere `codeigniter4/framework`, **no
   andamies**: salta a «Si el proyecto ya tiene CodeIgniter instalado».
2. **Instala en un subdirectorio del propio workspace**:
   `composer create-project codeigniter4/framework _skel --no-interaction`.
   Composer crea `_skel` él solo: no necesitas `mkdir` —y probablemente no lo
   tengas, porque `mkdir ci4tmp` rebotó contra la allowlist (ADR 0093)—.
3. **Lista lo instalado**: `list_files` con `path: "_skel"`. La lista trae
   también las entradas que empiezan por punto, que son las que se olvidan al
   mover a mano.
4. **Mueve entrada por entrada a la raíz** con `move_file`
   (`{"source": "_skel/app", "destination": "app"}`). Un directorio viaja
   entero en una sola llamada. Si el destino ya existe la llamada se
   **rechaza**: sobrescribir es la variante destructiva y se pide aparte.
5. **Retira el temporal**: `delete_file` con
   `{"path": "_skel", "recursive": true}`. Aquí sí es legítimo: `_skel` lo
   creaste tú en este run y no está versionado, así que la guarda de «Lo que NO
   se hace» no aplica.

Detalle del paso 4, que es donde se decide si esto sale bien. Para un fichero
suelto en el que el del framework deba ganar —`README.md` es el caso típico—
añade `"overwrite": true`. Para un directorio **ya versionado** (`app/` o
`app/Config` en un reintento) no lo intentes ni con `overwrite`: la tool lo
rechaza por el mismo motivo que el borrado recursivo, y con razón — mueve dentro
sólo los ficheros que de verdad quieras, y no vacíes el destino para hacer sitio.
Tampoco sirve moverlo a un temporal para borrarlo después: la protección viaja
con el directorio y el borrado del temporal se rechaza igual.

Los pasos 1, 3, 4 y 5 son de la familia de tools `file`, que **no** depende de
la allowlist de comandos del proyecto: siguen estando disponibles aunque
`stack_exec` te niegue medio toolchain.

Antes de dar la tarea por hecha: escribe el `.gitignore` («Qué NO se versiona»)
y
comprueba que el esqueleto responde, por ejemplo con `php spark routes`. Si no
moviste `vendor/`, un `composer install` en la raíz lo reconstruye desde el
`composer.lock` que sí moviste.

Y un atajo que no lo es: `composer require codeigniter4/framework` **no
andamia**. Deja la librería bajo `vendor/`, sin `app/`, sin `public/` y sin
`spark` — es decir, sin lo único que la tarea pide.

### Lo que NO se hace: vaciar el workspace

Borrar lo que estorba para que el andamiador arranque **destruye trabajo de
otras tareas**. No es un riesgo teórico, es lo que pasó:

```text
delete_file {"path": "app", "recursive": true}   ->  ok, entries=85
```

Esas 85 entradas eran el deliverable commiteado de la tarea anterior. `app/` no
tenía ningún significado especial: era la entrada más grande de la lista.

La plataforma ahora lo **rechaza**. Saberlo ahorra iteraciones contra la puerta:

- `delete_file` recursivo sobre un directorio **versionado**, a cualquier
  profundidad (`app/`, `app/Config/`) o que contenga uno, devuelve error:
  _«refusing to recursively delete 'app': it is tracked in this branch…»_. Vale
  para cualquier directorio que ya esté en la rama.
- `delete_file` sobre la **raíz**, también: no es podar un subárbol, es borrar
  el deliverable entero.
- `.git` no se toca: la tool lo rechaza (ADR 0163). Sin él tu trabajo no se
  puede commitear y quedaría hecho, en disco y fuera de toda rama — el desenlace
  del primer run.
- Por `stack_exec` tampoco: `rm -rf ./* ./.??*` rebotó contra la allowlist. Y
  donde `rm` estuviera autorizado sería el mismo destrozo: la razón para no
  hacerlo es el deliverable ajeno, no la guarda.

No insistas con la misma llamada: repetirla con los mismos argumentos dispara
la guarda de bucle —la cuarta idéntica corta el run—, y salta igual aunque la
tool sea de lectura. Relanzar `create-project .` tras borrar un fichero suelto
no es una estrategia nueva; el procedimiento de arriba sí.

### Si el proyecto ya tiene CodeIgniter instalado

No re-andamies. Es el caso del reintento, y es el que costó el deliverable: el
worktree ya traía CI4 de la tarea anterior y aun así se lanzó `create-project`
cuatro veces más.

- Que falte `vendor/` **no** significa que no esté instalado: `vendor/` no se
  versiona («Qué NO se versiona»). Se reconstruye con `composer install`, que
  respeta las versiones exactas del `composer.lock`. `composer update` no:
  reescribe el lock y mete en tu tarea un diff de dependencias que nadie pidió.
- Que falte un directorio concreto (`app/Modules/{Zona}/{Modulo}/…`) es trabajo
  de desarrollo normal: se crea con `write_file`, no reinstalando el framework.
- Trabaja sobre lo que hay. El esqueleto es el deliverable de otra tarea y su
  commit forma parte del plan; tu diff debe ser lo que añades, no una
  reinstalación que reescribe ficheros ajenos.

## Qué NO se versiona

`composer create-project codeigniter4/framework .` **no deja un `.gitignore` en
la raíz**. Comprobado el 2026-08-31 sobre una instalación intacta: los únicos
`.gitignore` del árbol son los que traen dentro los paquetes de `vendor/`.

Consecuencia si nadie lo escribe: el primer commit se lleva `vendor/` entero.
Medido en ese mismo proyecto — 4.442 ficheros de dependencias en la rama del
plan, sobre un total de 5.192. El diff de revisión de la tarea siguiente y el PR
del plan quedan ilegibles, y cada worktree posterior arrastra la copia.

**Escribe el `.gitignore` en la misma tarea que instala el esqueleto**, antes de
dar la tarea por hecha:

```gitignore
/vendor/
/writable/cache/
/writable/logs/
/writable/session/
/writable/uploads/
/writable/debugbar/
.env
/.php-cs-fixer.php
/.phpunit.cache
/phpunit.xml
/tests/coverage*
```

Tres notas sobre por qué esa lista y no otra:

- **`/vendor/`** lo reconstruye `composer install` desde `composer.lock`, que sí
  se versiona. Versionar ambos es guardar dos veces lo mismo, y sólo uno de los
  dos manda.
- **`/writable/`** se versiona en su _estructura_ pero no en su contenido: CI4
  necesita que los directorios existan, así que ignora los subdirectorios de
  runtime uno a uno en vez de la carpeta entera.
- **`.env`** nunca; commitea `env` (el ejemplo que ya trae el framework). Es la
  misma regla que está en la skill de seguridad de este stack.

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
