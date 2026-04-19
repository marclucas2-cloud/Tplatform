"""Reconciliation cycle regression tests (Phase 6 XXL).

Validates orchestration: per-book dispatch, severity matrix alerts, metrics,
exception handling, report persistence.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import core.governance.reconciliation as reconciliation
from core.governance.reconciliation_cycle import run_reconciliation_cycle


@pytest.fixture
def isolated_recon_dir(tmp_path, monkeypatch):
    """Redirect RECONCILE_DIR to tmp."""
    monkeypatch.setattr(reconciliation, "RECONCILE_DIR", tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Orchestration & alerts
# ---------------------------------------------------------------------------

class TestReconciliationCycleOrchestration:
    def test_dispatches_to_each_book(self, isolated_recon_dir):
        calls = []
        def fake(book_id):
            calls.append(book_id)
            return {"book": book_id, "divergences": []}

        with patch("core.governance.reconciliation_cycle.reconcile_book", side_effect=fake):
            out = run_reconciliation_cycle(books=("binance_crypto", "ibkr_futures"))

        assert calls == ["binance_crypto", "ibkr_futures"]
        assert set(out.keys()) == {"binance_crypto", "ibkr_futures"}

    def test_per_book_exception_does_not_kill_cycle(self, isolated_recon_dir):
        def fake(book_id):
            if book_id == "binance_crypto":
                raise RuntimeError("binance unreachable")
            return {"book": book_id, "divergences": []}

        with patch("core.governance.reconciliation_cycle.reconcile_book", side_effect=fake):
            out = run_reconciliation_cycle(books=("binance_crypto", "ibkr_futures"))

        # Both books still in output
        assert "binance_crypto" in out
        assert "ibkr_futures" in out
        # binance has error captured
        assert "error" in out["binance_crypto"]

    def test_critical_alert_on_only_in_broker(self, isolated_recon_dir):
        alerts = []
        def fake(book_id):
            return {
                "book": book_id,
                "divergences": [
                    {"type": "only_in_broker", "symbols": ["BTCUSDT"]},
                ],
            }

        with patch("core.governance.reconciliation_cycle.reconcile_book", side_effect=fake):
            run_reconciliation_cycle(
                books=("binance_crypto",),
                alert_callback=lambda msg, lvl: alerts.append((msg, lvl)),
            )

        assert len(alerts) == 1
        msg, lvl = alerts[0]
        assert lvl == "critical"
        assert "RECONCILIATION CRITICAL" in msg
        assert "only_in_broker" in msg

    def test_critical_alert_on_only_in_local(self, isolated_recon_dir):
        alerts = []
        def fake(book_id):
            return {
                "book": book_id,
                "divergences": [
                    {"type": "only_in_local", "symbols": ["MES"]},
                ],
            }

        with patch("core.governance.reconciliation_cycle.reconcile_book", side_effect=fake):
            run_reconciliation_cycle(
                books=("ibkr_futures",),
                alert_callback=lambda msg, lvl: alerts.append((msg, lvl)),
            )
        assert any("only_in_local" in m for m, _ in alerts)

    def test_warning_alert_on_broker_query_error(self, isolated_recon_dir):
        alerts = []
        def fake(book_id):
            return {
                "book": book_id,
                "error": "connection timeout",
                "divergences": [],
            }

        with patch("core.governance.reconciliation_cycle.reconcile_book", side_effect=fake):
            run_reconciliation_cycle(
                books=("binance_crypto",),
                alert_callback=lambda msg, lvl: alerts.append((msg, lvl)),
            )
        assert any(lvl == "warning" for _, lvl in alerts)

    def test_metrics_emitted_per_book(self, isolated_recon_dir):
        metrics = []
        def fake(book_id):
            return {
                "book": book_id,
                "divergences": [{"type": "only_in_local", "symbols": ["X"]}],
                "broker_positions": [{"symbol": "BTC"}, {"symbol": "ETH"}],
            }

        with patch("core.governance.reconciliation_cycle.reconcile_book", side_effect=fake):
            run_reconciliation_cycle(
                books=("binance_crypto",),
                metrics_callback=lambda n, v, t: metrics.append((n, v, t)),
                alert_callback=lambda *_: None,
            )

        names = [m[0] for m in metrics]
        assert "reconciliation.binance_crypto.divergences" in names
        assert "reconciliation.binance_crypto.broker_positions" in names
        # Values
        for n, v, t in metrics:
            if "divergences" in n:
                assert v == 1.0
            if "broker_positions" in n:
                assert v == 2.0

    def test_no_alert_when_clean(self, isolated_recon_dir):
        alerts = []
        def fake(book_id):
            return {"book": book_id, "divergences": []}

        with patch("core.governance.reconciliation_cycle.reconcile_book", side_effect=fake):
            run_reconciliation_cycle(
                books=("binance_crypto",),
                alert_callback=lambda msg, lvl: alerts.append((msg, lvl)),
            )

        assert alerts == []

    def test_state_file_corrupted_alert(self, isolated_recon_dir):
        alerts = []
        def fake(book_id):
            return {
                "book": book_id,
                "divergences": [
                    {"type": "state_file_corrupted", "err": "JSON decode error"},
                ],
            }

        with patch("core.governance.reconciliation_cycle.reconcile_book", side_effect=fake):
            run_reconciliation_cycle(
                books=("ibkr_futures",),
                alert_callback=lambda msg, lvl: alerts.append((msg, lvl)),
            )
        assert any("corrupted" in m.lower() and lvl == "critical" for m, lvl in alerts)


# ---------------------------------------------------------------------------
# Report persistence
# ---------------------------------------------------------------------------

class TestReconciliationReportPersistence:
    def test_report_saved_to_file(self, isolated_recon_dir):
        def fake(book_id):
            return {
                "book": book_id,
                "ts": "2026-04-19T12:00:00+00:00",
                "divergences": [],
                "broker_equity": 9000.0,
            }

        with patch("core.governance.reconciliation_cycle.reconcile_book", side_effect=fake):
            run_reconciliation_cycle(books=("binance_crypto",))

        files = list(isolated_recon_dir.glob("binance_crypto_*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["broker_equity"] == 9000.0
