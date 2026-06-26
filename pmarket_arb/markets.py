"""Domain model for Polymarket negRisk (multi-outcome, mutually exclusive) events.

A negRisk event ("Who wins X?", "Fed decision in July?") is modelled on
Polymarket as N independent binary Yes/No markets that share a
``negRiskMarketID``, with the guarantee that *exactly one* outcome resolves Yes.
That guarantee is what creates a dutch-book relationship across the set:

    sum_i  price(Yes_i)  == 1        (no-arbitrage)

Each outcome owns two CLOB tokens (Yes and No), each with its own order book.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .fees import FeeSchedule
from .orderbook import OrderBook


@dataclass
class Outcome:
    """One binary leg of a negRisk event (e.g. "No change")."""

    label: str            # groupItemTitle, e.g. "No change"
    question: str
    condition_id: str
    yes_token_id: str
    no_token_id: str
    fee: FeeSchedule
    # snapshot best prices from Gamma (cheap signal; order books are authoritative)
    best_bid: float = float("nan")
    best_ask: float = float("nan")
    # liquidity-reward (LRP) params, used by the maker model
    rewards_daily: float = 0.0
    rewards_min_size: float = 0.0
    rewards_max_spread: float = 0.0
    tick_size: float = 0.01
    order_min_size: float = 5.0
    yes_book: Optional[OrderBook] = None
    no_book: Optional[OrderBook] = None


@dataclass
class NegRiskEvent:
    """A multi-outcome mutually-exclusive event."""

    event_id: str
    title: str
    neg_risk_market_id: str
    category: str
    outcomes: List[Outcome] = field(default_factory=list)
    volume24hr: float = 0.0

    @property
    def n(self) -> int:
        return len(self.outcomes)

    def all_token_ids(self) -> List[str]:
        ids: List[str] = []
        for o in self.outcomes:
            ids.extend([o.yes_token_id, o.no_token_id])
        return ids

    def attach_books(self, books_by_token: dict) -> None:
        """Wire fetched OrderBook objects onto each outcome by token id."""
        for o in self.outcomes:
            o.yes_book = books_by_token.get(o.yes_token_id)
            o.no_book = books_by_token.get(o.no_token_id)

    def books_complete(self) -> bool:
        return all(o.yes_book and o.no_book for o in self.outcomes)
