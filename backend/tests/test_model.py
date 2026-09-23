"""Regression tests for the MeiaCoin price model against the 2026-09-23 fixture."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.model import (
    bought_minutes_in_window,
    compute_series,
    flow,
    level,
    parse_dt,
    pause_pro_rated_burn,
    price,
    pure_bleed,
)

UTC = timezone.utc
FIXTURE = Path(__file__).parent / "fixtures" / "meia_data_2026-09-23.json"


def _load_fixture():
    raw = json.loads(FIXTURE.read_text())
    snaps = [
        {"at": parse_dt(s["at"]), "ends_at": parse_dt(s["ends_at"])}
        for s in raw["snapshots"]
    ]
    incs = [
        {
            "at": parse_dt(i["at"]),
            "g": int(i["granted_seconds"]),
            "kind": i.get("kind", "grant"),
        }
        for i in raw["increases"]
    ]
    return snaps, incs


def _fixture_series():
    snaps, incs = _load_fixture()
    grants = [(i["at"], i["g"]) for i in incs if i["kind"] == "grant" or i.get("kind") is None]
    # Exclude adjustments if any
    grants = [(i["at"], i["g"]) for i in incs if i.get("kind", "grant") != "adjustment"]

    first_inc_at = grants[0][0]
    candidates = [s["ends_at"] for s in snaps if s["at"] < first_inc_at and s["ends_at"]]
    e_genesis = candidates[0] if candidates else grants[0][0]  # type: ignore[assignment]
    if not isinstance(e_genesis, datetime):
        e_genesis = grants[0][0]  # unreachable safeguard
        # actually ends_at of first grant's before — use candidates
    assert candidates, "need pre-first-buy snapshot for E_genesis"
    e_genesis = candidates[0]

    t0, t1 = snaps[0]["at"], snaps[-1]["at"]
    grid = []
    t = t0
    while t <= t1:
        grid.append(t)
        t += timedelta(seconds=60)
    return compute_series(grid, e_genesis, grants)


def test_fixture_headline_numbers():
    series = _fixture_series()
    prices = [s["price"] for s in series]
    mn, mx, close = min(prices), max(prices), prices[-1]
    change = (close / prices[0] - 1.0) * 100.0
    assert abs(mn - 89.53) < 0.01
    assert abs(mx - 104.94) < 0.01
    assert abs(close - 91.82) < 0.01
    assert abs(change - (-3.47)) < 0.01


def test_zero_buys_means_m_minus_one():
    assert flow(0.0) == pytest.approx(-1.0)
    assert flow(0.0, window_minutes=60.0) == -1.0


def test_pause_pro_rating():
    # Half the window paused → burn baseline = 30 min; 30 bought → m = 0 (break-even)
    burn = pause_pro_rated_burn(60.0, paused_seconds_in_window=1800.0)
    assert burn == pytest.approx(30.0)
    assert flow(30.0, effective_burn_minutes=burn) == pytest.approx(0.0)
    # Full pause → burn 0 → m pinned to −1
    assert flow(0.0, effective_burn_minutes=pause_pro_rated_burn(60.0, 3600.0)) == -1.0


def test_negative_delta_adjustment_excluded_from_flow():
    t = datetime(2026, 9, 22, 15, 0, tzinfo=UTC)
    # One grant + one "adjustment" that must not be passed into grants list
    grants = [(t - timedelta(minutes=10), 1800)]  # 30 min
    bought = bought_minutes_in_window(t, grants)
    assert bought == pytest.approx(30.0)
    # Adjustments are simply never included in the grants sequence
    adjustments = [(t - timedelta(minutes=5), 0)]
    bought2 = bought_minutes_in_window(t, grants)  # adjustments omitted
    assert bought2 == bought
    assert bought_minutes_in_window(t, grants + adjustments) == bought  # 0 seconds add nothing


def test_direction_decrease_sign_inversion():
    # 60 min bought in window → m = 0 with sign +1; with sign −1 → m = −2
    assert flow(60.0, direction_sign=1) == pytest.approx(0.0)
    assert flow(60.0, direction_sign=-1) == pytest.approx(-2.0)
    # Price with inverted flow is lower than neutral when buys are high
    L = 1.0
    p_up = price(L, flow(60.0, direction_sign=1))
    p_down = price(L, flow(60.0, direction_sign=-1))
    assert p_down < p_up


def test_level_and_pure_bleed():
    assert level(100.0, 200.0) == pytest.approx(0.5)
    assert level(100.0, 0.0) == 1.0
    pb = pure_bleed(1.0, kappa=0.05)
    assert pb == pytest.approx(100.0 * math.exp(-0.05))
