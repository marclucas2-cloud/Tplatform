"""
Strategie : Crypto Bear Cascade

Edge structurel :
Quand COIN (Coinbase) chute > 2% avant 10:00, les proxys crypto (MARA, MSTR,
RIOT) suivent avec un delai. On short le proxy qui a le MOINS baisse car il
n'a pas encore pleinement reagi — c'est le "retardataire" qui va rattraper.

Regles :
- COIN doit etre down > 2% depuis l'open avant 10:00
- Au moins 2 des 3 proxys (MARA, MSTR, RIOT) doivent etre down > 1%
- Shorter le proxy qui a baisse le MOINS (pas encore fully reacted)
- Confirmer : premiere barre rouge avec volume > 1.5x la moyenne
- Stop : high de 9:30-10:00 du proxy selectionne
- Target : 2.5x le risque
- Max 1 trade/jour
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import volume_ratio


# ── Parametres ──
LEADER = "COIN"
PROXIES = ["MARA", "MSTR", "RIOT"]
LEADER_MIN_DROP_PCT = 0.02    # COIN doit etre down > 2%
PROXY_MIN_DROP_PCT = 0.01     # Au moins 2 proxys down > 1%
MIN_PROXIES_DOWN = 2          # Au moins 2 sur 3
VOLUME_CONFIRM_MULT = 1.5     # Volume > 1.5x moyenne
TARGET_RISK_MULT = 2.5        # Target = 2.5x risque
MIN_BARS = 15
MAX_TRADES_PER_DAY = 1


class CryptoBearCascadeStrategy(BaseStrategy):
    name = "Crypto Bear Cascade"

    def __init__(
        self,
        leader_min_drop: float = LEADER_MIN_DROP_PCT,
        proxy_min_drop: float = PROXY_MIN_DROP_PCT,
        target_risk_mult: float = TARGET_RISK_MULT,
    ):
        self.leader_min_drop = leader_min_drop
        self.proxy_min_drop = proxy_min_drop
        self.target_risk_mult = target_risk_mult

    def get_required_tickers(self) -> list[str]:
        return ["COIN", "MARA", "MSTR", "RIOT"]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []

        # ── COIN requis ──
        if LEADER not in data:
            return signals

        df_coin = data[LEADER]
        if len(df_coin) < MIN_BARS:
            return signals

        coin_open = df_coin.iloc[0]["open"]
        if coin_open <= 0:
            return signals

        # ── Verifier COIN down > 2% avant 10:00 ──
        coin_morning = df_coin.between_time("09:30", "10:00")
        if coin_morning.empty:
            return signals

        # Prendre la derniere barre avant 10:00 pour evaluer le drop
        coin_at_10 = coin_morning.iloc[-1]["close"]
        coin_drop = (coin_at_10 - coin_open) / coin_open

        if coin_drop >= -self.leader_min_drop:
            return signals  # COIN pas assez baissier

        # ── Evaluer les proxys ──
        proxy_data = {}
        proxies_down_enough = 0

        for proxy_ticker in PROXIES:
            if proxy_ticker not in data:
                continue

            df_proxy = data[proxy_ticker]
            if len(df_proxy) < MIN_BARS:
                continue

            proxy_open = df_proxy.iloc[0]["open"]
            if proxy_open <= 0:
                continue

            # Performance du proxy avant 10:00
            proxy_morning = df_proxy.between_time("09:30", "10:00")
            if proxy_morning.empty:
                continue

            proxy_at_10 = proxy_morning.iloc[-1]["close"]
            proxy_drop = (proxy_at_10 - proxy_open) / proxy_open

            # High de 9:30-10:00 pour le stop
            morning_high = proxy_morning["high"].max()

            # Volume ratio pour confirmation
            df_vol_ratio = volume_ratio(df_proxy["volume"], lookback=20)

            proxy_data[proxy_ticker] = {
                "df": df_proxy,
                "open": proxy_open,
                "drop": proxy_drop,
                "morning_high": morning_high,
                "vol_ratio": df_vol_ratio,
            }

            if proxy_drop <= -self.proxy_min_drop:
                proxies_down_enough += 1

        # ── Au moins 2 proxys doivent etre down > 1% ──
        if proxies_down_enough < MIN_PROXIES_DOWN:
            return signals

        # ── Trouver le proxy qui a baisse le MOINS (retardataire) ──
        if not proxy_data:
            return signals

        # Trier par drop ascendant (le moins negatif = le moins baissier)
        sorted_proxies = sorted(
            proxy_data.items(),
            key=lambda x: x[1]["drop"],
            reverse=True,  # Le plus haut (le moins negatif) en premier
        )

        target_ticker = sorted_proxies[0][0]
        target_data = sorted_proxies[0][1]

        # ── Scanner barre par barre a partir de 10:00 ──
        df_target = target_data["df"]
        tradeable_bars = df_target.between_time("10:00", "15:00")
        if tradeable_bars.empty:
            return signals

        signal_found = False

        for ts, bar in tradeable_bars.iterrows():
            if signal_found:
                break

            idx = df_target.index.get_loc(ts)
            price = bar["close"]
            if price <= 0:
                continue

            # ── Condition : barre rouge (close < open) ──
            if bar["close"] >= bar["open"]:
                continue

            # ── Condition : volume > 1.5x moyenne ──
            vol_r = target_data["vol_ratio"].iloc[idx] if idx < len(target_data["vol_ratio"]) else np.nan
            if pd.isna(vol_r) or vol_r < VOLUME_CONFIRM_MULT:
                continue

            # ── Signal SHORT ──
            entry_price = price
            stop_loss = target_data["morning_high"]

            risk = stop_loss - entry_price
            if risk <= 0:
                continue

            take_profit = entry_price - (self.target_risk_mult * risk)

            signals.append(Signal(
                action="SHORT",
                ticker=target_ticker,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timestamp=ts,
                metadata={
                    "strategy": self.name,
                    "coin_drop_pct": round(coin_drop * 100, 2),
                    "proxy_drop_pct": round(target_data["drop"] * 100, 2),
                    "proxies_down": proxies_down_enough,
                    "vol_ratio": round(vol_r, 2),
                    "target_ticker": target_ticker,
                    "rr_ratio": round(self.target_risk_mult, 1),
                },
            ))
            signal_found = True

        return signals
