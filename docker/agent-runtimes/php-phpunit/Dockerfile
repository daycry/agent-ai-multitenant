# syntax=docker/dockerfile:1.7
# agent-runtime-php-phpunit:v1 — test-runtime template for PHP + PHPUnit (Plan 06 task_06_02).

FROM php:8.3-cli AS runtime

LABEL org.opencontainers.image.title="agent-runtime-php-phpunit" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: PHP 8.3 + Composer + PHPUnit."

ENV COMPOSER_ALLOW_SUPERUSER=1 \
    COMPOSER_NO_INTERACTION=1

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
 && docker-php-ext-enable pcov \
 && curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer

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
