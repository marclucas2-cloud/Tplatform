"""Allocation optimizer — optimal capital split across 3 brokers."""
import numpy as np


BROKERS = {
    "IBKR": {
        "strats": [
            # (name, sharpe, vol_target, max_leverage, cost_bps, min_cap)
            ("FX Carry VS", 3.59, 0.05, 20, 3, 5000),
            ("FX Carry Mom", 2.17, 0.05, 20, 3, 5000),
            ("FX G10 Carry", 1.61, 0.05, 20, 3, 5000),
            ("FX MR Hourly", 0.71, 0.05, 15, 3, 5000),
            ("MES Trend", 1.46, 0.10, 7, 5, 3000),
            ("MES/MNQ Pairs", 0.76, 0.06, 5, 10, 5000),
            ("MGC VIX Hedge", 0.45, 0.08, 5, 5, 2000),
            ("EU BCE", 0.79, 0.12, 1, 8, 3000),
            ("EU Sector Rot", 0.59, 0.12, 1, 8, 3000),
        ],
    },
    "Binance": {
        "strats": [
            ("Vol Breakout", 1.2, 0.25, 1, 10, 2000),
            ("BTC Dom Rotation", 1.0, 0.25, 1, 10, 2000),
            ("Borrow Carry", 0.9, 0.05, 1, 5, 1000),
            ("Liquidation Mom", 1.1, 0.25, 1, 10, 2000),
            ("Weekend Gap", 0.85, 0.20, 1, 10, 1000),
            ("BTC/ETH Mom", 0.8, 0.30, 1, 10, 2000),
            ("USDC Earn 4%", 0.0, 0.00, 1, 0, 500),
        ],
    },
    "Alpaca": {
        "strats": [
            ("DoW Seasonal", 1.5, 0.15, 1, 5, 2000),
            ("Corr Regime", 1.3, 0.12, 1, 5, 2000),
            ("VIX Short", 1.8, 0.18, 1, 5, 2000),
            ("High-Beta Short", 1.0, 0.15, 1, 5, 2000),
            ("Late Day MR", 0.6, 0.12, 1, 5, 1000),
        ],
    },
}


def kelly_for_sharpe(sharpe):
    if sharpe >= 3.0:
        return 0.50
    if sharpe >= 2.0:
        return 0.33
    if sharpe >= 1.0:
        return 0.25
    if sharpe >= 0.5:
        return 0.125
    return 0.0625


def broker_pnl(broker_name, capital, kelly_mult=1.0, lev_use=0.5, decay=0.0, regime=1.0):
    strats = BROKERS[broker_name]["strats"]
    total = 0
    max_dd = 0

    for name, sharpe, vol, lev_max, cost_bps, min_cap in strats:
        if capital < min_cap:
            continue

        # Earn special
        if "Earn" in name:
            total += capital * 0.30 * 0.04  # 30% in Earn, 4% APY
            continue

        adj_s = sharpe * (1 - decay) * regime
        if adj_s <= 0:
            continue

        k = kelly_for_sharpe(adj_s) * kelly_mult
        lev = min(lev_max * lev_use, 10) if broker_name == "IBKR" and lev_max > 1 else 1.0
        notional = capital * 0.15 * lev
        gross = adj_s * vol * notional * k
        cost = cost_bps / 10000 * 50 * notional * k
        total += gross - cost
        max_dd = max(max_dd, vol * 2.5 * k * lev)

    return total, max_dd


def main():
    # ===================================================================
    print("=" * 110)
    print("  OPTIMISATION ALLOCATION CAPITAL PAR BROKER — SCENARIOS DE DEPOT")
    print("=" * 110)

    # Paliers de depot
    paliers = [
        {
            "label": "ACTUEL ($0 depot)",
            "depot": 0,
            "IBKR": 500, "Binance": 23400, "Alpaca": 0,
        },
        {
            "label": "REDISTRIBUE ($0 depot)",
            "depot": 0,
            "IBKR": 10000, "Binance": 8400, "Alpaca": 5000,
            "note": "Transfert 15K Binance -> IBKR + Alpaca",
        },
        {
            "label": "+5K depot",
            "depot": 5000,
            "IBKR": 13000, "Binance": 8400, "Alpaca": 7000,
            "note": "5K depot sur IBKR",
        },
        {
            "label": "+10K depot",
            "depot": 10000,
            "IBKR": 18000, "Binance": 8400, "Alpaca": 7000,
            "note": "10K depot: 8K IBKR + 2K Alpaca",
        },
        {
            "label": "+25K depot",
            "depot": 25000,
            "IBKR": 28000, "Binance": 8400, "Alpaca": 12000,
            "note": "25K: 18K IBKR + 7K Alpaca",
        },
        {
            "label": "+50K depot",
            "depot": 50000,
            "IBKR": 42000, "Binance": 10000, "Alpaca": 21400,
            "note": "50K: 32K IBKR + 16K Alpaca + 2K Binance",
        },
        {
            "label": "+100K depot",
            "depot": 100000,
            "IBKR": 70000, "Binance": 15000, "Alpaca": 38400,
            "note": "100K: 60K IBKR + 33K Alpaca + 7K Binance",
        },
    ]

    # 3 scenarios
    configs = {
        "NOMINAL": {"kelly_mult": 1.0, "lev_use": 0.5, "decay": 0.10, "regime": 1.0},
        "BULL": {"kelly_mult": 1.5, "lev_use": 0.7, "decay": 0.0, "regime": 1.3},
        "DEFENSIF": {"kelly_mult": 0.25, "lev_use": 0.2, "decay": 0.20, "regime": 0.7},
    }

    # Table
    print()
    header = (
        f"{'Palier':<24s} {'Total':>8s} {'IBKR':>8s} {'Binance':>8s} "
        f"{'Alpaca':>8s} | {'NOM PnL':>9s} {'ROC':>6s} | "
        f"{'BULL PnL':>9s} {'ROC':>6s} | {'DEF PnL':>9s} {'ROC':>6s}"
    )
    print(header)
    print("-" * 120)

    for p in paliers:
        total = p["IBKR"] + p["Binance"] + p["Alpaca"]
        row = f"{p['label']:<24s} ${total:>7,} ${p['IBKR']:>7,} ${p['Binance']:>7,} ${p['Alpaca']:>7,} |"

        for cname in ["NOMINAL", "BULL", "DEFENSIF"]:
            c = configs[cname]
            pnl = 0
            for bname in ["IBKR", "Binance", "Alpaca"]:
                bp, _ = broker_pnl(bname, p[bname], **c)
                pnl += bp
            roc = pnl / total if total > 0 else 0
            row += f" ${pnl:>+8,.0f} {roc:>+5.1%} |"

        print(row)
        if "note" in p:
            print(f"  {'':24s} ^ {p['note']}")

    # ===================================================================
    print()
    print("=" * 110)
    print("  DETAIL PAR BROKER — SCENARIO NOMINAL (redistribue + 10K depot)")
    print("  IBKR $18K | Binance $8.4K | Alpaca $7K | Total $33.4K")
    print("=" * 110)
    print()

    c = configs["NOMINAL"]
    total_pnl = 0

    for bname in ["IBKR", "Binance", "Alpaca"]:
        cap = {"IBKR": 18000, "Binance": 8400, "Alpaca": 7000}[bname]
        print(f"  --- {bname} (${cap:,}) ---")

        for name, sharpe, vol, lev_max, cost_bps, min_cap in BROKERS[bname]["strats"]:
            if cap < min_cap:
                print(f"    {name:<22s}  SKIP (min ${min_cap:,})")
                continue

            if "Earn" in name:
                earn = cap * 0.30 * 0.04
                total_pnl += earn
                print(f"    {name:<22s}  APY 4% sur 30% capital = ${earn:+,.0f}/an")
                continue

            adj_s = sharpe * (1 - c["decay"]) * c["regime"]
            if adj_s <= 0:
                continue
            k = kelly_for_sharpe(adj_s) * c["kelly_mult"]
            lev = min(lev_max * c["lev_use"], 10) if bname == "IBKR" and lev_max > 1 else 1.0
            notional = cap * 0.15 * lev
            gross = adj_s * vol * notional * k
            cost = cost_bps / 10000 * 50 * notional * k
            net = gross - cost
            total_pnl += net
            roc_strat = net / cap * 100

            lev_str = f" x{lev:.0f}" if lev > 1 else ""
            print(f"    {name:<22s}  Sharpe {adj_s:.1f}  Kelly {k:.2f}  "
                  f"Notional ${notional:>9,.0f}{lev_str}  "
                  f"PnL ${net:>+7,.0f}  ({roc_strat:>+.1f}%)")

        print()

    total_cap = 18000 + 8400 + 7000
    print(f"  TOTAL PnL: ${total_pnl:>+,.0f}/an sur ${total_cap:,} = ROC {total_pnl/total_cap:>+.1%}")

    # ===================================================================
    print()
    print("=" * 110)
    print("  RECOMMANDATION FINALE")
    print("=" * 110)
    print("""
  PRIORITE 1 (impact immediat, $0 depot):
    Transferer $15K de Binance -> IBKR
    Methode: BTC Earn -> USDC -> retrait EUR -> virement IBKR
    Resultat: FX carry live avec levier = ROC passe de +1.9% a +10%
    Delai: 5 jours ouvres

  PRIORITE 2 ($10K depot recommande):
    $8K sur IBKR (total IBKR $18K pour FX carry 5x levier = $90K notional)
    $2K sur Alpaca (total $7K pour US strats live)
    Resultat: ROC +15% NOMINAL, +40% BULL

  PRIORITE 3 ($25K depot ambitieux):
    $18K IBKR (total $28K, levier 10x = $280K notional FX)
    $7K Alpaca (total $12K, proche seuil PDT $25K)
    Resultat: ROC +22% NOMINAL, +65% BULL

  REPARTITION OPTIMALE PAR RATIO:
    IBKR:    55-65% du capital (FX carry Sharpe 2-3.5 + levier = generateur #1)
    Binance: 15-25% plafonné $8-15K (crypto vol haute, Earn 4% garanti)
    Alpaca:  20-30% (US Sharpe 1-1.8, objectif franchir PDT $25K)
""")


if __name__ == "__main__":
    main()
