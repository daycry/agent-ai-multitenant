---
title: "WebScorpo — Testing Strategy"
scope: team_shared
audience: all-agents
doc_id: team-06-testing-strategy
source: C:/tmp/webscorpo-analysis.md §6.3; phpunit.xml.dist; composer.json scripts
---

# WebScorpo — Testing Strategy

PHPUnit 10.5 with three suites, strict configuration, and Infection mutation testing.

## Suites (from `phpunit.xml.dist`)

- **Unit** — `tests/unit` (no DB, fastest).
- **Integration** — `tests/integration` (suffix `Test.php`; DB-touching).
- **E2E** — `tests/E2E/Login` + `tests/E2E/WebProject` (suffix `Test.php`; Selenium / Chrome with
  screenshot capture).

`@test` runs Unit + Integration in a single phpunit boot; `@test-E2E` runs the Selenium suite.

## Strict mode (zero-tolerance)

`phpunit.xml.dist` sets `beStrictAboutOutputDuringTests="true"`, `failOnRisky="true"`,
`failOnWarning="true"`, and stops on error/failure/warning/risky. Any noise (stray output, risky
test, deprecation surfaced as warning) fails the run.

## Test environment (`<php>` block)

- `app.baseURL = https://example.com/`, `CODEIGNITER_SCREAM_DEPRECATIONS = 0`.
- `encryption.key = hex2bin:075e...226b`.
- `security.csrfProtection = session` (forced).
- `database.tests.foreignKeys = true` (forced), `database.tests.DBPrefix = ""`.
- `auth.DBGroup = tests`, `cronjob.databaseGroup = tests`, `settings.database.group = tests`.
- `X-API-KEY-TESTS` env var carries the API key used by REST tests.
- `memory_limit = 512M` (HTML coverage needs more than PHP's default 128M).

## Selenium bootstrap & coverage

- `tests/bootstrap.php` registers the Selenium extension
  (`Daycry\PHPUnit\Selenium\SeleniumExtension`, browser `chrome`, screenshots →
  `build/selenium/screenshots`) and handles driver shutdown.
- Coverage reports: Cobertura XML (`build/logs/cobertura.xml`, feeds Azure Pipelines), HTML
  (`build/logs/html` / `build/coverage` via `@test-coverage`), text on stdout, testdox
  (`build/logs/testdox.{html,txt}`), JUnit (`build/logs/logfile.xml`). The `<php>` coverage format
  is intentionally omitted to avoid OOM.
- Source coverage includes `./app`, excludes module Migrations, `./app/Views`, and
  `./app/Config/Routes.php`.

## Coverage target

Current coverage ~**52.69%** (a Phase-2 initiative aims to raise it). Test data uses Faker; HTTP
recorded via the VCR extension (`daycry/phpunit-extension-vcr`); filesystem via vfsStream.

## Mutation

`@mutation` runs Infection (`infection.json.dist`) over Unit + Integration; reports under
`build/mutation/`. Expects a green suite first.
