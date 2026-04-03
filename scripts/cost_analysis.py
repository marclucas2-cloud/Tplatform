"""
ROC-001 : Analyse des couts par strategie.

Pour chaque strategie, calcule le ratio couts/PnL brut.
Identifie les 3 strategies ou commissions > 30% du PnL.

Usage :
    python scripts/cost_analysis.py
    python scripts/cost_analysis.py --trades-dir output/session_20260326/
    python scripts/cost_analysis.py --output output/cost_report.md

Couts modeles :
    - Commission : $0.005/share
    - Slippage : 0.02% du notional
"""

import argparse
import csv
import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

# --- Cost model ---
COMMISSION_PER_SHARE = 0.005   # $0.005/share
SLIPPAGE_PCT = 0.0002          # 0.02% du notional

# Warning threshold
COST_PNL_WARNING_THRESHOLD = 0.30  # 30%


def load_trades_from_csv(filepath: str) -> List[dict]:
    """Charge les trades depuis un fichier CSV."""
    trades = []
    try:
        with open(filepath, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                trades.append(row)
    except Exception as e:
        logger.warning("Erreur lecture %s: %s", filepath, e)
    return trades


def extract_strategy_name(filename: str) -> str:
    """Extrait le nom de la strategie depuis le nom du fichier.

    Ex: trades_gap_continuation.csv -> gap_continuation
        trades_eu_brent_lag.csv -> eu_brent_lag
    """
    name = filename.replace("trades_", "").replace(".csv", "")
    return name


def estimate_trade_costs(trade: dict) -> dict:
    """Estime les couts pour un trade individuel.

    Args:
        trade: dict avec les champs possibles :
            entry_price, exit_price, qty, notional, shares, pnl

    Returns:
        {commission, slippage, total_cost, gross_pnl, net_pnl, cost_pct}
    """
    # Extraire les champs pertinents
    qty = 0
    notional = 0
    gross_pnl = 0

    # Essayer differents noms de colonnes
    for qty_col in ["qty", "shares", "quantity", "size"]:
        if trade.get(qty_col):
            try:
                qty = abs(float(trade[qty_col]))
                break
            except (ValueError, TypeError):
                pass

    for notional_col in ["notional", "trade_value", "value"]:
        if trade.get(notional_col):
            try:
                notional = abs(float(trade[notional_col]))
                break
            except (ValueError, TypeError):
                pass

    # Si pas de notional mais entry_price + qty
    if notional == 0:
        for price_col in ["entry_price", "price", "fill_price"]:
            if trade.get(price_col):
                try:
                    price = float(trade[price_col])
                    if qty > 0:
                        notional = price * qty
                    break
                except (ValueError, TypeError):
                    pass

    # Si pas de qty mais notional + prix
    if qty == 0 and notional > 0:
        for price_col in ["entry_price", "price", "fill_price"]:
            if trade.get(price_col):
                try:
                    price = float(trade[price_col])
                    if price > 0:
                        qty = notional / price
                    break
                except (ValueError, TypeError):
                    pass

    # PnL brut
    for pnl_col in ["pnl", "profit", "gross_pnl", "net_pnl", "return"]:
        if trade.get(pnl_col):
            try:
                gross_pnl = float(trade[pnl_col])
                break
            except (ValueError, TypeError):
                pass

    # Calculer les couts
    # Commission : $0.005/share x 2 (entree + sortie)
    commission = COMMISSION_PER_SHARE * qty * 2

    # Slippage : 0.02% du notional x 2 (entree + sortie)
    slippage = SLIPPAGE_PCT * notional * 2

    total_cost = commission + slippage
    net_pnl = gross_pnl - total_cost

    # Ratio couts / PnL brut (en valeur absolue)
    if abs(gross_pnl) > 0:
        cost_pct = total_cost / abs(gross_pnl)
    else:
        cost_pct = float("inf") if total_cost > 0 else 0.0

    return {
        "commission": round(commission, 4),
        "slippage": round(slippage, 4),
        "total_cost": round(total_cost, 4),
        "gross_pnl": round(gross_pnl, 4),
        "net_pnl": round(net_pnl, 4),
        "cost_pct": round(cost_pct, 4) if cost_pct != float("inf") else float("inf"),
        "qty": qty,
        "notional": notional,
    }


def analyze_costs(trades_dir: str = "output/session_20260326/") -> dict:
    """Pour chaque strategie, calculer le ratio couts/PnL brut.

    Identifie les 3 strategies ou commissions > 30% du PnL.

    Args:
        trades_dir: repertoire contenant les fichiers trades_*.csv

    Returns:
        {
            strategies: {name: {trades, gross_pnl, total_costs, cost_ratio, ...}},
            warnings: [{strategy, cost_ratio, reason}],
            summary: {total_trades, total_gross_pnl, total_costs, avg_cost_ratio},
        }
    """
    trades_path = Path(trades_dir)
    if not trades_path.exists():
        logger.warning("Repertoire %s non trouve", trades_dir)
        return {"strategies": {}, "warnings": [], "summary": {}}

    # Scanner les fichiers trades_*.csv
    csv_files = sorted(trades_path.glob("trades_*.csv"))
    if not csv_files:
        logger.warning("Aucun fichier trades_*.csv dans %s", trades_dir)
        return {"strategies": {}, "warnings": [], "summary": {}}

    strategy_stats = {}

    for csv_file in csv_files:
        strategy_name = extract_strategy_name(csv_file.name)
        trades = load_trades_from_csv(str(csv_file))

        if not trades:
            continue

        total_commission = 0.0
        total_slippage = 0.0
        total_cost = 0.0
        total_gross_pnl = 0.0
        trade_count = len(trades)

        for trade in trades:
            costs = estimate_trade_costs(trade)
            total_commission += costs["commission"]
            total_slippage += costs["slippage"]
            total_cost += costs["total_cost"]
            total_gross_pnl += costs["gross_pnl"]

        # Ratio couts / PnL brut
        if abs(total_gross_pnl) > 0:
            cost_ratio = total_cost / abs(total_gross_pnl)
        else:
            cost_ratio = float("inf") if total_cost > 0 else 0.0

        net_pnl = total_gross_pnl - total_cost

        strategy_stats[strategy_name] = {
            "trades": trade_count,
            "gross_pnl": round(total_gross_pnl, 2),
            "total_commission": round(total_commission, 2),
            "total_slippage": round(total_slippage, 2),
            "total_costs": round(total_cost, 2),
            "net_pnl": round(net_pnl, 2),
            "cost_ratio": round(cost_ratio, 4) if cost_ratio != float("inf") else float("inf"),
            "avg_cost_per_trade": round(total_cost / trade_count, 4) if trade_count > 0 else 0,
        }

    # Warnings : strategies ou cost_ratio > 30%
    warnings = []
    for name, stats in sorted(
        strategy_stats.items(),
        key=lambda x: x[1]["cost_ratio"] if x[1]["cost_ratio"] != float("inf") else 999,
        reverse=True,
    ):
        if stats["cost_ratio"] != float("inf") and stats["cost_ratio"] > COST_PNL_WARNING_THRESHOLD:
            warnings.append({
                "strategy": name,
                "cost_ratio": stats["cost_ratio"],
                "reason": f"Couts = {stats['cost_ratio']:.0%} du PnL brut (> {COST_PNL_WARNING_THRESHOLD:.0%})",
            })
        elif stats["cost_ratio"] == float("inf"):
            warnings.append({
                "strategy": name,
                "cost_ratio": stats["cost_ratio"],
                "reason": "PnL brut = 0 mais couts > 0",
            })

    # Limiter aux 3 pires
    warnings = warnings[:3]

    # Summary
    total_trades = sum(s["trades"] for s in strategy_stats.values())
    total_gross = sum(s["gross_pnl"] for s in strategy_stats.values())
    total_costs = sum(s["total_costs"] for s in strategy_stats.values())
    avg_ratio = total_costs / abs(total_gross) if abs(total_gross) > 0 else 0.0

    summary = {
        "total_trades": total_trades,
        "total_strategies": len(strategy_stats),
        "total_gross_pnl": round(total_gross, 2),
        "total_costs": round(total_costs, 2),
        "total_net_pnl": round(total_gross - total_costs, 2),
        "avg_cost_ratio": round(avg_ratio, 4),
    }

    return {
        "strategies": strategy_stats,
        "warnings": warnings,
        "summary": summary,
    }


def generate_report(analysis: dict) -> str:
    """Genere le rapport Markdown a partir de l'analyse.

    Args:
        analysis: dict retourne par analyze_costs().

    Returns:
        str: contenu Markdown du rapport.
    """
    lines = [
        "# Analyse des Couts par Strategie",
        "",
        f"> Date : {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "> Modele : commission $0.005/share + slippage 0.02%",
        "",
        "---",
        "",
    ]

    # Summary
    s = analysis.get("summary", {})
    lines.extend([
        "## Resume",
        "",
        "| Metrique | Valeur |",
        "|----------|--------|",
        f"| Strategies analysees | {s.get('total_strategies', 0)} |",
        f"| Trades total | {s.get('total_trades', 0)} |",
        f"| PnL brut total | ${s.get('total_gross_pnl', 0):,.2f} |",
        f"| Couts totaux | ${s.get('total_costs', 0):,.2f} |",
        f"| PnL net total | ${s.get('total_net_pnl', 0):,.2f} |",
        f"| Ratio couts moyen | {s.get('avg_cost_ratio', 0):.1%} |",
        "",
    ])

    # Warnings
    warnings = analysis.get("warnings", [])
    if warnings:
        lines.extend([
            "## Alertes : Strategies a Couts Eleves",
            "",
        ])
        for w in warnings:
            ratio_str = f"{w['cost_ratio']:.0%}" if w["cost_ratio"] != float("inf") else "inf"
            lines.append(f"- **{w['strategy']}** : {w['reason']}")
        lines.append("")

    # Detail par strategie
    strategies = analysis.get("strategies", {})
    if strategies:
        lines.extend([
            "## Detail par Strategie",
            "",
            "| Strategie | Trades | PnL Brut | Commissions | Slippage | Couts Total | Ratio |",
            "|-----------|--------|----------|-------------|----------|-------------|-------|",
        ])
        for name, st in sorted(strategies.items(), key=lambda x: x[1].get("cost_ratio", 0), reverse=True):
            ratio_str = f"{st['cost_ratio']:.0%}" if st["cost_ratio"] != float("inf") else "inf"
            flag = " **!!**" if (st["cost_ratio"] != float("inf") and st["cost_ratio"] > COST_PNL_WARNING_THRESHOLD) else ""
            lines.append(
                f"| {name} | {st['trades']} | ${st['gross_pnl']:,.2f} | "
                f"${st['total_commission']:,.2f} | ${st['total_slippage']:,.2f} | "
                f"${st['total_costs']:,.2f} | {ratio_str}{flag} |"
            )
        lines.append("")

    # Recommandations
    lines.extend([
        "## Recommandations",
        "",
        "1. Strategies avec ratio > 30% : envisager d'augmenter le sizing minimum ou de reduire la frequence de trading",
        "2. Strategies avec slippage > commissions : surveiller la liquidite et le timing d'execution",
        "3. Au passage en live, mesurer le slippage reel vs estime et recalibrer",
        "",
    ])

    return "\n".join(lines)


def main():
    """Point d'entree CLI."""
    parser = argparse.ArgumentParser(description="Analyse des couts par strategie")
    parser.add_argument(
        "--trades-dir",
        default="output/session_20260326/",
        help="Repertoire des trades CSV",
    )
    parser.add_argument(
        "--output",
        default="output/cost_report.md",
        help="Fichier de sortie du rapport",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    logger.info("Analyse des couts : %s", args.trades_dir)
    analysis = analyze_costs(args.trades_dir)

    if not analysis["strategies"]:
        logger.warning("Aucune strategie trouvee dans %s", args.trades_dir)
        return

    report = generate_report(analysis)

    # Sauvegarder le rapport
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    logger.info("Rapport sauvegarde : %s", output_path)
    logger.info("")
    logger.info("=== RESUME ===")
    s = analysis["summary"]
    logger.info("Strategies : %d", s["total_strategies"])
    logger.info("Trades : %d", s["total_trades"])
    logger.info("PnL brut : $%,.2f", s["total_gross_pnl"])
    logger.info("Couts : $%,.2f (ratio moyen: %s)", s["total_costs"], f"{s['avg_cost_ratio']:.1%}")

    if analysis["warnings"]:
        logger.info("")
        logger.info("=== ALERTES ===")
        for w in analysis["warnings"]:
            logger.info("  %s : %s", w["strategy"], w["reason"])


if __name__ == "__main__":
    main()
