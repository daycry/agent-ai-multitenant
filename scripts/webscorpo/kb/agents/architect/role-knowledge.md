---
title: "WebScorpo Architect — Role Knowledge"
scope: private
role: architect
agent_name: webscorpo-architect
audience: webscorpo-architect
doc_id: agent-architect-role-knowledge
source: C:/tmp/webscorpo-analysis.md §7, §9 (webscorpo-architect); app/Models/Entity/BaseEntity.php
---

# WebScorpo Architect — Role Knowledge

**Role**: Software Architect (CodeIgniter 4 + Doctrine).

**Why this role exists**: guards the HMVC module anatomy, the Config + Items + BaseEntity + SLC
patterns, the config-driven routing helpers (`getRoutesDatatables` / `getRoutesBlocks`), and the
JSON-column / translation conventions. Approves any deviation.

## Decision log topics (ADR-style)

- **CI4 + Doctrine** choice: why Doctrine ORM 3.x via `daycry/doctrine` over CI4's native models —
  attribute mapping, lifecycle callbacks, Second-Level Cache.
- **`#[ORM\MappedSuperclass]` BaseEntity** rationale: shared `id`/`uuid`/timestamps/soft-delete +
  `searchCriteriaBlocks()` so every entity inherits the UUID + audit + block-resolution behavior
  (`app/Models/Entity/BaseEntity.php`).
- **SLC** rationale: named cache regions (`entity_read_heavy`, `entity_mixed`, `collection_*`)
  backed by Redis; repositories invalidate on mutation. Trade-off: read-heavy menu/config vs
  invalidation complexity.
- **Config-driven routing** rationale: `getRoutesDatatables` / `getRoutesBlocks` in
  `Config/WebsCorpo.php` generate CRUD/block routes so modules stay uniform; hand-written CRUD
  routes are a deviation to reject.
- **JSON-column trade-offs**: multi-language content as `{"es","en"}` JSON vs normalized tables —
  chosen for flexibility; Scienta JSON DQL functions enable querying.
- **Module-boundary rules**: each module is a self-contained HMVC unit (Config / Controllers /
  Database / Models / Traits / Views); cross-module coupling goes through services/libraries.

## How to extend `BaseContentModuleController`

`BaseContentModuleController` provides `setWebProject`, `setModule`, `getConfiguration`,
`getSearchableModules`. New content modules subclass it, register their Twig paths in
`Config/Registrar.php`, declare routes via the `WebsCorpo` helpers, and follow Config + Items.

See team docs `02-architecture-map.md`, `03-routing-and-filters.md`, `04-data-model.md`.
