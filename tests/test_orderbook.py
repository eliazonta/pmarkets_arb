import math

from pmarket_arb.orderbook import Level, OrderBook


def make_book():
    # asks given out of order on purpose; OrderBook must sort
    return OrderBook(
        token_id="t",
        bids=[Level(0.40, 100), Level(0.45, 50)],
        asks=[Level(0.55, 100), Level(0.50, 30), Level(0.52, 20)],
    )


def test_sorts_and_top_of_book():
    b = make_book()
    assert b.best_ask == 0.50
    assert b.best_bid == 0.45
    assert b.ask_depth == 150
    assert b.bid_depth == 150


def test_walk_the_book_cost():
    b = make_book()
    # buy 40: 30 @0.50 + 10 @0.52 = 15.0 + 5.2 = 20.2
    assert math.isclose(b.cost_to_buy(40), 20.2)
    fills = b.fills_to_buy(40)
    assert fills == [(0.50, 30), (0.52, 10)]


def test_thin_book_partial_fill():
    b = make_book()
    fills = b.fills_to_buy(1000)          # more than total depth (150)
    assert math.isclose(sum(q for _, q in fills), 150)


def test_sell_consumes_bids_best_first():
    b = make_book()
    # sell 60: 50 @0.45 + 10 @0.40 = 22.5 + 4.0 = 26.5
    assert math.isclose(b.proceeds_to_sell(60), 26.5)


def test_from_clob_parses_strings():
    payload = {
        "asset_id": "123",
        "timestamp": "1700000000000",
        "bids": [{"price": "0.40", "size": "100"}],
        "asks": [{"price": "0.60", "size": "50"}],
    }
    b = OrderBook.from_clob(payload)
    assert b.token_id == "123" and b.best_ask == 0.60 and b.best_bid == 0.40
    assert b.timestamp == 1700000000000
