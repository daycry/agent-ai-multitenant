# syntax=docker/dockerfile:1.7
# agent-runtime-php-pest:v1 — test-runtime template for PHP + Pest (Plan 06 task_06_02).
#
# Pest is a layer on top of PHPUnit so the runtime is the same as
# php-phpunit; we ship a separate template so task acceptance criteria
# can distinguish the two and the output parser knows which signal to
# look for in the report.

# ---- composer: binario verificado, NO `curl | php` ------------------
# prod-11 task_composer_checksum_04 (hallazgo gap5-5). Misma historia que
# php-phpunit: el `curl -sS https://getcomposer.org/installer | php` era
# ejecución remota de código sin verificar en el build de una imagen que
# después ejecuta código NO confiable. La imagen oficial pineada por digest da
# la garantía del checksum y, al ser una etapa `FROM`, Dependabot la refresca.
FROM composer:2@sha256:4d71c3c2109c61d5415544264b59ad4087e4c5b7244481723664138fd36d5040 AS composer-bin

# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
#
# 2026-08-13: digest refrescado. El anterior era un trixie 13.6 con 61 CVE
# HIGH CON fix publicado en apt (el riesgo 3 de prod-11 en estado puro: el pin
# sin refresco congela las CVE de la base). La versión de PHP no cambia — sube
# el snapshot de Debian.
FROM php:8.5-cli@sha256:54d82ff9be6bd198145e90c917fc9b2e24230b42e52def8deb3554baf61c451a AS runtime

LABEL org.opencontainers.image.title="agent-runtime-php-pest" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: PHP 8.3 + Composer + Pest."

ENV COMPOSER_ALLOW_SUPERUSER=1 \
    COMPOSER_NO_INTERACTION=1

# `intl` (libicu) is REQUIRED by CodeIgniter 4 (its I18n/Time uses `Locale`;
# without it `php spark` fatals `Class "Locale" not found`, exit 255). `soap`
# (libxml2) for tests/clients hitting SOAP services. `pcov` = coverage driver
# (Pest corre sobre PHPUnit: sin driver, un phpunit.xml con `<coverage>` sale
# exit 1 con la suite verde — hallazgo #5, QA 2026-07-07).
# 2026-08-13: `apt-get -y upgrade` ANTES de instalar. La base oficial de PHP se
# reconstruye con retraso respecto al archivo de seguridad de Debian, y hoy eso
# son 49 CVE HIGH CON fix publicado (todas de `linux-libc-dev`, que entra como
# dependencia del toolchain que `pecl` y `docker-php-ext-install` necesitan).
# No se arregla refrescando el digest: se comprobó con el más reciente y
# seguían ahí. Aplicar el parche que Debian YA publicó es el arreglo.
#
# 2026-08-18: la frase que seguía aquí —«a diferencia de un pin se cura solo en
# el siguiente build»— era FALSA, y justo por eso este job se puso rojo. Lo que
# se cierra ahora es CVE-2026-6473 (HIGH) en `libpq-dev`/`libpq5`: instalada
# 17.10-0+deb13u1, corregida 17.11-0+deb13u1 en `trixie-security`.
#
# Es DERIVA DE CVE, no una regresión nuestra: esta plantilla pasó Trivy en verde
# el 13-08 a las 23:33 y falló el 14-08 a las 08:53 sin que cambiara una línea de
# este Dockerfile. Lo que se movió fue la base de datos de Trivy, no la imagen.
#
# Pero el arreglo NO puede ser el de `a8022af2` (refrescar el digest de la base),
# y conviene dejar dicho por qué, que si no el próximo lector lo intenta:
#
#   1. No hay digest nuevo que poner. `php:8.3-cli` SIGUE resolviendo hoy al
#      mismo `f62df991…` que ya está pineado arriba, y `composer:2` al mismo
#      `4d71c3c2…`.
#   2. `libpq` tampoco viene de la base. Comprobado ejecutando la base pineada:
#      no trae `libpq5` ni `libpq-dev` instalados — entran por ESTE `apt-get
#      install` de aquí abajo, y con el índice fresco `apt` ya ofrece 17.11 como
#      candidata desde `trixie-security`.
#
# La causa real es la CACHÉ DE CAPAS. El workflow construye con `cache-from` /
# `cache-to: type=gha`, y la clave de esta capa sale del digest de la base y del
# TEXTO de este RUN. Como el 14-08 no había cambiado ninguno de los dos, buildkit
# la dio por buena (`#13 CACHED`, literal en el log del job) y el `apt-get -y
# upgrade` de aquí arriba no llegó a ejecutarse: se reutilizó tal cual la capa
# construida el 13-08, cuando Debian aún no había publicado 17.11. Un
# `apt-get upgrade` que no se ejecuta no parchea nada.
#
# De ahí esta ÉPOCA. Su valor entra en la clave de caché del RUN —verificado a
# mano: mismo valor → capa cacheada, valor distinto → capa re-ejecutada—, así que
# subir la fecha obliga a rehacer el `update`+`upgrade` contra el archivo de
# seguridad del día.
#
# Y el precio, dicho en voz alta para no repetir el error de la frase anterior:
# esto NO se cura solo. Es la misma palanca manual que refrescar un digest, con
# la misma cadencia de mantenimiento — cuando Debian publique el siguiente
# parche de seguridad habrá que volver a subir la fecha. Lo que NO se hace es
# recortar la severidad de Trivy, añadir una entrada a `.trivyignore`, meter
# `continue-on-error` ni soltar el pin del digest: el gate sigue igual de duro;
# lo que se arregla es la imagen.
ARG APT_SECURITY_EPOCH=2026-08-18
RUN echo "apt security epoch: ${APT_SECURITY_EPOCH}" \
 && apt-get update \
 && apt-get -y upgrade \
 && apt-get install -y --no-install-recommends \
      git unzip libzip-dev libpq-dev libonig-dev libicu-dev libxml2-dev \
 && rm -rf /var/lib/apt/lists/* \
 && docker-php-ext-install -j$(nproc) zip pdo pdo_pgsql pdo_mysql mbstring intl soap \
 && pecl install pcov \
 && docker-php-ext-enable pcov

COPY --from=composer-bin /usr/bin/composer /usr/local/bin/composer

WORKDIR /workspace

# prod-12 task_prod12_img_01 (sandbox-6): el worker fuerza uid 1000 en cada
# run; la imagen le da un HOME escribible para que los dep-caches (pip/
# composer/npm/...) funcionen, y arranca no-root tambien por defecto
# (defensa en profundidad, como el agent-runtime). USER numerico: no
# necesita entrada en /etc/passwd (uniforme en debian y alpine).
RUN mkdir -p /home/agent && chown -R 1000:1000 /home/agent
ENV HOME=/home/agent
USER 1000:1000

ENTRYPOINT ["sleep", "infinity"]
