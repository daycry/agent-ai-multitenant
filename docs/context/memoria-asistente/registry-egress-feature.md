---
name: registry-egress-feature
description: registry-proxy (ADR 0094) — egress allowlisted de los runtime-templates a registries de paquetes (composer/pip/npm/go/nuget/...) + git hosts; implementado + desplegado + verificado e2e.
metadata:
  node_type: memory
  type: project
  originSessionId: 9b6ffa32-bda3-49a0-a5ed-708c0fca5208
---

**registry-proxy / egress de runtime-templates** (ADR 0094, rama `plan/runs-visor-trabajo`,
2026-06-30) — antes los runtime-templates corrían en bridge `internal=True` SIN egress alguno, así
que `composer install`/`pip install`/`npm ci`/`go mod download`/... no resolvían sus registries (solo
con dep_cache caliente). Ahora: segunda instancia de tinyproxy **`registry-proxy`** (disjunta del
`egress-proxy` LLM, solo en `agentic-net`, allowlist de registries PÚBLICOS + git hosts) que el
**worker conecta transitoriamente al bridge per-task** e inyecta `HTTP(S)_PROXY` cuando el launch pide
egress. Resuelve `task_prod12_net_01` (opción a). Complementa [[stack-exec-feature]].

**Invariante (Principio 2):** el bridge per-task se queda SIEMPRE `internal=True` (D1, se elimina el
NAT crudo de `open`); el egress solo existe vía el proxy allowlisted (deny-by-default). Knob por-launch
`dep_egress` en `TestRuntimeSpec`: `_run_stack_command`→True (el comando ES el install) y
`_launch_test_runtime_plans`→True (el pre_install en frío; el runner DESCONECTA el proxy antes de la
fase de test, D2 fail-closed). `_cleanup` desconecta (NUNCA elimina) el proxy compartido.

Ficheros: `docker/registry-proxy/{Dockerfile,tinyproxy.conf,filter.txt}` + servicio en
`docker-compose.yml` y `compose_generator.py` (CORE_SERVICES); `test_runtime.py`
(`_create_bridge` siempre internal, `_egress_enabled`/`_attach_registry_proxy`/`_detach_proxy`,
`_run_checks`→`_run_pre_install`+`_run_test_checks`, env proxy+cache+git-https); `config.py`
(`registry_proxy_url/container/alias`); `shared-test-runtimes/{types.py,catalog.py}` (`NetworkPolicy`
`registries`, `cache_env` por plantilla alineado al `dep_cache_mount`); `tasks.py` (dep_egress + audit).
7 commits be44bbc…ee82ede.

**Deploy (no olvidar):** `registry-proxy` build via compose; `api-server:ci` rebuild **WITH_CLAUDE=1**
(base del worker, lleva shared-test-runtimes) → `workers:ci` (FROM api-server:ci) → recrear
`workers`+`cortex-beat`+`registry-proxy`. El docker-socket-proxy ya tiene `NETWORKS=1`/`POST=1`/`EXEC=1`
(el worker necesita connect/disconnect/get). La api-server EN EJECUCIÓN usa `:manuals` y NO se recrea
(feature worker-side).

**Verificado e2e** (worker real, worktree con guzzle): `composer install` dep*egress=True → rc0 +
vendor/guzzlehttp + composer.lock escritos; dep_egress=False → rc100 "Could not resolve host" (sin NAT
crudo); proxy deny `evil.example.com` (403 Filtered), allow packagist/pypi. **Gotcha**: composer baja
dist de `api.github.com/repos/.../zipball` → hay que allowlistar `api.github.com` (no solo codeload) →
[[registry-proxy-composer-dist-403]]. Pendiente follow-up: privados con credenciales (Vault), tmpfs
/tmp 64m puede quedar corto para stacks grandes, retirada total de run*\* del catálogo.
