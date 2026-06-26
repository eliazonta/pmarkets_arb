"""CLOB client: fetch live order books in batch.

The CLOB (clob.polymarket.com) is the authoritative source of executable prices
and depth. ``POST /books`` accepts up to many ``{token_id}`` objects per call;
we chunk to stay well within the rate limit (batch reads ~500 req/10s).
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import requests

from .orderbook import OrderBook

logger = logging.getLogger(__name__)


class ClobClient:
    BASE_URL = "https://clob.polymarket.com"

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout: int = 15,
        chunk_size: int = 100,
    ):
        self.session = session or requests.Session()
        self.session.headers.setdefault("Accept", "application/json")
        self.session.headers.setdefault("User-Agent", "pmarket-arb/2.0")
        self.timeout = timeout
        self.chunk_size = chunk_size

    def fetch_books(self, token_ids: List[str]) -> Dict[str, OrderBook]:
        """Return {token_id: OrderBook} for all requested tokens."""
        out: Dict[str, OrderBook] = {}
        for chunk in _chunks(token_ids, self.chunk_size):
            payload = [{"token_id": tid} for tid in chunk]
            try:
                r = self.session.post(
                    f"{self.BASE_URL}/books", json=payload, timeout=self.timeout
                )
                r.raise_for_status()
                for entry in r.json():
                    book = OrderBook.from_clob(entry)
                    if book.token_id:
                        out[book.token_id] = book
            except requests.RequestException as e:
                logger.error("CLOB /books failed for chunk of %d: %s", len(chunk), e)
            except (ValueError, TypeError) as e:
                logger.error("CLOB /books bad payload: %s", e)
        return out

    def fetch_book(self, token_id: str) -> Optional[OrderBook]:
        try:
            r = self.session.get(
                f"{self.BASE_URL}/book", params={"token_id": token_id}, timeout=self.timeout
            )
            r.raise_for_status()
            return OrderBook.from_clob(r.json())
        except requests.RequestException as e:
            logger.error("CLOB /book failed: %s", e)
            return None


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]
