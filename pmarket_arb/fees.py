"""Polymarket taker-fee model.

Polymarket charges fees to *takers only* (makers are never charged and may earn
a rebate). The published formula is:

    fee = C * feeRate * (p * (1 - p)) ** exponent          # USDC

where ``C`` is the number of shares filled and ``p`` is the execution price
(0-1 probability units). With the default ``exponent == 1`` this is

    fee = C * feeRate * p * (1 - p)

The key structural fact for arbitrage: the fee is symmetric in ``p`` and
**maximised at p = 0.50** -- precisely the region where dutch-book mispricings
cluster. A gross edge therefore has to clear a fee that is largest exactly where
the edge is most likely to appear.

Verified against Polymarket's own worked example (docs.polymarket.com/trading/fees):
Crypto rate 0.07, 100 shares @ p=0.50  ->  100 * 0.07 * 0.5 * 0.5 = $1.75.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeeSchedule:
    """Per-market taker fee parameters, as returned on the Gamma market object.

    Attributes mirror the SDK's ``feeSchedule`` field
    (``{rate, exponent, takerOnly, rebateRate}``).
    """

    rate: float                 # base taker fee rate, e.g. 0.05 for economics
    exponent: float = 1.0       # curvature exponent ``e``
    taker_only: bool = True     # makers never pay; here for completeness
    rebate_rate: float = 0.0    # maker rebate fraction (informational)
    enabled: bool = True
    category: str = ""          # e.g. "economics_fees" (feeType)

    @classmethod
    def from_market(cls, market: dict) -> "FeeSchedule":
        """Parse a Gamma market dict into a FeeSchedule.

        Falls back to a fee-free schedule when fees are disabled or the
        schedule is missing, so callers always get a usable object.
        """
        enabled = bool(market.get("feesEnabled", False))
        sched = market.get("feeSchedule") or {}
        if not enabled or not sched:
            return cls(rate=0.0, enabled=enabled, category=market.get("feeType", ""))
        return cls(
            rate=float(sched.get("rate", 0.0)),
            exponent=float(sched.get("exponent", 1.0)),
            taker_only=bool(sched.get("takerOnly", True)),
            rebate_rate=float(sched.get("rebateRate", 0.0)),
            enabled=enabled,
            category=market.get("feeType", ""),
        )

    def taker_fee(self, shares: float, price: float) -> float:
        """USDC taker fee for filling ``shares`` at execution ``price``."""
        if not self.enabled or self.rate <= 0.0 or shares <= 0.0:
            return 0.0
        p = min(max(price, 0.0), 1.0)
        return shares * self.rate * (p * (1.0 - p)) ** self.exponent


# Documented per-category taker rates, used only as a fallback when a market
# does not carry a live feeSchedule. Always prefer FeeSchedule.from_market.
# Source: docs.polymarket.com/trading/fees (rates change; treat as approximate).
CATEGORY_RATE_FALLBACK = {
    "crypto": 0.07,
    "economics": 0.05,
    "culture": 0.05,
    "weather": 0.05,
    "politics": 0.04,
    "finance": 0.04,
    "tech": 0.04,
    "mentions": 0.04,
    "sports": 0.03,
    "geopolitics": 0.0,   # permanently fee-free
    "world": 0.0,
}
