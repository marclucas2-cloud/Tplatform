"""
Tests for preflight_check — pre-flight verification before worker start.

Covers 14 checks, each with PASS and FAIL cases:
  1.  binance_auth — Binance API key present + authenticate succeeds
  2.  binance_cash — cash or equity > $0
  3.  binance_positions — positions retrieval
  4.  ibkr_live_connect — port 4002 reachable
  5.  ibkr_live_equity — equity >= $5K
  6.  ibkr_paper — port 4003 reachable (warning only)
  7.  fx_data — parquets < 48h old
  8.  crypto_data — Binance API responds for BTCUSDC
  9.  earn_usdc — USDC in Earn Flexible
  10. margin — at least 1 isolated margin pair
  11. kill_switch — not active by mistake
  12. disk_space — > 1GB free
  13. ibgateway — port 4002 listens
  14. telegram — bot responds

All external dependencies mocked (no real broker connections, no real file I/O).
"""

import json
import os
import shutil
import socket
import sys
import time
from collections import namedtuple
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.preflight_check import (
    PreflightResult,
    _check_binance,
    _check_crypto_data,
    _check_disk,
    _check_earn,
    _check_fx_data,
    _check_ibgateway,
    _check_ibkr_live,
    _check_ibkr_paper,
    _check_kill_switch,
    _check_margin,
    _check_telegram,
    _persist_result,
    run_preflight,
)


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def result():
    """Fresh PreflightResult for each test."""
    return PreflightResult()


@pytest.fixture
def mock_binance_broker():
    """Mock BinanceBroker with standard responses."""
    broker = MagicMock()
    broker.authenticate.return_value = {"permissions": ["SPOT", "MARGIN"]}
    broker.get_account_info.return_value = {"cash": 5000, "equity": 10000}
    broker.get_positions.return_value = [
        {"symbol": "BTCUSDC", "qty": 0.1},
        {"symbol": "ETHUSDC", "qty": 1.5},
    ]
    return broker


# =============================================================================
# PreflightResult UNIT TESTS
# =============================================================================


class TestPreflightResult:
    def test_empty_result_passes(self):
        r = PreflightResult()
        assert r.all_passed is True
        assert r.blockers == []
        assert r.warnings == []

    def test_add_passing_check(self, result):
        result.add("test_check", True, "OK")
        assert result.checks["test_check"]["passed"] is True
        assert result.all_passed is True

    def test_add_blocking_failure(self, result):
        result.add("critical", False, "broken", blocking=True)
        assert result.all_passed is False
        assert len(result.blockers) == 1
        assert "critical" in result.blockers[0]

    def test_add_warning_failure(self, result):
        result.add("optional", False, "minor issue", blocking=False)
        assert result.all_passed is True  # warnings don't block
        assert len(result.warnings) == 1
        assert "optional" in result.warnings[0]

    def test_summary_format(self, result):
        result.add("check_a", True, "all good")
        result.add("check_b", False, "not good", blocking=True)
        result.add("check_c", False, "meh", blocking=False)
        summary = result.summary()
        assert "1/3 checks passed" in summary
        assert "[PASS] check_a" in summary
        assert "[FAIL] check_b" in summary
        assert "[FAIL] check_c" in summary
        assert "BLOCKER" in summary
        assert "warning" in summary.lower()

    def test_summary_all_pass(self, result):
        result.add("a", True, "ok")
        result.add("b", True, "ok")
        summary = result.summary()
        assert "2/2 checks passed" in summary
        assert "BLOCKER" not in summary


# =============================================================================
# CHECK: BINANCE AUTH + CASH + POSITIONS
# =============================================================================


class TestCheckBinance:
    @patch.dict(os.environ, {"BINANCE_API_KEY": "test_key"})
    @patch("scripts.preflight_check.BinanceBroker", create=True)
    def test_binance_pass(self, mock_cls, result, mock_binance_broker):
        """Binance auth + cash + positions all pass."""
        # We need to mock the import inside the function
        with patch.dict("sys.modules", {}):
            with patch("scripts.preflight_check.BinanceBroker", create=True):
                pass

        # Direct approach: mock at the import level
        mock_module = MagicMock()
        mock_module.BinanceBroker.return_value = mock_binance_broker
        with patch.dict("sys.modules", {"core.broker.binance_broker": mock_module}):
            _check_binance(result)

        assert result.checks["binance_auth"]["passed"] is True
        assert result.checks["binance_cash"]["passed"] is True
        assert result.checks["binance_positions"]["passed"] is True
        assert "permissions" in result.checks["binance_auth"]["message"]

    @patch.dict(os.environ, {}, clear=False)
    def test_binance_no_api_key(self, result):
        """BINANCE_API_KEY not set -> fail immediately."""
        env = os.environ.copy()
        env.pop("BINANCE_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            _check_binance(result)
        assert result.checks["binance_auth"]["passed"] is False
        assert "not set" in result.checks["binance_auth"]["message"]

    @patch.dict(os.environ, {"BINANCE_API_KEY": "test_key"})
    def test_binance_zero_cash(self, result):
        """Binance auth OK but cash=0 and equity=0 -> cash check fails."""
        mock_broker = MagicMock()
        mock_broker.authenticate.return_value = {"permissions": ["SPOT"]}
        mock_broker.get_account_info.return_value = {"cash": 0, "equity": 0}
        mock_broker.get_positions.return_value = []
        mock_module = MagicMock()
        mock_module.BinanceBroker.return_value = mock_broker
        with patch.dict("sys.modules", {"core.broker.binance_broker": mock_module}):
            _check_binance(result)
        assert result.checks["binance_auth"]["passed"] is True
        assert result.checks["binance_cash"]["passed"] is False

    @patch.dict(os.environ, {"BINANCE_API_KEY": "test_key"})
    def test_binance_exception(self, result):
        """Binance broker raises exception -> fail."""
        mock_module = MagicMock()
        mock_module.BinanceBroker.side_effect = Exception("connection refused")
        with patch.dict("sys.modules", {"core.broker.binance_broker": mock_module}):
            _check_binance(result)
        assert result.checks["binance_auth"]["passed"] is False
        assert "ERREUR" in result.checks["binance_auth"]["message"]


# =============================================================================
# CHECK: IBKR LIVE
# =============================================================================


class TestCheckIBKRLive:
    @patch.dict(os.environ, {"IBKR_HOST": "127.0.0.1", "IBKR_PORT": "4002"})
    @patch("socket.create_connection")
    def test_ibkr_live_connect_pass(self, mock_conn, result):
        """Port 4002 reachable + equity >= $5K -> pass."""
        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        mock_ibkr = MagicMock()
        mock_ibkr.get_account_info.return_value = {"equity": 10000}
        mock_module = MagicMock()
        mock_module.IBKRBroker.return_value = mock_ibkr
        with patch.dict("sys.modules", {"core.broker.ibkr_adapter": mock_module}):
            _check_ibkr_live(result)

        assert result.checks["ibkr_live_connect"]["passed"] is True
        assert result.checks["ibkr_live_equity"]["passed"] is True

    @patch.dict(os.environ, {"IBKR_HOST": "127.0.0.1", "IBKR_PORT": "4002"})
    @patch("socket.create_connection", side_effect=OSError("Connection refused"))
    def test_ibkr_live_connect_fail(self, mock_conn, result):
        """Port 4002 unreachable -> blocker, skip equity check."""
        _check_ibkr_live(result)
        assert result.checks["ibkr_live_connect"]["passed"] is False
        assert "unreachable" in result.checks["ibkr_live_connect"]["message"]
        assert "ibkr_live_equity" not in result.checks  # skipped

    @patch.dict(os.environ, {"IBKR_HOST": "127.0.0.1", "IBKR_PORT": "4002"})
    @patch("socket.create_connection")
    def test_ibkr_live_low_equity(self, mock_conn, result):
        """Port OK but equity < $5K -> fail."""
        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        mock_ibkr = MagicMock()
        mock_ibkr.get_account_info.return_value = {"equity": 3000}
        mock_module = MagicMock()
        mock_module.IBKRBroker.return_value = mock_ibkr
        with patch.dict("sys.modules", {"core.broker.ibkr_adapter": mock_module}):
            _check_ibkr_live(result)

        assert result.checks["ibkr_live_equity"]["passed"] is False
        assert "< $5K" in result.checks["ibkr_live_equity"]["message"]


# =============================================================================
# CHECK: IBKR PAPER
# =============================================================================


class TestCheckIBKRPaper:
    @patch.dict(os.environ, {"IBKR_HOST": "127.0.0.1", "IBKR_PAPER_PORT": "4003"})
    @patch("socket.create_connection")
    def test_ibkr_paper_pass(self, mock_conn, result):
        """Port 4003 reachable -> pass (non-blocking)."""
        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        _check_ibkr_paper(result)
        assert result.checks["ibkr_paper"]["passed"] is True
        assert result.all_passed is True  # non-blocking regardless

    @patch.dict(os.environ, {"IBKR_HOST": "127.0.0.1", "IBKR_PAPER_PORT": "4003"})
    @patch("socket.create_connection", side_effect=OSError("refused"))
    def test_ibkr_paper_fail(self, mock_conn, result):
        """Port 4003 unreachable -> warning only, not blocking."""
        _check_ibkr_paper(result)
        assert result.checks["ibkr_paper"]["passed"] is False
        assert result.all_passed is True  # non-blocking
        assert len(result.warnings) == 1


# =============================================================================
# CHECK: FX DATA FRESHNESS
# =============================================================================


class TestCheckFXData:
    def test_fx_data_pass(self, result, tmp_path):
        """All 4 parquets exist and are fresh -> pass."""
        fx_dir = tmp_path / "data" / "fx"
        fx_dir.mkdir(parents=True)
        for pair in ["AUDJPY", "USDJPY", "EURJPY", "NZDUSD"]:
            (fx_dir / f"{pair}_1D.parquet").write_text("fake")

        with patch("scripts.preflight_check.ROOT", tmp_path):
            _check_fx_data(result)

        assert result.checks["fx_data"]["passed"] is True
        assert "4 parquets < 72h" in result.checks["fx_data"]["message"]

    def test_fx_data_dir_missing(self, result, tmp_path):
        """data/fx/ does not exist -> fail."""
        with patch("scripts.preflight_check.ROOT", tmp_path):
            _check_fx_data(result)

        assert result.checks["fx_data"]["passed"] is False
        assert "n'existe pas" in result.checks["fx_data"]["message"]

    def test_fx_data_stale_parquet(self, result, tmp_path):
        """One parquet is > 48h old -> fail."""
        fx_dir = tmp_path / "data" / "fx"
        fx_dir.mkdir(parents=True)
        for pair in ["AUDJPY", "USDJPY", "EURJPY", "NZDUSD"]:
            fpath = fx_dir / f"{pair}_1D.parquet"
            fpath.write_text("fake")

        # Make AUDJPY stale (3 days old)
        stale_time = time.time() - 72 * 3600
        os.utime(str(fx_dir / "AUDJPY_1D.parquet"), (stale_time, stale_time))

        with patch("scripts.preflight_check.ROOT", tmp_path):
            _check_fx_data(result)

        assert result.checks["fx_data"]["passed"] is False
        assert "AUDJPY" in result.checks["fx_data"]["message"]

    def test_fx_data_missing_parquet(self, result, tmp_path):
        """One parquet absent -> fail."""
        fx_dir = tmp_path / "data" / "fx"
        fx_dir.mkdir(parents=True)
        for pair in ["AUDJPY", "USDJPY", "EURJPY"]:
            (fx_dir / f"{pair}_1D.parquet").write_text("fake")
        # NZDUSD is missing

        with patch("scripts.preflight_check.ROOT", tmp_path):
            _check_fx_data(result)

        assert result.checks["fx_data"]["passed"] is False
        assert "NZDUSD" in result.checks["fx_data"]["message"]
        assert "absent" in result.checks["fx_data"]["message"]


# =============================================================================
# CHECK: CRYPTO DATA
# =============================================================================


class TestCheckCryptoData:
    @patch.dict(os.environ, {"BINANCE_API_KEY": "test_key"})
    def test_crypto_data_pass(self, result):
        """Binance returns BTCUSDC bars -> pass."""
        mock_broker = MagicMock()
        mock_broker.get_prices.return_value = {
            "bars": [{"c": 65000}, {"c": 65100}, {"c": 64800}]
        }
        mock_module = MagicMock()
        mock_module.BinanceBroker.return_value = mock_broker
        with patch.dict("sys.modules", {"core.broker.binance_broker": mock_module}):
            _check_crypto_data(result)

        assert result.checks["crypto_data"]["passed"] is True
        assert "3 bars" in result.checks["crypto_data"]["message"]

    def test_crypto_data_no_api_key(self, result):
        """No BINANCE_API_KEY -> warning (non-blocking)."""
        env = os.environ.copy()
        env.pop("BINANCE_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            _check_crypto_data(result)
        assert result.checks["crypto_data"]["passed"] is False
        assert result.all_passed is True  # non-blocking

    @patch.dict(os.environ, {"BINANCE_API_KEY": "test_key"})
    def test_crypto_data_empty_bars(self, result):
        """Binance returns 0 bars -> fail."""
        mock_broker = MagicMock()
        mock_broker.get_prices.return_value = {"bars": []}
        mock_module = MagicMock()
        mock_module.BinanceBroker.return_value = mock_broker
        with patch.dict("sys.modules", {"core.broker.binance_broker": mock_module}):
            _check_crypto_data(result)

        assert result.checks["crypto_data"]["passed"] is False


# =============================================================================
# CHECK: EARN USDC
# =============================================================================


class TestCheckEarn:
    @patch.dict(os.environ, {"BINANCE_API_KEY": "test_key"})
    def test_earn_pass(self, result):
        """USDC present in Earn Flexible with amount > 0 -> pass."""
        mock_broker = MagicMock()
        mock_broker.get_earn_positions.return_value = [
            {"asset": "USDC", "amount": 3000},
            {"asset": "BTC", "amount": 0.01},
        ]
        mock_module = MagicMock()
        mock_module.BinanceBroker.return_value = mock_broker
        with patch.dict("sys.modules", {"core.broker.binance_broker": mock_module}):
            _check_earn(result)

        assert result.checks["earn_usdc"]["passed"] is True

    @patch.dict(os.environ, {"BINANCE_API_KEY": "test_key"})
    def test_earn_no_usdc(self, result):
        """No USDC in earn positions -> fail (non-blocking)."""
        mock_broker = MagicMock()
        mock_broker.get_earn_positions.return_value = [
            {"asset": "BTC", "amount": 0.01}
        ]
        mock_module = MagicMock()
        mock_module.BinanceBroker.return_value = mock_broker
        with patch.dict("sys.modules", {"core.broker.binance_broker": mock_module}):
            _check_earn(result)

        assert result.checks["earn_usdc"]["passed"] is False
        assert result.all_passed is True  # non-blocking

    @patch.dict(os.environ, {"BINANCE_API_KEY": "test_key"})
    def test_earn_zero_amount(self, result):
        """USDC present but amount=0 -> fail."""
        mock_broker = MagicMock()
        mock_broker.get_earn_positions.return_value = [
            {"asset": "USDC", "amount": 0}
        ]
        mock_module = MagicMock()
        mock_module.BinanceBroker.return_value = mock_broker
        with patch.dict("sys.modules", {"core.broker.binance_broker": mock_module}):
            _check_earn(result)

        assert result.checks["earn_usdc"]["passed"] is False

    def test_earn_no_api_key(self, result):
        """No BINANCE_API_KEY -> fail (non-blocking)."""
        env = os.environ.copy()
        env.pop("BINANCE_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            _check_earn(result)
        assert result.checks["earn_usdc"]["passed"] is False
        assert result.all_passed is True


# =============================================================================
# CHECK: MARGIN
# =============================================================================


class TestCheckMargin:
    @patch.dict(os.environ, {"BINANCE_API_KEY": "test_key"})
    def test_margin_pass(self, result):
        """At least 1 margin pair enabled including BTCUSDC -> pass."""
        mock_broker = MagicMock()
        mock_broker._get.return_value = {
            "assets": [
                {"symbol": "BTCUSDC", "enabled": True, "isolatedCreated": True},
                {"symbol": "ETHUSDC", "enabled": True, "isolatedCreated": True},
            ]
        }
        mock_module = MagicMock()
        mock_module.BinanceBroker.return_value = mock_broker
        with patch.dict("sys.modules", {"core.broker.binance_broker": mock_module}):
            _check_margin(result)

        assert result.checks["margin"]["passed"] is True
        assert "BTCUSDC" in result.checks["margin"]["message"]

    @patch.dict(os.environ, {"BINANCE_API_KEY": "test_key"})
    def test_margin_none_enabled(self, result):
        """No margin pairs enabled -> fail (non-blocking)."""
        mock_broker = MagicMock()
        mock_broker._get.return_value = {
            "assets": [
                {"symbol": "BTCUSDC", "enabled": False, "isolatedCreated": False},
            ]
        }
        mock_module = MagicMock()
        mock_module.BinanceBroker.return_value = mock_broker
        with patch.dict("sys.modules", {"core.broker.binance_broker": mock_module}):
            _check_margin(result)

        assert result.checks["margin"]["passed"] is False
        assert result.all_passed is True  # non-blocking

    @patch.dict(os.environ, {"BINANCE_API_KEY": "test_key"})
    def test_margin_exception(self, result):
        """Margin API error -> fail (non-blocking)."""
        mock_broker = MagicMock()
        mock_broker._get.side_effect = Exception("Margin not available")
        mock_module = MagicMock()
        mock_module.BinanceBroker.return_value = mock_broker
        with patch.dict("sys.modules", {"core.broker.binance_broker": mock_module}):
            _check_margin(result)

        assert result.checks["margin"]["passed"] is False
        assert "ERREUR" in result.checks["margin"]["message"]

    def test_margin_no_api_key(self, result):
        """No BINANCE_API_KEY -> fail (non-blocking)."""
        env = os.environ.copy()
        env.pop("BINANCE_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            _check_margin(result)
        assert result.checks["margin"]["passed"] is False
        assert result.all_passed is True


# =============================================================================
# CHECK: KILL SWITCH
# =============================================================================


class TestCheckKillSwitch:
    def test_kill_switch_no_file(self, result, tmp_path):
        """No kill switch state file -> pass (inactive)."""
        with patch("scripts.preflight_check.ROOT", tmp_path):
            _check_kill_switch(result)

        assert result.checks["kill_switch"]["passed"] is True
        assert "inactif" in result.checks["kill_switch"]["message"].lower()

    def test_kill_switch_inactive(self, result, tmp_path):
        """Kill switch file exists, active=False -> pass."""
        ks_dir = tmp_path / "data"
        ks_dir.mkdir(parents=True)
        ks_file = ks_dir / "crypto_kill_switch_state.json"
        ks_file.write_text(json.dumps({"active": False, "reason": ""}))

        with patch("scripts.preflight_check.ROOT", tmp_path):
            _check_kill_switch(result)

        assert result.checks["kill_switch"]["passed"] is True
        assert "Inactif" in result.checks["kill_switch"]["message"]

    def test_kill_switch_active(self, result, tmp_path):
        """Kill switch file exists, active=True -> fail (non-blocking warning)."""
        ks_dir = tmp_path / "data"
        ks_dir.mkdir(parents=True)
        ks_file = ks_dir / "crypto_kill_switch_state.json"
        ks_file.write_text(json.dumps({
            "active": True,
            "reason": "Daily drawdown exceeded"
        }))

        with patch("scripts.preflight_check.ROOT", tmp_path):
            _check_kill_switch(result)

        assert result.checks["kill_switch"]["passed"] is False
        assert "ACTIF" in result.checks["kill_switch"]["message"]
        assert "Daily drawdown" in result.checks["kill_switch"]["message"]

    def test_kill_switch_corrupt_file(self, result, tmp_path):
        """Kill switch file corrupt JSON -> fail (non-blocking)."""
        ks_dir = tmp_path / "data"
        ks_dir.mkdir(parents=True)
        ks_file = ks_dir / "crypto_kill_switch_state.json"
        ks_file.write_text("{not valid json")

        with patch("scripts.preflight_check.ROOT", tmp_path):
            _check_kill_switch(result)

        assert result.checks["kill_switch"]["passed"] is False
        assert "Erreur lecture" in result.checks["kill_switch"]["message"]


# =============================================================================
# CHECK: DISK SPACE
# =============================================================================


class TestCheckDisk:
    def test_disk_pass(self, result):
        """Disk has > 1GB free -> pass."""
        DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])
        mock_usage = DiskUsage(
            total=100 * 1024 ** 3,
            used=50 * 1024 ** 3,
            free=50 * 1024 ** 3,  # 50 GB free
        )
        with patch("shutil.disk_usage", return_value=mock_usage):
            _check_disk(result)

        assert result.checks["disk_space"]["passed"] is True
        assert "50.0 GB" in result.checks["disk_space"]["message"]

    def test_disk_fail_low_space(self, result):
        """Disk has < 1GB free -> fail."""
        DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])
        mock_usage = DiskUsage(
            total=100 * 1024 ** 3,
            used=99.5 * 1024 ** 3,
            free=0.5 * 1024 ** 3,  # 0.5 GB free
        )
        with patch("shutil.disk_usage", return_value=mock_usage):
            _check_disk(result)

        assert result.checks["disk_space"]["passed"] is False
        assert "< 1GB" in result.checks["disk_space"]["message"]

    def test_disk_exception(self, result):
        """disk_usage raises -> fail (non-blocking)."""
        with patch("shutil.disk_usage", side_effect=OSError("permission denied")):
            _check_disk(result)

        assert result.checks["disk_space"]["passed"] is False
        assert "ERREUR" in result.checks["disk_space"]["message"]


# =============================================================================
# CHECK: IB GATEWAY
# =============================================================================


class TestCheckIBGateway:
    @patch.dict(os.environ, {"IBKR_HOST": "178.104.125.74", "IBKR_PORT": "4002"})
    @patch("socket.create_connection")
    def test_ibgateway_pass(self, mock_conn, result):
        """Port 4002 listens -> pass."""
        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        _check_ibgateway(result)
        assert result.checks["ibgateway"]["passed"] is True
        assert "178.104.125.74:4002 OK" in result.checks["ibgateway"]["message"]

    @patch.dict(os.environ, {"IBKR_HOST": "178.104.125.74", "IBKR_PORT": "4002"})
    @patch("socket.create_connection", side_effect=OSError("timeout"))
    def test_ibgateway_fail(self, mock_conn, result):
        """Port 4002 not listening -> blocker."""
        _check_ibgateway(result)
        assert result.checks["ibgateway"]["passed"] is False
        assert "not listening" in result.checks["ibgateway"]["message"]
        assert result.all_passed is False


# =============================================================================
# CHECK: TELEGRAM
# =============================================================================


class TestCheckTelegram:
    def test_telegram_pass(self, result):
        """send_alert returns True -> pass."""
        mock_module = MagicMock()
        mock_module.send_alert.return_value = True
        with patch.dict("sys.modules", {"core.telegram_alert": mock_module}):
            _check_telegram(result)

        assert result.checks["telegram"]["passed"] is True
        assert "Bot OK" in result.checks["telegram"]["message"]

    def test_telegram_fail_false(self, result):
        """send_alert returns False -> fail (non-blocking)."""
        mock_module = MagicMock()
        mock_module.send_alert.return_value = False
        with patch.dict("sys.modules", {"core.telegram_alert": mock_module}):
            _check_telegram(result)

        assert result.checks["telegram"]["passed"] is False
        assert result.all_passed is True  # non-blocking

    def test_telegram_exception(self, result):
        """send_alert raises -> fail (non-blocking)."""
        mock_module = MagicMock()
        mock_module.send_alert.side_effect = Exception("network error")
        with patch.dict("sys.modules", {"core.telegram_alert": mock_module}):
            _check_telegram(result)

        assert result.checks["telegram"]["passed"] is False
        assert "ERREUR" in result.checks["telegram"]["message"]
        assert result.all_passed is True  # non-blocking


# =============================================================================
# PERSIST RESULT
# =============================================================================


class TestPersistResult:
    def test_persist_writes_json(self, result, tmp_path):
        """Persist creates preflight.json with correct structure."""
        result.add("test_check", True, "ok")
        result.add("test_fail", False, "broken", blocking=True)

        with patch("scripts.preflight_check.ROOT", tmp_path):
            _persist_result(result)

        out_path = tmp_path / "data" / "monitoring" / "preflight.json"
        assert out_path.exists()
        data = json.loads(out_path.read_text())
        assert "timestamp" in data
        assert data["all_passed"] is False
        assert "test_check" in data["checks"]
        assert len(data["blockers"]) == 1

    def test_persist_creates_directory(self, result, tmp_path):
        """Persist creates monitoring directory if it doesn't exist."""
        result.add("a", True, "ok")
        with patch("scripts.preflight_check.ROOT", tmp_path):
            _persist_result(result)

        assert (tmp_path / "data" / "monitoring").is_dir()


# =============================================================================
# INTEGRATION: run_preflight
# =============================================================================


class TestRunPreflight:
    @patch("scripts.preflight_check._persist_result")
    @patch("scripts.preflight_check._check_telegram")
    @patch("scripts.preflight_check._check_ibgateway")
    @patch("scripts.preflight_check._check_disk")
    @patch("scripts.preflight_check._check_kill_switch")
    @patch("scripts.preflight_check._check_margin")
    @patch("scripts.preflight_check._check_earn")
    @patch("scripts.preflight_check._check_crypto_data")
    @patch("scripts.preflight_check._check_fx_data")
    @patch("scripts.preflight_check._check_ibkr_paper")
    @patch("scripts.preflight_check._check_ibkr_live")
    @patch("scripts.preflight_check._check_binance")
    def test_run_preflight_calls_all_checks(
        self, mock_binance, mock_ibkr_live, mock_ibkr_paper, mock_fx,
        mock_crypto, mock_earn, mock_margin, mock_ks, mock_disk,
        mock_ibgateway, mock_telegram, mock_persist
    ):
        """run_preflight calls all 10 active check functions + persist (FX disabled)."""
        result = run_preflight(block_on_failure=False)

        mock_binance.assert_called_once()
        mock_ibkr_live.assert_called_once()
        mock_ibkr_paper.assert_called_once()
        mock_fx.assert_not_called()  # FX check disabled (IBIE France interdit levier FX retail)
        mock_crypto.assert_called_once()
        mock_earn.assert_called_once()
        mock_margin.assert_called_once()
        mock_ks.assert_called_once()
        mock_disk.assert_called_once()
        mock_ibgateway.assert_called_once()
        mock_telegram.assert_called_once()
        mock_persist.assert_called_once()
        assert isinstance(result, PreflightResult)

    @patch("scripts.preflight_check._persist_result")
    @patch("scripts.preflight_check._check_telegram")
    @patch("scripts.preflight_check._check_ibgateway")
    @patch("scripts.preflight_check._check_disk")
    @patch("scripts.preflight_check._check_kill_switch")
    @patch("scripts.preflight_check._check_margin")
    @patch("scripts.preflight_check._check_earn")
    @patch("scripts.preflight_check._check_crypto_data")
    @patch("scripts.preflight_check._check_fx_data")
    @patch("scripts.preflight_check._check_ibkr_paper")
    @patch("scripts.preflight_check._check_ibkr_live")
    @patch("scripts.preflight_check._check_binance")
    def test_run_preflight_returns_result_with_no_blockers(
        self, mock_binance, mock_ibkr_live, mock_ibkr_paper, mock_fx,
        mock_crypto, mock_earn, mock_margin, mock_ks, mock_disk,
        mock_ibgateway, mock_telegram, mock_persist
    ):
        """When all checks are mocked to do nothing, result has no blockers."""
        result = run_preflight()
        assert result.all_passed is True
        assert result.blockers == []


# =============================================================================
# EDGE CASES
# =============================================================================


class TestEdgeCases:
    @patch.dict(os.environ, {"IBKR_HOST": "127.0.0.1", "IBKR_PORT": "4002"})
    @patch("socket.create_connection")
    def test_ibkr_live_equity_exactly_5000(self, mock_conn, result):
        """Equity exactly $5000 -> pass (>= threshold)."""
        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        mock_ibkr = MagicMock()
        mock_ibkr.get_account_info.return_value = {"equity": 5000}
        mock_module = MagicMock()
        mock_module.IBKRBroker.return_value = mock_ibkr
        with patch.dict("sys.modules", {"core.broker.ibkr_adapter": mock_module}):
            _check_ibkr_live(result)

        assert result.checks["ibkr_live_equity"]["passed"] is True

    @patch.dict(os.environ, {"BINANCE_API_KEY": "test_key"})
    def test_binance_cash_zero_but_equity_positive(self, result):
        """cash=0 but equity=500 -> pass (cash > 0 OR equity > 0)."""
        mock_broker = MagicMock()
        mock_broker.authenticate.return_value = {"permissions": ["SPOT"]}
        mock_broker.get_account_info.return_value = {"cash": 0, "equity": 500}
        mock_broker.get_positions.return_value = []
        mock_module = MagicMock()
        mock_module.BinanceBroker.return_value = mock_broker
        with patch.dict("sys.modules", {"core.broker.binance_broker": mock_module}):
            _check_binance(result)

        assert result.checks["binance_cash"]["passed"] is True

    def test_disk_exactly_1gb(self, result):
        """Exactly 1.0 GB free -> fail (> 1.0, not >=)."""
        DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])
        mock_usage = DiskUsage(
            total=100 * 1024 ** 3,
            used=99 * 1024 ** 3,
            free=1.0 * 1024 ** 3,
        )
        with patch("shutil.disk_usage", return_value=mock_usage):
            _check_disk(result)

        # The code checks free_gb > 1.0, so exactly 1.0 fails
        # Due to floating point, 1.0 * 1024**3 / 1024**3 = 1.0, not > 1.0
        assert result.checks["disk_space"]["passed"] is False

    @patch.dict(os.environ, {"IBKR_HOST": "127.0.0.1", "IBKR_PORT": "4002"})
    @patch("socket.create_connection")
    def test_ibkr_broker_disconnect_called(self, mock_conn, result):
        """IBKR broker.disconnect() is always called (finally block)."""
        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        mock_ibkr = MagicMock()
        mock_ibkr.get_account_info.return_value = {"equity": 10000}
        mock_module = MagicMock()
        mock_module.IBKRBroker.return_value = mock_ibkr
        with patch.dict("sys.modules", {"core.broker.ibkr_adapter": mock_module}):
            _check_ibkr_live(result)

        mock_ibkr.disconnect.assert_called_once()

    @patch.dict(os.environ, {"BINANCE_API_KEY": "test_key"})
    def test_margin_btcusdt_also_accepted(self, result):
        """BTCUSDT (not only BTCUSDC) is accepted as valid margin pair."""
        mock_broker = MagicMock()
        mock_broker._get.return_value = {
            "assets": [
                {"symbol": "BTCUSDT", "enabled": True, "isolatedCreated": True},
            ]
        }
        mock_module = MagicMock()
        mock_module.BinanceBroker.return_value = mock_broker
        with patch.dict("sys.modules", {"core.broker.binance_broker": mock_module}):
            _check_margin(result)

        assert result.checks["margin"]["passed"] is True
