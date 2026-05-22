# agent-runtime

The sandboxed container image an agent's loop runs inside.

`agent-runtime:v1` is the first runtime template (Plan 02 Fase B,
`task_02_05`). It carries a Python 3.12 interpreter, LangGraph, and the
`agent_runtime` package — and nothing else. In particular it has **no
platform credentials** and **no Docker client**.

## Build

```bash
docker build -t agent-runtime:v1 docker/agent-runtimes/agent-runtime/
```

## Isolation

The image itself is only half the story. The Celery worker that
launches it (`apps/workers`) enforces the sandbox at run time:

- `cap-drop ALL`, `no-new-privileges`
- read-only root filesystem (`/workspace` and `/tmp` are the only
  writable mounts)
- Docker's default-deny seccomp profile
- a dedicated, internal network — no host, no platform services, no
  inter-container traffic
- **never** the Docker socket

See `apps/workers/src/workers/isolation.py` and
ADR `0012-aislamiento-contenedores-agent-runtime`.

## Roadmap

- Fase B (now): dependency self-check entrypoint.
- Fase C (`task_02_10`): the LangGraph agent loop — perceive → recall →
  plan → act → observe → reflect → finalize → self_review.
