"""
Strategie : Squeeze Fade

Edge structurel :
Les short squeezes s'epuisent souvent apres un move rapide > 4% en < 1 heure.
Quand le volume decline sur la derniere barre du spike et le RSI(7) depasse 85,
le mouvement est suracheté et un retour vers le VWAP est probable.
On attend la premiere barre faisant un lower high pour confirmer l'essoufflement
avant de shorter.

Regles :
- Scanner tous les tickers : up > 4% en < 12 barres (1 heure sur 5M)
- Volume en baisse sur la derniere barre du spike
- RSI(7) > 85
- Prix > VWAP + 2%
- Attendre premiere barre avec lower high → SHORT
- Stop : high du spike + 0.5%
- Target : retour au VWAP ou 50% du spike retrace
- Max 1 trade/jour, prix min $10
- Fenetre : 10:00-15:00
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import rsi, vwap


# ── Parametres ──
MIN_SPIKE_PCT = 0.04       # Up > 4% en < 12 barres
SPIKE_LOOKBACK = 12        # 12 barres = 1 heure (5M)
RSI_THRESHOLD = 85         # RSI(7) > 85
VWAP_PREMIUM_PCT = 0.02    # Prix > VWAP + 2%
STOP_BUFFER_PCT = 0.005    # Stop = high du spike + 0.5%
MIN_PRICE = 10.0
MAX_TRADES_PER_DAY = 1

# ── Tickers a exclure (ETFs leverages) ──
EXCLUDE = {
    "TQQQ", "SQQQ", "SPXL", "SPXS", "SPXU", "TNA", "TZA",
    "SOXL", "SOXS", "UVXY", "UVIX", "SVIX", "VXX",
    "NVDL", "NVDX", "TSLL", "TSLQ", "TSLS", "TSDD",
    "JDST", "JNUG", "NUGT", "LABU", "LABD",
    "UCO", "SCO", "TSLG", "TURB", "RWM",
    "PSQ", "SH", "SDS",
    # Index ETFs
    "SPY", "QQQ", "IWM", "DIA",
}


class SqueezeFadeStrategy(BaseStrategy):
    name = "Squeeze Fade"

    def __init__(
        self,
        min_spike_pct: float = MIN_SPIKE_PCT,
        rsi_threshold: float = RSI_THRESHOLD,
        max_trades_per_day: int = MAX_TRADES_PER_DAY,
    ):
        self.min_spike_pct = min_spike_pct
        self.rsi_threshold = rsi_threshold
        self.max_trades_per_day = max_trades_per_day

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        signal_found = False

        for ticker, df in data.items():
            if signal_found:
                break
            if ticker in EXCLUDE:
                continue
            if len(df) < SPIKE_LOOKBACK + 5:
                continue

            first_price = df.iloc[0]["open"]
            if first_price < MIN_PRICE:
                continue

            # ── Calculer VWAP ──
            df_vwap = vwap(df)

            # ── Calculer RSI(7) ──
            df_rsi = rsi(df["close"], period=7)

            # ── Scanner barre par barre de 10:00 a 15:00 ──
            tradeable_bars = df.between_time("10:00", "15:00")

            spike_detected = False
            spike_high = 0.0
            spike_low_price = 0.0
            prev_high = 0.0

            for ts, bar in tradeable_bars.iterrows():
                if signal_found:
                    break

                idx = df.index.get_loc(ts)
                if idx < SPIKE_LOOKBACK:
                    continue

                price = bar["close"]
                if price <= 0:
                    continue

                # ── Phase 1 : detecter le spike ──
                if not spike_detected:
                    # Regarder les 12 dernieres barres
                    lookback_start = max(0, idx - SPIKE_LOOKBACK)
                    lookback_slice = df.iloc[lookback_start:idx + 1]

                    low_in_window = lookback_slice["low"].min()
                    if low_in_window <= 0:
                        continue

                    move_pct = (price - low_in_window) / low_in_window

                    if move_pct < self.min_spike_pct:
                        prev_high = bar["high"]
                        continue

                    # ── Confirmer : volume en baisse sur cette barre ──
                    if idx < 2:
                        prev_high = bar["high"]
                        continue

                    current_vol = bar["volume"]
                    prev_vol = df.iloc[idx - 1]["volume"]
                    if prev_vol <= 0 or current_vol >= prev_vol:
                        prev_high = bar["high"]
                        continue  # Volume pas en baisse

                    # ── Confirmer : RSI(7) > 85 ──
                    rsi_val = df_rsi.iloc[idx] if idx < len(df_rsi) else np.nan
                    if pd.isna(rsi_val) or rsi_val < self.rsi_threshold:
                        prev_high = bar["high"]
                        continue

                    # ── Confirmer : prix > VWAP + 2% ──
                    vwap_val = df_vwap.iloc[idx] if idx < len(df_vwap) else np.nan
                    if pd.isna(vwap_val) or vwap_val <= 0:
                        prev_high = bar["high"]
                        continue

                    vwap_premium = (price - vwap_val) / vwap_val
                    if vwap_premium < VWAP_PREMIUM_PCT:
                        prev_high = bar["high"]
                        continue

                    # Spike confirme — stocker les infos
                    spike_detected = True
                    spike_high = lookback_slice["high"].max()
                    spike_low_price = low_in_window
                    prev_high = bar["high"]
                    continue  # Attendre le lower high

                # ── Phase 2 : attendre lower high → SHORT ──
                if bar["high"] < prev_high:
                    # Lower high confirme — entrer SHORT

                    # VWAP actuel pour le target
                    vwap_now = df_vwap.iloc[idx] if idx < len(df_vwap) else np.nan
                    if pd.isna(vwap_now) or vwap_now <= 0:
                        prev_high = bar["high"]
                        continue

                    entry_price = price
                    stop_loss = spike_high * (1 + STOP_BUFFER_PCT)

                    # Target : VWAP ou 50% du spike retrace (le plus proche)
                    target_vwap = vwap_now
                    target_50pct = entry_price - 0.5 * (spike_high - spike_low_price)
                    take_profit = max(target_vwap, target_50pct)

                    # Valider le risk/reward
                    risk = stop_loss - entry_price
                    reward = entry_price - take_profit
                    if risk <= 0 or reward <= 0:
                        prev_high = bar["high"]
                        continue

                    signals.append(Signal(
                        action="SHORT",
                        ticker=ticker,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "spike_pct": round((spike_high - spike_low_price) / spike_low_price * 100, 2),
                            "spike_high": round(spike_high, 2),
                            "vwap_target": round(target_vwap, 2),
                            "rr_ratio": round(reward / risk, 2),
                        },
                    ))
                    signal_found = True
                    break

                prev_high = bar["high"]

        return signals
