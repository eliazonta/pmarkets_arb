"""pmarket-arb: depth- and fee-aware dutch-book scanner for Polymarket negRisk markets."""

from .dutchbook import Opportunity, best_opportunity, scan_event
from .fees import FeeSchedule
from .maker import BasketMakerEdge, LegMakerEdge, basket_maker_edge, leg_maker_edge
from .markets import NegRiskEvent, Outcome
from .microstructure import PriceHistoryClient, PriceSeries
from .orderbook import Level, OrderBook
from .rewards import RewardProgram

__version__ = "2.0.0"

__all__ = [
    "Opportunity", "scan_event", "best_opportunity",
    "FeeSchedule", "NegRiskEvent", "Outcome", "OrderBook", "Level",
    "RewardProgram", "PriceSeries", "PriceHistoryClient",
    "LegMakerEdge", "BasketMakerEdge", "leg_maker_edge", "basket_maker_edge",
]
