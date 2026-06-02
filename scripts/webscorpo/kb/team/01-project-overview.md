---
title: "WebScorpo — Project Overview & Domain Glossary"
scope: team_shared
audience: all-agents
doc_id: team-01-project-overview
source: C:/tmp/webscorpo-analysis.md §1, §5
---

# WebScorpo — Project Overview & Domain Glossary

**WebScorpo** (`mediapro/webscorpo`, homepage `https://webscorporativas.mediapro.tv`) is a
multi-tenant corporate-website CMS built for **Mediapro**. It lets editors build and manage many
independent corporate websites ("WebProjects") from a single platform instance. Each WebProject is
a self-contained site with its own configuration, content modules, multi-language content (ES/EN),
publish/version lifecycle, and a REST API (`/api/v1/*`) that serves published content to the public
front-ends.

> This is a **CodeIgniter 4 / PHP** application — a _target project_ worked on by this agent team,
> not part of the Python agentic-platform itself.

## Two operational zones

- **Admin layer** (`/admin/config/*`, `/admin/content/*`) — global platform configuration: users,
  languages, translations, module catalog, WebProject CRUD, corporate config, CSP, cookies, header.
  Gated by the `admin_corpo` group.
- **WebProject layer** (`/webproject/{project}/content/*`) — per-site content management for a
  selected WebProject, gated by the `webproject:admin` permission. Each module is independently
  CRUD-able within its WebProject context.

Multi-tenancy is at the **WebProject level** (not OS/DB tenant isolation): every content entity is
scoped to a WebProject, and `app/Filters/WebProjectFilter.php` injects locale + WebProject context
into the request.

## Feature inventory (high level)

- **Auth / SSO**: session (default) + JWT + access-token + guest authenticators; Azure AD OAuth2
  SSO via `LoginController::sso()`; email 2FA actions; magic-link; remember-me cookies.
- **Admin · Users** (`/admin/config/users`), **Languages** (`/admin/config/languages`),
  **Translations** (`/api/v1/translations`), **Modules** (`/admin/config/modules`),
  **WebProjects** (`/admin/config/webprojects` — multi-tenant CRUD with `publish/$id` and
  `version/$id` lifecycle), **Corporate Configuration / Cookies / CSP / Header** (`/admin/content/*`).
- **Dashboard** (`/dashboard`) — authenticated landing/project overview.
- **WebProject content modules** — each at `/webproject/{project}/content/{module}` (web CRUD,
  blocks, SEO, language tabs) plus a public REST API at `/api/v1/{resource}`: Home, Pages, News,
  Services, Projects, Team, Installations, Multimedia, Documents, Menu, About, Contact,
  Configuration, Cookies, Footer, Terms of Use.
- **Upload controller** — `/{locale}/project/{project}/{segment}/{segment}/upload` (max 3 segments).
- **REST API v1** — Access Token (`X-API-KEY`) + JWT Bearer; per-method rate limiting; JMS
  serialization; WebProject context enforced.
- **Monitoring** — `/api/v1/monitoring/status` health check for Zabbix.
- **Blocks system** — generic reusable content blocks with AJAX partial rendering.
- **Content versioning** — WebProjects publish/version lifecycle enabling rollback.

## Domain glossary

| Term                     | Meaning                                                                                                                            |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| **WebProject**           | A self-contained corporate site (tenant unit). Master record `admin_webprojects`.                                                  |
| **Module**               | A reusable functional content area (News, Team, Services…). Registered in `admin_modules`.                                         |
| **Config + Items**       | Common module shape: a singleton `*_config` entity + N item entities.                                                              |
| **Block**                | Reusable content block, AJAX-rendered, shared across modules (`admin_webprojects_blocks`).                                         |
| **Section / Assignment** | Multimedia grouping; `MultimediaSectionAssignment` pivot links items to many sections (refactored Feb 2026).                       |
| **publish / version**    | WebProject lifecycle: publishing produces a versioned snapshot, enabling rollback.                                                 |
| **`admin_corpo`**        | Auth group gating the global admin layer.                                                                                          |
| **`webproject:admin`**   | Permission gating per-WebProject content management.                                                                               |
| **"international"**      | `Config\WebsCorpo::$international = 'international'`; `WebProjects::isInternational()` checks for the `multimedia-manager` module. |

## Stakeholders

Owner: **Mediapro** (Ingenieria team). The CMS powers public corporate sites under
`webscorporativas.mediapro.tv`.
