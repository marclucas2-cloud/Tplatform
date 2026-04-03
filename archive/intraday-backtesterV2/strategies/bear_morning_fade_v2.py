"""
Strategie : Bear Morning Fade V2

V2 vs V1 : Filtres assouplis pour generer plus de trades.
- Gap minimum : 0.5% au lieu de 0.8%
- Volume confirme : 1.2x au lieu de 1.5x
- SPY down threshold : -0.15% au lieu de -0.2%
- SMA period raccourci a 15 barres (plus reactif)
- Gap max augmente a 4% au lieu de 3%
- Fenetre elargie : 9:40-13:00 au lieu de 9:45-12:00

Edge structurel :
En conditions bear (SPY down), les stocks qui gap-up sont vendus par les
institutions dans les 15-30 premieres minutes. Le gap-up sans support du
marche est un piege a acheteurs — les early longs sont liquides rapidement.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import volume_ratio
import config


# ── Parametres V2 (assouplis drastiquement) ──
GAP_MIN_PCT = 0.002           # Gap-up minimum 0.2% (V1 = 0.8%, V2a = 0.5%)
GAP_MAX_PCT = 0.05            # Gap-up maximum 5% (V1 = 3%)
SPY_DOWN_THRESHOLD = -0.001   # SPY doit etre down > 0.1% (V1 = 0.2%)
VOL_CONFIRM_MULT = 1.0        # Volume barre > 1.0x moyenne (filtre desactive)
SMA_PERIOD = 15               # SMA 15 barres — utilise comme reference seulement
STOP_BUFFER_PCT = 0.003       # Stop = HOD + 0.3% (V1 = 0.2%)
MIN_PRICE = 10.0              # Min price reduit (V1 = 15)
MAX_TRADES_PER_DAY = 5        # Plus de trades autorises

# Tickers a exclure
EXCLUDE = {
    "TQQQ", "SQQQ", "SPXL", "SPXS", "SPXU", "TNA", "TZA",
    "SOXL", "SOXS", "UVXY", "UVIX", "SVIX", "VXX",
    "NVDL", "NVDX", "TSLL", "TSLQ", "TSLS", "TSDD",
    "JDST", "JNUG", "NUGT", "LABU", "LABD",
    "UCO", "SCO", "TSLG", "TURB", "RWM",
    "PSQ", "SH", "SDS", "SMCL", "ZSL",
    # Index ETFs (pas de fade sur ETFs)
    "SPY", "QQQ", "IWM", "DIA", "IVV", "VOO",
}


class BearMorningFadeV2Strategy(BaseStrategy):
    name = "Bear Morning Fade V2"

    def __init__(
        self,
        gap_min_pct: float = GAP_MIN_PCT,
        gap_max_pct: float = GAP_MAX_PCT,
        spy_down_threshold: float = SPY_DOWN_THRESHOLD,
        vol_mult: float = VOL_CONFIRM_MULT,
        sma_period: int = SMA_PERIOD,
        stop_buffer_pct: float = STOP_BUFFER_PCT,
        min_price: float = MIN_PRICE,
        max_trades_per_day: int = MAX_TRADES_PER_DAY,
    ):
        self.gap_min_pct = gap_min_pct
        self.gap_max_pct = gap_max_pct
        self.spy_down_threshold = spy_down_threshold
        self.vol_mult = vol_mult
        self.sma_period = sma_period
        self.stop_buffer_pct = stop_buffer_pct
        self.min_price = min_price
        self.max_trades_per_day = max_trades_per_day

    def get_required_tickers(self) -> list[str]:
        from universe import PERMANENT_TICKERS, SECTOR_MAP
        tickers = list(PERMANENT_TICKERS)
        for components in SECTOR_MAP.values():
            tickers.extend(components[:5])
        if "SPY" not in tickers:
            tickers.append("SPY")
        return list(set(tickers))

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        # ── SPY doit etre present ──
        if "SPY" not in data:
            return signals

        df_spy = data["SPY"]
        if len(df_spy) < 10:
            return signals

        # ── Verifier que SPY est DOWN a 9:40 (V2 : check plus tot) ──
        spy_open = df_spy.iloc[0]["open"]
        if spy_open <= 0:
            return signals

        spy_bars_940 = df_spy.between_time("09:35", "09:45")
        if spy_bars_940.empty:
            return signals

        spy_price_940 = spy_bars_940.iloc[-1]["close"]
        spy_perf = (spy_price_940 - spy_open) / spy_open

        if spy_perf > self.spy_down_threshold:
            return signals

        # ── Scanner tous les tickers pour gap-up en conditions bear ──
        candidates = []

        for ticker, df in data.items():
            if ticker in EXCLUDE:
                continue
            if ticker == config.BENCHMARK:
                continue
            if len(df) < 30:
                continue

            first_price = df.iloc[0]["open"]
            if first_price < self.min_price:
                continue

            # ── Previous close (veille) ──
            all_dates = sorted(set(df.index.date))
            prev_dates = [d for d in all_dates if d < date]
            if not prev_dates:
                continue
            prev_day_bars = df[df.index.date == prev_dates[-1]]
            if prev_day_bars.empty:
                continue
            prev_close = prev_day_bars.iloc[-1]["close"]
            if prev_close <= 0:
                continue

            # ── Gap-up ──
            day_bars = df[df.index.date == date]
            if day_bars.empty or len(day_bars) < 5:
                continue
            today_open = day_bars.iloc[0]["open"]

            gap_pct = (today_open - prev_close) / prev_close
            if gap_pct < self.gap_min_pct or gap_pct > self.gap_max_pct:
                continue

            # ── SMA barres : prix doit etre en-dessous ──
            sma_values = df["close"].rolling(self.sma_period, min_periods=8).mean()

            # ── Volume moyen glissant ──
            vol_avg = df["volume"].rolling(20, min_periods=5).mean()

            # ── High of Day tracker ──
            running_hod = day_bars.iloc[0]["high"]

            # ── V2 : Fenetre elargie 9:40-13:00 ──
            tradeable = day_bars.between_time("09:40", "13:00")
            if tradeable.empty:
                continue

            signal_found = False

            for ts, bar in tradeable.iterrows():
                if signal_found:
                    break

                # Mettre a jour HOD
                if bar["high"] > running_hod:
                    running_hod = bar["high"]

                price = bar["close"]

                # ── Confirmation : barre rouge (close < open) ──
                if bar["close"] >= bar["open"]:
                    continue

                # ── Volume >= moyenne (V2 : filtre tres souple) ──
                avg_v = vol_avg.get(ts, np.nan)
                if pd.isna(avg_v) or avg_v <= 0:
                    avg_v = df["volume"].mean()
                if bar["volume"] < self.vol_mult * avg_v:
                    continue

                # ── V2 : SMA utilise pour le score (pas comme filtre bloquant) ──
                # Un stock qui gap-up est par definition au-dessus de la SMA
                # On garde la reference pour le scoring mais ne bloque plus
                sma_val = sma_values.get(ts, np.nan)
                if pd.isna(sma_val):
                    sma_val = price  # Fallback

                # ── Signal SHORT ──
                stop_loss = running_hod * (1 + self.stop_buffer_pct)
                take_profit = prev_close  # Gap fill target

                risk = stop_loss - price
                reward = price - take_profit
                if risk <= 0 or reward <= 0:
                    continue

                candidates.append({
                    "score": gap_pct * (bar["volume"] / avg_v),
                    "signal": Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "gap_pct": round(gap_pct * 100, 2),
                            "spy_perf": round(spy_perf * 100, 2),
                            "hod": round(running_hod, 2),
                            "prev_close": round(prev_close, 2),
                            "sma": round(sma_val, 2),
                            "rr_ratio": round(reward / risk, 2),
                        },
                    ),
                })
                signal_found = True

        # ── Trier par score et limiter ──
        candidates.sort(key=lambda c: c["score"], reverse=True)
        for c in candidates[:self.max_trades_per_day]:
            signals.append(c["signal"])

        return signals
