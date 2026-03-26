"""
OPT-2 : Earnings IV Crush (Proxy)

Edge structurel :
Avant les earnings, la volatilite implicite gonfle pour pricer le move attendu.
Apres l'annonce, l'IV s'effondre (IV crush). Si le move realise est inferieur
au move implicite, un straddle short est profitable.

Proxy backtest :
- Pour chaque earnings (4x/an par stock), calculer le gap reel :
  abs(open_next_day / close_prev_day - 1)
- Comparer au implied move moyen des mega-cap (~5%)
- Si gap < implied move : WIN (straddle short profitable)
- Si gap > implied move : LOSS
- P&L proxy : WIN = premium_received * (1 - realized/implied)
               LOSS = -(realized - implied) * notional * 0.5

Donnees : yfinance AAPL, MSFT, NVDA, META, AMZN, GOOGL, TSLA daily 5 ans
"""
import pandas as pd
import numpy as np


# Target mega-caps for earnings plays
EARNINGS_TICKERS = ["AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA"]

# Implied move estimates per ticker (avg pre-earnings IV for mega-cap, approximate)
# These are typical 1-day expected moves priced by straddles
IMPLIED_MOVES = {
    "AAPL": 0.04,    # ~4%
    "MSFT": 0.04,    # ~4%
    "NVDA": 0.07,    # ~7% (higher vol)
    "META": 0.08,    # ~8% (historically volatile earnings)
    "AMZN": 0.05,    # ~5%
    "GOOGL": 0.05,   # ~5%
    "TSLA": 0.08,    # ~8% (very volatile)
}

# Premium proxy: straddle premium ~ implied_move * stock_price * 0.8
# (80% of theoretical move captured as premium)
PREMIUM_CAPTURE_RATIO = 0.80

# Notional per trade
NOTIONAL_PER_TRADE = 5000  # $5,000 notional per straddle

INITIAL_CAPITAL = 100_000


def detect_earnings_dates(df: pd.DataFrame, ticker: str) -> list[tuple]:
    """
    Detect probable earnings dates from daily price data.
    Earnings = days with unusually large overnight gaps (>= 2%).
    Filter to keep only ~4 per year (quarterly).

    Returns list of (date_idx, gap_pct) tuples.
    """
    if len(df) < 20:
        return []

    gaps = []
    for i in range(1, len(df)):
        prev_close = df.iloc[i - 1]["close"]
        curr_open = df.iloc[i]["open"]
        if prev_close <= 0:
            continue
        gap = abs(curr_open / prev_close - 1)
        if gap >= 0.02:  # 2% minimum gap = likely earnings
            gaps.append((i, gap))

    if not gaps:
        return []

    # Keep only the top gaps per quarter (~4/year = ~20 over 5 years)
    # Group by quarter and take the largest gap in each quarter
    quarterly_gaps = {}
    for idx, gap in gaps:
        dt = df.index[idx]
        if hasattr(dt, 'date'):
            dt = dt.date() if hasattr(dt, 'date') else pd.Timestamp(dt).date()
        else:
            dt = pd.Timestamp(dt).date()
        quarter_key = (dt.year, (dt.month - 1) // 3)
        if quarter_key not in quarterly_gaps or gap > quarterly_gaps[quarter_key][1]:
            quarterly_gaps[quarter_key] = (idx, gap)

    return list(quarterly_gaps.values())


class EarningsIVCrushStrategy:
    """
    Standalone daily strategy — does NOT inherit BaseStrategy.
    Uses its own backtest logic for earnings proxy.
    """
    name = "Earnings IV Crush (Proxy)"

    def backtest(self, all_data: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """
        Run the proxy backtest on multiple tickers.

        all_data: {ticker: DataFrame with OHLCV daily}

        Returns: DataFrame of trades with pnl, date, etc.
        """
        trades = []

        for ticker in EARNINGS_TICKERS:
            if ticker not in all_data:
                continue

            df = all_data[ticker]
            if len(df) < 100:
                continue

            implied_move = IMPLIED_MOVES.get(ticker, 0.05)
            earnings_dates = detect_earnings_dates(df, ticker)

            for idx, gap_pct in earnings_dates:
                if idx < 1 or idx >= len(df):
                    continue

                # Entry: close before earnings (sell straddle)
                prev_close = df.iloc[idx - 1]["close"]
                # Exit: open after earnings (IV crush)
                post_open = df.iloc[idx]["open"]
                post_close = df.iloc[idx]["close"]

                realized_move = abs(post_open / prev_close - 1)

                # Entry timestamp
                entry_ts = df.index[idx - 1]
                exit_ts = df.index[idx]
                entry_date = entry_ts.date() if hasattr(entry_ts, 'date') else pd.Timestamp(entry_ts).date()

                # P&L calculation (proxy)
                # Premium received ~ implied_move * notional * premium_capture
                premium = implied_move * NOTIONAL_PER_TRADE * PREMIUM_CAPTURE_RATIO

                if realized_move <= implied_move:
                    # Straddle short profitable: keep premium minus delta loss
                    # Profit ~ premium * (1 - realized/implied)
                    pnl = premium * (1 - realized_move / implied_move)
                    exit_reason = "iv_crush_win"
                else:
                    # Straddle short loss: move exceeded implied
                    # Loss ~ (realized - implied) * notional * 0.5
                    pnl = -(realized_move - implied_move) * NOTIONAL_PER_TRADE * 0.5
                    exit_reason = "move_exceeded_loss"

                # Commission proxy: ~$2.60 per leg (straddle = 2 legs)
                commission = 5.20

                trades.append({
                    "ticker": ticker,
                    "date": entry_date,
                    "direction": "SHORT_STRADDLE",
                    "entry_price": round(prev_close, 2),
                    "exit_price": round(post_open, 2),
                    "shares": 1,
                    "pnl": round(pnl, 2),
                    "commission": commission,
                    "net_pnl": round(pnl - commission, 2),
                    "entry_time": entry_ts,
                    "exit_time": exit_ts,
                    "exit_reason": exit_reason,
                    "strategy": self.name,
                    "implied_move_pct": round(implied_move * 100, 2),
                    "realized_move_pct": round(realized_move * 100, 2),
                    "gap_pct": round(gap_pct * 100, 2),
                })

        return pd.DataFrame(trades)
