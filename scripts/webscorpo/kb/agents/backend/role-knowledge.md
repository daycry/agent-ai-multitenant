---
title: "WebScorpo Backend — Role Knowledge"
scope: private
role: backend
agent_name: webscorpo-backend
audience: webscorpo-backend
doc_id: agent-backend-role-knowledge
source: C:/tmp/webscorpo-analysis.md §7, §9 (webscorpo-backend); app/Modules/WebProject/News
---

# WebScorpo Backend — Role Knowledge

**Role**: Backend Dev — CodeIgniter 4 specialist.

**Why this role exists**: day-to-day module CRUD — controllers (web / `Api` / `Config`),
`Routes`/`Registrar`/`Validation`, Twig views + partials, filters. Knows the `daycry/twig` +
DataTables + blocks macros cold.

## Module CRUD idioms

- A content module ships a web controller (`{Module}.php`), a REST controller (`Api.php`), and a
  config controller (`{Module}Config.php`), all under `Controllers/`.
- Register Twig template paths in `Config/Registrar.php`; declare routes in `Config/Routes.php`.
- Use the CI4 service container / `BaseController` to access Doctrine EM, Twig, Encryption,
  Language, helpers.

## DataTables trait contract

The `getRoutesDatatables()` helper (see team `03-routing-and-filters.md`) auto-generates
`delete`, `visibility`, and `list/order` POST routes; the controller implements:

- `delete($id[, $sub])` — soft delete (sets `deleted_at`).
- `visibility($id)` — toggle the `visible` flag.
- `updateOrder()` — persist new integer `position` ordering.

## Block CRUD flow + AJAX partials

`getRoutesBlocks()` generates the `blocks/*` group; controller implements `blocksList`, `getBlock`,
`validateBlock`, `partialBlock`, `partialElementTableBlock`, `getElementTableBlock`. Blocks render
via AJAX partials with repeater partials for dynamic field groups.

## Twig partial macros

- `input-forms/_field.twig` — central field macro; modes `standard` (Bootstrap row) / `bare`.
  Translated selects = one `<select>` with `data-{locale}` option attributes; translated
  non-selects = one control per language with locale visibility.
- `datatable.twig`, `blocks.twig`, `language-tabs.twig`, `seo.twig`, `form-section.twig`.

## Validation traits

Module validation lives in `Config/{Module}Validation.php`; common rules are shared (CommonRules).

## Upload controller contract

`/{locale}/project/{project}/{segment}/{segment}/upload` (max 3 segments) handles image/document
uploads.

See team `02-architecture-map.md` for the canonical module anatomy.
