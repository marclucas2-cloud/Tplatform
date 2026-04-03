"""
Backtest CUSTOM overnight — 3 strategies.

IMPORTANT : Le backtest engine standard (backtest_engine.py) force la cloture
a 15:55 ET, ce qui est incompatible avec les strategies overnight (hold du close
au open du lendemain). Ce script implemente son propre moteur.

Logique :
  - Entree : close de la barre 15:50 ET (proxy du close reel ~15:55-16:00)
  - Sortie : open de la barre 09:30 ET du jour suivant (proxy du open reel)
  - Slippage : 0.05% (plus eleve que intraday car gaps overnight)
  - Commission : $0.005/share
  - Capital : $100,000 — chaque strategie utilise 3% ($3,000) par trade

Strategies :
  B1. Overnight Simple SPY — achat systematique SPY close, vente open J+1
  B2. Overnight Sector Winner — meilleur secteur vs SPY > 0.5%, achat close
  B3. Overnight Short Bear — short SPY en regime bear, close -> open J+1

Donnees : barres 5M parquet resamplees en daily (close 15:50, open 9:30).
"""
import os
import sys
import json
import pandas as pd
import numpy as np
from datetime import datetime, date
from pathlib import Path

# ── Config ──
INITIAL_CAPITAL = 100_000
POSITION_SIZE_PCT = 0.03          # 3% du capital = $3,000
COMMISSION_PER_SHARE = 0.005      # $0.005/share
SLIPPAGE_PCT = 0.0005             # 0.05% slippage overnight (gaps)
DATA_DIR = os.path.join(os.path.dirname(__file__), "data_cache")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output", "session_20260326")

# Tickers necessaires
SPY = "SPY"
SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLU", "XLC", "XLRE"]
ALL_TICKERS = [SPY] + SECTOR_ETFS


# ============================================================
#  DATA LOADING : 5M parquet -> daily OHLC + close_1550 + open_0930
# ============================================================

def find_latest_parquet(ticker: str) -> str:
    """Trouve le fichier parquet le plus recent pour un ticker (couverture max)."""
    candidates = []
    for f in os.listdir(DATA_DIR):
        if f.startswith(f"{ticker}_5Min_") and f.endswith(".parquet"):
            candidates.append(os.path.join(DATA_DIR, f))
    if not candidates:
        return None
    # Prendre le fichier qui couvre la plus longue periode (date debut la plus ancienne)
    candidates.sort()  # tri alphabetique -> date debut la plus ancienne en premier
    return candidates[0]


def load_5m_bars(ticker: str) -> pd.DataFrame:
    """Charge les barres 5M depuis le parquet le plus complet."""
    path = find_latest_parquet(ticker)
    if path is None:
        print(f"  [WARN] Pas de donnees pour {ticker}")
        return pd.DataFrame()
    df = pd.read_parquet(path)
    if df.empty:
        return df
    print(f"  [OK] {ticker}: {len(df)} barres 5M, {df.index[0].date()} -> {df.index[-1].date()}")
    return df


def build_daily_ohlc(df_5m: pd.DataFrame) -> pd.DataFrame:
    """
    Resample les barres 5M en daily avec :
      - open  : open de la barre 09:30
      - high  : max des barres RTH (09:30-16:00)
      - low   : min des barres RTH
      - close : close de la barre 15:50 (derniere barre complete avant 15:55)
      - close_1550 : close de la barre 15:50 (prix d'entree overnight)
      - open_0930  : open de la barre 09:30 (prix de sortie overnight)
      - volume : somme RTH
    """
    # Filtrer RTH : 09:30 - 16:00 ET
    rth = df_5m.between_time("09:30", "15:55")
    if rth.empty:
        return pd.DataFrame()

    rows = []
    for d, day_df in rth.groupby(rth.index.date):
        if len(day_df) < 10:
            continue

        # Open a 9:30
        bars_930 = day_df.between_time("09:30", "09:30")
        if bars_930.empty:
            open_930 = day_df.iloc[0]["open"]
        else:
            open_930 = bars_930.iloc[0]["open"]

        # Close a 15:50
        bars_1550 = day_df.between_time("15:50", "15:50")
        if bars_1550.empty:
            # Fallback : derniere barre avant 15:55
            bars_late = day_df.between_time("15:45", "15:55")
            if bars_late.empty:
                close_1550 = day_df.iloc[-1]["close"]
            else:
                close_1550 = bars_late.iloc[-1]["close"]
        else:
            close_1550 = bars_1550.iloc[-1]["close"]

        rows.append({
            "date": d,
            "open": day_df.iloc[0]["open"],
            "high": day_df["high"].max(),
            "low": day_df["low"].min(),
            "close": close_1550,
            "close_1550": close_1550,
            "open_0930": open_930,
            "volume": day_df["volume"].sum(),
        })

    if not rows:
        return pd.DataFrame()

    daily = pd.DataFrame(rows).set_index("date").sort_index()
    return daily


def load_all_daily() -> dict:
    """Charge et resample toutes les donnees necessaires."""
    print("\n[DATA] Chargement des donnees 5M et resample en daily...")
    all_data = {}
    for ticker in ALL_TICKERS:
        df_5m = load_5m_bars(ticker)
        if df_5m.empty:
            continue
        daily = build_daily_ohlc(df_5m)
        if not daily.empty:
            all_data[ticker] = daily
    print(f"\n[DATA] {len(all_data)} tickers charges")
    return all_data


# ============================================================
#  METRICS
# ============================================================

def compute_metrics(trades: list, strategy_name: str) -> dict:
    """Calcule Sharpe, WR, PF, max DD, etc."""
    if not trades:
        return {
            "strategy": strategy_name,
            "trades": 0,
            "sharpe": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "max_dd_pct": 0,
            "total_pnl": 0,
            "avg_pnl": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "expectancy": 0,
            "status": "NO_TRADES",
        }

    df = pd.DataFrame(trades)
    net_pnls = df["net_pnl"].values
    total_pnl = net_pnls.sum()
    n_trades = len(df)

    # Win rate
    wins = net_pnls[net_pnls > 0]
    losses = net_pnls[net_pnls <= 0]
    win_rate = len(wins) / n_trades if n_trades > 0 else 0

    # Profit factor
    gross_profit = wins.sum() if len(wins) > 0 else 0
    gross_loss = abs(losses.sum()) if len(losses) > 0 else 0.01
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.0

    # Sharpe (annualise — overnight = ~1 trade/jour)
    if len(net_pnls) >= 2 and net_pnls.std() > 0:
        daily_sharpe = net_pnls.mean() / net_pnls.std()
        sharpe = daily_sharpe * np.sqrt(252)
    else:
        sharpe = 0

    # Max drawdown sur equity curve
    equity = INITIAL_CAPITAL + np.cumsum(net_pnls)
    running_max = np.maximum.accumulate(equity)
    drawdowns = (equity - running_max) / running_max
    max_dd_pct = abs(drawdowns.min()) * 100

    # Avg trade
    avg_pnl = net_pnls.mean()
    avg_win = wins.mean() if len(wins) > 0 else 0
    avg_loss = losses.mean() if len(losses) > 0 else 0

    # Expectancy
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss

    # Status
    status = "PASS" if (sharpe > 0.5 and profit_factor > 1.2 and n_trades >= 15 and max_dd_pct < 10) else "FAIL"

    return {
        "strategy": strategy_name,
        "trades": n_trades,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(avg_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "win_rate": round(win_rate * 100, 1),
        "profit_factor": round(profit_factor, 2),
        "sharpe": round(sharpe, 2),
        "max_dd_pct": round(max_dd_pct, 2),
        "expectancy": round(expectancy, 2),
        "status": status,
    }


def print_results(metrics: dict):
    """Affichage console des resultats."""
    status_icon = "PASS" if metrics["status"] == "PASS" else "FAIL"
    print(f"\n{'='*60}")
    print(f"  {metrics['strategy']}  [{status_icon}]")
    print(f"{'='*60}")
    print(f"  Trades     : {metrics['trades']}")
    print(f"  Total PnL  : ${metrics['total_pnl']:+,.2f}")
    print(f"  Avg PnL    : ${metrics['avg_pnl']:+,.2f}")
    print(f"  Avg Win    : ${metrics['avg_win']:+,.2f}")
    print(f"  Avg Loss   : ${metrics['avg_loss']:+,.2f}")
    print(f"  Win Rate   : {metrics['win_rate']:.1f}%")
    print(f"  Profit F.  : {metrics['profit_factor']:.2f}")
    print(f"  Sharpe     : {metrics['sharpe']:.2f}")
    print(f"  Max DD     : {metrics['max_dd_pct']:.2f}%")
    print(f"  Expectancy : ${metrics['expectancy']:+,.2f}")


# ============================================================
#  OVERNIGHT BACKTEST ENGINE
# ============================================================

def execute_overnight_trade(
    ticker: str,
    direction: str,         # "LONG" or "SHORT"
    entry_date: date,       # date J
    exit_date: date,        # date J+1
    entry_price: float,     # close 15:50 de J
    exit_price: float,      # open 09:30 de J+1
    capital: float,
    metadata: dict = None,
) -> dict:
    """
    Simule un trade overnight avec slippage et commissions.
    Retourne le dict du trade.
    """
    # Position sizing : 3% du capital
    position_dollars = capital * POSITION_SIZE_PCT
    shares = int(position_dollars / entry_price)
    if shares < 1:
        return None

    # Slippage
    if direction == "LONG":
        actual_entry = entry_price * (1 + SLIPPAGE_PCT)
        actual_exit = exit_price * (1 - SLIPPAGE_PCT)
        pnl = (actual_exit - actual_entry) * shares
    else:  # SHORT
        actual_entry = entry_price * (1 - SLIPPAGE_PCT)
        actual_exit = exit_price * (1 + SLIPPAGE_PCT)
        pnl = (actual_entry - actual_exit) * shares

    # Commission aller-retour
    commission = 2 * shares * COMMISSION_PER_SHARE
    net_pnl = pnl - commission

    trade = {
        "ticker": ticker,
        "direction": direction,
        "entry_date": str(entry_date),
        "exit_date": str(exit_date),
        "entry_price": round(actual_entry, 4),
        "exit_price": round(actual_exit, 4),
        "shares": shares,
        "pnl": round(pnl, 2),
        "commission": round(commission, 2),
        "net_pnl": round(net_pnl, 2),
        "return_pct": round((net_pnl / (actual_entry * shares)) * 100, 4),
    }
    if metadata:
        trade.update(metadata)

    return trade


# ============================================================
#  B1. OVERNIGHT SIMPLE SPY
# ============================================================

def run_overnight_simple_spy(data: dict) -> list:
    """
    Achat SPY a close chaque jour, vente a open le lendemain.
    FILTRE : ATR(20) > 2.5% = skip. Vendredi = skip.
    """
    print("\n[B1] Running Overnight Simple SPY...")

    if SPY not in data:
        print("  [SKIP] SPY non disponible")
        return []

    df = data[SPY].copy()
    dates = list(df.index)

    # Calculer ATR(20) daily
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr_20 = tr.rolling(20, min_periods=20).mean()
    df["atr_20"] = atr_20
    df["atr_pct"] = df["atr_20"] / df["close"]

    trades = []
    capital = INITIAL_CAPITAL

    for i in range(20, len(dates) - 1):
        d = dates[i]
        d_next = dates[i + 1]
        row = df.loc[d]

        # Skip vendredi (weekday 4)
        weekday = pd.Timestamp(d).weekday()
        if weekday == 4:
            continue

        # Skip si ATR(20) > 2.5%
        atr_pct = row["atr_pct"]
        if pd.isna(atr_pct) or atr_pct > 0.025:
            continue

        entry_price = row["close_1550"]
        exit_price = df.loc[d_next, "open_0930"]

        if entry_price <= 0 or exit_price <= 0:
            continue

        trade = execute_overnight_trade(
            ticker=SPY,
            direction="LONG",
            entry_date=d,
            exit_date=d_next,
            entry_price=entry_price,
            exit_price=exit_price,
            capital=capital,
            metadata={
                "strategy": "Overnight Simple SPY",
                "atr_pct": round(atr_pct * 100, 3),
                "weekday": weekday,
            },
        )
        if trade:
            trades.append(trade)
            capital += trade["net_pnl"]

    print(f"  Trades: {len(trades)}")
    return trades


# ============================================================
#  B2. OVERNIGHT SECTOR WINNER
# ============================================================

def run_overnight_sector_winner(data: dict) -> list:
    """
    A 15:45 (proxy: performance intraday du jour), trouver le sector ETF
    qui surperforme SPY > 0.5%. Acheter a close, vendre a open J+1.
    FILTRE : aucun secteur > 0.5% = skip. Vendredi = skip.
    Max 1 position.
    """
    print("\n[B2] Running Overnight Sector Winner...")

    if SPY not in data:
        print("  [SKIP] SPY non disponible")
        return []

    df_spy = data[SPY]
    dates = list(df_spy.index)

    # Pre-calculer les performances intraday pour chaque ETF
    sector_data = {}
    for etf in SECTOR_ETFS:
        if etf in data:
            sector_data[etf] = data[etf]

    if not sector_data:
        print("  [SKIP] Aucun ETF sectoriel disponible")
        return []

    print(f"  Secteurs disponibles: {list(sector_data.keys())}")

    trades = []
    capital = INITIAL_CAPITAL

    for i in range(len(dates) - 1):
        d = dates[i]
        d_next = dates[i + 1]

        # Skip vendredi
        weekday = pd.Timestamp(d).weekday()
        if weekday == 4:
            continue

        # Performance intraday SPY (open -> close_1550)
        spy_row = df_spy.loc[d]
        spy_open = spy_row["open"]
        spy_close = spy_row["close_1550"]
        if spy_open <= 0 or spy_close <= 0:
            continue
        spy_perf = (spy_close - spy_open) / spy_open

        # Trouver le meilleur secteur
        best_etf = None
        best_relative_perf = 0

        for etf, df_etf in sector_data.items():
            if d not in df_etf.index or d_next not in df_etf.index:
                continue

            etf_row = df_etf.loc[d]
            etf_open = etf_row["open"]
            etf_close = etf_row["close_1550"]
            if etf_open <= 0 or etf_close <= 0:
                continue

            etf_perf = (etf_close - etf_open) / etf_open
            relative_perf = etf_perf - spy_perf

            if relative_perf > 0.005 and relative_perf > best_relative_perf:
                best_relative_perf = relative_perf
                best_etf = etf

        if best_etf is None:
            continue

        # Trade le meilleur secteur
        entry_price = sector_data[best_etf].loc[d, "close_1550"]
        exit_price = sector_data[best_etf].loc[d_next, "open_0930"]

        if entry_price <= 0 or exit_price <= 0:
            continue

        etf_perf_val = (entry_price - sector_data[best_etf].loc[d, "open"]) / sector_data[best_etf].loc[d, "open"]

        trade = execute_overnight_trade(
            ticker=best_etf,
            direction="LONG",
            entry_date=d,
            exit_date=d_next,
            entry_price=entry_price,
            exit_price=exit_price,
            capital=capital,
            metadata={
                "strategy": "Overnight Sector Winner",
                "sector": best_etf,
                "relative_perf_pct": round(best_relative_perf * 100, 3),
                "spy_perf_pct": round(spy_perf * 100, 3),
                "etf_perf_pct": round(etf_perf_val * 100, 3),
                "weekday": weekday,
            },
        )
        if trade:
            trades.append(trade)
            capital += trade["net_pnl"]

    print(f"  Trades: {len(trades)}")
    return trades


# ============================================================
#  B3. OVERNIGHT SHORT BEAR
# ============================================================

def run_overnight_short_bear(data: dict) -> list:
    """
    REGIME BEAR obligatoire : SPY < SMA(200) daily.
    SPY en baisse > 0.3% aujourd'hui.
    SHORT SPY a close, cover a open J+1.
    FILTRE : regime != BEAR = skip. SPY en hausse = skip. Vendredi = skip. ATR > 3% = skip.

    ADAPTATION : Avec seulement ~250 jours de donnees, SMA(200) ne laisse que ~50 jours
    utiles (et le marche 2025-2026 est haussier => quasi 0 jour bear sur SMA200).
    On utilise donc un DUAL-SMA regime detector :
      - SMA(50) en regime primaire (bear si SPY < SMA50)
      - SMA(20) < SMA(50) en confirmation (trend baissier confirme)
    Le lookback demarre a 50, ce qui laisse ~200 jours utiles.
    En production avec 2+ ans de donnees, remplacer par SMA(200).
    """
    print("\n[B3] Running Overnight Short Bear...")

    if SPY not in data:
        print("  [SKIP] SPY non disponible")
        return []

    df = data[SPY].copy()
    dates = list(df.index)

    # SMA(50) + SMA(20) pour regime bear (adaptation donnees limitees)
    df["sma_50"] = df["close"].rolling(50, min_periods=50).mean()
    df["sma_20"] = df["close"].rolling(20, min_periods=20).mean()

    # ATR(20) daily
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    df["atr_20"] = tr.rolling(20, min_periods=20).mean()
    df["atr_pct"] = df["atr_20"] / df["close"]

    # Performance intraday
    df["day_return"] = (df["close"] - df["open"]) / df["open"]

    trades = []
    capital = INITIAL_CAPITAL
    bear_days = 0
    signal_days = 0

    for i in range(50, len(dates) - 1):
        d = dates[i]
        d_next = dates[i + 1]
        row = df.loc[d]

        # Skip vendredi
        weekday = pd.Timestamp(d).weekday()
        if weekday == 4:
            continue

        # REGIME BEAR : SPY < SMA(50) ET SMA(20) < SMA(50) (trend confirme)
        sma_50 = row["sma_50"]
        sma_20 = row["sma_20"]
        if pd.isna(sma_50) or pd.isna(sma_20):
            continue
        if row["close"] >= sma_50:
            continue  # Pas en regime bear
        if sma_20 >= sma_50:
            continue  # Trend pas confirme baissier
        bear_days += 1

        # SPY en baisse > 0.3%
        day_ret = row["day_return"]
        if pd.isna(day_ret) or day_ret >= -0.003:
            continue

        # ATR > 3% = skip
        atr_pct = row["atr_pct"]
        if pd.isna(atr_pct) or atr_pct > 0.03:
            continue

        signal_days += 1

        entry_price = row["close_1550"]
        exit_price = df.loc[d_next, "open_0930"]

        if entry_price <= 0 or exit_price <= 0:
            continue

        trade = execute_overnight_trade(
            ticker=SPY,
            direction="SHORT",
            entry_date=d,
            exit_date=d_next,
            entry_price=entry_price,
            exit_price=exit_price,
            capital=capital,
            metadata={
                "strategy": "Overnight Short Bear",
                "day_return_pct": round(day_ret * 100, 3),
                "sma_50": round(sma_50, 2),
                "sma_20": round(sma_20, 2),
                "atr_pct": round(atr_pct * 100, 3),
                "weekday": weekday,
            },
        )
        if trade:
            trades.append(trade)
            capital += trade["net_pnl"]

    print(f"  Bear days (SPY < SMA50 & SMA20 < SMA50): {bear_days}")
    print(f"  Signal days (all filters): {signal_days}")
    print(f"  Trades: {len(trades)}")
    return trades


# ============================================================
#  MAIN
# ============================================================

def main():
    print("=" * 70)
    print("  OVERNIGHT BACKTEST CUSTOM — 3 strategies")
    print(f"  Capital: ${INITIAL_CAPITAL:,.0f}")
    print(f"  Position size: {POSITION_SIZE_PCT*100:.0f}% (${INITIAL_CAPITAL * POSITION_SIZE_PCT:,.0f})")
    print(f"  Slippage: {SLIPPAGE_PCT*100:.2f}%")
    print(f"  Commission: ${COMMISSION_PER_SHARE}/share")
    print("=" * 70)

    # 1. Charger les donnees
    data = load_all_daily()

    if SPY not in data:
        print("\n[FATAL] SPY non disponible — impossible de backtester")
        sys.exit(1)

    spy_dates = data[SPY].index
    print(f"\n[INFO] Periode: {spy_dates[0]} -> {spy_dates[-1]} ({len(spy_dates)} jours)")

    # 2. Executer les 3 strategies
    results_all = {}

    # B1
    trades_b1 = run_overnight_simple_spy(data)
    metrics_b1 = compute_metrics(trades_b1, "Overnight Simple SPY")
    print_results(metrics_b1)
    results_all["overnight_simple_spy"] = metrics_b1

    # B2
    trades_b2 = run_overnight_sector_winner(data)
    metrics_b2 = compute_metrics(trades_b2, "Overnight Sector Winner")
    print_results(metrics_b2)
    results_all["overnight_sector_winner"] = metrics_b2

    # B3
    trades_b3 = run_overnight_short_bear(data)
    metrics_b3 = compute_metrics(trades_b3, "Overnight Short Bear")
    print_results(metrics_b3)
    results_all["overnight_short_bear"] = metrics_b3

    # 3. Export
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # CSV des trades
    for name, trades in [
        ("overnight_simple_spy", trades_b1),
        ("overnight_sector_winner", trades_b2),
        ("overnight_short_bear", trades_b3),
    ]:
        if trades:
            df_trades = pd.DataFrame(trades)
            csv_path = os.path.join(OUTPUT_DIR, f"trades_{name}_custom.csv")
            df_trades.to_csv(csv_path, index=False)
            print(f"\n[EXPORT] {csv_path}")

    # JSON des resultats
    json_path = os.path.join(OUTPUT_DIR, "overnight_results.json")
    with open(json_path, "w") as f:
        json.dump(results_all, f, indent=2)
    print(f"\n[EXPORT] {json_path}")

    # 4. Resume final
    print("\n" + "=" * 70)
    print("  RESUME OVERNIGHT BACKTEST")
    print("=" * 70)
    print(f"  {'Strategy':<30} {'Trades':>6} {'PnL':>10} {'Sharpe':>7} {'WR':>6} {'PF':>6} {'DD':>6} {'Status'}")
    print(f"  {'-'*28:<30} {'-'*6:>6} {'-'*10:>10} {'-'*7:>7} {'-'*6:>6} {'-'*6:>6} {'-'*6:>6} {'-'*4}")

    for key, m in results_all.items():
        print(f"  {m['strategy']:<30} {m['trades']:>6} ${m['total_pnl']:>+9,.2f} {m['sharpe']:>7.2f} {m['win_rate']:>5.1f}% {m['profit_factor']:>6.2f} {m['max_dd_pct']:>5.2f}% {m['status']}")

    print("=" * 70)

    # Validation
    passing = [m for m in results_all.values() if m["status"] == "PASS"]
    print(f"\n  {len(passing)}/{len(results_all)} strategies passent les criteres")
    print(f"  (Sharpe > 0.5, PF > 1.2, trades >= 15, DD < 10%)")

    return results_all


if __name__ == "__main__":
    main()
