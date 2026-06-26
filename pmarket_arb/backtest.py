"""Snapshot recorder + replayer for studying dutch-book edges over time.

Live scans are a single point in time. To say anything honest about whether an
edge is *capturable* you need to know how often it appears, how long it lasts,
and what it would have paid net of fees. This module records timestamped book
snapshots to JSONL and replays them into an opportunity time-series.

The replay PnL is deliberately conservative and labelled as such: it assumes you
could fill the optimal size once per snapshot a net-positive arb exists. It does
NOT annualise, does not net out capital constraints, and flags that consecutive
snapshots of the same standing opportunity are not independent. The honest
finding this is built to surface is usually "real net edge is rare and small",
which is a stronger interview story than a fabricated Sharpe.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, List

from .dutchbook import Opportunity, best_opportunity
from .fees import FeeSchedule
from .markets import NegRiskEvent, Outcome
from .orderbook import Level, OrderBook


# --- serialization ------------------------------------------------------
def _book_to_dict(b: OrderBook) -> dict:
    return {
        "token_id": b.token_id,
        "timestamp": b.timestamp,
        "bids": [[l.price, l.size] for l in b.bids],
        "asks": [[l.price, l.size] for l in b.asks],
    }


def _book_from_dict(d: dict) -> OrderBook:
    return OrderBook(
        token_id=d.get("token_id", ""),
        bids=[Level(p, s) for p, s in d.get("bids", [])],
        asks=[Level(p, s) for p, s in d.get("asks", [])],
        timestamp=d.get("timestamp", 0),
    )


def event_to_dict(ev: NegRiskEvent) -> dict:
    return {
        "event_id": ev.event_id,
        "title": ev.title,
        "neg_risk_market_id": ev.neg_risk_market_id,
        "category": ev.category,
        "volume24hr": ev.volume24hr,
        "outcomes": [
            {
                "label": o.label,
                "question": o.question,
                "condition_id": o.condition_id,
                "yes_token_id": o.yes_token_id,
                "no_token_id": o.no_token_id,
                "fee": {
                    "rate": o.fee.rate, "exponent": o.fee.exponent,
                    "enabled": o.fee.enabled, "category": o.fee.category,
                },
                "yes_book": _book_to_dict(o.yes_book) if o.yes_book else None,
                "no_book": _book_to_dict(o.no_book) if o.no_book else None,
            }
            for o in ev.outcomes
        ],
    }


def event_from_dict(d: dict) -> NegRiskEvent:
    outcomes = []
    for od in d.get("outcomes", []):
        f = od.get("fee", {})
        o = Outcome(
            label=od["label"], question=od.get("question", ""),
            condition_id=od.get("condition_id", ""),
            yes_token_id=od["yes_token_id"], no_token_id=od["no_token_id"],
            fee=FeeSchedule(rate=f.get("rate", 0.0), exponent=f.get("exponent", 1.0),
                            enabled=f.get("enabled", True), category=f.get("category", "")),
        )
        if od.get("yes_book"):
            o.yes_book = _book_from_dict(od["yes_book"])
        if od.get("no_book"):
            o.no_book = _book_from_dict(od["no_book"])
        outcomes.append(o)
    return NegRiskEvent(
        event_id=d.get("event_id", ""), title=d.get("title", ""),
        neg_risk_market_id=d.get("neg_risk_market_id", ""),
        category=d.get("category", ""), outcomes=outcomes,
        volume24hr=d.get("volume24hr", 0.0),
    )


# --- recording ----------------------------------------------------------
def record_snapshot(path: str, events: Iterable[NegRiskEvent], ts: int) -> None:
    """Append one timestamped snapshot of all events (with books) to JSONL."""
    line = {"ts": ts, "events": [event_to_dict(e) for e in events]}
    with open(path, "a") as f:
        f.write(json.dumps(line) + "\n")


def load_snapshots(path: str):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


# --- replay -------------------------------------------------------------
@dataclass
class ReplayStats:
    snapshots: int
    events_seen: int
    snapshots_with_arb: int
    total_net_captured: float       # conservative, see module docstring
    best_single_net: float
    mean_net_edge_bps: float        # over net-positive opportunities only
    arb_rate: float                 # fraction of (snapshot,event) pairs that were arbs


def replay(path: str) -> ReplayStats:
    snaps = 0
    pairs = 0
    arbs: List[Opportunity] = []
    snaps_with_arb = 0
    for snap in load_snapshots(path):
        snaps += 1
        had_arb = False
        for ed in snap.get("events", []):
            pairs += 1
            ev = event_from_dict(ed)
            opp = best_opportunity(ev)
            if opp and opp.is_arb:
                arbs.append(opp)
                had_arb = True
        if had_arb:
            snaps_with_arb += 1

    total_net = sum(o.net_profit for o in arbs)
    best = max((o.net_profit for o in arbs), default=0.0)
    mean_bps = (sum(o.net_edge_bps for o in arbs) / len(arbs)) if arbs else 0.0
    return ReplayStats(
        snapshots=snaps,
        events_seen=pairs,
        snapshots_with_arb=snaps_with_arb,
        total_net_captured=total_net,
        best_single_net=best,
        mean_net_edge_bps=mean_bps,
        arb_rate=(len(arbs) / pairs) if pairs else 0.0,
    )
