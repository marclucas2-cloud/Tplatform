"""Tests Phase 3.4 desk productif 2026-04-22: weekly_desk_review.

Couvre:
  - _week_slice filtre correctement les 7 jours
  - build_report avec CSV absent retourne dict valide (0 equity, 0 trades)
  - render_markdown produit un document non vide avec sections cles
  - render_telegram reste sous 500 chars avec 5+ metriques
"""
from __future__ import annotations

import importlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

UTC = timezone.utc
ROOT_PATH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_PATH))


def _reload_review(tmp_path, monkeypatch):
    import scripts.weekly_desk_review as mod
    importlib.reload(mod)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "REPORT_DIR", tmp_path / "reports" / "weekly")
    monkeypatch.setattr(mod, "LIVE_PNL_CSV", tmp_path / "data" / "live_pnl" / "daily_equity.csv")
    monkeypatch.setattr(mod, "LIVE_PNL_SUMMARY", tmp_path / "data" / "live_pnl" / "summary.json")
    (tmp_path / "reports" / "weekly").mkdir(parents=True, exist_ok=True)
    return mod


class TestWeekSlice:

    def test_filters_7_days(self, tmp_path, monkeypatch):
        mod = _reload_review(tmp_path, monkeypatch)
        week_end = date(2026, 4, 22)
        rows = [
            {"date": "2026-04-10", "total_equity_usd": 1000},  # outside
            {"date": "2026-04-16", "total_equity_usd": 2000},  # week_start
            {"date": "2026-04-22", "total_equity_usd": 3000},  # week_end
            {"date": "2026-04-23", "total_equity_usd": 4000},  # after
        ]
        out = mod._week_slice(rows, week_end)
        assert len(out) == 2
        assert out[0]["date"] == "2026-04-16"
        assert out[1]["date"] == "2026-04-22"

    def test_empty_rows_returns_empty(self, tmp_path, monkeypatch):
        mod = _reload_review(tmp_path, monkeypatch)
        assert mod._week_slice([], date(2026, 4, 22)) == []


class TestBuildReport:

    def test_no_data_returns_zero_equity(self, tmp_path, monkeypatch):
        mod = _reload_review(tmp_path, monkeypatch)
        report = mod.build_report(date(2026, 4, 22))
        assert report["equity_start_usd"] == 0.0
        assert report["equity_end_usd"] == 0.0
        assert report["pnl_week_usd"] == 0.0
        assert report["n_days_in_week"] == 0

    def test_with_data_computes_pnl(self, tmp_path, monkeypatch):
        mod = _reload_review(tmp_path, monkeypatch)
        (tmp_path / "data" / "live_pnl").mkdir(parents=True, exist_ok=True)
        csv_content = (
            "date,ibkr_equity_usd,binance_equity_usd,total_equity_usd,"
            "daily_return_pct,cum_return_pct,peak_equity_usd,drawdown_pct,source\n"
            "2026-04-16,11000,9000,20000,0.0,0.0,20000,0.0,live_brokers\n"
            "2026-04-22,11200,9100,20300,0.15,1.5,20300,0.0,live_brokers\n"
        )
        (tmp_path / "data" / "live_pnl" / "daily_equity.csv").write_text(csv_content, encoding="utf-8")

        report = mod.build_report(date(2026, 4, 22))
        assert report["equity_start_usd"] == 20000.0
        assert report["equity_end_usd"] == 20300.0
        assert report["pnl_week_usd"] == 300.0
        assert abs(report["return_week_pct"] - 1.5) < 0.01
        assert report["n_days_in_week"] == 2

    def test_report_has_all_sections(self, tmp_path, monkeypatch):
        mod = _reload_review(tmp_path, monkeypatch)
        report = mod.build_report(date(2026, 4, 22))
        required = {"equity_start_usd", "equity_end_usd", "pnl_week_usd",
                    "return_week_pct", "max_dd_week_pct", "trades_30d",
                    "capital_exposure", "incidents_active", "services",
                    "catalog_counts", "week_ending", "week_start"}
        assert required.issubset(set(report.keys()))


class TestRenderMarkdown:

    def test_markdown_has_all_sections(self, tmp_path, monkeypatch):
        mod = _reload_review(tmp_path, monkeypatch)
        report = mod.build_report(date(2026, 4, 22))
        md = mod.render_markdown(report)
        for section in ["# Weekly Desk Review",
                        "## 1. PnL & Equity",
                        "## 2. Trades live",
                        "## 3. Capital exposure",
                        "## 4. Incidents actifs",
                        "## 5. Services VPS",
                        "## 6. Catalogue strategies"]:
            assert section in md, f"Section manquante: {section}"


class TestRenderTelegram:

    def test_under_500_chars(self, tmp_path, monkeypatch):
        mod = _reload_review(tmp_path, monkeypatch)
        report = mod.build_report(date(2026, 4, 22))
        msg = mod.render_telegram(report)
        assert len(msg) < 500

    def test_contains_key_metrics(self, tmp_path, monkeypatch):
        mod = _reload_review(tmp_path, monkeypatch)
        report = mod.build_report(date(2026, 4, 22))
        msg = mod.render_telegram(report)
        for kw in ["PnL 7j", "Max DD", "Trades 30j", "Exposure", "Incidents"]:
            assert kw in msg, f"Metric manquante Telegram: {kw}"
