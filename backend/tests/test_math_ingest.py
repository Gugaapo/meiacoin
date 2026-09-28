"""Tests for timer increment classification (pause / unpause / grants)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.math_ingest import Observation, derive_increment

UTC = timezone.utc
T0 = datetime(2026, 9, 28, 15, 54, 0, tzinfo=UTC)


def _obs(
    *,
    paused: bool,
    ends_at: datetime,
    observed_at: datetime,
    feed_seconds: int | None,
    paused_at: datetime | None = None,
) -> Observation:
    return Observation(
        state="running",
        direction="increase",
        locked=False,
        paused=paused,
        ends_at=ends_at,
        paused_at=paused_at,
        observed_at=observed_at,
        status=None,
        feed_seconds=feed_seconds,
        feed_value=None,
        rules={},
        fetched_at=observed_at,
    )


def test_unpause_ends_at_bump_is_pause_credit_not_grant():
    """Long pause then unpause: ends_at jumps ~35h, remaining stays flat."""
    prev = _obs(
        paused=True,
        ends_at=datetime(2026, 10, 9, 20, 18, 57, tzinfo=UTC),
        observed_at=T0,
        feed_seconds=1_091_314,
    )
    cur = _obs(
        paused=False,
        ends_at=datetime(2026, 10, 11, 7, 2, 45, tzinfo=UTC),
        observed_at=T0 + timedelta(seconds=13),
        feed_seconds=1_091_311,
    )
    inc = derive_increment(prev, cur)
    assert inc["kind"] == "pause_credit"
    assert inc["granted_seconds"] == 0
    assert inc["raw_delta_seconds"] == 125_028
    assert inc["pause_credit_seconds"] == 125_028


def test_unpause_plus_real_buy_keeps_buy():
    prev = _obs(
        paused=True,
        ends_at=datetime(2026, 10, 9, 20, 0, 0, tzinfo=UTC),
        observed_at=T0,
        feed_seconds=1_000_000,
    )
    # Unpause restore + 600s buy → remaining +600, ends_at +3600+600
    cur = _obs(
        paused=False,
        ends_at=datetime(2026, 10, 9, 21, 10, 0, tzinfo=UTC),
        observed_at=T0 + timedelta(seconds=15),
        feed_seconds=1_000_600,
    )
    inc = derive_increment(prev, cur)
    assert inc["kind"] == "grant"
    assert inc["granted_seconds"] == 600
    assert inc["pause_credit_seconds"] == 3600


def test_tip_while_still_paused_counts_as_grant():
    prev = _obs(
        paused=True,
        ends_at=datetime(2026, 10, 9, 20, 0, 0, tzinfo=UTC),
        observed_at=T0,
        feed_seconds=1_000_000,
    )
    cur = _obs(
        paused=True,
        ends_at=datetime(2026, 10, 9, 20, 10, 0, tzinfo=UTC),
        observed_at=T0 + timedelta(seconds=15),
        feed_seconds=1_000_600,
    )
    inc = derive_increment(prev, cur)
    assert inc["kind"] == "grant"
    assert inc["granted_seconds"] == 600
    assert inc["pause_credit_seconds"] == 0


def test_unpause_without_feed_seconds_is_pause_credit():
    paused_at = T0 - timedelta(hours=2)
    prev = _obs(
        paused=True,
        ends_at=datetime(2026, 10, 9, 20, 0, 0, tzinfo=UTC),
        observed_at=T0,
        feed_seconds=None,
        paused_at=paused_at,
    )
    cur = _obs(
        paused=False,
        ends_at=datetime(2026, 10, 9, 22, 0, 0, tzinfo=UTC),
        observed_at=T0 + timedelta(seconds=15),
        feed_seconds=None,
    )
    inc = derive_increment(prev, cur)
    assert inc["kind"] == "pause_credit"
    assert inc["granted_seconds"] == 0
    assert inc["raw_delta_seconds"] == 7200


def test_normal_grant_while_running():
    prev = _obs(
        paused=False,
        ends_at=datetime(2026, 10, 9, 20, 0, 0, tzinfo=UTC),
        observed_at=T0,
        feed_seconds=500_000,
    )
    cur = _obs(
        paused=False,
        ends_at=datetime(2026, 10, 9, 20, 10, 0, tzinfo=UTC),
        observed_at=T0 + timedelta(seconds=15),
        feed_seconds=500_585,  # +600 buy minus ~15s burn
    )
    inc = derive_increment(prev, cur)
    assert inc["kind"] == "grant"
    assert inc["granted_seconds"] == 600


def test_no_movement():
    prev = _obs(
        paused=True,
        ends_at=datetime(2026, 10, 9, 20, 0, 0, tzinfo=UTC),
        observed_at=T0,
        feed_seconds=500_000,
    )
    cur = _obs(
        paused=True,
        ends_at=datetime(2026, 10, 9, 20, 0, 0, tzinfo=UTC),
        observed_at=T0 + timedelta(seconds=15),
        feed_seconds=500_000,
    )
    assert derive_increment(prev, cur)["kind"] == "none"
