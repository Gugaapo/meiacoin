"""MeiaCoin FastAPI entrypoint."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import database as db
from app.config import get_settings
from app.routers import meia as meia_router
from app.services import ingest
from app.services.timer_client import client as timer_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("meiacoin")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await db.connect()

    stop = asyncio.Event()
    task = None
    if settings.is_timer_configured:
        task = asyncio.create_task(ingest.poll_forever(stop), name="meiacoin-poller")

        def _done(t: asyncio.Task) -> None:
            try:
                exc = t.exception()
            except asyncio.CancelledError:
                return
            if exc is not None:
                logger.error("Poller crashed: %s", exc, exc_info=exc)

        task.add_done_callback(_done)
        app.state.marathon_task = task
        app.state.marathon_stop = stop
        logger.info("Poller task started")
    else:
        logger.warning("Timer feed not configured; poller not started")

    yield

    if task is not None:
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await timer_client.aclose()
    await db.disconnect()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="MeiaCoin",
        version="0.1.0",
        lifespan=lifespan,
        root_path=settings.api_root_path,
    )
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["https://tossemideia.cloud"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.include_router(meia_router.router)
    return app


app = create_app()
