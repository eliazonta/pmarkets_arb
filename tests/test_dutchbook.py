import math

from pmarket_arb.dutchbook import best_opportunity, scan_event
from pmarket_arb.fees import FeeSchedule
from pmarket_arb.markets import NegRiskEvent, Outcome
from pmarket_arb.orderbook import Level, OrderBook

FREE = FeeSchedule(rate=0.0)
ECON = FeeSchedule(rate=0.05)


def _outcome(label, yes_asks, no_asks, fee=FREE):
    o = Outcome(
        label=label, question=label, condition_id=label,
        yes_token_id=f"{label}-Y", no_token_id=f"{label}-N", fee=fee,
    )
    o.yes_book = OrderBook(f"{label}-Y", bids=[], asks=[Level(p, s) for p, s in yes_asks])
    o.no_book = OrderBook(f"{label}-N", bids=[], asks=[Level(p, s) for p, s in no_asks])
    return o


def _event(outcomes, category="test"):
    return NegRiskEvent("e", "Test event", "nrm", category, outcomes)


def test_yes_side_clean_arb():
    # sum of best Yes asks = 0.95 < 1  -> buy-all-Yes arb, fee free
    ev = _event([
        _outcome("A", yes_asks=[(0.40, 100)], no_asks=[(0.60, 100)]),
        _outcome("B", yes_asks=[(0.55, 100)], no_asks=[(0.45, 100)]),
    ])
    opp = best_opportunity(ev)
    assert opp.side == "YES"
    assert math.isclose(opp.optimal_size, 100)
    assert math.isclose(opp.net_profit, 5.0)        # 100 - 95
    assert math.isclose(opp.tob_gross_edge, 0.05)
    assert opp.is_arb and not opp.settles_immediately


def test_optimal_size_respects_depth_breakpoint():
    # Outcome A has a cheap shallow level then an expensive one; the optimum
    # is to stop at the cheap level (size 50), not fill the whole book.
    ev = _event([
        _outcome("A", yes_asks=[(0.40, 50), (0.60, 50)], no_asks=[(0.60, 100)]),
        _outcome("B", yes_asks=[(0.55, 100)], no_asks=[(0.45, 100)]),
    ])
    opp = best_opportunity(ev)
    assert math.isclose(opp.optimal_size, 50)
    assert math.isclose(opp.net_profit, 2.5)        # 50 - (20 + 27.5)


def test_fee_destroys_thin_edge():
    # 1c gross edge per set, but both legs near p=0.5 where the 5% fee bites
    ev = _event([
        _outcome("A", yes_asks=[(0.50, 1000)], no_asks=[(0.50, 1000)], fee=ECON),
        _outcome("B", yes_asks=[(0.49, 1000)], no_asks=[(0.51, 1000)], fee=ECON),
    ])
    opps = scan_event(ev)
    yes = next(o for o in opps if o.side == "YES")
    assert yes.tob_gross_edge > 0          # paper edge exists
    assert yes.gross_profit > 0
    assert yes.fee_paid > yes.gross_profit  # ...but fees exceed it
    assert not yes.is_arb                   # so it is NOT a real arb


def test_no_side_convert_arb():
    # 3 outcomes, sum of best No asks = 1.80 < N-1 = 2 -> buy-all-No + convert
    ev = _event([
        _outcome("A", yes_asks=[(0.45, 100)], no_asks=[(0.60, 100)]),
        _outcome("B", yes_asks=[(0.45, 100)], no_asks=[(0.60, 100)]),
        _outcome("C", yes_asks=[(0.45, 100)], no_asks=[(0.60, 100)]),
    ])
    opps = scan_event(ev)
    no = next(o for o in opps if o.side == "NO")
    assert no.tob_target == 2.0
    assert math.isclose(no.net_profit, 20.0)   # 200 payout - 180 cost
    assert no.settles_immediately


def test_no_books_returns_empty():
    o = Outcome("A", "A", "A", "A-Y", "A-N", FREE)   # books left as None
    assert scan_event(_event([o, o])) == []
