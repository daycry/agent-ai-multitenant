# Estrategia de testing: CodeIgniter 4

Guía práctica para testear aplicaciones CodeIgniter 4 con PHPUnit: tres
suites (Unit, Integration, E2E), configuración estricta (zero-tolerance),
cobertura, datos de prueba y mutation testing con Infection. Referencia para
agentes QA que escriben, ejecutan o revisan tests sobre un stack CI4.

## Suites de PHPUnit

Organiza las pruebas en tres suites declaradas en `phpunit.xml.dist`, cada una
con un propósito y coste distintos:

- **Unit** (`tests/unit`): pruebas puras, sin base de datos ni framework
  arrancado. Son las más rápidas; cúbrelas primero.
- **Integration** (`tests/integration`, ficheros con sufijo `Test.php`):
  tocan base de datos, servicios o el ciclo de request de CodeIgniter
  (`CIUnitTestCase`, `FeatureTestTrait`, `DatabaseTestTrait`).
- **E2E** (`tests/E2E`, sufijo `Test.php`): recorridos de navegador reales
  (login, flujos de contenido) con Selenium + Chrome y captura de pantallas.

Conviene tener scripts de Composer que separen los tiempos de ejecución: uno
que lance Unit + Integration en un solo arranque de PHPUnit y otro
independiente para la suite E2E (Selenium), que es lenta y depende de un
WebDriver.

```xml
<testsuites>
  <testsuite name="Unit">
    <directory>tests/unit</directory>
  </testsuite>
  <testsuite name="Integration">
    <directory suffix="Test.php">tests/integration</directory>
  </testsuite>
  <testsuite name="E2E">
    <directory suffix="Test.php">tests/E2E</directory>
  </testsuite>
</testsuites>
```

## Modo estricto (zero-tolerance)

Configura PHPUnit para que cualquier ruido haga fallar la ejecución. Esto
mantiene la suite honesta: un test "risky", una deprecación o salida perdida
no pasan silenciosamente.

En `phpunit.xml.dist`:

- `beStrictAboutOutputDuringTests="true"` — salida inesperada falla el test.
- `failOnRisky="true"` y `failOnWarning="true"`.
- `stopOnError`, `stopOnFailure`, `stopOnWarning`, `stopOnRisky` — corta a la
  primera incidencia para feedback rápido en local.

Regla: un test que no asierta nada, que produce salida o que dispara un
warning de deprecación se considera fallo, no aviso.

## Entorno de test (bloque `<php>`)

Aísla el entorno de pruebas del de desarrollo mediante variables en el bloque
`<php>` de `phpunit.xml.dist`. Valores típicos en CI4:

- `app.baseURL` apuntando a un host de ejemplo (p. ej. `https://example.com/`).
- `encryption.key` con una clave de test propia (formato `hex2bin:...`).
  Genera una clave dedicada para tests; **no reutilices** la de producción ni
  la dejes en texto plano fuera del entorno de pruebas.
- `security.csrfProtection = session` forzado para reproducir el flujo real.
- Grupo de BD de tests aislado: `database.tests.foreignKeys = true`,
  `database.tests.DBPrefix = ""`, y apuntar `auth.DBGroup`,
  `settings.database.group` y los grupos de cronjobs al grupo `tests`.
- Una variable de entorno para la API key que usan los tests REST (inyéctala
  por entorno/CI, nunca con un valor real hardcodeado en el repositorio).
- `memory_limit = 512M` o superior: la generación de cobertura HTML consume
  bastante más que el límite por defecto de PHP.

## Bootstrap y Selenium para E2E

El `tests/bootstrap.php` carga el autoloader y registra las extensiones de
PHPUnit. Para los recorridos E2E se usa la extensión Selenium de
`daycry/phpunit-extension-selenium` (`Daycry\PHPUnit\Selenium\SeleniumExtension`):

- Configura el navegador (`chrome`) y el directorio de capturas de pantalla
  (p. ej. `build/selenium/screenshots`) para depurar fallos visuales.
- La extensión gestiona el arranque y cierre ordenado del WebDriver.
- Cada test E2E modela un recorrido de usuario (login, alta de un recurso,
  navegación de un listado), no un detalle interno.

Mantén E2E como una suite aparte: es la más frágil y lenta, y no debe bloquear
el ciclo rápido de Unit + Integration.

## Datos de prueba y dobles

- **Faker** para generar datos realistas en fixtures y factories.
- **vfsStream** para simular el sistema de ficheros sin tocar disco real.
- **VCR** (`daycry/phpunit-extension-vcr`) para grabar y reproducir respuestas
  HTTP de servicios externos, de modo que los tests no dependan de la red.
- Para Integration con BD usa `DatabaseTestTrait` con migraciones + seeds, y
  transacciones que se revierten al terminar cada test (`$refresh`), dejando la
  base limpia.

## Cobertura

Configura los reportes de cobertura que necesites en el bloque `<coverage>` y
en los `<logging>`:

- **Cobertura XML** (`build/logs/cobertura.xml`): formato que la mayoría de
  pipelines CI consumen para mostrar la pestaña de cobertura.
- **HTML** (`build/coverage/` o `build/logs/html`): navegable en local vía un
  script `@test-coverage`.
- **Texto** en stdout y **testdox** (`build/logs/testdox.{html,txt}`) para un
  resumen legible de qué comportamiento cubre cada test.
- **JUnit** (`build/logs/logfile.xml`) para que el CI liste los tests.

Acota la cobertura al código de aplicación (`./app`) y excluye lo que no aporta
señal: migraciones de módulos, `./app/Views` y la configuración de rutas
(`./app/Config/Routes.php`). Evita emitir el formato de cobertura `php`
serializado: para suites grandes provoca consumo excesivo de memoria (OOM).

Trata la cobertura como una métrica que sube de forma incremental: fija un
objetivo razonable (>70% en la capa de dominio/servicios) y prioriza con el PM
los módulos críticos sin cobertura, en lugar de perseguir un porcentaje global
de golpe.

## Mutation testing con Infection

Añade Infection (`infection.json.dist`) sobre las suites Unit + Integration
para medir la calidad real de los tests (no solo qué líneas se ejecutan, sino
si las aserciones detectan cambios en el código):

- Un script `@mutation` lanza Infection y deja los reportes en `build/mutation/`.
- Infection **requiere una suite verde** antes de correr: si los tests fallan,
  el resultado de mutación no es fiable.
- Usa el MSI (Mutation Score Indicator) como objetivo de calidad por módulo,
  empezando por el código de negocio más sensible.

## Scripts de Composer recomendados

Centraliza los comandos en `composer.json` para que CI y desarrollo usen la
misma definición. Ejemplos neutros:

```json
{
  "scripts": {
    "test": "phpunit --testsuite Unit,Integration",
    "test-e2e": "phpunit --testsuite E2E",
    "test-coverage": "phpunit --testsuite Unit,Integration --coverage-html build/coverage",
    "mutation": "infection --threads=max --min-msi=60"
  }
}
```

El gate de CI debe ejecutar al menos `test` (Unit + Integration) en verde;
E2E y mutation pueden correr en etapas separadas por su coste.

## Checklist para el agente QA

1. Toda funcionalidad nueva trae tests Unit y, si toca BD o el request,
   Integration.
2. La suite corre en modo estricto: nada de salida perdida ni tests "risky".
3. Las dependencias externas (HTTP, filesystem) se doblan con VCR / vfsStream;
   los tests no dependen de la red ni de estado en disco.
4. Las claves y credenciales de test vienen del entorno/CI, nunca hardcodeadas.
5. La cobertura no baja respecto al baseline; los módulos críticos suben.
6. Antes de mergear, la suite Unit + Integration está verde y, si aplica, el
   MSI de Infection no regresiona.
