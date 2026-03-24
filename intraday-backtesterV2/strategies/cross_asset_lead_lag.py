"""
Stratégie 12 : Cross-Asset Lead-Lag (BTC → Tech Stocks)
Le Bitcoin mène souvent les tech stocks de 15-30 minutes.

Hypothèse :
- BTC trade 24/7 → il réagit aux news macro avant les US stocks
- Corrélation BTC-NASDAQ élevée depuis 2020 (~0.6-0.8 en tendance)
- Un mouvement fort de BTC pré-market ou early session → signal directionnel
  pour NVDA, COIN, MARA, MSTR et le QQQ

Extension :
- TLT (bonds) inverse de SPY → signal risk-on/risk-off
- GLD (gold) mène pendant les crises

Proxy avec Alpaca : utiliser COIN/MARA comme proxy BTC puisque
Alpaca ne fournit pas les données crypto directement en intraday.
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
import config


class CrossAssetLeadLagStrategy(BaseStrategy):
    name = "Cross-Asset Lead-Lag"

    def __init__(self, btc_proxy_threshold: float = 0.015, lead_lag_minutes: int = 30,
                 stop_pct: float = 0.004, target_pct: float = 0.008):
        self.btc_threshold = btc_proxy_threshold  # 1.5% move minimum
        self.lead_lag_min = lead_lag_minutes
        self.stop_pct = stop_pct
        self.target_pct = target_pct

    def get_required_tickers(self) -> list[str]:
        return ["COIN", "MARA", "MSTR",  # BTC proxies
                "NVDA", "QQQ", "AAPL", "MSFT",  # Followers
                "SPY", "TLT", "GLD"]  # Cross-asset

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        # ── Signal 1 : BTC proxy lead → Tech follow ──
        btc_proxies = ["COIN", "MARA", "MSTR"]
        tech_followers = ["NVDA", "QQQ", "AAPL"]

        # Calculer le mouvement agrégé des BTC proxies en première heure
        btc_moves = []
        for proxy in btc_proxies:
            if proxy not in data:
                continue
            df = data[proxy]
            first_hour = df.between_time("09:30", "10:30")
            if len(first_hour) < 5:
                continue

            move = (first_hour.iloc[-1]["close"] - first_hour.iloc[0]["open"]) / first_hour.iloc[0]["open"]
            btc_moves.append(move)

        if btc_moves:
            avg_btc_move = np.mean(btc_moves)

            if abs(avg_btc_move) > self.btc_threshold:
                # Signal fort — entrer sur les tech stocks après le lag
                for follower in tech_followers:
                    if follower not in data:
                        continue

                    df = data[follower]
                    # Entrer 30 min après le signal (10:30-11:00)
                    entry_window = df.between_time("10:30", "11:00")
                    if entry_window.empty:
                        continue

                    entry_bar = entry_window.iloc[0]
                    ts = entry_window.index[0]

                    # Vérifier que le follower n'a pas DÉJÀ rattrapé le mouvement
                    follower_first_hour = df.between_time("09:30", "10:30")
                    if not follower_first_hour.empty:
                        follower_move = (follower_first_hour.iloc[-1]["close"] - follower_first_hour.iloc[0]["open"]) / follower_first_hour.iloc[0]["open"]
                        # Si le follower a déjà bougé autant que le leader, skip
                        if abs(follower_move) > abs(avg_btc_move) * 0.7:
                            continue

                    entry = entry_bar["close"]
                    action = "LONG" if avg_btc_move > 0 else "SHORT"

                    signals.append(Signal(
                        action=action,
                        ticker=follower,
                        entry_price=entry,
                        stop_loss=entry * (1 - self.stop_pct) if action == "LONG" else entry * (1 + self.stop_pct),
                        take_profit=entry * (1 + self.target_pct) if action == "LONG" else entry * (1 - self.target_pct),
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "signal_type": "btc_lead",
                            "btc_proxy_move": round(avg_btc_move * 100, 2),
                        },
                    ))

        # ── Signal 2 : TLT/SPY divergence (risk-on/risk-off) ──
        if "TLT" in data and "SPY" in data:
            tlt_df = data["TLT"]
            spy_df = data["SPY"]

            tlt_morning = tlt_df.between_time("09:30", "11:00")
            spy_morning = spy_df.between_time("09:30", "11:00")

            if len(tlt_morning) > 5 and len(spy_morning) > 5:
                tlt_move = (tlt_morning.iloc[-1]["close"] - tlt_morning.iloc[0]["open"]) / tlt_morning.iloc[0]["open"]
                spy_move = (spy_morning.iloc[-1]["close"] - spy_morning.iloc[0]["open"]) / spy_morning.iloc[0]["open"]

                # Divergence : TLT monte + SPY descend → risk-off en cours
                # On peut shorter SPY ou longer TLT pour continuation
                if tlt_move > 0.005 and spy_move < -0.003:
                    # Risk-off détecté → SHORT SPY continuation
                    entry_window = spy_df.between_time("11:00", "11:15")
                    if not entry_window.empty:
                        entry = entry_window.iloc[0]["close"]
                        ts = entry_window.index[0]
                        signals.append(Signal(
                            action="SHORT",
                            ticker="SPY",
                            entry_price=entry,
                            stop_loss=entry * (1 + self.stop_pct),
                            take_profit=entry * (1 - self.target_pct),
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "signal_type": "risk_off",
                                "tlt_move": round(tlt_move * 100, 2),
                                "spy_move": round(spy_move * 100, 2),
                            },
                        ))

                elif tlt_move < -0.005 and spy_move > 0.003:
                    # Risk-on détecté → LONG SPY continuation
                    entry_window = spy_df.between_time("11:00", "11:15")
                    if not entry_window.empty:
                        entry = entry_window.iloc[0]["close"]
                        ts = entry_window.index[0]
                        signals.append(Signal(
                            action="LONG",
                            ticker="SPY",
                            entry_price=entry,
                            stop_loss=entry * (1 - self.stop_pct),
                            take_profit=entry * (1 + self.target_pct),
                            timestamp=ts,
                            metadata={
                                "strategy": self.name,
                                "signal_type": "risk_on",
                                "tlt_move": round(tlt_move * 100, 2),
                                "spy_move": round(spy_move * 100, 2),
                            },
                        ))

        return signals
