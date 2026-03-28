"""
Tests for scripts.tax_report_live — PFU 30% Tax Report Generator.

Covers:
  - Get closed trades for year
  - Get closed trades for month
  - P&L by instrument (grouping correct)
  - P&L by month (12 months)
  - Convert USD to EUR (fallback rate)
  - Taxable gains calculation (positive net)
  - Taxable gains calculation (negative net = reportable loss)
  - PFU breakdown (30% = 12.8% + 17.2%)
  - Tax is 0 when net gains <= 0
  - Wash sale detection (< 30 days)
  - No wash sale (> 30 days)
  - Generate markdown report (contains key sections)
  - Export CSV (correct columns)
  - By asset class breakdown
  - Empty trades (no data for period)
  - Multiple instrument types in same year
"""

import csv
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Import module under test — adjust path in sys.path if needed
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.tax_report_live import (
    TaxReportGenerator,
    PFU_RATE,
    IR_RATE,
    PS_RATE,
    ASSET_TAX_CLASS,
)


# ─── Fixtures ───────────────────────────────────────────────────────────────

def _create_test_db(db_path: str, trades: list):
    """Create a SQLite database with the trade journal schema and insert trades."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            timestamp_signal TEXT,
            timestamp_order_sent TEXT,
            timestamp_filled TEXT,
            timestamp_closed TEXT,
            latency_signal_to_fill_ms INTEGER,
            strategy TEXT NOT NULL,
            instrument TEXT NOT NULL,
            instrument_type TEXT NOT NULL DEFAULT 'EQUITY',
            direction TEXT NOT NULL,
            quantity REAL NOT NULL,
            entry_price_requested REAL,
            entry_price_filled REAL,
            slippage_entry_bps REAL,
            exit_price_requested REAL,
            exit_price_filled REAL,
            slippage_exit_bps REAL,
            stop_loss REAL,
            take_profit REAL,
            pnl_gross REAL,
            commission REAL DEFAULT 0.0,
            pnl_net REAL,
            pnl_pct REAL,
            holding_seconds INTEGER,
            regime TEXT,
            confluence_score REAL,
            conviction_level TEXT,
            exit_reason TEXT,
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'OPEN'
        )
    """)
    for t in trades:
        cols = list(t.keys())
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        conn.execute(
            f"INSERT INTO trades ({col_names}) VALUES ({placeholders})",
            [t[c] for c in cols],
        )
    conn.commit()
    conn.close()


def _make_trade(trade_id, instrument="AAPL", instrument_type="EQUITY",
                strategy="ORB_5MIN_V2", direction="LONG", quantity=10,
                entry_fill=150.0, exit_fill=155.0, commission=0.10,
                timestamp_filled="2026-03-15T10:30:00Z",
                timestamp_closed="2026-03-15T11:00:00Z",
                exit_reason="TP_HIT", status="CLOSED"):
    """Helper to create a trade dict matching the journal schema."""
    pnl_gross = round((exit_fill - entry_fill) * quantity, 2)
    if direction == "SHORT":
        pnl_gross = round((entry_fill - exit_fill) * quantity, 2)
    pnl_net = round(pnl_gross - commission, 2)
    pnl_pct = round(pnl_net / (entry_fill * quantity) * 100, 2) if entry_fill > 0 else 0.0

    return {
        "trade_id": trade_id,
        "mode": "LIVE",
        "strategy": strategy,
        "instrument": instrument,
        "instrument_type": instrument_type,
        "direction": direction,
        "quantity": quantity,
        "entry_price_requested": entry_fill,
        "entry_price_filled": entry_fill,
        "exit_price_requested": exit_fill,
        "exit_price_filled": exit_fill,
        "pnl_gross": pnl_gross,
        "commission": commission,
        "pnl_net": pnl_net,
        "pnl_pct": pnl_pct,
        "holding_seconds": 1800,
        "timestamp_signal": timestamp_filled,
        "timestamp_filled": timestamp_filled,
        "timestamp_closed": timestamp_closed,
        "exit_reason": exit_reason,
        "status": status,
    }


@pytest.fixture
def tmp_db(tmp_path):
    """Return a temp db path."""
    return str(tmp_path / "test_tax.db")


@pytest.fixture
def sample_trades():
    """A set of representative trades for 2026."""
    return [
        # March — AAPL profit
        _make_trade("T001", instrument="AAPL", entry_fill=150.0, exit_fill=155.0,
                     commission=0.50,
                     timestamp_filled="2026-03-10T10:00:00Z",
                     timestamp_closed="2026-03-10T11:00:00Z"),
        # March — SPY loss
        _make_trade("T002", instrument="SPY", entry_fill=450.0, exit_fill=445.0,
                     commission=0.50, quantity=5,
                     timestamp_filled="2026-03-12T10:00:00Z",
                     timestamp_closed="2026-03-12T11:00:00Z"),
        # April — AAPL profit
        _make_trade("T003", instrument="AAPL", entry_fill=160.0, exit_fill=170.0,
                     commission=1.00,
                     timestamp_filled="2026-04-05T10:00:00Z",
                     timestamp_closed="2026-04-05T14:00:00Z"),
        # June — EUR/USD FX trade
        _make_trade("T004", instrument="EUR/USD", instrument_type="FX",
                     strategy="FX_CARRY", entry_fill=1.0800, exit_fill=1.0850,
                     quantity=100000, commission=5.0,
                     timestamp_filled="2026-06-15T08:00:00Z",
                     timestamp_closed="2026-06-15T16:00:00Z"),
        # September — ES futures
        _make_trade("T005", instrument="ES", instrument_type="FUTURES",
                     strategy="MOMENTUM_FUTURES", entry_fill=5100.0, exit_fill=5110.0,
                     quantity=2, commission=4.0,
                     timestamp_filled="2026-09-20T14:00:00Z",
                     timestamp_closed="2026-09-20T15:30:00Z"),
    ]


@pytest.fixture
def gen_with_trades(tmp_db, sample_trades):
    """TaxReportGenerator with sample trades pre-loaded."""
    _create_test_db(tmp_db, sample_trades)
    return TaxReportGenerator(journal_db_path=tmp_db, usd_eur_rate=0.92)


# ─── 1. Get closed trades for year ─────────────────────────────────────────

class TestGetClosedTrades:
    def test_get_all_trades_for_year(self, gen_with_trades):
        trades = gen_with_trades.get_closed_trades(2026)
        assert len(trades) == 5

    def test_get_no_trades_wrong_year(self, gen_with_trades):
        trades = gen_with_trades.get_closed_trades(2025)
        assert len(trades) == 0

    def test_get_trades_for_month(self, gen_with_trades):
        trades = gen_with_trades.get_closed_trades(2026, month=3)
        assert len(trades) == 2
        instruments = {t["instrument"] for t in trades}
        assert "AAPL" in instruments
        assert "SPY" in instruments

    def test_get_trades_for_empty_month(self, gen_with_trades):
        trades = gen_with_trades.get_closed_trades(2026, month=1)
        assert len(trades) == 0


# ─── 2. Convert USD to EUR ─────────────────────────────────────────────────

class TestConvertToEur:
    def _make_gen_no_bce(self, tmp_db, rate=0.92):
        """Create a generator that will only use fallback rate (no BCE fetch)."""
        _create_test_db(tmp_db, [])
        gen = TaxReportGenerator(journal_db_path=tmp_db, usd_eur_rate=rate)
        # Mark BCE as already loaded (empty) to force fallback
        gen._bce_loaded = True
        gen._bce_rates = {}
        return gen

    def test_fallback_rate(self, tmp_db):
        """When no BCE rates loaded, uses fallback rate."""
        gen = self._make_gen_no_bce(tmp_db, 0.92)
        result = gen.convert_to_eur(100.0, "2026-03-15")
        assert result == 92.0  # 100 * 0.92

    def test_zero_amount(self, tmp_db):
        gen = self._make_gen_no_bce(tmp_db, 0.92)
        result = gen.convert_to_eur(0.0, "2026-03-15")
        assert result == 0.0

    def test_negative_amount(self, tmp_db):
        gen = self._make_gen_no_bce(tmp_db, 0.92)
        result = gen.convert_to_eur(-100.0, "2026-03-15")
        assert result == -92.0

    def test_custom_fallback_rate(self, tmp_db):
        gen = self._make_gen_no_bce(tmp_db, 0.85)
        result = gen.convert_to_eur(1000.0, "2026-06-01")
        assert result == 850.0


# ─── 3. P&L by instrument ──────────────────────────────────────────────────

class TestPnlByInstrument:
    def test_grouping_correct(self, gen_with_trades):
        result = gen_with_trades.calculate_pnl_by_instrument(2026)
        instruments = {r["instrument"] for r in result}
        assert instruments == {"AAPL", "SPY", "EUR/USD", "ES"}

    def test_aapl_aggregated(self, gen_with_trades):
        result = gen_with_trades.calculate_pnl_by_instrument(2026)
        aapl = next(r for r in result if r["instrument"] == "AAPL")
        assert aapl["n_trades"] == 2  # T001 + T003
        assert aapl["instrument_type"] == "EQUITY"
        assert aapl["tax_class"] == "valeurs_mobilieres"
        assert aapl["pnl_net_usd"] > 0

    def test_spy_loss(self, gen_with_trades):
        result = gen_with_trades.calculate_pnl_by_instrument(2026)
        spy = next(r for r in result if r["instrument"] == "SPY")
        assert spy["n_trades"] == 1
        assert spy["pnl_net_usd"] < 0

    def test_fx_instrument(self, gen_with_trades):
        result = gen_with_trades.calculate_pnl_by_instrument(2026)
        fx = next(r for r in result if r["instrument"] == "EUR/USD")
        assert fx["instrument_type"] == "FX"
        assert fx["tax_class"] == "forex"

    def test_month_filter(self, gen_with_trades):
        result = gen_with_trades.calculate_pnl_by_instrument(2026, month=3)
        instruments = {r["instrument"] for r in result}
        assert "AAPL" in instruments
        assert "SPY" in instruments
        assert "ES" not in instruments  # ES is in September

    def test_commissions_tracked(self, gen_with_trades):
        result = gen_with_trades.calculate_pnl_by_instrument(2026)
        for item in result:
            assert item["commissions_usd"] >= 0

    def test_eur_conversion_applied(self, gen_with_trades):
        result = gen_with_trades.calculate_pnl_by_instrument(2026)
        for item in result:
            # EUR values should be approximately 0.92x of USD values
            if item["pnl_net_usd"] != 0:
                ratio = item["pnl_net_eur"] / item["pnl_net_usd"]
                assert 0.85 <= ratio <= 0.99  # Reasonable range for 0.92 rate


# ─── 4. P&L by month ───────────────────────────────────────────────────────

class TestPnlByMonth:
    def test_twelve_months_returned(self, gen_with_trades):
        result = gen_with_trades.calculate_pnl_by_month(2026)
        assert len(result) == 12
        months = [m["month"] for m in result]
        assert months == list(range(1, 13))

    def test_march_has_trades(self, gen_with_trades):
        result = gen_with_trades.calculate_pnl_by_month(2026)
        march = result[2]  # index 2 = month 3
        assert march["month"] == 3
        assert march["n_trades"] == 2
        assert march["pnl_net_usd"] != 0

    def test_january_empty(self, gen_with_trades):
        result = gen_with_trades.calculate_pnl_by_month(2026)
        january = result[0]
        assert january["month"] == 1
        assert january["n_trades"] == 0
        assert january["pnl_net_usd"] == 0.0

    def test_eur_values_present(self, gen_with_trades):
        result = gen_with_trades.calculate_pnl_by_month(2026)
        for m in result:
            assert "pnl_net_eur" in m


# ─── 5. Taxable gains — positive net ───────────────────────────────────────

class TestTaxableGainsPositive:
    def test_positive_net_gains(self, gen_with_trades):
        """With our sample data, net should be positive (more gains than losses)."""
        result = gen_with_trades.calculate_taxable_gains(2026)
        assert result["total_gains_eur"] > 0
        assert result["total_losses_eur"] > 0
        assert result["net_gains_eur"] > 0  # Gains exceed losses

    def test_pfu_breakdown(self, gen_with_trades):
        result = gen_with_trades.calculate_taxable_gains(2026)
        net = result["net_gains_eur"]
        assert result["tax_pfu_eur"] == round(net * PFU_RATE, 2)
        assert result["tax_ir_eur"] == round(net * IR_RATE, 2)
        assert result["tax_ps_eur"] == round(net * PS_RATE, 2)

    def test_pfu_equals_ir_plus_ps(self, gen_with_trades):
        result = gen_with_trades.calculate_taxable_gains(2026)
        assert result["tax_pfu_eur"] == pytest.approx(
            result["tax_ir_eur"] + result["tax_ps_eur"], abs=0.02
        )

    def test_no_reportable_loss(self, gen_with_trades):
        result = gen_with_trades.calculate_taxable_gains(2026)
        assert result["reportable_loss_eur"] == 0.0

    def test_n_trades_correct(self, gen_with_trades):
        result = gen_with_trades.calculate_taxable_gains(2026)
        assert result["n_trades"] == 5


# ─── 6. Taxable gains — negative net (reportable loss) ─────────────────────

class TestTaxableGainsNegative:
    def test_reportable_loss(self, tmp_db):
        """When losses exceed gains, tax is 0 and loss is reportable."""
        trades = [
            _make_trade("L001", instrument="AAPL", entry_fill=150.0, exit_fill=140.0,
                         commission=0.5,
                         timestamp_filled="2026-05-01T10:00:00Z",
                         timestamp_closed="2026-05-01T11:00:00Z"),
            _make_trade("L002", instrument="SPY", entry_fill=450.0, exit_fill=430.0,
                         commission=1.0, quantity=5,
                         timestamp_filled="2026-06-01T10:00:00Z",
                         timestamp_closed="2026-06-01T11:00:00Z"),
        ]
        _create_test_db(tmp_db, trades)
        gen = TaxReportGenerator(journal_db_path=tmp_db, usd_eur_rate=0.92)
        result = gen.calculate_taxable_gains(2026)

        assert result["net_gains_eur"] < 0
        assert result["tax_pfu_eur"] == 0.0
        assert result["tax_ir_eur"] == 0.0
        assert result["tax_ps_eur"] == 0.0
        assert result["reportable_loss_eur"] > 0
        assert result["reportable_loss_eur"] == round(abs(result["net_gains_eur"]), 2)

    def test_tax_is_zero_when_loss(self, tmp_db):
        """Tax must be exactly 0 when net gains are negative."""
        trades = [
            _make_trade("L003", instrument="QQQ", entry_fill=400.0, exit_fill=380.0,
                         commission=1.0,
                         timestamp_filled="2026-07-01T10:00:00Z",
                         timestamp_closed="2026-07-01T11:00:00Z"),
        ]
        _create_test_db(tmp_db, trades)
        gen = TaxReportGenerator(journal_db_path=tmp_db, usd_eur_rate=0.92)
        result = gen.calculate_taxable_gains(2026)

        assert result["tax_pfu_eur"] == 0.0
        assert result["tax_ir_eur"] == 0.0
        assert result["tax_ps_eur"] == 0.0


# ─── 7. Wash sale detection ────────────────────────────────────────────────

class TestWashSaleDetection:
    def test_wash_sale_detected(self, tmp_db):
        """Sell AAPL at loss, rebuy 10 days later -> wash sale."""
        trades = [
            _make_trade("W001", instrument="AAPL", entry_fill=150.0, exit_fill=140.0,
                         commission=0.5,
                         timestamp_filled="2026-03-01T10:00:00Z",
                         timestamp_closed="2026-03-05T11:00:00Z"),
            # Rebuy AAPL 10 days after close
            _make_trade("W002", instrument="AAPL", entry_fill=142.0, exit_fill=148.0,
                         commission=0.5,
                         timestamp_filled="2026-03-15T10:00:00Z",
                         timestamp_closed="2026-03-20T14:00:00Z"),
        ]
        _create_test_db(tmp_db, trades)
        gen = TaxReportGenerator(journal_db_path=tmp_db, usd_eur_rate=0.92)
        result = gen.detect_wash_sales(2026)

        assert len(result) == 1
        ws = result[0]
        assert ws["instrument"] == "AAPL"
        assert ws["sell_date"] == "2026-03-05"
        assert ws["buy_date"] == "2026-03-15"
        assert ws["days_between"] == 10
        assert ws["amount"] < 0  # Loss amount

    def test_no_wash_sale_over_30_days(self, tmp_db):
        """Sell at loss, rebuy 45 days later -> no wash sale."""
        trades = [
            _make_trade("NW001", instrument="MSFT", entry_fill=300.0, exit_fill=280.0,
                         commission=0.5,
                         timestamp_filled="2026-02-01T10:00:00Z",
                         timestamp_closed="2026-02-10T11:00:00Z"),
            # Rebuy 45 days later
            _make_trade("NW002", instrument="MSFT", entry_fill=285.0, exit_fill=290.0,
                         commission=0.5,
                         timestamp_filled="2026-03-27T10:00:00Z",
                         timestamp_closed="2026-03-28T14:00:00Z"),
        ]
        _create_test_db(tmp_db, trades)
        gen = TaxReportGenerator(journal_db_path=tmp_db, usd_eur_rate=0.92)
        result = gen.detect_wash_sales(2026)

        assert len(result) == 0

    def test_no_wash_sale_on_profit(self, tmp_db):
        """Sell at profit, rebuy soon -> NOT a wash sale."""
        trades = [
            _make_trade("NW003", instrument="GOOG", entry_fill=140.0, exit_fill=155.0,
                         commission=0.5,
                         timestamp_filled="2026-04-01T10:00:00Z",
                         timestamp_closed="2026-04-05T11:00:00Z"),
            _make_trade("NW004", instrument="GOOG", entry_fill=152.0, exit_fill=160.0,
                         commission=0.5,
                         timestamp_filled="2026-04-10T10:00:00Z",
                         timestamp_closed="2026-04-15T14:00:00Z"),
        ]
        _create_test_db(tmp_db, trades)
        gen = TaxReportGenerator(journal_db_path=tmp_db, usd_eur_rate=0.92)
        result = gen.detect_wash_sales(2026)

        assert len(result) == 0

    def test_wash_sale_different_instruments_no_match(self, tmp_db):
        """Loss on AAPL, rebuy SPY soon -> not a wash sale."""
        trades = [
            _make_trade("D001", instrument="AAPL", entry_fill=150.0, exit_fill=140.0,
                         commission=0.5,
                         timestamp_filled="2026-05-01T10:00:00Z",
                         timestamp_closed="2026-05-05T11:00:00Z"),
            _make_trade("D002", instrument="SPY", entry_fill=445.0, exit_fill=450.0,
                         commission=0.5,
                         timestamp_filled="2026-05-10T10:00:00Z",
                         timestamp_closed="2026-05-15T14:00:00Z"),
        ]
        _create_test_db(tmp_db, trades)
        gen = TaxReportGenerator(journal_db_path=tmp_db, usd_eur_rate=0.92)
        result = gen.detect_wash_sales(2026)

        assert len(result) == 0


# ─── 8. Markdown report generation ─────────────────────────────────────────

class TestGenerateReport:
    def test_report_contains_key_sections(self, gen_with_trades):
        report = gen_with_trades.generate_report(2026)
        assert "Rapport Fiscal PFU 30%" in report
        assert "Resume fiscal annuel" in report
        assert "Plus-values nettes" in report
        assert "Par classe d'actifs" in report
        assert "Detail mensuel" in report
        assert "Detail par instrument" in report
        assert "wash sale" in report.lower()

    def test_report_contains_instruments(self, gen_with_trades):
        report = gen_with_trades.generate_report(2026)
        assert "AAPL" in report
        assert "SPY" in report
        assert "EUR/USD" in report
        assert "ES" in report

    def test_monthly_report(self, gen_with_trades):
        report = gen_with_trades.generate_monthly_report(2026, 3)
        assert "2026-03" in report
        assert "AAPL" in report
        # Monthly report should NOT contain annual sections
        assert "Resume fiscal annuel" not in report

    def test_report_with_loss(self, tmp_db):
        """Report when net is negative should show reportable loss section."""
        trades = [
            _make_trade("RL001", instrument="TSLA", entry_fill=250.0, exit_fill=200.0,
                         commission=1.0, quantity=20,
                         timestamp_filled="2026-08-01T10:00:00Z",
                         timestamp_closed="2026-08-01T16:00:00Z"),
        ]
        _create_test_db(tmp_db, trades)
        gen = TaxReportGenerator(journal_db_path=tmp_db, usd_eur_rate=0.92)
        report = gen.generate_report(2026)

        assert "Moins-value reportable" in report
        assert "10 annees" in report


# ─── 9. CSV export ─────────────────────────────────────────────────────────

class TestExportCsv:
    def test_csv_created(self, gen_with_trades, tmp_path):
        csv_path = str(tmp_path / "test_export.csv")
        result_path = gen_with_trades.export_csv(2026, csv_path)
        assert Path(result_path).exists()

    def test_csv_correct_columns(self, gen_with_trades, tmp_path):
        csv_path = str(tmp_path / "test_export.csv")
        gen_with_trades.export_csv(2026, csv_path)

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter=";")
            header = next(reader)

        expected_cols = [
            "Date_Cloture", "Instrument", "Type_Actif", "Classe_Fiscale",
            "Direction", "Quantite", "Prix_Entree", "Prix_Sortie",
            "PnL_Brut_USD", "PnL_Net_USD", "PnL_Net_EUR",
            "Commission_USD", "Strategie", "Duree_Secondes", "Raison_Sortie",
        ]
        assert header == expected_cols

    def test_csv_correct_row_count(self, gen_with_trades, tmp_path):
        csv_path = str(tmp_path / "test_export.csv")
        gen_with_trades.export_csv(2026, csv_path)

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter=";")
            rows = list(reader)

        # 1 header + 5 trades = 6 rows
        assert len(rows) == 6

    def test_csv_semicolon_delimiter(self, gen_with_trades, tmp_path):
        csv_path = str(tmp_path / "test_export.csv")
        gen_with_trades.export_csv(2026, csv_path)

        with open(csv_path, "r", encoding="utf-8") as f:
            first_line = f.readline()

        # Should have semicolons
        assert ";" in first_line

    def test_csv_eur_values(self, gen_with_trades, tmp_path):
        csv_path = str(tmp_path / "test_export.csv")
        gen_with_trades.export_csv(2026, csv_path)

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter=";")
            header = next(reader)
            eur_idx = header.index("PnL_Net_EUR")
            for row in reader:
                eur_val = float(row[eur_idx])
                # Should be non-zero for non-zero pnl
                usd_idx = header.index("PnL_Net_USD")
                usd_val = float(row[usd_idx])
                if usd_val != 0:
                    assert eur_val != 0


# ─── 10. By asset class breakdown ──────────────────────────────────────────

class TestByAssetClass:
    def test_all_classes_present(self, gen_with_trades):
        result = gen_with_trades.calculate_taxable_gains(2026)
        assert "valeurs_mobilieres" in result["by_asset_class"]
        assert "forex" in result["by_asset_class"]
        assert "instruments_financiers_a_terme" in result["by_asset_class"]

    def test_equity_trades_in_valeurs_mobilieres(self, gen_with_trades):
        result = gen_with_trades.calculate_taxable_gains(2026)
        vm = result["by_asset_class"]["valeurs_mobilieres"]
        # AAPL (2 trades) + SPY (1 trade) = 3 equity trades
        assert vm["n_trades"] == 3

    def test_fx_trades_in_forex(self, gen_with_trades):
        result = gen_with_trades.calculate_taxable_gains(2026)
        fx = result["by_asset_class"]["forex"]
        assert fx["n_trades"] == 1

    def test_futures_trades_in_ift(self, gen_with_trades):
        result = gen_with_trades.calculate_taxable_gains(2026)
        ift = result["by_asset_class"]["instruments_financiers_a_terme"]
        assert ift["n_trades"] == 1


# ─── 11. Empty trades ──────────────────────────────────────────────────────

class TestEmptyTrades:
    def test_empty_year_taxable_gains(self, tmp_db):
        _create_test_db(tmp_db, [])
        gen = TaxReportGenerator(journal_db_path=tmp_db, usd_eur_rate=0.92)
        result = gen.calculate_taxable_gains(2026)

        assert result["total_gains_eur"] == 0.0
        assert result["total_losses_eur"] == 0.0
        assert result["net_gains_eur"] == 0.0
        assert result["tax_pfu_eur"] == 0.0
        assert result["reportable_loss_eur"] == 0.0
        assert result["n_trades"] == 0

    def test_empty_year_by_instrument(self, tmp_db):
        _create_test_db(tmp_db, [])
        gen = TaxReportGenerator(journal_db_path=tmp_db, usd_eur_rate=0.92)
        result = gen.calculate_pnl_by_instrument(2026)
        assert result == []

    def test_empty_year_by_month(self, tmp_db):
        _create_test_db(tmp_db, [])
        gen = TaxReportGenerator(journal_db_path=tmp_db, usd_eur_rate=0.92)
        result = gen.calculate_pnl_by_month(2026)
        assert len(result) == 12
        assert all(m["n_trades"] == 0 for m in result)

    def test_empty_year_report(self, tmp_db):
        _create_test_db(tmp_db, [])
        gen = TaxReportGenerator(journal_db_path=tmp_db, usd_eur_rate=0.92)
        report = gen.generate_report(2026)
        assert "Rapport Fiscal" in report

    def test_empty_csv(self, tmp_db, tmp_path):
        _create_test_db(tmp_db, [])
        gen = TaxReportGenerator(journal_db_path=tmp_db, usd_eur_rate=0.92)
        csv_path = str(tmp_path / "empty.csv")
        gen.export_csv(2026, csv_path)

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter=";")
            rows = list(reader)
        assert len(rows) == 1  # Header only


# ─── 12. Multiple instrument types in same year ────────────────────────────

class TestMultipleTypes:
    def test_mixed_types_totals(self, gen_with_trades):
        """With EQUITY, FX, and FUTURES trades, totals should be consistent."""
        result = gen_with_trades.calculate_taxable_gains(2026)

        # Sum across asset classes should equal totals
        class_gains = sum(
            bc["total_gains_eur"] for bc in result["by_asset_class"].values()
        )
        class_losses = sum(
            bc["total_losses_eur"] for bc in result["by_asset_class"].values()
        )
        class_trades = sum(
            bc["n_trades"] for bc in result["by_asset_class"].values()
        )

        assert result["total_gains_eur"] == pytest.approx(class_gains, abs=0.02)
        assert result["total_losses_eur"] == pytest.approx(class_losses, abs=0.02)
        assert result["n_trades"] == class_trades

    def test_only_open_trades_excluded(self, tmp_db):
        """OPEN trades should not appear in tax calculations."""
        trades = [
            _make_trade("O001", instrument="AAPL", entry_fill=150.0, exit_fill=155.0,
                         status="CLOSED",
                         timestamp_filled="2026-03-01T10:00:00Z",
                         timestamp_closed="2026-03-01T11:00:00Z"),
            _make_trade("O002", instrument="SPY", entry_fill=450.0, exit_fill=460.0,
                         status="OPEN",
                         timestamp_filled="2026-03-15T10:00:00Z",
                         timestamp_closed="2026-03-15T16:00:00Z"),
        ]
        _create_test_db(tmp_db, trades)
        gen = TaxReportGenerator(journal_db_path=tmp_db, usd_eur_rate=0.92)
        result = gen.calculate_taxable_gains(2026)

        assert result["n_trades"] == 1  # Only the CLOSED trade


# ─── 13. PFU rate constants ────────────────────────────────────────────────

class TestRateConstants:
    def test_pfu_rate(self):
        assert PFU_RATE == 0.30

    def test_ir_rate(self):
        assert IR_RATE == 0.128

    def test_ps_rate(self):
        assert PS_RATE == 0.172

    def test_pfu_equals_ir_plus_ps(self):
        assert PFU_RATE == pytest.approx(IR_RATE + PS_RATE, abs=0.001)

    def test_asset_tax_classes(self):
        assert ASSET_TAX_CLASS["EQUITY"] == "valeurs_mobilieres"
        assert ASSET_TAX_CLASS["FX"] == "forex"
        assert ASSET_TAX_CLASS["FUTURES"] == "instruments_financiers_a_terme"
