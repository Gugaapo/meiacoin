"""Correlate Twitch/Pixie SSE events with timer grants using feed rules."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import get_settings
from app.database import get_db
from app.services.clips import clip_url_from_feed_docs
from app.services.math_ingest import parse_dt

logger = logging.getLogger(__name__)

UTC = timezone.utc

TIER_KEYS = {
    "prime": "prime_sub",
    1: "tier_1_sub",
    2: "tier_2_sub",
    3: "tier_3_sub",
}

LABELS = {
    "twitch.sub": "Sub Twitch",
    "twitch.resub": "Resub Twitch",
    "twitch.subgift": "Gift Twitch",
    "twitch.submysterygift": "Mystery gift Twitch",
    "twitch.cheer": "Bits",
    "pixie.transaction.confirmed": "Pix (Pixie)",
}


def _as_utc(dt: Any) -> datetime | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        return parse_dt(dt)
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return None


def _tags(payload: dict[str, Any]) -> dict[str, Any]:
    tags = payload.get("tags")
    return tags if isinstance(tags, dict) else {}


def detect_sub_tier(payload: dict[str, Any]) -> str | int:
    """Return 'prime' or 1/2/3."""
    tags = _tags(payload)
    plan = str(tags.get("msg-param-sub-plan") or tags.get("msg_param_sub_plan") or "").strip()
    if plan.lower() == "prime":
        return "prime"
    if plan in ("1000", "1"):
        return 1
    if plan in ("2000", "2"):
        return 2
    if plan in ("3000", "3"):
        return 3

    text = " ".join(
        str(x)
        for x in (
            payload.get("system_message"),
            payload.get("message"),
            payload.get("type"),
        )
        if x
    )
    if re.search(r"\bprime\b", text, re.I):
        return "prime"
    m = re.search(r"tier\s*([123])", text, re.I)
    if m:
        return int(m.group(1))
    return 1


def detect_gift_count(payload: dict[str, Any]) -> int:
    tags = _tags(payload)
    for key in ("msg-param-mass-gift-count", "msg_param_mass_gift_count", "msg-param-sender-count"):
        raw = tags.get(key)
        if raw is not None:
            try:
                n = int(raw)
                if n > 0:
                    return n
            except (TypeError, ValueError):
                pass
    text = str(payload.get("system_message") or "")
    m = re.search(r"gifting\s+(\d+)", text, re.I)
    if m:
        return max(1, int(m.group(1)))
    m = re.search(r"(\d+)\s+tier", text, re.I)
    if m:
        return max(1, int(m.group(1)))
    return 1


def tier_seconds(rules: dict[str, Any], tier: str | int) -> int | None:
    twitch = (rules or {}).get("twitch") or {}
    key = TIER_KEYS.get(tier) or TIER_KEYS.get(1)
    item = twitch.get(key) or {}
    try:
        secs = int(item.get("seconds"))
    except (TypeError, ValueError):
        return None
    return secs if secs > 0 else None


def cheer_seconds(rules: dict[str, Any], bits: int) -> int | None:
    twitch = (rules or {}).get("twitch") or {}
    bit = twitch.get("bit") or {}
    try:
        each = int(bit.get("each") or 0)
        secs = int(bit.get("seconds") or 0)
    except (TypeError, ValueError):
        return None
    if each <= 0 or secs <= 0 or bits <= 0:
        return None
    return (bits // each) * secs


# Product peg: R$1 = +1 minute. Feed tip.each=1 is ambiguous for matching.
TIP_SECONDS_PER_REAL = 60


def extract_pixie_minor_units(payload: dict[str, Any]) -> int | None:
    candidates: list[Any] = [
        payload.get("amount_minor_units"),
        payload.get("amount"),
    ]
    data = payload.get("data")
    if isinstance(data, dict):
        txn = data.get("transaction")
        if isinstance(txn, dict):
            amt = txn.get("amount")
            if isinstance(amt, dict):
                candidates.append(amt.get("minor_units"))
            else:
                candidates.append(amt)
            candidates.append(txn.get("amount_minor_units"))
        amt = data.get("amount")
        if isinstance(amt, dict):
            candidates.append(amt.get("minor_units"))
        else:
            candidates.append(amt)
    for raw in candidates:
        if raw is None:
            continue
        if isinstance(raw, dict):
            raw = raw.get("minor_units")
            if raw is None:
                continue
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n
    return None


def tip_seconds_for_match(amount_minor: int | None) -> int | None:
    if amount_minor is None or amount_minor <= 0:
        return None
    return (amount_minor // 100) * TIP_SECONDS_PER_REAL


def expected_seconds(event_type: str, payload: dict[str, Any], rules: dict[str, Any]) -> int | None:
    if event_type == "twitch.cheer":
        try:
            bits = int(payload.get("bits") or 0)
        except (TypeError, ValueError):
            bits = 0
        return cheer_seconds(rules, bits)

    if event_type in ("twitch.sub", "twitch.resub", "twitch.subgift"):
        return tier_seconds(rules, detect_sub_tier(payload))

    if event_type == "twitch.submysterygift":
        per = tier_seconds(rules, detect_sub_tier(payload))
        if per is None:
            return None
        return per * detect_gift_count(payload)

    if event_type.startswith("pixie."):
        return tip_seconds_for_match(extract_pixie_minor_units(payload))

    return None


def event_label(event_type: str, payload: dict[str, Any], rules: dict[str, Any]) -> str:
    base = LABELS.get(event_type)
    if event_type == "twitch.cheer":
        bits = payload.get("bits")
        return f"Bits ({bits})" if bits is not None else "Bits"
    if event_type in ("twitch.sub", "twitch.resub", "twitch.subgift", "twitch.submysterygift"):
        tier = detect_sub_tier(payload)
        tier_s = "Prime" if tier == "prime" else f"Tier {tier}"
        if event_type == "twitch.submysterygift":
            n = detect_gift_count(payload)
            return f"Mystery gift ×{n} ({tier_s})"
        if event_type == "twitch.subgift":
            return f"Gift {tier_s}"
        if event_type == "twitch.resub":
            return f"Resub {tier_s}"
        return f"Sub {tier_s}"
    if event_type.startswith("pixie."):
        minor = extract_pixie_minor_units(payload)
        if minor is not None:
            return f"Pix R$ {minor / 100.0:.2f}".replace(".", ",")
        return base or "Pixie"
    return base or event_type


def donor_name(payload: dict[str, Any]) -> str | None:
    for key in ("display_name", "displayName", "username", "user_name", "user_login"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    data = payload.get("data")
    if isinstance(data, dict):
        for nest_key in ("transaction", "customer", "payer", "from"):
            nest = data.get(nest_key)
            if not isinstance(nest, dict):
                continue
            for key in (
                "display_name",
                "displayName",
                "username",
                "user_name",
                "name",
                "full_name",
            ):
                val = nest.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
    return None


def classify_code(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == "twitch.cheer":
        return "bits"
    if event_type == "twitch.submysterygift":
        return "mystery_gift"
    if event_type == "twitch.subgift":
        return "gift"
    if event_type == "twitch.resub":
        return "resub"
    if event_type == "twitch.sub":
        tier = detect_sub_tier(payload)
        return "prime" if tier == "prime" else f"tier{tier}"
    if event_type.startswith("pixie."):
        return "pix"
    return event_type.replace(".", "_")


def event_occurred_at(payload: dict[str, Any] | None, fallback: datetime) -> datetime:
    if not payload:
        return fallback
    for key in ("occurred_at", "sent_at", "achieved_at", "observed_at"):
        dt = _as_utc(payload.get(key))
        if dt:
            return dt
    return fallback


def stable_event_key(event_type: str, payload: dict[str, Any] | None, sse_id: str | None) -> str:
    if payload:
        for key in ("event_id", "message_id", "id"):
            val = payload.get(key)
            if val is not None and str(val).strip():
                return f"{event_type}:{val}"
    if sse_id:
        return f"{event_type}:sse:{sse_id}"
    at = event_occurred_at(payload, datetime.now(UTC)).isoformat()
    return f"{event_type}:at:{at}"


async def latest_rules() -> dict[str, Any]:
    db = get_db()
    sample = await db.feed_samples.find_one(sort=[("at", -1)])
    rules = (sample or {}).get("rules")
    return rules if isinstance(rules, dict) else {}


async def store_feed_event(
    event_type: str,
    payload: dict[str, Any] | None,
    *,
    sse_id: str | None = None,
) -> dict[str, Any] | None:
    """Persist a Twitch/Pixie (or timer meta) event. Returns stored doc for matchable types."""
    if event_type in ("handshake",) or not event_type:
        return None

    db = get_db()
    now = datetime.now(UTC)
    body = payload if isinstance(payload, dict) else {}
    at = event_occurred_at(body, now)
    key = stable_event_key(event_type, body, sse_id)
    rules = await latest_rules()
    expected = expected_seconds(event_type, body, rules) if event_type.startswith(("twitch.", "pixie.")) else None

    doc = {
        "key": key,
        "type": event_type,
        "sse_id": sse_id,
        "at": at,
        "received_at": now,
        "payload": body,
        "expected_seconds": expected,
        "user_name": donor_name(body),
        "label": event_label(event_type, body, rules) if event_type.startswith(("twitch.", "pixie.")) else event_type,
        "class_code": classify_code(event_type, body) if event_type.startswith(("twitch.", "pixie.")) else None,
        "matched_grant_at": None,
    }
    await db.feed_events.update_one(
        {"key": key},
        {
            "$set": {k: v for k, v in doc.items() if k != "matched_grant_at"},
            "$setOnInsert": {"matched_grant_at": None},
        },
        upsert=True,
    )
    stored = await db.feed_events.find_one({"key": key})
    if event_type.startswith(("twitch.", "pixie.")):
        await try_match_unattributed_grants()
    return stored


async def attribute_grant(
    *,
    grant_at: datetime,
    granted_seconds: int,
    precision_seconds: int,
) -> dict[str, Any] | None:
    """Find unmatched feed events that explain this grant; stamp both sides."""
    if granted_seconds <= 0:
        return None
    settings = get_settings()
    window = max(
        int(settings.attribution_window_seconds),
        int(precision_seconds) + 15,
        int(settings.timer_poll_seconds) + 30,
    )
    db = get_db()
    start = grant_at - timedelta(seconds=window)
    end = grant_at + timedelta(seconds=min(30, window // 2))

    cursor = db.feed_events.find(
        {
            "matched_grant_at": None,
            "expected_seconds": {"$gt": 0},
            "type": {"$regex": r"^(twitch\.|pixie\.)"},
            "at": {"$gte": start, "$lte": end},
        }
    ).sort("at", 1)
    candidates = [doc async for doc in cursor]
    if not candidates:
        return None

    # Prefer exact single-event match.
    exact = [
        c
        for c in candidates
        if abs(int(c.get("expected_seconds") or 0) - granted_seconds) <= 5
    ]
    chosen: list[dict[str, Any]]
    if exact:
        # Closest in time
        exact.sort(key=lambda c: abs((_as_utc(c["at"]) or grant_at) - grant_at))
        chosen = [exact[0]]
    else:
        # Greedy pack by time order summing to grant
        chosen = []
        total = 0
        for c in candidates:
            secs = int(c.get("expected_seconds") or 0)
            if total + secs > granted_seconds + 5:
                continue
            chosen.append(c)
            total += secs
            if abs(total - granted_seconds) <= 5:
                break
        if abs(sum(int(c.get("expected_seconds") or 0) for c in chosen) - granted_seconds) > 5:
            return None

    if not chosen:
        return None

    names = [c.get("user_name") for c in chosen if c.get("user_name")]
    labels = [c.get("label") for c in chosen if c.get("label")]
    types = [c.get("type") for c in chosen]
    keys = [c.get("key") for c in chosen]

    if len(chosen) == 1:
        label = labels[0] if labels else chosen[0].get("type")
        class_code = chosen[0].get("class_code")
        user_name = names[0] if names else None
    else:
        label = " + ".join(labels) if labels else f"{len(chosen)} eventos"
        class_code = "bundle"
        user_name = names[0] if len(set(names)) == 1 else (", ".join(names) if names else None)

    attribution: dict[str, Any] = {
        "source": types[0] if len(types) == 1 else "bundle",
        "sources": types,
        "feed_event_keys": keys,
        "label": label,
        "class_code": class_code,
        "user_name": user_name,
        "attributed": True,
    }
    clip_url = clip_url_from_feed_docs(chosen)
    if clip_url:
        attribution["clip_url"] = clip_url

    await db.events.update_one(
        {"at": grant_at, "kind": "grant", "granted_seconds": granted_seconds},
        {"$set": {"attribution": attribution}},
    )
    for c in chosen:
        await db.feed_events.update_one(
            {"_id": c["_id"]},
            {"$set": {"matched_grant_at": grant_at}},
        )
    logger.info(
        "Attributed grant %ss at %s → %s (%s)",
        granted_seconds,
        grant_at.isoformat(),
        label,
        user_name,
    )
    return attribution


async def try_match_unattributed_grants(*, lookback_hours: float | None = None) -> None:
    """When a Twitch event arrives after a grant, backfill attribution."""
    db = get_db()
    settings = get_settings()
    if lookback_hours is None:
        window = int(settings.attribution_window_seconds)
        since = datetime.now(UTC) - timedelta(seconds=window * 2)
    else:
        since = datetime.now(UTC) - timedelta(hours=max(0.1, lookback_hours))
    cursor = db.events.find(
        {
            "kind": "grant",
            "granted_seconds": {"$gt": 0},
            "at": {"$gte": since},
            "$or": [
                {"attribution": {"$exists": False}},
                {"attribution.attributed": {"$ne": True}},
            ],
        }
    ).sort("at", -1)
    async for grant in cursor:
        at = _as_utc(grant.get("at"))
        if at is None:
            continue
        await attribute_grant(
            grant_at=at,
            granted_seconds=int(grant.get("granted_seconds") or 0),
            precision_seconds=int(grant.get("precision_seconds") or settings.timer_poll_seconds),
        )


async def refresh_feed_event_expectations(*, rematch_hours: float = 48.0) -> int:
    db = get_db()
    rules = await latest_rules()
    updated = 0
    cursor = db.feed_events.find(
        {
            "type": {"$regex": r"^(twitch\.|pixie\.)"},
            "matched_grant_at": None,
        }
    )
    async for doc in cursor:
        body = doc.get("payload") if isinstance(doc.get("payload"), dict) else {}
        event_type = str(doc.get("type") or "")
        fields = {
            "expected_seconds": expected_seconds(event_type, body, rules),
            "label": event_label(event_type, body, rules),
            "class_code": classify_code(event_type, body),
            "user_name": donor_name(body),
        }
        if any(doc.get(k) != v for k, v in fields.items()):
            await db.feed_events.update_one({"_id": doc["_id"]}, {"$set": fields})
            updated += 1
    await try_match_unattributed_grants(lookback_hours=rematch_hours)
    logger.info("Refreshed %s feed_events expectations; rematch lookback=%.1fh", updated, rematch_hours)
    return updated
