"""Pure MeiaCoin price model. No I/O.

P(t) = 100 · L(t)^α · e^(κ · m(t))

  L = R / R_ref          peak-anchored level (fraction of peak life left)
  m = sign · (bought_eff / burn_baseline) − 1

Bought flow uses hold + exponential fade: full weight for `hold`, then
e^(-(age−hold)/τ) so spikes cool over ~a day instead of cliffing at 60m.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence


UTC = timezone.utc

DEFAULT_ALPHA = 1.0
DEFAULT_KAPPA = 0.05
DEFAULT_WINDOW_MINUTES = 60.0
DEFAULT_HOLD_MINUTES = 60.0
DEFAULT_TAU_MINUTES = 480.0  # 8h → soft tail ~1 day
DEFAULT_BASE = 100.0


def remaining_seconds(ends_at: datetime, now: datetime) -> float:
    """R(t) = ends_at − now. Always derive from ends_at, never feed seconds/value."""
    return (ends_at - now).total_seconds()


def level(R: float, R_ref: float) -> float:
    """Peak-anchored level L ∈ (0, 1]."""
    if R_ref <= 0:
        return 1.0
    return R / R_ref


def flow(
    bought_minutes: float,
    *,
    window_minutes: float = DEFAULT_WINDOW_MINUTES,
    effective_burn_minutes: float | None = None,
    direction_sign: int = 1,
) -> float:
    """Normalised net flow m. Zero buys ⇒ m = −1 (pure bleed).

    When the window contains paused time, pass the pro-rated burn baseline as
    effective_burn_minutes (active minutes of clock in the window).
    direction_sign = −1 when feed direction is decrease (inverts flow).
    """
    burn = effective_burn_minutes if effective_burn_minutes is not None else window_minutes
    if burn <= 0:
        return -1.0
    return direction_sign * (bought_minutes / burn) - 1.0


def price(
    L: float,
    m: float,
    *,
    alpha: float = DEFAULT_ALPHA,
    kappa: float = DEFAULT_KAPPA,
    base: float = DEFAULT_BASE,
) -> float:
    return base * (L ** alpha) * math.exp(kappa * m)


def pure_bleed(
    L: float,
    *,
    kappa: float = DEFAULT_KAPPA,
    base: float = DEFAULT_BASE,
) -> float:
    """Dotted chart path: 100 · L · e^(−κ) — if nobody ever buys again."""
    return base * L * math.exp(-kappa)


def grant_weight(
    age_seconds: float,
    *,
    hold_seconds: float,
    tau_seconds: float,
) -> float:
    """Full credit through hold, then exponential fade."""
    if age_seconds < 0:
        return 0.0
    if age_seconds <= hold_seconds:
        return 1.0
    if tau_seconds <= 0:
        return 0.0
    return math.exp(-(age_seconds - hold_seconds) / tau_seconds)


def bought_minutes_in_window(
    t: datetime,
    grants: Sequence[tuple[datetime, int]],
    *,
    window_seconds: int = 3600,
) -> float:
    """Sum granted_seconds where (t − W) < at ≤ t, as minutes. Hard cutoff (legacy)."""
    start = t - timedelta(seconds=window_seconds)
    total = 0
    for at, granted in grants:
        if start < at <= t:
            total += granted
    return total / 60.0


def bought_minutes_soft(
    t: datetime,
    grants: Sequence[tuple[datetime, int]],
    *,
    hold_seconds: float = DEFAULT_HOLD_MINUTES * 60.0,
    tau_seconds: float = DEFAULT_TAU_MINUTES * 60.0,
    max_age_seconds: float | None = None,
) -> float:
    """Weighted bought minutes: hold at 1.0, then e^(-(age−hold)/τ)."""
    if max_age_seconds is None:
        max_age_seconds = hold_seconds + 5.0 * max(tau_seconds, 1.0)
    total = 0.0
    for at, granted in grants:
        if at > t or granted <= 0:
            continue
        age = (t - at).total_seconds()
        if age > max_age_seconds:
            continue
        w = grant_weight(age, hold_seconds=hold_seconds, tau_seconds=tau_seconds)
        if w <= 0:
            continue
        total += (granted / 60.0) * w
    return total


def pause_pro_rated_burn(
    window_minutes: float,
    paused_seconds_in_window: float,
    *,
    window_seconds: float = 3600.0,
) -> float:
    """Effective burn baseline after removing paused time from the window."""
    active = max(0.0, window_seconds - paused_seconds_in_window)
    return window_minutes * (active / window_seconds)


def ends_at_at(
    t: datetime,
    e_genesis: datetime,
    grants: Sequence[tuple[datetime, int]],
) -> datetime:
    """Reconstruct ends_at step function from genesis + grants with at ≤ t."""
    total = sum(g for at, g in grants if at <= t)
    return e_genesis + timedelta(seconds=total)


def compute_series(
    grid: Iterable[datetime],
    e_genesis: datetime,
    grants: Sequence[tuple[datetime, int]],
    *,
    alpha: float = DEFAULT_ALPHA,
    kappa: float = DEFAULT_KAPPA,
    window_seconds: int = 3600,
    hold_seconds: float = DEFAULT_HOLD_MINUTES * 60.0,
    tau_seconds: float = DEFAULT_TAU_MINUTES * 60.0,
    direction_sign: int = 1,
) -> list[dict]:
    """1-step price series with peak R_ref ratchet. Soft-weighted grants."""
    series: list[dict] = []
    r_max = 0.0
    window_minutes = window_seconds / 60.0
    for t in grid:
        ends = ends_at_at(t, e_genesis, grants)
        R = remaining_seconds(ends, t)
        r_max = max(r_max, R)
        bought = bought_minutes_soft(
            t,
            grants,
            hold_seconds=hold_seconds,
            tau_seconds=tau_seconds,
        )
        L = level(R, r_max)
        m = flow(bought, window_minutes=window_minutes, direction_sign=direction_sign)
        p = price(L, m, alpha=alpha, kappa=kappa)
        series.append(
            {
                "t": t,
                "R": R,
                "Rmax": r_max,
                "L": L,
                "m": m,
                "bought_60m": bought,
                "price": p,
                "pure_bleed": pure_bleed(L, kappa=kappa),
            }
        )
    return series


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)
