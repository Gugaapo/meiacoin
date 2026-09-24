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
    # Single ascending TTL index on feed_samples.at (30 days).
    existing = await db.feed_samples.index_information()
    ttl_ok = False
    for name, info in existing.items():
        keys = info.get("key")
        # Motor may return SON/dict or list of pairs.
        if isinstance(keys, dict):
            key_pairs = list(keys.items())
        else:
            key_pairs = list(keys or [])
        if key_pairs == [("at", 1)]:
            if info.get("expireAfterSeconds") == FEED_SAMPLES_TTL_SECONDS:
                ttl_ok = True
            else:
                await db.feed_samples.drop_index(name)
    if not ttl_ok:
        await db.feed_samples.create_index(
            [("at", 1)],
            expireAfterSeconds=FEED_SAMPLES_TTL_SECONDS,
        )
    await db.events.create_index([("at", 1)])
    await db.events.create_index([("kind", 1), ("at", 1)])
    await db.daily.create_index([("day", 1)], unique=True)
    await db.feed_events.create_index([("key", 1)], unique=True)
    await db.feed_events.create_index([("at", -1)])
    await db.feed_events.create_index([("type", 1), ("at", -1)])
    await db.feed_events.create_index([("matched_grant_at", 1), ("at", -1)])
    # Keep Twitch/Pixie event buffer for ~14 days (attribution + counters).
    existing_fe = await db.feed_events.index_information()
    ttl_name = None
    for name, info in existing_fe.items():
        keys = info.get("key")
        key_pairs = list(keys.items()) if isinstance(keys, dict) else list(keys or [])
        if key_pairs == [("received_at", 1)] and "expireAfterSeconds" in info:
            ttl_name = name
            if info.get("expireAfterSeconds") != 14 * 24 * 3600:
                await db.feed_events.drop_index(name)
                ttl_name = None
            break
    if ttl_name is None:
        await db.feed_events.create_index(
            [("received_at", 1)],
            expireAfterSeconds=14 * 24 * 3600,
        )
