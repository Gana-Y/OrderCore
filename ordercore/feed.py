"""Synthetic participants, so the book has something in it before you trade.

Two populations:
  - market makers, who quote a two-sided spread around a drifting fair value
    and refresh their quotes every tick
  - noise traders, who cross the spread at random

Together they produce a book that behaves plausibly: a spread that widens when
the fair value jumps, queues that build at round numbers, and a trade tape.
"""

from __future__ import annotations

import random

from .engine import MatchingEngine
from .models import Order, OrderType, Side


class MarketSimulator:
    def __init__(
        self,
        engine: MatchingEngine,
        fair_value: int = 1500_00,
        volatility: float = 12.0,
        makers: int = 4,
        seed: int | None = None,
    ):
        self.engine = engine
        self.fair_value = fair_value
        self.volatility = volatility
        self.rng = random.Random(seed)
        self.maker_names = [f"mm-{i + 1}" for i in range(makers)]
        self._quotes: dict[str, list[int]] = {n: [] for n in self.maker_names}

    def _round_tick(self, price: float) -> int:
        tick = self.engine.tick_size
        return max(tick, int(round(price / tick) * tick))

    def step(self) -> None:
        """Advance the simulation by one tick."""
        self.fair_value = max(100, self.fair_value + self.rng.gauss(0, self.volatility))

        for name in self.maker_names:
            for order_id in self._quotes[name]:
                self.engine.cancel(order_id)
            self._quotes[name] = []

            edge = self.rng.uniform(10, 45)
            size = self.rng.randint(5, 60)
            bid = self._round_tick(self.fair_value - edge)
            ask = self._round_tick(self.fair_value + edge)
            if ask <= bid:
                ask = bid + self.engine.tick_size

            for side, price in ((Side.BUY, bid), (Side.SELL, ask)):
                result = self.engine.submit(
                    Order(side=side, quantity=size, price=price, account=name)
                )
                if result.order.is_live:
                    self._quotes[name].append(result.order.id)

        if self.rng.random() < 0.55:
            self.engine.submit(
                Order(
                    side=self.rng.choice([Side.BUY, Side.SELL]),
                    quantity=self.rng.randint(1, 40),
                    order_type=OrderType.MARKET,
                    account=f"noise-{self.rng.randint(1, 3)}",
                )
            )
