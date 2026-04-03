"""
Strategie : OpEx Weekly Expansion

Edge structurel :
Extension de la strategie OpEx Gamma Pin aux lundis et mercredis,
jours ou les options 0DTE (zero-day-to-expiry) SPY/QQQ expirent aussi.
Les market makers hedgent leur gamma, creant un effet d'aimant vers
les round numbers (strikes les plus liquides).

Differences vs OpEx Gamma Pin :
- Actif UNIQUEMENT lundi et mercredi (vendredi = original OpEx)
- Position size 50% du normal (confiance reduite)
- Meme logique gamma pinning : deviation > 0.3% du round number
- Stop 0.5%, target = round number

Tickers : SPY, QQQ uniquement.
Fenetre : 13:00-15:30.
Max 2 trades/jour.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal


# ── Parametres ──
DEVIATION_PCT = 0.003         # Entree quand prix devie > 0.3% du round number
STOP_PCT = 0.005              # Stop 0.5%
MAX_TRADES_PER_DAY = 2
TICKERS = ["SPY", "QQQ"]


def nearest_round_number(price: float) -> float:
    """Trouve le round number (strike probable) le plus proche."""
    if price > 500:
        step = 10
    elif price > 100:
        step = 5
    elif price > 50:
        step = 2.5
    else:
        step = 1
    return round(price / step) * step


class OpExWeeklyExpansionStrategy(BaseStrategy):
    name = "OpEx Weekly Expansion"

    def __init__(
        self,
        deviation_pct: float = DEVIATION_PCT,
        stop_pct: float = STOP_PCT,
        max_trades: int = MAX_TRADES_PER_DAY,
    ):
        self.deviation_pct = deviation_pct
        self.stop_pct = stop_pct
        self.max_trades = max_trades

    def get_required_tickers(self) -> list[str]:
        return ["SPY", "QQQ"]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        # ── UNIQUEMENT lundi (0) et mercredi (2) — vendredi = original OpEx ──
        if hasattr(date, "weekday"):
            weekday = date.weekday()
        else:
            weekday = pd.Timestamp(date).weekday()

        if weekday not in (0, 2):  # 0=Lundi, 2=Mercredi
            return signals

        trades_today = 0

        for ticker in TICKERS:
            if trades_today >= self.max_trades:
                break
            if ticker not in data:
                continue

            df = data[ticker]
            if len(df) < 20:
                continue

            # ── Calculer le magnet price : round number le plus proche du VWAP ──
            if "vwap" in df.columns and df["vwap"].notna().any():
                # Utiliser le VWAP jusqu'a l'apres-midi
                afternoon_start = df.between_time("12:00", "13:00")
                if not afternoon_start.empty:
                    anchor = afternoon_start["vwap"].dropna().iloc[-1] if afternoon_start["vwap"].notna().any() else df["close"].mean()
                else:
                    anchor = df["vwap"].dropna().iloc[-1]
            else:
                anchor = df["close"].mean()

            magnet = nearest_round_number(anchor)
            signal_found = False

            # ── Iterer barre par barre : 13:00-15:30 ──
            afternoon = df.between_time("13:00", "15:30")
            if afternoon.empty:
                continue

            for ts, bar in afternoon.iterrows():
                if signal_found:
                    break
                if trades_today >= self.max_trades:
                    break

                price = bar["close"]
                if price <= 0:
                    continue

                deviation = (price - magnet) / magnet

                # ── Prix trop loin AU-DESSUS du magnet → SHORT (mean reversion) ──
                if deviation > self.deviation_pct:
                    entry_price = price
                    stop_loss = entry_price * (1 + self.stop_pct)
                    take_profit = magnet

                    signals.append(Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "magnet_price": magnet,
                            "deviation_pct": round(deviation * 100, 3),
                            "weekday": weekday,
                            "day_name": "Monday" if weekday == 0 else "Wednesday",
                            "size_factor": 0.5,  # 50% position size
                        },
                    ))
                    trades_today += 1
                    signal_found = True

                # ── Prix trop loin EN-DESSOUS du magnet → LONG ──
                elif deviation < -self.deviation_pct:
                    entry_price = price
                    stop_loss = entry_price * (1 - self.stop_pct)
                    take_profit = magnet

                    signals.append(Signal(
                        action="LONG",
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "magnet_price": magnet,
                            "deviation_pct": round(deviation * 100, 3),
                            "weekday": weekday,
                            "day_name": "Monday" if weekday == 0 else "Wednesday",
                            "size_factor": 0.5,  # 50% position size
                        },
                    ))
                    trades_today += 1
                    signal_found = True

        return signals
