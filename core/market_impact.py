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
from typing import Dict, List, Tuple

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

    def __init__(self, adv_overrides: Dict[str, int] | None = None):
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
                        price: float | None = None) -> float:
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
        capital_levels: List[int] | None = None,
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

    # -----------------------------------------------------------------
    # ROC-3 : Sizing futures avec levier structurel
    # -----------------------------------------------------------------

    # Specifications des contrats futures principaux
    FUTURES_SPECS = {
        "FESX": {
            "name": "Eurostoxx 50",
            "point_value": 10.0,       # EUR par point
            "currency": "EUR",
            "approx_price": 5000,      # points
            "notional_approx": 50_000,  # EUR par contrat
            "margin_approx": 3_000,    # EUR
            "cost_rt": 0.003,          # 0.3% round-trip
        },
        "FDAX": {
            "name": "DAX",
            "point_value": 25.0,
            "currency": "EUR",
            "approx_price": 18_000,
            "notional_approx": 450_000,
            "margin_approx": 25_000,
            "cost_rt": 0.002,
        },
        "FDXM": {
            "name": "Mini-DAX",
            "point_value": 5.0,
            "currency": "EUR",
            "approx_price": 18_000,
            "notional_approx": 90_000,
            "margin_approx": 5_000,
            "cost_rt": 0.003,
        },
        "CL": {
            "name": "Crude Oil (WTI)",
            "point_value": 1000.0,     # USD par point (1000 barils)
            "currency": "USD",
            "approx_price": 70,        # USD/bbl
            "notional_approx": 70_000,
            "margin_approx": 6_000,
            "cost_rt": 0.005,
        },
        "BZ": {
            "name": "Brent Crude",
            "point_value": 1000.0,
            "currency": "USD",
            "approx_price": 75,
            "notional_approx": 75_000,
            "margin_approx": 6_500,
            "cost_rt": 0.005,
        },
        "ES": {
            "name": "E-mini S&P 500",
            "point_value": 50.0,
            "currency": "USD",
            "approx_price": 5_200,
            "notional_approx": 260_000,
            "margin_approx": 13_000,
            "cost_rt": 0.001,
        },
        "MES": {
            "name": "Micro E-mini S&P 500",
            "point_value": 5.0,
            "currency": "USD",
            "approx_price": 5_200,
            "notional_approx": 26_000,
            "margin_approx": 1_300,
            "cost_rt": 0.002,
        },
        "EURUSD": {
            "name": "EUR/USD Spot FX",
            "point_value": 100_000,    # 1 lot standard = 100K units
            "currency": "USD",
            "approx_price": 1.08,
            "notional_approx": 108_000,
            "margin_approx": 3_600,     # 30:1 leverage FX
            "cost_rt": 0.00005,         # ~0.5 pip spread
        },
        "EURGBP": {
            "name": "EUR/GBP Spot FX",
            "point_value": 100_000,
            "currency": "GBP",
            "approx_price": 0.86,
            "notional_approx": 86_000,
            "margin_approx": 2_900,
            "cost_rt": 0.00008,
        },
        "EURJPY": {
            "name": "EUR/JPY Spot FX",
            "point_value": 100_000,
            "currency": "JPY",
            "approx_price": 163.0,
            "notional_approx": 16_300_000,  # JPY
            "margin_approx": 4_000,         # EUR equiv
            "cost_rt": 0.00012,
        },
    }

    def calculate_futures_sizing(
        self,
        capital: float,
        instrument: str,
        max_leverage: float = 3.0,
        allocation_pct: float = 1.0,
    ) -> dict:
        """Calcule le nombre de contrats futures en respectant le levier max.

        Le levier structurel est le ratio notionnel_total / capital_alloue.
        On ne depasse jamais max_leverage pour controler le risque de ruine.

        Exemples avec $5K capital et levier 3:1 :
          FESX : $15K notionnel max -> 0 contrats (EUR50K > $15K) -> micro si dispo
          EUR/USD : $10K position (levier 2:1) -> 0.1 lot mini
          MES : $15K notionnel max -> 0 contrats ($26K > $15K)

        Args:
            capital: capital total du portefeuille en USD
            instrument: code du contrat (FESX, CL, EURUSD, etc.)
            max_leverage: levier maximum autorise (defaut 3.0)
            allocation_pct: fraction du capital allouee a cet instrument (defaut 1.0)

        Returns:
            {
                instrument, name, contracts, notional_per_contract,
                total_notional, margin_required, leverage_effective,
                capital_allocated, feasible, reason, cost_estimate_rt
            }
        """
        spec = self.FUTURES_SPECS.get(instrument)
        if spec is None:
            return {
                "instrument": instrument,
                "name": "UNKNOWN",
                "contracts": 0,
                "feasible": False,
                "reason": f"Instrument {instrument} non reconnu",
            }

        allocated = capital * allocation_pct
        max_notional = allocated * max_leverage
        notional_per_contract = spec["notional_approx"]
        margin_per_contract = spec["margin_approx"]

        # Nombre de contrats = floor(max_notional / notional_per_contract)
        # Mais aussi contraint par la marge disponible
        if notional_per_contract <= 0:
            contracts = 0
        else:
            contracts_by_notional = int(max_notional / notional_per_contract)
            contracts_by_margin = int(allocated / margin_per_contract) if margin_per_contract > 0 else 0
            contracts = min(contracts_by_notional, contracts_by_margin)

        total_notional = contracts * notional_per_contract
        margin_required = contracts * margin_per_contract
        leverage_effective = total_notional / allocated if allocated > 0 else 0

        feasible = contracts > 0
        if not feasible:
            reason = (
                f"Capital alloue ${allocated:,.0f} insuffisant. "
                f"1 contrat = ${notional_per_contract:,.0f} notionnel, "
                f"${margin_per_contract:,.0f} margin. "
                f"Minimum requis: ${notional_per_contract / max_leverage:,.0f}"
            )
        else:
            reason = "OK"

        cost_rt = contracts * notional_per_contract * spec["cost_rt"]

        result = {
            "instrument": instrument,
            "name": spec["name"],
            "contracts": contracts,
            "notional_per_contract": notional_per_contract,
            "total_notional": total_notional,
            "margin_required": margin_required,
            "leverage_effective": round(leverage_effective, 2),
            "capital_allocated": round(allocated, 2),
            "feasible": feasible,
            "reason": reason,
            "cost_estimate_rt": round(cost_rt, 2),
            "currency": spec["currency"],
        }

        if feasible:
            logger.info(
                "Futures sizing %s: %d contracts, notional $%s, leverage %.1fx",
                instrument, contracts, f"{total_notional:,.0f}", leverage_effective,
            )
        else:
            logger.warning("Futures sizing %s: NOT FEASIBLE — %s", instrument, reason)

        return result

    def simulate_futures_portfolio(
        self,
        capital: float,
        instruments: List[str],
        max_leverage: float = 3.0,
    ) -> dict:
        """Simule le sizing pour un portefeuille de futures.

        Repartit le capital equitablement entre les instruments,
        puis calcule le sizing pour chacun.

        Args:
            capital: capital total USD
            instruments: liste de codes instruments
            max_leverage: levier max global

        Returns:
            {
                instruments: {instrument: sizing_result},
                total_notional, total_margin, effective_leverage,
                feasible_count, total_instruments
            }
        """
        n = len(instruments)
        if n == 0:
            return {"instruments": {}, "total_notional": 0, "feasible_count": 0}

        alloc_per = 1.0 / n
        results = {}
        total_notional = 0
        total_margin = 0
        feasible_count = 0

        for inst in instruments:
            sizing = self.calculate_futures_sizing(
                capital, inst, max_leverage, alloc_per
            )
            results[inst] = sizing
            if sizing["feasible"]:
                total_notional += sizing["total_notional"]
                total_margin += sizing["margin_required"]
                feasible_count += 1

        return {
            "instruments": results,
            "total_notional": total_notional,
            "total_margin": total_margin,
            "effective_leverage": round(total_notional / capital, 2) if capital > 0 else 0,
            "feasible_count": feasible_count,
            "total_instruments": n,
            "capital": capital,
        }

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
