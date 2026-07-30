# syntax=docker/dockerfile:1.7
# agent-runtime-ruby-rspec:v1 — test-runtime template for Ruby + RSpec (Plan 06 task_06_02).

FROM ruby:3.3-slim AS runtime

LABEL org.opencontainers.image.title="agent-runtime-ruby-rspec" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Test-runtime template: Ruby 3.3 + Bundler + RSpec."

ENV BUNDLE_PATH=/usr/local/bundle \
    BUNDLE_BIN=/usr/local/bundle/bin \
    PATH=/usr/local/bundle/bin:$PATH

# Build deps for native gems (nokogiri, pg, sqlite3, etc.). Kept here
# rather than in the project's pre_install so we don't pay apt-get on
# every task.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential git libpq-dev libsqlite3-dev libxml2-dev libxslt1-dev \
 && rm -rf /var/lib/apt/lists/*

RUN gem install bundler rspec

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
