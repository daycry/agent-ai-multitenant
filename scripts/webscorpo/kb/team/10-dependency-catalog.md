---
title: "WebScorpo — Dependency Catalog"
scope: team_shared
audience: all-agents
doc_id: team-10-dependency-catalog
source: C:/tmp/webscorpo-analysis.md §2, §10; composer.json
---

# WebScorpo — Dependency Catalog

From `composer.json` (`require` / `require-dev` / `repositories`).

## Runtime dependencies (`require`)

| Package                            | Constraint | Role                                                          |
| ---------------------------------- | ---------- | ------------------------------------------------------------- |
| `php`                              | `^8.2`     | Language (runtime image PHP 8.4-FPM Alpine).                  |
| `codeigniter4/framework`           | `^4.0`     | HMVC web framework.                                           |
| `codeigniter4/translations`        | `^4.5`     | Framework translations.                                       |
| `daycry/auth`                      | `^2.0`     | Session/JWT/access-token/guest auth, groups/permissions, 2FA. |
| `daycry/codeigniter-language`      | `^1.0`     | i18n helper layer.                                            |
| `daycry/doctrine`                  | `^5`       | Doctrine ORM 3.x integration (attribute mapping, SLC).        |
| `daycry/twig`                      | `^3`       | Twig 3 templating with custom extensions.                     |
| `guzzlehttp/guzzle`                | `^7.9`     | HTTP client.                                                  |
| `hermawan/codeigniter4-datatables` | `^0.8`     | Server-side DataTables.                                       |
| `mediapro/gdi-library`             | `^3`       | Azure AD / OAuth2 SSO (`Mediapro\GDI\Library\Azure`).         |
| `michalsn/minifier`                | `^2.0`     | Asset minification.                                           |
| `ramsey/uuid-doctrine`             | `^2.1`     | UUID Doctrine type.                                           |
| `scienta/doctrine-json-functions`  | `^6.1`     | `JSON_EXTRACT`/`JSON_SET` DQL functions.                      |
| `tinymce/tinymce`                  | `^7.3`     | Rich text editor.                                             |
| `twbs/bootstrap`                   | `5.2.3`    | CSS framework (pinned).                                       |

## Dev / quality dependencies (`require-dev`)

`codeigniter/coding-standard ^1.8`, `codeigniter/phpstan-codeigniter ^1.4`,
`daycry/phpunit-extension-selenium ^1`, `daycry/phpunit-extension-vcr ^1`,
`ergebnis/composer-normalize`, `fakerphp/faker ^1.9`, `friendsofphp/php-cs-fixer ^3`,
`icanhazstring/composer-unused`, `infection/infection ^0.30.2`, `league/commonmark ^2.8`,
`mikey179/vfsstream ^1.6`, `nexusphp/cs-config ^3.6`, `nexusphp/tachycardia`,
`php-webdriver/webdriver ^1.15`, `phpstan/phpstan ^2.0` (+ deprecation/phpunit/strict rules),
`phpunit/phpcov ^10`, `rector/rector ^2.0`, `systemsdk/phpcpd ^8.0`, `vimeo/psalm ^5||^6`.

Suggested: `roave/security-advisories` (block insecure versions).

## Custom VCS repositories (`repositories`)

Two Azure DevOps SSH VCS repos provide the SSO stack:

```
git@ssh.dev.azure.com:v3/ImaginaDEVOPS/Mediapro%20-%20Common%20Desarrollo/VENDOR%20-%20AzureOauthClient
git@ssh.dev.azure.com:v3/ImaginaDEVOPS/MEDIAPRO%20-%20Equipo%20Desarrollo/GdiLibraryHelper
```

`minimum-stability: dev`, `prefer-stable: true`. Installing requires Azure DevOps SSH access — see
`07-cicd-deploy-runbook.md` / the devops agent KB for auth setup.

## Other notable libraries (transitive / used)

JMS Serializer (`@Groups` REST output), Doctrine Second-Level Cache (PSR-6), Select2, jQuery 3.x.
