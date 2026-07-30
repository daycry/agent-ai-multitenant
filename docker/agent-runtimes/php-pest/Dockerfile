# syntax=docker/dockerfile:1.7
# agent-runtime-php-pest:v1 — test-runtime template for PHP + Pest (Plan 06 task_06_02).
#
# Pest is a layer on top of PHPUnit so the runtime is the same as
# php-phpunit; we ship a separate template so task acceptance criteria
# can distinguish the two and the output parser knows which signal to
# look for in the report.

FROM php:8.3-cli AS runtime

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
