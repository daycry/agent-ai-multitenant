---
adr: "0094"
title: Egress de runtime-templates a registries de paquetes vía proxy con allowlist
status: proposed
date: 2026-06-30
deciders: operador, System Architect (claude-opus)
phase: prod-12-hardening
related: ["0012", "0019", "0045", "0051", "0067", "0093"]
docs_language: es
---

# ADR 0094 — Egress de runtime-templates a registries de paquetes (`registry-proxy`)

## Contexto

Tras ADR 0093 (`stack_exec`) un agente ya puede pedir al worker que ejecute su toolchain
(`composer install`, `npm ci`, `go mod download`, `dotnet restore`, `pip install`…) en el
runtime-template del proyecto. Pero el runtime-template **no tiene egress alguno**, así que
esas instalaciones **no resuelven sus registries** y solo completan si el `dep_cache` ya está
caliente (en frío fallan). Análisis del código:

- `TestRuntimeRunner._create_bridge` (`apps/workers/src/workers/test_runtime.py`) crea un
  bridge efímero por tarea con `internal = (policy != "open")`. Todas las plantillas del
  catálogo (`packages/shared-test-runtimes/.../catalog.py`) son `none`/`restricted`; ninguna
  `open` → el bridge es **siempre `internal=True`** → sin ruta a internet (solo alcanza los
  sidecars de la propia tarea: postgres-test/redis-test).
- `_build_test_kwargs` **no inyecta `HTTP_PROXY`** al contenedor del runtime.
- El único opt-in (`network_policy="open"`) daría **NAT crudo** en un bridge no-interno, sin
  proxy ni allowlist — y nadie en producción lo usa. Abrir NAT crudo sería una regresión del
  Principio 2 (deny-by-default egress).
- El `egress-proxy` (tinyproxy, `FilterDefaultDeny`, ADR 0019) ya existe pero está cableado
  **solo al agent-runtime** (proveedores LLM y web del córtex). Su `filter.txt` no contiene
  ningún registry de paquetes, y vive en `agentic-agents` (la red del sandbox del agente).

Quién tiene que descargar dependencias es el **runtime-template** (lo lanza el worker), no el
agent-runtime (fino a propósito, principios 2 y 3). El problema es dar a ese runtime egress
**controlado** a los registries sin romper el aislamiento per-task ni regalar NAT crudo.

`docs/roadmap/prod-12` ya lo había previsto: `task_prod12_net_01` (Decisión 2) pide
explícitamente **un ADR** para la semántica de egress con dos opciones — (a) eliminar el
internet crudo enrutando por el egress-proxy con allowlist; (b) mantener `open` crudo
restringido a runtimes confiables con auditoría. Y su **Riesgo #4** nombra el caso de uso:
"enrutar por el proxy puede romper installs de npm/pip/nuget contra registries variados →
allowlist de registries comunes en el filtro".

## Decisión

Se elige la **opción (a)**: el runtime-template obtiene egress **solo a través de un proxy con
allowlist**, nunca NAT crudo. Se introduce un **proxy dedicado `registry-proxy`** (segunda
instancia de tinyproxy, disjunta del `egress-proxy` LLM) cuya allowlist son los **registries
públicos del catálogo de stacks** + los git hosts públicos.

### D1 — El bridge per-task se queda SIEMPRE `internal=True`

`_create_bridge` deja de calcular `internal = policy != "open"` y pasa **siempre
`internal=True`**. El egress no se concede por la red del bridge, sino conectando
transitoriamente el `registry-proxy` a ese bridge interno e inyectando
`HTTP(S)_PROXY=http://registry-proxy:8888` en el runtime. El bridge interno (sin ruta
off-bridge salvo el proxy) es la frontera; `HTTP_PROXY` es solo cómo el cliente encuentra el
proxy. Un script post-install malicioso que ignore el proxy y abra un socket crudo **no tiene a
dónde ir**. La política `open` se redefine como alias de la nueva `registries` (egress proxificado,
sin internet crudo) por retrocompatibilidad.

### D2 — Egress por-launch, desconectado antes de los tests

El egress se activa con un flag `dep_egress` en el `TestRuntimeSpec`, no como propiedad fija de
la plantilla. Lo activan los dos call sites que instalan dependencias:

- **`stack_exec`** (`run_command`, ADR 0093): el proxy queda conectado durante todo el comando
  — el comando _es_ el install.
- **Acceptance** (`launch`): el proxy se conecta para `default_pre_install` y **se desconecta
  antes de la primera check** → la fase de test corre **offline** (no puede exfiltrar). Decisión
  del operador (2026-06-30).

### D3 — `registry-proxy` separado del `egress-proxy` LLM (superficies disjuntas)

Dos instancias de tinyproxy con allowlists distintas, en redes distintas:

- `egress-proxy` (existente): proveedores LLM + web del córtex; vive en `agentic-net` +
  `agentic-agents`. Lo usa el **agent-runtime**.
- `registry-proxy` (nuevo): registries de paquetes + git hosts; vive **solo en `agentic-net`**
  (NUNCA en `agentic-agents`). Lo conecta el worker a los bridges per-task de los runtimes.

Así el **agent-runtime nunca alcanza github/pypi/etc.** y el runtime nunca alcanza los
endpoints LLM. Principio de mínimo privilegio. El coste es un contenedor alpine ~5 MB y un
fichero de filtro.

### D4 — Allowlist: solo registries públicos curados (sin credenciales)

Decisión del operador: en esta entrega, **solo registries y git hosts públicos** (sin auth, sin
allowlist por-proyecto). Hosts permitidos (regex ERE anclados al `Host`/CONNECT):
packagist/getcomposer, PyPI (`pypi.org`, `files.pythonhosted.org`), npm
(`registry.npmjs.org`), Go (`proxy.golang.org`, `sum.golang.org`, `storage.googleapis.com`),
Maven Central + Gradle, RubyGems, crates.io, NuGet (`*.nuget.org`), y git
(`github.com`, `codeload.github.com`, `api.github.com`, `*.githubusercontent.com`, `gitlab.com`,
`dev.azure.com`, `bitbucket.org`; `api.github.com` lo exige composer/go para los zipballs de dist
`api.github.com/repos/.../zipball/<ref>`). Los registries/git **privados** con credenciales (Packagist privado, GitLab
self-host, Nexus/Artifactory, PyPI interno) quedan para una iteración posterior (solapa con la
allowlist por-proyecto de la Ola B0.2, ADR 0067, y con la inyección de credenciales desde Vault).

## Anti-SSRF / seguridad

A diferencia de ADR 0067 (web-fetch de URLs **arbitrarias** controladas por el usuario), aquí
los hosts son **fijos y conocidos** del catálogo: no hay host controlado por el usuario, así que
**la allowlist estática ES el control** y no hace falta la maquinaria de DNS-pinning a este
nivel (el runtime ni siquiera resuelve el destino — manda `CONNECT pypi.org:443` por nombre y
el proxy, en `agentic-net` con DNS real, resuelve). Esto **complementa**, no sustituye, la Fase A
de prod-12 (que protege las tools `http_*` del agent-runtime, otra superficie).

**Aislamiento entre tareas** (el `registry-proxy` queda multi-homed sobre N bridges per-task +
`agentic-net`): no hay tránsito L3 entre bridges hermanos — el proxy no es router
(sin `ip_forward`, sin `NET_ADMIN`, sin MASQUERADE) y los runtimes en un bridge interno no
tienen ruta a la subred de otro bridge. El único camino cross-bridge es a nivel de aplicación vía
el CONNECT del proxy, bloqueado por `FilterDefaultDeny` (una IP interna no casa ningún regex → 403) y `ConnectPort 443/8443`. Es el mismo modelo de confianza ya aceptado para el `egress-proxy`
dual-homed sobre `agentic-agents`.

**Auditoría**: cada launch que habilita egress emite una línea de audit-log (requisito de
prod-12), reflejado en el texto de consentimiento.

Se preservan todas las invariantes del runtime endurecido: `cap_drop ALL`, `read_only`,
`no-new-privileges`, non-root, un bridge por tarea, sin socket Docker en el runtime.

## Invariantes preservadas (principios 2 y 3)

- El bridge per-task nunca es no-interno → **nunca hay NAT crudo**. Un sentinel de test lo fija.
- El agente sigue en su sandbox endurecido sin socket; el **worker** orquesta el runtime y el
  proxy — el mismo reparto que el sistema ya hace para los tests post-hoc y para `stack_exec`.
- La imagen del runtime-template no cambia (el env de proxy/caché/git-https se inyecta en el
  launch); se reusa la cañería de runtime-templates + dep-cache (ADR 0045/0051).

## Alternativas rechazadas

- **Bridge no-interno + NAT crudo** (lo que haría hoy `network_policy="open"`): regresión directa
  del Principio 2; egress sin allowlist. Rechazada — de hecho se **elimina** el camino.
- **Runtime en la red compartida `agentic-agents`** (para alcanzar el proxy): destruye el
  aislamiento per-task (runtimes concurrentes se verían) y contamina la red del sandbox del
  agente. Rechazada.
- **Reusar el `egress-proxy` LLM** con un filtro combinado: ampliaría la allowlist del agente
  (untrusted) con github/pypi/etc. → superficie de exfiltración. Rechazada por mínimo privilegio.
- **Bake de php/registries en una imagen multi-stack**: antipatrón ya rechazado en ADR 0093.

## Consecuencias / notas

- `storage.googleapis.com` en la allowlist es amplio (cualquier bucket público de GCS) pero lo
  exigen los zips de módulo Go servidos vía `proxy.golang.org`. Riesgo residual aceptable de
  superficie-registry; documentado.
- Deps git que por defecto usan SSH (`git@github.com:`) no atraviesan tinyproxy (HTTP/CONNECT
  solo) → se fuerza HTTPS con `GIT_CONFIG_* url.https://….insteadOf` inyectado solo cuando hay
  egress (sin rebuild de imagen).
- Alineación de caché: se corrige el mismatch `HOME=/workspace` vs `dep_cache_mount` con env
  por-tool (`COMPOSER_CACHE_DIR`, `PIP_CACHE_DIR`, `GOMODCACHE`…) para que el cache caliente
  reduzca egress entre ejecuciones. composer(`vendor/`)/npm(`node_modules/`)/go(cache) funcionan
  con el bind-mount uid-1000; pip/gem/nuget-global que instalan en rutas root dependen del trabajo
  de imagen non-root de prod-12 (`task_prod12_img_01`) — coordinación, no bloqueo de esta entrega.
- **Coordinación**: `apps/api-server/src/api_server/marketplace/sandbox.py` carga la misma
  semántica `open` de bridge crudo (la otra mitad de `task_prod12_net_01`); reusará el mismo
  helper de attach. Fuera del alcance de runtime-templates de este ADR, pero el ADR gobierna ambos.
- **Pendientes** (registries privados con credenciales, marketplace sandbox, tmpfs `/tmp`, caché
  no-root, retirada total de `run_*`, slug de proyecto): anotados en
  `docs/roadmap/registry-egress-followups.md`.

## Verificación e2e (despliegue dev)

Desplegado en dev (`registry-proxy` nuevo + `workers:ci`/`api-server:ci` reconstruidos con
`WITH_CLAUDE=1`) y verificado por el worker real (`TestRuntimeRunner.run_command` sobre un
worktree con `composer.json` que requiere `guzzlehttp/guzzle`):

- **Positivo (`dep_egress=True`)**: `composer install` → rc 0; resolvió guzzle + 8 deps
  transitivas y escribió `vendor/guzzlehttp/{guzzle,promises,psr7}` + `vendor/autoload.php` +
  `composer.lock` al worktree (mount RW persiste).
- **Negativo (`dep_egress=False`)**: el mismo `composer install` → rc 100, _"Could not resolve
  host: repo.packagist.org"_ — el bridge interno no tiene salida sin el proxy (no hay NAT crudo).
- **Allowlist (deny-by-default)**: probe HTTPS por el proxy → `repo.packagist.org`/`pypi.org` 200;
  `evil.example.com` → _"Tunnel connection failed: 403 Filtered"_.
- **Hallazgo**: composer descarga los zipballs de dist de paquetes GitHub vía
  `api.github.com/repos/.../zipball/<ref>` → hubo que añadir `^api\.github\.com$` al filtro.

## Trazabilidad

Investigación multi-agente de la topología de red (sesión `plan/runs-visor-trabajo`); plan en
`~/.claude/plans`. Implementación: `registry-proxy` (`docker/registry-proxy/`) + servicio en
`docker-compose.yml`; `test_runtime.py` (attach/detach proxy, bridge interno siempre, env);
`config.py` (settings del proxy); `shared-test-runtimes/{types.py,catalog.py}` (`NetworkPolicy`
`registries`, `cache_env`); `tasks.py` (`dep_egress=True` + audit). Resuelve `task_prod12_net_01`.
