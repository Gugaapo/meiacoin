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

    Pauses are common (stream breaks, etc.). MeiaUm typically freezes remaining
    time and, on unpause, pushes ends_at forward by ~pause duration. That restore
    must never count as a buy. Real tips during/around pause still raise
    remaining seconds — we use that as the buy signal whenever pause is involved.
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

    rem_delta: int | None = None
    if prev.feed_seconds is not None and cur.feed_seconds is not None:
        rem_delta = int(cur.feed_seconds) - int(prev.feed_seconds)

    pause_involved = bool(prev.paused or cur.paused)

    # --- Pause-aware path: remaining time is the buy signal -----------------
    # ends_at can jump for pause restore; only rem increases are real tips.
    if pause_involved and raw > 0:
        if rem_delta is not None:
            buy = max(0, rem_delta)
            # Flat/down remaining + ends_at jump → pure pause restore (or burn).
            if buy <= pause_tolerance:
                if raw > pause_tolerance:
                    return {
                        "kind": "pause_credit",
                        "granted_seconds": 0,
                        "raw_delta_seconds": raw,
                        "pause_seconds": paused_seconds,
                        "pause_credit_seconds": raw,
                    }
                # Tiny ends_at wobble while paused — ignore as grant.
                return {
                    **empty,
                    "kind": "pause_credit" if prev.paused else "none",
                    "raw_delta_seconds": raw,
                    "pause_seconds": paused_seconds,
                    "pause_credit_seconds": raw if prev.paused else 0,
                }
            # Tip during pause, or unpause + tip: credit the restore, grant the rem rise.
            credit = max(0, raw - buy)
            return {
                "kind": "grant",
                "granted_seconds": buy,
                "raw_delta_seconds": raw,
                "pause_seconds": paused_seconds,
                "pause_credit_seconds": credit,
            }

        # No feed_seconds: conservative on unpause (avoid ghost mega-grants).
        if prev.paused and not cur.paused and raw > pause_tolerance:
            pause_dur = None
            if prev.paused_at is not None:
                pause_dur = abs(int((cur.observed_at - prev.paused_at).total_seconds()))
            if pause_dur is not None and abs(raw - pause_dur) <= max(pause_tolerance, pause_dur // 20):
                return {
                    "kind": "pause_credit",
                    "granted_seconds": 0,
                    "raw_delta_seconds": raw,
                    "pause_seconds": paused_seconds,
                    "pause_credit_seconds": raw,
                }
            # Unknown rem: still treat large unpause jumps as pause credit.
            return {
                "kind": "pause_credit",
                "granted_seconds": 0,
                "raw_delta_seconds": raw,
                "pause_seconds": paused_seconds,
                "pause_credit_seconds": raw,
            }

    # --- Running (no pause): ends_at increase is a grant ---------------------
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
