"""
Strategie : Momentum Exhaustion Short (A5)

Edge structurel :
Quand un stock atteint un RSI(2) extreme > 90 alors qu'il est deja au-dessus
de la Bollinger Band superieure (20, 2.5 std) avec du volume > 1.5x la moyenne
ET un gain cumule > 8% sur 5 jours, le momentum est sur-exploite.
Les institutions prennent leurs profits et les shorts agressifs entrent,
creant un mean reversion rapide.

Regles :
- RSI(2) > 90 (extreme surachat court terme)
- Prix > Bollinger Band upper (20, 2.5 std)
- Volume barre > 1.5x la moyenne 20 barres
- Gain cumule 5 jours > 8%
- Top 50 stocks liquides uniquement (via pre-filtrage)
- Stop : -2% (hard stop)
- Target : +3% de profit
- Fenetre : 10:00-15:00 ET
- Max 3 trades/jour, min price $10
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import rsi, bollinger_bands
import config


# ── Parametres ──
RSI_PERIOD = 2                # RSI ultra court terme
RSI_THRESHOLD = 90            # RSI(2) > 90
BB_PERIOD = 20                # Bollinger Bands 20 periodes
BB_STD = 2.5                  # 2.5 ecarts-types
VOL_MULT = 1.5                # Volume > 1.5x moyenne
GAIN_5D_PCT = 0.08            # Gain 5 jours > 8%
STOP_PCT = 0.02               # Stop loss 2%
TARGET_PCT = 0.03             # Take profit 3%
MIN_PRICE = 10.0
MAX_TRADES_PER_DAY = 3

# Tickers a exclure (ETFs leverages, index ETFs)
EXCLUDE = {
    "TQQQ", "SQQQ", "SPXL", "SPXS", "SPXU", "TNA", "TZA",
    "SOXL", "SOXS", "UVXY", "UVIX", "SVIX", "VXX",
    "NVDL", "NVDX", "TSLL", "TSLQ", "TSLS", "TSDD",
    "JDST", "JNUG", "NUGT", "LABU", "LABD",
    "UCO", "SCO", "TSLG", "TURB", "RWM",
    "PSQ", "SH", "SDS", "SMCL", "ZSL",
    # Index ETFs
    "SPY", "QQQ", "IWM", "DIA", "IVV", "VOO",
}


class MomentumExhaustionShortStrategy(BaseStrategy):
    name = "Momentum Exhaustion Short"

    def __init__(
        self,
        rsi_period: int = RSI_PERIOD,
        rsi_threshold: float = RSI_THRESHOLD,
        bb_period: int = BB_PERIOD,
        bb_std: float = BB_STD,
        vol_mult: float = VOL_MULT,
        gain_5d_pct: float = GAIN_5D_PCT,
        stop_pct: float = STOP_PCT,
        target_pct: float = TARGET_PCT,
        min_price: float = MIN_PRICE,
        max_trades_per_day: int = MAX_TRADES_PER_DAY,
    ):
        self.rsi_period = rsi_period
        self.rsi_threshold = rsi_threshold
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.vol_mult = vol_mult
        self.gain_5d_pct = gain_5d_pct
        self.stop_pct = stop_pct
        self.target_pct = target_pct
        self.min_price = min_price
        self.max_trades_per_day = max_trades_per_day
        # Cache pour eviter de recalculer les indicateurs a chaque jour
        self._indicator_cache: dict[str, dict] = {}

    def _get_indicators(self, ticker: str, df: pd.DataFrame) -> dict:
        """Calcule et cache les indicateurs pour un ticker."""
        if ticker in self._indicator_cache:
            return self._indicator_cache[ticker]

        indicators = {
            "rsi": rsi(df["close"], period=self.rsi_period),
            "bb_upper": bollinger_bands(
                df["close"], period=self.bb_period, std_dev=self.bb_std
            )[0],
            "vol_avg": df["volume"].rolling(20, min_periods=5).mean(),
            "dates": sorted(set(df.index.date)),
        }
        self._indicator_cache[ticker] = indicators
        return indicators

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        candidates = []

        for ticker, df in data.items():
            if ticker in EXCLUDE:
                continue
            if ticker == config.BENCHMARK:
                continue
            if len(df) < 100:
                continue

            # ── Pre-filtres ──
            day_bars = df[df.index.date == date]
            if len(day_bars) < 5:
                continue

            today_open = day_bars.iloc[0]["open"]
            if today_open < self.min_price:
                continue

            # ── Gain cumule 5 jours (rapide avec cache) ──
            ind = self._get_indicators(ticker, df)
            all_dates = ind["dates"]

            # Binary search pour trouver l'index
            import bisect
            date_idx = bisect.bisect_left(all_dates, date)
            if date_idx >= len(all_dates) or all_dates[date_idx] != date:
                continue
            if date_idx < 5:
                continue

            # Close 5 jours avant
            date_5d_ago = all_dates[date_idx - 5]
            bars_5d_ago = df[df.index.date == date_5d_ago]
            if bars_5d_ago.empty:
                continue
            close_5d_ago = bars_5d_ago.iloc[-1]["close"]
            if close_5d_ago <= 0:
                continue

            gain_5d = (today_open - close_5d_ago) / close_5d_ago
            if gain_5d < self.gain_5d_pct:
                continue

            # ── Utiliser indicateurs caches ──
            df_rsi = ind["rsi"]
            bb_upper = ind["bb_upper"]
            vol_avg = ind["vol_avg"]

            # ── Scanner barres 10:00-15:00 ──
            tradeable = day_bars.between_time("10:00", "15:00")
            if tradeable.empty:
                continue

            signal_found = False

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                price = bar["close"]
                if price <= 0:
                    continue

                idx = df.index.get_loc(ts)

                # ── Condition 1 : RSI(2) > 90 ──
                rsi_val = df_rsi.iloc[idx] if idx < len(df_rsi) else np.nan
                if pd.isna(rsi_val) or rsi_val < self.rsi_threshold:
                    continue

                # ── Condition 2 : Prix > BB upper (20, 2.5) ──
                bb_val = bb_upper.iloc[idx] if idx < len(bb_upper) else np.nan
                if pd.isna(bb_val) or price <= bb_val:
                    continue

                # ── Condition 3 : Volume > 1.5x moyenne ──
                avg_v = vol_avg.iloc[idx] if idx < len(vol_avg) else np.nan
                if pd.isna(avg_v) or avg_v <= 0:
                    avg_v = df["volume"].mean()
                if bar["volume"] < self.vol_mult * avg_v:
                    continue

                # ── Tous les filtres passes : SHORT ──
                entry_price = price
                stop_loss = entry_price * (1 + self.stop_pct)
                take_profit = entry_price * (1 - self.target_pct)

                candidates.append({
                    "score": rsi_val * gain_5d * (bar["volume"] / avg_v),
                    "signal": Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "rsi_2": round(rsi_val, 1),
                            "bb_upper": round(bb_val, 2),
                            "gain_5d_pct": round(gain_5d * 100, 2),
                            "vol_ratio": round(bar["volume"] / avg_v, 2),
                        },
                    ),
                })
                signal_found = True

        # ── Trier par score (RSI le plus extreme en premier) et limiter ──
        candidates.sort(key=lambda c: c["score"], reverse=True)
        for c in candidates[:self.max_trades_per_day]:
            signals.append(c["signal"])

        return signals
