"""
Strategie 4 : ETF NAV Premium/Discount

Edge structurel :
Les ETFs sectoriels devient parfois de leur NAV intrajournalier estime
(calcule via les composants). Quand le premium/discount depasse un seuil,
les Authorized Participants (AP) arbitrent, ramenant le prix vers la NAV.
On anticipe ce retour a la NAV.

Regles :
- Estime la NAV via la moyenne ponderee egale des returns des composants
- LONG si ETF price < NAV estimee - 0.15% (discount excessif)
- SHORT si ETF price > NAV estimee + 0.15% (premium excessif)
- Stop : 0.3% adverse
- Target : retour a la NAV (premium/discount = 0)
- Timing : 10:00-15:00 ET
- Filtre volume ETF > 500K, au moins 3 composants disponibles
"""
import pandas as pd
import numpy as np
from backtest_engine import BaseStrategy, Signal
import config


# Mapping ETF sectoriel -> composants principaux (poids egaux)
SECTOR_NAV = {
    "XLK": ["AAPL", "MSFT", "NVDA", "AVGO", "CRM"],    # Tech
    "XLF": ["JPM", "BAC", "WFC", "GS", "MS"],           # Finance
    "XLE": ["XOM", "CVX", "COP", "SLB", "EOG"],         # Energy
}

# Seuils
PREMIUM_THRESHOLD = 0.0015   # 0.15% min deviation pour entrer
STOP_PCT = 0.003             # 0.3% stop-loss
MIN_VOLUME = 500_000         # Volume minimum de l'ETF
MIN_COMPONENTS = 3           # Composants minimum disponibles


class ETFNavPremiumStrategy(BaseStrategy):
    name = "ETF NAV Premium/Discount"

    def get_required_tickers(self) -> list[str]:
        return [
            "XLK", "XLF", "XLE",
            "AAPL", "MSFT", "NVDA", "AVGO", "CRM",
            "JPM", "BAC", "WFC", "GS", "MS",
            "XOM", "CVX", "COP", "SLB", "EOG",
        ]

    def generate_signals(self, data: dict[str, pd.DataFrame], date) -> list[Signal]:
        signals = []
        traded_etfs = set()

        for etf, components in SECTOR_NAV.items():
            # --- Skip si ETF absent ---
            if etf not in data:
                continue

            df_etf = data[etf].copy()
            if len(df_etf) < 10:
                continue

            # --- Verifier le volume cumulatif de l'ETF ---
            total_volume = df_etf["volume"].sum()
            if total_volume < MIN_VOLUME:
                continue

            # --- Verifier le nombre de composants disponibles ---
            available_components = [c for c in components if c in data]
            if len(available_components) < MIN_COMPONENTS:
                continue

            # --- Calculer la NAV estimee ---
            # On utilise le return moyen des composants depuis l'open,
            # applique au prix d'ouverture de l'ETF
            etf_open = df_etf.iloc[0]["open"]

            # Calculer le return moyen des composants a chaque barre
            # Aligner les timestamps de tous les composants avec l'ETF
            component_returns = []
            for comp in available_components:
                df_comp = data[comp]
                if len(df_comp) < 5:
                    continue
                comp_open = df_comp.iloc[0]["open"]
                if comp_open <= 0:
                    continue
                # Return depuis l'open pour chaque barre
                comp_ret = df_comp["close"] / comp_open - 1.0
                component_returns.append(comp_ret)

            if len(component_returns) < MIN_COMPONENTS:
                continue

            # Moyenne ponderee egale des returns, alignee sur l'index de l'ETF
            avg_return = pd.DataFrame(component_returns).T.mean(axis=1)
            # Reindexer sur l'index de l'ETF via forward-fill
            common_idx = df_etf.index.intersection(avg_return.index)
            if len(common_idx) < 10:
                continue

            avg_return_aligned = avg_return.reindex(df_etf.index, method="ffill")
            nav_estimated = etf_open * (1 + avg_return_aligned)

            # --- Scanner les barres dans la fenetre 10:00-15:00 ---
            tradeable = df_etf.between_time("10:00", "15:00")

            for i in range(1, len(tradeable)):
                if etf in traded_etfs:
                    break

                ts = tradeable.index[i]
                bar = tradeable.iloc[i]
                price = bar["close"]
                nav = nav_estimated.get(ts, np.nan)

                if pd.isna(nav) or nav <= 0:
                    continue

                # Premium/discount : (price - NAV) / NAV
                premium = (price - nav) / nav

                # Volume cumule jusqu'a cette barre (filtre dynamique)
                vol_so_far = df_etf.loc[:ts, "volume"].sum()
                if vol_so_far < MIN_VOLUME:
                    continue

                # --- Entree LONG : discount excessif (prix sous NAV) ---
                if premium < -PREMIUM_THRESHOLD:
                    stop_loss = price * (1 - STOP_PCT)
                    take_profit = nav  # Retour a la NAV

                    signals.append(Signal(
                        action="LONG",
                        ticker=etf,
                        entry_price=price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "premium_pct": round(premium * 100, 4),
                            "nav_estimated": round(nav, 4),
                            "components_used": len(component_returns),
                            "etf_volume": int(vol_so_far),
                        },
                    ))
                    traded_etfs.add(etf)

                # --- Entree SHORT : premium excessif (prix au-dessus NAV) ---
                elif premium > PREMIUM_THRESHOLD:
                    stop_loss = price * (1 + STOP_PCT)
                    take_profit = nav  # Retour a la NAV

                    signals.append(Signal(
                        action="SHORT",
                        ticker=etf,
                        entry_price=price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=ts,
                        metadata={
                            "strategy": self.name,
                            "premium_pct": round(premium * 100, 4),
                            "nav_estimated": round(nav, 4),
                            "components_used": len(component_returns),
                            "etf_volume": int(vol_so_far),
                        },
                    ))
                    traded_etfs.add(etf)

        return signals
