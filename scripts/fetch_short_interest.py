"""
Short Interest Fetcher — donnees short interest depuis yfinance (proxy FINRA).

Les donnees de short interest sont publiees par FINRA bi-mensuellement
(15 et fin de mois). Ce script les recupere via yfinance comme proxy.

Signaux exploitables :
  - SI ratio eleve (> 20% du float) → potentiel short squeeze
  - SI en baisse (> 20% reduction) → signal de covering (bullish)
  - SI en hausse rapide → conviction bearish forte
  - Days to cover > 5 → short squeeze risk

Usage :
    fetcher = ShortInterestFetcher()

    # Donnees actuelles
    si_data = fetcher.fetch_latest(["MARA", "RIOT", "COIN", "MSTR"])
    for ticker, data in si_data.items():
        print(f"{ticker}: SI ratio={data['short_ratio']:.1f}%, "
              f"days_to_cover={data['days_to_cover']:.1f}")

    # Detection de covering
    current = fetcher.fetch_latest(["MARA"])
    previous = {"MARA": {"short_interest": 50_000_000}}
    signal = fetcher.detect_covering_signal(current, previous)
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Check yfinance
try:
    import yfinance as yf
    _HAS_YFINANCE = True
except ImportError:
    _HAS_YFINANCE = False
    logger.warning("yfinance non installe — short interest desactive (pip install yfinance)")


class ShortInterestFetcher:
    """Fetch donnees short interest depuis yfinance (proxy FINRA bi-mensuel).

    yfinance fournit :
      - shortPercentOfFloat : % du float shorte
      - sharesShort : nombre d'actions shortees
      - sharesShortPriorMonth : SI du mois precedent
      - shortRatio (days to cover) : jours necessaires pour couvrir les shorts
      - dateShortInterest : date du dernier rapport
    """

    # Seuils de detection
    COVERING_THRESHOLD = 0.20    # Baisse de > 20% du SI → signal covering
    SQUEEZE_SI_THRESHOLD = 0.20  # SI > 20% du float → squeeze risk
    HIGH_DTC_THRESHOLD = 5.0     # Days to cover > 5 → squeeze risk eleve

    def __init__(self):
        if not _HAS_YFINANCE:
            logger.error(
                "yfinance non disponible. "
                "Installer avec : pip install yfinance"
            )

    def fetch_latest(self, tickers: List[str]) -> Dict[str, dict]:
        """Retourne le short interest pour chaque ticker.

        Args:
            tickers: liste de symboles.

        Returns:
            {
                ticker: {
                    short_interest: int,       # nombre d'actions shortees
                    short_ratio: float,        # % du float shorte (0-100)
                    days_to_cover: float,      # jours pour couvrir
                    prior_month_si: int,       # SI du mois precedent
                    si_change_pct: float,      # variation en % vs mois precedent
                    report_date: str,          # date du rapport FINRA
                    squeeze_risk: str,         # "low", "medium", "high"
                    float_shares: int,         # nombre d'actions en circulation libre
                }
            }
        """
        if not _HAS_YFINANCE:
            logger.error("yfinance non disponible")
            return {}

        results = {}

        for ticker in tickers:
            try:
                data = self._fetch_single(ticker)
                if data:
                    results[ticker] = data
            except Exception as e:
                logger.warning(f"Erreur fetch SI pour {ticker}: {e}")
                results[ticker] = self._empty_result(ticker)

        return results

    def detect_covering_signal(
        self,
        current_si: Dict[str, dict],
        previous_si: Dict[str, dict],
    ) -> Dict[str, dict]:
        """Detecte les signaux de short covering.

        Si le SI baisse de > COVERING_THRESHOLD (20%) → signal bullish.
        Si le SI monte de > 20% → signal bearish renforce.

        Args:
            current_si: donnees SI actuelles (output de fetch_latest).
            previous_si: donnees SI precedentes (sauvegardees).

        Returns:
            {
                ticker: {
                    signal: "covering" | "building" | "neutral",
                    si_change_pct: float,
                    description: str,
                }
            }
        """
        signals = {}

        for ticker in current_si:
            current = current_si[ticker]
            previous = previous_si.get(ticker, {})

            curr_si = current.get("short_interest", 0)
            prev_si = previous.get("short_interest", 0)

            if prev_si <= 0:
                signals[ticker] = {
                    "signal": "neutral",
                    "si_change_pct": 0.0,
                    "description": f"{ticker}: pas de donnees precedentes",
                }
                continue

            change_pct = (curr_si - prev_si) / prev_si

            if change_pct <= -self.COVERING_THRESHOLD:
                signal = "covering"
                desc = (
                    f"{ticker}: SHORT COVERING detecte — SI en baisse de "
                    f"{abs(change_pct)*100:.1f}% "
                    f"({prev_si:,} → {curr_si:,})"
                )
                logger.info(desc)
            elif change_pct >= self.COVERING_THRESHOLD:
                signal = "building"
                desc = (
                    f"{ticker}: SHORT BUILDING — SI en hausse de "
                    f"{change_pct*100:.1f}% "
                    f"({prev_si:,} → {curr_si:,})"
                )
                logger.info(desc)
            else:
                signal = "neutral"
                desc = (
                    f"{ticker}: SI stable — variation {change_pct*100:+.1f}%"
                )

            signals[ticker] = {
                "signal": signal,
                "si_change_pct": round(change_pct * 100, 2),
                "description": desc,
            }

        return signals

    def generate_report(self, si_data: Dict[str, dict]) -> str:
        """Genere un rapport markdown du short interest.

        Args:
            si_data: output de fetch_latest.

        Returns:
            Rapport markdown.
        """
        lines = [
            "# Short Interest Report",
            f"",
            f"Date : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"Tickers analyses : {len(si_data)}",
            f"",
            "| Ticker | SI (shares) | SI Ratio | DTC | Change vs prior | Squeeze Risk |",
            "|--------|-------------|----------|-----|-----------------|--------------|",
        ]

        for ticker in sorted(si_data.keys()):
            d = si_data[ticker]
            si = d.get("short_interest", 0)
            ratio = d.get("short_ratio", 0)
            dtc = d.get("days_to_cover", 0)
            change = d.get("si_change_pct", 0)
            risk = d.get("squeeze_risk", "?")

            lines.append(
                f"| {ticker} | {si:,} | {ratio:.1f}% "
                f"| {dtc:.1f} | {change:+.1f}% | {risk} |"
            )

        # Alertes
        alerts = []
        for ticker, d in si_data.items():
            if d.get("squeeze_risk") == "high":
                alerts.append(
                    f"- **{ticker}** : squeeze risk eleve "
                    f"(SI {d['short_ratio']:.1f}%, DTC {d['days_to_cover']:.1f})"
                )
            if d.get("si_change_pct", 0) < -20:
                alerts.append(
                    f"- **{ticker}** : covering massif detecte "
                    f"({d['si_change_pct']:+.1f}%)"
                )

        if alerts:
            lines.extend(["", "## Alertes", ""])
            lines.extend(alerts)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_single(self, ticker: str) -> Optional[dict]:
        """Fetch le short interest pour un seul ticker via yfinance."""
        info = yf.Ticker(ticker).info

        short_interest = info.get("sharesShort", 0) or 0
        short_pct_float = (info.get("shortPercentOfFloat", 0) or 0) * 100
        short_ratio = info.get("shortRatio", 0) or 0  # days to cover
        prior_month = info.get("sharesShortPriorMonth", 0) or 0
        float_shares = info.get("floatShares", 0) or 0

        # Calculer la variation vs mois precedent
        si_change_pct = 0.0
        if prior_month > 0:
            si_change_pct = ((short_interest - prior_month) / prior_month) * 100

        # Date du rapport
        report_ts = info.get("dateShortInterest")
        report_date = ""
        if report_ts:
            try:
                report_date = datetime.fromtimestamp(report_ts).strftime("%Y-%m-%d")
            except (ValueError, TypeError, OSError):
                report_date = str(report_ts)

        # Evaluer le squeeze risk
        squeeze_risk = self._assess_squeeze_risk(
            short_pct_float, short_ratio, si_change_pct
        )

        return {
            "short_interest": int(short_interest),
            "short_ratio": round(short_pct_float, 2),
            "days_to_cover": round(short_ratio, 2),
            "prior_month_si": int(prior_month),
            "si_change_pct": round(si_change_pct, 2),
            "report_date": report_date,
            "squeeze_risk": squeeze_risk,
            "float_shares": int(float_shares),
        }

    def _assess_squeeze_risk(self, si_ratio: float, dtc: float,
                              si_change: float) -> str:
        """Evalue le risque de short squeeze.

        Criteres :
          - high : SI > 20% ET DTC > 5 (ou SI en hausse rapide)
          - medium : SI > 10% OU DTC > 3
          - low : sinon
        """
        if (si_ratio > self.SQUEEZE_SI_THRESHOLD * 100
                and dtc > self.HIGH_DTC_THRESHOLD):
            return "high"
        if si_ratio > self.SQUEEZE_SI_THRESHOLD * 100 and si_change > 20:
            return "high"
        if si_ratio > 10 or dtc > 3:
            return "medium"
        return "low"

    @staticmethod
    def _empty_result(ticker: str) -> dict:
        """Retourne un resultat vide pour un ticker en erreur."""
        return {
            "short_interest": 0,
            "short_ratio": 0.0,
            "days_to_cover": 0.0,
            "prior_month_si": 0,
            "si_change_pct": 0.0,
            "report_date": "",
            "squeeze_risk": "unknown",
            "float_shares": 0,
        }
