---
title: "WebScorpo — i18n Policy (EN/ES)"
scope: team_shared
audience: all-agents
doc_id: team-08-i18n-policy
source: C:/tmp/webscorpo-analysis.md §2, §6.4; composer.json
---

# WebScorpo — i18n Policy (EN/ES)

WebScorpo is bilingual: **English (en)** and **Spanish (es)** only. Every user-facing string and
every content field must exist in both locales.

## Configuration

- CI4 i18n: `defaultLocale = en`, `negotiateLocale = false`, `supportedLocales = ['en','es']`.
- Routes are locale-prefixed (`/en/...`, `/es/...`) — see `03-routing-and-filters.md`.
- Packages: CI4 native language files + `daycry/codeigniter-language ^1.0` + `codeigniter4/translations`.
- Custom `Admin\Language` module manages the global language registry and translations
  (`/admin/config/languages`, `/api/v1/translations`).

## Language file locations

Per-locale CI4 language files live under the framework convention, including `Locales.php`,
`Validation.php`, and `Response.php` per locale. Translation lookups go through
`translation_helper.php` (`lang()`).

## Translatable content (JSON columns)

Multi-language _content_ (not UI strings) is stored in JSON columns shaped `{"es": "...", "en": "..."}`.
The `_field.twig` macro renders translated fields:

- Translated **selects** → a single `<select>` with `data-{locale}` option attributes.
- Translated **non-selects** → one control per language with locale-specific visibility (driven by
  the `language-tabs.js` behavior).

The `admin_languages` registry (name, code, `traductions` JSON, `visible`) is linked to configs via
a `languages` JSON array on the config entities.

## Rules for agents

1. Never add a content field without both `es` and `en` entries.
2. Never hardcode user-facing strings — use `lang()` / language files.
3. When adding a UI string, add it to BOTH locale files in the same change.
4. Keep the `languages` array on a config in sync when enabling/disabling a locale for a WebProject.
