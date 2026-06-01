---
title: "WebScorpo — Architecture Map (HMVC)"
scope: team_shared
audience: all-agents
doc_id: team-02-architecture-map
source: C:/tmp/webscorpo-analysis.md §3; composer.json PSR-4; app/Modules/WebProject/News
---

# WebScorpo — Architecture Map (HMVC)

WebScorpo is a **CodeIgniter 4** app following the App Starter Kit layout with **HMVC modules**
PSR-4-autoloaded under `app/Modules/`. The PSR-4 map lives in `composer.json` (`autoload.psr-4`).

## Module zones (28 modules)

- **Admin/** (`Admin\*` namespaces): `CSP`, `Cookies`, `CorporateConfiguration`, `Header`,
  `Language`, `Module`, `Translation`, `User`, `WebProject`.
- **WebProject/** (`WebProject\*` namespaces): `About`, `Configuration`, `Contact`, `Cookies`,
  `Documents`, `Footer`, `Home`, `Installations`, `Menu`, `Multimedia`, `News`, `Pages`,
  `Projects`, `Services`, `Team`, `TermsOfUse`.
- **Standalone**: `Dashboard`, `Docs` (dev-only doc viewer), `Login`, `Monitoring`.

> The composer PSR-4 map lists slightly fewer top-level namespaces than the 28-module count because
> some Admin modules share a namespace prefix; treat `composer.json` as authoritative for the
> autoload roots.

## Canonical module anatomy

Confirmed via `app/Modules/WebProject/News/`:

```
Modules/{Zone}/{Module}/
  Config/        Routes.php, Registrar.php (Twig paths), {Module}Validation.php
  Controllers/   {Module}.php (web CRUD), Api.php (REST), {Module}Config.php (config zone)
  Database/      Migrations/ (per-module), Seeds/
  Models/        Entity/*.php (Doctrine attribute entities), Repositories/*.php (custom queries)
  Traits/        e.g. NewsList.php (module-specific list logic)
  Views/         *.twig + partials/*.twig
```

## Controllers hierarchy

- `BaseController` injects shared services: Doctrine EM, Twig, Encryption, Language, helpers.
- `BaseApiController` — minimal REST base.
- `BaseContentModuleController` — content CRUD base (`setWebProject`, `setModule`,
  `getConfiguration`, `getSearchableModules`).
- ~29 module controllers; content modules typically expose a web controller, an `Api` controller,
  and a `Config` controller.

## Views / form & block system

- Twig 3 (`daycry/twig`) with shared partials in `app/Views/partials`: `input-forms/_field.twig`
  (central field macro), `banner.twig`, `blocks.twig`, `datatable.twig`, `form-section.twig`,
  `header.twig`, `navbar.twig`, `seo.twig`, `language-tabs.twig`.
- **Block system**: reusable content blocks across modules; generic block routes; AJAX partial
  rendering; repeater partials for dynamic field groups.
- **DataTables macro**: sortable/filterable lists with bulk actions via AJAX.

## Frontend assets

`public/assets/js/core/`: `jquery-form-validation.js`, `tinymce.js`, `language-tabs.js`,
`bulk-actions.js`. Third-party libs under `public/assets/third-party`. Asset versioning via
`public/assets/versions.json`.

## Services, libraries, helpers, CLI

- Services: `MenuData`, `MenuCacheService` (SLC-backed), `PerformanceManager` (QueryMonitor, `orX`
  builder delegation for DataTables).
- Libraries: `Menu.php` (nav builder per group), `Utils.php` (token gen, string normalization,
  slug validation, case conversion, JSON), `Multimedia.php`, `PerformanceManager.php`.
- Helpers: `translation_helper.php` (`lang()` lookup).
- CLI: `php spark performance {stats|clean|monitor|optimize}`, `CheckImages`, `DoctrineProxies`,
  `PurgeSessions`.

## Architectural rules agents MUST respect

1. Keep new code inside the correct module per its zone/anatomy.
2. Use the **Config + Items** pattern for new content modules.
3. Use the config-driven routing helpers (see `03-routing-and-filters.md`) instead of hand-writing
   CRUD routes.
4. Multi-language content goes in JSON columns (`{"es": "...", "en": "..."}`), never separate rows.
