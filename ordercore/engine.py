"""Matching engine: pre-trade risk, order matching, position keeping."""

from __future__ import annotations

from dataclasses import dataclass, field

from .book import OrderBook
from .models import Order, OrderType, Side, Status, Trade


class RiskRejection(Exception):
    pass


@dataclass
class Account:
    name: str
    cash: int                     # paise
    opening_cash: int = 0         # set at creation, the P&L baseline
    position: int = 0             # signed quantity
    max_order_value: int = 50_00_000_00   # 50 lakh, in paise
    max_position: int = 10_000

    def pnl(self, last_price: int | None) -> int:
        """Cash moved plus the open position valued at the last traded price."""
        mark = (self.position * last_price) if last_price is not None else 0
        return (self.cash - self.opening_cash) + mark


@dataclass
class MatchResult:
    order: Order
    trades: list[Trade] = field(default_factory=list)

    @property
    def filled_quantity(self) -> int:
        return sum(t.quantity for t in self.trades)

    @property
    def average_price(self) -> float | None:
        if not self.trades:
            return None
        notional = sum(t.price * t.quantity for t in self.trades)
        return notional / self.filled_quantity

    def to_dict(self) -> dict:
        return {
            "order": self.order.to_dict(),
            "trades": [t.to_dict() for t in self.trades],
            "average_price": self.average_price,
        }


class MatchingEngine:
    """Single-symbol engine. Orders are processed one at a time, in arrival order."""

    def __init__(self, symbol: str = "SIM", tick_size: int = 5, opening_cash: int = 10_00_000_00):
        self.book = OrderBook(symbol)
        self.tick_size = tick_size
        self.opening_cash = opening_cash
        self.accounts: dict[str, Account] = {}
        self.trades: list[Trade] = []
        self.last_price: int | None = None

    # --------------------------------------------------------------- accounts

    def account(self, name: str) -> Account:
        if name not in self.accounts:
            self.accounts[name] = Account(
                name=name, cash=self.opening_cash, opening_cash=self.opening_cash
            )
        return self.accounts[name]

    # ------------------------------------------------------------------- risk

    def _check_risk(self, order: Order) -> None:
        acct = self.account(order.account)

        if order.quantity <= 0:
            raise RiskRejection("quantity must be positive")

        if order.order_type is OrderType.MARKET:
            if order.price is not None:
                raise RiskRejection("market orders must not carry a price")
        else:
            if order.price is None or order.price <= 0:
                raise RiskRejection("limit price must be positive")
            if order.price % self.tick_size:
                raise RiskRejection(f"price must be a multiple of {self.tick_size} paise")

        reference = order.price or self.last_price or self.book.best_ask() or 0
        if reference * order.quantity > acct.max_order_value:
            raise RiskRejection("order value exceeds per-order limit")

        signed = order.quantity if order.side is Side.BUY else -order.quantity
        if abs(acct.position + signed) > acct.max_position:
            raise RiskRejection("order would breach position limit")

        if order.side is Side.BUY and reference * order.quantity > acct.cash:
            raise RiskRejection("insufficient funds")

    # --------------------------------------------------------------- matching

    def submit(self, order: Order) -> MatchResult:
        try:
            self._check_risk(order)
        except RiskRejection as exc:
            order.status = Status.REJECTED
            order.reject_reason = str(exc)
            return MatchResult(order=order)

        if order.order_type is OrderType.FOK:
            if self.book.available(order.side.opposite, order.price) < order.quantity:
                order.status = Status.CANCELLED
                order.reject_reason = "fill-or-kill could not be filled in full"
                return MatchResult(order=order)

        result = MatchResult(order=order)
        self._match(order, result)

        if order.remaining > 0:
            if order.order_type is OrderType.LIMIT:
                self.book.add(order)
            else:
                # MARKET and IOC never rest
                order.status = Status.CANCELLED
                order.reject_reason = "unfilled remainder cancelled"

        return result

    def _match(self, taker: Order, result: MatchResult) -> None:
        opposite = taker.side.opposite

        while taker.remaining > 0:
            level = self.book.best(opposite)
            if level is None:
                break

            if taker.price is not None:
                crosses = (
                    level.price <= taker.price
                    if taker.side is Side.BUY
                    else level.price >= taker.price
                )
                if not crosses:
                    break

            maker = level.front()
            if maker is None:
                break
            if maker.account == taker.account:
                # self-trade prevention: cancel the resting order, don't print a trade
                self.book.cancel(maker.id)
                maker.reject_reason = "self-trade prevention"
                continue

            quantity = min(taker.remaining, maker.remaining)
            # the resting order sets the price — the maker was there first
            price = level.price

            self.book.consume_front(level, quantity)
            taker.filled += quantity
            taker.status = Status.FILLED if taker.remaining == 0 else Status.PARTIAL

            trade = Trade(
                price=price,
                quantity=quantity,
                taker_id=taker.id,
                maker_id=maker.id,
                taker_side=taker.side,
            )
            self._settle(trade, taker, maker)
            self.trades.append(trade)
            result.trades.append(trade)
            self.last_price = price

    def _settle(self, trade: Trade, taker: Order, maker: Order) -> None:
        notional = trade.price * trade.quantity
        buyer = taker if taker.side is Side.BUY else maker
        seller = maker if taker.side is Side.BUY else taker

        b, s = self.account(buyer.account), self.account(seller.account)
        b.cash -= notional
        b.position += trade.quantity
        s.cash += notional
        s.position -= trade.quantity

    # ---------------------------------------------------------------- helpers

    def cancel(self, order_id: int) -> Order | None:
        return self.book.cancel(order_id)

    def snapshot(self, depth: int = 5) -> dict:
        return {
            **self.book.depth(depth),
            "last_price": self.last_price,
            "trade_count": len(self.trades),
            "recent_trades": [t.to_dict() for t in self.trades[-15:]][::-1],
            "accounts": [
                {
                    "name": a.name,
                    "cash": a.cash,
                    "position": a.position,
                    "pnl": a.pnl(self.last_price),
                }
                for a in self.accounts.values()
            ],
        }
