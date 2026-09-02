---
adr: "0012"
title: Aislamiento de contenedores agent-runtime
status: accepted
date: 2026-05-22
deciders: System Architect, Security
phase: 02-ejecucion-agentes
---

# ADR 0012 — Aislamiento de contenedores agent-runtime

## Contexto

Plan 02 Fase B pone a los agentes a ejecutar trabajo real. El principio
rector 2 (CLAUDE.md) es innegociable: **los workers no ejecutan código
del usuario**; lanzan contenedores efímeros con red restringida, sin
socket Docker, `cap-drop ALL` y seccomp.

Hay que decidir, en concreto:

1. Qué perfil de _hardening_ aplica el worker a cada contenedor.
2. Cómo se construye la imagen `agent-runtime`.
3. Cómo llegan las credenciales al agente sin filtrarse.
4. Cómo se garantiza —y se verifica con un test— que el agente nunca
   alcanza el socket Docker.

Restricciones: instalación mono-máquina con Docker Compose (no
Kubernetes, no Swarm), host Linux en producción y Docker Desktop en
desarrollo (Windows/macOS).

## Decisión

### Imagen `agent-runtime:v1` (task_02_05)

`docker/agent-runtimes/agent-runtime/` — imagen multi-stage
`python:3.12-slim` + LangGraph + el paquete interno `agent_runtime`.
No lleva credenciales ni cliente Docker. Build:

```
docker build -t agent-runtime:v1 docker/agent-runtimes/agent-runtime/
```

El contexto de build es la propia carpeta del runtime (no la raíz del
repo): la imagen es autocontenida y el catálogo de runtime templates
(principio rector 3) crece añadiendo carpetas hermanas. En Fase B el
_entrypoint_ es un self-check; el agent loop LangGraph aterriza en
Fase C (task_02_10).

### Perfil de _hardening_ del worker (task_02_06 / task_02_07)

`apps/workers/src/workers/isolation.py` construye, sin ruta de
excepción, los kwargs de `docker.containers.run`:

- `cap_drop=["ALL"]` + `no-new-privileges` — cero capabilities Linux y
  sin posibilidad de recuperarlas vía binarios setuid.
- **Root filesystem read-only.** Sólo `/workspace` y `/tmp` son
  escribibles, ambos como `tmpfs` con tamaño acotado. Cuando llegue el
  worktree compartido (Plan 06), `/workspace` será un bind read-write.
- **Red dedicada `agentic-agents`**, `internal` (sin egress directo a
  host ni a internet). ICC se habilitó (`enable_icc=true`) en
  `task_02_35` / ADR 0019 para que el agente pueda alcanzar al servicio
  `egress-proxy` que comparte esa red; cualquier otro destino externo
  pasa filtrado por ese proxy (allowlist). El agente sigue sin alcanzar
  los servicios de plataforma (Postgres/Redis/Vault) porque éstos viven
  en `agentic-net`, otra red.
- **Usuario no-root** (uid/gid 1000).
- **Límites de pids y memoria** — una fork bomb o una fuga no tumban
  el host.
- El worker nunca monta el socket Docker (ver más abajo).

`AgentContainerRunner` (`container.py`) lanza el contenedor, lo
supervisa con un presupuesto de _wall-clock_ y lo elimina siempre —
incluso si expira o falla. Modelo **simple: un contenedor por tarea**;
el pool elástico por plan es Plan 06 (sección 12.5 del .docx).

### Seccomp y AppArmor: el _default_ de Docker, no un perfil propio

Docker aplica de serie un perfil seccomp **default-deny**
(`SCMP_ACT_ERRNO` + allowlist) y, donde el kernel lo soporta, el perfil
AppArmor `docker-default`. La decisión es **apoyarse en ellos**: el
worker nunca pasa `seccomp=unconfined` ni `apparmor=unconfined`, así
que ambos siguen vigentes. `WORKERS_SECCOMP_PROFILE` y
`WORKERS_APPARMOR_PROFILE` permiten fijar un perfil más estricto sin
tocar código.

No se mantiene un perfil seccomp propio en el repo: replicar el
default de Docker es deuda de mantenimiento, y un perfil _más_
restrictivo escrito a mano es frágil (un syscall olvidado y el
intérprete Python no arranca). El test `test_container_isolation.py`
verifica que seccomp está **activo** dentro del contenedor (campo
`Seccomp: 2` de `/proc/self/status` — modo filtro).

### Inyección de credenciales (task_02_08)

Las credenciales **nunca** viajan en el entorno del contenedor — un
env var se filtra a `docker inspect`, a los volcados y a los procesos
hijo. En su lugar (`workers/secrets.py`):

```
Vault → fichero en staging (host) → bind mount read-only → /run/secrets/<name>
```

Mismo modelo que un `secrets:` de Docker Compose. El _staging_ es un
`mkdtemp` por lanzamiento, se borra al terminar el contenedor.
`SecretsProvider` es la costura donde enchufa un proveedor respaldado
por Vault; Fase B trae el provider estático y el mecanismo
fichero → `/run/secrets`.

### El socket Docker, jamás (task_02_09)

Un contenedor con acceso al socket Docker escapa al host de forma
trivial. Defensa en dos capas:

1. **Por construcción**: el perfil no monta nada en `/var/run` y, con
   `tmpfs` para `/workspace`, un lanzamiento normal no tiene bind
   mounts.
2. **Tripwire**: `assert_no_docker_socket()` inspecciona los kwargs
   antes de cada lanzamiento y aborta si algún `volume`/`mount`
   apunta a `docker.sock` o al named pipe `docker_engine` de Windows.
   Una edición descuidada futura no puede reintroducir el socket en
   silencio.

## Alternativas descartadas

1. **Perfil seccomp propio versionado en el repo.** Aporta poco sobre
   el default-deny de Docker y es deuda de mantenimiento; un perfil
   hecho a mano más estricto es frágil. Se deja como knob de
   configuración para quien lo necesite.
2. **Credenciales por variable de entorno.** Simple, pero se filtran
   a `docker inspect`, logs y procesos hijo. Inaceptable.
3. **gVisor / Kata / Firecracker.** Aislamiento más fuerte (kernel
   propio por sandbox), pero un componente más que instalar y operar
   en una plataforma mono-máquina. La combinación namespaces +
   cap-drop + seccomp + read-only + red interna es suficiente para el
   alcance actual. Reevaluable si el modelo de amenaza se endurece.
4. **Montar el socket Docker en el worker para que controle a sus
   "hermanos".** El worker sí necesita hablar con el daemon — pero lo
   hace desde el worker, **nunca** desde dentro del contenedor del
   agente. El contenedor del agente jamás ve el socket.
5. **Imagen agent-runtime construida desde la raíz del repo** (para
   poder copiar `packages/shared-*`). Rompe la autocontención del
   catálogo de runtimes; las libs compartidas se publicarán a un
   índice interno cuando haga falta.

## Consecuencias

Positivas:

- Aislamiento estricto verificado por tests automáticos
  (`test_container_isolation.py`, `test_no_docker_socket.py`,
  `test_secrets_injection.py`) — no sólo por configuración.
- Sin perfiles seccomp/AppArmor propios que mantener; los _knobs_
  permiten endurecer sin código.
- El tripwire `assert_no_docker_socket` hace imposible reintroducir el
  socket por descuido.

Negativas / cuidados:

- **Red `internal`**: el agente no tiene egress. La tool `http_request`
  (Fase D, task_02_17) necesitará una vía de salida controlada
  (proxy con allowlist); se aborda en su tarea.
- **AppArmor** depende del kernel del host. En Docker Desktop puede no
  estar disponible; el perfil `docker-default` se aplica donde el host
  lo soporta y se omite donde no — sin romper el lanzamiento.
- Los tests de contenedores de Fase B usan `python:3.12-slim` (la base
  de `agent-runtime`), no la imagen completa: verifican el _sandbox_,
  no el agent loop, y así no dependen del build pesado de LangGraph.
- Modelo un-contenedor-por-tarea: sin reutilización inter-paso todavía.
  La imagen y el loop se diseñan para soportarla; el pool elástico es
  Plan 06.

## Adenda 2026-09-02 — un bridge por ejecución (`task_cv_25`, auditoría B-07)

La red «dedicada» de esta decisión era UNA red (`agentic-agents`, `internal`,
con ICC) compartida por todos los sandboxes de todos los tenants, los previews
de review, el api-server y los workers: dos sandboxes de tenants distintos
podían hablarse por IP. Desde el 2026-09-02 el worker crea un bridge `internal`
por ejecución (`agent-run-<exec>-<hex>`, etiqueta
`com.agentic-platform.run-bridge`), le conecta con alias sólo lo que ese run
necesita —el egress-proxy (`WORKERS_EGRESS_PROXY_CONTAINER`), el api-server
interno y los servidores MCP internos que declare el proyecto— y lo desmonta al
terminar; el sweeper de ejecuciones rancias poda los que deja un worker que
muere. `WORKERS_AGENT_NETWORK_PER_EXECUTION=false` devuelve la red compartida.
El preview de review (ADR 0130) sigue en `agentic-agents`: no ejecuta código del
agente en vivo y su aislamiento es otro (worktree de sólo lectura, `task_cv_26`).

## Referencias

- `docs/roadmap/02-ejecucion-agentes.md` — Fase B, task_02_05..09.
- Imagen: `docker/agent-runtimes/agent-runtime/`.
- Worker: `apps/workers/src/workers/{isolation,container,secrets,tasks}.py`.
- Tests: `tests/integration/test_worker_launches_container.py`,
  `test_container_isolation.py`, `test_secrets_injection.py`,
  `test_no_docker_socket.py`.
- Documento maestro, secciones 12, 12.5 y 12.6 (ejecución y aislamiento).
- ADR 0011 — bus de eventos (el orchestrator encola el trabajo que
  estos contenedores ejecutan).
