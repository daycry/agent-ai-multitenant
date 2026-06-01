---
title: "WebScorpo — Data Model Reference (BaseEntity / Doctrine / SLC)"
scope: team_shared
audience: all-agents
doc_id: team-04-data-model
source: C:/tmp/webscorpo-analysis.md §4; app/Models/Entity/BaseEntity.php
---

# WebScorpo — Data Model Reference

Persistence is **Doctrine ORM 3.x** via `daycry/doctrine ^5` with PHP **attribute** mapping
(`#[ORM\...]`), Ramsey UUID Doctrine, and Scienta Doctrine JSON Functions
(`JSON_EXTRACT`, `JSON_SET`, …). Database: MySQL 8+ / MariaDB 10.5+ via the **MySQLi** driver,
`utf8mb4`.

## BaseEntity pattern (confirmed in `app/Models/Entity/BaseEntity.php`)

All entities extend `App\Models\Entity\BaseEntity`, a Doctrine `#[ORM\MappedSuperclass]` with
`#[ORM\HasLifecycleCallbacks]`:

- `id` — `IDENTITY` auto-increment integer PK (`#[ORM\GeneratedValue(strategy: 'IDENTITY')]`).
- `uuid` — `varchar(50)`, unique constraint, auto-generated in `#[ORM\PrePersist]`
  (`Uuid::uuid4()->toString()`); exposed to JMS in the `blocks` group.
- `created_at` / `updated_at` / `deleted_at` — `datetime` columns, each with a named index;
  `deleted_at` enables **soft deletes**. `prePersist()` sets `created_at`; `preUpdate()` sets
  `updated_at`.
- JMS `#[ExclusionPolicy('all')]` + `#[Expose]` + `#[Groups([...])]` for selective API
  serialization (e.g. `timestamps`, `blocks` groups).
- `searchCriteriaBlocks(WebProjects $webProject, array|object|null $blocks)` — shared block
  resolution for custom blocks + projects blocks using Doctrine `Criteria`, with an ordering
  `usort` honoring the requested block order and a manual-filter fallback when the collection has
  no `matching()`.

~38 entity classes total (Admin/_ ≈ 8, WebProject/_ ≈ 30).

## Admin (global) tables

- `admin_modules_types` — module categories.
- `admin_modules` — reusable functional modules (name, icon, route, position, `visible`,
  `searchable`, `translations` JSON, FK to types; index on `visible`).
- `admin_webprojects_templates` — site templates (name, `translations` JSON); 1→N WebProjects.
- `admin_webprojects_statuses` — lifecycle states (draft/active/archived); 1→N WebProjects.
- `admin_webprojects` — **master WebProject record** (uuid, name, company_name, analytics_tag,
  api_key_maps, url, `app_services` JSON, logo, version; FK to template + status). Soft-deletable.
- `admin_webprojects_blocks` — reusable block definitions per WebProject (`content` JSON).
- `admin_webprojects_admin_modules` — join table (unique `(admin_module_id, admin_webproject_id)`).

## WebProject (per-site) tables — Config + Items pattern

Most modules follow **Config + Items**: a singleton `*_config` entity + N item entities (each with
`visible` flag, integer `position`, descriptions, content).

- `webprojects_configurations` (1:1) — translations, addresses, logos, social networks,
  project_categories, media_sections, CSP, cookies (all JSON). Central config aggregator.
- Team: `webprojects_team_config` + `webprojects_team_employees` (position ordering, `show_photos`).
- Multimedia: `webprojects_multimedia_sections` + `webprojects_multimedia` +
  `webprojects_multimedia_config`; **`MultimediaSectionAssignment`** pivot (Feb 2026 refactor)
  links items to multiple sections with `visible` + position.
- News: `webprojects_news_config` + `webprojects_news` (position, visible, `related_news` JSON,
  `presentation_format` JSON, social networks).
- Projects: `webprojects_projects_config` + `webprojects_projects` (`related_projects` JSON).
- Services: `webprojects_services_config` + `webprojects_services`.
- Singletons (1:1 per WebProject): `webprojects_pages`, `webprojects_documents`,
  `webprojects_menu`, `webprojects_about`, `webprojects_cookies`, `webprojects_footer`,
  `webprojects_contact` (form `configuration` JSON), `webprojects_home`,
  `webprojects_terms_of_use`.

## Auth tables (`daycry/auth`)

`users` (+ soft deletes), `auth_users_identities`, `auth_groups`, `auth_permissions`,
`auth_groups_users`, `auth_permissions_users`, `auth_logins`, `auth_remember_tokens`, `auth_logs`.
Default group `user`; admin group `admin_corpo`; content group `webproject:admin`. Login attempts
recorded on **FAILURE only** (production-safe); no IP blocking by default.

## Cross-cutting persistence patterns

- **Soft deletes everywhere** (`deleted_at`); FK `CASCADE DELETE` at DB level + Doctrine
  `cascade: ['persist','remove']`; logical deletes invalidate result caches.
- **UUID** alongside numeric PK for distributed/API use.
- **JSON columns** pervasive for multi-language content (`{"es": "...", "en": "..."}`), config
  (banner/SEO/blocks/social_networks/gallery/resources/international), and state.
- **Doctrine Second-Level Cache (SLC)** with named regions (`entity_read_heavy`, `entity_mixed`,
  `collection_*`), PSR-6, backed by Redis; query + metadata caches; `MenuCacheService`. Repositories
  invalidate specific keys on mutation.
- `admin_languages` — global language registry (name, code, `traductions` JSON, `visible`); linked
  to configs via a `languages` JSON array.
- `WebProjects::isInternational()` checks for presence of the `multimedia-manager` module.
