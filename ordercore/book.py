"""Limit order book with strict price-time priority.

Design notes
------------
Each side keeps a dict of {price -> FIFO queue of resting orders} plus a heap of
active prices, so best-bid / best-ask is O(1) amortised and adding an order is
O(log P) in the number of distinct price levels (not the number of orders).

Cancellation is O(1): the order is looked up by id and flagged, and the queue
skips over dead orders lazily when it is next walked. This avoids the O(n) scan
that a naive list-based book pays on every cancel — and in real markets the vast
majority of orders are cancelled, not filled.
"""

from __future__ import annotations

import heapq
from collections import deque

from .models import Order, Side, Status


class PriceLevel:
    __slots__ = ("price", "orders", "volume")

    def __init__(self, price: int):
        self.price = price
        self.orders: deque[Order] = deque()
        self.volume = 0

    def append(self, order: Order) -> None:
        self.orders.append(order)
        self.volume += order.remaining

    def front(self) -> Order | None:
        """Head of the queue, discarding cancelled/filled orders as we go."""
        while self.orders:
            head = self.orders[0]
            if head.is_live:
                return head
            self.orders.popleft()
        return None

    def __bool__(self) -> bool:
        return self.front() is not None


class OrderBook:
    def __init__(self, symbol: str = "SIM"):
        self.symbol = symbol
        self._levels: dict[Side, dict[int, PriceLevel]] = {Side.BUY: {}, Side.SELL: {}}
        # bids are a max-heap (negated), asks a min-heap
        self._heaps: dict[Side, list[int]] = {Side.BUY: [], Side.SELL: []}
        self._orders: dict[int, Order] = {}

    # ---------------------------------------------------------------- resting

    def add(self, order: Order) -> None:
        assert order.price is not None, "cannot rest a market order"
        side_levels = self._levels[order.side]
        level = side_levels.get(order.price)
        if level is None:
            level = side_levels[order.price] = PriceLevel(order.price)
            key = -order.price if order.side is Side.BUY else order.price
            heapq.heappush(self._heaps[order.side], key)
        level.append(order)
        self._orders[order.id] = order

    def cancel(self, order_id: int) -> Order | None:
        order = self._orders.get(order_id)
        if order is None or not order.is_live:
            return None
        order.status = Status.CANCELLED
        level = self._levels[order.side].get(order.price)
        if level is not None:
            level.volume -= order.remaining
        return order

    def get(self, order_id: int) -> Order | None:
        return self._orders.get(order_id)

    # ------------------------------------------------------------------- tops

    def best(self, side: Side) -> PriceLevel | None:
        heap = self._heaps[side]
        levels = self._levels[side]
        while heap:
            price = -heap[0] if side is Side.BUY else heap[0]
            level = levels.get(price)
            if level is not None and level.front() is not None:
                return level
            heapq.heappop(heap)
            levels.pop(price, None)
        return None

    def best_bid(self) -> int | None:
        level = self.best(Side.BUY)
        return level.price if level else None

    def best_ask(self) -> int | None:
        level = self.best(Side.SELL)
        return level.price if level else None

    def spread(self) -> int | None:
        bid, ask = self.best_bid(), self.best_ask()
        return None if bid is None or ask is None else ask - bid

    def consume_front(self, level: PriceLevel, quantity: int) -> None:
        """Reduce the head order of `level` by `quantity`."""
        head = level.front()
        if head is None:
            return
        head.filled += quantity
        level.volume -= quantity
        head.status = Status.FILLED if head.remaining == 0 else Status.PARTIAL
        if head.remaining == 0:
            level.orders.popleft()

    # ------------------------------------------------------------------ views

    def depth(self, levels: int = 5) -> dict:
        """Aggregated top-of-book snapshot, the shape a market data feed sends."""

        def side_depth(side: Side) -> list[dict]:
            prices = sorted(
                (p for p, lv in self._levels[side].items() if lv.front() is not None),
                reverse=(side is Side.BUY),
            )[:levels]
            out = []
            for price in prices:
                level = self._levels[side][price]
                qty = sum(o.remaining for o in level.orders if o.is_live)
                if qty:
                    out.append({"price": price, "quantity": qty, "orders": len(level.orders)})
            return out

        return {
            "symbol": self.symbol,
            "bids": side_depth(Side.BUY),
            "asks": side_depth(Side.SELL),
            "best_bid": self.best_bid(),
            "best_ask": self.best_ask(),
            "spread": self.spread(),
        }

    def available(self, side: Side, limit_price: int | None) -> int:
        """Total quantity resting on `side` that would satisfy `limit_price`."""
        total = 0
        for price, level in self._levels[side].items():
            if limit_price is not None:
                if side is Side.SELL and price > limit_price:
                    continue
                if side is Side.BUY and price < limit_price:
                    continue
            total += sum(o.remaining for o in level.orders if o.is_live)
        return total
