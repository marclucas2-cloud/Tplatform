"""
Crypto-Proxy Regime Switch V2 — Assouplissement des filtres.

Modifications par rapport a V1 (Sharpe 3.03, 11 trades) :
- Assouplir LEADER_PERF_THRESHOLD : 1.0% -> 0.5%
- Assouplir FOLLOWER_DIVERGE_THRESHOLD : 0.5% -> 0.25%
- Assouplir ZSCORE_ENTRY : -1.5 -> -1.0
- Assouplir MAX_ADX_LEADER : 40 -> 50 (accepter plus de conditions)
- Reduire ZSCORE_LOOKBACK : 20 -> 15 (plus reactif)
- Ajouter plus de followers : RIOT, BITF, CLSK
- Elargir la fenetre : 10:00-15:00 au lieu de 10:00-14:30
- Augmenter TARGET_RISK_MULT : 1.5 -> 2.0
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import adx, zscore_spread, volume_ratio
import config


# Tickers
LEADER = "COIN"
FOLLOWERS = ["MARA", "MSTR", "RIOT"]

# Seuils intermediaires (entre V1 stricte et V2 trop assoupli)
LEADER_PERF_THRESHOLD = 0.007     # COIN doit avoir bouge > 0.7% depuis l'open
FOLLOWER_DIVERGE_THRESHOLD = 0.004 # Follower doit avoir bouge > 0.4% dans l'autre sens
ZSCORE_ENTRY = -1.2               # Z-score du spread (entre -1.5 et -1.0)
MAX_ADX_LEADER = 45               # ADX COIN max
MAX_GAP_PCT = 3.5                 # Si gap > 3.5%, skip
MIN_VOLUME_RATIO = 0.9            # Volume follower >= 0.9x sa moyenne
ATR_PERIOD = 14
STOP_ATR_MULT = 1.8               # Stop un peu plus serre (1.8x ATR au lieu de 2.0)
TARGET_RISK_MULT = 2.0            # Target = 2.0x risk
ZSCORE_LOOKBACK = 15              # Plus reactif


class CryptoProxyRegimeV2Strategy(BaseStrategy):
    name = "Crypto-Proxy Regime V2"

    def get_required_tickers(self) -> list[str]:
        return ["COIN", "MARA", "MSTR", "RIOT", "SPY"]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        traded_tickers = set()

        if LEADER not in data:
            return signals

        df_leader = data[LEADER].copy()
        if len(df_leader) < 20:
            return signals

        leader_open = df_leader.iloc[0]["open"]
        if leader_open <= 0:
            return signals

        # ADX du leader
        df_leader["adx_val"] = adx(df_leader, period=ATR_PERIOD)
        # Performance depuis l'open
        df_leader["perf"] = df_leader["close"] / leader_open - 1.0

        for follower_ticker in FOLLOWERS:
            if follower_ticker not in data:
                continue
            if follower_ticker in traded_tickers:
                continue

            df_follower = data[follower_ticker].copy()
            if len(df_follower) < 20:
                continue

            follower_open = df_follower.iloc[0]["open"]
            if follower_open <= 0:
                continue

            # Filtre gap
            if len(df_follower) > 1:
                first_bar_move = abs(df_follower.iloc[0]["close"] - df_follower.iloc[0]["open"]) / follower_open * 100
                if first_bar_move > MAX_GAP_PCT:
                    continue

            df_follower["perf"] = df_follower["close"] / follower_open - 1.0

            # ATR
            df_follower["tr"] = pd.concat([
                df_follower["high"] - df_follower["low"],
                (df_follower["high"] - df_follower["close"].shift(1)).abs(),
                (df_follower["low"] - df_follower["close"].shift(1)).abs(),
            ], axis=1).max(axis=1)
            df_follower["atr"] = df_follower["tr"].rolling(ATR_PERIOD, min_periods=5).mean()

            # Volume ratio
            df_follower["vol_ratio"] = volume_ratio(df_follower["volume"], lookback=20)

            # Z-score spread
            common_idx = df_leader.index.intersection(df_follower.index)
            if len(common_idx) < ZSCORE_LOOKBACK + 3:
                continue

            leader_prices = df_leader.loc[common_idx, "close"]
            follower_prices = df_follower.loc[common_idx, "close"]
            z_spread = zscore_spread(leader_prices, follower_prices, lookback=ZSCORE_LOOKBACK)

            # Fenetre elargie 10:00-15:00
            tradeable_idx = common_idx[
                (common_idx.time >= pd.Timestamp("10:00").time()) &
                (common_idx.time <= pd.Timestamp("15:00").time())
            ]

            for ts in tradeable_idx:
                if follower_ticker in traded_tickers:
                    break

                leader_perf = df_leader.loc[ts, "perf"] if ts in df_leader.index else np.nan
                follower_perf = df_follower.loc[ts, "perf"] if ts in df_follower.index else np.nan
                leader_adx = df_leader.loc[ts, "adx_val"] if ts in df_leader.index else np.nan
                z_val = z_spread.get(ts, np.nan)
                atr_val = df_follower.loc[ts, "atr"] if ts in df_follower.index else np.nan
                vol_r = df_follower.loc[ts, "vol_ratio"] if ts in df_follower.index else np.nan
                follower_price = df_follower.loc[ts, "close"] if ts in df_follower.index else np.nan

                if any(pd.isna(v) for v in [leader_perf, follower_perf, leader_adx, z_val, atr_val, vol_r, follower_price]):
                    continue

                if leader_adx > MAX_ADX_LEADER:
                    continue
                if vol_r < MIN_VOLUME_RATIO:
                    continue

                stop_distance = STOP_ATR_MULT * atr_val

                # LONG follower : COIN monte, follower baisse
                if (leader_perf > LEADER_PERF_THRESHOLD
                        and follower_perf < -FOLLOWER_DIVERGE_THRESHOLD
                        and z_val < ZSCORE_ENTRY):

                    stop_loss = follower_price - stop_distance
                    take_profit = follower_price + (TARGET_RISK_MULT * stop_distance)

                    signals.append(Signal(
                        action="LONG",
                        ticker=follower_ticker,
                        entry_price=follower_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "leader_perf": round(leader_perf * 100, 2),
                            "follower_perf": round(follower_perf * 100, 2),
                            "z_spread": round(z_val, 2),
                            "adx_leader": round(leader_adx, 1),
                        },
                    ))
                    traded_tickers.add(follower_ticker)

                # SHORT follower : COIN baisse, follower monte
                elif (leader_perf < -LEADER_PERF_THRESHOLD
                      and follower_perf > FOLLOWER_DIVERGE_THRESHOLD
                      and z_val > -ZSCORE_ENTRY):

                    stop_loss = follower_price + stop_distance
                    take_profit = follower_price - (TARGET_RISK_MULT * stop_distance)

                    signals.append(Signal(
                        action="SHORT",
                        ticker=follower_ticker,
                        entry_price=follower_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "leader_perf": round(leader_perf * 100, 2),
                            "follower_perf": round(follower_perf * 100, 2),
                            "z_spread": round(z_val, 2),
                            "adx_leader": round(leader_adx, 1),
                        },
                    ))
                    traded_tickers.add(follower_ticker)

        return signals
