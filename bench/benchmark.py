"""Throughput and latency benchmark. Run: python bench/benchmark.py"""

import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ordercore import MatchingEngine, Order, OrderType, Side

N = 200_000


import argparse

def main() -> None:
    parser = argparse.ArgumentParser(description="OrderCore matching engine benchmark")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for reproducibility")
    args = parser.parse_args()

    engine = MatchingEngine(tick_size=5, opening_cash=10**15)
    for acct in (f"t{i}" for i in range(8)):
        a = engine.account(acct)
        a.max_order_value = 10**15
        a.max_position = 10**9

    rng = random.Random(args.seed)
    orders = []
    fair = 1500_00
    for i in range(N):
        fair += rng.gauss(0, 8)
        price = int(round((fair + rng.gauss(0, 60)) / 5) * 5)
        market = rng.random() < 0.08
        orders.append(
            Order(
                side=rng.choice([Side.BUY, Side.SELL]),
                quantity=rng.randint(1, 50),
                price=None if market else max(5, price),
                order_type=OrderType.MARKET if market else OrderType.LIMIT,
                account=f"t{i % 8}",
            )
        )

    samples = []
    start = time.perf_counter()
    for order in orders:
        t0 = time.perf_counter_ns()
        engine.submit(order)
        samples.append(time.perf_counter_ns() - t0)
    elapsed = time.perf_counter() - start

    samples.sort()
    print(f"orders       {N:,}")
    print(f"trades       {len(engine.trades):,}")
    print(f"wall time    {elapsed:.2f}s")
    print(f"throughput   {N / elapsed:,.0f} orders/sec")
    print(f"median       {statistics.median(samples) / 1000:.2f} µs")
    print(f"p99          {samples[int(len(samples) * 0.99)] / 1000:.2f} µs")


if __name__ == "__main__":
    main()
