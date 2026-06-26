"""Command-line interface.

Subcommands:
  scan     fetch negRisk events + live order books, report net-of-fee edges
  record   append a timestamped book snapshot to a JSONL file
  replay   replay recorded snapshots into opportunity statistics
"""

from __future__ import annotations

import argparse
import time

from rich.console import Console

from .clob import ClobClient
from .dutchbook import scan_event
from .gamma import GammaClient
from .maker import basket_maker_edge
from .microstructure import PriceHistoryClient
from . import report
from .backtest import record_snapshot, replay


def _discover(console: Console, limit: int, min_outcomes: int, max_outcomes: int,
              min_volume: float):
    """Fetch negRisk events and attach live order books."""
    gamma = GammaClient()
    clob = ClobClient()

    console.print("[dim]Discovering negRisk multi-outcome events via Gamma…[/dim]")
    events = gamma.neg_risk_events(limit=limit, min_outcomes=min_outcomes,
                                   max_outcomes=max_outcomes)
    events = [e for e in events if e.volume24hr >= min_volume]
    if not events:
        console.print("[yellow]No negRisk events matched the filters.[/yellow]")
        return []

    token_ids = [tid for e in events for tid in e.all_token_ids()]
    console.print(f"[dim]Fetching {len(token_ids)} live order books from the CLOB "
                  f"across {len(events)} events…[/dim]")
    books = clob.fetch_books(token_ids)
    ready = []
    for e in events:
        e.attach_books(books)
        if e.books_complete():
            ready.append(e)
    return ready


def cmd_scan(args, console: Console) -> None:
    events = _discover(console, args.limit, args.min_outcomes, args.max_outcomes,
                       args.min_volume)
    if not events:
        return

    opps = []
    for e in events:
        opps.extend(scan_event(e))
    if not opps:
        console.print("[yellow]No opportunities computed.[/yellow]")
        return

    report.render_summary(opps, console)

    real = sorted((o for o in opps if o.is_arb), key=lambda o: o.net_profit, reverse=True)
    if args.detail and real:
        console.print()
        for o in real[: args.detail]:
            report.render_detail(o, console)
    elif args.detail:
        # No real arb -- show the closest near-miss to make the fee story concrete.
        near = max(opps, key=lambda o: o.net_profit)
        console.print("\n[dim]No net-positive arb; showing the closest near-miss:[/dim]")
        report.render_detail(near, console)

    if args.plot:
        report.plot_fee_drag(opps, args.plot)
        console.print(f"\n[green]Saved fee-drag chart to {args.plot}[/green]")

    if args.export:
        _export_csv(opps, args.export)
        console.print(f"[green]Exported {len(opps)} rows to {args.export}[/green]")


def cmd_maker(args, console: Console) -> None:
    events = _discover(console, args.limit, args.min_outcomes, args.max_outcomes,
                       args.min_volume)
    if not events:
        return
    events = events[: args.max_events]

    hist = PriceHistoryClient()
    baskets = []
    for e in events:
        console.print(f"[dim]Pulling price history for {e.n} legs of "
                      f"“{e.title}”…[/dim]")
        series_by_token = {}
        for o in e.outcomes:
            s = hist.fetch(o.yes_token_id, interval=args.interval, fidelity=args.fidelity)
            if s:
                series_by_token[o.yes_token_id] = s
        # default quote size = the largest reward-min-size across legs (so the
        # quote qualifies for rewards on every leg) or a sane floor.
        qsize = args.quote_size or max(
            [o.rewards_min_size for o in e.outcomes] + [100.0])
        be = basket_maker_edge(e, series_by_token, quote_size=qsize)
        if be:
            baskets.append(be)

    if not baskets:
        console.print("[yellow]No maker edges computed.[/yellow]")
        return

    report.render_maker_summary(baskets, console)

    if args.detail:
        console.print()
        for b in sorted(baskets, key=lambda x: x.reward_bps_day, reverse=True)[: args.detail]:
            report.render_maker_detail(b, console)

    if args.plot:
        report.plot_maker(baskets, args.plot)
        console.print(f"\n[green]Saved maker-edge chart to {args.plot}[/green]")


def cmd_record(args, console: Console) -> None:
    iterations = max(1, args.iterations)
    for i in range(iterations):
        events = _discover(console, args.limit, args.min_outcomes, args.max_outcomes,
                           args.min_volume)
        ts = int(time.time())
        record_snapshot(args.file, events, ts)
        console.print(f"[green]snapshot {i + 1}/{iterations}: "
                      f"{len(events)} events -> {args.file}[/green]")
        if i + 1 < iterations:
            time.sleep(args.interval)


def cmd_replay(args, console: Console) -> None:
    stats = replay(args.file)
    console.print("[bold cyan]Replay statistics[/bold cyan]")
    console.print(f"  snapshots                 {stats.snapshots}")
    console.print(f"  (snapshot,event) pairs    {stats.events_seen}")
    console.print(f"  snapshots with an arb     {stats.snapshots_with_arb}")
    console.print(f"  arb rate (per pair)       {stats.arb_rate * 100:.2f}%")
    console.print(f"  mean net edge (arbs)      {stats.mean_net_edge_bps:,.0f} bps")
    console.print(f"  best single net profit    ${stats.best_single_net:,.2f}")
    console.print(f"  total net captured*       ${stats.total_net_captured:,.2f}")
    console.print("  [dim]*conservative: assumes one optimal-size fill per "
                  "net-positive snapshot; consecutive snapshots are not independent.[/dim]")


def _export_csv(opps, path: str) -> None:
    import csv

    fields = ["title", "side", "n_outcomes", "tob_basket_price", "tob_target",
              "tob_gross_edge", "optimal_size", "capital", "gross_profit",
              "fee_paid", "net_profit", "net_edge_bps", "is_arb"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for o in opps:
            w.writerow({k: getattr(o, k) for k in fields[:-1]} | {"is_arb": o.is_arb})


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pmarket-arb",
        description="Depth- and fee-aware dutch-book arbitrage scanner for "
                    "Polymarket negRisk markets.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("--limit", type=int, default=300, help="Gamma events to scan")
        sp.add_argument("--min-outcomes", type=int, default=3)
        sp.add_argument("--max-outcomes", type=int, default=24)
        sp.add_argument("--min-volume", type=float, default=50000,
                        help="min 24h event volume (USD)")

    sp = sub.add_parser("scan", help="scan live markets for net-of-fee dutch books")
    add_common(sp)
    sp.add_argument("--detail", type=int, nargs="?", const=3, default=0,
                    help="show per-leg detail for the top N arbs (or near-miss)")
    sp.add_argument("--plot", type=str, default=None, help="save fee-drag chart to path")
    sp.add_argument("--export", type=str, default=None, help="export opportunities to CSV")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("maker", help="model maker edge: reward subsidy + inventory netting")
    add_common(sp)
    sp.add_argument("--max-events", type=int, default=6,
                    help="cap events (each pulls per-leg price history)")
    sp.add_argument("--quote-size", type=float, default=None,
                    help="maker quote size in shares (default: per-event reward min size)")
    sp.add_argument("--interval", type=str, default="1d", help="price-history window")
    sp.add_argument("--fidelity", type=int, default=1, help="price-history minutes/sample")
    sp.add_argument("--detail", type=int, nargs="?", const=3, default=0,
                    help="show per-leg detail for the top N events")
    sp.add_argument("--plot", type=str, default=None, help="save maker-edge chart to path")
    sp.set_defaults(func=cmd_maker)

    sp = sub.add_parser("record", help="append a timestamped book snapshot to JSONL")
    add_common(sp)
    sp.add_argument("--file", type=str, required=True)
    sp.add_argument("--iterations", type=int, default=1)
    sp.add_argument("--interval", type=float, default=60.0, help="seconds between snapshots")
    sp.set_defaults(func=cmd_record)

    sp = sub.add_parser("replay", help="replay snapshots into opportunity statistics")
    sp.add_argument("--file", type=str, required=True)
    sp.set_defaults(func=cmd_replay)

    return p


def run_cli(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console()
    args.func(args, console)


if __name__ == "__main__":
    run_cli()
