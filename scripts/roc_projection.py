"""ROC Projection 1 an — 5 scenarios basees sur WF+MC reels."""
import numpy as np

# Capital reel
CAPITAL_BINANCE = 23_400
CAPITAL_IBKR_TARGET = 10_000  # Cible depôt Q2 2026

# Kelly fractions
KELLY = {"AGGRESSIVE": 0.25, "NOMINAL": 0.125, "DEFENSIVE": 0.03125}

# HRP improvement (backtest: Sharpe 1.83 vs EW 1.43)
HRP_BOOST = 1.28

BUCKETS = {
    "Crypto LIVE (Binance)": {
        "capital": CAPITAL_BINANCE, "real": True,
        "strategies": [
            ("Vol Breakout", 1.2, 0.15),
            ("BTC Dom Rotation", 1.0, 0.12),
            ("Borrow Carry", 0.9, 0.20),
            ("Liquidation Mom", 1.1, 0.12),
            ("Weekend Gap", 0.85, 0.10),
            ("BTC/ETH Momentum", 0.8, 0.08),
        ],
        "cost_bps": 10, "vol": 0.25,
    },
    "FX Carry LIVE (IBKR)": {
        "capital": CAPITAL_IBKR_TARGET, "real": True,
        "strategies": [
            ("Carry Vol-Scaled", 3.59, 0.30),
            ("Carry Momentum", 2.17, 0.25),
            ("G10 Diversified", 1.61, 0.20),
            ("MR Hourly", 0.71, 0.10),
        ],
        "cost_bps": 3, "vol": 0.05,
    },
    "EU Paper (IBKR)": {
        "capital": 50_000, "real": False,
        "strategies": [
            ("BCE Press Conf", 0.79, 0.15),
            ("Sector Rotation", 0.59, 0.15),
            ("BCE Mom Drift", 2.0, 0.20),
            ("Auto Sector German", 1.5, 0.15),
            ("EU Gap Open", 1.0, 0.15),
        ],
        "cost_bps": 8, "vol": 0.12,
    },
    "US Paper (Alpaca)": {
        "capital": 100_000, "real": False,
        "strategies": [
            ("DoW Seasonal", 1.5, 0.25),
            ("Corr Regime Hedge", 1.3, 0.20),
            ("VIX Short", 1.8, 0.20),
            ("High-Beta Short", 1.0, 0.15),
        ],
        "cost_bps": 5, "vol": 0.15,
    },
    "Futures Paper (IBKR)": {
        "capital": 20_000, "real": False,
        "strategies": [
            ("MES Trend", 1.46, 0.40),
            ("MES/MNQ Pairs", 0.76, 0.35),
            ("MGC VIX", 0.45, 0.25),
        ],
        "cost_bps": 5, "vol": 0.10,
    },
}

SCENARIOS = {
    "BULL (agressif)": {
        "desc": "Marches haussiers, crypto bull, vol basse, tous les edges marchent",
        "kelly": "AGGRESSIVE", "slip": 0.8, "decay": 0.0,
        "ibkr_fund": 1.0, "crypto_mult": 1.5,
    },
    "NOMINAL (base)": {
        "desc": "Marches normaux, vol moyenne, performances WF medianes",
        "kelly": "NOMINAL", "slip": 1.0, "decay": 0.10,
        "ibkr_fund": 0.8, "crypto_mult": 1.0,
    },
    "DEFENSIF (bear)": {
        "desc": "Marches baissiers moderes, vol elevee, alpha decay 20%",
        "kelly": "DEFENSIVE", "slip": 1.5, "decay": 0.20,
        "ibkr_fund": 0.5, "crypto_mult": 0.5,
    },
    "CRASH (stress)": {
        "desc": "Flash crash Mars 2020, correlations 0.95, kill switch trigger",
        "kelly": "DEFENSIVE", "slip": 3.0, "decay": 0.40,
        "ibkr_fund": 0.3, "crypto_mult": 0.2,
    },
    "WORST CASE": {
        "desc": "Pire scenario: alpha mort, couts explosent, crypto -50%",
        "kelly": "DEFENSIVE", "slip": 5.0, "decay": 0.70,
        "ibkr_fund": 0.0, "crypto_mult": -0.3,
    },
}


def bucket_return(bucket, kelly_frac, slip_mult, alpha_decay, regime_mult=1.0):
    w_sharpe = sum(s * a for _, s, a in bucket["strategies"])
    w_sharpe *= (1 - alpha_decay) * regime_mult
    vol = bucket["vol"]
    cost = bucket["cost_bps"] / 10000 * 50 * slip_mult  # ~50 RT trades/an * slip
    gross = w_sharpe * vol * kelly_frac * HRP_BOOST
    net = gross - cost * kelly_frac
    dd = vol * 2.5 * kelly_frac
    return net, dd


def main():
    print("=" * 105)
    print("  PROJECTIONS ROC 1 AN — 5 SCENARIOS")
    print(f"  Capital reel: ${CAPITAL_BINANCE + CAPITAL_IBKR_TARGET:,.0f}"
          f" (Binance ${CAPITAL_BINANCE:,.0f} + IBKR cible ${CAPITAL_IBKR_TARGET:,.0f})")
    print(f"  19 strategies (12 VALIDATED + 7 BORDERLINE) | HRP +28% | Kelly dynamique")
    print("=" * 105)

    summary_rows = []

    for sname, sp in SCENARIOS.items():
        kf = KELLY[sp["kelly"]]
        pnl_real, pnl_paper = 0.0, 0.0
        cap_real = 0.0
        worst_dd = 0.0

        print(f"\n--- {sname} ---")
        print(f"  {sp['desc']}")
        print(f"  Kelly {sp['kelly']} ({kf:.4f}) | Slippage x{sp['slip']} | Alpha decay {sp['decay']:.0%}")
        print()
        print(f"  {'Bucket':<30s} {'Capital':>10s} {'Return':>8s} {'PnL':>10s} {'Max DD':>8s}")
        print(f"  {'-'*30} {'-'*10} {'-'*8} {'-'*10} {'-'*8}")

        for bname, b in BUCKETS.items():
            is_crypto = "Crypto" in bname
            regime = sp["crypto_mult"] if is_crypto else 1.0

            # IBKR funding timeline
            eff_cap = b["capital"]
            if "IBKR" in bname and b["real"]:
                eff_cap = b["capital"] * sp["ibkr_fund"]

            ret, dd = bucket_return(b, kf, sp["slip"], sp["decay"], regime)

            pnl = eff_cap * ret if b["real"] else b["capital"] * ret
            tag = "" if b["real"] else " (paper)"

            if b["real"]:
                pnl_real += eff_cap * ret
                cap_real += eff_cap
                worst_dd = max(worst_dd, dd)
            else:
                pnl_paper += b["capital"] * ret

            print(f"  {bname:<30s} ${eff_cap:>9,.0f} {ret:>+7.1%} ${pnl:>+9,.0f} {dd:>7.1%}")

        roc = pnl_real / cap_real if cap_real > 0 else 0
        print()
        print(f"  CAPITAL REEL:  ${cap_real:,.0f}")
        print(f"  PnL REEL:     ${pnl_real:>+,.0f}  (ROC {roc:>+.1%})")
        print(f"  PnL PAPER:    ${pnl_paper:>+,.0f}  (validation uniquement)")
        print(f"  Max DD:       {worst_dd:.1%}")

        summary_rows.append((sname, cap_real, pnl_real, roc, worst_dd, pnl_paper))

    # Summary table
    print()
    print("=" * 105)
    print("  RESUME COMPARATIF 1 AN")
    print("=" * 105)
    print(f"  {'Scenario':<25s} {'Capital':>10s} {'PnL reel':>10s} {'ROC':>8s} {'Max DD':>8s} {'PnL paper':>10s}")
    print(f"  {'-'*25} {'-'*10} {'-'*10} {'-'*8} {'-'*8} {'-'*10}")
    for row in summary_rows:
        sn, cap, pnl, roc, dd, pp = row
        print(f"  {sn:<25s} ${cap:>9,.0f} ${pnl:>+9,.0f} {roc:>+7.1%} {dd:>7.1%} ${pp:>+9,.0f}")

    print()
    print("  Hypotheses:")
    print("  - Sharpe OOS = resultats walk-forward reels (pas backtest IS)")
    print("  - HRP +28% (backteste: Sharpe 1.83 vs EW 1.43)")
    print("  - Kelly dynamique bascule auto (equity momentum)")
    print("  - Alpha decay = degradation edges (crowding, regime shift)")
    print("  - Crypto LIVE, FX LIVE (si IBKR 10K depose), reste PAPER")
    print("  - Couts: Binance 10bps, FX 3bps, EU 8bps, US 5bps, Futures 5bps")


if __name__ == "__main__":
    main()
