"""
Strategie : EOD Sell Pressure

Edge structurel :
Dans les 90 dernieres minutes de la session, les institutions executent des
programmes de vente importants. Quand un stock etait flat/up entre 12:00-14:30
mais que le volume commence a augmenter avec le prix qui casse l'EMA(9) et
passe sous le VWAP, c'est un signal de pression vendeuse institutionnelle.

Regles :
- Tickers : SPY, QQQ, NVDA, AAPL, MSFT
- A 14:30 : si le stock etait flat/up entre 12:00-14:30
- Volume barre > 1.3x la barre precedente (acceleration)
- Prix casse sous EMA(9)
- Prix sous VWAP
- SHORT
- Stop : high de 14:00-14:30 + 0.1%
- Target : 15:55 (EOD close)
- Max 2 trades/jour
- Fenetre : 14:30-15:50
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import vwap


# ── Parametres ──
TICKERS = ["SPY", "QQQ", "NVDA", "AAPL", "MSFT"]
EMA_PERIOD = 9
VOLUME_ACCELERATION = 1.3   # Volume barre > 1.3x precedente
STOP_BUFFER_PCT = 0.001     # Stop = high 14:00-14:30 + 0.1%
MAX_TRADES_PER_DAY = 2
MIN_BARS = 30


class EODSellPressureStrategy(BaseStrategy):
    name = "EOD Sell Pressure"

    def __init__(
        self,
        ema_period: int = EMA_PERIOD,
        volume_acceleration: float = VOLUME_ACCELERATION,
        max_trades_per_day: int = MAX_TRADES_PER_DAY,
    ):
        self.ema_period = ema_period
        self.volume_acceleration = volume_acceleration
        self.max_trades_per_day = max_trades_per_day

    def get_required_tickers(self) -> list[str]:
        return ["SPY", "QQQ", "NVDA", "AAPL", "MSFT"]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        trades_today = 0

        for ticker in TICKERS:
            if trades_today >= self.max_trades_per_day:
                break
            if ticker not in data:
                continue

            df = data[ticker]
            if len(df) < MIN_BARS:
                continue

            open_price = df.iloc[0]["open"]
            if open_price <= 0:
                continue

            # ── Calculer VWAP ──
            df_vwap = vwap(df)

            # ── Calculer EMA(9) ──
            df_ema = df["close"].ewm(span=self.ema_period, min_periods=self.ema_period).mean()

            # ── Performance 12:00-14:30 (le stock doit etre flat ou up) ──
            midday_bars = df.between_time("12:00", "14:29")
            if len(midday_bars) < 5:
                continue

            midday_start_price = midday_bars.iloc[0]["open"]
            midday_end_price = midday_bars.iloc[-1]["close"]
            if midday_start_price <= 0:
                continue

            midday_return = (midday_end_price - midday_start_price) / midday_start_price
            if midday_return < -0.002:  # Deja en baisse > 0.2% → pas notre setup
                continue

            # ── High de 14:00-14:30 pour le stop ──
            stop_ref_bars = df.between_time("14:00", "14:30")
            if stop_ref_bars.empty:
                continue
            stop_ref_high = stop_ref_bars["high"].max()

            # ── Scanner barre par barre de 14:30 a 15:50 ──
            eod_bars = df.between_time("14:30", "15:50")
            if eod_bars.empty:
                continue

            signal_found = False

            for ts, bar in eod_bars.iterrows():
                if signal_found:
                    break
                if trades_today >= self.max_trades_per_day:
                    break

                idx = df.index.get_loc(ts)
                if idx < self.ema_period + 1:
                    continue

                price = bar["close"]
                if price <= 0:
                    continue

                # ── Condition 1 : volume en acceleration ──
                current_vol = bar["volume"]
                prev_vol = df.iloc[idx - 1]["volume"]
                if prev_vol <= 0:
                    continue
                if current_vol < prev_vol * self.volume_acceleration:
                    continue

                # ── Condition 2 : prix sous EMA(9) ──
                ema_val = df_ema.iloc[idx]
                if pd.isna(ema_val) or price >= ema_val:
                    continue

                # ── Condition 3 : prix sous VWAP ──
                vwap_val = df_vwap.iloc[idx] if idx < len(df_vwap) else np.nan
                if pd.isna(vwap_val) or price >= vwap_val:
                    continue

                # ── Signal SHORT ──
                entry_price = price
                stop_loss = stop_ref_high * (1 + STOP_BUFFER_PCT)

                # Target = close 15:55 (EOD) — on met un target fictif large
                # Le moteur forcera la sortie a 15:55 de toute facon
                risk = stop_loss - entry_price
                if risk <= 0:
                    continue
                take_profit = entry_price - risk * 2.0  # Target 2x risk (EOD close)

                signals.append(Signal(
                    action="SHORT",
                    ticker=ticker,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    timestamp=ts,
                    metadata={
                        "strategy": self.name,
                        "midday_return_pct": round(midday_return * 100, 2),
                        "vol_acceleration": round(current_vol / prev_vol, 2),
                        "ema_9": round(ema_val, 2),
                        "vwap": round(vwap_val, 2),
                        "stop_ref_high": round(stop_ref_high, 2),
                    },
                ))
                signal_found = True
                trades_today += 1

        return signals
