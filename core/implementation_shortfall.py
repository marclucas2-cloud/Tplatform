"""
ImplementationShortfall — Mesure du cout cache de chaque trade.

ROC-005 : Mesure la difference entre le P&L theorique (signal)
et le P&L reel (fill).

Decomposition :
1. Slippage : fill price vs mid price au moment du signal
2. Latency : mouvement de prix entre signal et fill
3. Commission : cout de transaction
4. Spread : demi-spread paye

Total IS = slippage + latency + commission + spread
C'est le "cout cache" de chaque trade.

Stockage en memoire (liste de dicts) — pas de dependance DB.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable, Dict, Any, List

logger = logging.getLogger(__name__)

# Seuil d'alerte par defaut en bps
DEFAULT_ALERT_THRESHOLD_BPS = 5.0

# Nombre de jours de trading par an (approximation)
TRADING_DAYS_PER_YEAR = 252


class ImplementationShortfall:
    """Measure the difference between theoretical P&L (signal) and actual P&L (fill).

    Decomposition:
    1. Slippage: fill price vs mid price at signal time
    2. Latency: price movement between signal and fill
    3. Commission: transaction cost
    4. Spread: half-spread paid

    Total IS = slippage + latency + commission + spread
    This is the "hidden cost" of each trade.
    """

    def __init__(
        self,
        alert_threshold_bps: float = DEFAULT_ALERT_THRESHOLD_BPS,
        alerter: Optional[Callable] = None,
    ):
        """Initialise le tracker Implementation Shortfall.

        Args:
            alert_threshold_bps: seuil d'alerte en bps pour un trade individuel
            alerter: callback d'alerte optionnel (ex: Telegram send_alert)
        """
        self._alert_threshold_bps = alert_threshold_bps
        self._alerter = alerter

        # Stockage en memoire
        self._signals: Dict[str, dict] = {}   # signal_id -> signal data
        self._records: List[dict] = []          # Liste de tous les IS calcules

        logger.info(
            "ImplementationShortfall initialized — alert_threshold=%.1f bps",
            alert_threshold_bps,
        )

    # ------------------------------------------------------------------
    # Enregistrement signal
    # ------------------------------------------------------------------

    def on_signal(
        self,
        signal_id: str,
        symbol: str,
        signal_price: float,
        mid_price: float,
        spread: float,
        strategy: str,
        timestamp: Optional[str] = None,
    ):
        """Record signal price at the time of signal generation.

        Args:
            signal_id: identifiant unique du signal/trade
            symbol: instrument (ex: EURUSD, AAPL)
            signal_price: prix cible du signal (decision price)
            mid_price: prix mid au moment du signal
            spread: spread bid-ask au moment du signal (en prix, pas bps)
            strategy: nom de la strategie
            timestamp: ISO timestamp (auto-genere si absent)
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc).isoformat()

        self._signals[signal_id] = {
            "signal_id": signal_id,
            "symbol": symbol,
            "signal_price": signal_price,
            "mid_price": mid_price,
            "spread": spread,
            "strategy": strategy,
            "signal_timestamp": timestamp,
        }

        logger.debug(
            "Signal recorded: %s %s @ %.4f (mid=%.4f, spread=%.4f)",
            signal_id, symbol, signal_price, mid_price, spread,
        )

    # ------------------------------------------------------------------
    # Enregistrement fill
    # ------------------------------------------------------------------

    def on_fill(
        self,
        signal_id: str,
        fill_price: float,
        fill_qty: float,
        commission: float,
        timestamp: Optional[str] = None,
        side: str = "BUY",
    ) -> Optional[dict]:
        """Compute and store Implementation Shortfall for a fill.

        Args:
            signal_id: identifiant du signal (doit correspondre a on_signal)
            fill_price: prix d'execution reel
            fill_qty: quantite executee
            commission: commission totale en dollars/devise
            timestamp: ISO timestamp du fill (auto-genere si absent)
            side: "BUY" ou "SELL"

        Returns:
            dict avec la decomposition IS, ou None si signal inconnu
        """
        if signal_id not in self._signals:
            logger.warning("on_fill: signal_id '%s' inconnu — ignoring", signal_id)
            return None

        signal = self._signals[signal_id]

        if timestamp is None:
            timestamp = datetime.now(timezone.utc).isoformat()

        mid_price = signal["mid_price"]
        signal_price = signal["signal_price"]
        spread = signal["spread"]

        if mid_price <= 0:
            logger.error("mid_price <= 0 for signal %s, cannot compute IS", signal_id)
            return None

        # --- Decomposition IS ---
        # Direction : BUY = fill > mid est adverse, SELL = fill < mid est adverse
        side_upper = side.upper()
        direction = 1.0 if side_upper == "BUY" else -1.0

        # 1. Spread cost : demi-spread paye (en bps)
        half_spread = spread / 2.0
        spread_bps = (half_spread / mid_price) * 10_000

        # 2. Latency cost : mouvement du prix entre signal et moment du fill
        #    = (signal_price - mid_price) ajuste par la direction
        #    Si BUY et signal_price > mid_price, le marche a bouge contre nous
        latency_cost = direction * (signal_price - mid_price)
        latency_bps = (latency_cost / mid_price) * 10_000

        # 3. Slippage : fill price vs signal price (cout d'execution)
        #    Si BUY et fill > signal, on a paye plus cher
        slippage_cost = direction * (fill_price - signal_price)
        slippage_bps = (slippage_cost / mid_price) * 10_000

        # 4. Commission en bps
        notional = abs(fill_price * fill_qty)
        if notional > 0:
            commission_bps = (commission / notional) * 10_000
        else:
            commission_bps = 0.0

        # Total IS = somme des composantes
        total_is_bps = slippage_bps + latency_bps + commission_bps + spread_bps

        # Cout total en devise
        total_cost = (total_is_bps / 10_000) * notional

        # Extraire l'heure pour analyse par heure
        try:
            fill_dt = datetime.fromisoformat(timestamp)
            fill_hour = fill_dt.hour
        except (ValueError, TypeError):
            fill_hour = None

        record = {
            "signal_id": signal_id,
            "symbol": signal["symbol"],
            "strategy": signal["strategy"],
            "side": side_upper,
            "signal_price": signal_price,
            "mid_price": mid_price,
            "fill_price": fill_price,
            "fill_qty": fill_qty,
            "spread": spread,
            "commission": commission,
            "notional": notional,
            "signal_timestamp": signal["signal_timestamp"],
            "fill_timestamp": timestamp,
            "fill_hour": fill_hour,
            # Decomposition IS
            "slippage_bps": round(slippage_bps, 4),
            "latency_bps": round(latency_bps, 4),
            "commission_bps": round(commission_bps, 4),
            "spread_bps": round(spread_bps, 4),
            "total_is_bps": round(total_is_bps, 4),
            "total_cost": round(total_cost, 4),
        }

        self._records.append(record)

        logger.info(
            "IS recorded: %s %s %s — total=%.2f bps (slip=%.2f, lat=%.2f, comm=%.2f, spread=%.2f)",
            signal["strategy"], side_upper, signal["symbol"],
            total_is_bps, slippage_bps, latency_bps, commission_bps, spread_bps,
        )

        # Alerte si IS depasse le seuil
        if total_is_bps > self._alert_threshold_bps:
            msg = (
                f"IS ELEVE — {signal['strategy']} {signal['symbol']}\n"
                f"Total IS: {total_is_bps:.2f} bps (seuil: {self._alert_threshold_bps:.1f})\n"
                f"Decomposition: slip={slippage_bps:.2f} lat={latency_bps:.2f} "
                f"comm={commission_bps:.2f} spread={spread_bps:.2f}"
            )
            logger.warning(msg)
            if self._alerter:
                self._alerter(msg, level="warning")

        # Nettoyer le signal consomme
        del self._signals[signal_id]

        return record

    # ------------------------------------------------------------------
    # Consultation d'un trade
    # ------------------------------------------------------------------

    def get_record(self, signal_id: str) -> Optional[dict]:
        """Get IS breakdown for a single trade.

        Args:
            signal_id: identifiant du signal/trade

        Returns:
            dict avec la decomposition IS, ou None si introuvable
        """
        for record in self._records:
            if record["signal_id"] == signal_id:
                return dict(record)
        return None

    # ------------------------------------------------------------------
    # Rapport agrege
    # ------------------------------------------------------------------

    def get_report(self, period_days: int = 30) -> dict:
        """Generate aggregate IS report.

        Args:
            period_days: nombre de jours a inclure dans le rapport

        Returns:
            dict avec moyennes, ventilation par strategie/symbole/heure,
            pires trades, cout annuel estime, recommandations.
        """
        # Filtrer par periode
        cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)
        cutoff_iso = cutoff.isoformat()

        records = [
            r for r in self._records
            if r.get("fill_timestamp", "") >= cutoff_iso
        ]

        if not records:
            return {
                "avg_total_is_bps": 0.0,
                "avg_slippage_bps": 0.0,
                "avg_latency_bps": 0.0,
                "avg_commission_bps": 0.0,
                "avg_spread_bps": 0.0,
                "by_strategy": {},
                "by_symbol": {},
                "by_hour": {},
                "worst_trades": [],
                "estimated_annual_cost_usd": 0.0,
                "recommendations": [],
                "period_days": period_days,
                "n_trades": 0,
            }

        n = len(records)

        # Moyennes globales
        avg_total = sum(r["total_is_bps"] for r in records) / n
        avg_slippage = sum(r["slippage_bps"] for r in records) / n
        avg_latency = sum(r["latency_bps"] for r in records) / n
        avg_commission = sum(r["commission_bps"] for r in records) / n
        avg_spread = sum(r["spread_bps"] for r in records) / n

        # Par strategie
        by_strategy: Dict[str, Dict[str, Any]] = {}
        for r in records:
            strat = r["strategy"]
            if strat not in by_strategy:
                by_strategy[strat] = {"total_is": 0.0, "total_cost": 0.0, "count": 0}
            by_strategy[strat]["total_is"] += r["total_is_bps"]
            by_strategy[strat]["total_cost"] += r["total_cost"]
            by_strategy[strat]["count"] += 1

        for strat, data in by_strategy.items():
            data["avg_is"] = round(data["total_is"] / data["count"], 4)
            data["total_cost"] = round(data["total_cost"], 2)
            del data["total_is"]

        # Par symbole
        by_symbol: Dict[str, Dict[str, Any]] = {}
        for r in records:
            sym = r["symbol"]
            if sym not in by_symbol:
                by_symbol[sym] = {"total_is": 0.0, "count": 0}
            by_symbol[sym]["total_is"] += r["total_is_bps"]
            by_symbol[sym]["count"] += 1

        for sym, data in by_symbol.items():
            data["avg_is"] = round(data["total_is"] / data["count"], 4)
            del data["total_is"]

        # Par heure
        by_hour: Dict[str, Dict[str, Any]] = {}
        for r in records:
            hour = r.get("fill_hour")
            if hour is not None:
                h_key = str(hour)
                if h_key not in by_hour:
                    by_hour[h_key] = {"total_is": 0.0, "count": 0}
                by_hour[h_key]["total_is"] += r["total_is_bps"]
                by_hour[h_key]["count"] += 1

        for h_key, data in by_hour.items():
            data["avg_is"] = round(data["total_is"] / data["count"], 4)
            del data["total_is"]

        # Pires trades (top 5 par IS total)
        sorted_records = sorted(records, key=lambda r: r["total_is_bps"], reverse=True)
        worst_trades = [
            {
                "signal_id": r["signal_id"],
                "symbol": r["symbol"],
                "strategy": r["strategy"],
                "total_is_bps": r["total_is_bps"],
                "total_cost": r["total_cost"],
                "side": r["side"],
            }
            for r in sorted_records[:5]
        ]

        # Cout annuel estime
        total_cost_period = sum(r["total_cost"] for r in records)
        if period_days > 0:
            daily_cost = total_cost_period / period_days
            estimated_annual_cost = daily_cost * TRADING_DAYS_PER_YEAR
        else:
            estimated_annual_cost = 0.0

        # Recommandations
        recommendations = self._generate_recommendations(
            avg_total, by_strategy, by_symbol, records
        )

        return {
            "avg_total_is_bps": round(avg_total, 4),
            "avg_slippage_bps": round(avg_slippage, 4),
            "avg_latency_bps": round(avg_latency, 4),
            "avg_commission_bps": round(avg_commission, 4),
            "avg_spread_bps": round(avg_spread, 4),
            "by_strategy": by_strategy,
            "by_symbol": by_symbol,
            "by_hour": by_hour,
            "worst_trades": worst_trades,
            "estimated_annual_cost_usd": round(estimated_annual_cost, 2),
            "recommendations": recommendations,
            "period_days": period_days,
            "n_trades": n,
        }

    # ------------------------------------------------------------------
    # Recommandations automatiques
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_recommendations(
        avg_total_bps: float,
        by_strategy: Dict[str, Dict],
        by_symbol: Dict[str, Dict],
        records: List[dict],
    ) -> List[str]:
        """Genere des recommandations basees sur l'analyse IS.

        Regles :
            - Strategie avec avg IS > 3 bps -> suggerer limit orders
            - Symbole avec avg IS > 5 bps -> verifier liquidite
            - IS moyen global > 4 bps -> alerte generale
            - Commission dominante -> negocier les frais
        """
        recommendations = []

        # Strategie avec IS eleve
        for strat, data in by_strategy.items():
            if data["avg_is"] > 3.0 and data["count"] >= 3:
                recommendations.append(
                    f"Switch {strat} to limit orders (avg IS > 3bps: {data['avg_is']:.1f}bps)"
                )

        # Symbole avec IS eleve
        for sym, data in by_symbol.items():
            if data["avg_is"] > 5.0 and data["count"] >= 3:
                recommendations.append(
                    f"Review liquidity for {sym} (avg IS > 5bps: {data['avg_is']:.1f}bps)"
                )

        # IS moyen global trop eleve
        if avg_total_bps > 4.0:
            recommendations.append(
                f"Overall avg IS is high ({avg_total_bps:.1f}bps). "
                f"Consider reducing market orders and increasing limit order usage."
            )

        # Analyse composante dominante
        if records:
            n = len(records)
            avg_commission = sum(r["commission_bps"] for r in records) / n
            avg_spread = sum(r["spread_bps"] for r in records) / n
            avg_slippage = sum(r["slippage_bps"] for r in records) / n

            if avg_commission > avg_slippage and avg_commission > avg_spread and avg_commission > 1.5:
                recommendations.append(
                    f"Commission is the dominant cost ({avg_commission:.1f}bps). "
                    f"Negotiate lower fees or increase trade size."
                )

            if avg_spread > avg_slippage and avg_spread > avg_commission and avg_spread > 1.5:
                recommendations.append(
                    f"Spread is the dominant cost ({avg_spread:.1f}bps). "
                    f"Trade during higher-liquidity hours."
                )

        return recommendations
