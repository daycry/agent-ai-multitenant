---
title: Un run con agente asignado acaba `failed` si el sandbox no alcanza al api-server
area: docker
encountered: 2026-08-19
stack: docker compose v2, agent-runtime:v1, api-server (prod-01 task_11 / sandbox-4)
---

## Síntoma

Cuatro tests de integración caen con la MISMA aserción, y ninguno menciona red,
api-server ni token:

```
assert 'failed' == <ExecutionStatus.DONE: 'done'>
```

- `tests/integration/test_e2e_smoke.py::test_full_pipeline_dispatches_runs_and_persists_an_execution`
- `tests/integration/test_autonomous_cycle.py::test_plan_task_travels_dispatch_run_review_to_done`
- `tests/integration/test_autonomous_cycle.py::test_reviewer_reject_sends_task_back_to_backlog`
- `tests/integration/test_orchestrator_dispatch.py::test_run_execution_celery_task_conducts_the_execution`

En el log del worker sólo aparece `workers.execution_finished status=failed`. El
modelo es `scripted`, así que la sospecha natural —«en CI no hay credencial de
LLM»— es **falsa** y hace perder el rato.

Fuera de los tests, el mismo síntoma es: todo run de agente muere en segundos
tras un despliegue en el que el api-server no está en la red `agentic-agents`.

## Causa raíz

Una cadena de cuatro eslabones que ningún fichero contaba entera:

1. si la tarea tiene **agente asignado**, `workers.execution._build_env` mintea
   `AGENTIC_INTERNAL_TOKEN` y publica `AGENTIC_API_URL` (ADR 0012);
2. el runtime construye su cliente con `InternalAgentAPI.from_env()`. **Sin**
   token devuelve `None` y salta las familias de conocimiento/memoria —eso es lo
   que hace que un run sin agente asignado no note nada—;
3. **con** token, `_build_internal_api()` llama a `ensure_reachable()`, que desde
   prod-01 `task_11` / `sandbox-4` **revienta el arranque** en vez de degradar en
   silencio (era la decisión correcta: un run de producción que se queda sin
   memoria y no lo dice es peor);
4. el contenedor sale 1 emitiendo
   `{"event": "execution.error", "error": "InternalAPIUnreachableError: ..."}` y
   el worker finaliza la ejecución en `failed`.

O sea: **un run con agente asignado necesita el api-server vivo en
`agentic-agents`**, y el discriminante que explica por qué unos tests caen y otros
no es exactamente `agent_id` — `test_worker_runs_execution.py` y
`test_live_approval_safeguards.py` siembran `agent_id=None` y por eso pasan.

El caso concreto: el job `test-integration` de CI levantaba
`postgres redis vault minio` y el `api-server` sólo existía en
`docker-compose.manuals.yml`, que ese job no apila. O sea que CI **no podía correr
ni un solo run con la forma que tienen todos los de producción**, y no se veía
porque el job agotaba su reloj de 45 min y GitHub lo marcaba `cancelled` — ni
verde ni rojo. Al partirlo en cuatro shards (2026-08-19) salieron los cuatro rojos
de golpe, la primera vez que ese job daba veredicto.

## Fix

**No** se tocó `ensure_reachable`: la guarda estaba haciendo su trabajo.

1. **El job tiene la precondición.** `docker/docker-compose.ci.yml` declara un
   `api-server` mínimo (en `agentic-net` + `agentic-agents`) y `ci.yml` construye
   `agentic-platform/api-server:ci` con caché de buildx y lo levanta con `--wait`
   antes de pytest. Se descartó apilar `docker-compose.manuals.yml`: ese overlay
   **redefine `vault`** (de `-dev` a modo servidor con desellado por un compañero)
   y dejaría el Vault de CI sellado.
2. **La precondición se comprueba y dice su nombre.**
   `_pipeline_helpers.require_internal_api_reachable()` sondea `/healthz` **desde
   la red del sandbox** (un contenedor en `agent_network`, porque esa red es
   `internal` y un `curl` desde el host no dice nada sobre ella) y contra la MISMA
   URL que el worker inyecta. Skip honesto en local, **FAIL bajo CI** — mismo
   criterio que `require_agent_runtime_image` (finding tests-6).
3. **El motivo viaja en la aserción.** `why_the_run_failed()` va como mensaje del
   `assert` (Python sólo lo evalúa si falla), y vuelca `abort_code`, `output` y el
   último paso del `steps_log`, que ya estaban en la fila y no los miraba nadie.

## Cómo verificar el fix

Reproducir la avería sin desmontar nada: apuntar el worker a un api-server que no
existe y lanzar el sondeo.

```bash
docker run --rm --network agentic-agents \
  -e AGENT_TASK_SPEC='{"task":{"id":"...","title":"probe","description":""},
                       "model":{"kind":"scripted","decisions":[{"kind":"finish","output":"ok"}]}}' \
  -e AGENTIC_INTERNAL_TOKEN=cualquiera \
  -e AGENTIC_API_URL=http://api-server-que-no-existe:8000 \
  agent-runtime:v1
# -> {"error": "InternalAPIUnreachableError: internal API at ... is not reachable
#     after 3 attempt(s): ConnectError('[Errno -3] Temporary failure in name
#     resolution')...", "event": "execution.error"}
```

Y que la precondición lo diga en 20 segundos en vez de en cinco minutos:

```bash
CI=1 WORKERS_AGENT_INTERNAL_API_URL=http://api-server-que-no-existe:8000 \
  pytest tests/integration/test_e2e_smoke.py -x -q
# -> Failed: [CI] required precondition missing: el api-server no contesta en
#    http://api-server-que-no-existe:8000/healthz desde la red agentic-agents: ...
```

Con el stack completo (dev: el overlay `manuals`; CI: el paso «Bring up the
api-server») los cuatro tests pasan.
