import pytest

from ordercore import MatchingEngine, Order, OrderType, Side, Status


@pytest.fixture
def eng():
    return MatchingEngine(symbol="TEST", tick_size=5, opening_cash=10_00_00_000)


def limit(side, qty, price, account="a"):
    return Order(side=side, quantity=qty, price=price, account=account)


def test_resting_order_does_not_trade(eng):
    r = eng.submit(limit(Side.BUY, 10, 100_00))
    assert r.trades == []
    assert eng.book.best_bid() == 100_00


def test_price_priority_beats_arrival(eng):
    eng.submit(limit(Side.SELL, 10, 101_00, "early"))
    eng.submit(limit(Side.SELL, 10, 100_00, "late"))
    r = eng.submit(limit(Side.BUY, 10, 101_00, "taker"))
    assert r.trades[0].price == 100_00  # better price wins regardless of arrival


def test_time_priority_at_equal_price(eng):
    first = eng.submit(limit(Side.SELL, 10, 100_00, "first")).order
    eng.submit(limit(Side.SELL, 10, 100_00, "second"))
    r = eng.submit(limit(Side.BUY, 10, 100_00, "taker"))
    assert r.trades[0].maker_id == first.id


def test_trade_prints_at_the_resting_price(eng):
    eng.submit(limit(Side.SELL, 10, 100_00, "maker"))
    r = eng.submit(limit(Side.BUY, 10, 105_00, "taker"))
    assert r.trades[0].price == 100_00  # not the aggressive 105.00


def test_partial_fill_leaves_remainder_resting(eng):
    eng.submit(limit(Side.SELL, 4, 100_00, "maker"))
    r = eng.submit(limit(Side.BUY, 10, 100_00, "taker"))
    assert r.filled_quantity == 4
    assert r.order.status is Status.PARTIAL
    assert eng.book.best_bid() == 100_00


def test_walking_the_book_gives_a_blended_price(eng):
    eng.submit(limit(Side.SELL, 10, 100_00, "m1"))
    eng.submit(limit(Side.SELL, 10, 102_00, "m2"))
    r = eng.submit(limit(Side.BUY, 20, 102_00, "taker"))
    assert r.filled_quantity == 20
    assert r.average_price == 101_00


def test_market_order_never_rests(eng):
    eng.submit(limit(Side.SELL, 5, 100_00, "maker"))
    r = eng.submit(Order(side=Side.BUY, quantity=50, order_type=OrderType.MARKET, account="t"))
    assert r.filled_quantity == 5
    assert r.order.status is Status.CANCELLED
    assert eng.book.best_bid() is None


def test_ioc_cancels_its_remainder(eng):
    eng.submit(limit(Side.SELL, 3, 100_00, "maker"))
    r = eng.submit(
        Order(side=Side.BUY, quantity=10, price=100_00, order_type=OrderType.IOC, account="t")
    )
    assert r.filled_quantity == 3
    assert eng.book.best_bid() is None


def test_fok_is_all_or_nothing(eng):
    eng.submit(limit(Side.SELL, 3, 100_00, "maker"))
    r = eng.submit(
        Order(side=Side.BUY, quantity=10, price=100_00, order_type=OrderType.FOK, account="t")
    )
    assert r.trades == []
    assert eng.book.best_ask() == 100_00  # maker untouched


def test_cancelled_order_is_skipped_when_matching(eng):
    stale = eng.submit(limit(Side.SELL, 10, 100_00, "m1")).order
    eng.submit(limit(Side.SELL, 10, 100_00, "m2"))
    eng.cancel(stale.id)
    r = eng.submit(limit(Side.BUY, 10, 100_00, "taker"))
    assert r.trades[0].maker_id != stale.id


def test_self_trade_is_prevented(eng):
    eng.submit(limit(Side.SELL, 10, 100_00, "same"))
    r = eng.submit(limit(Side.BUY, 10, 100_00, "same"))
    assert r.trades == []


def test_off_tick_price_is_rejected(eng):
    r = eng.submit(limit(Side.BUY, 10, 100_03))
    assert r.order.status is Status.REJECTED
    assert "multiple of 5" in r.order.reject_reason


def test_position_limit_is_enforced(eng):
    acct = eng.account("whale")
    acct.max_position = 100
    r = eng.submit(limit(Side.BUY, 500, 100_00, "whale"))
    assert r.order.status is Status.REJECTED


def test_cash_conserves_across_a_trade(eng):
    eng.submit(limit(Side.SELL, 10, 100_00, "seller"))
    eng.submit(limit(Side.BUY, 10, 100_00, "buyer"))
    b, s = eng.account("buyer"), eng.account("seller")
    assert b.cash + s.cash == b.opening_cash + s.opening_cash
    assert b.position + s.position == 0


def test_order_at_exact_position_limit_boundary_is_accepted(eng):
    acct = eng.account("edge")
    acct.max_position = 100
    acct.position = 100
    # exactly 100 position, buying more crosses the limit
    r = eng.submit(limit(Side.BUY, 1, 100_00, "edge"))
    assert r.order.status is Status.REJECTED
    # but selling 1 reduces absolute position, which is accepted
    r2 = eng.submit(limit(Side.SELL, 1, 100_00, "edge"))
    assert r2.order.status is not Status.REJECTED

def test_zero_quantity_order_is_rejected(eng):
    r = eng.submit(limit(Side.BUY, 0, 100_00))
    assert r.order.status is Status.REJECTED
    assert "quantity must be positive" in r.order.reject_reason

def test_cancel_of_order_already_filled_returns_none(eng):
    o = eng.submit(limit(Side.SELL, 10, 100_00, "maker")).order
    # sweep it
    eng.submit(limit(Side.BUY, 10, 100_00, "taker"))
    # try to cancel it now
    cancelled = eng.cancel(o.id)
    assert cancelled is None

def test_market_order_sweeps_multiple_price_levels(eng):
    eng.submit(limit(Side.SELL, 10, 100_00, "m1"))
    eng.submit(limit(Side.SELL, 10, 102_00, "m2"))
    eng.submit(limit(Side.SELL, 10, 105_00, "m3"))
    r = eng.submit(Order(side=Side.BUY, quantity=25, order_type=OrderType.MARKET, account="t"))
    assert r.filled_quantity == 25
    assert len(r.trades) == 3
    prices = [t.price for t in r.trades]
    assert prices == [100_00, 102_00, 105_00]

