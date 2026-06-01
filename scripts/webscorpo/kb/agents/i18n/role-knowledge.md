---
title: "WebScorpo i18n — Role Knowledge"
scope: private
role: i18n
agent_name: webscorpo-i18n
audience: webscorpo-i18n
doc_id: agent-i18n-role-knowledge
source: C:/tmp/webscorpo-analysis.md §6.4, §9 (webscorpo-i18n)
---

# WebScorpo i18n — Role Knowledge

**Role**: i18n / Localization.

**Why this role exists**: EN/ES language files + `daycry/codeigniter-language` + the per-project
`Admin\Language` module + JSON translation columns + language-tabs UI; ensures every new field is
translatable and both locales are covered.

## CI4 language-file structure

Per-locale files following the framework convention: `Locales.php`, `Validation.php`,
`Response.php` (one set per `en` / `es`). Lookups go through `translation_helper.php` (`lang()`).
Config: `defaultLocale = en`, `negotiateLocale = false`, `supportedLocales = ['en','es']`.

## daycry/codeigniter-language API

`daycry/codeigniter-language ^1.0` layers on CI4 i18n. `codeigniter4/translations` supplies
framework strings.

## `Admin\Language` module

- Manages the global language registry (`admin_languages`: name, code, `traductions` JSON,
  `visible`) and translations.
- Components: `LanguagesRepository`, `LanguageValidation`, `language-tabs.twig`.
- Endpoints: `/admin/config/languages` (CRUD), `/api/v1/translations` (REST, JMS-serialized).

## JSON translation columns

Content translations are stored as `{"es": "...", "en": "..."}` JSON. Configs reference enabled
locales via a `languages` JSON array. The `_field.twig` macro renders translated selects (single
`<select>` + `data-{locale}` options) and translated non-selects (one control per locale,
visibility driven by `language-tabs.js`).

## Rules

1. Every new content field gets `es` + `en`.
2. New UI strings go into BOTH locale files in the same change.
3. Keep a config's `languages` array consistent with the WebProject's enabled locales.

See team `08-i18n-policy.md`.
