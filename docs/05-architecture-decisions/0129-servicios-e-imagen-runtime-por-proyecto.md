---
title: "ADR 0129: Servicios auxiliares e imagen de runtime por proyecto"
status: accepted
date: 2026-07-24
deciders: [operador]
relates_to: [0062, 0063, 0093, 0094, 0021, 0045, 0051]
---

# ADR 0129: Servicios auxiliares e imagen de runtime por proyecto

## Contexto

Los agentes ejecutan `stack_exec`, los tests de aceptación y el app-preview de
validación humana dentro de **runtime-templates** (catálogo cerrado de 14
imágenes `agent-runtime-<id>:v1`, ADR 0093/0051). Un proyecto real necesita a
menudo **servicios de respaldo** (MySQL/MariaDB, Redis, colas tipo Beanstalkd,
Postgres) para que sus tests o su app previsualizable arranquen.

Hallazgo del análisis (2026-07-24): **la infraestructura de servicios ya existe
pero está DORMIDA**:

- `workers/test_runtime.py` tiene `AuxServiceSpec` + `_start_aux_services` +
  `build_aux_run_kwargs` (sidecars endurecidos: cap-drop ALL, no-new-privileges,
  caps de mem/pids, en el **bridge interno** del task, alcanzables por hostname)
  y `TestcontainersMode` (proxy DinD con ACL mínima). Trae `DEFAULT_POSTGRES` y
  `DEFAULT_REDIS`.
- PERO `TestRuntimeSpec.aux_services` por defecto es `()` y **ningún call site lo
  puebla** — ni `stack_exec`, ni los tests de aceptación, ni el review. No hay
  **superficie de configuración** de servicios en el proyecto.
- **No hay inyección de connection-string**: hoy la conexión es por convención
  (hostname `postgres-test`, credenciales `test/test/test`); el contenedor
  principal no recibe `DATABASE_URL`/`REDIS_URL`.
- El **review/preview NO monta servicios** (diferido en `review_runtime_task`),
  así que una app con base de datos no se puede previsualizar.
- La **imagen** del runtime es del catálogo cerrado (no customizable por tenant);
  el único precedente de imagen aportada por el tenant es `review_image` (ADR
  0062/0063), sin validación de registry/procedencia.

## Decisión

Exponer **dos mecanismos por proyecto**, ortogonales y con precedencia clara:

### 1) Servicios declarativos (recomendado, primario)

El proyecto declara una lista de servicios en `repository_config.services` y un
mapa de env en `repository_config.env`. Cada servicio es o bien un **tipo del
catálogo de servicios** (allowlist: `mysql`, `mariadb`, `postgres`, `redis`,
`beanstalkd`) o una **imagen arbitraria** (`{image, alias, env}`). El worker los
traduce a `AuxServiceSpec` (sidecars endurecidos que YA sabe lanzar) y **deriva
las variables de conexión** que inyecta en el contenedor principal
(`DATABASE_URL`, `REDIS_URL`, host/puerto/credenciales por servicio), fusionadas
con el `env` del proyecto. Alcance: `stack_exec` + tests de aceptación +
(fase 2) el review/preview. El bridge sigue **interno** (sin NAT).

Es la opción de mayor ROI y menor riesgo: la fontanería peligrosa (lanzar +
endurecer sidecars) ya está escrita y probada; esto añade la config, la
traducción y la inyección de env. Los servicios son **declarativos** (sin código
arbitrario del tenant corriendo AGENTE), reproducibles y aislados.

### 2) Imagen de runtime custom (secundario, para paquetes/extensiones)

Cuando un proyecto necesita **paquetes/extensiones de sistema** en el propio
runtime (no cubiertos por `stack_exec`/`default_pre_install` que instalan deps
del proyecto), puede fijar `repository_config.runtime_image` (mismo patrón que
`review_image`). Precedencia de imagen: `runtime_image` → runtime-template del
proyecto → default del tool → `python-pytest`. **Cómo construirla** (guía): basar
la imagen en un runtime-template de la plataforma e instalar lo que falte, p.ej.

```dockerfile
FROM agentic-platform/agent-runtime-php-phpunit:v1
USER root
RUN install-php-extensions gd intl && apt-get update && apt-get install -y --no-install-recommends imagemagick
USER 1000
```

o partir de cero cumpliendo el contrato del runtime-template (WORKDIR
`/workspace`, uid 1000, `ENTRYPOINT ["sleep","infinity"]`, el toolchain en PATH).
La plataforma **no construye** la imagen (como `review_image`, ADR 0063): la
publica el proyecto/CI y la plataforma la referencia por tag y la lanza
endurecida. Riesgo: corre CÓDIGO DEL AGENTE sobre una imagen del tenant →
supply-chain; validación de procedencia/escaneo queda **diferida** (igual que
`review_image` hoy); de momento la protección es el envelope de aislamiento
(cap-drop, sin socket Docker, red interna, uid no-root, root RO).

## Opciones consideradas

1. **Servicios declarativos + imagen custom opcional (elegida).** Cubre el caso
   común (DB/cache/colas) de forma segura y reutiliza infra existente; deja la
   imagen custom para el caso estrecho.
2. **Solo imagen custom (instalar todo en la imagen).** Rechazada como primaria:
   mete servicios DENTRO de la imagen del runtime (anti-patrón), no reproducible,
   y máximo riesgo de supply-chain.
3. **Statu quo (catálogo cerrado, sin servicios).** Bloquea apps realistas y el
   app-preview de apps con base de datos.

## Consecuencias

- **A favor:** apps realistas (CI4+MySQL, Node+Redis, colas) corren sus tests y
  se previsualizan; reutiliza sidecars endurecidos ya probados; connection-env
  explícito; imagen custom disponible para el caso que lo pida.
- **Riesgos / a validar:** (a) los servicios consumen recursos del host (caps de
  mem/pids por sidecar ya aplican; falta un tope por proyecto); (b) la imagen
  custom corre código del agente sin escaneo aún (envelope de aislamiento como
  única defensa, como `review_image`); (c) el `env` del proyecto puede contener
  secretos — va en `repository_config` (no Vault); para secretos reales, futura
  integración con Vault (fuera de alcance).
- **Relación:** reutiliza ADR 0093/0094 (runtime-templates, egress), 0062/0063
  (precedente `review_image` de imagen aportada por el tenant), y respeta el
  catálogo cerrado (0021/0045/0051): el catálogo de runtime-templates NO se abre;
  esto añade una capa de **servicios** + un override de **imagen** por proyecto.

## Estado de implementación

- **HECHO (2026-07-24), TDD:** módulo `workers/runtime_services.py`
  (`build_project_runtime_services(repository_config) -> (aux_services, main_env,
runtime_image_override)`): catálogo de servicios allowlisted + servicio de
  imagen arbitraria, derivación de connection-env, merge del `env` del proyecto,
  validación (tipos/alias/chars). `TestRuntimeSpec` gana `main_env`, inyectado en
  `_build_test_kwargs`. Cableado en `stack_exec_task` + `test_runtime_task`
  (leen `project.repository_config`). Override de imagen `runtime_image` en la
  resolución de `stack_exec`. Tests `tests/unit/test_runtime_services.py`.
- **HECHO (2026-07-24, fase 2), TDD:** el review/preview monta los servicios del
  proyecto. `_spawn_review_runtime` traduce `repository_config` a sidecars
  endurecidos sobre un **bridge interno per-sesión** (aislado — nunca en la red
  compartida `agentic-agents`, para no filtrar entre tenants), conecta el
  contenedor principal a ese bridge (resuelve los aux por alias) y le inyecta la
  connection-env. Los aux + el bridge llevan labels de la sesión de review para
  que los reapers los limpien: `expire_review_runtimes` reap por `container_ids`,
  `orphan_reaper` reap los contenedores aux por `review-session-id` y el bridge
  vacío por `component=review-runtime`. `review_autostart` hila
  `repository_config` en la request. Config inválida NO deja huérfana la review
  (cae a main-only). UI: sección **«Servicios e imagen de runtime»** en el hub de
  proyecto (servicios del catálogo / imagen arbitraria + env + `runtime_image`),
  con validación cliente espejo de la del backend. Tests
  `tests/unit/test_review_aux_services.py` + `test_orphan_container_reaper.py`
  (bridge de review). El override de imagen `runtime_image` NO aplica al review
  (usa `main_image`, la imagen de app del proyecto); solo a stack_exec/tests.
- **PENDIENTE (diferido, gated):** validación de procedencia/escaneo de la
  imagen custom (misma postura que `review_image`); tope de recursos por
  proyecto (caps de mem/pids por sidecar ya aplican; falta el agregado por
  proyecto).

## Addendum del 2026-09-02: «probada» era un `MagicMock`

La auditoría del 2026-09-01 (B-02) midió que la fontanería de los sidecars los
lanzaba con `cap_drop ALL` y **sin** `cap_add` ni `user`: exactamente la
combinación que `gotchas/docker-cap-drop-all-breaks-official-images.md` documenta
como crash-loop de las imágenes oficiales de postgres/redis/mysql (`gosu`/`su-exec`
necesitan `SETUID`/`SETGID` para bajar de root, y el entrypoint `CHOWN` sobre el
datadir). Ningún proyecto que declarase `services` podía arrancarlos; el único
test era un `MagicMock` que no levanta nada. Desde `task_cv_04` los sidecars
reciben el mismo `x-infra-caps` que el compose concede a esas imágenes, `remove`
lleva `v=True` para no dejar volúmenes anónimos, y el test cruza la lista con la
del compose para que no diverjan.

## Addendum del 2026-09-02: la imagen que declara un tenant va pineada (`task_cv_44`)

La auditoría del 2026-09-01 (B-05, B-09) midió que `image:` aceptaba cualquier
`host/repo:tag` y que `version` en un sidecar del catálogo recomponía
`repo:tag`, deshaciendo el pin por digest que la propia tabla del catálogo
cuida. Desde el 2026-09-02 una `image:` propia o un `runtime_image` llevan
`@sha256:` o vienen de un registry/prefijo de
`WORKERS_TENANT_IMAGE_REGISTRY_ALLOWLIST` (vacía por defecto: sólo digest), y
`version` resuelve contra `pinned_versions()` del catálogo; la que no está
pineada se rechaza nombrando las que hay. Los detalles y la parte de las
imágenes de runtime de la plataforma están en el addendum del ADR 0148.
