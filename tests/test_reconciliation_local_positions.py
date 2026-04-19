"""Regression tests for reconcile_binance_crypto + reconcile_alpaca_us local_positions
extraction.

Incident 2026-04-19: reconcile_binance_crypto mistakenly listed DDBaselines
metadata keys (`peak_equity`, `session_id`, `schema_version`, ...) as
"positions", generating false CRITICAL reconciliation alerts. Same pattern
for reconcile_alpaca_us with strategy_ids (`vrp_rotation`) from
paper_portfolio_state.json instead of tickers.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import core.governance.reconciliation as rec


@pytest.fixture
def isolated_root(tmp_path, monkeypatch):
    """Point rec.ROOT to a tmp dir so local state file paths are controlled."""
    monkeypatch.setattr(rec, "ROOT", tmp_path)
    (tmp_path / "data" / "state" / "binance_crypto").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "state" / "alpaca_us").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "state").mkdir(parents=True, exist_ok=True)
    return tmp_path


class TestBinanceLocalPositions:
    def test_ignores_dd_baselines_metadata_file(self, isolated_root):
        """crypto_dd_state.json must NOT be parsed as positions (incident 2026-04-19)."""
        dd_state = isolated_root / "data" / "crypto_dd_state.json"
        dd_state.write_text(json.dumps({
            "schema_version": 1,
            "session_id": "migrated-legacy",
            "peak_equity": 10000.0,
            "daily_anchor": "2026-04-19",
            "daily_start_equity": 5600.58,
            "weekly_anchor": "2026-W16",
            "monthly_anchor": "2026-04",
            "total_equity": 10580.06,
            "last_check_ts": 1776599107,
            "weekly_start_equity": 5600.58,
            "monthly_start_equity": 5600.58,
        }))

        with patch("core.broker.binance_broker.BinanceBroker") as MockBroker:
            mock = MagicMock()
            mock.get_account_info.return_value = {"equity": 9840.0}
            mock.get_positions.return_value = []
            MockBroker.return_value = mock
            result = rec.reconcile_binance_crypto()

        assert result["local_positions"] == []
        assert result["divergences"] == [], (
            f"No divergence expected (crypto_dd_state.json is NOT a positions file), "
            f"got: {result['divergences']}"
        )

    def test_reads_canonical_positions_json(self, isolated_root):
        positions_path = isolated_root / "data" / "state" / "binance_crypto" / "positions.json"
        positions_path.write_text(json.dumps({
            "positions": {"BTCUSDC": {"qty": 0.1, "avg_entry": 68000}},
        }))

        with patch("core.broker.binance_broker.BinanceBroker") as MockBroker:
            mock = MagicMock()
            mock.get_account_info.return_value = {"equity": 9840.0}
            mock.get_positions.return_value = []
            MockBroker.return_value = mock
            result = rec.reconcile_binance_crypto()

        assert result["local_positions"] == ["BTCUSDC"]
        # broker empty, local has BTCUSDC -> only_in_local divergence
        assert any(d["type"] == "only_in_local" for d in result["divergences"])

    def test_accepts_flat_dict_format(self, isolated_root):
        """Also accept {"SYMBOL": {...}} without top-level 'positions' key."""
        positions_path = isolated_root / "data" / "state" / "binance_crypto" / "positions.json"
        positions_path.write_text(json.dumps({"ETHUSDC": {"qty": 1.0}}))

        with patch("core.broker.binance_broker.BinanceBroker") as MockBroker:
            mock = MagicMock()
            mock.get_account_info.return_value = {"equity": 9840.0}
            mock.get_positions.return_value = []
            MockBroker.return_value = mock
            result = rec.reconcile_binance_crypto()

        assert "ETHUSDC" in result["local_positions"]

    def test_corrupted_positions_file_reports_error(self, isolated_root):
        positions_path = isolated_root / "data" / "state" / "binance_crypto" / "positions.json"
        positions_path.write_text("{ not valid json")

        with patch("core.broker.binance_broker.BinanceBroker") as MockBroker:
            mock = MagicMock()
            mock.get_account_info.return_value = {"equity": 9840.0}
            mock.get_positions.return_value = []
            MockBroker.return_value = mock
            result = rec.reconcile_binance_crypto()

        assert any(d["type"] == "state_file_corrupted" for d in result["divergences"])


class TestAlpacaLocalPositions:
    def test_paper_portfolio_extracts_tickers_from_symbols_list(self, isolated_root):
        """paper_portfolio_state.json has strategy_id as key with symbols[] inside.
        We must extract the inner symbols (incident 2026-04-19)."""
        paper = isolated_root / "data" / "state" / "paper_portfolio_state.json"
        paper.write_text(json.dumps({
            "capital": 99495.42,
            "positions": {
                "vrp_rotation": {"symbols": ["SPY"]},
                "momentum_25etf": {"symbols": ["QQQ", "VTI"]},
            },
        }))

        with patch("core.alpaca_client.client.AlpacaClient") as MockClient:
            mock = MagicMock()
            mock.get_account_info.return_value = {"equity": 99495.42}
            mock.get_positions.return_value = []
            MockClient.from_env.return_value = mock
            result = rec.reconcile_alpaca_us()

        # Must contain real tickers, not strategy_ids
        assert "SPY" in result["local_positions"]
        assert "QQQ" in result["local_positions"]
        assert "VTI" in result["local_positions"]
        assert "vrp_rotation" not in result["local_positions"]
        assert "momentum_25etf" not in result["local_positions"]

    def test_paper_portfolio_no_symbols_means_no_position(self, isolated_root):
        """Strategy mapped but no symbols yet = no position to reconcile."""
        paper = isolated_root / "data" / "state" / "paper_portfolio_state.json"
        paper.write_text(json.dumps({
            "positions": {
                "vrp_rotation": {"symbols": []},
                "dow_seasonal": {},  # no symbols key at all
            },
        }))

        with patch("core.alpaca_client.client.AlpacaClient") as MockClient:
            mock = MagicMock()
            mock.get_account_info.return_value = {"equity": 99495.42}
            mock.get_positions.return_value = []
            MockClient.from_env.return_value = mock
            result = rec.reconcile_alpaca_us()

        assert result["local_positions"] == []
        assert result["divergences"] == []

    def test_canonical_alpaca_positions_json_preferred(self, isolated_root):
        """If data/state/alpaca_us/positions.json exists, prefer it over legacy."""
        canonical = isolated_root / "data" / "state" / "alpaca_us" / "positions.json"
        canonical.write_text(json.dumps({"positions": {"SPY": {"qty": 10}}}))
        legacy = isolated_root / "data" / "state" / "paper_portfolio_state.json"
        legacy.write_text(json.dumps({
            "positions": {"vrp_rotation": {"symbols": ["SHOULD_NOT_APPEAR"]}}
        }))

        with patch("core.alpaca_client.client.AlpacaClient") as MockClient:
            mock = MagicMock()
            mock.get_account_info.return_value = {"equity": 99495.42}
            mock.get_positions.return_value = []
            MockClient.from_env.return_value = mock
            result = rec.reconcile_alpaca_us()

        assert "SPY" in result["local_positions"]
        assert "SHOULD_NOT_APPEAR" not in result["local_positions"]
