"""15s timer poller + SSE feed_events / attribution."""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.database import get_db
from app.services import attribution as attr
from app.services.math_ingest import Observation, derive_increment, parse_dt
from app.services.sse_client import stream_client
from app.services.timer_client import TimerFeedError, client as timer_client

logger = logging.getLogger(__name__)

HEALTH_ID = "current"
GENESIS_ID = "genesis"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        return parse_dt(dt)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _ensure_health_doc() -> dict[str, Any]:
    db = get_db()
    doc = await db.health.find_one({"_id": HEALTH_ID})
    if doc:
        return doc
    initial = {
        "_id": HEALTH_ID,
        "polls_ok": 0,
        "polls_fail": 0,
        "polls_403": 0,
        "polls_429": 0,
        "last_ok": None,
        "last_fail": None,
        "last_gap_seconds": None,
        "max_gap_seconds": 0,
        "genesis_at": None,
        "e_genesis": None,
        "last_ends_at": None,
        "events_count": 0,
        "status": "placeholder",
        "updated_at": _utc_now(),
    }
    await db.health.insert_one(initial)
    return initial


async def _bump_health(**fields: Any) -> None:
    db = get_db()
    fields["updated_at"] = _utc_now()
    await db.health.update_one({"_id": HEALTH_ID}, {"$set": fields}, upsert=True)


async def _inc_health(field: str, amount: int = 1) -> None:
    db = get_db()
    await db.health.update_one(
        {"_id": HEALTH_ID},
        {"$inc": {field: amount}, "$set": {"updated_at": _utc_now()}},
        upsert=True,
    )


async def record_observation(obs: Observation, *, prev: Observation | None) -> Observation:
    """Persist one successful poll and any ends_at change."""
    db = get_db()
    settings = get_settings()
    at = obs.fetched_at

    sample = {
        "at": at,
        "ends_at": obs.ends_at,
        "observed_at": obs.observed_at,
        "seconds": obs.feed_seconds,
        "value": obs.feed_value,
        "state": obs.state,
        "direction": obs.direction,
        "paused": obs.paused,
        "paused_at": obs.paused_at,
        "locked": obs.locked,
        "status": obs.status,
        "rules": obs.rules,
        "source": "poll",
    }
    await db.feed_samples.insert_one(sample)

    health = await _ensure_health_doc()
    gap = None
    last_ok = _as_utc(health.get("last_ok"))
    if last_ok is not None:
        gap = max(0, int((at - last_ok).total_seconds()))

    max_gap = int(health.get("max_gap_seconds") or 0)
    if gap is not None and gap > max_gap:
        max_gap = gap

    updates: dict[str, Any] = {
        "last_ok": at,
        "last_gap_seconds": gap,
        "max_gap_seconds": max_gap,
        "status": "ok",
        "last_ends_at": obs.ends_at,
        "last_state": obs.state,
        "last_paused": obs.paused,
        "last_direction": obs.direction,
    }

    # Capture genesis on first successful poll with an ends_at.
    if health.get("genesis_at") is None and obs.ends_at is not None:
        updates["genesis_at"] = at
        updates["e_genesis"] = obs.ends_at
        await db.health.update_one(
            {"_id": HEALTH_ID},
            {"$set": {"genesis_at": at, "e_genesis": obs.ends_at}},
            upsert=True,
        )
        logger.info("Genesis captured at=%s e_genesis=%s", at.isoformat(), obs.ends_at.isoformat())

    await _inc_health("polls_ok", 1)
    await _bump_health(**updates)

    if prev is not None and prev.ends_at is not None and obs.ends_at is not None:
        if prev.ends_at != obs.ends_at:
            inc = derive_increment(
                prev,
                obs,
                pause_tolerance=settings.pause_credit_tolerance_seconds,
            )
            kind = inc["kind"]
            if kind in ("grant", "adjustment", "pause_credit"):
                # Normalize pause_credit into adjustment for storage (zero granted).
                store_kind = "grant" if kind == "grant" and inc["granted_seconds"] > 0 else (
                    "grant" if kind == "grant" else "adjustment"
                )
                if kind == "grant" and inc["granted_seconds"] <= 0:
                    store_kind = "adjustment"
                precision = max(
                    1,
                    abs(int((obs.fetched_at - prev.fetched_at).total_seconds())),
                )
                event = {
                    "at": at,
                    "kind": store_kind,
                    "granted_seconds": int(inc["granted_seconds"]) if store_kind == "grant" else 0,
                    "raw_delta_seconds": int(inc["raw_delta_seconds"]),
                    "precision_seconds": precision,
                    "ends_at_before": prev.ends_at,
                    "ends_at_after": obs.ends_at,
                    "pause_seconds": int(inc.get("pause_seconds") or 0),
                    "pause_credit_seconds": int(inc.get("pause_credit_seconds") or 0),
                    "direction": obs.direction,
                }
                await db.events.insert_one(event)
                await _inc_health("events_count", 1)
                logger.info(
                    "Event kind=%s granted=%s raw=%s precision=%ss",
                    event["kind"],
                    event["granted_seconds"],
                    event["raw_delta_seconds"],
                    precision,
                )
                if store_kind == "grant" and event["granted_seconds"] > 0:
                    try:
                        await attr.attribute_grant(
                            grant_at=at,
                            granted_seconds=int(event["granted_seconds"]),
                            precision_seconds=precision,
                        )
                    except Exception:
                        logger.exception("Grant attribution failed")

    return obs


async def _apply_timer_updated(payload: dict[str, Any]) -> None:
    """Refresh latest sample from SSE without creating grants (poll owns deltas).

    Skip writing a sample if ends_at is missing — that would blank the ticker.
    """
    current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
    if not current:
        return
    db = get_db()
    now = _utc_now()
    ends_at = parse_dt(current.get("ends_at"))
    observed = parse_dt(payload.get("observed_at")) or now
    # Merge rules from last poll sample when SSE omits them.
    last = await db.feed_samples.find_one(sort=[("at", -1)])
    if ends_at is None:
        ends_at = parse_dt((last or {}).get("ends_at"))
        if ends_at is None:
            logger.warning("Ignoring timer.updated without ends_at")
            return
    rules = (last or {}).get("rules") if isinstance((last or {}).get("rules"), dict) else {}
    sample = {
        "at": now,
        "ends_at": ends_at,
        "observed_at": observed,
        "seconds": current.get("seconds"),
        "value": current.get("value"),
        "state": str(current.get("state") or (last or {}).get("state") or "unavailable"),
        "direction": str(current.get("direction") or (last or {}).get("direction") or "increase"),
        "paused": bool(current.get("paused")) if "paused" in current else bool((last or {}).get("paused")),
        "paused_at": (last or {}).get("paused_at"),
        "locked": bool(current.get("locked")) if "locked" in current else bool((last or {}).get("locked")),
        "status": (last or {}).get("status"),
        "rules": rules,
        "source": "sse",
    }
    await db.feed_samples.insert_one(sample)
    await _bump_health(
        last_ok=now,
        status="ok",
        last_ends_at=ends_at,
        last_state=sample["state"],
        last_paused=sample["paused"],
        last_direction=sample["direction"],
        last_sse_at=now,
    )


async def handle_stream_event(
    event_type: str,
    payload: dict[str, Any] | None,
    sse_id: str | None,
) -> None:
    if event_type == "handshake":
        await _bump_health(last_sse_handshake_at=_utc_now(), sse_connected=True)
        return
    if event_type == "timer.updated" and isinstance(payload, dict):
        await _apply_timer_updated(payload)
        await attr.store_feed_event(event_type, payload, sse_id=sse_id)
        return
    if event_type.startswith("twitch.") or event_type.startswith("pixie.") or event_type.startswith("timer."):
        await attr.store_feed_event(event_type, payload, sse_id=sse_id)
        await _inc_health("sse_events", 1)
        return
    logger.debug("Ignoring SSE event type=%s", event_type)


async def stream_forever(stop: asyncio.Event) -> None:
    await stream_client.run_forever(stop, handle_stream_event)


async def poll_forever(stop: asyncio.Event) -> None:
    settings = get_settings()
    poll_s = max(5, int(settings.timer_poll_seconds))
    prev: Observation | None = None
    await _ensure_health_doc()
    logger.info("MeiaCoin poller started cadence=%ss ±2s", poll_s)

    while not stop.is_set():
        error_backoff = poll_s
        try:
            obs = await timer_client.get_timer()
            prev = await record_observation(obs, prev=prev)
        except TimerFeedError as exc:
            logger.warning("Poll failed: %s", exc)
            await _inc_health("polls_fail", 1)
            fail_fields: dict[str, Any] = {
                "last_fail": _utc_now(),
                "status": "degraded",
                "last_error": str(exc),
            }
            if exc.status == 403:
                await _inc_health("polls_403", 1)
                fail_fields["status"] = "feed_forbidden"
            elif exc.status == 429:
                await _inc_health("polls_429", 1)
            await _bump_health(**fail_fields)
            retry = exc.retry_after
            error_backoff = min(
                poll_s * 10,
                max(poll_s, int((retry or poll_s) * 1.5)),
            )
            error_backoff = error_backoff * (0.8 + random.random() * 0.4)
        except Exception:
            logger.exception("Unexpected poller error")
            await _inc_health("polls_fail", 1)
            await _bump_health(last_fail=_utc_now(), status="degraded")
            error_backoff = poll_s * 2

        # ±2s jitter around nominal cadence (or error backoff).
        base = error_backoff if error_backoff != poll_s else poll_s
        if error_backoff != poll_s:
            delay = error_backoff
        else:
            delay = max(1.0, poll_s + random.uniform(-2.0, 2.0))
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
            break
        except asyncio.TimeoutError:
            continue

    logger.info("MeiaCoin poller stopped")
