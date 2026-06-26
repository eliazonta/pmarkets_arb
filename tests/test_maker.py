import math

from pmarket_arb.maker import basket_maker_edge, leg_maker_edge
from pmarket_arb.markets import NegRiskEvent, Outcome
from pmarket_arb.microstructure import PriceSeries
from pmarket_arb.orderbook import Level, OrderBook
from pmarket_arb.rewards import RewardProgram
from pmarket_arb.fees import FeeSchedule


def _book(bid, ask, size=5000):
    return OrderBook("t", bids=[Level(bid, size)], asks=[Level(ask, size)])


def test_realized_vol_per_min():
    # diffs are +/-1 alternating: mean 0, sample var = 4/3
    s = PriceSeries("t", times=[0, 60, 120, 180, 240], prices=[0, 1, 0, 1, 0], bar_seconds=60)
    assert math.isclose(s.realized_vol_per_min(), math.sqrt(4 / 3), rel_tol=1e-9)


def test_breakeven_fill_time_consistency():
    # half-spread 1c, vol such that tau* should equal (h/sigma)^2
    book = _book(0.49, 0.51)                       # h = 1.0 cent
    series = PriceSeries("t", times=list(range(0, 300, 60)),
                         prices=[0.50, 0.51, 0.50, 0.51, 0.50], bar_seconds=60)
    rp = RewardProgram(daily_pool=0, min_size=0, max_spread_cents=0)
    edge = leg_maker_edge("A", book, rp, series, quote_size=5000)
    assert math.isclose(edge.half_spread_cents, 1.0, rel_tol=1e-6)
    assert edge.sigma_min_cents > 0
    assert math.isclose(edge.tau_breakeven_min,
                        (edge.half_spread_cents / edge.sigma_min_cents) ** 2, rel_tol=1e-9)


def test_reward_bps_on_capital():
    book = _book(0.49, 0.51)
    rp = RewardProgram(daily_pool=300, min_size=1000, max_spread_cents=2.0)
    edge = leg_maker_edge("A", book, rp, None, quote_size=5000)
    # mid 0.5, capital = 5000*0.5 = 2500; competing depth within band exists
    assert edge.reward_daily > 0
    assert math.isclose(edge.reward_bps_day, edge.reward_daily / 2500 * 1e4, rel_tol=1e-9)


def _outcome(label, bid, ask):
    o = Outcome(label, label, label, f"{label}-Y", f"{label}-N", FeeSchedule(0.0),
                rewards_daily=100, rewards_min_size=50, rewards_max_spread=2.0)
    o.yes_book = _book(bid, ask)
    return o


def test_basket_resolution_variance_nets_to_zero():
    # two complementary legs whose mids sum to 1 -> balanced basket is riskless
    ev = NegRiskEvent("e", "Test", "nrm", "sports",
                      [_outcome("A", 0.39, 0.41), _outcome("B", 0.59, 0.61)])
    be = basket_maker_edge(ev, series_by_token={}, quote_size=100)
    # mids 0.40 + 0.60 = 1.00 -> balanced variance ~ 0
    assert abs(be.balanced_basket_resolution_var) < 1e-6
    # held independently the legs carry real variance: 100^2*(0.24+0.24)=4800
    assert math.isclose(be.summed_leg_resolution_var, 4800.0, rel_tol=1e-9)
    assert be.total_reward_daily > 0
