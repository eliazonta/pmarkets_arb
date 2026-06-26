"""Microstructure measurement from real CLOB price history.

The maker question is "is the spread wide enough to pay me for the risk I take
while my quote rests?" Answering it honestly requires *measured* short-horizon
volatility, not an assumption. This module pulls Polymarket's ``prices-history``
endpoint and derives:

  * realized per-minute volatility of the mid (in price/probability units), and
  * a markout curve -- the average signed and absolute price move k minutes after
    a reference point -- which is the empirical fingerprint of adverse selection.

Prices are 0-1 probability; a move of 0.01 is one cent of PnL per share.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


@dataclass
class PriceSeries:
    token_id: str
    times: List[int]          # unix seconds
    prices: List[float]       # 0-1
    bar_seconds: int          # nominal seconds per sample (fidelity * 60)

    def __len__(self) -> int:
        return len(self.prices)

    def returns(self) -> List[float]:
        """First differences of price (additive, since price is a probability)."""
        return [self.prices[i + 1] - self.prices[i] for i in range(len(self.prices) - 1)]

    def realized_vol_per_min(self) -> float:
        """Standard deviation of mid moves, normalised to a 1-minute horizon.

        Each sample spans ``bar_seconds``; variance scales linearly with time, so
        per-minute sigma = sigma_per_bar / sqrt(bar_minutes).
        """
        diffs = self.returns()
        if len(diffs) < 2:
            return 0.0
        mean = sum(diffs) / len(diffs)
        var = sum((d - mean) ** 2 for d in diffs) / (len(diffs) - 1)
        sigma_bar = math.sqrt(var)
        bar_min = max(self.bar_seconds / 60.0, 1e-9)
        return sigma_bar / math.sqrt(bar_min)

    def markout_curve(self, horizons_min: List[int]) -> Dict[int, Tuple[float, float]]:
        """For each horizon k (minutes), return (mean signed move, mean |move|).

        Sampled in bar units; k is rounded to the nearest number of bars.
        """
        bar_min = max(self.bar_seconds / 60.0, 1e-9)
        out: Dict[int, Tuple[float, float]] = {}
        for k in horizons_min:
            step = max(1, round(k / bar_min))
            moves = [
                self.prices[i + step] - self.prices[i]
                for i in range(len(self.prices) - step)
            ]
            if not moves:
                out[k] = (0.0, 0.0)
                continue
            signed = sum(moves) / len(moves)
            absolute = sum(abs(m) for m in moves) / len(moves)
            out[k] = (signed, absolute)
        return out


class PriceHistoryClient:
    BASE_URL = "https://clob.polymarket.com"

    def __init__(self, session: Optional[requests.Session] = None, timeout: int = 15):
        self.session = session or requests.Session()
        self.session.headers.setdefault("Accept", "application/json")
        self.session.headers.setdefault("User-Agent", "pmarket-arb/2.0")
        self.timeout = timeout

    def fetch(self, token_id: str, interval: str = "1d", fidelity: int = 1) -> Optional[PriceSeries]:
        """Fetch a price series. ``fidelity`` is minutes per sample."""
        params = {"market": token_id, "interval": interval, "fidelity": fidelity}
        try:
            r = self.session.get(f"{self.BASE_URL}/prices-history", params=params, timeout=self.timeout)
            r.raise_for_status()
            hist = r.json().get("history", [])
        except (requests.RequestException, ValueError, AttributeError) as e:
            logger.error("prices-history failed for %s: %s", token_id[:12], e)
            return None
        if not hist:
            return None
        times = [int(pt["t"]) for pt in hist]
        prices = [float(pt["p"]) for pt in hist]
        return PriceSeries(token_id, times, prices, bar_seconds=fidelity * 60)
