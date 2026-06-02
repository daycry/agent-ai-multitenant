---
title: "WebScorpo — Routing & Filter Conventions"
scope: team_shared
audience: all-agents
doc_id: team-03-routing-and-filters
source: C:/tmp/webscorpo-analysis.md §3.2; app/Config/WebsCorpo.php; app/Filters/WebProjectFilter.php
---

# WebScorpo — Routing & Filter Conventions

## Locale-prefixed routes

All routes are **locale-prefixed** via the `{locale}` placeholder → `/en/...`, `/es/...`. Root `/`
redirects to `/login`. `daycry/auth` routes are delegated to the package. Each module ships its own
`Config/Routes.php` (one per module).

## Filter chain

Filters (CI4 `Filters`):

- `auth:session` — default web gate (also `auth:jwt` / `auth:access_token` for APIs).
- `group:admin_corpo` — admin-layer gate.
- `webproject:admin` — per-WebProject content gate.
- `WebProjectFilter` (`app/Filters/WebProjectFilter.php`) — sets locale + WebProject context.

`WebProjectFilter::before()` (confirmed in source):

1. Requires URI segment 3 to be present (the project slug), else `redirect()->back()`.
2. Checks the project slug is one of `auth()->user()->getGroups()`.
3. If arguments are passed, validates the user holds a `{project}.{permission}` permission matching
   one of the filter arguments; otherwise `redirect()->back()`.

## Route-helper contract (config-driven)

`app/Config/WebsCorpo.php` provides two helpers that **auto-generate** the standard CRUD/block
routes. Agents MUST use these instead of hand-writing routes.

### `getRoutesDatatables(RouteCollection &$routes, string $module)`

Generates per-module DataTables POST routes:

```php
$routes->post('(:hash)/delete', $module . '::delete/$1/$2');
$routes->post('(:hash)/delete', $module . '::delete/$1');
$routes->post('(:hash)/visibility', $module . '::visibility/$1');
$routes->post('list/order', $module . '::updateOrder');
```

So every module gets `delete`, `visibility`, and `list/order` for free; the controller implements
`delete`, `visibility`, `updateOrder`.

### `getRoutesBlocks(RouteCollection &$routes, string $module)`

Generates the full `blocks/*` route group:

```php
$routes->group('blocks', static function ($routes) use ($module): void {
    $routes->get('list/(:hash)', $module . '::blocksList/$1/$2');
    $routes->get('list', $module . '::blocksList/$1');
    $routes->post('(:hash)/edit', $module . '::getBlock/$1/$2');
    $routes->post('validate', $module . '::validateBlock/$1');
    $routes->post('partial/(:segment)', $module . '::partialBlock/$1/$2');
    $routes->post('partial/(:segment)/element', $module . '::partialElementTableBlock/$1/$2');
    $routes->post('partial/(:segment)/edit/(:segment)/element', $module . '::getElementTableBlock/$1/$2/$3');
});
```

## Other config in `WebsCorpo.php`

- Path constants: `$pathAdminContent='/admin/content/'`, `$pathAdminConfig='/admin/config/'`,
  `$pathWebProject='/webproject/'`.
- `$imagesUrl='https://images.mediapro.tv/'`, `$international='international'`.
- `$menuModulesRelations` maps module keys (`news-manager`, `team`, `installations-manager`,
  `services-manager`, `projects-manager`, `about`, `contact`) → config getter methods.
- `getAvailableNetworks()` → `['Facebook','Twitter','GooglePlus','LinkedIn']`.

> Security note: `WebsCorpo.php` also holds hardcoded `$monitoringKey`,
> `$apiKeyForApiMonitoring`, and `$exifToolPath` — see `09-security-baseline.md`.
