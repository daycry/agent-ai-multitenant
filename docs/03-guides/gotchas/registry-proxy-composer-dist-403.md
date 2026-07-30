---
title: "composer/go install: dist 403 'CONNECT tunnel failed' aunque packagist resuelva"
area: docker
encountered: 2026-06-30
stack: docker · registry-proxy · tinyproxy · composer · ADR 0094
---

## Síntoma

Con el `registry-proxy` (ADR 0094) ya enrutando los runtime-templates, `composer install`
resuelve la **metadata** de packagist pero falla al **descargar los paquetes**:

```
Failed to download guzzlehttp/guzzle from dist: curl error 56 while downloading
  https://api.github.com/repos/guzzle/guzzle/zipball/<ref>: CONNECT tunnel failed, response 403
```

(rc 100). Lo mismo le pasa a `go mod download` con módulos alojados en GitHub.

## Causa raíz

La allowlist del `registry-proxy` (`docker/registry-proxy/filter.txt`, `FilterDefaultDeny Yes`)
tenía `packagist.org`/`repo.packagist.org` pero **no `api.github.com`**. composer (y go) no bajan
el zipball de dist de `codeload.github.com` directamente: piden
`https://api.github.com/repos/{owner}/{repo}/zipball/{ref}` (que redirige a codeload). Sin
`api.github.com` en el filtro, tinyproxy rechaza el `CONNECT api.github.com:443` con **403
Filtered** → `curl error 56`.

Es fácil de pasar por alto porque la metadata de packagist SÍ pasa (da sensación de que el
egress funciona), pero el dist es otro host.

## Fix

Añadir el host al filtro y reconstruir el proxy:

```
# docker/registry-proxy/filter.txt
^api\.github\.com$
```

```bash
docker compose -p agentic-platform -f docker-compose.yml -f docker-compose.dev.yml \
  -f docker-compose.manuals.yml up -d --build --force-recreate --no-deps registry-proxy
```

## Cómo verificarlo

```bash
# El puente entero (worker -> runtime -> proxy -> registries) sobre un worktree con
# composer.json que requiere guzzle:
docker exec agentic-platform-workers-1 python -c "
from workers.config import Settings
from workers.test_runtime import TestRuntimeRunner, TestRuntimeSpec, RuntimePlan
from shared_test_runtimes.catalog import get
spec = TestRuntimeSpec(plan=RuntimePlan(template=get('php-phpunit'), checks=()),
                       worktree_host_path='/data/agent-platform/<worktree>', dep_egress=True)
print(TestRuntimeRunner(Settings()).run_command(spec, 'composer install --no-interaction', timeout_s=300)[0])
"   # -> 0, y vendor/ poblado
```

## Relacionado

- `ADR 0094` — egress de runtime-templates a registries vía `registry-proxy`.
- El allowlist es deny-by-default: cada nuevo host de dist que un stack necesite hay que
  añadirlo explícitamente al filtro (mismo patrón que el `egress-proxy` LLM, ADR 0019).
