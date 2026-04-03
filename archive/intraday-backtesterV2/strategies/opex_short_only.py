"""
SHORT-6 : OpEx Short Extension

Edge : Les vendredis, les market makers hedgent leur gamma. Quand le prix
est AU-DESSUS d'un round number de > 0.3%, la pression de hedging pousse
le prix vers ce round number. C'est le meme phenomene que la strategie
OpEx Gamma Pin existante, mais uniquement cote SHORT.

Regles :
- Vendredis uniquement (weekly options expiration)
- Prix au-dessus du round number de > 0.3%
- Short vers le round number (mean reversion)
- Tickers : SPY, QQQ, TSLA (les plus liquides pour les options)
- Timing 13:00-15:30 ET (pic de gamma pinning)
- SL = 0.5%. TP = round number.
"""
import pandas as pd
import numpy as np
from datetime import time as dt_time, date as dt_date
from backtest_engine import BaseStrategy, Signal


# Target tickers (most liquid options)
OPEX_TICKERS = ["SPY", "QQQ", "TSLA"]

# Thresholds
DEVIATION_MIN_PCT = 0.003     # > 0.3% above round number
STOP_PCT = 0.005              # 0.5%
ENTRY_START = dt_time(13, 0)
ENTRY_END = dt_time(15, 30)
MAX_TRADES_PER_DAY = 2


def nearest_round_number(price: float) -> float:
    """Find the nearest round number (likely strike price)."""
    if price > 500:
        step = 10
    elif price > 100:
        step = 5
    elif price > 50:
        step = 2.5
    else:
        step = 1
    return round(price / step) * step


class OpExShortOnlyStrategy(BaseStrategy):
    name = "OpEx Short Extension"

    def get_required_tickers(self) -> list[str]:
        return OPEX_TICKERS

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        # Only trade on Fridays (weekly options expiration)
        if date.weekday() != 4:
            return []

        signals = []

        for ticker in OPEX_TICKERS:
            if ticker not in data:
                continue

            df = data[ticker]
            if len(df) < 20:
                continue

            # Look at afternoon bars (13:00 - 15:30 ET)
            afternoon = df.between_time(
                ENTRY_START.strftime("%H:%M"),
                ENTRY_END.strftime("%H:%M"),
            )
            if afternoon.empty:
                continue

            # Calculate anchor price (VWAP or mean close of the day)
            if "vwap" in df.columns and df["vwap"].notna().any():
                anchor = df["vwap"].dropna().iloc[-1]
            else:
                anchor = df["close"].mean()

            magnet = nearest_round_number(anchor)
            signal_found = False

            for ts, bar in afternoon.iterrows():
                if signal_found:
                    break

                deviation = (bar["close"] - magnet) / magnet

                # SHORT only: price ABOVE round number by > 0.3%
                if deviation > DEVIATION_MIN_PCT:
                    entry_price = bar["close"]
                    stop = entry_price * (1 + STOP_PCT)
                    target = magnet  # Target = round number

                    signals.append(Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=stop,
                        take_profit=target,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "magnet_price": magnet,
                            "deviation_pct": round(deviation * 100, 3),
                            "is_friday": True,
                        },
                    ))
                    signal_found = True

        # Limit trades per day
        return signals[:MAX_TRADES_PER_DAY]
