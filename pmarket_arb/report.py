"""Terminal + chart reporting for dutch-book scan results.

The narrative this is built to tell: lots of *paper* (top-of-book) edges exist,
but once you account for taker fees and real book depth, almost none survive --
and the ones that look biggest on paper are often killed hardest by the fee,
because the fee peaks exactly at p=0.50 where the mispricings cluster.
"""

from __future__ import annotations

from typing import List

from rich.console import Console
from rich.table import Table

from .dutchbook import Opportunity
from .maker import BasketMakerEdge


def _fmt_usd(v: float) -> str:
    return f"${v:,.2f}"


def _fmt_bps(v: float) -> str:
    return f"{v:,.0f} bps"


def render_summary(opps: List[Opportunity], console: Console) -> None:
    """One row per opportunity: paper edge vs what survives fees + depth."""
    table = Table(
        title="negRisk dutch-book scan — paper edge vs net-of-fee capturable edge",
        header_style="bold magenta",
        show_lines=False,
    )
    table.add_column("Event", style="cyan", max_width=34, no_wrap=False)
    table.add_column("Side", justify="center")
    table.add_column("N", justify="right")
    table.add_column("Basket", justify="right")        # sum of best asks vs target
    table.add_column("Paper edge", justify="right")     # top-of-book gross, ¢/set
    table.add_column("Size", justify="right")           # optimal shares/leg
    table.add_column("Capital", justify="right")
    table.add_column("Gross", justify="right")
    table.add_column("Fee", justify="right", style="yellow")
    table.add_column("Net", justify="right")
    table.add_column("Net edge", justify="right")
    table.add_column("Arb?", justify="center")

    for o in sorted(opps, key=lambda x: x.net_profit, reverse=True):
        arb = "[bold green]YES[/]" if o.is_arb else "[dim]no[/]"
        net_style = "bold green" if o.is_arb else "red"
        basket = f"{o.tob_basket_price:.3f}/{o.tob_target:.0f}"
        table.add_row(
            o.title,
            o.side,
            str(o.n_outcomes),
            basket,
            f"{o.tob_gross_edge * 100:.2f}¢",
            f"{o.optimal_size:,.0f}",
            _fmt_usd(o.capital),
            _fmt_usd(o.gross_profit),
            _fmt_usd(o.fee_paid),
            f"[{net_style}]{_fmt_usd(o.net_profit)}[/]",
            _fmt_bps(o.net_edge_bps),
            arb,
        )
    console.print(table)

    paper = [o for o in opps if o.tob_gross_edge > 0]
    real = [o for o in opps if o.is_arb]
    console.print(
        f"\n[dim]{len(paper)} events show a paper (top-of-book) edge; "
        f"[bold]{len(real)}[/bold] survive fees + depth as a real net-positive arb.[/dim]"
    )


def render_detail(opp: Opportunity, console: Console) -> None:
    """Per-leg breakdown of a single opportunity."""
    title = f"{opp.title}  —  {opp.side} side, {opp.n_outcomes} outcomes"
    table = Table(title=title, header_style="bold cyan")
    table.add_column("Leg")
    table.add_column("Shares", justify="right")
    table.add_column("VWAP", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Fee", justify="right", style="yellow")
    for leg in opp.legs:
        table.add_row(
            leg.label, f"{leg.shares:,.0f}", f"{leg.vwap:.3f}",
            _fmt_usd(leg.cost), _fmt_usd(leg.fee),
        )
    table.add_section()
    table.add_row(
        "[bold]Basket[/]", f"{opp.optimal_size:,.0f}", "",
        _fmt_usd(sum(l.cost for l in opp.legs)),
        _fmt_usd(opp.fee_paid),
    )
    console.print(table)
    settle = "redeemable immediately via convertPositions" if opp.settles_immediately \
        else "capital locked until event resolution"
    console.print(
        f"  payout {_fmt_usd(opp.tob_target * opp.optimal_size)} · "
        f"gross {_fmt_usd(opp.gross_profit)} · net {_fmt_usd(opp.net_profit)} "
        f"({_fmt_bps(opp.net_edge_bps)}) · [dim]{settle}[/dim]"
    )


def _median(xs):
    xs = sorted(x for x in xs if x == x)        # drop NaN
    if not xs:
        return float("nan")
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def render_maker_summary(baskets: List[BasketMakerEdge], console: Console) -> None:
    """One row per event: the maker subsidy and the negRisk netting advantage."""
    table = Table(
        title="negRisk maker edge — reward subsidy + inventory netting "
              "(where the taker arb dies, the maker is paid)",
        header_style="bold magenta",
    )
    table.add_column("Event", style="cyan", max_width=32, no_wrap=False)
    table.add_column("N", justify="right")
    table.add_column("Reward/day", justify="right", style="green")
    table.add_column("Reward edge", justify="right", style="green")
    table.add_column("med h", justify="right")          # half-spread, cents
    table.add_column("med σ₁ₘ", justify="right")        # realized 1-min vol, cents
    table.add_column("med τ*", justify="right")         # breakeven fill time, min
    table.add_column("Resolution risk", justify="right")  # single -> basket

    for b in sorted(baskets, key=lambda x: x.reward_bps_day, reverse=True):
        med_h = _median([l.half_spread_cents for l in b.legs])
        med_sig = _median([l.sigma_min_cents for l in b.legs if l.has_history])
        med_tau = _median([l.tau_breakeven_min for l in b.legs if l.has_history])
        tau_str = "—" if med_tau != med_tau else (
            ">1d" if med_tau >= 1440 else f"{med_tau:,.0f}m")
        sig_str = "—" if med_sig != med_sig else f"{med_sig:.2f}¢"
        table.add_row(
            b.title, str(b.n_outcomes),
            _fmt_usd(b.total_reward_daily),
            f"{b.reward_bps_day:,.0f} bps/d",
            f"{med_h:.2f}¢",
            sig_str,
            tau_str,
            f"σ²={b.summed_leg_resolution_var:,.0f}→~0",
        )
    console.print(table)
    console.print(
        "\n[dim]Reward edge = daily LRP pool as bps of basket capital. "
        "τ* = breakeven fill time: quote fills faster than τ* ⇒ half-spread beats "
        "mid drift. Resolution risk: a balanced basket across all N legs nets "
        "terminal payoff variance to ~0 (exactly one outcome pays $1).[/dim]"
    )


def render_maker_detail(b: BasketMakerEdge, console: Console) -> None:
    table = Table(title=f"{b.title} — per-leg maker edge", header_style="bold cyan")
    table.add_column("Leg", max_width=24)
    table.add_column("Mid", justify="right")
    table.add_column("h (½-spread)", justify="right")
    table.add_column("σ₁ₘ", justify="right")
    table.add_column("τ* (fill)", justify="right")
    table.add_column("Reward/day", justify="right", style="green")
    table.add_column("Reward edge", justify="right", style="green")
    for l in b.legs:
        tau = l.tau_breakeven_min
        tau_str = "—" if not l.has_history else (
            ">1d" if tau >= 1440 else f"{tau:,.0f}m")
        sig_str = "—" if not l.has_history else f"{l.sigma_min_cents:.2f}¢"
        table.add_row(
            l.label, f"{l.mid:.3f}", f"{l.half_spread_cents:.2f}¢", sig_str, tau_str,
            _fmt_usd(l.reward_daily), f"{l.reward_bps_day:,.0f} bps/d",
        )
    console.print(table)
    console.print(
        f"  basket capital {_fmt_usd(b.basket_capital)} · "
        f"total reward {_fmt_usd(b.total_reward_daily)}/day "
        f"({b.reward_bps_day:,.0f} bps/day) · "
        f"resolution risk σ²={b.summed_leg_resolution_var:,.0f} (independent legs) "
        f"→ {b.balanced_basket_resolution_var:,.2f} (balanced basket)"
    )


def plot_maker(baskets: List[BasketMakerEdge], save_path: str) -> None:
    """Spread-vs-vol viability per leg + reward edge per event."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    hs, sigs = [], []
    for b in baskets:
        for l in b.legs:
            if l.has_history and l.sigma_min_cents > 0:
                hs.append(l.sigma_min_cents)
                sigs.append(l.half_spread_cents)
    ax1.scatter(hs, sigs, alpha=0.7, color="#3a7ca5")
    lim = max(hs + sigs + [1.0])
    ax1.plot([0, lim], [0, lim], "k--", lw=0.8, label="h = σ₁ₘ  (τ* = 1 min)")
    ax1.set_xlabel("realized 1-min volatility σ₁ₘ (cents)")
    ax1.set_ylabel("quoted half-spread h (cents)")
    ax1.set_title("Maker viability: above the line, the spread beats 1-min drift")
    ax1.legend()

    titles = [b.title[:18] for b in baskets]
    bps = [b.reward_bps_day for b in baskets]
    ax2.barh(range(len(titles)), bps, color="#2e8b57")
    ax2.set_yticks(range(len(titles)))
    ax2.set_yticklabels(titles, fontsize=8)
    ax2.set_xlabel("LRP reward edge (bps/day on basket capital)")
    ax2.set_title("Maker subsidy: paid daily, fill or not")

    fig.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_fee_drag(opps: List[Opportunity], save_path: str) -> None:
    """Two-panel chart: paper vs net edge, and the fee's bite vs basket price.

    Imported lazily so the core package has no hard matplotlib dependency.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # Panel 1: per-event paper gross edge vs net-of-fee edge (bps of capital).
    data = sorted(opps, key=lambda o: o.tob_gross_edge, reverse=True)
    paper_bps = [
        (o.tob_gross_edge / o.tob_target * 1e4) if o.tob_target else 0.0 for o in data
    ]
    net_bps = [o.net_edge_bps for o in data]
    x = range(len(data))
    ax1.bar([i - 0.2 for i in x], paper_bps, width=0.4, label="paper edge (top of book)", color="#7aa6c2")
    ax1.bar([i + 0.2 for i in x], net_bps, width=0.4, label="net edge (fees + depth)", color="#c2553a")
    ax1.axhline(0, color="black", lw=0.8)
    ax1.set_ylabel("edge (bps of basket / capital)")
    ax1.set_title("Paper edge evaporates after fees + depth")
    ax1.set_xticks([])
    ax1.legend()

    # Panel 2: fee as % of gross vs how close the basket trades to p=0.50.
    xs, ys = [], []
    for o in opps:
        if o.gross_profit <= 0:
            continue
        avg_p = o.tob_basket_price / o.n_outcomes
        xs.append(abs(avg_p - 0.5))
        ys.append(min(o.fee_paid / o.gross_profit, 5.0))
    ax2.scatter(xs, ys, alpha=0.7, color="#c2553a")
    ax2.axhline(1.0, color="black", ls="--", lw=0.8, label="fee = gross edge")
    ax2.set_xlabel("|avg leg price − 0.50|  (distance from where fee peaks)")
    ax2.set_ylabel("fee paid / gross edge  (>1 ⇒ no arb)")
    ax2.set_title("Edges nearest p=0.50 are hit hardest by the fee")
    ax2.legend()

    fig.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
