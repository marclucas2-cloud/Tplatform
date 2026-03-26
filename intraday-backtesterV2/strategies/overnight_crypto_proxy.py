"""
Strategie : Overnight Crypto Proxy

Edge structurel :
Le marche crypto trade 24/7 mais les crypto-proxies (COIN, MARA, MSTR)
ne tradent que pendant les heures US. Quand le leader (COIN) est fort en
fin de journee, le gap overnight des proxies capture le mouvement crypto
qui continue hors-marche.

Regles :
- COIN doit etre en hausse > 1.5% sur la journee
- Volume du proxy selectionne > 1.2x sa moyenne 20 barres
- Acheter le crypto-proxy avec le meilleur momentum journalier
- Entree a la derniere barre avant 15:55
- Skip vendredi (weekend risk)
- Stop : 4%, TP : 2.5%
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import volume_ratio


# ── Parametres ──
LEADER = "COIN"
PROXIES = ["COIN", "MARA", "MSTR"]
LEADER_MIN_PERF = 0.015       # COIN doit etre up > 1.5% sur la journee
VOL_RATIO_MIN = 1.2           # Volume > 1.2x moyenne
STOP_PCT = 0.04               # Stop 4%
TP_PCT = 0.025                # TP 2.5%
MAX_TRADES_PER_DAY = 1
VOL_LOOKBACK = 20


class OvernightCryptoProxyStrategy(BaseStrategy):
    name = "Overnight Crypto Proxy"

    def __init__(
        self,
        leader_min_perf: float = LEADER_MIN_PERF,
        vol_ratio_min: float = VOL_RATIO_MIN,
        stop_pct: float = STOP_PCT,
        tp_pct: float = TP_PCT,
    ):
        self.leader_min_perf = leader_min_perf
        self.vol_ratio_min = vol_ratio_min
        self.stop_pct = stop_pct
        self.tp_pct = tp_pct

    def get_required_tickers(self) -> list[str]:
        return ["COIN", "MARA", "MSTR"]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        # ── Skip vendredi (weekend risk) ──
        if hasattr(date, "weekday"):
            weekday = date.weekday()
        else:
            weekday = pd.Timestamp(date).weekday()

        if weekday == 4:  # Vendredi
            return signals

        # ── COIN requis comme leader ──
        if LEADER not in data:
            return signals

        df_leader = data[LEADER]
        if len(df_leader) < 20:
            return signals

        leader_open = df_leader.iloc[0]["open"]
        if leader_open <= 0:
            return signals

        # ── Verifier performance COIN sur la journee ──
        leader_late = df_leader.between_time("15:40", "15:54")
        if leader_late.empty:
            return signals

        leader_last_close = leader_late.iloc[-1]["close"]
        leader_perf = (leader_last_close - leader_open) / leader_open

        if leader_perf < self.leader_min_perf:
            return signals

        # ── Evaluer chaque proxy : momentum + volume ──
        candidates = []

        for proxy in PROXIES:
            if proxy not in data:
                continue

            df_proxy = data[proxy]
            if len(df_proxy) < VOL_LOOKBACK + 5:
                continue

            proxy_open = df_proxy.iloc[0]["open"]
            if proxy_open <= 0:
                continue

            # Barres de fin de journee
            proxy_late = df_proxy.between_time("15:45", "15:54")
            if proxy_late.empty:
                continue

            proxy_last_close = proxy_late.iloc[-1]["close"]
            proxy_perf = (proxy_last_close - proxy_open) / proxy_open

            # Volume ratio
            vol_r = volume_ratio(df_proxy["volume"], lookback=VOL_LOOKBACK)
            last_vol_r = vol_r.iloc[-1] if not vol_r.empty and not pd.isna(vol_r.iloc[-1]) else 0

            if last_vol_r < self.vol_ratio_min:
                continue

            candidates.append({
                "ticker": proxy,
                "momentum": proxy_perf,
                "vol_ratio": last_vol_r,
                "entry_price": proxy_last_close,
                "timestamp": proxy_late.index[-1],
            })

        if not candidates:
            return signals

        # ── Prendre le proxy avec le meilleur momentum ──
        candidates.sort(key=lambda c: c["momentum"], reverse=True)
        best = candidates[0]

        signal_found = False

        # ── Iterer barre par barre sur les barres de fin de journee du winner ──
        df_best = data[best["ticker"]]
        late_bars = df_best.between_time("15:45", "15:54")

        for ts, bar in late_bars.iterrows():
            if signal_found:
                break

            entry_price = bar["close"]
            if entry_price <= 0:
                continue

            stop_loss = entry_price * (1 - self.stop_pct)
            take_profit = entry_price * (1 + self.tp_pct)

            signals.append(Signal(
                action="LONG",
                ticker=best["ticker"],
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timestamp=ts,
                metadata={
                    "strategy": self.name,
                    "leader_perf_pct": round(leader_perf * 100, 2),
                    "proxy_momentum_pct": round(best["momentum"] * 100, 2),
                    "vol_ratio": round(best["vol_ratio"], 2),
                    "weekday": weekday,
                    "entry_type": "overnight_crypto_proxy",
                },
            ))
            signal_found = True

        return signals
