"""Unit tests for SSE event → expected seconds / labels (no Mongo)."""
from __future__ import annotations

from app.services.attribution import (
    cheer_seconds,
    detect_gift_count,
    detect_sub_tier,
    event_label,
    expected_seconds,
)

RULES = {
    "tip": {"each": 1, "unit": "minor_units", "seconds": 60, "currency": "BRL"},
    "twitch": {
        "bit": {"each": 100, "unit": "bits", "seconds": 300},
        "prime_sub": {"unit": "subscription", "seconds": 30},
        "tier_1_sub": {"unit": "subscription", "seconds": 60},
        "tier_2_sub": {"unit": "subscription", "seconds": 120},
        "tier_3_sub": {"unit": "subscription", "seconds": 180},
    },
}


def test_tier_from_system_message():
    assert detect_sub_tier({"system_message": "Usuário subscribed at Tier 1."}) == 1
    assert detect_sub_tier({"system_message": "Usuário subscribed at Tier 3."}) == 3
    assert detect_sub_tier({"system_message": "Usuário used Prime."}) == "prime"


def test_tier_from_tags():
    assert detect_sub_tier({"tags": {"msg-param-sub-plan": "2000"}}) == 2
    assert detect_sub_tier({"tags": {"msg-param-sub-plan": "Prime"}}) == "prime"


def test_mystery_gift_count():
    assert detect_gift_count({"system_message": "Usuário is gifting 5 Tier 1 Subs."}) == 5
    assert detect_gift_count({"tags": {"msg-param-mass-gift-count": "10"}}) == 10


def test_expected_seconds_sub_gift_cheer():
    assert expected_seconds("twitch.sub", {"system_message": "Tier 1"}, RULES) == 60
    assert expected_seconds("twitch.subgift", {"system_message": "Tier 2"}, RULES) == 120
    assert (
        expected_seconds(
            "twitch.submysterygift",
            {"system_message": "gifting 5 Tier 1 Subs"},
            RULES,
        )
        == 300
    )
    assert expected_seconds("twitch.cheer", {"bits": 100}, RULES) == 300
    assert expected_seconds("twitch.cheer", {"bits": 250}, RULES) == 600
    assert cheer_seconds(RULES, 50) == 0


def test_pixie_nested_amount_product_peg():
    payload = {
        "type": "transaction.confirmed",
        "data": {
            "transaction": {
                "amount": {"currency": "BRL", "minor_units": 100},
            }
        },
    }
    # R$1.00 → 60s (product peg), not feed-literal tip.each=1
    assert expected_seconds("pixie.transaction.confirmed", payload, RULES) == 60
    assert "Pix R$" in event_label("pixie.transaction.confirmed", payload, RULES)


def test_labels():
    assert "Tier 1" in event_label("twitch.sub", {"system_message": "Tier 1"}, RULES)
    assert "Bits (100)" == event_label("twitch.cheer", {"bits": 100}, RULES)
    assert "×5" in event_label(
        "twitch.submysterygift",
        {"system_message": "gifting 5 Tier 1 Subs"},
        RULES,
    )
