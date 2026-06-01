---
title: "WebScorpo Auth/Security — Role Knowledge"
scope: private
role: auth-security
agent_name: webscorpo-auth-security
audience: webscorpo-auth-security
doc_id: agent-auth-security-role-knowledge
source: C:/tmp/webscorpo-analysis.md §4.4, §5, §6.6, §9 (webscorpo-auth-security); app/Config/WebsCorpo.php; app/Filters/WebProjectFilter.php
---

# WebScorpo Auth/Security — Role Knowledge

**Role**: Auth / Security specialist (daycry/auth + Azure SSO).

**Why this role exists**: owns authenticators, groups/permissions, the Azure AD SSO bridge,
password validators, API rate limiting, CSP/Cookies modules, and remediation of the
hardcoded-secret / `AUTH_MODE=skip` findings.

## daycry/auth configuration (`app/Config/Auth.php`)

- Authenticators: session (default web) + JWT + access-token + guest.
- Validators: Composition, Dictionary, NothingPersonal.
- Tables: `users`, `auth_users_identities`, `auth_groups`, `auth_permissions`,
  `auth_groups_users`, `auth_permissions_users`, `auth_logins`, `auth_remember_tokens`,
  `auth_logs`.
- Login attempts recorded on **FAILURE only**; no IP blocking by default.

## Groups / permissions model

- Groups: `user` (default), `admin_corpo` (admin gate), and one group per WebProject slug.
- Permissions stored as `{project}.{permission}`. `app/Filters/WebProjectFilter.php` verifies the
  URL project segment is in `user->getGroups()` and the user holds a matching
  `{project}.{permission}`.

## Azure SSO flow

`LoginController::sso()` bridges `Mediapro\GDI\Library\Azure` (from `mediapro/gdi-library`) into a
daycry session. Config via `gdi.*` env keys. **`AUTH_MODE=skip`** is a dev bypass that fabricates a
logged-in session — must be impossible in non-dev.

## API auth + rate limiting

Access Token via `X-API-KEY` header + JWT Bearer; per-method rate limiting (e.g. 1 req/min,
configurable). WebProject context enforced on API routes. In practice routes use session +
access_token; JWT is configured but unused (decide: wire or remove).

## CSP / Cookies / CSRF

Dedicated Admin modules (`Admin\CSP`, `Admin\Cookies`) manage CSP + cookie policy; CSRF is
session-based (`security.csrfProtection = session`).

## Open findings + remediation (own these)

1. Hardcoded `$monitoringKey` / `$apiKeyForApiMonitoring` in `app/Config/WebsCorpo.php` → move to env.
2. `$exifToolPath` Windows dev path in config → env-driven.
3. `AUTH_MODE=skip` → assert disabled when `CI_ENVIRONMENT=production`.
4. Unused JWT authenticator → wire into the API group or remove.

See team `09-security-baseline.md` for the full baseline.
