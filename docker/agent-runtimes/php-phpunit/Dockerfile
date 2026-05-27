# syntax=docker/dockerfile:1.7
# agent-runtime-php-phpunit:v1 — test-runtime template for PHP + PHPUnit (Plan 06 task_06_02).

FROM php:8.3-cli AS runtime

LABEL org.opencontainers.image.title="agent-runtime-php-phpunit" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: PHP 8.3 + Composer + PHPUnit."

ENV COMPOSER_ALLOW_SUPERUSER=1 \
    COMPOSER_NO_INTERACTION=1

# Composer + the extensions Laravel / Symfony projects expect most often.
# Anything more specific lives in the project's composer.json and is
# installed by the worker at task start.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git unzip libzip-dev libpq-dev libonig-dev \
 && rm -rf /var/lib/apt/lists/* \
 && docker-php-ext-install -j$(nproc) zip pdo pdo_pgsql pdo_mysql mbstring \
 && curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer

WORKDIR /workspace

ENTRYPOINT ["sleep", "infinity"]
