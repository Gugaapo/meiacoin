"""Auto-create Twitch clips for Twitch/Pixie donation SSE events."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.config import get_settings
from app.database import get_db
from app.services.twitch_helix import TwitchHelixError, helix

logger = logging.getLogger(__name__)

_RATE_LIMIT_S = 3.0
_last_clip_start_mono: float = 0.0
_clip_tasks: set[asyncio.Task] = set()


def schedule_clip_for_event(stored: dict[str, Any] | None) -> None:
    """Fire-and-forget clip create for a stored twitch.*/pixie.* feed event."""
    if not stored or not stored.get("key"):
        return
    settings = get_settings()
    if not settings.twitch_clips_active:
        return
    event_type = str(stored.get("type") or "")
    if not (event_type.startswith("twitch.") or event_type.startswith("pixie.")):
        return
    if stored.get("clip_url") or stored.get("clip_id") or stored.get("clip_status"):
        return

    global _last_clip_start_mono
    now = time.monotonic()
    if now - _last_clip_start_mono < _RATE_LIMIT_S:
        logger.info("Skipping clip for %s (rate limit)", stored.get("key"))
        return
    _last_clip_start_mono = now

    label = str(stored.get("label") or event_type)
    user = stored.get("user_name")
    title = f"MeiaCoin · {label}"
    if user:
        title = f"{title} · {user}"
    title = title[:100]

    task = asyncio.create_task(
        _create_and_resolve_clip(str(stored["key"]), title),
        name=f"meiacoin-clip:{stored['key'][:48]}",
    )
    _clip_tasks.add(task)
    task.add_done_callback(_clip_tasks.discard)


async def _create_and_resolve_clip(feed_event_key: str, title: str) -> None:
    settings = get_settings()
    db = get_db()
    pad = max(0.0, float(settings.twitch_clip_pad_seconds))
    duration = float(settings.twitch_clip_duration)

    claim = await db.feed_events.update_one(
        {
            "key": feed_event_key,
            "clip_id": {"$exists": False},
            "$or": [
                {"clip_status": {"$exists": False}},
                {"clip_status": None},
            ],
        },
        {"$set": {"clip_status": "pending"}},
    )
    if claim.matched_count == 0:
        return

    if pad > 0:
        await asyncio.sleep(pad)

    doc = await db.feed_events.find_one({"key": feed_event_key})
    if not doc:
        return
    if doc.get("clip_id") or doc.get("clip_url"):
        return
    if not settings.twitch_clips_active:
        await db.feed_events.update_one(
            {"key": feed_event_key},
            {"$set": {"clip_status": "failed"}},
        )
        return

    try:
        clip_id = await helix.create_clip(title=title, duration=duration)
    except TwitchHelixError as exc:
        logger.warning(
            "Create clip failed for %s: %s %s",
            feed_event_key,
            exc.status,
            exc.detail,
        )
        await db.feed_events.update_one(
            {"key": feed_event_key},
            {"$set": {"clip_status": "failed", "clip_error": f"{exc.status}: {exc.detail}"[:400]}},
        )
        return
    except Exception:
        logger.exception("Unexpected create clip error for %s", feed_event_key)
        await db.feed_events.update_one(
            {"key": feed_event_key},
            {"$set": {"clip_status": "failed"}},
        )
        return

    await db.feed_events.update_one(
        {"key": feed_event_key},
        {"$set": {"clip_id": clip_id, "clip_status": "pending"}},
    )

    clip_url = await _wait_for_clip_url(clip_id)
    if not clip_url:
        logger.warning("Clip %s for %s did not become ready in time", clip_id, feed_event_key)
        await db.feed_events.update_one(
            {"key": feed_event_key},
            {"$set": {"clip_status": "failed", "clip_error": "timeout waiting for clip url"}},
        )
        return

    await db.feed_events.update_one(
        {"key": feed_event_key},
        {"$set": {"clip_url": clip_url, "clip_status": "ready"}, "$unset": {"clip_error": ""}},
    )
    await _propagate_clip_to_grant(feed_event_key, clip_url)
    logger.info("Clip ready for %s → %s", feed_event_key, clip_url)


async def _wait_for_clip_url(clip_id: str, *, timeout_s: float = 60.0) -> str | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            clip = await helix.get_clip(clip_id)
        except TwitchHelixError as exc:
            logger.debug("Get clip %s: %s", clip_id, exc)
            clip = None
        if clip:
            url = clip.get("url") or clip.get("embed_url")
            if isinstance(url, str) and url.strip():
                return url.strip()
            # Fallback from id slug
            slug = clip.get("id") or clip_id
            if slug:
                return f"https://clips.twitch.tv/{slug}"
        await asyncio.sleep(2.0)
    return None


async def _propagate_clip_to_grant(feed_event_key: str, clip_url: str) -> None:
    db = get_db()
    doc = await db.feed_events.find_one({"key": feed_event_key})
    if not doc:
        return
    grant_at = doc.get("matched_grant_at")
    if grant_at is None:
        return
    await db.events.update_one(
        {"at": grant_at, "kind": "grant", "attribution.attributed": True},
        {"$set": {"attribution.clip_url": clip_url}},
    )


def clip_url_from_feed_docs(docs: list[dict[str, Any]]) -> str | None:
    for c in docs:
        url = c.get("clip_url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    return None
