---
adr_id: "0060"
title: "Acceso de los workers al daemon Docker y ruta de red de la API interna del sandbox"
status: proposed
date: 2026-06-17
authors: [claude-code-2026-06]
plan_referenced: prod-01-despliegue-ejecutable
docs_language: es
---

# ADR 0060 — Daemon Docker para los workers + ruta de la API interna del sandbox

> **Estado: `proposed`** — requiere decisión del operador. Es la "decisión clave
> 1" del plan prod-01 (tasks 09 y 11). Afecta al Principio Rector nº2
> (aislamiento por contenedor): los workers lanzan runtimes de agente efímeros,
> pero **no pueden recibir el socket Docker directo** (= escape a root del host).

## Contexto

El worker (`apps/workers`) materializa cada tarea de agente lanzando un
contenedor `agent-runtime` efímero (red restringida, seccomp/AppArmor, cap-drop
ALL). Para crear/arrancar/parar esos contenedores necesita hablar con el daemon
Docker. Dos problemas a resolver en el despliegue generado:

1. **¿Cómo accede el worker al daemon Docker** sin violar el Principio 2?
   Montar `/var/run/docker.sock` en el worker da control total del daemon →
   crear un contenedor `--privileged` con el FS del host montado = root del host.
2. **¿Cómo alcanza el `agent-runtime` la API interna del api-server**
   (`/internal/agent/*`, que le entrega su contexto de agente)? El runtime sale
   por el `egress-proxy` deny-by-default (allowlist en `egress-proxy/filter.txt`,
   que NO incluye `api-server`), así que hoy esas llamadas se bloquean y el
   runtime degrada en silencio (finding sandbox-4).

## Decisión

### Parte A — Acceso al daemon Docker vía `docker-socket-proxy` (task_09)

Los workers **nunca** montan el socket. El compose generado añade un servicio
`docker-socket-proxy` (`tecnativa/docker-socket-proxy`, pin `:0.3.0`) que:

- monta `/var/run/docker.sock` (read-only) y expone la API Docker por TCP
  (`:2375`) **solo** en una red interna **dedicada** `agentic-docker`;
- aplica una **ACL por endpoint**: `CONTAINERS`/`IMAGES`/`NETWORKS`/`POST`
  permitidos (crear + cablear runtimes); `EXEC`/`VOLUMES`/`SWARM` y todo lo
  demás **denegado** (sin `docker exec`, sin bind-mounts del host, sin swarm);
- los servicios `workers` y `workers-privileged` reciben
  `DOCKER_HOST=tcp://docker-socket-proxy:2375` y se unen a `agentic-docker`.

`agentic-docker` es `internal: true` y **no** la comparten los runtimes
(que viven en `agentic-agents`): solo los workers alcanzan la API Docker.

**Alternativas consideradas:** (a) socket directo en el worker — **rechazada**
(escape a root); (b) Sysbox / Docker rootless / DinD — más aislamiento pero más
operativa y fuera del alcance de "Docker Compose una sola máquina"; reconsiderar
si el modelo de amenaza sube (ADR futuro); (c) Kata/gVisor para los runtimes —
ortogonal (refuerza el runtime, no cambia el acceso al daemon), futurible.

### Parte B — Ruta de red de la API interna (task_11)

**Pendiente de elección humana** entre dos opciones (la implementación de
task_11 toma la elegida):

- **Opción B1 (recomendada):** unir `api-server` a `agentic-agents` con un
  **listener interno dedicado** (la API pública sigue solo en `agentic-net`/
  detrás del reverse-proxy TLS de Fase E). El runtime llama a
  `http://api-server:8000/internal/agent/*` por `agentic-agents` **sin pasar por
  el egress-proxy** (el cliente httpx del runtime se crea con `trust_env=False`,
  task_11a, para no heredar `HTTP(S)_PROXY`). Mínima superficie nueva.
- **Opción B2:** alias de red dedicado / segundo puerto interno solo para
  `/internal/agent/*`. Más aislamiento (separa la superficie interna de la de
  negocio) a cambio de más config.

En ambos casos, task_11 sustituye la **degradación silenciosa** por un check de
arranque que **falla ruidosamente** si la API interna no responde con un agente
asignado (finding sandbox-4).

## Consecuencias

- **+** El daemon Docker queda tras una ACL mínima en una red dedicada; el
  socket nunca toca al worker ni al runtime. Cumple el Principio 2.
- **+** Un solo punto auditable de acceso al daemon (el proxy), con allowlist de
  endpoints como regression-guard en `_docker_socket_proxy_service`.
- **−** Una imagen y un servicio más en el stack; el proxy es sensible (tiene el
  socket) → va endurecido (cap-drop ALL, no-new-privileges, límites) y aislado.
- La Parte B queda **abierta**; sin decidirla, task_11 no puede cerrar
  sandbox-4. Recomendación: **B1**.
