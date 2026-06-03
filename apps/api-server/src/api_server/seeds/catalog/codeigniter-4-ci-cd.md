# CodeIgniter 4 — CI/CD y despliegue

Guía práctica de integración y entrega continua para aplicaciones CodeIgniter 4
(PHP 8.2+) construidas con un stack basado en `daycry/*` (auth, doctrine, twig),
HMVC bajo `app/Modules/`, Doctrine ORM y PHPUnit. Referencia para agentes de
DevOps/Release y para el gate de revisión que decide la promoción a la rama
principal. Los ejemplos son neutros: adapta nombres de imagen, registry y
proveedor de pipeline a tu entorno.

## Pipeline CI/CD: estructura por etapas

El pipeline modela el flujo **build → test → deploy** y es agnóstico del
proveedor (GitHub Actions, GitLab CI, Jenkins, Azure Pipelines, etc.). Una
estructura típica de tres etapas:

1. **Build** — instala dependencias (`composer install --no-dev` en release),
   compila y versiona los assets, prepara el artefacto desplegable (p. ej. un
   `build.zip` o una imagen Docker).
2. **Test** — ejecuta el gate de calidad y la batería de tests (`composer ci`,
   ver más abajo) contra un servicio de base de datos efímero.
3. **Deploy** — promociona el artefacto al entorno destino. Solo se dispara
   desde la rama principal (`main`); las ramas de feature/desarrollo se quedan
   en build + test.

Define los disparadores (`triggers`) de forma explícita por rama y reserva el
deploy para `main`. Mantén la lógica reutilizable del pipeline en plantillas o
workflows compartidos y versionados (pin por tag, no por rama móvil).

## El gate de calidad: `composer ci`

El proyecto expone scripts de Composer que el pipeline invoca como un solo
comando. Un patrón habitual:

```json
{
  "scripts": {
    "quality": ["@cs-check", "@phpstan", "@psalm", "@phpcpd"],
    "test": "phpunit",
    "ci": ["@quality", "@test"]
  }
}
```

- `@quality` agrupa el análisis estático y de estilo: PHP-CS-Fixer (estándar
  CI4), PHPStan, Psalm y detección de duplicación (phpcpd).
- `@test` ejecuta PHPUnit (suites Unit/Integration/E2E).
- `@ci` es la unión de ambos: es lo que corre en cada push.

El pipeline ejecuta `composer ci` y falla si cualquier sub-script devuelve un
código distinto de cero. Mantén el gate idéntico en local y en CI para evitar
sorpresas ("en mi máquina pasaba").

## Política de baseline (PHPStan / Psalm)

PHPStan y Psalm admiten un _baseline_ que silencia los hallazgos preexistentes.
Reglas de gobierno:

- El código **nuevo** debe estar limpio: solo se toleran las entradas ya
  presentes en el baseline.
- **No** añadas supresiones nuevas al baseline para ocultar problemas recién
  introducidos. Un baseline que crece commit a commit es una señal de alarma en
  revisión (baseline drift).
- El baseline se reduce con el tiempo, no se infla. Cuando arregles deuda,
  regenera el baseline para que esos hallazgos dejen de tolerarse.

## Imagen de runtime (Docker)

La aplicación se empaqueta en una imagen Docker basada en PHP-FPM + un servidor
web (Nginx) gestionados por Supervisor dentro del mismo contenedor. Ejemplo
neutro de `Dockerfile`:

```dockerfile
ARG PHP_VERSION=8.3
FROM php:${PHP_VERSION}-fpm-alpine AS production

# Dependencias de sistema y extensiones PHP típicas de CI4 + Doctrine
RUN apk add --no-cache nginx supervisor icu-dev libzip-dev oniguruma-dev \
    && docker-php-ext-install -j"$(nproc)" intl mbstring zip pdo_mysql opcache

# Config de servicios
COPY docker/nginx.conf       /etc/nginx/http.d/default.conf
COPY docker/supervisord.conf /etc/supervisord.conf
COPY docker/entrypoint.sh    /usr/local/bin/entrypoint
RUN chmod +x /usr/local/bin/entrypoint

# Aplicación + permisos de directorios escribibles de CI4
COPY . /var/www/html
RUN mkdir -p writable/cache writable/logs writable/session writable/uploads \
    && chmod -R 755 writable \
    && chown -R www-data:www-data writable

ENV CI_ENVIRONMENT=production
EXPOSE 8080
ENTRYPOINT ["entrypoint"]
```

Notas de configuración:

- Fija `PHP_VERSION` como ARG para poder bumpearla de forma controlada.
- Ajusta el `php.ini` de producción: `memory_limit`, `upload_max_filesize`,
  `post_max_size`, `max_execution_time`, zona horaria y `variables_order`.
- Activa y afina OPcache en producción (`opcache.validate_timestamps=0`,
  `opcache.max_accelerated_files`, `opcache.memory_consumption`).
- Crea los directorios `writable/{cache,logs,session,uploads}` con permisos
  correctos y propietario del usuario del servidor web; son requisito de CI4.
- `CI_ENVIRONMENT=production` desactiva el modo debug y las trazas detalladas.

## Configuración por entorno y secretos

CodeIgniter 4 lee la configuración de entorno desde `.env` (y de variables de
entorno reales en runtime). Claves habituales a parametrizar:

- `CI_ENVIRONMENT` (`production` / `development` / `testing`).
- `app.baseURL`, `app.allowedHostnames`, `app.forceGlobalSecureRequests`.
- `database.default.*` (host, base, usuario, contraseña).
- `encryption.key`.
- `cache.*`, `logger.threshold`, `minifier.*`.
- Parámetros de `daycry/auth` (registro habilitado, validadores, etc.).

Regla dura: **no hardcodees secretos** en el código ni en clases de
configuración. Las credenciales, tokens y claves de API se inyectan por
variable de entorno o por un gestor de secretos, nunca como literales en el
repositorio. Si detectas un secreto comprometido, rótalo y muévelo al store de
secretos (coordina con el rol de seguridad/auth).

## Versionado de assets

Si el build compila o minifica assets, versiona el resultado para invalidar
caché de navegador (p. ej. con `michalsn/minifier` y un `versions.json`).
Bumpea la versión cuando cambien los assets, e inclúyelo en el checklist de
deploy para que CSS/JS antiguos no queden cacheados.

## Health check y observabilidad

Expón un endpoint de salud (p. ej. `/health` o `/status`) que el sistema de
monitorización pueda sondear para verificar que la app y sus dependencias
(base de datos, caché) responden. El endpoint debe ser barato y no requerir
autenticación de usuario, pero **no** debe filtrar información sensible ni
exponer secretos: si protege con una clave, esa clave vive en el store de
secretos, no en el código. Emite logs estructurados con un id de correlación y,
si procede, métricas para el sistema de observabilidad.

## Checklist de deploy

Antes de promocionar a la rama principal y disparar el deploy:

1. **Migraciones** aplicadas y **reversibles** (migraciones Doctrine por módulo;
   verifica el `down()`).
2. **`composer ci` verde** en la rama (calidad + tests).
3. **Assets versionados** si hubo cambios en CSS/JS.
4. **Sin nuevos hallazgos de baseline** en PHPStan/Psalm.
5. **i18n**: ambos locales (EN/ES) presentes para campos/strings nuevos.
6. **Sin secretos hardcodeados** ni configuración de seguridad relajada (p. ej.
   modos de auth deshabilitados) que se cuele a producción.
7. **Rama destino correcta**: solo `main` despliega.

## El gate de merge-to-main

La promoción a la rama principal es la frontera de calidad. El revisor (o el
job de CI que actúa como gatekeeper) hace cumplir:

1. `composer ci` en verde (`@quality` + `@test`).
2. Conventional Commits y un PR por unidad de trabajo.
3. Sin nuevas entradas en el baseline ni secretos hardcodeados nuevos.
4. Migraciones reversibles y assets versionados si cambiaron.
5. i18n completo para los dos locales soportados.
6. Hallazgos de seguridad no regresionados.
7. Solo la rama `main` dispara el deploy: confirma el target del PR.

El veredicto del gate es binario. Cuando un agente revisor emite su decisión,
la expresa de forma explícita, por ejemplo:

```
<verdict>approve</verdict>
```

o

```
<verdict>reject</verdict>
```

Un `reject` debe acompañarse de los motivos concretos y accionables (qué falla,
en qué fichero, cómo corregirlo) para que el desarrollo pueda iterar sin
ambigüedad.
