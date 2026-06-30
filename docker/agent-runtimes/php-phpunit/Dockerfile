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
# clients that hit SOAP services. Anything more specific lives in the project's
# composer.json and is installed by the worker at task start.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git unzip libzip-dev libpq-dev libonig-dev libicu-dev libxml2-dev \
 && rm -rf /var/lib/apt/lists/* \
 && docker-php-ext-install -j$(nproc) zip pdo pdo_pgsql pdo_mysql mbstring intl soap \
 && curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer

WORKDIR /workspace

ENTRYPOINT ["sleep", "infinity"]
