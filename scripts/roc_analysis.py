"""
ROC-002 : Capital Utilization Analysis.

Analyse l'utilisation du capital sur 24h en creneaux d'1h (CET).
Identifie les zones mortes (< 10% capital actif) et recommande
des strategies pour combler les trous.

Usage:
    python scripts/roc_analysis.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# =============================================================================
# STRATEGY DEFINITIONS : active hours + capital allocation
# =============================================================================

# Chaque strategie est definie par :
#   - market: marche source
#   - hours_cet: (start, end) en heures CET
#   - allocation_pct: pourcentage du capital alloue
#   - edge_type: type d'edge pour reference

STRATEGIES = {
    # --- US Intraday (11 strategies, 15:35-22:00 CET) ---
    "OpEx Gamma Pin": {
        "market": "us_equity", "hours_cet": (15, 22), "allocation_pct": 0.06,
        "edge_type": "event",
    },
    "Overnight Gap Continuation": {
        "market": "us_equity", "hours_cet": (15, 17), "allocation_pct": 0.05,
        "edge_type": "momentum",
    },
    "Gold Fear Gauge": {
        "market": "us_equity", "hours_cet": (15, 22), "allocation_pct": 0.04,
        "edge_type": "short",
    },
    "Crypto-Proxy Regime V2": {
        "market": "us_equity", "hours_cet": (15, 22), "allocation_pct": 0.04,
        "edge_type": "momentum",
    },
    "Day-of-Week Seasonal": {
        "market": "us_equity", "hours_cet": (15, 22), "allocation_pct": 0.03,
        "edge_type": "event",
    },
    "VWAP Micro-Deviation": {
        "market": "us_equity", "hours_cet": (15, 22), "allocation_pct": 0.03,
        "edge_type": "mean_reversion",
    },
    "ORB 5-Min V2": {
        "market": "us_equity", "hours_cet": (15, 16), "allocation_pct": 0.03,
        "edge_type": "momentum",
    },
    "Mean Reversion V2": {
        "market": "us_equity", "hours_cet": (15, 22), "allocation_pct": 0.03,
        "edge_type": "mean_reversion",
    },
    "Correlation Regime Hedge": {
        "market": "us_equity", "hours_cet": (15, 22), "allocation_pct": 0.03,
        "edge_type": "mean_reversion",
    },
    "Triple EMA Pullback": {
        "market": "us_equity", "hours_cet": (15, 22), "allocation_pct": 0.03,
        "edge_type": "momentum",
    },
    "Late Day Mean Reversion": {
        "market": "us_equity", "hours_cet": (20, 22), "allocation_pct": 0.03,
        "edge_type": "mean_reversion",
    },

    # --- US Daily/Monthly (3 strategies) ---
    "Momentum 25 ETFs": {
        "market": "us_equity", "hours_cet": (15, 22), "allocation_pct": 0.04,
        "edge_type": "momentum",
    },
    "Pairs MU/AMAT": {
        "market": "us_equity", "hours_cet": (15, 22), "allocation_pct": 0.02,
        "edge_type": "mean_reversion",
    },
    "VRP SVXY/SPY/TLT": {
        "market": "us_equity", "hours_cet": (15, 22), "allocation_pct": 0.01,
        "edge_type": "momentum",
    },

    # --- EU Intraday (5 strategies V5) ---
    "EU Gap Open": {
        "market": "eu_equity", "hours_cet": (9, 12), "allocation_pct": 0.06,
        "edge_type": "gap",
    },
    "BCE Momentum Drift": {
        "market": "eu_equity", "hours_cet": (13, 17), "allocation_pct": 0.06,
        "edge_type": "event",
    },
    "Auto Sector German": {
        "market": "eu_equity", "hours_cet": (9, 17), "allocation_pct": 0.05,
        "edge_type": "event",
    },
    "Brent Lag Play": {
        "market": "eu_equity", "hours_cet": (15, 20), "allocation_pct": 0.06,
        "edge_type": "momentum",
    },
    "EU Close -> US Afternoon": {
        "market": "eu_equity", "hours_cet": (15, 21), "allocation_pct": 0.05,
        "edge_type": "cross_timezone",
    },

    # --- FX Swing (7 paires, 22h-24h pool) ---
    "FX EUR/USD Trend": {
        "market": "fx", "hours_cet": (0, 24), "allocation_pct": 0.04,
        "edge_type": "momentum",
    },
    "FX EUR/GBP MR": {
        "market": "fx", "hours_cet": (8, 17), "allocation_pct": 0.02,
        "edge_type": "mean_reversion",
    },
    "FX EUR/JPY Carry": {
        "market": "fx", "hours_cet": (0, 24), "allocation_pct": 0.03,
        "edge_type": "carry",
    },
    "FX AUD/JPY Carry": {
        "market": "fx", "hours_cet": (0, 24), "allocation_pct": 0.03,
        "edge_type": "carry",
    },
    "FX GBP/USD Trend": {
        "market": "fx", "hours_cet": (8, 20), "allocation_pct": 0.02,
        "edge_type": "momentum",
    },
    "FX USD/CHF MR": {
        "market": "fx", "hours_cet": (8, 20), "allocation_pct": 0.02,
        "edge_type": "mean_reversion",
    },
    "FX NZD/USD Carry": {
        "market": "fx", "hours_cet": (0, 24), "allocation_pct": 0.02,
        "edge_type": "carry",
    },

    # --- Futures (3 strategies V5) ---
    "Futures MES Trend": {
        "market": "futures", "hours_cet": (0, 24), "allocation_pct": 0.04,
        "edge_type": "momentum",
    },
    "Futures MNQ MR": {
        "market": "futures", "hours_cet": (15, 22), "allocation_pct": 0.03,
        "edge_type": "mean_reversion",
    },
    "Brent Lag Futures": {
        "market": "futures", "hours_cet": (15, 20), "allocation_pct": 0.03,
        "edge_type": "momentum",
    },
}

# Current V1 strategies (US intraday only, for comparison)
STRATEGIES_V1 = {
    name: info for name, info in STRATEGIES.items()
    if info["market"] == "us_equity"
}


# =============================================================================
# ANALYSIS FUNCTIONS
# =============================================================================

def is_strategy_active(strategy: dict, hour: int) -> bool:
    """Determine si une strategie est active a une heure donnee (CET).

    Gere les creneaux qui traversent minuit (ex: 22h-6h).
    """
    start, end = strategy["hours_cet"]

    if start <= end:
        # Creneau normal (ex: 9-17)
        return start <= hour < end
    else:
        # Creneau qui traverse minuit (ex: 22-6)
        return hour >= start or hour < end


def calculate_hourly_utilization(strategies: dict) -> dict:
    """Calcule l'utilisation du capital par heure sur 24h.

    Returns:
        {hour: {
            active_capital: float (somme des allocations actives),
            strategies: [noms des strategies actives],
            markets: set des marches actifs,
        }}
    """
    hourly = {}
    for hour in range(24):
        active_capital = 0.0
        active_strategies = []
        active_markets = set()

        for name, info in strategies.items():
            if is_strategy_active(info, hour):
                active_capital += info["allocation_pct"]
                active_strategies.append(name)
                active_markets.add(info["market"])

        hourly[hour] = {
            "active_capital": round(active_capital, 4),
            "strategies": active_strategies,
            "markets": active_markets,
        }

    return hourly


def count_active_hours(hourly: dict, threshold: float = 0.10) -> float:
    """Compte le nombre d'heures avec capital actif > threshold."""
    return sum(1 for h in hourly.values() if h["active_capital"] >= threshold)


def find_dead_zones(hourly: dict, threshold: float = 0.10) -> list:
    """Identifie les plages horaires avec < threshold de capital actif.

    Returns:
        [(start_hour, end_hour, capital_pct), ...]
    """
    dead_zones = []
    zone_start = None

    for hour in range(25):  # 25 pour fermer la derniere zone
        h = hour % 24
        is_dead = hourly[h]["active_capital"] < threshold if hour < 24 else True

        if is_dead and zone_start is None:
            zone_start = h
        elif not is_dead and zone_start is not None:
            min_capital = min(
                hourly[hh % 24]["active_capital"]
                for hh in range(zone_start, hour)
            )
            dead_zones.append((zone_start, h, round(min_capital, 4)))
            zone_start = None

    return dead_zones


def generate_recommendations(dead_zones: list) -> list:
    """Genere des recommandations pour couvrir les zones mortes."""
    recommendations = []

    for start, end, capital in dead_zones:
        duration = (end - start) % 24
        if start >= 0 and start < 8:
            recommendations.append(
                f"  - {start:02d}:00-{end:02d}:00 CET ({duration}h, {capital:.0%} actif): "
                f"Ajouter des strategies FX Asian Session (AUD/JPY, NZD/USD, USD/JPY) "
                f"ou futures overnight (MES/MNQ overnight gap strategies)"
            )
        elif start >= 22 or start < 2:
            recommendations.append(
                f"  - {start:02d}:00-{end:02d}:00 CET ({duration}h, {capital:.0%} actif): "
                f"FX carry trades actifs 24h ou crypto-proxy overnight strategies"
            )
        else:
            recommendations.append(
                f"  - {start:02d}:00-{end:02d}:00 CET ({duration}h, {capital:.0%} actif): "
                f"Explorer des strategies sur les marches ouverts a ces heures"
            )

    return recommendations


# =============================================================================
# REPORT GENERATION
# =============================================================================

def format_strategies_short(strategies: list, max_display: int = 6) -> str:
    """Formate une liste de strategies pour affichage compact."""
    if not strategies:
        return "---"
    if len(strategies) <= max_display:
        return ", ".join(strategies)
    return ", ".join(strategies[:max_display]) + f" (+{len(strategies) - max_display})"


def generate_report(compare_v1: bool = True) -> str:
    """Genere le rapport complet d'utilisation du capital."""

    lines = []
    lines.append("")
    lines.append("=" * 80)
    lines.append("  ROC ANALYSIS - Capital Utilization (24h CET)")
    lines.append("=" * 80)
    lines.append("")

    # V5 analysis
    hourly_v5 = calculate_hourly_utilization(STRATEGIES)
    active_hours_v5 = count_active_hours(hourly_v5)
    dead_zones_v5 = find_dead_zones(hourly_v5)

    # V1 analysis (for comparison)
    hourly_v1 = calculate_hourly_utilization(STRATEGIES_V1)
    active_hours_v1 = count_active_hours(hourly_v1)

    # Summary
    total_alloc = sum(s["allocation_pct"] for s in STRATEGIES.values())
    n_strategies = len(STRATEGIES)
    n_markets = len(set(s["market"] for s in STRATEGIES.values()))

    lines.append(f"  Strategies:      {n_strategies}")
    lines.append(f"  Marches:         {n_markets} (us_equity, eu_equity, fx, futures)")
    lines.append(f"  Capital total:   {total_alloc:.0%} (+ 7% cash reserve)")
    lines.append(f"  Coverage V1:     {active_hours_v1:.0f}h/24h (US intraday only)")
    lines.append(f"  Coverage V5:     {active_hours_v5:.0f}h/24h (multi-asset)")
    lines.append("  Target:          18h/24h")
    lines.append("")

    # Hour-by-hour heatmap
    lines.append("-" * 80)
    lines.append(f"  {'Hour (CET)':<15} | {'Capital':>8} | {'Bar':<20} | Strategies")
    lines.append("-" * 80)

    for hour in range(24):
        data = hourly_v5[hour]
        cap = data["active_capital"]
        bar_len = int(cap * 40)  # Scale to 40 chars max
        bar = "#" * bar_len

        # Color coding via text markers
        if cap >= 0.50:
            marker = "[HIGH]"
        elif cap >= 0.20:
            marker = "[MED] "
        elif cap >= 0.10:
            marker = "[LOW] "
        else:
            marker = "[DEAD]"

        strats = format_strategies_short(data["strategies"])
        markets_str = "/".join(sorted(data["markets"])) if data["markets"] else "---"

        lines.append(
            f"  {hour:02d}:00-{(hour+1) % 24:02d}:00    | {cap:>6.0%}   | {bar:<20} {marker} | "
            f"{markets_str}: {strats}"
        )

    lines.append("-" * 80)
    lines.append("")

    # Coverage assessment
    if active_hours_v5 >= 18:
        status = "PASS"
    elif active_hours_v5 >= 15:
        status = "CLOSE"
    else:
        status = "FAIL"

    lines.append(f"  Coverage: {active_hours_v5:.1f}h/24h (target: 18h) {status}")
    lines.append("")

    # Dead zones
    if dead_zones_v5:
        lines.append("  Dead zones (< 10% capital actif):")
        for start, end, cap in dead_zones_v5:
            duration = (end - start) % 24
            lines.append(f"    {start:02d}:00-{end:02d}:00 CET ({duration}h, min {cap:.0%} active)")
        lines.append("")

        # Recommendations
        recommendations = generate_recommendations(dead_zones_v5)
        if recommendations:
            lines.append("  Recommandations:")
            for rec in recommendations:
                lines.append(rec)
            lines.append("")
    else:
        lines.append("  Aucune zone morte detectee.")
        lines.append("")

    # Market breakdown
    lines.append("-" * 80)
    lines.append("  BREAKDOWN PAR MARCHE")
    lines.append("-" * 80)
    market_groups = {}
    for name, info in STRATEGIES.items():
        market_groups.setdefault(info["market"], []).append((name, info))

    for market in ["us_equity", "eu_equity", "fx", "futures"]:
        strats = market_groups.get(market, [])
        total_alloc_market = sum(s[1]["allocation_pct"] for s in strats)
        hours_set = set()
        for _, info in strats:
            start, end = info["hours_cet"]
            if start <= end:
                hours_set.update(range(start, end))
            else:
                hours_set.update(range(start, 24))
                hours_set.update(range(0, end))
        lines.append(
            f"  {market:<15} | {len(strats):>2} strats | {total_alloc_market:>5.0%} capital | "
            f"{len(hours_set):>2}h active"
        )

    lines.append("")
    lines.append("=" * 80)
    lines.append("")

    return "\n".join(lines)


# =============================================================================
# MAIN
# =============================================================================

def main():
    report = generate_report()
    print(report)

    # Also save to file
    output_path = ROOT / "output" / "roc_analysis.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"  Report saved to: {output_path}")


if __name__ == "__main__":
    main()
