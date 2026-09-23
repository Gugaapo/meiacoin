"""Pure timer observation math. No I/O."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Observation:
    state: str
    direction: str
    locked: bool
    paused: bool
    ends_at: datetime | None
    paused_at: datetime | None
    observed_at: datetime
    status: str | None
    feed_seconds: int | None
    feed_value: str | None
    rules: dict
    fetched_at: datetime


def derive_increment(
    prev: Observation,
    cur: Observation,
    *,
    pause_tolerance: int = 60,
) -> dict:
    """Classify ends_at movement between two polls.

    Returns kind in: grant | pause_credit | adjustment | none.
    Negative raw deltas are adjustments (granted_seconds=0), never buys.
    """
    empty = {
        "kind": "none",
        "granted_seconds": 0,
        "raw_delta_seconds": 0,
        "pause_seconds": 0,
        "pause_credit_seconds": 0,
    }
    if prev.ends_at is None or cur.ends_at is None:
        return empty

    raw = int((cur.ends_at - prev.ends_at).total_seconds())
    if raw == 0:
        return empty

    if raw < 0:
        return {**empty, "kind": "adjustment", "raw_delta_seconds": raw}

    paused_seconds = 0
    if prev.paused or cur.paused:
        paused_seconds = abs(int((cur.observed_at - prev.observed_at).total_seconds()))

    credit = 0
    if paused_seconds and abs(raw - paused_seconds) <= pause_tolerance:
        credit = min(paused_seconds, raw)

    granted = raw - credit
    if credit and granted == 0:
        return {
            "kind": "pause_credit",
            "granted_seconds": 0,
            "raw_delta_seconds": raw,
            "pause_seconds": paused_seconds,
            "pause_credit_seconds": credit,
        }
    return {
        "kind": "grant",
        "granted_seconds": granted,
        "raw_delta_seconds": raw,
        "pause_seconds": paused_seconds,
        "pause_credit_seconds": credit,
    }
