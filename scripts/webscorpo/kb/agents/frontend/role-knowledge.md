---
title: "WebScorpo Frontend — Role Knowledge"
scope: private
role: frontend
agent_name: webscorpo-frontend
audience: webscorpo-frontend
doc_id: agent-frontend-role-knowledge
source: C:/tmp/webscorpo-analysis.md §3.4, §3.5, §9 (webscorpo-frontend)
---

# WebScorpo Frontend — Role Knowledge

**Role**: Frontend Dev.

**Why this role exists**: Bootstrap 5.2.3 / jQuery / ES6 assets, TinyMCE 7.3 integration, asset
versioning, the `_field.twig` form macro behavior, Select2 / DataTables UI.

## Asset pipeline

- Core JS in `public/assets/js/core/`: `jquery-form-validation.js`, `tinymce.js`,
  `language-tabs.js`, `bulk-actions.js`.
- Third-party libs under `public/assets/third-party`.
- **Asset versioning** via `public/assets/versions.json` — bump when assets change (CI minify runs
  `michalsn/minifier`).

## Key behaviors

- `jquery-form-validation.js` — AJAX form handling with **double-submit prevention**.
- `tinymce.js` — TinyMCE **7.3** lifecycle init/teardown, including a **z-index fix** so the editor
  renders above Bootstrap modals.
- `language-tabs.js` — switches visible locale controls (drives translated non-select rendering).
- `bulk-actions.js` — checkbox select + bulk toggle-visibility / soft-delete on DataTables lists.

## `_field.twig` rendering rules (`app/Views/partials/input-forms/_field.twig`)

- Modes: `standard` (Bootstrap row) and `bare`.
- Translated **selects**: a single `<select>` with `data-{locale}` option attributes.
- Translated **non-selects**: one control per language, locale-specific visibility (works with
  `language-tabs.js`).

## UI conventions

Bootstrap **5.2.3** (pinned), jQuery 3.x, Select2, server-side DataTables
(`hermawan/codeigniter4-datatables`). SEO fields + language tabs render via the shared partials
(`seo.twig`, `language-tabs.twig`).

See team `02-architecture-map.md` (views/partials) and `08-i18n-policy.md` (translated fields).
