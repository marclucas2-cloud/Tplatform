"""
Strategie 6 : Crypto-Proxy Regime Switch

Edge structurel :
Les crypto-proxies (COIN, MARA, MSTR) suivent normalement BTC/crypto.
Quand cette correlation se brise intraday (crypto monte mais MARA baisse,
ou l'inverse), c'est souvent un signal de regime : soit le stock rattrape,
soit il y a une raison fondamentale.
On trade le rattrapage quand la decorrelation est temporaire.

COIN sert de proxy du "crypto sentiment". Si COIN monte et MARA/MSTR
baissent, MARA/MSTR devraient rattraper.

Regles :
- LONG MARA/MSTR : COIN perf > +1% ET MARA/MSTR perf < -0.5%, z-score spread < -1.5
- SHORT MARA/MSTR : COIN perf < -1% ET MARA/MSTR perf > +0.5%
- Stop : 2x ATR(14)
- Target : fermeture du spread (1.5x risk)
- Timing : 10:00-14:30 ET
- Filtre : volume > 1x moyenne, ADX COIN < 40, gap open < 3%
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
from utils.indicators import adx, zscore_spread, volume_ratio
import config


# Tickers : COIN est le leader, MARA/MSTR sont les followers
LEADER = "COIN"
FOLLOWERS = ["MARA", "MSTR"]

# Seuils
LEADER_PERF_THRESHOLD = 0.01    # COIN doit avoir bouge > 1% depuis l'open
FOLLOWER_DIVERGE_THRESHOLD = 0.005  # Follower doit avoir bouge > 0.5% dans l'autre sens
ZSCORE_ENTRY = -1.5             # Z-score du spread pour confirmer la decorrelation
MAX_ADX_LEADER = 40             # Si ADX COIN > 40, c'est du momentum propre, pas de rattrapage
MAX_GAP_PCT = 3.0               # Si gap > 3%, le gap EST la decorrelation
MIN_VOLUME_RATIO = 1.0          # Volume follower >= 1x sa moyenne
ATR_PERIOD = 14                 # Periode ATR
STOP_ATR_MULT = 2.0            # Stop = 2x ATR
TARGET_RISK_MULT = 1.5         # Target = 1.5x risk (si spread ne ferme pas)
ZSCORE_LOOKBACK = 20            # Lookback pour z-score du spread


class CryptoProxyRegimeStrategy(BaseStrategy):
    name = "Crypto-Proxy Regime Switch"

    def get_required_tickers(self) -> list[str]:
        return ["COIN", "MARA", "MSTR", "SPY"]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        traded_tickers = set()

        # --- Le leader (COIN) doit etre present ---
        if LEADER not in data:
            return signals

        df_leader = data[LEADER].copy()
        if len(df_leader) < 30:
            return signals

        # --- Calculer la performance du leader depuis l'open ---
        leader_open = df_leader.iloc[0]["open"]
        if leader_open <= 0:
            return signals

        # ADX du leader : si trop fort, pas de rattrapage
        df_leader["adx_val"] = adx(df_leader, period=ATR_PERIOD)

        # Performance du leader a chaque barre (depuis l'open)
        df_leader["perf"] = df_leader["close"] / leader_open - 1.0

        for follower_ticker in FOLLOWERS:
            if follower_ticker not in data:
                continue
            if follower_ticker in traded_tickers:
                continue

            df_follower = data[follower_ticker].copy()
            if len(df_follower) < 30:
                continue

            follower_open = df_follower.iloc[0]["open"]
            if follower_open <= 0:
                continue

            # --- Filtre gap : si le follower a gappe > 3%, skip ---
            gap_pct = abs(follower_open / df_follower.iloc[0]["close"] - 1.0) * 100
            # Approximation : comparer open du jour vs close de la premiere barre
            # Meilleur : si on avait le close de la veille. On utilise le gap
            # intra-open comme proxy simplifie.
            # Verifier plutot le gap depuis le premier prix disponible
            if len(df_follower) > 1:
                first_close = df_follower.iloc[0]["close"]
                if first_close > 0:
                    day_gap = abs(df_follower.iloc[1]["open"] / first_close - 1.0) * 100
                    # On verifie aussi le gap d'ouverture du jour complet
                    intraday_range = (df_follower["high"].max() - df_follower["low"].min()) / follower_open * 100
                    # Si le gap initial est > 3%, c'est event-driven
                    # On approxime : si la premiere barre a bouge > 3%, skip
                    first_bar_move = abs(df_follower.iloc[0]["close"] - df_follower.iloc[0]["open"]) / follower_open * 100
                    if first_bar_move > MAX_GAP_PCT:
                        continue

            # --- Performance du follower depuis l'open ---
            df_follower["perf"] = df_follower["close"] / follower_open - 1.0

            # --- ATR du follower pour le stop ---
            df_follower["tr"] = pd.concat([
                df_follower["high"] - df_follower["low"],
                (df_follower["high"] - df_follower["close"].shift(1)).abs(),
                (df_follower["low"] - df_follower["close"].shift(1)).abs(),
            ], axis=1).max(axis=1)
            df_follower["atr"] = df_follower["tr"].rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()

            # --- Volume ratio du follower ---
            df_follower["vol_ratio"] = volume_ratio(df_follower["volume"], lookback=20)

            # --- Z-score du spread COIN vs follower ---
            # Aligner sur les timestamps communs
            common_idx = df_leader.index.intersection(df_follower.index)
            if len(common_idx) < ZSCORE_LOOKBACK + 5:
                continue

            leader_prices = df_leader.loc[common_idx, "close"]
            follower_prices = df_follower.loc[common_idx, "close"]
            z_spread = zscore_spread(leader_prices, follower_prices, lookback=ZSCORE_LOOKBACK)

            # --- Scanner les barres dans la fenetre 10:00-14:30 ---
            tradeable_idx = common_idx[
                (common_idx.time >= pd.Timestamp("10:00").time()) &
                (common_idx.time <= pd.Timestamp("14:30").time())
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

                # Verifier toutes les valeurs
                if any(pd.isna(v) for v in [leader_perf, follower_perf, leader_adx, z_val, atr_val, vol_r, follower_price]):
                    continue

                # --- Filtre ADX leader ---
                if leader_adx > MAX_ADX_LEADER:
                    continue

                # --- Filtre volume ---
                if vol_r < MIN_VOLUME_RATIO:
                    continue

                stop_distance = STOP_ATR_MULT * atr_val

                # --- LONG follower : COIN monte, follower baisse ---
                if (leader_perf > LEADER_PERF_THRESHOLD
                        and follower_perf < -FOLLOWER_DIVERGE_THRESHOLD
                        and z_val < ZSCORE_ENTRY):

                    stop_loss = follower_price - stop_distance
                    # Target : fermeture du spread, ou 1.5x risk
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
                            "vol_ratio": round(vol_r, 2),
                            "atr": round(atr_val, 4),
                        },
                    ))
                    traded_tickers.add(follower_ticker)

                # --- SHORT follower : COIN baisse, follower monte ---
                elif (leader_perf < -LEADER_PERF_THRESHOLD
                      and follower_perf > FOLLOWER_DIVERGE_THRESHOLD
                      and z_val > -ZSCORE_ENTRY):  # z_val > 1.5 (inverse)

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
                            "vol_ratio": round(vol_r, 2),
                            "atr": round(atr_val, 4),
                        },
                    ))
                    traded_tickers.add(follower_ticker)

        return signals
