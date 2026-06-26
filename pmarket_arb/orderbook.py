"""Order-book representation with depth-aware fill simulation.

A real arbitrage check cannot use top-of-book alone: filling any meaningful size
walks *up* the book, and the marginal price you pay rises as you consume levels.
This module models that explicitly so the scanner reasons about *capturable*
edge at a given size, not a paper edge that exists only for the first share.

Prices are 0-1 probability units; sizes are outcome shares (USDC notional at
resolution is shares * $1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple


@dataclass(frozen=True)
class Level:
    price: float
    size: float


@dataclass
class OrderBook:
    """Sorted order book for a single CLOB token.

    ``asks`` are kept ascending by price (best = cheapest first); ``bids``
    descending (best = highest first). The Polymarket CLOB does not guarantee
    sort order on the wire, so we sort defensively on construction.
    """

    token_id: str
    bids: List[Level]
    asks: List[Level]
    timestamp: int = 0

    def __post_init__(self) -> None:
        self.asks = sorted(self.asks, key=lambda l: l.price)
        self.bids = sorted(self.bids, key=lambda l: l.price, reverse=True)

    # --- top of book -----------------------------------------------------
    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else float("nan")

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else float("nan")

    @property
    def ask_depth(self) -> float:
        return sum(l.size for l in self.asks)

    @property
    def bid_depth(self) -> float:
        return sum(l.size for l in self.bids)

    # --- fill simulation -------------------------------------------------
    def fills_to_buy(self, shares: float) -> List[Tuple[float, float]]:
        """Return the (price, qty) fills to BUY ``shares`` by consuming asks.

        Stops early if the book is too thin; the returned quantities then sum to
        less than ``shares``. Callers should check the filled total.
        """
        return _consume(self.asks, shares)

    def fills_to_sell(self, shares: float) -> List[Tuple[float, float]]:
        """Return the (price, qty) fills to SELL ``shares`` by consuming bids."""
        return _consume(self.bids, shares)

    def cost_to_buy(self, shares: float) -> float:
        """Total USDC paid to buy ``shares`` (0 if nothing fills)."""
        return sum(p * q for p, q in self.fills_to_buy(shares))

    def proceeds_to_sell(self, shares: float) -> float:
        """Total USDC received to sell ``shares``."""
        return sum(p * q for p, q in self.fills_to_sell(shares))

    @classmethod
    def from_clob(cls, payload: dict) -> "OrderBook":
        """Build from a CLOB ``/book`` (or ``/books`` element) response."""
        def levels(key: str) -> List[Level]:
            out = []
            for lvl in payload.get(key, []) or []:
                try:
                    out.append(Level(float(lvl["price"]), float(lvl["size"])))
                except (KeyError, TypeError, ValueError):
                    continue
            return out

        ts = payload.get("timestamp", 0)
        try:
            ts = int(ts)
        except (TypeError, ValueError):
            ts = 0
        return cls(
            token_id=str(payload.get("asset_id") or payload.get("token_id") or ""),
            bids=levels("bids"),
            asks=levels("asks"),
            timestamp=ts,
        )


def _consume(levels: List[Level], shares: float) -> List[Tuple[float, float]]:
    """Walk ``levels`` (already sorted best-first) filling up to ``shares``."""
    if shares <= 0:
        return []
    remaining = shares
    fills: List[Tuple[float, float]] = []
    for lvl in levels:
        if remaining <= 0:
            break
        take = min(remaining, lvl.size)
        if take <= 0:
            continue
        fills.append((lvl.price, take))
        remaining -= take
    return fills
