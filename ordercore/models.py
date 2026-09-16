"""Core domain types for the matching engine.

Prices are integers in paise (1 rupee = 100 paise). Floats are never used for
money anywhere in the engine — 0.1 + 0.2 != 0.3 is not an acceptable property
for an order book.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum

_seq = itertools.count(1)


def next_id() -> int:
    return next(_seq)


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    IOC = "IOC"  # immediate-or-cancel: fill what you can, kill the rest
    FOK = "FOK"  # fill-or-kill: fill entirely or not at all


class Status(str, Enum):
    NEW = "NEW"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass
class Order:
    side: Side
    quantity: int
    price: int | None = None          # None for MARKET orders
    order_type: OrderType = OrderType.LIMIT
    account: str = "default"
    id: int = field(default_factory=next_id)
    seq: int = field(default_factory=next_id)  # arrival order, drives time priority
    filled: int = 0
    status: Status = Status.NEW
    reject_reason: str | None = None

    @property
    def remaining(self) -> int:
        return self.quantity - self.filled

    @property
    def is_live(self) -> bool:
        return self.status in (Status.NEW, Status.PARTIAL) and self.remaining > 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "side": self.side.value,
            "type": self.order_type.value,
            "price": self.price,
            "quantity": self.quantity,
            "filled": self.filled,
            "remaining": self.remaining,
            "status": self.status.value,
            "account": self.account,
            "reject_reason": self.reject_reason,
        }


@dataclass(frozen=True)
class Trade:
    price: int
    quantity: int
    taker_id: int
    maker_id: int
    taker_side: Side
    id: int = field(default_factory=next_id)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "price": self.price,
            "quantity": self.quantity,
            "taker_id": self.taker_id,
            "maker_id": self.maker_id,
            "taker_side": self.taker_side.value,
        }


def rupees(paise: int | None) -> str:
    if paise is None:
        return "MKT"
    return f"{paise / 100:.2f}"
