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

ENTRYPOINT ["sleep", "infinity"]
