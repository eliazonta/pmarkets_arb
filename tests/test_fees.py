import math

from pmarket_arb.fees import FeeSchedule


def test_matches_polymarket_worked_example():
    # docs.polymarket.com/trading/fees: crypto rate 0.07, 100 shares @ 0.50 -> $1.75
    fee = FeeSchedule(rate=0.07)
    assert math.isclose(fee.taker_fee(100, 0.50), 1.75, rel_tol=1e-9)
    # @ 0.30 -> 100 * 0.07 * 0.3 * 0.7 = 1.47
    assert math.isclose(fee.taker_fee(100, 0.30), 1.47, rel_tol=1e-9)


def test_fee_peaks_at_half():
    fee = FeeSchedule(rate=0.05)
    at_half = fee.taker_fee(1, 0.50)
    assert fee.taker_fee(1, 0.20) < at_half
    assert fee.taker_fee(1, 0.80) < at_half
    # symmetric around 0.5
    assert math.isclose(fee.taker_fee(1, 0.20), fee.taker_fee(1, 0.80))


def test_disabled_and_zero_rate():
    assert FeeSchedule(rate=0.0).taker_fee(100, 0.5) == 0.0
    assert FeeSchedule(rate=0.05, enabled=False).taker_fee(100, 0.5) == 0.0


def test_from_market_parses_schedule():
    market = {
        "feesEnabled": True,
        "feeType": "economics_fees",
        "feeSchedule": {"exponent": 1, "rate": 0.05, "takerOnly": True, "rebateRate": 0.25},
    }
    fs = FeeSchedule.from_market(market)
    assert fs.rate == 0.05 and fs.exponent == 1 and fs.rebate_rate == 0.25
    assert fs.category == "economics_fees"


def test_from_market_disabled_is_fee_free():
    fs = FeeSchedule.from_market({"feesEnabled": False})
    assert fs.taker_fee(100, 0.5) == 0.0
