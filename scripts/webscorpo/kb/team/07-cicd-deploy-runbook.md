---
title: "WebScorpo — CI/CD & Deploy Runbook"
scope: team_shared
audience: all-agents
doc_id: team-07-cicd-deploy-runbook
source: C:/tmp/webscorpo-analysis.md §6.4; azure-pipelines.yml; Dockerfile
---

# WebScorpo — CI/CD & Deploy Runbook

## CI/CD — Azure Pipelines (`azure-pipelines.yml`)

- **Triggers**: branches `release`, `development`, `main`.
- **Stages**:
  1. **Build** — PHP setup, CI minify (`michalsn/minifier` + CI minify step), PHPUnit with a MySQL
     service, produces `build.zip`. Runs `@ci` (= `@quality` + `@test`) effectively per push.
  2. **Deploy EUS** (East US) — `main` only.
  3. **Deploy WEU** (West Europe) — `main` only.
- Uses a shared Azure template library pinned at `refs/tags/v3.1.5`.

## Runtime image (`Dockerfile`)

- Base **`php:8.4-fpm-alpine`** (build ARG `PHP_VERSION=8.4`), single `production` stage.
- System deps: `nginx`, `supervisor`, `icu-dev`, `libzip-dev`, `openldap-dev`, `oniguruma-dev`.
- PHP extensions: `mbstring`, `opcache` (conditional), `intl`, `mysqli`, `pdo_mysql`, `zip`, `ldap`.
- PHP ini (production): `memory_limit=256M`, `upload_max_filesize=50M`, `post_max_size=50M`,
  `max_execution_time=300`, `date.timezone=Europe/Madrid`, `variables_order=EGPCS`.
- OPcache: enabled, `memory_consumption=128`, `interned_strings_buffer=16`,
  `max_accelerated_files=10000`, `validate_timestamps=0`.
- Configs copied: `docker/nginx.conf` → `/etc/nginx/http.d/default.conf`,
  `docker/supervisord.conf`, `docker/entrypoint.sh` (entrypoint).
- Writable dirs created with `chmod 755`: `writable/cache`, `writable/logs`, `writable/session`,
  `writable/uploads`; owner `www-data`.
- `ENV CI_ENVIRONMENT=production`; `EXPOSE 8080`.

## Deploy topology

Azure App Service Linux, **dual-region** (East US + West Europe), Docker (Nginx + PHP-FPM +
Supervisor), port **8080**. Both regions deploy only from `main`.

## Monitoring

Zabbix polls `/api/v1/monitoring/status` (health check). Monitoring keys currently live in
`Config/WebsCorpo.php` — see `09-security-baseline.md` for the remediation.

## Deploy checklist (agents)

1. Migrations applied and reversible (per-module Doctrine migrations).
2. `@ci` green on the branch.
3. Asset versions bumped (`public/assets/versions.json`) if assets changed.
4. Merge to `main` → triggers EUS then WEU deploy. Do not deploy from `development`/`release`.
