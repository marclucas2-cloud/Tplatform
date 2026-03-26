"""
Strategie : VWAP Micro Crypto

Edge structurel :
Adaptation du VWAP Micro-Deviation Reversion aux crypto-proxies.
Ces titres ont 2-3x plus de volatilite que les large caps classiques,
ce qui necessite des seuils plus larges pour eviter les faux signaux
tout en capturant des reverts plus genereux.

Modifications vs VWAPMicroReversionStrategy :
- ENTRY_SD = 1.8 (vs 1.2) — plus large pour le bruit crypto
- STOP_SD = 3.0 (vs 2.0) — stop plus large
- TARGET_SD = 0.5 (vs 0.3) — target un peu plus ambitieux
- RSI seuils : 35/65 (vs 40/60) — plus larges
- MIN_VOLUME = 50_000 (vs 20_000) — crypto-proxies ont un gros volume
- Tickers : COIN, MARA, MSTR, RIOT
- Max 2 trades/jour
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import rsi


# ── Parametres adaptes aux crypto-proxies ──
VWAP_LOOKBACK = 20
ENTRY_SD = 1.8             # Plus large pour le bruit crypto
STOP_SD = 3.0              # Stop plus large
TARGET_SD = 0.5            # Target plus ambitieux
RSI_PERIOD = 14
RSI_CONFIRM_LOW = 35       # Plus large (vs 40)
RSI_CONFIRM_HIGH = 65      # Plus large (vs 60)
MIN_PRICE = 5.0
MAX_TRADES_PER_DAY = 2
MIN_VOLUME = 50_000

CRYPTO_TICKERS = ["COIN", "MARA", "MSTR", "RIOT"]


class VWAPMicroCryptoStrategy(BaseStrategy):
    name = "VWAP Micro Crypto"

    def get_required_tickers(self) -> list[str]:
        return ["COIN", "MARA", "MSTR", "RIOT"]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        trades_today = 0

        for ticker in CRYPTO_TICKERS:
            if trades_today >= MAX_TRADES_PER_DAY:
                break
            if ticker not in data:
                continue

            df = data[ticker]
            if len(df) < VWAP_LOOKBACK + 15:
                continue

            close = df["close"]
            if close.iloc[0] < MIN_PRICE:
                continue

            # ── Pre-calculer indicateurs ──
            rsi_vals = rsi(close, RSI_PERIOD)

            # ── Rolling VWAP (micro, 20 barres ~1h40) ──
            typical_price = (df["high"] + df["low"] + df["close"]) / 3
            tp_vol = typical_price * df["volume"]
            cum_tp_vol = tp_vol.rolling(VWAP_LOOKBACK, min_periods=VWAP_LOOKBACK).sum()
            cum_vol = df["volume"].rolling(VWAP_LOOKBACK, min_periods=VWAP_LOOKBACK).sum()
            r_vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
            deviation = close - r_vwap
            r_std = deviation.rolling(VWAP_LOOKBACK, min_periods=VWAP_LOOKBACK).std()

            signal_found = False

            # ── Iterer barre par barre (10:30-15:30) ──
            tradeable = df.between_time("10:30", "15:30")
            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break
                if trades_today >= MAX_TRADES_PER_DAY:
                    break

                idx = df.index.get_loc(ts)
                if idx < VWAP_LOOKBACK + 5:
                    continue

                vwap_now = r_vwap.iloc[idx]
                std_now = r_std.iloc[idx]
                rsi_now = rsi_vals.iloc[idx]

                if pd.isna(vwap_now) or pd.isna(std_now) or std_now == 0 or pd.isna(rsi_now):
                    continue

                if bar["volume"] < MIN_VOLUME:
                    continue

                entry_price = bar["close"]
                zscore = (entry_price - vwap_now) / std_now

                # ── LONG : prix tres en dessous du VWAP micro + RSI survente ──
                if zscore < -ENTRY_SD and rsi_now < RSI_CONFIRM_LOW:
                    stop = vwap_now - STOP_SD * std_now
                    target = vwap_now - TARGET_SD * std_now

                    signals.append(Signal(
                        action="LONG",
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=stop,
                        take_profit=target,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "zscore": round(zscore, 2),
                            "rsi": round(rsi_now, 1),
                            "volume": int(bar["volume"]),
                        },
                    ))
                    trades_today += 1
                    signal_found = True

                # ── SHORT : prix tres au-dessus du VWAP micro + RSI surachat ──
                elif zscore > ENTRY_SD and rsi_now > RSI_CONFIRM_HIGH:
                    stop = vwap_now + STOP_SD * std_now
                    target = vwap_now + TARGET_SD * std_now

                    signals.append(Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=stop,
                        take_profit=target,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "zscore": round(zscore, 2),
                            "rsi": round(rsi_now, 1),
                            "volume": int(bar["volume"]),
                        },
                    ))
                    trades_today += 1
                    signal_found = True

        return signals
