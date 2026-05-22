"""FastAPI application for the orchestrator service.

Two jobs:
  1. Expose `/healthz` and `/orchestrator/stats` for ops + the
     watchdog / system-health probes.
  2. Run the Redis Streams consumer loop as a background task tied to
     the app lifespan.

The consume loop lives in `consumer.StreamConsumer`; here we just own
its lifecycle (start on app startup, cancel on shutdown).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import FastAPI
from redis.asyncio import Redis

from orchestrator.config import Settings, get_settings
from orchestrator.consumer import StreamConsumer

_log = structlog.get_logger("orchestrator.app")


async def _run_loop(consumer: StreamConsumer) -> None:
    """Drive `consume_once()` forever until the task is cancelled.

    XREADGROUP blocks up to `block_ms`, so this isn't a busy-loop. A
    transient error (Redis blip) is logged and retried after a short
    backoff rather than killing the loop.
    """
    while True:
        try:
            await consumer.consume_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # loop must survive transient Redis blips
            _log.error("orchestrator.loop_error", error=str(exc))
            await asyncio.sleep(1.0)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app. `settings` is injectable for tests."""
    cfg = settings or get_settings()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        redis: Redis = Redis.from_url(cfg.redis_url, decode_responses=True)
        consumer = StreamConsumer(redis, cfg)
        await consumer.ensure_group()

        loop_task = asyncio.create_task(_run_loop(consumer))
        app.state.redis = redis
        app.state.consumer = consumer
        app.state.loop_task = loop_task
        _log.info(
            "orchestrator.started",
            stream=cfg.events_stream,
            group=cfg.consumer_group,
        )
        try:
            yield
        finally:
            loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task
            await redis.aclose()
            _log.info("orchestrator.stopped")

    app = FastAPI(
        title="agentic-platform / orchestrator",
        version="0.0.0",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/orchestrator/stats")
    async def stats() -> dict[str, Any]:
        consumer: StreamConsumer | None = getattr(app.state, "consumer", None)
        loop_task: asyncio.Task[None] | None = getattr(app.state, "loop_task", None)
        return {
            "stream": cfg.events_stream,
            "consumer_group": cfg.consumer_group,
            "loop_running": loop_task is not None and not loop_task.done(),
            "events": (
                consumer.stats.as_dict()
                if consumer is not None
                else {"processed": 0, "malformed": 0, "failed": 0}
            ),
        }

    return app


app = create_app()
