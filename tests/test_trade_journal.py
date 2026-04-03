"""
Tests for core.trade_journal — Automated Trade Journal.

Covers:
  - Journal creation (LIVE/PAPER modes, separate DBs)
  - Trade open/close recording
  - P&L calculation (EQUITY long/short, FX, FUTURES)
  - Slippage calculation (entry/exit, adverse/favorable)
  - Sequential trade ID generation
  - Query filters (strategy, instrument_type, date range, status, direction)
  - Daily/weekly/monthly summaries
  - P&L period reports
  - Edge cases (cancel, partial fills, double close)
"""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.trade_journal import TradeJournal

# ─── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    """Return a temporary DB path for tests."""
    return str(tmp_path / "test_journal.db")


@pytest.fixture
def journal(tmp_db):
    """Fresh PAPER journal in a temp directory."""
    return TradeJournal(mode="PAPER", db_path=tmp_db)


@pytest.fixture
def live_journal(tmp_path):
    """Fresh LIVE journal in a separate temp directory."""
    return TradeJournal(mode="LIVE", db_path=str(tmp_path / "live_journal.db"))


# ─── Helpers ────────────────────────────────────────────────────────────────

def _ts(offset_seconds=0):
    """UTC ISO timestamp with optional offset."""
    return (datetime.now(UTC) + timedelta(seconds=offset_seconds)).isoformat()


def _open_and_close_equity(journal, strategy="TEST_STRAT", instrument="AAPL",
                           direction="LONG", quantity=10,
                           entry_req=150.0, entry_fill=150.05,
                           exit_req=155.0, exit_fill=154.95,
                           exit_reason="TP_HIT", commission=0.10,
                           trade_id=None):
    """Helper to open and close a simple equity trade."""
    ts_signal = _ts(-60)
    ts_filled = _ts(-59)
    ts_closed = _ts(0)

    tid = journal.record_trade_open(
        trade_id=trade_id,
        strategy=strategy,
        instrument=instrument,
        instrument_type="EQUITY",
        direction=direction,
        quantity=quantity,
        entry_price_requested=entry_req,
        entry_price_filled=entry_fill,
        timestamp_signal=ts_signal,
        timestamp_filled=ts_filled,
    )
    result = journal.record_trade_close(
        trade_id=tid,
        exit_price_requested=exit_req,
        exit_price_filled=exit_fill,
        exit_reason=exit_reason,
        commission=commission,
        timestamp_closed=ts_closed,
    )
    return tid, result


# ─── 1. Journal creation ───────────────────────────────────────────────────

class TestJournalCreation:
    def test_create_paper_journal(self, tmp_db):
        j = TradeJournal(mode="PAPER", db_path=tmp_db)
        assert j.mode == "PAPER"
        assert Path(tmp_db).exists()

    def test_create_live_journal(self, tmp_path):
        db = str(tmp_path / "live.db")
        j = TradeJournal(mode="LIVE", db_path=db)
        assert j.mode == "LIVE"
        assert Path(db).exists()

    def test_invalid_mode_raises(self, tmp_db):
        with pytest.raises(ValueError, match="Invalid mode"):
            TradeJournal(mode="INVALID", db_path=tmp_db)

    def test_separate_dbs(self, tmp_path):
        paper = TradeJournal(mode="PAPER", db_path=str(tmp_path / "paper.db"))
        live = TradeJournal(mode="LIVE", db_path=str(tmp_path / "live.db"))
        # Record a trade in paper
        paper.record_trade_open(
            trade_id=None, strategy="S1", instrument="SPY",
            instrument_type="EQUITY", direction="LONG",
            quantity=1, entry_price_requested=100, entry_price_filled=100,
        )
        # Live should have no trades
        assert len(live.get_trades()) == 0
        assert len(paper.get_trades()) == 1


# ─── 2. Record open ────────────────────────────────────────────────────────

class TestRecordOpen:
    def test_basic_open(self, journal):
        tid = journal.record_trade_open(
            trade_id=None,
            strategy="ORB_5MIN_V2",
            instrument="AAPL",
            instrument_type="EQUITY",
            direction="LONG",
            quantity=10,
            entry_price_requested=175.50,
            entry_price_filled=175.55,
            stop_loss=174.00,
            take_profit=178.00,
        )
        assert tid.startswith("PAPER-")
        trade = journal.get_trade(tid)
        assert trade is not None
        assert trade["status"] == "OPEN"
        assert trade["instrument"] == "AAPL"
        assert trade["direction"] == "LONG"
        assert trade["quantity"] == 10
        assert trade["entry_price_filled"] == 175.55
        assert trade["stop_loss"] == 174.00
        assert trade["take_profit"] == 178.00

    def test_custom_trade_id(self, journal):
        tid = journal.record_trade_open(
            trade_id="CUSTOM-001",
            strategy="TEST",
            instrument="SPY",
            instrument_type="EQUITY",
            direction="LONG",
            quantity=5,
            entry_price_requested=450.0,
            entry_price_filled=450.0,
        )
        assert tid == "CUSTOM-001"

    def test_invalid_direction_raises(self, journal):
        with pytest.raises(ValueError, match="Invalid direction"):
            journal.record_trade_open(
                trade_id=None, strategy="S", instrument="X",
                instrument_type="EQUITY", direction="UP",
                quantity=1, entry_price_requested=1, entry_price_filled=1,
            )

    def test_invalid_instrument_type_raises(self, journal):
        with pytest.raises(ValueError, match="Invalid instrument_type"):
            journal.record_trade_open(
                trade_id=None, strategy="S", instrument="X",
                instrument_type="CRYPTO", direction="LONG",
                quantity=1, entry_price_requested=1, entry_price_filled=1,
            )

    def test_slippage_entry_long(self, journal):
        """LONG entry: filled > requested = positive (adverse) slippage."""
        tid = journal.record_trade_open(
            trade_id=None, strategy="S", instrument="AAPL",
            instrument_type="EQUITY", direction="LONG",
            quantity=1, entry_price_requested=100.00, entry_price_filled=100.05,
        )
        trade = journal.get_trade(tid)
        # 0.05 / 100.00 * 10000 = 5.0 bps
        assert trade["slippage_entry_bps"] == 5.0

    def test_slippage_entry_short(self, journal):
        """SHORT entry: filled < requested = positive (adverse) slippage."""
        tid = journal.record_trade_open(
            trade_id=None, strategy="S", instrument="AAPL",
            instrument_type="EQUITY", direction="SHORT",
            quantity=1, entry_price_requested=100.00, entry_price_filled=99.95,
        )
        trade = journal.get_trade(tid)
        # adverse for short: -(99.95 - 100.00) / 100.00 * 10000 = 5.0 bps
        assert trade["slippage_entry_bps"] == 5.0

    def test_favorable_slippage(self, journal):
        """LONG entry: filled < requested = negative (favorable) slippage."""
        tid = journal.record_trade_open(
            trade_id=None, strategy="S", instrument="SPY",
            instrument_type="EQUITY", direction="LONG",
            quantity=1, entry_price_requested=100.00, entry_price_filled=99.97,
        )
        trade = journal.get_trade(tid)
        assert trade["slippage_entry_bps"] == -3.0


# ─── 3. Record close & P&L ─────────────────────────────────────────────────

class TestRecordClose:
    def test_close_long_profit(self, journal):
        """LONG trade: buy 10 @ 150.05, sell @ 154.95, commission 0.10."""
        tid, result = _open_and_close_equity(journal)
        assert result["pnl_gross"] == round((154.95 - 150.05) * 10, 2)  # 49.0
        assert result["pnl_net"] == round(result["pnl_gross"] - 0.10, 2)  # 48.90
        assert result["pnl_pct"] > 0
        assert result["holding_seconds"] >= 0
        # Trade should be CLOSED
        trade = journal.get_trade(tid)
        assert trade["status"] == "CLOSED"
        assert trade["exit_reason"] == "TP_HIT"

    def test_close_long_loss(self, journal):
        """LONG trade with loss."""
        tid, result = _open_and_close_equity(
            journal, entry_req=150.0, entry_fill=150.0,
            exit_req=145.0, exit_fill=145.0,
            exit_reason="SL_HIT", commission=0.10,
        )
        expected_gross = (145.0 - 150.0) * 10  # -50.0
        assert result["pnl_gross"] == expected_gross
        assert result["pnl_net"] == expected_gross - 0.10  # -50.10
        assert result["pnl_pct"] < 0

    def test_close_short_profit(self, journal):
        """SHORT trade: sell @ 200.00, buy back @ 190.00."""
        tid, result = _open_and_close_equity(
            journal, direction="SHORT",
            entry_req=200.0, entry_fill=200.0,
            exit_req=190.0, exit_fill=190.0,
            commission=0.20,
        )
        expected_gross = (200.0 - 190.0) * 10  # 100.0
        assert result["pnl_gross"] == expected_gross
        assert result["pnl_net"] == expected_gross - 0.20

    def test_close_short_loss(self, journal):
        """SHORT trade with loss: sell @ 200, buy back @ 210."""
        tid, result = _open_and_close_equity(
            journal, direction="SHORT",
            entry_req=200.0, entry_fill=200.0,
            exit_req=210.0, exit_fill=210.0,
            commission=0.0,
        )
        expected_gross = (200.0 - 210.0) * 10  # -100.0
        assert result["pnl_gross"] == expected_gross

    def test_slippage_exit(self, journal):
        """Exit slippage is recorded correctly."""
        tid, result = _open_and_close_equity(
            journal, exit_req=155.0, exit_fill=154.90,
        )
        # LONG exit: filled < requested = adverse (positive)
        # adverse = -(154.90 - 155.0) / 155.0 * 10000 = 6.45 bps
        assert result["slippage_exit_bps"] == pytest.approx(6.45, abs=0.1)

    def test_close_not_found_raises(self, journal):
        with pytest.raises(ValueError, match="not found"):
            journal.record_trade_close(
                trade_id="NONEXISTENT", exit_price_requested=100,
                exit_price_filled=100, exit_reason="MANUAL",
            )

    def test_double_close_raises(self, journal):
        tid, _ = _open_and_close_equity(journal)
        with pytest.raises(ValueError, match="already closed"):
            journal.record_trade_close(
                trade_id=tid, exit_price_requested=160,
                exit_price_filled=160, exit_reason="MANUAL",
            )

    def test_invalid_exit_reason_raises(self, journal):
        tid = journal.record_trade_open(
            trade_id=None, strategy="S", instrument="X",
            instrument_type="EQUITY", direction="LONG",
            quantity=1, entry_price_requested=100, entry_price_filled=100,
        )
        with pytest.raises(ValueError, match="Invalid exit_reason"):
            journal.record_trade_close(
                trade_id=tid, exit_price_requested=110,
                exit_price_filled=110, exit_reason="TIMEOUT",
            )


# ─── 4. Sequential trade IDs ───────────────────────────────────────────────

class TestTradeIDGeneration:
    def test_sequential_ids(self, journal):
        ids = []
        for i in range(5):
            tid = journal.record_trade_open(
                trade_id=None, strategy="S", instrument="X",
                instrument_type="EQUITY", direction="LONG",
                quantity=1, entry_price_requested=100, entry_price_filled=100,
            )
            ids.append(tid)

        year = datetime.now(UTC).strftime("%Y")
        assert ids[0] == f"PAPER-{year}-0001"
        assert ids[1] == f"PAPER-{year}-0002"
        assert ids[4] == f"PAPER-{year}-0005"

    def test_live_mode_prefix(self, live_journal):
        tid = live_journal.record_trade_open(
            trade_id=None, strategy="S", instrument="X",
            instrument_type="EQUITY", direction="LONG",
            quantity=1, entry_price_requested=100, entry_price_filled=100,
        )
        assert tid.startswith("LIVE-")


# ─── 5. FX trades ──────────────────────────────────────────────────────────

class TestFXTrades:
    def test_fx_pnl_long(self, journal):
        """FX LONG: buy EUR/USD 100,000 units @ 1.0800, sell @ 1.0850."""
        tid = journal.record_trade_open(
            trade_id=None, strategy="FX_CARRY", instrument="EUR/USD",
            instrument_type="FX", direction="LONG",
            quantity=100000, entry_price_requested=1.0800, entry_price_filled=1.0800,
        )
        result = journal.record_trade_close(
            trade_id=tid, exit_price_requested=1.0850, exit_price_filled=1.0850,
            exit_reason="TP_HIT",
        )
        # P&L = (1.0850 - 1.0800) * 100000 = 500.0
        assert result["pnl_gross"] == 500.0

    def test_fx_pnl_short(self, journal):
        """FX SHORT: sell EUR/USD 50,000 @ 1.0900, buy back @ 1.0850."""
        tid = journal.record_trade_open(
            trade_id=None, strategy="FX_CARRY", instrument="EUR/USD",
            instrument_type="FX", direction="SHORT",
            quantity=50000, entry_price_requested=1.0900, entry_price_filled=1.0900,
        )
        result = journal.record_trade_close(
            trade_id=tid, exit_price_requested=1.0850, exit_price_filled=1.0850,
            exit_reason="SIGNAL",
        )
        # P&L = (1.0900 - 1.0850) * 50000 = 250.0
        assert result["pnl_gross"] == 250.0


# ─── 6. Futures trades ─────────────────────────────────────────────────────

class TestFuturesTrades:
    def test_futures_pnl_es(self, journal):
        """ES futures LONG: buy 2 contracts @ 5100, sell @ 5110. Multiplier=50."""
        tid = journal.record_trade_open(
            trade_id=None, strategy="MOMENTUM_FUTURES", instrument="ES",
            instrument_type="FUTURES", direction="LONG",
            quantity=2, entry_price_requested=5100, entry_price_filled=5100,
        )
        result = journal.record_trade_close(
            trade_id=tid, exit_price_requested=5110, exit_price_filled=5110,
            exit_reason="TP_HIT",
        )
        # P&L = (5110 - 5100) * 2 * 50 = 1000.0
        assert result["pnl_gross"] == 1000.0

    def test_futures_pnl_gc_short(self, journal):
        """GC (Gold) futures SHORT: sell 1 @ 2000, buy back @ 1990. Multiplier=100."""
        tid = journal.record_trade_open(
            trade_id=None, strategy="GOLD_MEAN_REV", instrument="GC",
            instrument_type="FUTURES", direction="SHORT",
            quantity=1, entry_price_requested=2000, entry_price_filled=2000,
        )
        result = journal.record_trade_close(
            trade_id=tid, exit_price_requested=1990, exit_price_filled=1990,
            exit_reason="SIGNAL",
        )
        # P&L = (2000 - 1990) * 1 * 100 = 1000.0
        assert result["pnl_gross"] == 1000.0

    def test_futures_unknown_root_defaults_multiplier_1(self, journal):
        """Unknown futures root should use multiplier 1.0."""
        tid = journal.record_trade_open(
            trade_id=None, strategy="EXOTIC", instrument="ZZ",
            instrument_type="FUTURES", direction="LONG",
            quantity=5, entry_price_requested=100, entry_price_filled=100,
        )
        result = journal.record_trade_close(
            trade_id=tid, exit_price_requested=110, exit_price_filled=110,
            exit_reason="MANUAL",
        )
        # multiplier = 1.0, P&L = (110 - 100) * 5 * 1 = 50.0
        assert result["pnl_gross"] == 50.0


# ─── 7. Query filters ──────────────────────────────────────────────────────

class TestQueryFilters:
    def test_filter_by_strategy(self, journal):
        _open_and_close_equity(journal, strategy="STRAT_A")
        _open_and_close_equity(journal, strategy="STRAT_B")
        _open_and_close_equity(journal, strategy="STRAT_A")

        results = journal.get_trades(strategy="STRAT_A")
        assert len(results) == 2
        assert all(t["strategy"] == "STRAT_A" for t in results)

    def test_filter_by_instrument_type(self, journal):
        _open_and_close_equity(journal, instrument="AAPL")
        # Open an FX trade
        tid = journal.record_trade_open(
            trade_id=None, strategy="FX", instrument="EUR/USD",
            instrument_type="FX", direction="LONG",
            quantity=1000, entry_price_requested=1.08, entry_price_filled=1.08,
        )
        journal.record_trade_close(
            trade_id=tid, exit_price_requested=1.09, exit_price_filled=1.09,
            exit_reason="SIGNAL",
        )
        results = journal.get_trades(instrument_type="FX")
        assert len(results) == 1
        assert results[0]["instrument"] == "EUR/USD"

    def test_filter_by_status(self, journal):
        # One closed
        _open_and_close_equity(journal, strategy="S1")
        # One open
        journal.record_trade_open(
            trade_id=None, strategy="S2", instrument="SPY",
            instrument_type="EQUITY", direction="LONG",
            quantity=1, entry_price_requested=100, entry_price_filled=100,
        )
        open_trades = journal.get_trades(status="OPEN")
        closed_trades = journal.get_trades(status="CLOSED")
        assert len(open_trades) == 1
        assert len(closed_trades) == 1

    def test_filter_by_direction(self, journal):
        _open_and_close_equity(journal, direction="LONG")
        _open_and_close_equity(journal, direction="SHORT",
                               entry_req=200, entry_fill=200,
                               exit_req=190, exit_fill=190)
        longs = journal.get_trades(direction="LONG")
        shorts = journal.get_trades(direction="SHORT")
        assert len(longs) == 1
        assert len(shorts) == 1

    def test_filter_by_date_range(self, journal):
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        _open_and_close_equity(journal)
        results = journal.get_trades(start_date=today, end_date=today)
        assert len(results) >= 1

    def test_get_open_trades(self, journal):
        journal.record_trade_open(
            trade_id=None, strategy="S", instrument="AAPL",
            instrument_type="EQUITY", direction="LONG",
            quantity=1, entry_price_requested=100, entry_price_filled=100,
        )
        journal.record_trade_open(
            trade_id=None, strategy="S", instrument="SPY",
            instrument_type="EQUITY", direction="SHORT",
            quantity=1, entry_price_requested=450, entry_price_filled=450,
        )
        opens = journal.get_open_trades()
        assert len(opens) == 2

    def test_limit(self, journal):
        for i in range(5):
            _open_and_close_equity(journal)
        results = journal.get_trades(limit=3)
        assert len(results) == 3


# ─── 8. Cancel trades ──────────────────────────────────────────────────────

class TestCancelTrade:
    def test_cancel_open_trade(self, journal):
        tid = journal.record_trade_open(
            trade_id=None, strategy="S", instrument="AAPL",
            instrument_type="EQUITY", direction="LONG",
            quantity=1, entry_price_requested=100, entry_price_filled=100,
        )
        journal.cancel_trade(tid, reason="Order rejected by broker")
        trade = journal.get_trade(tid)
        assert trade["status"] == "CANCELLED"
        assert "CANCELLED" in (trade["notes"] or "")

    def test_cancel_closed_raises(self, journal):
        tid, _ = _open_and_close_equity(journal)
        with pytest.raises(ValueError, match="already closed"):
            journal.cancel_trade(tid)

    def test_cancel_not_found_raises(self, journal):
        with pytest.raises(ValueError, match="not found"):
            journal.cancel_trade("NONEXISTENT")


# ─── 9. Daily summary ──────────────────────────────────────────────────────

class TestDailySummary:
    def test_daily_summary_basic(self, journal):
        _open_and_close_equity(journal, strategy="S1",
                               entry_fill=100, exit_fill=110, commission=1.0)
        _open_and_close_equity(journal, strategy="S2",
                               entry_fill=100, exit_fill=95, commission=0.5)

        summary = journal.get_daily_summary()
        assert summary["closed_trades"] == 2
        assert summary["winners"] == 1
        assert summary["losers"] == 1
        assert summary["win_rate"] == 50.0
        assert summary["pnl_gross"] > 0  # net positive (100 - 50 = 50)
        assert summary["total_commission"] == 1.5
        assert len(summary["strategies_active"]) == 2

    def test_daily_summary_no_trades(self, journal):
        summary = journal.get_daily_summary(date="2020-01-01")
        assert summary["total_trades"] == 0
        assert summary["win_rate"] == 0.0

    def test_daily_summary_best_worst(self, journal):
        _open_and_close_equity(journal, strategy="WINNER",
                               entry_fill=100, exit_fill=120, commission=0)
        _open_and_close_equity(journal, strategy="LOSER",
                               entry_fill=100, exit_fill=80, commission=0)

        summary = journal.get_daily_summary()
        assert summary["best_trade"]["pnl_net"] > 0
        assert summary["worst_trade"]["pnl_net"] < 0


# ─── 10. Weekly summary ────────────────────────────────────────────────────

class TestWeeklySummary:
    def test_weekly_summary(self, journal):
        # Create some trades
        for i in range(3):
            _open_and_close_equity(journal, entry_fill=100, exit_fill=105, commission=0.5)
        _open_and_close_equity(journal, entry_fill=100, exit_fill=95, commission=0.5)

        summary = journal.get_weekly_summary()
        assert summary["total_trades"] == 4
        assert summary["win_rate"] == 75.0
        assert summary["total_pnl_net"] > 0
        assert "strategies_breakdown" in summary

    def test_weekly_summary_empty(self, journal):
        summary = journal.get_weekly_summary()
        assert summary["total_trades"] == 0
        assert summary["sharpe_ratio"] == 0.0


# ─── 11. Monthly summary ───────────────────────────────────────────────────

class TestMonthlySummary:
    def test_monthly_summary(self, journal):
        _open_and_close_equity(journal, strategy="ORB", entry_fill=100, exit_fill=115)
        _open_and_close_equity(journal, strategy="MR", entry_fill=100, exit_fill=108)

        summary = journal.get_monthly_summary()
        assert summary["total_trades"] == 2
        assert summary["total_pnl_net"] > 0
        assert "ORB" in summary["strategies_breakdown"]
        assert "MR" in summary["strategies_breakdown"]


# ─── 12. P&L period ────────────────────────────────────────────────────────

class TestGetPnL:
    def test_pnl_today(self, journal):
        _open_and_close_equity(journal, entry_fill=100, exit_fill=110, commission=1.0)
        pnl = journal.get_pnl("today")
        assert pnl["period"] == "today"
        assert pnl["n_trades"] == 1
        assert pnl["pnl_gross"] == 100.0  # (110-100)*10
        assert pnl["pnl_net"] == 99.0
        assert pnl["total_commission"] == 1.0

    def test_pnl_mtd(self, journal):
        _open_and_close_equity(journal, entry_fill=100, exit_fill=105)
        pnl = journal.get_pnl("mtd")
        assert pnl["n_trades"] >= 1

    def test_pnl_ytd(self, journal):
        pnl = journal.get_pnl("ytd")
        assert pnl["n_trades"] == 0

    def test_pnl_7d(self, journal):
        _open_and_close_equity(journal)
        pnl = journal.get_pnl("7d")
        assert pnl["n_trades"] >= 1

    def test_pnl_30d(self, journal):
        _open_and_close_equity(journal)
        pnl = journal.get_pnl("30d")
        assert pnl["n_trades"] >= 1

    def test_pnl_invalid_period_raises(self, journal):
        with pytest.raises(ValueError, match="Invalid period"):
            journal.get_pnl("2y")

    def test_pnl_win_rate(self, journal):
        _open_and_close_equity(journal, entry_fill=100, exit_fill=110)  # win
        _open_and_close_equity(journal, entry_fill=100, exit_fill=110)  # win
        _open_and_close_equity(journal, entry_fill=100, exit_fill=90)   # loss

        pnl = journal.get_pnl("today")
        assert pnl["win_rate"] == pytest.approx(66.7, abs=0.1)


# ─── 13. Holding time ──────────────────────────────────────────────────────

class TestHoldingTime:
    def test_holding_seconds_calculated(self, journal):
        ts_open = _ts(-3600)  # 1 hour ago
        ts_close = _ts(0)     # now
        tid = journal.record_trade_open(
            trade_id=None, strategy="S", instrument="SPY",
            instrument_type="EQUITY", direction="LONG",
            quantity=1, entry_price_requested=100, entry_price_filled=100,
            timestamp_filled=ts_open,
        )
        result = journal.record_trade_close(
            trade_id=tid, exit_price_requested=101, exit_price_filled=101,
            exit_reason="SIGNAL", timestamp_closed=ts_close,
        )
        # Should be approximately 3600 seconds
        assert 3590 <= result["holding_seconds"] <= 3610


# ─── 14. Latency ───────────────────────────────────────────────────────────

class TestLatency:
    def test_latency_calculated(self, journal):
        ts_signal = _ts(-5)  # 5 seconds ago
        ts_filled = _ts(0)   # now
        tid = journal.record_trade_open(
            trade_id=None, strategy="S", instrument="QQQ",
            instrument_type="EQUITY", direction="LONG",
            quantity=1, entry_price_requested=400, entry_price_filled=400,
            timestamp_signal=ts_signal, timestamp_filled=ts_filled,
        )
        trade = journal.get_trade(tid)
        # Should be approximately 5000 ms
        assert 4500 <= trade["latency_signal_to_fill_ms"] <= 5500


# ─── 15. Profit factor & Sharpe in summaries ───────────────────────────────

class TestAdvancedMetrics:
    def test_profit_factor(self, journal):
        # 3 winners of $50 each, 1 loser of -$50
        for _ in range(3):
            _open_and_close_equity(journal, entry_fill=100, exit_fill=105, commission=0)
        _open_and_close_equity(journal, entry_fill=100, exit_fill=95, commission=0)

        summary = journal.get_weekly_summary()
        # gross_wins = 150, gross_losses = 50 => PF = 3.0
        assert summary["profit_factor"] == 3.0

    def test_profit_factor_no_losses(self, journal):
        _open_and_close_equity(journal, entry_fill=100, exit_fill=110, commission=0)
        summary = journal.get_weekly_summary()
        assert summary["profit_factor"] == "inf"

    def test_strategies_breakdown_in_summary(self, journal):
        _open_and_close_equity(journal, strategy="ORB", entry_fill=100, exit_fill=110, commission=0)
        _open_and_close_equity(journal, strategy="ORB", entry_fill=100, exit_fill=108, commission=0)
        _open_and_close_equity(journal, strategy="MR", entry_fill=100, exit_fill=95, commission=0)

        summary = journal.get_weekly_summary()
        assert "ORB" in summary["strategies_breakdown"]
        assert summary["strategies_breakdown"]["ORB"]["n_trades"] == 2
        assert summary["strategies_breakdown"]["ORB"]["win_rate"] == 100.0
        assert "MR" in summary["strategies_breakdown"]
        assert summary["strategies_breakdown"]["MR"]["win_rate"] == 0.0


# ─── 16. Notes merging ─────────────────────────────────────────────────────

class TestNotes:
    def test_notes_on_open_and_close(self, journal):
        tid = journal.record_trade_open(
            trade_id=None, strategy="S", instrument="AAPL",
            instrument_type="EQUITY", direction="LONG",
            quantity=1, entry_price_requested=100, entry_price_filled=100,
            notes="Opened during high vol regime",
        )
        journal.record_trade_close(
            trade_id=tid, exit_price_requested=110, exit_price_filled=110,
            exit_reason="TP_HIT", notes="Clean exit at target",
        )
        trade = journal.get_trade(tid)
        assert "high vol regime" in trade["notes"]
        assert "Clean exit" in trade["notes"]


# ─── 17. Regime and confluence metadata ─────────────────────────────────────

class TestMetadata:
    def test_regime_and_confluence_stored(self, journal):
        tid = journal.record_trade_open(
            trade_id=None, strategy="S", instrument="SPY",
            instrument_type="EQUITY", direction="LONG",
            quantity=10, entry_price_requested=450, entry_price_filled=450,
            regime="BULL_LOW_VOL", confluence_score=0.85,
            conviction_level="HIGH",
        )
        trade = journal.get_trade(tid)
        assert trade["regime"] == "BULL_LOW_VOL"
        assert trade["confluence_score"] == 0.85
        assert trade["conviction_level"] == "HIGH"
