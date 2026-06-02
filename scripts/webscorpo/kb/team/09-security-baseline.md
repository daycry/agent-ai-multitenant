---
title: "WebScorpo — Security Baseline & Known Findings"
scope: team_shared
audience: all-agents
doc_id: team-09-security-baseline
source: C:/tmp/webscorpo-analysis.md §6.5, §6.6; app/Config/WebsCorpo.php
---

# WebScorpo — Security Baseline & Known Findings

## Auth surface

- Authenticators (daycry/auth): session (default web) + JWT + access-token + guest.
- Groups/permissions: `admin_corpo` (admin gate), `webproject:admin` (content gate); default group
  `user`. Permissions are stored as `{project}.{permission}` and checked by `WebProjectFilter`.
- Password validators: Composition, Dictionary, NothingPersonal.
- API: Access Token via `X-API-KEY` header + JWT Bearer; per-method rate limiting (e.g. 1 req/min,
  configurable). WebProject context enforced on API routes.
- Login attempts recorded on **FAILURE only** (production-safe); no IP blocking by default.
- CSRF: session-based (`security.csrfProtection = session`). CSP + Cookies managed by dedicated
  Admin modules.

## Known findings (remediation backlog) — confirmed in `app/Config/WebsCorpo.php`

1. **Hardcoded secrets in `Config/WebsCorpo.php`**:
   - `$monitoringKey = 'TLd6BybnLV3eaLRkcqZgQCBPcH8ukj3O'` (Zabbix).
   - `$apiKeyForApiMonitoring = 'VqsRBtYehlwmJXap7g7dhd4J9rlgO0wr'` (CMS/API status checks).
     Both literal strings in source → must move to `.env` / a secret store.
     > Note: the same value as `$apiKeyForApiMonitoring` is also wired into `phpunit.xml.dist` as
     > `X-API-KEY-TESTS`; rotate together.
2. **Local-machine path leak**: `$exifToolPath = 'c:/laragon/tools/exiftool-13.34_64/exiftool'` — a
   Windows dev path shipped in committed config (breaks on the Linux deploy image). Must be
   env-driven.
3. **`AUTH_MODE=skip`** dev bypass in `LoginController::sso()` fabricates a logged-in session — must
   be impossible to enable in any non-dev environment.
4. **JWT authenticator configured but unused in routes** — dead surface to confirm/remove or wire
   intentionally.

## Remediation playbook

- Move all three config secrets/paths to `.env` keys; keep `Config/WebsCorpo.php` reading from env
  with safe defaults (no literals).
- Add a deploy-time assertion that `AUTH_MODE != skip` when `CI_ENVIRONMENT = production`.
- Decide JWT: wire into the API route group with `auth:jwt`, or remove the configuration.
- Rotate the monitoring + API keys after they leave source control, and update the Zabbix probe.
