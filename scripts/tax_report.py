"""
Tax Report Generator — rapport fiscal pour la declaration francaise.

Genere un rapport annuel avec :
  - Plus/moins-values par trade
  - Detection des wash sales (rachat < 30 jours apres vente a perte)
  - Separation court terme / long terme (> 1 an)
  - Candidats au tax-loss harvesting
  - Export CSV compatible declaration fiscale FR (formulaire 2074)

Usage :
    generator = TaxReportGenerator()
    trades = load_trades_from_alpaca(year=2026)
    report = generator.generate_annual_report(trades, year=2026)
    generator.export_csv(trades, "output/tax_2026.csv")
    print(generator.format_report(report))

Note fiscale FR :
    - PFU (Prelevement Forfaitaire Unique) = 30% (12.8% IR + 17.2% PS)
    - Ou option bareme progressif IR + 17.2% PS
    - Plus-values sur titres : formulaire 2074
    - Abattement pour duree de detention supprime depuis 2018 pour le PFU
"""

import csv
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import List

logger = logging.getLogger(__name__)


class TaxReportGenerator:
    """Genere un rapport fiscal pour la declaration francaise.

    Attend des trades au format :
    {
        "ticker": str,
        "side": "BUY" | "SELL",
        "qty": float,
        "price": float,
        "notional": float,       # qty * price
        "timestamp": str,        # ISO 8601
        "strategy": str,
        "pnl": float,           # P&L realise (pour les SELL)
        "commission": float,     # frais de courtage
    }
    """

    # PFU = 30% (defaut en France depuis 2018)
    PFU_RATE = 0.30
    IR_RATE = 0.128      # Impot sur le revenu part
    PS_RATE = 0.172      # Prelevements sociaux part

    # Wash sale = rachat dans les 30 jours calendaires
    WASH_SALE_WINDOW_DAYS = 30

    def generate_annual_report(self, trades: List[dict], year: int) -> dict:
        """Genere le rapport fiscal annuel complet.

        Args:
            trades: liste de trades (format decrit dans la docstring de la classe).
            year: annee fiscale.

        Returns:
            {
                year: int,
                total_gains: float,
                total_losses: float,
                net_pnl: float,
                total_commissions: float,
                wash_sales: [{ticker, buy_date, sell_date, loss, disallowed}],
                short_term_gains: float,
                short_term_losses: float,
                long_term_gains: float,
                long_term_losses: float,
                tax_loss_harvest_candidates: [{ticker, unrealized_loss, last_sell_date}],
                by_month: [{month, pnl, trades_count, gains, losses}],
                by_strategy: [{strategy, pnl, trades_count, win_rate}],
                estimated_tax_pfu: float,
                estimated_tax_ir_ps: float,
            }
        """
        # Filtrer les trades de l'annee
        year_trades = self._filter_year(trades, year)

        # Separer les ventes (realisations de P&L)
        sells = [t for t in year_trades if t.get("side") == "SELL"]
        buys = [t for t in year_trades if t.get("side") == "BUY"]

        # Calculer les gains/pertes
        total_gains = sum(t["pnl"] for t in sells if t.get("pnl", 0) > 0)
        total_losses = sum(t["pnl"] for t in sells if t.get("pnl", 0) < 0)
        net_pnl = total_gains + total_losses  # losses sont negatives
        total_commissions = sum(abs(t.get("commission", 0)) for t in year_trades)

        # Wash sales
        wash_sales = self.detect_wash_sales(year_trades)
        wash_disallowed = sum(w["disallowed"] for w in wash_sales)

        # Court terme vs long terme
        short_term, long_term = self._split_by_holding_period(sells, buys)

        # Par mois
        by_month = self._aggregate_by_month(sells, year)

        # Par strategie
        by_strategy = self._aggregate_by_strategy(sells)

        # Tax-loss harvesting candidates (pertes non realisees)
        # Note : necessite les positions ouvertes, pas juste les trades
        harvest_candidates = self._find_harvest_candidates(sells)

        # Estimation fiscale
        taxable_pnl = max(net_pnl - total_commissions + wash_disallowed, 0)
        estimated_tax_pfu = taxable_pnl * self.PFU_RATE
        estimated_tax_ir_ps = taxable_pnl * (self.IR_RATE + self.PS_RATE)

        return {
            "year": year,
            "total_trades": len(year_trades),
            "total_sells": len(sells),
            "total_gains": round(total_gains, 2),
            "total_losses": round(total_losses, 2),
            "net_pnl": round(net_pnl, 2),
            "total_commissions": round(total_commissions, 2),
            "wash_sales": wash_sales,
            "wash_disallowed_total": round(wash_disallowed, 2),
            "short_term_gains": round(short_term["gains"], 2),
            "short_term_losses": round(short_term["losses"], 2),
            "long_term_gains": round(long_term["gains"], 2),
            "long_term_losses": round(long_term["losses"], 2),
            "tax_loss_harvest_candidates": harvest_candidates,
            "by_month": by_month,
            "by_strategy": by_strategy,
            "taxable_pnl": round(taxable_pnl, 2),
            "estimated_tax_pfu": round(estimated_tax_pfu, 2),
            "estimated_tax_ir_ps": round(estimated_tax_ir_ps, 2),
        }

    def detect_wash_sales(self, trades: List[dict]) -> List[dict]:
        """Detecte les wash sales (rachat < 30 jours apres vente a perte).

        Un wash sale se produit quand :
          1. On vend un titre a perte
          2. On rachete le meme titre dans les 30 jours calendaires

        En cas de wash sale, la perte est "disallowed" fiscalement et
        ajoutee au cout de base de la nouvelle position.

        Note : le concept de wash sale est americain (IRS Rule).
        En droit francais, il n'y a pas exactement d'equivalent,
        mais la detection est utile pour la declaration US (Alpaca = broker US).

        Args:
            trades: liste complete des trades.

        Returns:
            Liste de wash sales detectes.
        """
        wash_sales = []

        # Grouper par ticker
        by_ticker = defaultdict(list)
        for t in trades:
            by_ticker[t["ticker"]].append(t)

        for ticker, ticker_trades in by_ticker.items():
            # Trier par date
            sorted_trades = sorted(
                ticker_trades,
                key=lambda t: t.get("timestamp", "")
            )

            # Trouver les ventes a perte
            for i, trade in enumerate(sorted_trades):
                if trade.get("side") != "SELL":
                    continue
                pnl = trade.get("pnl", 0)
                if pnl >= 0:
                    continue

                sell_date = self._parse_date(trade["timestamp"])
                if sell_date is None:
                    continue

                # Chercher un rachat dans les 30 jours suivants
                window_end = sell_date + timedelta(days=self.WASH_SALE_WINDOW_DAYS)

                for j in range(i + 1, len(sorted_trades)):
                    next_trade = sorted_trades[j]
                    if next_trade.get("side") != "BUY":
                        continue

                    buy_date = self._parse_date(next_trade["timestamp"])
                    if buy_date is None:
                        continue

                    if buy_date <= window_end:
                        wash_sales.append({
                            "ticker": ticker,
                            "sell_date": sell_date.isoformat()[:10],
                            "buy_date": buy_date.isoformat()[:10],
                            "loss": round(pnl, 2),
                            "disallowed": round(abs(pnl), 2),
                            "days_between": (buy_date - sell_date).days,
                        })
                        break  # un seul wash sale par vente

        return wash_sales

    def export_csv(self, trades: List[dict], filepath: str,
                   year: int | None = None) -> str:
        """Export au format CSV compatible declaration fiscale FR (formulaire 2074).

        Colonnes : Date, Ticker, Operation, Quantite, Prix, Montant,
                   Commission, PnL, Strategie

        Args:
            trades: liste des trades.
            filepath: chemin du fichier CSV de sortie.
            year: si specifie, filtre sur cette annee.

        Returns:
            Chemin du fichier genere.
        """
        if year is not None:
            trades = self._filter_year(trades, year)

        # Trier par date
        sorted_trades = sorted(trades, key=lambda t: t.get("timestamp", ""))

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=";")  # delimiter FR = ;

            # En-tete
            writer.writerow([
                "Date", "Ticker", "Operation", "Quantite",
                "Prix_Unitaire", "Montant_Brut", "Commission",
                "PnL_Realise", "Strategie", "Duree_Detention_Jours",
            ])

            for trade in sorted_trades:
                date_str = trade.get("timestamp", "")[:10]
                writer.writerow([
                    date_str,
                    trade.get("ticker", ""),
                    trade.get("side", ""),
                    trade.get("qty", 0),
                    round(trade.get("price", 0), 4),
                    round(trade.get("notional", 0), 2),
                    round(trade.get("commission", 0), 4),
                    round(trade.get("pnl", 0), 2) if trade.get("side") == "SELL" else "",
                    trade.get("strategy", ""),
                    trade.get("holding_days", ""),
                ])

        logger.info(f"Tax CSV exporte : {filepath} ({len(sorted_trades)} trades)")
        return filepath

    def format_report(self, report: dict) -> str:
        """Formate le rapport annuel en texte lisible."""
        lines = [
            f"# Rapport Fiscal {report['year']}",
            "",
            "## Resume",
            f"- Trades total : {report['total_trades']}",
            f"- Ventes : {report['total_sells']}",
            f"- Gains : ${report['total_gains']:,.2f}",
            f"- Pertes : ${report['total_losses']:,.2f}",
            f"- **P&L net : ${report['net_pnl']:,.2f}**",
            f"- Commissions : ${report['total_commissions']:,.2f}",
            "",
            "## Duree de detention",
            f"- Court terme (< 1 an) : gains ${report['short_term_gains']:,.2f}, "
            f"pertes ${report['short_term_losses']:,.2f}",
            f"- Long terme (> 1 an) : gains ${report['long_term_gains']:,.2f}, "
            f"pertes ${report['long_term_losses']:,.2f}",
            "",
            "## Wash Sales",
            f"- Detectes : {len(report['wash_sales'])}",
            f"- Pertes disallowed : ${report['wash_disallowed_total']:,.2f}",
        ]

        if report["wash_sales"]:
            lines.append("")
            for ws in report["wash_sales"]:
                lines.append(
                    f"  - {ws['ticker']}: vendu {ws['sell_date']} "
                    f"(perte ${ws['loss']:,.2f}), "
                    f"rachete {ws['buy_date']} ({ws['days_between']}j)"
                )

        lines.extend([
            "",
            "## Estimation fiscale",
            f"- P&L imposable : ${report['taxable_pnl']:,.2f}",
            f"- PFU (30%) : ${report['estimated_tax_pfu']:,.2f}",
            f"- IR+PS (12.8%+17.2%) : ${report['estimated_tax_ir_ps']:,.2f}",
        ])

        if report["by_strategy"]:
            lines.extend(["", "## Par strategie"])
            for s in report["by_strategy"]:
                lines.append(
                    f"- {s['strategy']}: ${s['pnl']:,.2f} "
                    f"({s['trades_count']} trades, WR {s['win_rate']:.0f}%)"
                )

        if report["by_month"]:
            lines.extend(["", "## Par mois"])
            for m in report["by_month"]:
                lines.append(
                    f"- {m['month']}: ${m['pnl']:,.2f} "
                    f"({m['trades_count']} trades)"
                )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _filter_year(self, trades: List[dict], year: int) -> List[dict]:
        """Filtre les trades d'une annee donnee."""
        result = []
        for t in trades:
            ts = t.get("timestamp", "")
            if ts[:4] == str(year):
                result.append(t)
        return result

    def _parse_date(self, timestamp: str) -> datetime | None:
        """Parse un timestamp ISO en datetime."""
        if not timestamp:
            return None
        try:
            # Supporte "2026-03-26T15:30:00Z" et "2026-03-26"
            clean = timestamp.replace("Z", "+00:00")
            if "T" in clean:
                return datetime.fromisoformat(clean)
            return datetime.fromisoformat(clean + "T00:00:00+00:00")
        except (ValueError, TypeError):
            return None

    def _split_by_holding_period(
        self, sells: List[dict], buys: List[dict]
    ) -> tuple:
        """Separe les P&L en court terme (< 1 an) et long terme (>= 1 an).

        Utilise le champ holding_days si disponible, sinon estime via FIFO.
        """
        short_term = {"gains": 0.0, "losses": 0.0}
        long_term = {"gains": 0.0, "losses": 0.0}

        # Index des achats par ticker (FIFO)
        buy_queue = defaultdict(list)
        for b in sorted(buys, key=lambda t: t.get("timestamp", "")):
            buy_queue[b["ticker"]].append(b)

        for sell in sells:
            pnl = sell.get("pnl", 0)
            holding_days = sell.get("holding_days")

            if holding_days is None:
                # Estimer via FIFO
                ticker = sell["ticker"]
                sell_date = self._parse_date(sell.get("timestamp", ""))
                if sell_date and buy_queue[ticker]:
                    buy = buy_queue[ticker][0]  # FIFO
                    buy_date = self._parse_date(buy.get("timestamp", ""))
                    if buy_date:
                        holding_days = (sell_date - buy_date).days
                        # Consommer l'achat (simplification)
                        buy_queue[ticker].pop(0)

            if holding_days is None:
                # Defaut : court terme (intraday)
                holding_days = 0

            bucket = long_term if holding_days >= 365 else short_term
            if pnl > 0:
                bucket["gains"] += pnl
            else:
                bucket["losses"] += pnl

        return short_term, long_term

    def _aggregate_by_month(self, sells: List[dict], year: int) -> List[dict]:
        """Agrege les P&L par mois."""
        months = {}
        for m in range(1, 13):
            key = f"{year}-{m:02d}"
            months[key] = {"month": key, "pnl": 0.0, "trades_count": 0,
                           "gains": 0.0, "losses": 0.0}

        for sell in sells:
            ts = sell.get("timestamp", "")
            if len(ts) < 7:
                continue
            key = ts[:7]
            if key in months:
                pnl = sell.get("pnl", 0)
                months[key]["pnl"] += pnl
                months[key]["trades_count"] += 1
                if pnl > 0:
                    months[key]["gains"] += pnl
                else:
                    months[key]["losses"] += pnl

        # Arrondir et retourner
        result = []
        for key in sorted(months.keys()):
            m = months[key]
            m["pnl"] = round(m["pnl"], 2)
            m["gains"] = round(m["gains"], 2)
            m["losses"] = round(m["losses"], 2)
            result.append(m)
        return result

    def _aggregate_by_strategy(self, sells: List[dict]) -> List[dict]:
        """Agrege les P&L par strategie."""
        strats = defaultdict(lambda: {"pnl": 0.0, "trades_count": 0, "wins": 0})

        for sell in sells:
            strategy = sell.get("strategy", "unknown")
            pnl = sell.get("pnl", 0)
            strats[strategy]["pnl"] += pnl
            strats[strategy]["trades_count"] += 1
            if pnl > 0:
                strats[strategy]["wins"] += 1

        result = []
        for name in sorted(strats.keys()):
            s = strats[name]
            count = s["trades_count"]
            result.append({
                "strategy": name,
                "pnl": round(s["pnl"], 2),
                "trades_count": count,
                "win_rate": (s["wins"] / count * 100) if count > 0 else 0,
            })
        return result

    def _find_harvest_candidates(self, sells: List[dict]) -> List[dict]:
        """Identifie les tickers avec des pertes recentes (candidats TLH).

        Un candidat est un ticker qui a ete vendu a perte recemment,
        suggerant qu'il pourrait etre rachete apres 30 jours pour
        materialiser la perte fiscale.
        """
        candidates = []
        seen = set()

        for sell in sorted(sells, key=lambda t: t.get("timestamp", ""),
                           reverse=True):
            ticker = sell.get("ticker", "")
            if ticker in seen:
                continue
            pnl = sell.get("pnl", 0)
            if pnl < 0:
                candidates.append({
                    "ticker": ticker,
                    "unrealized_loss": round(pnl, 2),
                    "last_sell_date": sell.get("timestamp", "")[:10],
                })
                seen.add(ticker)

        return candidates
