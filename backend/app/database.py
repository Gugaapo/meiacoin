"""MongoDB connection and index setup for the meiacoin database."""
from __future__ import annotations

import logging
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import get_settings

logger = logging.getLogger(__name__)

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None

FEED_SAMPLES_TTL_SECONDS = 30 * 24 * 3600  # 30 days


async def connect() -> AsyncIOMotorDatabase:
    global _client, _db
    settings = get_settings()
    _client = AsyncIOMotorClient(
        settings.mongodb_url,
        serverSelectionTimeoutMS=settings.mongodb_timeout_ms,
    )
    _db = _client[settings.mongodb_db_name]
    await _client.admin.command("ping")
    await ensure_indexes(_db)
    logger.info("Connected to MongoDB db=%s", settings.mongodb_db_name)
    return _db


async def disconnect() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
        _client = None
        _db = None
        logger.info("Disconnected from MongoDB")


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("Database not connected")
    return _db


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.feed_samples.create_index("at", expireAfterSeconds=FEED_SAMPLES_TTL_SECONDS)
    await db.feed_samples.create_index([("at", 1)])
    await db.events.create_index([("at", 1)])
    await db.events.create_index([("kind", 1), ("at", 1)])
    await db.health.create_index("_id")
    await db.records.create_index("_id")
    await db.daily.create_index([("day", 1)], unique=True)
