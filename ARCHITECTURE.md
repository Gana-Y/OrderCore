# Architecture Decision Log

## 1. Why integer paise, not floats
All money in the engine is represented as integer paise rather than floating-point numbers. This avoids IEEE 754 rounding errors inherent in binary floating-point math, such as `0.1 + 0.2 != 0.3`. In a financial system that settles real cash balances, determinism and exact precision are non-negotiable requirements. The trade-off is that all external boundaries (API, UI) must explicitly convert to and from decimal formats, and developers must remember that `100` means `1.00`, introducing a small but constant risk of scale-factor bugs if a division is forgotten.

## 2. Why heap + dict, not a sorted list
The order book is structured as a dictionary of FIFO queues keyed by price, paired with a heap of active price levels, rather than a single sorted list of orders. This allows inserting an order to be $O(\log P)$ where $P$ is the number of distinct active price levels (which is small and strictly bounded by the tick grid), instead of $O(\log N)$ where $N$ is the unbounded number of total resting orders. The trade-off is slightly higher structural complexity and the need for lazy cleanup — the dict and heap can temporarily hold "ghost" price levels that only resolve when they are next walked.

## 3. Why lazy O(1) cancellation
Cancellation is performed by marking an order's status as `CANCELLED` in a globally accessible dictionary rather than eagerly removing it from its queue. Real-world order flow consists overwhelmingly of cancellations (often >95%); finding and removing an element from the middle of a deque is an $O(N)$ operation that would severely bottleneck throughput. By flagging it, cancellation becomes $O(1)$ and the queue lazily discards dead orders when walking the book to match. The trade-off is that memory is not freed immediately upon cancellation, and deeply buried cancelled orders consume memory until a price sweep reaches them.

## 4. Why trades print at the maker's price
When an aggressive order crosses the spread, the resulting trade executes at the price of the resting order rather than the price of the aggressive order. This upholds the principle that the maker, by resting liquidity in the book and taking on time-priority risk, has the right to set the exact terms of execution. The trade-off is that takers will frequently experience "price improvement" — getting filled at a better price than their limit — which means the engine's output doesn't perfectly match their input price, requiring clients to correctly handle and reconcile average fill prices.
