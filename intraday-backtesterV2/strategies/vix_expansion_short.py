"""
Strategie : VIX Expansion Short

Edge structurel :
Quand la volatilite de SPY s'expand (ATR recente > 1.5x ATR precedente) et que
SPY est en baisse de > 0.5%, les high-beta stocks (TSLA, NVDA, AMD, COIN, etc.)
chutent proportionnellement plus fort. On SHORT le high-beta avec le plus gros
decline et le plus de volume — il a le momentum negatif le plus fort.

Regles :
- Surveiller SPY : ATR des 12 dernieres barres > 1.5x ATR des 12 barres precedentes
- SPY doit etre down > 0.5% depuis l'open
- Parmi les high-beta : short celui avec le plus gros decline + volume
- Stop : 1.0%, Target : 2.5%
- Max 1 trade/jour
- Timing : 10:00-14:00 ET
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
import config


# ── Parametres ──
ATR_EXPANSION_MULT = 1.5     # ATR recente > 1.5x ATR precedente
ATR_LOOKBACK = 12            # Barres pour chaque fenetre ATR
SPY_DOWN_THRESHOLD = -0.005  # SPY down > 0.5%
STOP_PCT = 0.010             # Stop 1.0%
TARGET_PCT = 0.025           # Target 2.5%
MIN_DECLINE_PCT = 0.005      # High-beta doit etre down > 0.5%
MIN_VOL_RATIO = 1.2          # Volume > 1.2x moyenne

# Tickers high-beta + SPY pour le signal
HIGH_BETA_TICKERS = ["TSLA", "NVDA", "AMD", "COIN", "MARA", "MSTR"]
SIGNAL_TICKER = "SPY"


class VIXExpansionShortStrategy(BaseStrategy):
    name = "VIX Expansion Short"

    def __init__(
        self,
        atr_expansion_mult: float = ATR_EXPANSION_MULT,
        atr_lookback: int = ATR_LOOKBACK,
        spy_down_threshold: float = SPY_DOWN_THRESHOLD,
        stop_pct: float = STOP_PCT,
        target_pct: float = TARGET_PCT,
        min_decline_pct: float = MIN_DECLINE_PCT,
        min_vol_ratio: float = MIN_VOL_RATIO,
    ):
        self.atr_expansion_mult = atr_expansion_mult
        self.atr_lookback = atr_lookback
        self.spy_down_threshold = spy_down_threshold
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.min_decline_pct = min_decline_pct
        self.min_vol_ratio = min_vol_ratio

    def get_required_tickers(self) -> list[str]:
        return ["SPY", "TSLA", "NVDA", "AMD", "COIN", "MARA", "MSTR"]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        # ── SPY doit etre present ──
        if SIGNAL_TICKER not in data:
            return signals

        df_spy = data[SIGNAL_TICKER]
        if len(df_spy) < self.atr_lookback * 2 + 5:
            return signals

        spy_open = df_spy.iloc[0]["open"]
        if spy_open <= 0:
            return signals

        # ── Calculer ATR de SPY barre par barre ──
        spy_tr = pd.concat([
            df_spy["high"] - df_spy["low"],
            (df_spy["high"] - df_spy["close"].shift(1)).abs(),
            (df_spy["low"] - df_spy["close"].shift(1)).abs(),
        ], axis=1).max(axis=1)

        # ── Iterer barres SPY 10:00-14:00 ──
        spy_tradeable = df_spy.between_time("10:00", "14:00")
        if spy_tradeable.empty:
            return signals

        signal_found = False

        for ts, spy_bar in spy_tradeable.iterrows():
            if signal_found:
                break

            # ── SPY performance depuis l'open ──
            spy_perf = (spy_bar["close"] - spy_open) / spy_open
            if spy_perf > self.spy_down_threshold:
                continue

            # ── ATR expansion : derniere 12 barres vs precedente 12 barres ──
            # Trouver l'index de cette barre dans le DataFrame complet
            bar_loc = df_spy.index.get_loc(ts)
            if bar_loc < self.atr_lookback * 2:
                continue

            recent_tr = spy_tr.iloc[bar_loc - self.atr_lookback:bar_loc]
            prev_tr = spy_tr.iloc[bar_loc - self.atr_lookback * 2:bar_loc - self.atr_lookback]

            if recent_tr.empty or prev_tr.empty:
                continue

            atr_recent = recent_tr.mean()
            atr_prev = prev_tr.mean()

            if pd.isna(atr_recent) or pd.isna(atr_prev) or atr_prev <= 0:
                continue

            atr_ratio = atr_recent / atr_prev
            if atr_ratio < self.atr_expansion_mult:
                continue

            # ── Volatilite expand + SPY down : chercher le meilleur short high-beta ──
            best_candidate = None
            best_score = 0

            for hb_ticker in HIGH_BETA_TICKERS:
                if hb_ticker not in data:
                    continue

                df_hb = data[hb_ticker]
                if len(df_hb) < 20:
                    continue

                hb_open = df_hb.iloc[0]["open"]
                if hb_open <= 0:
                    continue

                # Trouver la barre au meme timestamp (ou la plus proche avant)
                hb_bars_at_ts = df_hb[df_hb.index <= ts]
                if hb_bars_at_ts.empty:
                    continue
                hb_bar = hb_bars_at_ts.iloc[-1]
                hb_ts = hb_bars_at_ts.index[-1]

                hb_price = hb_bar["close"]
                hb_perf = (hb_price - hb_open) / hb_open

                # Le high-beta doit etre en baisse
                if hb_perf > -self.min_decline_pct:
                    continue

                # Volume > 1.2x moyenne
                vol_avg = df_hb["volume"].rolling(20, min_periods=5).mean()
                avg_v = vol_avg.get(hb_ts, np.nan)
                if pd.isna(avg_v) or avg_v <= 0:
                    avg_v = df_hb["volume"].mean()
                if hb_bar["volume"] < self.min_vol_ratio * avg_v:
                    continue

                vol_r = hb_bar["volume"] / avg_v

                # Score = amplitude du decline * volume ratio
                score = abs(hb_perf) * vol_r

                if score > best_score:
                    best_score = score
                    best_candidate = {
                        "ticker": hb_ticker,
                        "price": hb_price,
                        "perf": hb_perf,
                        "vol_ratio": vol_r,
                        "timestamp": hb_ts,
                    }

            if best_candidate is None:
                continue

            # ── Signal SHORT sur le meilleur high-beta ──
            entry_price = best_candidate["price"]
            stop_loss = entry_price * (1 + self.stop_pct)
            take_profit = entry_price * (1 - self.target_pct)

            signals.append(Signal(
                action="SHORT",
                ticker=best_candidate["ticker"],
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timestamp=best_candidate["timestamp"],
                metadata={
                    "strategy": self.name,
                    "spy_perf": round(spy_perf * 100, 2),
                    "atr_ratio": round(atr_ratio, 2),
                    "hb_perf": round(best_candidate["perf"] * 100, 2),
                    "vol_ratio": round(best_candidate["vol_ratio"], 2),
                },
            ))
            signal_found = True

        return signals
