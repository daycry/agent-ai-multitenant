# syntax=docker/dockerfile:1.7
# agent-runtime-php-phpunit:v1 — test-runtime template for PHP + PHPUnit (Plan 06 task_06_02).

# ---- composer: binario verificado, NO `curl | php` ------------------
# prod-11 task_composer_checksum_04 (hallazgo gap5-5). Hasta 2026-07-31 esta
# imagen hacía `curl -sS https://getcomposer.org/installer | php`: ejecución
# remota de código SIN verificar dentro del build de una imagen que después
# ejecuta código NO confiable (Principio Rector 2). Un getcomposer.org
# comprometido —o un MITM sobre el build— entraba directo al sandbox.
#
# La imagen oficial pineada por digest da la misma garantía que un checksum y
# encima es una etapa `FROM`, así que Dependabot (ecosistema docker) la refresca
# sola; un `COPY --from=composer:2@sha256:…` en línea NO lo parsea.
FROM composer:2@sha256:629d4ef35e75349d452851637d37a40ac33a09d6ac010139020603d79713d9bf AS composer-bin

# Base pineada por DIGEST (prod-11 task_digest_pin_11, hallazgo gap5-3): un tag
# de imagen es mutable y esta es una de las imagenes donde el Principio Rector 2
# deposita el aislamiento del codigo NO confiable. El refresco lo hace Dependabot
# (ecosistema docker, .github/dependabot.yml).
FROM php:8.3-cli@sha256:db99254edaf6de1644b16afa38b150b8b89f05ec4091a631b59a954dd1330058 AS runtime

LABEL org.opencontainers.image.title="agent-runtime-php-phpunit" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: PHP 8.3 + Composer + PHPUnit."

# AUD16-26: COMPOSER_HOME explícito bajo el HOME escribible de uid 1000 —
# el ruido "no escribible" de los runs históricos venía de imágenes previas a
# prod-12 img_01; el env explícito lo hace inmune a overrides de HOME.
ENV COMPOSER_ALLOW_SUPERUSER=1 \
    COMPOSER_NO_INTERACTION=1 \
    COMPOSER_HOME=/home/agent/.composer

# Composer + the extensions Laravel / Symfony / CodeIgniter 4 projects expect
# most often. `intl` (libicu) is REQUIRED by CodeIgniter 4 — its I18n/Time class
# uses the `Locale` class; without it `php spark` fatals with
# `Class "Locale" not found` (exit 255). `soap` (libxml2) is needed by tests/
# clients that hit SOAP services. `pcov` (pecl) is the coverage driver: without
# one, a phpunit.xml with a `<coverage>` block warns "No code coverage driver
# available" and PHPUnit exits 1 con la suite VERDE (hallazgo #5, QA
# 2026-07-07) — quemaba turnos del agente y confundía al reviewer. Anything
# more specific lives in the project's composer.json and is installed by the
# worker at task start.
RUN apt-get update \
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
RUN mkdir -p /home/agent/.composer && chown -R 1000:1000 /home/agent
ENV HOME=/home/agent
USER 1000:1000

ENTRYPOINT ["sleep", "infinity"]
