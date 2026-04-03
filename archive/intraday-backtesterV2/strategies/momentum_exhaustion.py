"""
Strategie 5 : Momentum Exhaustion

Edge structurel :
Quand le momentum s'est etendu (prix loin de VWAP + RSI extreme + volume
qui decline), le mouvement est epuise et un mean reversion est probable.
On identifie l'epuisement via 3 confirmations simultanees :
  1. Prix eloigne du VWAP (> 1.5 ecarts-types)
  2. RSI extreme (< 25 pour oversold, > 75 pour overbought)
  3. Volume declinant (barre actuelle < 0.7x moyenne 20 barres)

Regles :
- LONG reversal : prix < VWAP - 1.5*SD, RSI < 25, volume seche
- SHORT reversal : prix > VWAP + 1.5*SD, RSI > 75, volume seche
- Stop : 2x ATR(14) en 5min
- Target : retour au VWAP
- Timing : 10:30-15:00 ET
- Filtre : ADX < 50, move du jour < 5%, >= 30 barres
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import vwap as calc_vwap, rsi, adx
import config


# Tickers cibles (focus volatils)
FOCUS_TICKERS = ["NVDA", "TSLA", "AMD", "META", "COIN", "MARA"]

# Seuils
VWAP_SD_MULT = 1.5        # Multiplicateur ecart-type VWAP
RSI_OVERSOLD = 25          # RSI long threshold
RSI_OVERBOUGHT = 75        # RSI short threshold
VOLUME_DRY_RATIO = 0.7     # Volume barre < 0.7x moyenne = sellers/buyers epuises
VOLUME_LOOKBACK = 20        # Barres pour moyenne volume
ATR_PERIOD = 14             # Periode ATR pour le stop
STOP_ATR_MULT = 2.0        # Stop = 2x ATR
MAX_ADX = 50               # Skip si trend trop fort
MAX_DAY_MOVE_PCT = 5.0     # Skip si mouvement > 5% (event driven)
MIN_BARS = 30              # Minimum barres dans le jour


class MomentumExhaustionStrategy(BaseStrategy):
    name = "Momentum Exhaustion"

    def get_required_tickers(self) -> list[str]:
        """Focus sur les tickers volatils, mais accepte tout l'univers."""
        return FOCUS_TICKERS + [config.BENCHMARK]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        traded_tickers = set()

        for ticker, df in data.items():
            if ticker == config.BENCHMARK:
                continue
            if len(df) < MIN_BARS:
                continue
            if ticker in traded_tickers:
                continue

            df = df.copy()

            # --- Filtre : mouvement du jour > 5% = event driven, skip ---
            day_open = df.iloc[0]["open"]
            if day_open <= 0:
                continue
            day_high = df["high"].max()
            day_low = df["low"].min()
            day_range_pct = (day_high - day_low) / day_open * 100
            if day_range_pct > MAX_DAY_MOVE_PCT:
                continue

            # --- Calculer indicateurs ---
            df["vwap_val"] = calc_vwap(df)
            df["rsi_val"] = rsi(df["close"], period=14)
            df["adx_val"] = adx(df, period=ATR_PERIOD)

            # Ecart-type du prix par rapport au VWAP (rolling sur les barres du jour)
            df["price_vwap_diff"] = df["close"] - df["vwap_val"]
            df["vwap_sd"] = df["price_vwap_diff"].expanding().std()

            # ATR en 5min (True Range)
            df["tr"] = pd.concat([
                df["high"] - df["low"],
                (df["high"] - df["close"].shift(1)).abs(),
                (df["low"] - df["close"].shift(1)).abs(),
            ], axis=1).max(axis=1)
            df["atr"] = df["tr"].rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()

            # Volume moyenne mobile
            df["vol_avg"] = df["volume"].rolling(VOLUME_LOOKBACK, min_periods=10).mean()

            # --- Scanner les barres dans la fenetre 10:30-15:00 ---
            tradeable = df.between_time("10:30", "15:00")

            for i in range(1, len(tradeable)):
                if ticker in traded_tickers:
                    break

                ts = tradeable.index[i]
                bar = tradeable.iloc[i]

                # Verifier que les indicateurs sont valides
                vwap_val = bar["vwap_val"]
                rsi_val = bar["rsi_val"]
                adx_val = bar["adx_val"]
                vwap_sd = bar["vwap_sd"]
                atr_val = bar["atr"]
                vol_avg = bar["vol_avg"]

                if any(pd.isna(v) for v in [vwap_val, rsi_val, adx_val, vwap_sd, atr_val, vol_avg]):
                    continue

                # Filtre ADX : skip si trend trop fort
                if adx_val > MAX_ADX:
                    continue

                # Filtre ecart-type : eviter division par zero
                if vwap_sd <= 0:
                    continue

                price = bar["close"]
                vol = bar["volume"]

                # Condition volume : barre actuelle < 0.7x moyenne (volume qui seche)
                volume_dry = vol < (VOLUME_DRY_RATIO * vol_avg)

                # Distance en ecarts-types du VWAP
                z_vwap = (price - vwap_val) / vwap_sd

                # Stop-loss base sur ATR
                stop_distance = STOP_ATR_MULT * atr_val

                # --- LONG reversal (sell-off epuise) ---
                if z_vwap < -VWAP_SD_MULT and rsi_val < RSI_OVERSOLD and volume_dry:
                    stop_loss = price - stop_distance
                    take_profit = vwap_val  # Retour au VWAP

                    # Verifier que le target est realiste (au-dessus de l'entree)
                    if take_profit <= price:
                        continue

                    signals.append(Signal(
                        action="LONG",
                        ticker=ticker,
                        entry_price=price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "z_vwap": round(z_vwap, 2),
                            "rsi": round(rsi_val, 1),
                            "adx": round(adx_val, 1),
                            "vol_ratio": round(vol / vol_avg, 2) if vol_avg > 0 else 0,
                            "atr": round(atr_val, 4),
                            "vwap": round(vwap_val, 4),
                        },
                    ))
                    traded_tickers.add(ticker)

                # --- SHORT reversal (rally epuise) ---
                elif z_vwap > VWAP_SD_MULT and rsi_val > RSI_OVERBOUGHT and volume_dry:
                    stop_loss = price + stop_distance
                    take_profit = vwap_val  # Retour au VWAP

                    # Verifier que le target est realiste (en-dessous de l'entree)
                    if take_profit >= price:
                        continue

                    signals.append(Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "z_vwap": round(z_vwap, 2),
                            "rsi": round(rsi_val, 1),
                            "adx": round(adx_val, 1),
                            "vol_ratio": round(vol / vol_avg, 2) if vol_avg > 0 else 0,
                            "atr": round(atr_val, 4),
                            "vwap": round(vwap_val, 4),
                        },
                    ))
                    traded_tickers.add(ticker)

                # Limiter a 3 signaux par jour (toute la strategie)
                if len(signals) >= 3:
                    return signals

        return signals
