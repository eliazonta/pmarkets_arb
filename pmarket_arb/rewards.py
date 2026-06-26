"""Polymarket Liquidity Rewards Program (LRP) modelling.

Makers pay zero fee and additionally earn from a per-market daily reward pool
(``clobRewards.rewardsDailyRate``) for posting two-sided size within
``rewardsMaxSpread`` of the mid, subject to a ``rewardsMinSize`` floor. This is
the *subsidy* side of the same fee schedule that makes the taker dutch-book
unprofitable -- and it is paid for quoting, independent of whether you get filled.

Polymarket's real scoring is a (roughly quadratic) function of closeness-to-mid
and size. We use a transparent, conservative proportional-to-qualifying-size
model and label it as an approximation: your share of the pool is your size over
the total qualifying size competing within the reward band.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class RewardProgram:
    daily_pool: float          # USDC/day for this market (sum of active rates)
    min_size: float            # min order size (shares) to qualify
    max_spread_cents: float    # quotes must sit within this many cents of mid
    enabled: bool = True

    @classmethod
    def from_market(cls, market: Dict[str, Any]) -> "RewardProgram":
        pool = 0.0
        for r in (market.get("clobRewards") or []):
            try:
                pool += float(r.get("rewardsDailyRate", 0) or 0)
            except (TypeError, ValueError):
                continue
        min_size = _f(market.get("rewardsMinSize"), 0.0)
        max_spread = _f(market.get("rewardsMaxSpread"), 0.0)
        return cls(daily_pool=pool, min_size=min_size, max_spread_cents=max_spread,
                   enabled=pool > 0 and max_spread > 0)

    def qualifies(self, size: float, spread_cents_from_mid: float) -> bool:
        return (
            self.enabled
            and size >= self.min_size
            and spread_cents_from_mid <= self.max_spread_cents
        )

    def daily_reward(self, size: float, competing_size: float,
                     spread_cents_from_mid: float) -> float:
        """Estimated USDC/day for posting ``size`` against ``competing_size`` of
        other qualifying liquidity. Returns 0 if the quote does not qualify."""
        if not self.qualifies(size, spread_cents_from_mid):
            return 0.0
        denom = size + max(competing_size, 0.0)
        if denom <= 0:
            return 0.0
        return self.daily_pool * size / denom


def _f(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
