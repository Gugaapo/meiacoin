"""MeiaCoin read-only API routes."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from app.config import get_settings
from app.database import get_db

router = APIRouter(prefix="/api/v1/meia", tags=["meia"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/health")
async def health():
    settings = get_settings()
    db = get_db()
    doc = await db.health.find_one({"_id": "current"}) or {}
    last_ok = doc.get("last_ok")
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
    return {
        "status": status,
        "polls_ok": int(doc.get("polls_ok") or 0),
        "polls_fail": int(doc.get("polls_fail") or 0),
        "polls_403": int(doc.get("polls_403") or 0),
        "polls_429": int(doc.get("polls_429") or 0),
        "last_ok": last_ok.isoformat() if isinstance(last_ok, datetime) else last_ok,
        "max_gap_seconds": int(doc.get("max_gap_seconds") or 0),
        "last_gap_seconds": doc.get("last_gap_seconds"),
        "genesis_at": (
            doc["genesis_at"].isoformat()
            if isinstance(doc.get("genesis_at"), datetime)
            else doc.get("genesis_at")
        ),
        "events_count": int(events_count),
        "degraded": degraded,
        "model": settings.public_model_constants(),
    }
