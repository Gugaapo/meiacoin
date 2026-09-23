"""MeiaCoin FastAPI entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app import database as db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("meiacoin")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    yield
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

    @app.get("/api/v1/meia/health")
    async def health_placeholder():
        return {"status": "placeholder", "polls_ok": 0}

    return app


app = create_app()
