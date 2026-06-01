---
title: "WebScorpo Reviewer — Role Knowledge"
scope: private
role: reviewer
agent_name: webscorpo-reviewer
audience: webscorpo-reviewer
doc_id: agent-reviewer-role-knowledge
source: C:/tmp/webscorpo-analysis.md §6.1, §6.2, §9 (webscorpo-reviewer); rector.php; phpstan.neon; .php-cs-fixer.dist.php
---

# WebScorpo Reviewer — Role Knowledge

**Role**: Code Reviewer / Quality Gatekeeper.

**Why this role exists**: enforces `@quality` + `@ci` on every PR (CS-Fixer, PHPStan L2, Psalm,
Rector, phpcpd, composer-unused), checks convention adherence and baseline drift + the security
findings, and owns the merge-to-`main` gate.

## Toolchain configs to know

- `.php-cs-fixer.dist.php` — CI4 standard via `nexusphp/cs-config`; targets `app/` + `tests/`,
  excludes `build/` + `Models/Proxies`.
- `phpstan.neon` (+ baseline) — **L2**; covers Controllers/Models/Commands/Modules/Traits/
  Validation/tests; excludes Config/Helpers/Filters/Views/Database/Proxies.
- Psalm baseline — errorLevel 4; only NEW issues fail.
- `rector.php` — PHP 8.2 target; sets DEAD_CODE/CODE_QUALITY/EARLY_RETURN/TYPE_DECLARATION +
  PHPUnit code-quality + ~30 explicit rules; skips Views/Migrations/Proxies/vendor/writable/
  public-vendor and a few framework-extending Config/Test files
  (`TypedPropertyFromAssignsRector`).
- `infection.json.dist` — Infection mutation config.
- `tools/phpcpd-run.php` — duplication detection (excludes Migrations/Seeds/Proxies).

## Baseline-drift policy

Do not add suppressions to PHPStan/Psalm baselines to hide pre-existing issues. New code must be
clean; only previously-baselined issues are tolerated. A growing baseline is a review red flag.

## Merge-to-main gate checklist

1. `@ci` (`@quality` + `@test`) green.
2. Conventional Commits; one PR per unit of work.
3. No new baseline entries; no new hardcoded secrets (watch `Config/WebsCorpo.php`).
4. Migrations reversible; asset `versions.json` bumped if assets changed.
5. i18n: both locales present for new fields/strings.
6. Security findings not regressed (`AUTH_MODE=skip`, secret literals, exiftool path).
7. Only `main` deploys (dual-region) — confirm target branch.

See team `05-coding-standards-toolchain.md` + `09-security-baseline.md`.
