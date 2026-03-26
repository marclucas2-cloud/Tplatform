"""
Market Impact Model — Almgren-Chriss simplifie.

Estime le slippage additionnel cause par la taille de l'ordre,
au-dela du slippage de base ($0.005/share + 0.02%).

Utilise dans le pipeline pour :
  1. Alerter si un ordre depasse le seuil d'impact acceptable
  2. Simuler la degradation de performance a differents niveaux de capital
  3. Guider les decisions de scaling (quelles strategies garder/exclure)

Usage :
    model = MarketImpactModel()
    impact = model.estimate_impact("MARA", 5000, 25.0)
    # impact = 0.0015 → 0.15% de slippage additionnel

    report = model.simulate_scaling(strategies, [25000, 50000, 100000])
"""

import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Nombre de barres 5-min dans une session reguliere US (9:30-16:00 = 6.5h = 78 barres)
BARS_PER_DAY = 78

# Slippage de base deja compte dans les backtests (ne pas doubler)
BASE_SLIPPAGE = 0.0002  # 0.02%

# Seuil d'alerte : au-dela, la strategie n'est pas scalable a ce capital
IMPACT_ALERT_THRESHOLD = 0.0020  # 0.20%


class MarketImpactModel:
    """Almgren-Chriss simplifie pour estimer le slippage par taille d'ordre.

    Le modele decompose l'impact en :
      - Temporary impact : cout de liquidite immediat, proportionnel a sqrt(participation)
      - Permanent impact : deplacement du prix d'equilibre, proportionnel a participation

    Parametres calibres sur les donnees empiriques US equities (2020-2025).
    """

    # ADV moyen par ticker en USD (estime sur les 30 derniers jours)
    ADV_ESTIMATES: Dict[str, int] = {
        # Mega-caps / ETFs — tres liquides
        'SPY': 50_000_000_000,
        'QQQ': 20_000_000_000,
        'IWM': 5_000_000_000,
        'TLT': 2_000_000_000,
        'GLD': 2_000_000_000,
        'USO': 500_000_000,
        'SVXY': 200_000_000,
        'XLE': 1_000_000_000,
        'XLV': 500_000_000,
        # Large caps
        'AAPL': 10_000_000_000,
        'MSFT': 8_000_000_000,
        'NVDA': 15_000_000_000,
        'TSLA': 15_000_000_000,
        'AMD': 5_000_000_000,
        'META': 8_000_000_000,
        'GOOGL': 6_000_000_000,
        'AMZN': 8_000_000_000,
        'AVGO': 3_000_000_000,
        # Crypto-proxy — liquidite variable
        'COIN': 500_000_000,
        'MARA': 200_000_000,
        'MSTR': 300_000_000,
        'RIOT': 100_000_000,
        'BITF': 50_000_000,
        # Semis (pairs)
        'MU': 1_000_000_000,
        'AMAT': 800_000_000,
        # Finance
        'JPM': 3_000_000_000,
        'GS': 1_500_000_000,
        # Other frequently traded
        'BA': 2_000_000_000,
        'NKE': 1_000_000_000,
    }

    # Coefficients Almgren-Chriss (calibres sur empirical US equities mid/small cap)
    # Sources : Almgren et al. 2005, Kissell & Glantz 2003
    TEMP_COEFF = 0.005      # coefficient impact temporaire
    TEMP_EXPONENT = 0.5     # exposant (racine carree)
    PERM_COEFF = 0.002      # coefficient impact permanent

    def __init__(self, adv_overrides: Optional[Dict[str, int]] = None):
        """
        Args:
            adv_overrides: surcharges manuelles des ADV par ticker.
        """
        self.adv = dict(self.ADV_ESTIMATES)
        if adv_overrides:
            self.adv.update(adv_overrides)

    def get_adv(self, ticker: str) -> int:
        """Retourne l'ADV estime pour un ticker (defaut $1B si inconnu)."""
        return self.adv.get(ticker, 1_000_000_000)

    def estimate_participation_rate(self, ticker: str, order_notional: float) -> float:
        """Calcule le taux de participation (ordre / volume moyen par barre 5-min).

        Args:
            ticker: symbole du titre.
            order_notional: montant USD de l'ordre.

        Returns:
            Taux de participation (0.0 a 1.0+). > 0.05 = problematique.
        """
        adv = self.get_adv(ticker)
        avg_bar_volume = adv / BARS_PER_DAY
        if avg_bar_volume <= 0:
            return 1.0
        return order_notional / avg_bar_volume

    def estimate_impact(self, ticker: str, order_notional: float,
                        price: Optional[float] = None) -> float:
        """Estime l'impact de marche total (temporary + permanent).

        Args:
            ticker: symbole du titre.
            order_notional: montant USD de l'ordre.
            price: prix actuel (non utilise dans le modele simplifie, reserve pour V2).

        Returns:
            Impact en fraction (ex: 0.0015 = 0.15% de slippage additionnel).
            Minimum BASE_SLIPPAGE (0.02%) car on ne peut pas avoir moins que le spread.
        """
        participation = self.estimate_participation_rate(ticker, order_notional)

        temporary = self.TEMP_COEFF * (participation ** self.TEMP_EXPONENT)
        permanent = self.PERM_COEFF * participation

        total_impact = temporary + permanent
        return max(total_impact, BASE_SLIPPAGE)

    def estimate_impact_detail(self, ticker: str, order_notional: float) -> dict:
        """Version detaillee avec decomposition de l'impact.

        Returns:
            {
                ticker, order_notional, adv, participation_rate,
                temporary_impact, permanent_impact, total_impact,
                impact_bps, alert, scalable
            }
        """
        adv = self.get_adv(ticker)
        participation = self.estimate_participation_rate(ticker, order_notional)

        temporary = self.TEMP_COEFF * (participation ** self.TEMP_EXPONENT)
        permanent = self.PERM_COEFF * participation
        total = max(temporary + permanent, BASE_SLIPPAGE)

        return {
            "ticker": ticker,
            "order_notional": order_notional,
            "adv": adv,
            "participation_rate": round(participation, 6),
            "temporary_impact": round(temporary, 6),
            "permanent_impact": round(permanent, 6),
            "total_impact": round(total, 6),
            "impact_bps": round(total * 10_000, 2),
            "alert": total > IMPACT_ALERT_THRESHOLD,
            "scalable": total <= IMPACT_ALERT_THRESHOLD,
        }

    def check_order(self, ticker: str, order_notional: float) -> Tuple[bool, str]:
        """Verifie si un ordre est acceptable du point de vue market impact.

        Returns:
            (ok, message) — ok=False si impact > seuil d'alerte.
        """
        detail = self.estimate_impact_detail(ticker, order_notional)
        if detail["alert"]:
            msg = (
                f"MARKET IMPACT ALERT: {ticker} ordre ${order_notional:,.0f} "
                f"→ impact {detail['impact_bps']:.1f}bps "
                f"(participation {detail['participation_rate']:.4f}, "
                f"ADV ${detail['adv']:,.0f})"
            )
            logger.warning(msg)
            return False, msg
        return True, f"{ticker}: impact {detail['impact_bps']:.1f}bps — OK"

    def simulate_scaling(
        self,
        strategies: Dict[str, dict],
        capital_levels: Optional[List[int]] = None,
    ) -> Dict[int, Dict[str, dict]]:
        """Simule la performance a differents niveaux de capital.

        Args:
            strategies: {
                name: {
                    tickers: [str],          # tickers utilises par la strategie
                    allocation_pct: float,   # % du capital alloue (ex: 0.10)
                    sharpe: float,           # Sharpe backtest
                    avg_trades_per_day: float,
                }
            }
            capital_levels: liste de niveaux de capital a simuler.

        Returns:
            {
                capital: {
                    strategy_name: {
                        order_notional, max_impact, avg_impact,
                        sharpe_adjusted, scalable, alerts: [str]
                    }
                }
            }
        """
        if capital_levels is None:
            capital_levels = [25_000, 50_000, 100_000, 250_000]

        results = {}

        for capital in capital_levels:
            results[capital] = {}

            for name, strat in strategies.items():
                alloc = strat.get("allocation_pct", 0.05)
                tickers = strat.get("tickers", [])
                sharpe = strat.get("sharpe", 1.0)
                order_notional = capital * alloc

                if not tickers:
                    results[capital][name] = {
                        "order_notional": order_notional,
                        "max_impact": BASE_SLIPPAGE,
                        "avg_impact": BASE_SLIPPAGE,
                        "sharpe_adjusted": sharpe,
                        "scalable": True,
                        "alerts": [],
                    }
                    continue

                # Calculer l'impact pour chaque ticker (pire cas = position entiere sur 1 ticker)
                impacts = []
                alerts = []
                for ticker in tickers:
                    impact = self.estimate_impact(ticker, order_notional)
                    impacts.append(impact)
                    if impact > IMPACT_ALERT_THRESHOLD:
                        alerts.append(
                            f"{ticker}: {impact*10000:.1f}bps "
                            f"(${order_notional:,.0f})"
                        )

                max_impact = max(impacts) if impacts else BASE_SLIPPAGE
                avg_impact = sum(impacts) / len(impacts) if impacts else BASE_SLIPPAGE

                # Ajuster le Sharpe en retirant l'impact additionnel
                # Impact au-dela du base slippage deja compte dans les backtests
                extra_impact = max(avg_impact - BASE_SLIPPAGE, 0)
                # Hypothese : chaque trade perd extra_impact en plus
                # Annualise : avg_trades_per_day * 252 * extra_impact
                trades_per_day = strat.get("avg_trades_per_day", 1.0)
                annual_drag = trades_per_day * 252 * extra_impact
                # Sharpe ajuste (approximation : drag / vol annualisee ~15%)
                vol_estimate = 0.15
                sharpe_adjusted = sharpe - (annual_drag / vol_estimate)

                results[capital][name] = {
                    "order_notional": round(order_notional, 2),
                    "max_impact": round(max_impact, 6),
                    "avg_impact": round(avg_impact, 6),
                    "sharpe_adjusted": round(sharpe_adjusted, 4),
                    "scalable": max_impact <= IMPACT_ALERT_THRESHOLD,
                    "alerts": alerts,
                }

        return results

    def generate_report(self, scaling_results: Dict[int, Dict[str, dict]]) -> str:
        """Genere un rapport markdown a partir des resultats de simulate_scaling."""
        lines = ["# Market Impact — Rapport de Scaling\n"]

        for capital in sorted(scaling_results.keys()):
            lines.append(f"\n## Capital ${capital:,}\n")
            lines.append("| Strategie | Ordre | Impact max | Sharpe ajuste | Scalable |")
            lines.append("|-----------|-------|------------|---------------|----------|")

            strats = scaling_results[capital]
            for name in sorted(strats.keys()):
                s = strats[name]
                scalable_str = "OK" if s["scalable"] else "**NON**"
                lines.append(
                    f"| {name} | ${s['order_notional']:,.0f} "
                    f"| {s['max_impact']*10000:.1f}bps "
                    f"| {s['sharpe_adjusted']:.2f} "
                    f"| {scalable_str} |"
                )

            # Alertes
            alerts = []
            for name, s in strats.items():
                alerts.extend(s["alerts"])
            if alerts:
                lines.append(f"\n**Alertes ({len(alerts)}):**")
                for a in alerts:
                    lines.append(f"- {a}")

        return "\n".join(lines)
