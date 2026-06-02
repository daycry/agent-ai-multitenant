---
title: "WebScorpo DevOps — Role Knowledge"
scope: private
role: devops
agent_name: webscorpo-devops
audience: webscorpo-devops
doc_id: agent-devops-role-knowledge
source: C:/tmp/webscorpo-analysis.md §6.4, §9 (webscorpo-devops); azure-pipelines.yml; Dockerfile; composer.json
---

# WebScorpo DevOps — Role Knowledge

**Role**: DevOps / Release (can fold into PM).

**Why this role exists**: Azure Pipelines, Docker image, dual-region EUS/WEU deploy, Zabbix
monitoring endpoint, env/secret management, writable-dir permissions.

## Azure Pipelines (`azure-pipelines.yml`)

- Triggers: `release`, `development`, `main`.
- Stages: **Build** (PHP setup, CI minify, PHPUnit + MySQL service, `build.zip`) → **Deploy EUS** →
  **Deploy WEU** (both `main`-only).
- Shared template library pinned at `refs/tags/v3.1.5`.

## Docker image (`Dockerfile`)

- Base `php:8.4-fpm-alpine` (ARG `PHP_VERSION=8.4`), single `production` stage.
- Deps: nginx, supervisor, icu-dev, libzip-dev, openldap-dev, oniguruma-dev.
- Extensions: mbstring, opcache (conditional), intl, mysqli, pdo_mysql, zip, ldap.
- PHP ini: `memory_limit=256M`, upload/post 50M, `max_execution_time=300`,
  `date.timezone=Europe/Madrid`, `variables_order=EGPCS`.
- OPcache tuned (`validate_timestamps=0`, `max_accelerated_files=10000`, etc.).
- Configs: `docker/nginx.conf` → `/etc/nginx/http.d/default.conf`, `docker/supervisord.conf`,
  `docker/entrypoint.sh`.
- Writable dirs `chmod 755`: `writable/{cache,logs,session,uploads}`, owner `www-data`.
- `ENV CI_ENVIRONMENT=production`; `EXPOSE 8080`.

## Dual-region deploy

Azure App Service Linux, East US + West Europe, both from `main` only. Sequence EUS → WEU.

## Monitoring

Zabbix polls `/api/v1/monitoring/status`. Monitoring keys (`$monitoringKey`,
`$apiKeyForApiMonitoring`) currently hardcoded in `Config/WebsCorpo.php` — move to env/secret store
and rotate (coordinate with auth-security).

## Env / secrets

`.env` keys: `CI_ENVIRONMENT`, `API_TOKEN_HEADER`/`API_TOKEN`, `AUTH_MODE`,
`app.baseURL`/`allowedHostnames`/`forceGlobalSecureRequests`, `cache.*`, `database.default.*`,
`encryption.key`, `logger.threshold`, `minifier.*`, `auth.allowRegistration`, `gdi.*` (SSO).

## Composer Azure DevOps VCS auth

Two SSH VCS repos provide the SSO stack (`VENDOR - AzureOauthClient`, `GdiLibraryHelper`); installs
need Azure DevOps SSH access. See team `10-dependency-catalog.md`.

See team `07-cicd-deploy-runbook.md`.
