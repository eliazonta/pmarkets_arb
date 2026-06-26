"""Depth- and fee-aware dutch-book arbitrage scanner for negRisk events.

Two riskless structures exist when the cross-outcome no-arbitrage relation
``sum_i price(Yes_i) == 1`` is violated:

  YES side (set underpriced, sum of Yes asks < 1)
    Buy ``s`` Yes shares on every outcome. Exactly one resolves to $1/share, so
    the basket pays ``s`` at resolution. Profit = s - cost(buy all Yes) - fees.

  NO side (set overpriced, sum of Yes bids > 1, i.e. sum of No asks < N-1)
    Buy ``s`` No shares on every outcome, then call the negRisk adapter's
    ``convertPositions`` to redeem the complete No-set for ``s * (N-1)`` USDC
    immediately on-chain (gasless via the relayer). Profit = s*(N-1) - cost - fees.

Both are evaluated *against the real order books*, walking depth so the reported
edge is the edge actually capturable at the optimal size -- net of Polymarket's
taker fee, which peaks at p=0.50 exactly where these violations live.

Capital is locked until resolution on the YES side; the NO+convert side settles
immediately. We report capital deployed and net edge in bps so the two are
comparable, and flag the holding caveat rather than annualising naively.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .markets import NegRiskEvent, Outcome
from .orderbook import OrderBook


@dataclass
class LegFill:
    label: str
    shares: float
    cost: float          # USDC paid (ex-fee)
    fee: float           # USDC taker fee
    vwap: float          # cost / shares


@dataclass
class Opportunity:
    event_id: str
    title: str
    category: str
    side: str                 # "YES" or "NO"
    n_outcomes: int
    # top-of-book signal (size -> 0 limit), before fees
    tob_basket_price: float   # sum of best asks used by this side
    tob_target: float         # 1.0 (YES) or N-1 (NO): the no-arb threshold
    tob_gross_edge: float     # max(0, target - basket) per set, paper edge
    # depth- and fee-aware result at the optimal size
    optimal_size: float       # shares per leg
    capital: float            # USDC deployed (basket cost incl. fees)
    gross_profit: float       # ignoring fees, at optimal_size
    fee_paid: float
    net_profit: float         # after fees
    net_edge_bps: float       # net_profit / capital * 1e4
    legs: List[LegFill]
    settles_immediately: bool # True for NO+convert; YES locks until resolution

    @property
    def is_arb(self) -> bool:
        return self.net_profit > 0


def _evaluate_side(
    event: NegRiskEvent,
    books: List[OrderBook],
    outcomes: List[Outcome],
    target: float,
    side: str,
) -> Optional[Opportunity]:
    """Evaluate one dutch-book side. ``books`` are the ask books to buy from,
    aligned with ``outcomes``; ``target`` is the per-set payout (1 or N-1)."""
    if any(b is None for b in books):
        return None

    # Top-of-book signal (paper edge before fees / depth).
    tob_basket = sum(b.best_ask for b in books)
    tob_gross = max(0.0, target - tob_basket)

    # Max complete-set size = the thinnest leg's ask depth.
    s_max = min(b.ask_depth for b in books)
    if s_max <= 0:
        return None

    # Candidate sizes: every leg's cumulative-depth boundary (net profit is
    # piecewise-linear in s and the fee term is non-monotonic across p=0.5, so
    # the optimum sits at one of these breakpoints).
    candidates = {s_max}
    for b in books:
        cum = 0.0
        for lvl in b.asks:
            cum += lvl.size
            if cum <= s_max:
                candidates.add(cum)
    candidate_sizes = sorted(s for s in candidates if s > 0)

    best: Optional[Opportunity] = None
    for s in candidate_sizes:
        legs: List[LegFill] = []
        total_cost = 0.0
        total_fee = 0.0
        feasible = True
        for oc, b in zip(outcomes, books):
            fills = b.fills_to_buy(s)
            filled = sum(q for _, q in fills)
            if filled < s - 1e-9:        # book too thin to complete this leg
                feasible = False
                break
            cost = sum(p * q for p, q in fills)
            fee = sum(oc.fee.taker_fee(q, p) for p, q in fills)
            legs.append(LegFill(oc.label, s, cost, fee, cost / s if s else 0.0))
            total_cost += cost
            total_fee += fee
        if not feasible:
            continue

        payout = target * s
        gross_profit = payout - total_cost
        net_profit = payout - total_cost - total_fee
        capital = total_cost + total_fee
        cand = Opportunity(
            event_id=event.event_id,
            title=event.title,
            category=event.category,
            side=side,
            n_outcomes=event.n,
            tob_basket_price=tob_basket,
            tob_target=target,
            tob_gross_edge=tob_gross,
            optimal_size=s,
            capital=capital,
            gross_profit=gross_profit,
            fee_paid=total_fee,
            net_profit=net_profit,
            net_edge_bps=(net_profit / capital * 1e4) if capital > 0 else 0.0,
            legs=legs,
            settles_immediately=(side == "NO"),
        )
        if best is None or cand.net_profit > best.net_profit:
            best = cand
    return best


def scan_event(event: NegRiskEvent) -> List[Opportunity]:
    """Return the best YES- and NO-side opportunities for an event.

    Includes results even when net_profit <= 0 so callers can study the gap
    between paper (top-of-book) edge and what survives fees + depth.
    """
    if event.n < 2 or not event.books_complete():
        return []

    out: List[Opportunity] = []
    yes_books = [o.yes_book for o in event.outcomes]
    no_books = [o.no_book for o in event.outcomes]

    yes_opp = _evaluate_side(event, yes_books, event.outcomes, target=1.0, side="YES")
    no_opp = _evaluate_side(event, no_books, event.outcomes, target=event.n - 1.0, side="NO")
    if yes_opp:
        out.append(yes_opp)
    if no_opp:
        out.append(no_opp)
    return out


def best_opportunity(event: NegRiskEvent) -> Optional[Opportunity]:
    """The single most profitable *net* opportunity, or None."""
    opps = scan_event(event)
    if not opps:
        return None
    return max(opps, key=lambda o: o.net_profit)
