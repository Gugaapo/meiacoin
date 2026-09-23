"""Derive MeiaCoin market views from events + live observation. No bar storage."""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import get_settings
from app.database import get_db
from app.model import (
    bought_minutes_in_window,
    compute_series,
    ends_at_at,
    flow,
    level,
    parse_dt,
    price,
    pure_bleed,
    remaining_seconds,
)

logger = logging.getLogger(__name__)

UTC = timezone.utc

TF_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

# Likely class by exact granted_seconds (rules from feed).
LIKELY_CLASS = {
    1800: ("kick_sub", "provável sub Kick (1800s)"),
    300: ("bits_100", "prováveis 100 bits (300s)"),
    180: ("tier3_or_yt", "provável Tier 3 / membro YouTube (180s)"),
    120: ("tier2", "provável Tier 2 (120s)"),
    60: ("pix", "provável Pix (60s)"),
    30: ("prime", "provável Prime (30s)"),
}

TIP_LABEL = "equivalente em Pix a R$1 = 1 min"


def _as_utc(dt: Any) -> datetime | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        return parse_dt(dt)
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _now() -> datetime:
    return datetime.now(UTC)


async def load_health() -> dict:
    db = get_db()
    return await db.health.find_one({"_id": "current"}) or {}


async def load_grants() -> list[tuple[datetime, int, dict]]:
    """Return (at, granted_seconds, raw_doc) for kind=grant only."""
    db = get_db()
    out = []
    cursor = db.events.find({"kind": "grant", "granted_seconds": {"$gt": 0}}).sort("at", 1)
    async for doc in cursor:
        at = _as_utc(doc.get("at"))
        if at is None:
            continue
        out.append((at, int(doc["granted_seconds"]), doc))
    return out


async def load_latest_sample() -> dict | None:
    db = get_db()
    return await db.feed_samples.find_one(sort=[("at", -1)])


def _stale_flags(health: dict, settings) -> tuple[bool, bool]:
    last_ok = _as_utc(health.get("last_ok"))
    if last_ok is None:
        return True, True
    age = (_now() - last_ok).total_seconds()
    degraded = age > settings.timer_degraded_seconds
    stale = age > settings.timer_stale_seconds
    return stale, degraded


def classify_grant(seconds: int) -> tuple[str, str]:
    if seconds in LIKELY_CLASS:
        return LIKELY_CLASS[seconds]
    # Nearest known size for odd merges
    for known in (1800, 300, 180, 120, 60, 30):
        if abs(seconds - known) <= 5:
            return LIKELY_CLASS[known]
    mins = seconds / 60.0
    return ("unknown", f"provável ~{mins:.1f} min Pix/fusão ({seconds}s)")


async def current_state() -> dict[str, Any]:
    """Live L, m, P, remaining from latest sample + grants."""
    settings = get_settings()
    health = await load_health()
    grants_full = await load_grants()
    grants = [(a, g) for a, g, _ in grants_full]
    sample = await load_latest_sample()
    stale, degraded = _stale_flags(health, settings)

    genesis_at = _as_utc(health.get("genesis_at"))
    e_genesis = _as_utc(health.get("e_genesis"))
    now = _now()

    ends_at = _as_utc(sample.get("ends_at")) if sample else None
    direction = (sample or {}).get("direction") or "increase"
    direction_sign = -1 if direction == "decrease" else 1
    paused = bool((sample or {}).get("paused"))

    if ends_at is None and e_genesis is not None:
        ends_at = ends_at_at(now, e_genesis, grants)

    R = remaining_seconds(ends_at, now) if ends_at else 0.0
    # Peak R_ref: max of current R and historical peak stored / computed
    r_ref = float(health.get("r_ref") or 0.0)
    if R > r_ref:
        r_ref = R
        db = get_db()
        await db.health.update_one({"_id": "current"}, {"$set": {"r_ref": r_ref}}, upsert=True)
    if r_ref <= 0 and e_genesis is not None:
        # First reading: use genesis remaining
        r_ref = max(R, remaining_seconds(e_genesis, genesis_at or now))

    L = level(R, r_ref) if r_ref > 0 else 1.0
    bought = bought_minutes_in_window(now, grants)
    m = flow(
        bought,
        window_minutes=settings.model_window_minutes,
        direction_sign=direction_sign,
    )
    # While paused, clock isn't burning — treat burn baseline as paused-pro-rated approx
    if paused:
        m = flow(bought, window_minutes=settings.model_window_minutes, effective_burn_minutes=0.01)

    p = price(L, m, alpha=settings.model_alpha, kappa=settings.model_kappa)
    return {
        "now": now,
        "price": p,
        "L": L,
        "m": m,
        "R": R,
        "r_ref": r_ref,
        "ends_at": ends_at,
        "e_genesis": e_genesis,
        "genesis_at": genesis_at,
        "grants": grants,
        "grants_full": grants_full,
        "stale": stale,
        "degraded": degraded,
        "paused": paused,
        "direction": direction,
        "sample": sample,
        "health": health,
        "settings": settings,
        "pure_bleed": pure_bleed(L, kappa=settings.model_kappa),
    }


async def build_price_path(step_seconds: int = 60) -> list[dict]:
    """Minute (or coarser) series from genesis → now."""
    state = await current_state()
    settings = state["settings"]
    genesis_at = state["genesis_at"]
    e_genesis = state["e_genesis"]
    if genesis_at is None or e_genesis is None:
        return []

    now = state["now"]
    grants = state["grants"]
    direction_sign = -1 if state["direction"] == "decrease" else 1

    grid = []
    t = genesis_at.replace(microsecond=0)
    # Align to step
    epoch = int(t.timestamp())
    aligned = epoch - (epoch % step_seconds)
    t = datetime.fromtimestamp(aligned, tz=UTC)
    if t < genesis_at:
        t += timedelta(seconds=step_seconds)
    while t <= now:
        grid.append(t)
        t += timedelta(seconds=step_seconds)
    if not grid or grid[-1] < now:
        grid.append(now)

    return compute_series(
        grid,
        e_genesis,
        grants,
        alpha=settings.model_alpha,
        kappa=settings.model_kappa,
        window_seconds=int(settings.model_window_minutes * 60),
        direction_sign=direction_sign,
    )


def _bucket_series(path: list[dict], tf: str, genesis_at: datetime, now: datetime) -> list[dict]:
    step = TF_SECONDS[tf]
    if not path:
        return []
    # For 1m use path as-is (already minute); for coarser, take last point in each bucket
    buckets: dict[int, dict] = {}
    for s in path:
        ts = int(s["t"].timestamp())
        key = ts - (ts % step)
        buckets[key] = s
    # Ensure one bucket per step since genesis for 1h acceptance
    out = []
    start = int(genesis_at.timestamp())
    start = start - (start % step)
    end = int(now.timestamp())
    warming_until = genesis_at + timedelta(seconds=3600)
    t = start
    while t <= end:
        s = buckets.get(t)
        if s is None:
            # find closest earlier
            earlier = [k for k in buckets if k <= t]
            s = buckets[max(earlier)] if earlier else None
        if s is not None:
            bt = datetime.fromtimestamp(t, tz=UTC)
            out.append(
                {
                    "t": _iso(bt),
                    "price": round(s["price"], 4),
                    "L": round(s["L"], 6),
                    "m": round(s["m"], 4),
                    "pure_bleed": round(s["pure_bleed"], 4),
                    "volume_bought_min": round(s["bought_60m"], 2),
                    "volume_burn_min": 60.0 if (bt - genesis_at).total_seconds() >= 3600 else round(
                        max(0.0, (bt - genesis_at).total_seconds() / 60.0), 2
                    ),
                    "warming_up": bt < warming_until,
                }
            )
        t += step
    return out


async def ticker_payload() -> dict:
    state = await current_state()
    settings = state["settings"]
    path = await build_price_path(60)
    prices = [s["price"] for s in path] if path else [state["price"]]
    now = state["now"]
    p = state["price"]

    def price_at_offset(hours: float) -> float | None:
        target = now - timedelta(hours=hours)
        earlier = [s for s in path if s["t"] <= target]
        return earlier[-1]["price"] if earlier else (prices[0] if prices else None)

    p_1h = price_at_offset(1)
    p_24h = price_at_offset(24)
    day = [s for s in path if s["t"] >= now - timedelta(hours=24)] or path
    day_prices = [s["price"] for s in day] or [p]
    ath = max(prices) if prices else p
    change_1h = ((p / p_1h) - 1) * 100 if p_1h else None
    change_24h = ((p / p_24h) - 1) * 100 if p_24h else None
    drawdown = ((ath - p) / ath * 100) if ath else 0.0

    return {
        "model": settings.public_model_constants(),
        "price": round(p, 4),
        "L": round(state["L"], 6),
        "m": round(state["m"], 4),
        "remaining_seconds": int(max(0, state["R"])),
        "change_1h_pct": round(change_1h, 4) if change_1h is not None else None,
        "change_24h_pct": round(change_24h, 4) if change_24h is not None else None,
        "high_24h": round(max(day_prices), 4),
        "low_24h": round(min(day_prices), 4),
        "ath": round(ath, 4),
        "drawdown_pct": round(drawdown, 4),
        "market_cap_seconds": int(max(0, state["R"])),
        "stale": state["stale"],
        "degraded": state["degraded"],
        "genesis_at": _iso(state["genesis_at"]),
        "pure_bleed": round(state["pure_bleed"], 4),
    }


async def series_payload(tf: str = "1h", from_ts: str | None = None, to_ts: str | None = None) -> dict:
    settings = get_settings()
    if tf not in TF_SECONDS:
        tf = "1h"
    state = await current_state()
    path = await build_price_path(60)
    genesis_at = state["genesis_at"] or state["now"]
    bars = _bucket_series(path, tf, genesis_at, state["now"])

    if from_ts:
        fr = parse_dt(from_ts)
        if fr:
            bars = [b for b in bars if parse_dt(b["t"]) and parse_dt(b["t"]) >= fr]
    if to_ts:
        to = parse_dt(to_ts)
        if to:
            bars = [b for b in bars if parse_dt(b["t"]) and parse_dt(b["t"]) <= to]

    markers = []
    for at, secs, doc in state["grants_full"]:
        markers.append(
            {
                "t": _iso(at),
                "granted_seconds": secs,
                "precision_seconds": int(doc.get("precision_seconds") or 0),
            }
        )

    return {
        "model": settings.public_model_constants(),
        "tf": tf,
        "bars": bars,
        "markers": markers,
        "stale": state["stale"],
        "genesis_at": _iso(state["genesis_at"]),
    }


async def trades_payload(limit: int = 100) -> dict:
    settings = get_settings()
    state = await current_state()
    trades = []
    for at, secs, doc in reversed(state["grants_full"][-limit:]):
        cls, label = classify_grant(secs)
        trades.append(
            {
                "at": _iso(at),
                "granted_seconds": secs,
                "granted_minutes": round(secs / 60.0, 2),
                "likely_class": cls,
                "likely_label": label,
                "precision_seconds": int(doc.get("precision_seconds") or 0),
                "tip_equivalent_brl": round(secs / 60.0, 2),
                "tip_equivalent_label": TIP_LABEL,
            }
        )
    return {
        "model": settings.public_model_constants(),
        "trades": trades,
        "stale": state["stale"],
        "label": TIP_LABEL,
    }


async def burn_payload() -> dict:
    settings = get_settings()
    state = await current_state()
    genesis_at = state["genesis_at"]
    now = state["now"]
    grants = state["grants"]
    bought_total = sum(g for _, g in grants) / 60.0
    if genesis_at:
        burned_total = max(0.0, (now - genesis_at).total_seconds() / 60.0)
        elapsed_h = max(1e-9, (now - genesis_at).total_seconds() / 3600.0)
    else:
        burned_total = 0.0
        elapsed_h = 1e-9
    pace = bought_total / elapsed_h  # min/h = R$/h tip-eq
    coverage = (bought_total / burned_total * 100.0) if burned_total > 0 else 0.0
    wall = 60.0
    deficit = wall - pace
    # Runway: if net burn continues at (60 - pace) min/h
    net_burn_per_h = max(0.0, wall - pace)
    runway_eta = None
    if net_burn_per_h > 0 and state["R"] > 0:
        hours_left = (state["R"] / 60.0) / net_burn_per_h
        runway_eta = _iso(now + timedelta(hours=hours_left))

    return {
        "model": settings.public_model_constants(),
        "burn_wall_brl_per_hour": wall,
        "pace_brl_per_hour": round(pace, 2),
        "coverage_pct": round(coverage, 2),
        "deficit_brl_per_hour": round(deficit, 2),
        "minutes_bought_total": round(bought_total, 2),
        "minutes_burned_total": round(burned_total, 2),
        "runway_eta": runway_eta,
        "remaining_seconds": int(max(0, state["R"])),
        "label": TIP_LABEL,
        "stale": state["stale"],
        "genesis_at": _iso(genesis_at),
    }


async def records_payload() -> dict:
    settings = get_settings()
    state = await current_state()
    path = await build_price_path(60)
    genesis_at = state["genesis_at"]
    grants = state["grants"]

    # Hourly aggregation of buys and price
    by_hour: dict[datetime, dict] = {}
    for at, secs in grants:
        h = at.replace(minute=0, second=0, microsecond=0)
        by_hour.setdefault(h, {"bought_min": 0.0, "prices": []})
        by_hour[h]["bought_min"] += secs / 60.0
    for s in path:
        h = s["t"].replace(minute=0, second=0, microsecond=0)
        by_hour.setdefault(h, {"bought_min": 0.0, "prices": []})
        by_hour[h]["prices"].append(s["price"])

    best_hour = None
    worst_hour = None
    biggest_net = 0.0
    for h, data in by_hour.items():
        b = data["bought_min"]
        m = b / 60.0 - 1.0
        avg_p = sum(data["prices"]) / len(data["prices"]) if data["prices"] else None
        net = b - 60.0  # vs burn
        if biggest_net < net:
            biggest_net = net
        entry = {
            "at": _iso(h),
            "m": round(m, 4),
            "price": round(avg_p, 4) if avg_p is not None else None,
            "bought_min": round(b, 2),
        }
        if best_hour is None or b > best_hour["bought_min"]:
            best_hour = entry
        if worst_hour is None or b < worst_hour["bought_min"]:
            worst_hour = entry

    # Longest stretch above break-even (m > 0) and dry spell (bought == 0 for hour)
    hours_sorted = sorted(by_hour.keys())
    longest_above = 0
    cur_above = 0
    longest_dry = 0
    cur_dry = 0
    for h in hours_sorted:
        b = by_hour[h]["bought_min"]
        if b / 60.0 - 1.0 > 0:
            cur_above += 1
            longest_above = max(longest_above, cur_above)
        else:
            cur_above = 0
        if b <= 0:
            cur_dry += 1
            longest_dry = max(longest_dry, cur_dry)
        else:
            cur_dry = 0

    sizes = Counter(secs for _, secs in grants)
    size_distribution = [
        {"seconds": s, "count": c, "likely_label": classify_grant(s)[1]}
        for s, c in sorted(sizes.items(), key=lambda x: -x[1])
    ]

    return {
        "model": settings.public_model_constants(),
        "best_hour": best_hour,
        "worst_hour": worst_hour,
        "biggest_hour_net_min": round(biggest_net, 2),
        "longest_above_breakeven_hours": longest_above,
        "longest_dry_spell_hours": longest_dry,
        "size_distribution": size_distribution,
        "stale": state["stale"],
        "genesis_at": _iso(genesis_at),
        "trades_count": len(grants),
    }
