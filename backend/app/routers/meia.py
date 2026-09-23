"""MeiaCoin read-only API routes."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from app.config import get_settings
from app.database import get_db

router = APIRouter(prefix="/api/v1/meia", tags=["meia"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@router.get("/health")
async def health():
    settings = get_settings()
    db = get_db()
    doc = await db.health.find_one({"_id": "current"}) or {}
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
        events_count = await db.events.count_documents({})
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
