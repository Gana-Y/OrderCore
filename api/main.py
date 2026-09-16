"""REST order entry plus a WebSocket market data feed, in front of the engine."""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ordercore import MarketSimulator, MatchingEngine, Order, OrderType, Side, Status

import logging

logging.basicConfig(
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger("ordercore")

_start_time = time.time()

STATIC = Path(__file__).resolve().parent.parent / "static"

engine = MatchingEngine(symbol="SIM", tick_size=5)
simulator = MarketSimulator(engine, seed=7)
subscribers: set[WebSocket] = set()
latencies: list[float] = []


class OrderRequest(BaseModel):
    side: Side
    quantity: int = Field(gt=0, le=100_000)
    price: float | None = Field(default=None, description="in rupees; omit for MARKET")
    order_type: OrderType = OrderType.LIMIT
    account: str = "you"


async def broadcast() -> None:
    if not subscribers:
        return
    payload = engine.snapshot()
    payload["latency_us"] = round(sum(latencies[-500:]) / len(latencies[-500:]), 1) if latencies else None
    payload["open_orders"] = [
        o.to_dict() for o in engine.book._orders.values()
        if o.account == "you" and o.is_live
    ]
    dead = []
    for ws in subscribers:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        subscribers.discard(ws)


async def market_loop() -> None:
    while True:
        simulator.step()
        await broadcast()
        await asyncio.sleep(0.9)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(market_loop())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="OrderCore", lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "symbol": engine.book.symbol,
        "orders_matched": len(engine.trades),
        "uptime_seconds": time.time() - _start_time,
    }


@app.post("/orders")
async def place_order(req: OrderRequest):
    price = None if req.price is None else int(round(req.price * 100))
    if req.order_type is OrderType.MARKET:
        price = None

    order = Order(
        side=req.side,
        quantity=req.quantity,
        price=price,
        order_type=req.order_type,
        account=req.account,
    )

    logger.info("ORDER  | side=%s qty=%d price=%s type=%s account=%s",
                order.side.value, order.quantity,
                "MKT" if order.price is None else f"{order.price/100:.2f}",
                order.order_type.value, order.account)

    started = time.perf_counter()
    result = engine.submit(order)
    latency_us = (time.perf_counter() - started) * 1_000_000
    latencies.append(latency_us)

    if result.order.status == Status.REJECTED:
        logger.warning("REJECT | id=%d reason=%s", result.order.id, result.order.reject_reason)

    for t in result.trades:
        logger.info("TRADE  | price=%.2f qty=%d taker=%d maker=%d latency=%.0fµs",
                    t.price / 100, t.quantity, t.taker_id, t.maker_id, latency_us)

    await broadcast()
    return result.to_dict()


@app.delete("/orders/{order_id}")
async def cancel_order(order_id: int):
    cancelled = engine.cancel(order_id)
    if cancelled is None:
        raise HTTPException(404, "no live order with that id")
    await broadcast()
    return cancelled.to_dict()


@app.post("/burst")
async def burst(n: int = 250):
    t0 = time.perf_counter()
    trades_before = len(engine.trades)
    for _ in range(n):
        simulator.step()
    elapsed_s = time.perf_counter() - t0
    trades_new = len(engine.trades) - trades_before
    throughput = round(n / elapsed_s, 0) if elapsed_s > 0 else n * 1000
    await broadcast()
    return {
        "orders_injected": n,
        "trades_generated": trades_new,
        "elapsed_ms": round(elapsed_s * 1000, 2),
        "throughput_ops": throughput,
    }


@app.get("/book")
async def get_book(depth: int = 5):
    return engine.snapshot(depth)


@app.websocket("/ws")
async def feed(ws: WebSocket):
    await ws.accept()
    subscribers.add(ws)
    await ws.send_json(engine.snapshot())
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        subscribers.discard(ws)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")
