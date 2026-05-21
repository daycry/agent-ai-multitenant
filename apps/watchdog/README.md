# watchdog

Container health watchdog with exponential backoff.

Polls Docker for each container in `WATCHDOG_SERVICES` (default: the
five phase-0 infra services) every `WATCHDOG_POLL_INTERVAL` seconds
(default: 30). If a container reports unhealthy:

1. Wait `10s × 3^attempt`: 10s, 30s, 90s, 270s, 810s.
2. Call `container.restart(timeout=10)`.
3. After 5 attempts emit `watchdog.alert` and stop retrying until a
   future health check succeeds.
4. A healthy status resets the counter.

## Run

```bash
python -m watchdog
```

## Env

| Var                        | Default                             | Meaning                       |
| -------------------------- | ----------------------------------- | ----------------------------- |
| `WATCHDOG_COMPOSE_PROJECT` | `agentic-platform`                  | Compose project prefix.       |
| `WATCHDOG_SERVICES`        | `postgres,redis,minio,vault,clamav` | Names of containers to watch. |
| `WATCHDOG_POLL_INTERVAL`   | `30`                                | Seconds between ticks.        |
