"""Gamma API client: discover and parse negRisk multi-outcome events.

Gamma (gamma-api.polymarket.com) is metadata, not the live book. We use it only
to *discover* mutually-exclusive event sets and their CLOB token ids + fee
schedules; the authoritative prices come from the CLOB order books (clob.py).

Note: ``clobTokenIds``, ``outcomes`` and ``outcomePrices`` arrive as
JSON-encoded strings and must be parsed; they are index-aligned (``[0]``=Yes).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import requests

from .fees import FeeSchedule
from .markets import NegRiskEvent, Outcome

logger = logging.getLogger(__name__)


def _parse_json_field(value: Any, default):
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return default


def _to_float(value: Any, default: float = float("nan")) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class GammaClient:
    BASE_URL = "https://gamma-api.polymarket.com"

    def __init__(self, session: Optional[requests.Session] = None, timeout: int = 15):
        self.session = session or requests.Session()
        self.session.headers.setdefault("Accept", "application/json")
        self.session.headers.setdefault("User-Agent", "pmarket-arb/2.0")
        self.timeout = timeout

    def fetch_events(self, limit: int = 200, order: str = "volume24hr") -> List[Dict[str, Any]]:
        params = {
            "closed": "false",
            "active": "true",
            "limit": limit,
            "order": order,
            "ascending": "false",
        }
        try:
            r = self.session.get(f"{self.BASE_URL}/events", params=params, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            logger.error("Gamma /events failed: %s", e)
            return []

    def neg_risk_events(
        self, limit: int = 200, min_outcomes: int = 3, max_outcomes: int = 64
    ) -> List[NegRiskEvent]:
        """Discover and parse negRisk events with a tractable number of outcomes."""
        raw = self.fetch_events(limit=limit)
        events: List[NegRiskEvent] = []
        for e in raw:
            if not e.get("negRisk"):
                continue
            markets = e.get("markets") or []
            outcomes = [self._parse_outcome(m) for m in markets]
            outcomes = [o for o in outcomes if o is not None]
            if not (min_outcomes <= len(outcomes) <= max_outcomes):
                continue
            events.append(
                NegRiskEvent(
                    event_id=str(e.get("id", "")),
                    title=(e.get("title") or "").strip(),
                    neg_risk_market_id=str(e.get("negRiskMarketID") or ""),
                    category=_category_of(e, markets),
                    outcomes=outcomes,
                    volume24hr=_to_float(e.get("volume24hr"), 0.0),
                )
            )
        return events

    @staticmethod
    def _parse_outcome(market: Dict[str, Any]) -> Optional[Outcome]:
        token_ids = _parse_json_field(market.get("clobTokenIds"), [])
        names = _parse_json_field(market.get("outcomes"), [])
        if len(token_ids) < 2 or len(names) < 2:
            return None
        # Index 0 is the "Yes" token, index 1 the "No" token.
        return Outcome(
            label=(market.get("groupItemTitle") or market.get("question") or "").strip(),
            question=(market.get("question") or "").strip(),
            condition_id=str(market.get("conditionId") or ""),
            yes_token_id=str(token_ids[0]),
            no_token_id=str(token_ids[1]),
            fee=FeeSchedule.from_market(market),
            best_bid=_to_float(market.get("bestBid")),
            best_ask=_to_float(market.get("bestAsk")),
            rewards_daily=_reward_pool(market),
            rewards_min_size=_to_float(market.get("rewardsMinSize"), 0.0),
            rewards_max_spread=_to_float(market.get("rewardsMaxSpread"), 0.0),
            tick_size=_to_float(market.get("orderPriceMinTickSize"), 0.01),
            order_min_size=_to_float(market.get("orderMinSize"), 5.0),
        )


def _reward_pool(market: Dict[str, Any]) -> float:
    """Sum the active daily reward rates from the market's clobRewards list."""
    total = 0.0
    for r in (market.get("clobRewards") or []):
        total += _to_float(r.get("rewardsDailyRate"), 0.0) if r else 0.0
    return total if total == total else 0.0  # NaN guard


def _category_of(event: Dict[str, Any], markets: List[Dict[str, Any]]) -> str:
    for m in markets:
        ft = m.get("feeType")
        if ft:
            return str(ft)
    tags = event.get("tags") or []
    if tags and isinstance(tags[0], dict):
        return str(tags[0].get("label", ""))
    return ""
