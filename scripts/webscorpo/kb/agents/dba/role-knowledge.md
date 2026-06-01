---
title: "WebScorpo DBA — Role Knowledge"
scope: private
role: dba
agent_name: webscorpo-dba
audience: webscorpo-dba
doc_id: agent-dba-role-knowledge
source: C:/tmp/webscorpo-analysis.md §4, §9 (webscorpo-dba); app/Models/Entity/BaseEntity.php
---

# WebScorpo DBA — Role Knowledge

**Role**: Doctrine ORM / DBA.

**Why this role exists**: owns entities (attribute mapping, lifecycle callbacks, soft deletes,
UUID), repositories + custom queries (incl. Scienta JSON functions), migrations (per-module,
reversible), seeds, and Second-Level Cache regions + invalidation. The Doctrine layer is deep and
central — distinct from backend.

## Doctrine attribute mapping deep-dive

- Entities use `#[ORM\...]` PHP attributes (not XML/YAML). `BaseEntity` is an
  `#[ORM\MappedSuperclass]` with `#[ORM\HasLifecycleCallbacks]`,
  `#[ORM\GeneratedValue(strategy: 'IDENTITY')]` PK, unique `uuid`, indexed timestamps.
- JMS annotations (`#[ExclusionPolicy('all')]`, `#[Expose]`, `#[Groups]`) co-exist on entity
  properties for selective REST output (`timestamps`, `blocks` groups).

## Lifecycle callbacks + soft-delete pattern

- `#[ORM\PrePersist] prePersist()` sets `uuid` (if empty) + `created_at`.
- `#[ORM\PreUpdate] preUpdate()` sets `updated_at`.
- Soft delete = set `deleted_at` (never hard-delete content). FK `CASCADE DELETE` at DB level +
  Doctrine `cascade: ['persist','remove']`. Logical deletes must invalidate the relevant SLC keys.

## Repository custom-query cookbook

- Custom queries live in `Models/Repositories/*.php` per module.
- Use **Scienta Doctrine JSON Functions** (`JSON_EXTRACT`, `JSON_SET`, …) to query the pervasive
  JSON columns (translations `{es,en}`, config, social_networks, blocks).
- `searchCriteriaBlocks()` on `BaseEntity` shows the Doctrine `Criteria` + `usort` ordering idiom
  for resolving custom/projects blocks.

## Migrations + seeds

- Per-module migrations under `Modules/{Zone}/{Module}/Database/Migrations/`; must be **reversible**.
- Seeds under the same module `Database/Seeds/`.
- phpcpd + coverage exclude Migrations/Seeds/Proxies.

## Second-Level Cache (SLC)

- Named regions: `entity_read_heavy`, `entity_mixed`, `collection_*`; PSR-6 over Redis; query +
  metadata caches. `MenuCacheService` is SLC-backed.
- Repositories invalidate specific keys on persist/update/remove.

## Proxy generation

`php spark DoctrineProxies` regenerates proxies (`Models/Proxies/`). Dev/test regenerate on the fly;
production should ship pre-generated proxies. Proxies are excluded from cs-fixer/phpstan/coverage.

See team `04-data-model.md` for the full table inventory.
