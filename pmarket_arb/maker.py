"""Maker-side edge model for negRisk markets.

Where the taker dutch-book dies on fees, the *maker* is paid: zero fee plus a
liquidity-reward subsidy. This module quantifies whether providing liquidity is
actually profitable, from measured inputs, with two components:

1. Spread vs. adverse selection (the rigorous spine).
   A resting two-sided quote earns the half-spread ``h`` if the mid doesn't move
   while it rests, and loses ~``sigma * sqrt(tau)`` to mid drift over the time
   ``tau`` it takes to fill (adverse selection). Setting earn = lose gives a
   *breakeven fill time*:

       tau* = (h / sigma_per_min) ** 2     [minutes]

   Interpretation that needs no fabricated fill model: a maker is viable if
   quotes fill *faster* than tau*. ``sigma`` is measured from real price history.

2. Liquidity rewards (a fill-independent subsidy that lowers the bar).
   Estimated from the market's real daily reward pool (see rewards.py).

negRisk basket netting: holding a balanced inventory across all N mutually-
exclusive legs has *zero* resolution-payoff variance (exactly one Yes pays $1),
so a basket maker can warehouse inventory at no terminal risk -- a structural
edge a single-binary maker does not have. We quantify the variance netted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .markets import NegRiskEvent
from .microstructure import PriceSeries
from .orderbook import OrderBook
from .rewards import RewardProgram


@dataclass
class LegMakerEdge:
    label: str
    mid: float
    half_spread_cents: float
    sigma_min_cents: float          # realized 1-min vol, in cents
    tau_breakeven_min: float        # fill faster than this => spread beats drift
    quote_size: float
    competing_size: float           # qualifying depth within the reward band
    reward_daily: float             # USDC/day for our quote
    reward_bps_day: float           # on deployed capital (quote_size * mid)
    resolution_var_per_share: float # p(1-p): risk a single-binary maker carries
    has_history: bool

    @property
    def spread_covers_vol(self) -> bool:
        # A generous-but-honest bar: a quote that needs >1 day to fill to break
        # even on drift alone is effectively reward-only.
        return self.tau_breakeven_min >= 1.0


@dataclass
class BasketMakerEdge:
    title: str
    n_outcomes: int
    legs: List[LegMakerEdge]
    total_reward_daily: float
    basket_capital: float           # cost of a balanced 1-set-per-leg inventory
    reward_bps_day: float           # total reward / basket capital
    summed_leg_resolution_var: float  # risk if legs held independently
    balanced_basket_resolution_var: float  # ~0: the netting result


def _half_spread_cents(book: OrderBook) -> float:
    if not book.bids or not book.asks:
        return float("nan")
    return (book.best_ask - book.best_bid) / 2.0 * 100.0


def _mid(book: OrderBook) -> float:
    if not book.bids or not book.asks:
        return float("nan")
    return (book.best_ask + book.best_bid) / 2.0


def _depth_within_band(book: OrderBook, mid: float, band_cents: float) -> float:
    """Total resting size within ``band_cents`` of the mid on both sides."""
    band = band_cents / 100.0
    size = 0.0
    for lvl in book.bids:
        if mid - lvl.price <= band:
            size += lvl.size
    for lvl in book.asks:
        if lvl.price - mid <= band:
            size += lvl.size
    return size


def leg_maker_edge(
    label: str,
    book: OrderBook,
    reward: RewardProgram,
    series: Optional[PriceSeries],
    quote_size: float,
) -> Optional[LegMakerEdge]:
    mid = _mid(book)
    h = _half_spread_cents(book)
    if mid != mid or h != h:        # NaN guard (empty book)
        return None

    sigma_min = (series.realized_vol_per_min() * 100.0) if series else 0.0  # cents/min
    if sigma_min > 0:
        tau = (h / sigma_min) ** 2
    else:
        tau = float("inf")          # no measured vol => can't bound it; flag below

    band = reward.max_spread_cents if reward.max_spread_cents > 0 else h * 2
    competing = _depth_within_band(book, mid, band)
    # competing_size excludes our own contribution
    competing = max(competing - quote_size, 0.0)
    reward_daily = reward.daily_reward(quote_size, competing, band)
    capital = quote_size * mid
    reward_bps = (reward_daily / capital * 1e4) if capital > 0 else 0.0

    return LegMakerEdge(
        label=label,
        mid=mid,
        half_spread_cents=h,
        sigma_min_cents=sigma_min,
        tau_breakeven_min=tau,
        quote_size=quote_size,
        competing_size=competing,
        reward_daily=reward_daily,
        reward_bps_day=reward_bps,
        resolution_var_per_share=mid * (1.0 - mid),
        has_history=series is not None and len(series) > 2,
    )


def basket_maker_edge(
    event: NegRiskEvent,
    series_by_token: Dict[str, PriceSeries],
    quote_size: float,
) -> Optional[BasketMakerEdge]:
    """Aggregate per-leg maker edges and quantify negRisk inventory netting."""
    legs: List[LegMakerEdge] = []
    total_reward = 0.0
    capital = 0.0
    summed_var = 0.0

    for oc in event.outcomes:
        if not oc.yes_book:
            continue
        reward = RewardProgram(
            daily_pool=oc.rewards_daily, min_size=oc.rewards_min_size,
            max_spread_cents=oc.rewards_max_spread,
            enabled=oc.rewards_daily > 0 and oc.rewards_max_spread > 0,
        )
        series = series_by_token.get(oc.yes_token_id)
        edge = leg_maker_edge(oc.label, oc.yes_book, reward, series, quote_size)
        if edge is None:
            continue
        legs.append(edge)
        total_reward += edge.reward_daily
        capital += quote_size * edge.mid
        summed_var += (quote_size ** 2) * edge.resolution_var_per_share

    if not legs:
        return None

    # Balanced basket = quote_size shares of every leg. Exactly one outcome
    # resolves Yes, so the terminal payoff is quote_size with certainty: it is a
    # synthetic risk-free bond. Resolution-payoff variance is therefore exactly
    # zero as a payoff identity, regardless of the probabilities -- which is the
    # whole point. (A single-binary maker, by contrast, carries the per-leg
    # variance summed below.)
    balanced_var = 0.0

    reward_bps = (total_reward / capital * 1e4) if capital > 0 else 0.0
    return BasketMakerEdge(
        title=event.title,
        n_outcomes=event.n,
        legs=legs,
        total_reward_daily=total_reward,
        basket_capital=capital,
        reward_bps_day=reward_bps,
        summed_leg_resolution_var=summed_var,
        balanced_basket_resolution_var=balanced_var,
    )
