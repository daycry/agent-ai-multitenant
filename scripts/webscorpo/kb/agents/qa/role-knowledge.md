---
title: "WebScorpo QA — Role Knowledge"
scope: private
role: qa
agent_name: webscorpo-qa
audience: webscorpo-qa
doc_id: agent-qa-role-knowledge
source: C:/tmp/webscorpo-analysis.md §6.3, §9 (webscorpo-qa); phpunit.xml.dist
---

# WebScorpo QA — Role Knowledge

**Role**: QA / Test Engineer.

**Why this role exists**: PHPUnit Unit/Integration + Selenium E2E (Login + WebProject journeys),
raising coverage past 52.69%, test DB + API-key fixtures, Infection mutation gates, strict-mode
discipline.

## `phpunit.xml.dist` essentials

- Suites: **Unit** (`tests/unit`), **Integration** (`tests/integration`, `*Test.php`), **E2E**
  (`tests/E2E/Login`, `tests/E2E/WebProject`, `*Test.php`).
- Strict mode: `beStrictAboutOutputDuringTests`, `failOnRisky`, `failOnWarning`,
  `stopOnError/Failure/Warning/Risky` — zero tolerance. A risky test or stray output FAILS the run.
- Test env: `encryption.key` (hex2bin), `security.csrfProtection=session`,
  `database.tests.foreignKeys=true`, `auth.DBGroup=tests`, `settings.database.group=tests`,
  `X-API-KEY-TESTS` for REST tests, `memory_limit=512M`.

## Bootstrap + Selenium

`tests/bootstrap.php` registers `Daycry\PHPUnit\Selenium\SeleniumExtension` (browser `chrome`,
screenshots → `build/selenium/screenshots`) and handles driver shutdown. E2E journeys: Login +
WebProject content flows.

## Fixtures / tooling

- **Faker** for data, **vfsStream** for filesystem, **VCR** (`daycry/phpunit-extension-vcr`) for
  recorded HTTP.

## Coverage commands + locations

- `@test-coverage` → HTML at `build/coverage/` (also `build/logs/html`).
- Cobertura XML → `build/logs/cobertura.xml` (Azure coverage tab).
- testdox → `build/logs/testdox.{html,txt}`; JUnit → `build/logs/logfile.xml`.
- Current coverage ~**52.69%** — Phase-2 goal to raise it; coordinate priority modules with PM.

## Mutation

`@mutation` runs Infection (`infection.json.dist`) over Unit+Integration → `build/mutation/`.
Requires a green suite first.

See team `06-testing-strategy.md`.
