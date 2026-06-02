---
title: "WebScorpo — Coding Standards & Toolchain"
scope: team_shared
audience: all-agents
doc_id: team-05-coding-standards-toolchain
source: C:/tmp/webscorpo-analysis.md §6.1, §6.2; composer.json scripts; rector.php
---

# WebScorpo — Coding Standards & Toolchain

Triple-layer quality gates + mutation testing. The contract agents must obey is encoded in the
**Composer scripts** of `composer.json`.

## Quality tools

- **Style**: PHP-CS-Fixer (`friendsofphp/php-cs-fixer ^3`), CI4 standard via `nexusphp/cs-config`
  - `codeigniter/coding-standard`; config `.php-cs-fixer.dist.php`. Targets `app/` + `tests/`,
    excludes `build/` and `Models/Proxies`.
- **Static analysis**:
  - PHPStan (`phpstan/phpstan ^2.0`) **L2**, config `phpstan.neon` (+ baseline). Covers
    Controllers/Models/Commands/Modules/Traits/Validation/tests; excludes
    Config/Helpers/Filters/Views/Database/Proxies.
  - Psalm (`vimeo/psalm`) errorLevel 4 with baseline — only NEW issues fail.
- **Modernization**: Rector 2 (`rector/rector ^2.0`), `rector.php`, target **PHP 8.2**
  (`PhpVersion::PHP_82`), sets DEAD_CODE / CODE_QUALITY / EARLY_RETURN / TYPE_DECLARATION +
  PHPUnit code-quality, plus ~30 explicit rules. Skips `app/Views`, `app/Database/Migrations`,
  `app/Models/Proxies`, `vendor`, `writable`, `public/vendor`.
- **Duplication**: phpcpd (`systemsdk/phpcpd ^8.0`) via `tools/phpcpd-run.php` (excludes
  Migrations/Seeds/Proxies).
- **Unused deps**: `icanhazstring/composer-unused`.
- **Mutation**: Infection (`infection/infection ^0.30.2`), `infection.json.dist`, over
  Controllers/Commands/Libraries/Traits/Helpers → `build/mutation/`.

## Composer scripts — the contract

| Script                             | Definition                                                                               | When                              |
| ---------------------------------- | ---------------------------------------------------------------------------------------- | --------------------------------- |
| `@ci`                              | `@quality` + `@test`                                                                     | What Azure runs on every push.    |
| `@quality`                         | `@cs-check` + `@static-analysis` (`@phpstan`+`@psalm`) + `@deduplicate` + `@unused-deps` | Fast pre-commit gates (no tests). |
| `@fix`                             | `@cs-fixer` + `@rector-fix`                                                              | Auto-fix everything fixable.      |
| `@test`                            | `phpunit --testsuite Unit,Integration` (single boot)                                     | Default test run.                 |
| `@test-E2E`                        | `phpunit --testsuite E2E`                                                                | Selenium suite.                   |
| `@test-all`                        | `@test` + `@test-E2E`                                                                    | Every suite.                      |
| `@test-coverage`                   | `@test` + HTML coverage → `build/coverage/`                                              | Coverage report.                  |
| `@test-unit` / `@test-integration` | single suite                                                                             | Fast / DB-touching.               |
| `@mutation`                        | `infection --testsuite=Unit,Integration`                                                 | Mutation gate (slow).             |
| `@quality-full`                    | `@ci` + `@test-E2E` + `@rector` + `@mutation`                                            | Release-blocking, slowest.        |

Helper scripts: `cs-check`, `cs-fixer`, `phpstan`, `psalm`, `static-analysis`, `deduplicate`,
`unused-deps`, `rector` (dry-run), `rector-fix`.

## Conventions

- PHP `^8.2`; `declare(strict_types=1)` where present; attribute-based Doctrine + JMS mapping.
- Run `@fix` then `@quality` before committing; `@ci` is the merge gate.
- Do not edit baselines to silence pre-existing issues; only NEW Psalm/PHPStan issues should fail.
