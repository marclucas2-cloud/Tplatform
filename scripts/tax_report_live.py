"""
Tax Report Generator — PFU 30% for French tax declaration.

Generates reports for live trading P&L with:
  - Gains/losses by instrument and month
  - Net taxable gains (plus-values nettes)
  - Reportable losses (moins-values)
  - EUR conversion using BCE daily rates
  - Wash sale detection (rachat < 30 jours du meme instrument)
  - IFU-compatible export

Usage:
  python scripts/tax_report_live.py --year 2026 [--output output/tax/]
  python scripts/tax_report_live.py --year 2026 --month 4  # Monthly
  python scripts/tax_report_live.py --year 2026 --format csv
"""

import argparse
import csv
import logging
import sqlite3
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

# PFU rates (Prelevement Forfaitaire Unique)
PFU_RATE = 0.30          # Total: 30%
IR_RATE = 0.128           # Impot sur le revenu: 12.8%
PS_RATE = 0.172           # Prelevements sociaux: 17.2%

# Asset type classification for tax purposes
ASSET_TAX_CLASS = {
    "EQUITY": "valeurs_mobilieres",
    "FX": "forex",
    "FUTURES": "instruments_financiers_a_terme",
}

# BCE exchange rate API (European Central Bank)
BCE_RATE_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
BCE_NAMESPACE = {"gesmes": "http://www.gesmes.org/xml/2002-08-01",
                 "eurofxref": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}

# Default path for the live trade journal
DEFAULT_JOURNAL_DB = Path(__file__).parent.parent / "data" / "live_journal.db"


class TaxReportGenerator:
    """Generates French tax reports for live trading.

    Handles:
    - P&L aggregation by instrument, month, asset class
    - USD to EUR conversion (BCE rates or fallback)
    - Wash sale detection (revente/rachat < 30 jours)
    - Net taxable gains calculation
    - Monthly and annual reports
    - CSV export for IFU compatibility
    """

    def __init__(self, journal_db_path: str = None,
                 base_currency: str = "EUR",
                 usd_eur_rate: float = 0.92):
        """
        Args:
            journal_db_path: path to trade journal SQLite
            base_currency: declaration currency (EUR for France)
            usd_eur_rate: fallback USD/EUR rate if BCE unavailable
        """
        self._db_path = Path(journal_db_path) if journal_db_path else DEFAULT_JOURNAL_DB
        self._base_currency = base_currency
        self._fallback_rate = usd_eur_rate
        self._bce_rates: Dict[str, float] = {}  # date_str -> USD/EUR rate
        self._bce_loaded = False

    # ------------------------------------------------------------------
    # Data retrieval
    # ------------------------------------------------------------------

    def get_closed_trades(self, year: int, month: int = None) -> list:
        """Get all closed trades for the period.

        Reads from the trade journal SQLite database.
        Only returns CLOSED trades (status = 'CLOSED') with a timestamp_closed
        in the specified year (and optionally month).

        Returns: list of trade dicts from the journal
        """
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            if month:
                start = f"{year}-{month:02d}-01"
                # End of month: handle variable month lengths
                if month == 12:
                    end = f"{year + 1}-01-01"
                else:
                    end = f"{year}-{month + 1:02d}-01"
                query = """
                    SELECT * FROM trades
                    WHERE status = 'CLOSED'
                      AND timestamp_closed >= ?
                      AND timestamp_closed < ?
                    ORDER BY timestamp_closed
                """
                rows = conn.execute(query, (start, end)).fetchall()
            else:
                start = f"{year}-01-01"
                end = f"{year + 1}-01-01"
                query = """
                    SELECT * FROM trades
                    WHERE status = 'CLOSED'
                      AND timestamp_closed >= ?
                      AND timestamp_closed < ?
                    ORDER BY timestamp_closed
                """
                rows = conn.execute(query, (start, end)).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Currency conversion
    # ------------------------------------------------------------------

    def _load_bce_rates(self):
        """Load BCE daily exchange rates (last 90 days).

        Fetches the ECB reference rate XML feed and parses USD/EUR rates
        by date. Silently falls back if network is unavailable.
        """
        if self._bce_loaded:
            return
        try:
            req = urllib.request.Request(BCE_RATE_URL, headers={"User-Agent": "TaxReport/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                xml_data = resp.read()
            root = ET.fromstring(xml_data)
            # Navigate: Cube > Cube[@time] > Cube[@currency='USD']
            cube_root = root.find(".//eurofxref:Cube", BCE_NAMESPACE)
            if cube_root is not None:
                for day_cube in cube_root.findall("eurofxref:Cube", BCE_NAMESPACE):
                    day_date = day_cube.get("time")
                    for rate_cube in day_cube.findall("eurofxref:Cube", BCE_NAMESPACE):
                        if rate_cube.get("currency") == "USD":
                            # ECB rate = 1 EUR = X USD, so USD->EUR = 1/X
                            usd_per_eur = float(rate_cube.get("rate"))
                            self._bce_rates[day_date] = 1.0 / usd_per_eur
            logger.info("BCE rates loaded: %d days", len(self._bce_rates))
        except Exception as e:
            logger.warning("Could not load BCE rates: %s. Using fallback rate %.4f",
                           e, self._fallback_rate)
        self._bce_loaded = True

    def convert_to_eur(self, amount_usd: float, trade_date: str) -> float:
        """Convert USD to EUR using BCE rate for the date.

        Falls back to self._fallback_rate if BCE rate unavailable.
        For dates not in the BCE dataset (weekends, holidays), uses the
        most recent available rate.

        Args:
            amount_usd: amount in USD
            trade_date: ISO date string (YYYY-MM-DD or full ISO timestamp)

        Returns:
            amount in EUR
        """
        self._load_bce_rates()

        date_str = trade_date[:10]  # Extract YYYY-MM-DD
        rate = self._bce_rates.get(date_str)

        if rate is None and self._bce_rates:
            # Find the closest previous date
            try:
                target = datetime.strptime(date_str, "%Y-%m-%d").date()
                available = sorted(self._bce_rates.keys(), reverse=True)
                for d in available:
                    d_date = datetime.strptime(d, "%Y-%m-%d").date()
                    if d_date <= target:
                        rate = self._bce_rates[d]
                        break
                # If target is before all available dates, use earliest
                if rate is None and available:
                    rate = self._bce_rates[available[-1]]
            except (ValueError, KeyError):
                pass

        if rate is None:
            rate = self._fallback_rate

        return round(amount_usd * rate, 2)

    # ------------------------------------------------------------------
    # P&L calculations
    # ------------------------------------------------------------------

    def calculate_pnl_by_instrument(self, year: int, month: int = None) -> list:
        """P&L grouped by instrument.

        Returns: [{
            instrument: str,
            instrument_type: str,
            tax_class: str,
            n_trades: int,
            pnl_gross_usd: float,
            pnl_net_usd: float,
            commissions_usd: float,
            pnl_net_eur: float,
        }]
        """
        trades = self.get_closed_trades(year, month)
        grouped: Dict[str, dict] = {}

        for t in trades:
            inst = t["instrument"]
            if inst not in grouped:
                grouped[inst] = {
                    "instrument": inst,
                    "instrument_type": t.get("instrument_type", "EQUITY"),
                    "tax_class": ASSET_TAX_CLASS.get(
                        t.get("instrument_type", "EQUITY"), "valeurs_mobilieres"
                    ),
                    "n_trades": 0,
                    "pnl_gross_usd": 0.0,
                    "pnl_net_usd": 0.0,
                    "commissions_usd": 0.0,
                    "pnl_net_eur": 0.0,
                }

            g = grouped[inst]
            g["n_trades"] += 1
            pnl_gross = t.get("pnl_gross") or 0.0
            pnl_net = t.get("pnl_net") or 0.0
            commission = t.get("commission") or 0.0
            g["pnl_gross_usd"] += pnl_gross
            g["pnl_net_usd"] += pnl_net
            g["commissions_usd"] += commission

            close_date = t.get("timestamp_closed") or t.get("timestamp_filled") or ""
            g["pnl_net_eur"] += self.convert_to_eur(pnl_net, close_date)

        result = []
        for inst in sorted(grouped.keys()):
            g = grouped[inst]
            g["pnl_gross_usd"] = round(g["pnl_gross_usd"], 2)
            g["pnl_net_usd"] = round(g["pnl_net_usd"], 2)
            g["commissions_usd"] = round(g["commissions_usd"], 2)
            g["pnl_net_eur"] = round(g["pnl_net_eur"], 2)
            result.append(g)

        return result

    def calculate_pnl_by_month(self, year: int) -> list:
        """Monthly P&L breakdown.

        Returns: [{month: int, pnl_net_usd: float, pnl_net_eur: float, n_trades: int}]
        """
        trades = self.get_closed_trades(year)
        months: Dict[int, dict] = {}

        for m in range(1, 13):
            months[m] = {"month": m, "pnl_net_usd": 0.0, "pnl_net_eur": 0.0, "n_trades": 0}

        for t in trades:
            close_date = t.get("timestamp_closed") or ""
            if len(close_date) < 7:
                continue
            try:
                m = int(close_date[5:7])
            except (ValueError, IndexError):
                continue

            if m not in months:
                continue

            pnl_net = t.get("pnl_net") or 0.0
            months[m]["pnl_net_usd"] += pnl_net
            months[m]["pnl_net_eur"] += self.convert_to_eur(pnl_net, close_date)
            months[m]["n_trades"] += 1

        result = []
        for m in range(1, 13):
            entry = months[m]
            entry["pnl_net_usd"] = round(entry["pnl_net_usd"], 2)
            entry["pnl_net_eur"] = round(entry["pnl_net_eur"], 2)
            result.append(entry)
        return result

    def calculate_taxable_gains(self, year: int) -> dict:
        """Net taxable gains for the year.

        Computes gains and losses per asset class, then aggregates for the
        overall tax calculation. Under PFU, gains across all asset classes
        are netted together.

        Returns: {
            total_gains_eur: float,     # Sum of positive P&L
            total_losses_eur: float,    # Sum of negative P&L (absolute)
            net_gains_eur: float,       # gains - losses
            tax_pfu_eur: float,         # net_gains * 30% (if positive)
            tax_ir_eur: float,          # net_gains * 12.8%
            tax_ps_eur: float,          # net_gains * 17.2%
            reportable_loss_eur: float, # If net < 0, loss reportable over 10 years
            n_trades: int,
            by_asset_class: {
                "valeurs_mobilieres": {...},
                "forex": {...},
                "instruments_financiers_a_terme": {...},
            }
        }
        """
        trades = self.get_closed_trades(year)

        # Initialise per-asset-class buckets
        by_class: Dict[str, dict] = {}
        for tax_class in ASSET_TAX_CLASS.values():
            by_class[tax_class] = {
                "total_gains_eur": 0.0,
                "total_losses_eur": 0.0,
                "net_gains_eur": 0.0,
                "n_trades": 0,
            }

        total_gains_eur = 0.0
        total_losses_eur = 0.0

        for t in trades:
            pnl_net = t.get("pnl_net") or 0.0
            close_date = t.get("timestamp_closed") or t.get("timestamp_filled") or ""
            pnl_eur = self.convert_to_eur(pnl_net, close_date)

            inst_type = t.get("instrument_type", "EQUITY")
            tax_class = ASSET_TAX_CLASS.get(inst_type, "valeurs_mobilieres")

            bucket = by_class[tax_class]
            bucket["n_trades"] += 1

            if pnl_eur > 0:
                total_gains_eur += pnl_eur
                bucket["total_gains_eur"] += pnl_eur
            else:
                total_losses_eur += abs(pnl_eur)
                bucket["total_losses_eur"] += abs(pnl_eur)

        # Net per asset class
        for tax_class in by_class:
            bc = by_class[tax_class]
            bc["net_gains_eur"] = round(bc["total_gains_eur"] - bc["total_losses_eur"], 2)
            bc["total_gains_eur"] = round(bc["total_gains_eur"], 2)
            bc["total_losses_eur"] = round(bc["total_losses_eur"], 2)

        net_gains_eur = round(total_gains_eur - total_losses_eur, 2)

        # Tax is only due if net gains > 0
        if net_gains_eur > 0:
            tax_pfu_eur = round(net_gains_eur * PFU_RATE, 2)
            tax_ir_eur = round(net_gains_eur * IR_RATE, 2)
            tax_ps_eur = round(net_gains_eur * PS_RATE, 2)
            reportable_loss_eur = 0.0
        else:
            tax_pfu_eur = 0.0
            tax_ir_eur = 0.0
            tax_ps_eur = 0.0
            reportable_loss_eur = round(abs(net_gains_eur), 2)

        return {
            "total_gains_eur": round(total_gains_eur, 2),
            "total_losses_eur": round(total_losses_eur, 2),
            "net_gains_eur": net_gains_eur,
            "tax_pfu_eur": tax_pfu_eur,
            "tax_ir_eur": tax_ir_eur,
            "tax_ps_eur": tax_ps_eur,
            "reportable_loss_eur": reportable_loss_eur,
            "n_trades": len(trades),
            "by_asset_class": by_class,
        }

    # ------------------------------------------------------------------
    # Wash sale detection
    # ------------------------------------------------------------------

    def detect_wash_sales(self, year: int) -> list:
        """Detect potential wash sales (same instrument sold and rebought within 30 days).

        French tax doesn't have a formal wash sale rule like US,
        but it's good to flag for audit trail.

        Scans all trades for the year (including OPEN trades started that year)
        and identifies cases where a loss was realized and the same instrument
        was bought again within 30 calendar days.

        Returns: [{instrument, sell_date, buy_date, days_between, amount}]
        """
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            # Get all trades for the year (closed and open)
            start = f"{year}-01-01"
            end = f"{year + 1}-01-01"
            query = """
                SELECT * FROM trades
                WHERE (timestamp_closed >= ? AND timestamp_closed < ?)
                   OR (timestamp_filled >= ? AND timestamp_filled < ?)
                ORDER BY instrument, timestamp_filled
            """
            rows = conn.execute(query, (start, end, start, end)).fetchall()
            all_trades = [dict(row) for row in rows]
        finally:
            conn.close()

        wash_sales = []

        # Group by instrument
        by_instrument: Dict[str, list] = {}
        for t in all_trades:
            inst = t["instrument"]
            if inst not in by_instrument:
                by_instrument[inst] = []
            by_instrument[inst].append(t)

        for inst, inst_trades in by_instrument.items():
            # Sort by timestamp
            inst_trades.sort(key=lambda t: t.get("timestamp_closed") or t.get("timestamp_filled") or "")

            # Find closed trades with losses
            for i, trade in enumerate(inst_trades):
                if trade.get("status") != "CLOSED":
                    continue
                pnl_net = trade.get("pnl_net") or 0.0
                if pnl_net >= 0:
                    continue

                sell_date_str = (trade.get("timestamp_closed") or "")[:10]
                if not sell_date_str:
                    continue

                try:
                    sell_date = datetime.strptime(sell_date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue

                window_end = sell_date + timedelta(days=30)

                # Look for a subsequent buy (new OPEN or new trade) on the same instrument
                for j in range(i + 1, len(inst_trades)):
                    next_trade = inst_trades[j]
                    buy_date_str = (next_trade.get("timestamp_filled") or "")[:10]
                    if not buy_date_str:
                        continue
                    try:
                        buy_date = datetime.strptime(buy_date_str, "%Y-%m-%d").date()
                    except ValueError:
                        continue

                    if buy_date > window_end:
                        break  # Past the 30-day window

                    if buy_date >= sell_date:
                        wash_sales.append({
                            "instrument": inst,
                            "sell_date": sell_date_str,
                            "buy_date": buy_date_str,
                            "days_between": (buy_date - sell_date).days,
                            "amount": round(pnl_net, 2),
                        })
                        break  # One wash sale per loss event

        return wash_sales

    # ------------------------------------------------------------------
    # Report generation
    # ------------------------------------------------------------------

    def generate_report(self, year: int, month: int = None) -> str:
        """Generate markdown tax report.

        Includes:
        - Summary table
        - Monthly breakdown
        - By instrument
        - By asset class
        - Tax calculation
        - Wash sale warnings
        """
        lines = []
        period_label = f"{year}-{month:02d}" if month else str(year)

        lines.append(f"# Rapport Fiscal PFU 30% - {period_label}")
        lines.append("")
        lines.append(f"*Genere le {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
        lines.append(f"*Devise de declaration : {self._base_currency}*")
        lines.append(f"*Taux de change USD/EUR fallback : {self._fallback_rate}*")
        lines.append("")

        # --- Taxable gains summary ---
        tax_data = self.calculate_taxable_gains(year) if not month else None

        if not month:
            lines.append("## Resume fiscal annuel")
            lines.append("")
            lines.append("| Poste | Montant (EUR) |")
            lines.append("|-------|---------------|")
            lines.append(f"| Plus-values brutes | {tax_data['total_gains_eur']:,.2f} |")
            lines.append(f"| Moins-values brutes | -{tax_data['total_losses_eur']:,.2f} |")
            lines.append(f"| **Plus-values nettes** | **{tax_data['net_gains_eur']:,.2f}** |")
            lines.append(f"| Nombre de trades | {tax_data['n_trades']} |")
            lines.append("")

            if tax_data["net_gains_eur"] > 0:
                lines.append("## Estimation fiscale PFU")
                lines.append("")
                lines.append("| Impot | Taux | Montant (EUR) |")
                lines.append("|-------|------|---------------|")
                lines.append(f"| Impot sur le revenu (IR) | 12.8% | {tax_data['tax_ir_eur']:,.2f} |")
                lines.append(f"| Prelevements sociaux (PS) | 17.2% | {tax_data['tax_ps_eur']:,.2f} |")
                lines.append(f"| **PFU total** | **30%** | **{tax_data['tax_pfu_eur']:,.2f}** |")
                lines.append("")
                lines.append(f"*Net apres impot : {tax_data['net_gains_eur'] - tax_data['tax_pfu_eur']:,.2f} EUR*")
            else:
                lines.append("## Moins-value reportable")
                lines.append("")
                lines.append(f"Moins-value nette : **{tax_data['reportable_loss_eur']:,.2f} EUR**")
                lines.append("Reportable sur les 10 annees suivantes (CGI art. 150-0 D).")
            lines.append("")

            # --- By asset class ---
            lines.append("## Par classe d'actifs")
            lines.append("")
            lines.append("| Classe fiscale | Gains (EUR) | Pertes (EUR) | Net (EUR) | Trades |")
            lines.append("|----------------|-------------|--------------|-----------|--------|")
            for tax_class, data in tax_data["by_asset_class"].items():
                label = tax_class.replace("_", " ").title()
                lines.append(
                    f"| {label} | {data['total_gains_eur']:,.2f} "
                    f"| -{data['total_losses_eur']:,.2f} "
                    f"| {data['net_gains_eur']:,.2f} "
                    f"| {data['n_trades']} |"
                )
            lines.append("")

        # --- Monthly breakdown ---
        if not month:
            lines.append("## Detail mensuel")
            lines.append("")
            lines.append("| Mois | P&L net (USD) | P&L net (EUR) | Trades |")
            lines.append("|------|---------------|---------------|--------|")
            monthly = self.calculate_pnl_by_month(year)
            for m in monthly:
                month_name = f"{year}-{m['month']:02d}"
                lines.append(
                    f"| {month_name} | {m['pnl_net_usd']:,.2f} "
                    f"| {m['pnl_net_eur']:,.2f} "
                    f"| {m['n_trades']} |"
                )
            lines.append("")

        # --- By instrument ---
        lines.append("## Detail par instrument")
        lines.append("")
        lines.append("| Instrument | Type | P&L brut (USD) | Commissions (USD) | P&L net (USD) | P&L net (EUR) | Trades |")
        lines.append("|------------|------|----------------|-------------------|---------------|---------------|--------|")
        by_inst = self.calculate_pnl_by_instrument(year, month)
        for item in by_inst:
            lines.append(
                f"| {item['instrument']} "
                f"| {item['instrument_type']} "
                f"| {item['pnl_gross_usd']:,.2f} "
                f"| {item['commissions_usd']:,.2f} "
                f"| {item['pnl_net_usd']:,.2f} "
                f"| {item['pnl_net_eur']:,.2f} "
                f"| {item['n_trades']} |"
            )
        lines.append("")

        # --- Wash sales ---
        if not month:
            wash_sales = self.detect_wash_sales(year)
            lines.append("## Alertes wash sale (rachat < 30 jours)")
            lines.append("")
            if wash_sales:
                lines.append(f"**{len(wash_sales)} wash sale(s) detecte(s) :**")
                lines.append("")
                lines.append("| Instrument | Date vente | Date rachat | Jours | Perte (USD) |")
                lines.append("|------------|-----------|-------------|-------|-------------|")
                for ws in wash_sales:
                    lines.append(
                        f"| {ws['instrument']} "
                        f"| {ws['sell_date']} "
                        f"| {ws['buy_date']} "
                        f"| {ws['days_between']} "
                        f"| {ws['amount']:,.2f} |"
                    )
                lines.append("")
                lines.append("*Note : La France n'a pas de regle formelle de wash sale (contrairement aux US/IRS).")
                lines.append("Ces alertes sont a titre informatif pour la piste d'audit.*")
            else:
                lines.append("Aucun wash sale detecte.")
            lines.append("")

        # --- Footer ---
        lines.append("---")
        lines.append("*Rapport genere automatiquement. Ne constitue pas un conseil fiscal.")
        lines.append("Consultez votre expert-comptable pour la declaration definitive.*")

        return "\n".join(lines)

    def generate_monthly_report(self, year: int, month: int) -> str:
        """Quick monthly report for ongoing tracking."""
        return self.generate_report(year, month)

    # ------------------------------------------------------------------
    # CSV export
    # ------------------------------------------------------------------

    def export_csv(self, year: int, output_path: str = None) -> str:
        """Export trades in CSV format compatible with French IFU declaration.

        Columns: Date, Instrument, Type, Direction, Quantity,
                 Entry Price, Exit Price, P&L USD, P&L EUR, Commission

        Uses semicolon as delimiter (French standard).

        Args:
            year: tax year
            output_path: path for the output CSV file

        Returns:
            Path of the generated CSV file
        """
        if output_path is None:
            output_path = f"output/tax/trades_{year}.csv"

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        trades = self.get_closed_trades(year)

        with open(output_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=";")

            writer.writerow([
                "Date_Cloture",
                "Instrument",
                "Type_Actif",
                "Classe_Fiscale",
                "Direction",
                "Quantite",
                "Prix_Entree",
                "Prix_Sortie",
                "PnL_Brut_USD",
                "PnL_Net_USD",
                "PnL_Net_EUR",
                "Commission_USD",
                "Strategie",
                "Duree_Secondes",
                "Raison_Sortie",
            ])

            for t in trades:
                close_date = t.get("timestamp_closed") or ""
                pnl_net = t.get("pnl_net") or 0.0
                pnl_eur = self.convert_to_eur(pnl_net, close_date)

                writer.writerow([
                    close_date[:10],
                    t.get("instrument", ""),
                    t.get("instrument_type", "EQUITY"),
                    ASSET_TAX_CLASS.get(t.get("instrument_type", "EQUITY"), "valeurs_mobilieres"),
                    t.get("direction", ""),
                    t.get("quantity", 0),
                    round(t.get("entry_price_filled") or 0, 4),
                    round(t.get("exit_price_filled") or 0, 4),
                    round(t.get("pnl_gross") or 0, 2),
                    round(pnl_net, 2),
                    pnl_eur,
                    round(t.get("commission") or 0, 4),
                    t.get("strategy", ""),
                    t.get("holding_seconds") or "",
                    t.get("exit_reason", ""),
                ])

        logger.info("Tax CSV exported: %s (%d trades)", output_file, len(trades))
        return str(output_file)


def main():
    parser = argparse.ArgumentParser(description="Tax Report Generator - PFU 30%%")
    parser.add_argument("--year", type=int, required=True, help="Tax year")
    parser.add_argument("--month", type=int, default=None, help="Month (1-12) for monthly report")
    parser.add_argument("--output", default="output/tax", help="Output directory")
    parser.add_argument("--format", choices=["md", "csv", "both"], default="both")
    parser.add_argument("--usd-eur", type=float, default=0.92, help="Fallback USD/EUR rate")
    parser.add_argument("--db", default=None, help="Path to trade journal SQLite database")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    gen = TaxReportGenerator(journal_db_path=args.db, usd_eur_rate=args.usd_eur)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.format in ("md", "both"):
        report = gen.generate_report(args.year, args.month)
        suffix = f"_{args.month:02d}" if args.month else ""
        report_path = output_dir / f"tax_report_{args.year}{suffix}.md"
        report_path.write_text(report, encoding="utf-8")
        print(f"Report saved to {report_path}")
        print()
        print(report)

    if args.format in ("csv", "both"):
        csv_path = gen.export_csv(args.year, str(output_dir / f"trades_{args.year}.csv"))
        print(f"CSV saved to {csv_path}")


if __name__ == "__main__":
    main()
