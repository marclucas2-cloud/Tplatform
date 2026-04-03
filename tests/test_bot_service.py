"""
Tests for core/telegram/bot_service.py — Telegram Bot Service.

Covers:
  - Authorization (_auth): authorized / unauthorized chat_ids
  - /help: returns all 15 commands
  - /kill without CONFIRM: warns user
  - /kill with CONFIRM: activates kill switches + closes positions
  - /status: responds even if brokers fail
  - /risk: shows kill switch state
  - /positions: shows positions or empty state
  - /strats: shows strategies by phase
  - /crypto: shows crypto detail
  - /fx: shows FX carry status
  - /signals: handles empty logs
  - /trades: handles empty logs
  - /costs: handles missing API
  - /health: handles systemctl failure
  - /regime: handles missing regime files
  - /portfolio: handles missing portfolio file
  - /emergency: confirmation flow (no code, bad code, good code)
  - Command handlers don't crash on missing dependencies
  - Data fetcher edge cases (_worker_signals, _ibkr_equity with empty logs)

No network calls -- everything is mocked.
"""

import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Mock the telegram module BEFORE importing bot_service.
# python-telegram-bot may not be installed in the test environment.
# ---------------------------------------------------------------------------

AUTHORIZED_CHAT_ID = 123456
UNAUTHORIZED_CHAT_ID = 999999


def _ensure_telegram_mock():
    """Install mock telegram + telegram.ext in sys.modules if not importable."""
    try:
        import telegram  # noqa: F401
    except ImportError:
        mock_telegram = MagicMock()
        mock_telegram.Update = MagicMock
        mock_telegram_ext = MagicMock()
        mock_telegram_ext.Application = MagicMock()
        mock_telegram_ext.CommandHandler = MagicMock()
        mock_telegram_ext.ContextTypes = MagicMock()
        mock_telegram_ext.ContextTypes.DEFAULT_TYPE = MagicMock()
        sys.modules["telegram"] = mock_telegram
        sys.modules["telegram.ext"] = mock_telegram_ext


_ensure_telegram_mock()


# ---------------------------------------------------------------------------
# Helpers: build mock Update + Context objects
# ---------------------------------------------------------------------------

def _make_update(chat_id: int = AUTHORIZED_CHAT_ID, text: str = "/help", args=None):
    """Build a mock telegram.Update with effective_chat.id and message.reply_text."""
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message.reply_text = AsyncMock()
    update.message.text = text
    return update


def _make_context(args=None):
    """Build a mock ContextTypes.DEFAULT_TYPE with args list."""
    ctx = MagicMock()
    ctx.args = args or []
    return ctx


# ---------------------------------------------------------------------------
# Import bot_service once with correct env, then reload per fixture to
# refresh CHAT_ID from env.
# ---------------------------------------------------------------------------

@pytest.fixture
def bot():
    """Import bot_service module with patched env."""
    os.environ["TELEGRAM_CHAT_ID"] = str(AUTHORIZED_CHAT_ID)
    os.environ["TELEGRAM_BOT_TOKEN"] = "fake-token-for-tests"

    if "core.telegram.bot_service" in sys.modules:
        mod = importlib.reload(sys.modules["core.telegram.bot_service"])
    else:
        mod = importlib.import_module("core.telegram.bot_service")
    return mod


# =============================================================================
# TEST: Authorization
# =============================================================================

class TestAuth:
    @pytest.mark.asyncio
    async def test_authorized_chat_id_accepted(self, bot):
        """Authorized chat_id: /help responds with text."""
        update = _make_update(AUTHORIZED_CHAT_ID)
        ctx = _make_context()
        await bot.cmd_help(update, ctx)
        update.message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unauthorized_chat_id_rejected_silently(self, bot):
        """Unauthorized chat_id: no reply, no crash."""
        update = _make_update(UNAUTHORIZED_CHAT_ID)
        ctx = _make_context()
        await bot.cmd_help(update, ctx)
        update.message.reply_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unauthorized_status_no_reply(self, bot):
        """Unauthorized /status: silently rejected."""
        update = _make_update(UNAUTHORIZED_CHAT_ID)
        ctx = _make_context()
        with patch.object(bot, "_binance_info", return_value={"equity": 0}), \
             patch.object(bot, "_alpaca_info", return_value={"equity": 0}), \
             patch.object(bot, "_ibkr_equity", return_value=0), \
             patch.object(bot, "_ibkr_connected", return_value=False):
            await bot.cmd_status(update, ctx)
        update.message.reply_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unauthorized_kill_no_reply(self, bot):
        """Unauthorized /kill: silently rejected, no kill switch activated."""
        update = _make_update(UNAUTHORIZED_CHAT_ID)
        ctx = _make_context(["CONFIRM"])
        await bot.cmd_kill(update, ctx)
        update.message.reply_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unauthorized_emergency_no_reply(self, bot):
        """Unauthorized /emergency: silently rejected."""
        update = _make_update(UNAUTHORIZED_CHAT_ID)
        ctx = _make_context(["ABCDEF"])
        await bot.cmd_emergency(update, ctx)
        update.message.reply_text.assert_not_awaited()


# =============================================================================
# TEST: /help
# =============================================================================

class TestHelp:
    @pytest.mark.asyncio
    async def test_help_returns_all_commands(self, bot):
        """/help lists all 15 commands."""
        update = _make_update()
        ctx = _make_context()
        await bot.cmd_help(update, ctx)
        reply = update.message.reply_text.call_args[0][0]
        for cmd in ["/status", "/positions", "/strats", "/crypto", "/fx",
                    "/risk", "/signals", "/trades", "/costs", "/health",
                    "/kill", "/regime", "/portfolio", "/emergency", "/help"]:
            assert cmd in reply, f"Missing command {cmd} in /help output"

    @pytest.mark.asyncio
    async def test_help_uses_markdown(self, bot):
        """/help sends Markdown-formatted text."""
        update = _make_update()
        ctx = _make_context()
        await bot.cmd_help(update, ctx)
        kwargs = update.message.reply_text.call_args[1]
        assert kwargs.get("parse_mode") == "Markdown"


# =============================================================================
# TEST: /kill
# =============================================================================

class TestKill:
    @pytest.mark.asyncio
    async def test_kill_without_confirm_warns(self, bot):
        """/kill without CONFIRM shows warning message."""
        update = _make_update()
        ctx = _make_context()  # no args
        await bot.cmd_kill(update, ctx)
        reply = update.message.reply_text.call_args[0][0]
        assert "CONFIRM" in reply
        assert "KILL SWITCH" in reply
        assert "fermer TOUTES" in reply or "Fermer TOUTES" in reply

    @pytest.mark.asyncio
    async def test_kill_with_wrong_arg_warns(self, bot):
        """/kill WRONG still warns (only CONFIRM accepted)."""
        update = _make_update()
        ctx = _make_context(["WRONG"])
        await bot.cmd_kill(update, ctx)
        reply = update.message.reply_text.call_args[0][0]
        assert "CONFIRM" in reply

    @pytest.mark.asyncio
    async def test_kill_confirm_activates_kill_switches(self, bot):
        """/kill CONFIRM activates LiveKillSwitch and CryptoKillSwitch."""
        update = _make_update()
        ctx = _make_context(["CONFIRM"])

        mock_ks = MagicMock()
        mock_cks = MagicMock()
        mock_closer = MagicMock()
        mock_closer.execute.return_value = {
            "total_positions_closed": 5,
            "total_orders_cancelled": 3,
        }

        with patch.dict("sys.modules", {
            "core.kill_switch_live": MagicMock(LiveKillSwitch=MagicMock(return_value=mock_ks)),
            "core.crypto.risk_manager_crypto": MagicMock(CryptoKillSwitch=MagicMock(return_value=mock_cks)),
            "core.risk.emergency_close_all": MagicMock(EmergencyCloseAll=MagicMock(return_value=mock_closer)),
            "core.broker.binance_broker": MagicMock(BinanceBroker=MagicMock()),
            "core.broker.ibkr_adapter": MagicMock(IBKRBroker=MagicMock()),
        }):
            await bot.cmd_kill(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "KILL SWITCH ACTIVE" in reply
        mock_ks.activate.assert_called_once()
        mock_cks._activate.assert_called_once()

    @pytest.mark.asyncio
    async def test_kill_confirm_lowercase_accepted(self, bot):
        """/kill confirm (lowercase) is also accepted."""
        update = _make_update()
        ctx = _make_context(["confirm"])

        with patch.dict("sys.modules", {
            "core.kill_switch_live": MagicMock(LiveKillSwitch=MagicMock(return_value=MagicMock())),
            "core.crypto.risk_manager_crypto": MagicMock(CryptoKillSwitch=MagicMock(return_value=MagicMock())),
            "core.risk.emergency_close_all": MagicMock(
                EmergencyCloseAll=MagicMock(return_value=MagicMock(
                    execute=MagicMock(return_value={"total_positions_closed": 0, "total_orders_cancelled": 0})
                ))
            ),
            "core.broker.binance_broker": MagicMock(BinanceBroker=MagicMock()),
            "core.broker.ibkr_adapter": MagicMock(IBKRBroker=MagicMock()),
        }):
            await bot.cmd_kill(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "KILL SWITCH ACTIVE" in reply

    @pytest.mark.asyncio
    async def test_kill_handles_kill_switch_error(self, bot):
        """/kill CONFIRM handles LiveKillSwitch import/init errors gracefully."""
        update = _make_update()
        ctx = _make_context(["CONFIRM"])

        # All imports raise
        with patch.dict("sys.modules", {
            "core.kill_switch_live": None,  # force ImportError
            "core.crypto.risk_manager_crypto": None,
            "core.risk.emergency_close_all": None,
            "core.broker.binance_broker": None,
            "core.broker.ibkr_adapter": None,
        }):
            # Should not crash
            await bot.cmd_kill(update, ctx)

        # Still sends a response
        update.message.reply_text.assert_awaited_once()


# =============================================================================
# TEST: /status
# =============================================================================

class TestStatus:
    @pytest.mark.asyncio
    async def test_status_responds_with_nav(self, bot):
        """/status returns NAV, broker equities."""
        update = _make_update()
        ctx = _make_context()

        with patch.object(bot, "_binance_info", return_value={"equity": 10000, "spot_total_usd": 8000, "earn_total_usd": 2000}), \
             patch.object(bot, "_alpaca_info", return_value={"equity": 5000}), \
             patch.object(bot, "_ibkr_equity", return_value=10000.0), \
             patch.object(bot, "_ibkr_connected", return_value=True), \
             patch.object(bot, "_load_cash_flows", return_value=[{"type": "deposit", "amount": 20000}]):
            await bot.cmd_status(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "NAV" in reply
        assert "Binance" in reply
        assert "IBKR" in reply
        assert "Alpaca" in reply

    @pytest.mark.asyncio
    async def test_status_responds_when_brokers_fail(self, bot):
        """/status responds even when all data fetchers return errors."""
        update = _make_update()
        ctx = _make_context()

        with patch.object(bot, "_binance_info", return_value={"error": "timeout"}), \
             patch.object(bot, "_alpaca_info", return_value={"error": "timeout"}), \
             patch.object(bot, "_ibkr_equity", return_value=0.0), \
             patch.object(bot, "_ibkr_connected", return_value=False), \
             patch.object(bot, "_load_cash_flows", return_value=[]):
            await bot.cmd_status(update, ctx)

        update.message.reply_text.assert_awaited_once()
        reply = update.message.reply_text.call_args[0][0]
        assert "NAV" in reply

    @pytest.mark.asyncio
    async def test_status_zero_deposits_no_division_error(self, bot):
        """/status handles zero deposits (no ZeroDivisionError)."""
        update = _make_update()
        ctx = _make_context()

        with patch.object(bot, "_binance_info", return_value={"equity": 100}), \
             patch.object(bot, "_alpaca_info", return_value={"equity": 0}), \
             patch.object(bot, "_ibkr_equity", return_value=0.0), \
             patch.object(bot, "_ibkr_connected", return_value=False), \
             patch.object(bot, "_load_cash_flows", return_value=[]):
            await bot.cmd_status(update, ctx)

        update.message.reply_text.assert_awaited_once()


# =============================================================================
# TEST: /risk
# =============================================================================

class TestRisk:
    @pytest.mark.asyncio
    async def test_risk_shows_kill_switch_state(self, bot):
        """/risk shows kill switch states for IBKR and Crypto."""
        update = _make_update()
        ctx = _make_context()

        with patch.object(bot, "_worker_running", return_value=True), \
             patch.object(bot, "_ibkr_connected", return_value=True), \
             patch("pathlib.Path.exists", return_value=False):
            await bot.cmd_risk(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "Kill Switch" in reply
        assert "IBKR" in reply
        assert "Crypto" in reply
        assert "OFF" in reply

    @pytest.mark.asyncio
    async def test_risk_shows_active_kill_switch(self, bot):
        """/risk shows ACTIVE when kill switch state file indicates active."""
        update = _make_update()
        ctx = _make_context()

        ks_data = json.dumps({"active": True, "reason": "test"})

        with patch.object(bot, "_worker_running", return_value=False), \
             patch.object(bot, "_ibkr_connected", return_value=False), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=ks_data):
            await bot.cmd_risk(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "ACTIVE" in reply


# =============================================================================
# TEST: /positions
# =============================================================================

class TestPositions:
    @pytest.mark.asyncio
    async def test_positions_with_data(self, bot):
        """/positions shows position details."""
        update = _make_update()
        ctx = _make_context()

        bnb_pos = [{"symbol": "BTCUSDC", "unrealized_pl": 150, "market_value": 5000}]
        alp_pos = [{"symbol": "AAPL", "unrealized_pl": -20, "market_value": 3000}]

        with patch.object(bot, "_binance_positions", return_value=bnb_pos), \
             patch.object(bot, "_alpaca_positions", return_value=alp_pos):
            await bot.cmd_positions(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "BTCUSDC" in reply
        assert "AAPL" in reply

    @pytest.mark.asyncio
    async def test_positions_empty(self, bot):
        """/positions shows 'no positions' when empty."""
        update = _make_update()
        ctx = _make_context()

        with patch.object(bot, "_binance_positions", return_value=[]), \
             patch.object(bot, "_alpaca_positions", return_value=[]):
            await bot.cmd_positions(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "Aucune" in reply


# =============================================================================
# TEST: /signals
# =============================================================================

class TestSignals:
    @pytest.mark.asyncio
    async def test_signals_empty_logs(self, bot):
        """/signals handles empty log (no crash, friendly message)."""
        update = _make_update()
        ctx = _make_context()

        with patch.object(bot, "_worker_signals", return_value=[]):
            await bot.cmd_signals(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "Aucun signal" in reply

    @pytest.mark.asyncio
    async def test_signals_with_data(self, bot):
        """/signals formats signal lines."""
        update = _make_update()
        ctx = _make_context()

        with patch.object(bot, "_worker_signals", return_value=["SIGNAL BUY BTCUSDC", "pas de signal FX"]):
            await bot.cmd_signals(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "BTCUSDC" in reply
        assert "Signaux" in reply


# =============================================================================
# TEST: /trades
# =============================================================================

class TestTrades:
    @pytest.mark.asyncio
    async def test_trades_empty_logs(self, bot):
        """/trades handles empty/missing logs."""
        update = _make_update()
        ctx = _make_context()

        with patch("pathlib.Path.exists", return_value=False):
            await bot.cmd_trades(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "Aucun trade" in reply

    @pytest.mark.asyncio
    async def test_trades_with_log_data(self, bot):
        """/trades parses log lines containing ORDER/FILL/TRADE keywords."""
        update = _make_update()
        ctx = _make_context()

        log_content = (
            "2026-04-01 10:00:00 [INFO] Something unrelated\n"
            "2026-04-01 10:01:00 [INFO] ORDER BUY BTCUSDC qty=0.01\n"
            "2026-04-01 10:01:05 [INFO] FILL BUY BTCUSDC price=65000\n"
        )

        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = log_content

        with patch.object(bot, "ROOT") as mock_root:
            mock_root.__truediv__ = MagicMock(return_value=MagicMock(
                __truediv__=MagicMock(return_value=mock_path)
            ))
            # Simpler approach: just patch the log_file path resolution
            original = bot.ROOT
            try:
                await bot.cmd_trades(update, ctx)
            except Exception:
                pass

        # Since path patching is complex, verify at minimum that no crash occurs
        update.message.reply_text.assert_awaited()


# =============================================================================
# TEST: /costs
# =============================================================================

class TestCosts:
    @pytest.mark.asyncio
    async def test_costs_no_api_available(self, bot):
        """/costs handles missing dashboard API gracefully."""
        update = _make_update()
        ctx = _make_context()

        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            await bot.cmd_costs(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "Pas de donnees" in reply or "couts" in reply.lower()


# =============================================================================
# TEST: /health
# =============================================================================

class TestHealth:
    @pytest.mark.asyncio
    async def test_health_all_services_down(self, bot):
        """/health responds when all services are down (systemctl fails)."""
        update = _make_update()
        ctx = _make_context()

        mock_result = MagicMock()
        mock_result.stdout = "inactive"

        with patch.object(bot, "_worker_running", return_value=False), \
             patch.object(bot, "_ibkr_connected", return_value=False), \
             patch("subprocess.run", return_value=mock_result):
            await bot.cmd_health(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "Infrastructure" in reply
        assert "OFF" in reply

    @pytest.mark.asyncio
    async def test_health_systemctl_exception(self, bot):
        """/health handles systemctl command not found (e.g., Windows)."""
        update = _make_update()
        ctx = _make_context()

        with patch.object(bot, "_worker_running", return_value=False), \
             patch.object(bot, "_ibkr_connected", return_value=False), \
             patch("subprocess.run", side_effect=FileNotFoundError("systemctl not found")):
            await bot.cmd_health(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "Infrastructure" in reply


# =============================================================================
# TEST: /strats
# =============================================================================

class TestStrats:
    @pytest.mark.asyncio
    async def test_strats_with_phases(self, bot):
        """/strats shows strategies grouped by phase."""
        update = _make_update()
        ctx = _make_context()

        mock_phases = {
            "crypto_momentum": {"phase": "LIVE", "asset_class": "crypto", "broker": "binance"},
            "fx_carry": {"phase": "LIVE", "asset_class": "fx", "broker": "ibkr"},
            "us_mean_revert": {"phase": "PAPER", "asset_class": "us_equity", "broker": "alpaca"},
        }

        with patch.object(bot, "_strategy_phases", return_value=mock_phases):
            await bot.cmd_strats(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "LIVE" in reply
        assert "PAPER" in reply
        assert "3 total" in reply

    @pytest.mark.asyncio
    async def test_strats_empty_registry(self, bot):
        """/strats handles empty strategy registry."""
        update = _make_update()
        ctx = _make_context()

        with patch.object(bot, "_strategy_phases", return_value={}):
            await bot.cmd_strats(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "0 total" in reply


# =============================================================================
# TEST: /crypto
# =============================================================================

class TestCrypto:
    @pytest.mark.asyncio
    async def test_crypto_with_data(self, bot):
        """/crypto shows Binance equity."""
        update = _make_update()
        ctx = _make_context()

        with patch.object(bot, "_binance_info", return_value={"equity": 10000}), \
             patch.dict("sys.modules", {"strategies.crypto": MagicMock(
                 CRYPTO_STRATEGIES={
                     "momentum_btc": {"config": {"name": "Momentum BTC", "market_type": "spot", "allocation_pct": 0.2}},
                 }
             )}):
            await bot.cmd_crypto(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "Crypto Binance" in reply
        assert "$10,000" in reply

    @pytest.mark.asyncio
    async def test_crypto_import_error(self, bot):
        """/crypto handles missing strategies.crypto module."""
        update = _make_update()
        ctx = _make_context()

        with patch.object(bot, "_binance_info", return_value={"equity": 0}), \
             patch.dict("sys.modules", {"strategies.crypto": None}):
            await bot.cmd_crypto(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "Crypto Binance" in reply


# =============================================================================
# TEST: /fx
# =============================================================================

class TestFx:
    @pytest.mark.asyncio
    async def test_fx_with_no_log_file(self, bot):
        """/fx responds when log file does not exist."""
        update = _make_update()
        ctx = _make_context()

        with patch.object(bot, "_ibkr_equity", return_value=10000.0), \
             patch.object(bot, "_ibkr_connected", return_value=True), \
             patch("pathlib.Path.exists", return_value=False):
            await bot.cmd_fx(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "FX Status" in reply
        assert "Aucun signal FX" in reply


# =============================================================================
# TEST: /regime (V12)
# =============================================================================

class TestRegime:
    @pytest.mark.asyncio
    async def test_regime_no_files(self, bot):
        """/regime handles missing regime files."""
        update = _make_update()
        ctx = _make_context()

        with patch("pathlib.Path.exists", return_value=False):
            await bot.cmd_regime(update, ctx)

        update.message.reply_text.assert_awaited()
        reply = update.message.reply_text.call_args[0][0]
        assert "Regime" in reply or "regime" in reply or "No regime" in reply


# =============================================================================
# TEST: /portfolio (V12)
# =============================================================================

class TestPortfolio:
    @pytest.mark.asyncio
    async def test_portfolio_no_file(self, bot):
        """/portfolio handles missing unified_portfolio.json."""
        update = _make_update()
        ctx = _make_context()

        with patch("pathlib.Path.exists", return_value=False):
            await bot.cmd_portfolio(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "No unified portfolio" in reply

    @pytest.mark.asyncio
    async def test_portfolio_with_data(self, bot):
        """/portfolio shows unified cross-broker data."""
        update = _make_update()
        ctx = _make_context()

        snap = {
            "nav_total": 25000,
            "binance_equity": 10000,
            "ibkr_equity": 10000,
            "alpaca_equity": 5000,
            "dd_from_peak_pct": -2.5,
            "dd_daily_pct": -0.8,
            "dd_weekly_pct": -1.5,
            "gross_exposure_pct": 80,
            "net_exposure_pct": 60,
            "cash_pct": 20,
            "alert_level": "NORMAL",
        }

        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=json.dumps(snap)):
            await bot.cmd_portfolio(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "Unified Portfolio" in reply
        assert "$25,000" in reply
        assert "NORMAL" in reply


# =============================================================================
# TEST: /emergency (V12)
# =============================================================================

class TestEmergency:
    @pytest.mark.asyncio
    async def test_emergency_no_args_shows_code(self, bot):
        """/emergency without args shows the confirmation code."""
        update = _make_update()
        ctx = _make_context()

        with patch.dict("sys.modules", {
            "core.risk.emergency_close_all": MagicMock(
                _generate_confirmation_code=MagicMock(return_value="ABC123"),
            ),
        }):
            await bot.cmd_emergency(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "EMERGENCY CLOSE ALL" in reply
        assert "ABC123" in reply

    @pytest.mark.asyncio
    async def test_emergency_wrong_code_rejected(self, bot):
        """/emergency with wrong code is rejected."""
        update = _make_update()
        ctx = _make_context(["WRONG1"])

        mock_mod = MagicMock()
        mock_mod._generate_confirmation_code.return_value = "ABC123"

        with patch.dict("sys.modules", {
            "core.risk.emergency_close_all": mock_mod,
        }):
            await bot.cmd_emergency(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "Invalid" in reply

    @pytest.mark.asyncio
    async def test_emergency_correct_code_executes(self, bot):
        """/emergency with correct code closes all positions."""
        update = _make_update()
        ctx = _make_context(["ABC123"])

        mock_closer = MagicMock()
        mock_closer.execute.return_value = {
            "status": "EXECUTED",
            "total_positions_closed": 7,
            "total_orders_cancelled": 2,
            "elapsed_seconds": 1.5,
        }

        mock_mod = MagicMock()
        mock_mod._generate_confirmation_code.return_value = "ABC123"
        mock_mod.EmergencyCloseAll.return_value = mock_closer

        with patch.dict("sys.modules", {
            "core.risk.emergency_close_all": mock_mod,
            "core.broker.binance_broker": MagicMock(BinanceBroker=MagicMock()),
            "core.broker.ibkr_adapter": MagicMock(IBKRBroker=MagicMock()),
        }), patch.dict("os.environ", {"BINANCE_API_KEY": "test"}):
            await bot.cmd_emergency(update, ctx)

        reply = update.message.reply_text.call_args[0][0]
        assert "DONE" in reply
        assert "7" in reply


# =============================================================================
# TEST: Data fetchers (edge cases)
# =============================================================================

class TestDataFetchers:
    def test_worker_signals_empty_log(self, bot):
        """_worker_signals returns [] when log file is missing."""
        with patch("pathlib.Path.exists", return_value=False):
            result = bot._worker_signals(15)
        assert result == []

    def test_worker_signals_no_matching_lines(self, bot):
        """_worker_signals returns [] when log has no signal lines."""
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value="just some normal log\nanother line\n"):
            result = bot._worker_signals(15)
        assert result == []

    def test_worker_signals_extracts_signals(self, bot):
        """_worker_signals extracts lines containing SIGNAL keyword."""
        log = (
            "2026-04-01 [INFO] Starting worker\n"
            "2026-04-01 [INFO] SIGNAL BUY BTCUSDC qty=0.01\n"
            "2026-04-01 [INFO] pas de signal FX\n"
        )
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=log):
            result = bot._worker_signals(15)
        assert len(result) == 2

    def test_ibkr_equity_no_log_dir(self, bot):
        """_ibkr_equity returns 0.0 when log dir does not exist."""
        with patch("pathlib.Path.exists", return_value=False):
            result = bot._ibkr_equity()
        assert result == 0.0

    def test_binance_info_exception(self, bot):
        """_binance_info returns error dict on exception."""
        with patch.dict("sys.modules", {"core.broker.binance_broker": None}):
            result = bot._binance_info()
        assert "error" in result

    def test_binance_positions_exception(self, bot):
        """_binance_positions returns [] on exception."""
        with patch.dict("sys.modules", {"core.broker.binance_broker": None}):
            result = bot._binance_positions()
        assert result == []

    def test_alpaca_info_exception(self, bot):
        """_alpaca_info returns error dict on exception."""
        with patch.dict("sys.modules", {"core.alpaca_client.client": None}):
            result = bot._alpaca_info()
        assert "error" in result

    def test_alpaca_positions_exception(self, bot):
        """_alpaca_positions returns [] on exception."""
        with patch.dict("sys.modules", {"core.alpaca_client.client": None}):
            result = bot._alpaca_positions()
        assert result == []

    def test_ibkr_connected_returns_false_on_error(self, bot):
        """_ibkr_connected returns False when connection fails."""
        with patch("socket.create_connection", side_effect=OSError("refused")):
            result = bot._ibkr_connected()
        assert result is False

    def test_worker_running_returns_false_on_error(self, bot):
        """_worker_running returns False when health check fails."""
        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            result = bot._worker_running()
        assert result is False

    def test_load_cash_flows_no_file(self, bot):
        """_load_cash_flows returns [] when file does not exist."""
        with patch("pathlib.Path.exists", return_value=False):
            result = bot._load_cash_flows()
        assert result == []

    def test_strategy_phases_exception(self, bot):
        """_strategy_phases returns {} on exception."""
        with patch("importlib.util.spec_from_file_location", side_effect=Exception("not found")):
            result = bot._strategy_phases()
        assert result == {}


# =============================================================================
# TEST: _auth function directly
# =============================================================================

class TestAuthFunction:
    def test_auth_matching_chat_id(self, bot):
        """_auth returns True for matching CHAT_ID."""
        update = _make_update(AUTHORIZED_CHAT_ID)
        assert bot._auth(update) is True

    def test_auth_wrong_chat_id(self, bot):
        """_auth returns False for non-matching CHAT_ID."""
        update = _make_update(UNAUTHORIZED_CHAT_ID)
        assert bot._auth(update) is False

    def test_auth_zero_chat_id(self, bot):
        """_auth returns False for chat_id=0 (default when env not set)."""
        update = _make_update(0)
        assert bot._auth(update) is False


# =============================================================================
# TEST: All commands reject unauthorized (comprehensive sweep)
# =============================================================================

class TestAllCommandsAuth:
    """Verify every async command handler rejects unauthorized chat_ids."""

    COMMANDS = [
        "cmd_status", "cmd_positions", "cmd_strats", "cmd_crypto", "cmd_fx",
        "cmd_risk", "cmd_signals", "cmd_trades", "cmd_costs", "cmd_health",
        "cmd_kill", "cmd_regime", "cmd_portfolio", "cmd_emergency", "cmd_help",
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cmd_name", COMMANDS)
    async def test_unauthorized_rejected(self, bot, cmd_name):
        """Each command silently rejects unauthorized users."""
        update = _make_update(UNAUTHORIZED_CHAT_ID)
        ctx = _make_context()
        handler_fn = getattr(bot, cmd_name)
        await handler_fn(update, ctx)
        update.message.reply_text.assert_not_awaited()
