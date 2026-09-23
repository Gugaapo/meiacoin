"""MeiaCoin read-only API routes."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query

from app.config import get_settings
from app.database import get_db
from app.services import market

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/meia", tags=["meia"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_model() -> dict:
    return get_settings().public_model_constants()


@router.get("/health")
async def health():
    settings = get_settings()
    try:
        db = get_db()
        doc = await db.health.find_one({"_id": "current"}) or {}
    except Exception as exc:
        logger.warning("health db error: %s", exc)
        return {
            "status": "degraded",
            "polls_ok": 0,
            "polls_fail": 0,
            "last_ok": None,
            "max_gap_seconds": 0,
            "genesis_at": None,
            "events_count": 0,
            "degraded": True,
            "stale": True,
            "model": _safe_model(),
        }
    last_ok = _as_utc(doc.get("last_ok"))
    degraded = False
    status = doc.get("status") or "placeholder"
    if last_ok is not None:
        age = (_now() - last_ok).total_seconds()
        if age > settings.timer_degraded_seconds:
            degraded = True
            if status == "ok":
                status = "degraded"
    events_count = doc.get("events_count")
    if events_count is None:
        try:
            events_count = await db.events.count_documents({})
        except Exception:
            events_count = 0
    genesis_at = _as_utc(doc.get("genesis_at"))
    return {
        "status": status,
        "polls_ok": int(doc.get("polls_ok") or 0),
        "polls_fail": int(doc.get("polls_fail") or 0),
        "polls_403": int(doc.get("polls_403") or 0),
        "polls_429": int(doc.get("polls_429") or 0),
        "last_ok": last_ok.isoformat() if last_ok else None,
        "max_gap_seconds": int(doc.get("max_gap_seconds") or 0),
        "last_gap_seconds": doc.get("last_gap_seconds"),
        "genesis_at": genesis_at.isoformat() if genesis_at else None,
        "events_count": int(events_count),
        "degraded": degraded,
        "model": settings.public_model_constants(),
    }


@router.get("/ticker")
async def ticker():
    try:
        return await market.ticker_payload()
    except Exception as exc:
        logger.exception("ticker error: %s", exc)
        return {
            "model": _safe_model(),
            "price": None,
            "stale": True,
            "degraded": True,
            "error": "unavailable",
        }


@router.get("/series")
async def series(
    tf: str = Query("1h", pattern="^(1m|5m|15m|1h|4h|1d)$"),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
):
    try:
        return await market.series_payload(tf=tf, from_ts=from_, to_ts=to)
    except Exception as exc:
        logger.exception("series error: %s", exc)
        return {
            "model": _safe_model(),
            "tf": tf,
            "bars": [],
            "markers": [],
            "stale": True,
        }


@router.get("/trades")
async def trades(limit: int = Query(100, ge=1, le=500)):
    try:
        return await market.trades_payload(limit=limit)
    except Exception as exc:
        logger.exception("trades error: %s", exc)
        return {"model": _safe_model(), "trades": [], "stale": True, "label": market.TIP_LABEL}


@router.get("/burn")
async def burn():
    try:
        return await market.burn_payload()
    except Exception as exc:
        logger.exception("burn error: %s", exc)
        return {
            "model": _safe_model(),
            "burn_wall_brl_per_hour": 60.0,
            "pace_brl_per_hour": 0.0,
            "coverage_pct": 0.0,
            "deficit_brl_per_hour": 60.0,
            "label": market.TIP_LABEL,
            "stale": True,
        }


@router.get("/records")
async def records():
    try:
        return await market.records_payload()
    except Exception as exc:
        logger.exception("records error: %s", exc)
        return {"model": _safe_model(), "stale": True}
