#!/usr/bin/env python3
"""T1-C — Crypto basis / funding carry (market-neutral).

Logique: long spot BTCUSDC + short BTCUSDT_PERP = receive funding (perps paient
les longs en regime bear, les shorts paient les longs en regime bull).

Moteur economique:
  - Daily PnL = (notional × funding_rate_daily) - (rebalance_cost / holding_days)
  - Direction neutral spot/perp => delta 0 sur le prix BTC

Approche (pragmatique, sans historique funding API):
  - Proxy funding rate via BTC 60d momentum (bull market -> funding haut)
  - Base constante 8.7%/an (mediane historique BTC 2019-2026, cf. S0bis)
  - Ajustement: +- 15%/an selon momentum (clip [-5%, +30%])

Couts (Binance):
  - Spot : 0.15% RT commission + 5 bps slippage + 5 bps spread = 25 bps
  - Perp : 0.06% RT commission + 5 bps slippage = 11 bps
  - Total setup+close : ~36 bps

Variantes:
  1. basis_carry_always : funding recu en continu, rebalance monthly
  2. basis_carry_bullish : activ seulement si BTC 60d momentum > 0
  3. basis_carry_funding_filter : activ seulement si funding_proxy > 5%/an
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from scripts.research.portfolio_marginal_score import score_candidate  # noqa: E402

BTC_PATH = ROOT / "data" / "crypto" / "candles" / "BTCUSDT_1D_LONG.parquet"
BASELINE_PATH = ROOT / "data" / "research" / "portfolio_baseline_timeseries.parquet"
MD_OUT_DIR = ROOT / "docs" / "research" / "wf_reports"
JSON_OUT_DIR = ROOT / "output" / "research" / "wf_reports"

NOTIONAL = 5_000.0   # strat capital
FUNDING_MEDIAN_ANNUAL = 0.087  # from S0bis doc
COST_SETUP_CLOSE = 0.0036      # 36 bps setup + close
REBALANCE_DAYS = 30            # monthly rebalance


def load_btc() -> pd.DataFrame:
    df = pd.read_parquet(BTC_PATH)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df.sort_index()


def funding_proxy(df: pd.DataFrame) -> pd.Series:
    """Estimated daily funding rate via BTC 60d momentum."""
    mom = df["close"].pct_change(60)  # 60d momentum
    # Base rate + momentum tilt
    annual = FUNDING_MEDIAN_ANNUAL + 1.5 * mom  # tilt: +1.5% per +1% monthly momentum
    annual = annual.clip(-0.05, 0.30)
    daily = annual / 365
    return daily.fillna(FUNDING_MEDIAN_ANNUAL / 365)


def funding_real(df: pd.DataFrame) -> pd.Series:
    """Real Binance perp funding rate, daily aggregated.

    Loads data/crypto/funding/BTCUSDT_funding_daily.parquet (downloaded by
    scripts/research/download_binance_funding.py). Aligned to df index;
    pre-2019-09 dates use proxy as fallback.
    """
    funding_path = ROOT / "data" / "crypto" / "funding" / "BTCUSDT_funding_daily.parquet"
    if not funding_path.exists():
        print(f"  WARN: {funding_path} not found, using proxy")
        return funding_proxy(df)
    f = pd.read_parquet(funding_path)
    f.index = pd.to_datetime(f.index).tz_localize(None).normalize()
    # Reindex to df index, fillna with proxy for pre-2019 dates
    proxy = funding_proxy(df)
    real_daily = f["funding_daily_sum"].reindex(df.index)
    out = real_daily.fillna(proxy)
    return out


def variant_always(df: pd.DataFrame) -> pd.Series:
    """Always-on basis carry, monthly rebalance."""
    daily_funding = funding_real(df)
    # Daily PnL = notional * daily_funding
    gross = daily_funding * NOTIONAL
    # Amortize setup+close cost over REBALANCE_DAYS
    daily_cost = (COST_SETUP_CLOSE * NOTIONAL) / REBALANCE_DAYS
    pnl = (gross - daily_cost).fillna(0)
    pnl.name = "basis_carry_always"
    return pnl


def variant_bullish_filter(df: pd.DataFrame) -> pd.Series:
    """Only active when BTC 60d momentum > 0 (bull)."""
    daily_funding = funding_real(df)
    mom = df["close"].pct_change(60)
    active = (mom > 0).shift(1).fillna(False)
    gross = (daily_funding * NOTIONAL).where(active, 0.0)
    # Cost only when active
    daily_cost = (COST_SETUP_CLOSE * NOTIONAL) / REBALANCE_DAYS
    cost = np.where(active, daily_cost, 0.0)
    pnl = (gross - cost).astype(float).fillna(0.0)
    pnl.name = "basis_carry_bullish"
    return pnl


def variant_funding_filter(df: pd.DataFrame, threshold_annual: float = 0.05) -> pd.Series:
    """Only active when funding_proxy > threshold (typically bull markets)."""
    daily_funding = funding_real(df)
    active = (daily_funding * 365 > threshold_annual).shift(1).fillna(False)
    gross = (daily_funding * NOTIONAL).where(active, 0.0)
    daily_cost = (COST_SETUP_CLOSE * NOTIONAL) / REBALANCE_DAYS
    cost = np.where(active, daily_cost, 0.0)
    pnl = (gross - cost).astype(float).fillna(0.0)
    pnl.name = f"basis_carry_funding_gt_{int(threshold_annual * 100)}pct"
    return pnl


def main():
    print("=== T1-C : Crypto basis / funding carry ===\n")
    btc = load_btc()
    print(f"BTC: {len(btc)} days, {btc.index.min().date()} -> {btc.index.max().date()}")

    baseline = pd.read_parquet(BASELINE_PATH)
    baseline.index = pd.to_datetime(baseline.index).normalize()
    print(f"Baseline (7 strats): {baseline.shape}\n")

    variants = [
        variant_always(btc),
        variant_bullish_filter(btc),
        variant_funding_filter(btc, 0.05),
        variant_funding_filter(btc, 0.10),
    ]

    print(f"{'Variant':<42s} {'Active':>8s} {'TotPnL$':>10s}")
    for v in variants:
        active = int((v != 0).sum())
        total = float(v.sum())
        print(f"{v.name:<42s} {active:>8d} {total:>+10.0f}")

    print("\n--- Scoring ---")
    scorecards = []
    for v in variants:
        try:
            sc = score_candidate(v.name, v, baseline, 10_000.0, 1.0)
            scorecards.append(sc)
            print(f"  [{sc.verdict:<20s}] {sc.candidate_id:<42s} "
                  f"score={sc.marginal_score:+.3f} dSharpe={sc.delta_sharpe:+.3f} "
                  f"dMaxDD={sc.delta_maxdd:+.2f}pp corr={sc.corr_to_portfolio:+.2f}")
        except Exception as e:
            print(f"  [SKIP] {v.name}: {e}")

    scorecards.sort(key=lambda r: r.marginal_score, reverse=True)

    JSON_OUT_DIR.mkdir(parents=True, exist_ok=True)
    (JSON_OUT_DIR / "T1-01_scorecards.json").write_text(
        json.dumps([sc.to_dict() for sc in scorecards], indent=2, default=str))

    md_lines = [
        "# T1-C — Crypto basis / funding carry",
        "",
        f"**Run date** : {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Note importante** : funding rate **approxime** via proxy (BTC 60d momentum +",
        f" base 8.7%/an). Historique funding API non telecharge. Session T1-C sera re-lancee",
        f" avec funding reel avant toute decision PROMOTE_PAPER.",
        "",
        "## Standalone stats",
        "",
        "| Variant | Active days | Total PnL $ |",
        "|---|---:|---:|",
    ]
    for v in variants:
        active = int((v != 0).sum())
        total = float(v.sum())
        md_lines.append(f"| `{v.name}` | {active} | {total:+,.0f} |")

    md_lines += [
        "",
        "## Scorecards (marginal vs 7-strat baseline)",
        "",
        "| Variant | Verdict | Score | dSharpe | dCAGR | dMaxDD | Corr |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for sc in scorecards:
        md_lines.append(
            f"| `{sc.candidate_id}` | **{sc.verdict}** | "
            f"{sc.marginal_score:+.3f} | {sc.delta_sharpe:+.3f} | "
            f"{sc.delta_cagr:+.2f}% | {sc.delta_maxdd:+.2f}pp | {sc.corr_to_portfolio:+.2f} |"
        )

    md_lines += [
        "",
        "## Caveat data",
        "",
        "Les resultats ci-dessus utilisent un funding **proxy** base sur le momentum BTC 60d",
        "et la mediane historique 8.7%/an. Une session T1-C' devra :",
        "1. Telecharger le funding historique reel via Binance API `/fapi/v1/fundingRate` BTCUSDT.",
        "2. Recomputer chaque variant avec le funding reel.",
        "3. Verifier correlation STRAT-006 `borrow_rate_carry` existant (doctrine doublon = DROP).",
        "",
    ]
    MD_OUT_DIR.mkdir(parents=True, exist_ok=True)
    (MD_OUT_DIR / "T1-01_crypto_basis_carry.md").write_text("\n".join(md_lines), encoding="utf-8")
    print(f"\nReports OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
