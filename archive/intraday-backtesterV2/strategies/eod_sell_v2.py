"""
Strategie : EOD Sell Pressure V2

V2 vs V1 : Timing raccourci + filtres ajustes pour capturer le sell-off final.
- Fenetre raccourcie : 15:00-15:50 au lieu de 14:30-15:50 (moins de faux signaux)
- Volume acceleration reduit : 1.15x au lieu de 1.3x
- EMA plus courte : 7 au lieu de 9 (plus reactif)
- Midday flat check elargi : 13:00-15:00 au lieu de 12:00-14:30
- Ajout filtre : prix doit etre dans le top 50% du range du jour
  (institution vend du haut, pas en bas)
- Tickers elargis : top 10 au lieu de 5
- Filtre supplementaire : prix doit avoir decline depuis le signal VWAP

Edge structurel :
Dans les 55 dernieres minutes de la session, les institutions executent des
programmes de vente importants. Quand un stock etait stable mais que le volume
accelere avec cassure sous EMA et VWAP, c'est de la pression vendeuse institutionnelle
tardive. Le timing serre limite le risk car la fermeture force la sortie.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import vwap
import config


# ── Parametres V2 ──
TICKERS = [
    "SPY", "QQQ", "NVDA", "AAPL", "MSFT",
    "META", "AMZN", "TSLA", "AMD", "GOOGL",
]
EMA_PERIOD = 7                  # EMA plus courte (V1 = 9)
VOLUME_ACCELERATION = 1.15      # Volume barre > 1.15x precedente (V1 = 1.3)
STOP_BUFFER_PCT = 0.0015        # Stop = high 14:30-15:00 + 0.15% (V1 = 0.1%)
MAX_TRADES_PER_DAY = 3          # Augmente (V1 = 2)
MIN_BARS = 30
MIDDAY_MAX_DECLINE = -0.003     # V2 : stock pas deja en baisse > 0.3% midday


class EODSellV2Strategy(BaseStrategy):
    name = "EOD Sell Pressure V2"

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
        return list(TICKERS)

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

            day_bars = df[df.index.date == date]
            if len(day_bars) < 20:
                continue

            # ── Calculer VWAP ──
            df_vwap = vwap(df)

            # ── Calculer EMA(7) (V2 plus reactif) ──
            df_ema = df["close"].ewm(span=self.ema_period, min_periods=self.ema_period).mean()

            # ── V2 : Performance 13:00-15:00 (le stock doit etre flat ou up) ──
            midday_bars = df.between_time("13:00", "14:59")
            if len(midday_bars) < 3:
                continue

            midday_start_price = midday_bars.iloc[0]["open"]
            midday_end_price = midday_bars.iloc[-1]["close"]
            if midday_start_price <= 0:
                continue

            midday_return = (midday_end_price - midday_start_price) / midday_start_price
            if midday_return < MIDDAY_MAX_DECLINE:
                continue

            # ── V2 : Filtre — prix doit etre dans le top 50% du range du jour ──
            day_high = day_bars["high"].max()
            day_low = day_bars["low"].min()
            day_range = day_high - day_low
            if day_range <= 0:
                continue
            mid_range = day_low + day_range * 0.5

            # ── High de 14:30-15:00 pour le stop (V2 ajuste) ──
            stop_ref_bars = df.between_time("14:30", "15:00")
            if stop_ref_bars.empty:
                # Fallback sur 14:00-15:00
                stop_ref_bars = df.between_time("14:00", "15:00")
            if stop_ref_bars.empty:
                continue
            stop_ref_high = stop_ref_bars["high"].max()

            # ── V2 : Scanner barre par barre de 15:00 a 15:50 (fenetre raccourcie) ──
            eod_bars = df.between_time("15:00", "15:50")
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

                # ── V2 : prix doit etre dans le top 50% du range ──
                # Si le stock est deja au fond, on ne short pas
                if price < mid_range:
                    continue

                # ── Condition 1 : volume en acceleration ──
                current_vol = bar["volume"]
                prev_vol = df.iloc[idx - 1]["volume"]
                if prev_vol <= 0:
                    continue
                if current_vol < prev_vol * self.volume_acceleration:
                    continue

                # ── Condition 2 : prix sous EMA(7) ──
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

                # Target : EOD close = le moteur ferme a 15:55
                # On met un target 2x risk qui ne sera probablement pas atteint
                # → la sortie sera EOD close (ce qu'on veut)
                risk = stop_loss - entry_price
                if risk <= 0:
                    continue
                take_profit = entry_price - risk * 2.0

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
                        "ema_7": round(ema_val, 2),
                        "vwap": round(vwap_val, 2),
                        "stop_ref_high": round(stop_ref_high, 2),
                        "price_in_range_pct": round((price - day_low) / day_range * 100, 1),
                    },
                ))
                signal_found = True
                trades_today += 1

        return signals
