"""
Tests for the Telegram command handler module.

Covers:
  - Authorization (allowed / rejected chat_ids)
  - All read-only commands (/status, /positions, /pnl, /paper, /margin, /leverage, /health)
  - Help command
  - Destructive command confirmation flow (/kill, /close, /reduce)
  - Rate limiting on destructive commands
  - Confirmation expiry (60s TTL)
  - Command audit logging
  - Unknown commands
  - Missing callbacks
  - Bot mention suffix stripping (/status@MyBot)

No network calls — everything is mocked.
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.telegram_commands import CONFIRMATION_TTL, TelegramCommandHandler

# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def handler():
    """Create a handler with all callbacks mocked."""
    return TelegramCommandHandler(
        authorized_chat_ids=["123456"],
        bot_token="fake-token",
        get_status_func=MagicMock(return_value="Status: 3 positions, $102k equity"),
        get_positions_func=MagicMock(return_value="AAPL +10 @ $150, MSFT -5 @ $400"),
        get_pnl_func=MagicMock(return_value="Today: +$320, MTD: +$1,200"),
        get_paper_pnl_func=MagicMock(return_value="Paper PnL: +$50"),
        get_margin_func=MagicMock(return_value="Margin used: 22%"),
        kill_func=MagicMock(return_value="All 3 positions closed."),
        pause_strategy_func=MagicMock(return_value="momentum_etf paused."),
        resume_strategy_func=MagicMock(return_value="momentum_etf resumed."),
        reduce_positions_func=MagicMock(return_value="Reduced by 50%."),
        close_position_func=MagicMock(return_value="AAPL closed."),
        get_leverage_func=MagicMock(return_value="Equity leverage: 1.2x"),
        get_health_func=MagicMock(return_value="All systems OK"),
    )


@pytest.fixture
def handler_no_callbacks():
    """Create a handler with no callbacks configured."""
    return TelegramCommandHandler(
        authorized_chat_ids=["123456"],
        bot_token="fake-token",
    )


CHAT = "123456"
UNAUTHORIZED = "999999"


# =============================================================================
# TEST: Authorization
# =============================================================================

class TestAuthorization:
    def test_authorized_chat_id_accepted(self, handler):
        """Authorized chat_id: command is processed."""
        resp = handler.handle_message(CHAT, "/help")
        assert "Trading Platform Commands" in resp

    def test_unauthorized_chat_id_rejected(self, handler):
        """Unauthorized chat_id: command is rejected."""
        resp = handler.handle_message(UNAUTHORIZED, "/status")
        assert resp == "Unauthorized."

    def test_unauthorized_not_logged(self, handler):
        """Unauthorized commands should not appear in audit log."""
        handler.handle_message(UNAUTHORIZED, "/status")
        assert len(handler.get_command_log()) == 0


# =============================================================================
# TEST: /help
# =============================================================================

class TestHelp:
    def test_help_returns_all_commands(self, handler):
        """Help lists all available commands."""
        resp = handler.handle_message(CHAT, "/help")
        for cmd in ["/status", "/positions", "/pnl", "/paper", "/margin",
                    "/leverage", "/health", "/pause", "/resume", "/close",
                    "/reduce", "/kill", "/help"]:
            assert cmd in resp


# =============================================================================
# TEST: Read-only commands
# =============================================================================

class TestReadOnlyCommands:
    def test_status_calls_callback(self, handler):
        """Status calls get_status_func and returns result."""
        resp = handler.handle_message(CHAT, "/status")
        handler._callbacks["status"].assert_called_once()
        assert "3 positions" in resp

    def test_positions_calls_callback(self, handler):
        """Positions calls get_positions_func."""
        resp = handler.handle_message(CHAT, "/positions")
        handler._callbacks["positions"].assert_called_once()
        assert "AAPL" in resp

    def test_pnl_calls_callback(self, handler):
        """PnL calls get_pnl_func."""
        resp = handler.handle_message(CHAT, "/pnl")
        handler._callbacks["pnl"].assert_called_once()
        assert "+$320" in resp

    def test_paper_calls_callback(self, handler):
        """Paper calls get_paper_pnl_func."""
        resp = handler.handle_message(CHAT, "/paper")
        handler._callbacks["paper"].assert_called_once()
        assert "Paper PnL" in resp

    def test_margin_calls_callback(self, handler):
        """Margin calls get_margin_func."""
        resp = handler.handle_message(CHAT, "/margin")
        handler._callbacks["margin"].assert_called_once()
        assert "22%" in resp

    def test_leverage_calls_callback(self, handler):
        """Leverage calls get_leverage_func."""
        resp = handler.handle_message(CHAT, "/leverage")
        handler._callbacks["leverage"].assert_called_once()
        assert "1.2x" in resp

    def test_health_calls_callback(self, handler):
        """Health calls get_health_func."""
        resp = handler.handle_message(CHAT, "/health")
        handler._callbacks["health"].assert_called_once()
        assert "All systems OK" in resp


# =============================================================================
# TEST: /kill (destructive with confirmation)
# =============================================================================

class TestKillCommand:
    def test_kill_without_confirm_asks_confirmation(self, handler):
        """/kill without CONFIRM asks for confirmation."""
        resp = handler.handle_message(CHAT, "/kill")
        assert "CONFIRM" in resp
        assert "60s" in resp
        handler._callbacks["kill"].assert_not_called()

    def test_kill_confirm_executes(self, handler):
        """/kill CONFIRM executes after initial /kill."""
        handler.handle_message(CHAT, "/kill")
        resp = handler.handle_message(CHAT, "/kill CONFIRM")
        assert "ACTIVATED" in resp
        handler._callbacks["kill"].assert_called_once()

    def test_kill_confirm_without_prior_rejected(self, handler):
        """/kill CONFIRM without a prior /kill is rejected."""
        resp = handler.handle_message(CHAT, "/kill CONFIRM")
        assert "No pending" in resp
        handler._callbacks["kill"].assert_not_called()


# =============================================================================
# TEST: /close (destructive with confirmation)
# =============================================================================

class TestCloseCommand:
    def test_close_without_confirm_asks_confirmation(self, handler):
        """/close AAPL without CONFIRM asks for confirmation."""
        resp = handler.handle_message(CHAT, "/close AAPL")
        assert "CONFIRM" in resp
        assert "AAPL" in resp
        handler._callbacks["close"].assert_not_called()

    def test_close_confirm_executes(self, handler):
        """/close AAPL CONFIRM executes after initial /close AAPL."""
        handler.handle_message(CHAT, "/close AAPL")
        resp = handler.handle_message(CHAT, "/close AAPL CONFIRM")
        assert "closed" in resp.lower() or "AAPL" in resp
        handler._callbacks["close"].assert_called_once_with("AAPL")

    def test_close_no_ticker_shows_usage(self, handler):
        """/close without ticker shows usage."""
        resp = handler.handle_message(CHAT, "/close")
        assert "Usage" in resp

    def test_close_confirm_wrong_ticker_rejected(self, handler):
        """/close AAPL then /close MSFT CONFIRM is rejected (wrong ticker)."""
        handler.handle_message(CHAT, "/close AAPL")
        resp = handler.handle_message(CHAT, "/close MSFT CONFIRM")
        assert "No pending" in resp
        handler._callbacks["close"].assert_not_called()


# =============================================================================
# TEST: /reduce (destructive with confirmation)
# =============================================================================

class TestReduceCommand:
    def test_reduce_without_confirm_asks_confirmation(self, handler):
        """/reduce 50% without CONFIRM asks for confirmation."""
        resp = handler.handle_message(CHAT, "/reduce 50%")
        assert "CONFIRM" in resp
        assert "50" in resp
        handler._callbacks["reduce"].assert_not_called()

    def test_reduce_confirm_executes(self, handler):
        """/reduce 50% CONFIRM executes after initial /reduce 50%."""
        handler.handle_message(CHAT, "/reduce 50%")
        resp = handler.handle_message(CHAT, "/reduce 50% CONFIRM")
        handler._callbacks["reduce"].assert_called_once_with(50.0)

    def test_reduce_invalid_percentage(self, handler):
        """/reduce abc% returns error."""
        resp = handler.handle_message(CHAT, "/reduce abc%")
        assert "Invalid" in resp

    def test_reduce_out_of_range(self, handler):
        """/reduce 150% returns error."""
        resp = handler.handle_message(CHAT, "/reduce 150%")
        assert "between" in resp.lower() or "1 and 100" in resp

    def test_reduce_no_args_shows_usage(self, handler):
        """/reduce without percentage shows usage."""
        resp = handler.handle_message(CHAT, "/reduce")
        assert "Usage" in resp


# =============================================================================
# TEST: /pause and /resume
# =============================================================================

class TestPauseResume:
    def test_pause_calls_callback(self, handler):
        """/pause strategy_name calls the pause callback."""
        resp = handler.handle_message(CHAT, "/pause momentum_etf")
        handler._callbacks["pause"].assert_called_once_with("momentum_etf")
        assert "paused" in resp.lower()

    def test_resume_calls_callback(self, handler):
        """/resume strategy_name calls the resume callback."""
        resp = handler.handle_message(CHAT, "/resume momentum_etf")
        handler._callbacks["resume"].assert_called_once_with("momentum_etf")
        assert "resumed" in resp.lower()

    def test_pause_no_args_shows_usage(self, handler):
        """/pause without strategy name shows usage."""
        resp = handler.handle_message(CHAT, "/pause")
        assert "Usage" in resp

    def test_resume_no_args_shows_usage(self, handler):
        """/resume without strategy name shows usage."""
        resp = handler.handle_message(CHAT, "/resume")
        assert "Usage" in resp


# =============================================================================
# TEST: Rate limiting
# =============================================================================

class TestRateLimiting:
    def test_two_destructive_within_cooldown_rejected(self, handler):
        """Two destructive commands less than 60s apart: second is rejected."""
        # First /kill
        handler.handle_message(CHAT, "/kill")
        handler.handle_message(CHAT, "/kill CONFIRM")
        # Second /kill immediately
        handler.handle_message(CHAT, "/kill")
        resp = handler.handle_message(CHAT, "/kill CONFIRM")
        assert "Rate limited" in resp

    def test_destructive_after_cooldown_allowed(self, handler):
        """Destructive command after cooldown period is allowed."""
        handler.handle_message(CHAT, "/kill")
        handler.handle_message(CHAT, "/kill CONFIRM")
        # Simulate time passing
        handler._last_destructive[CHAT] = time.time() - 61
        handler.handle_message(CHAT, "/kill")
        resp = handler.handle_message(CHAT, "/kill CONFIRM")
        assert "ACTIVATED" in resp


# =============================================================================
# TEST: Confirmation expiry
# =============================================================================

class TestConfirmationExpiry:
    def test_confirmation_expires_after_ttl(self, handler):
        """Confirmation expires after CONFIRMATION_TTL seconds."""
        handler.handle_message(CHAT, "/kill")
        # Backdate the pending confirmation
        handler._pending_confirmations[CHAT]["timestamp"] = time.time() - (CONFIRMATION_TTL + 1)
        resp = handler.handle_message(CHAT, "/kill CONFIRM")
        assert "No pending" in resp
        handler._callbacks["kill"].assert_not_called()


# =============================================================================
# TEST: Command logging
# =============================================================================

class TestCommandLogging:
    def test_every_command_recorded(self, handler):
        """Every authorized command is recorded in the audit log."""
        handler.handle_message(CHAT, "/status")
        handler.handle_message(CHAT, "/pnl")
        handler.handle_message(CHAT, "/help")
        log = handler.get_command_log()
        assert len(log) == 3
        # Most recent first
        assert "/help" in log[0]["text"]
        assert "/pnl" in log[1]["text"]
        assert "/status" in log[2]["text"]

    def test_log_contains_required_fields(self, handler):
        """Log entries contain timestamp, chat_id, text, response_preview."""
        handler.handle_message(CHAT, "/status")
        log = handler.get_command_log(limit=1)
        entry = log[0]
        assert "timestamp" in entry
        assert entry["chat_id"] == CHAT
        assert entry["text"] == "/status"
        assert len(entry["response_preview"]) > 0

    def test_log_limit(self, handler):
        """get_command_log respects the limit parameter."""
        for i in range(10):
            handler.handle_message(CHAT, "/help")
        log = handler.get_command_log(limit=3)
        assert len(log) == 3


# =============================================================================
# TEST: Unknown commands & missing callbacks
# =============================================================================

class TestEdgeCases:
    def test_unknown_command(self, handler):
        """Unknown command returns helpful error."""
        resp = handler.handle_message(CHAT, "/foobar")
        assert "Unknown command" in resp
        assert "/help" in resp

    def test_missing_callback_returns_not_configured(self, handler_no_callbacks):
        """Command with no callback returns 'not configured'."""
        resp = handler_no_callbacks.handle_message(CHAT, "/status")
        assert "not configured" in resp

    def test_non_command_text(self, handler):
        """Text that does not start with / returns error."""
        resp = handler.handle_message(CHAT, "hello world")
        assert "Unknown command" in resp

    def test_bot_mention_suffix_stripped(self, handler):
        """/status@MyBot works the same as /status."""
        resp = handler.handle_message(CHAT, "/status@TradingBot")
        assert "3 positions" in resp
        handler._callbacks["status"].assert_called_once()

    def test_callback_exception_handled(self, handler):
        """If a callback raises, handler returns error message gracefully."""
        handler._callbacks["status"] = MagicMock(side_effect=RuntimeError("broker down"))
        resp = handler.handle_message(CHAT, "/status")
        assert "Error" in resp
        assert "broker down" in resp

    def test_kill_callback_exception_handled(self, handler):
        """If kill callback raises, handler returns error message."""
        handler._callbacks["kill"] = MagicMock(side_effect=RuntimeError("connection lost"))
        handler.handle_message(CHAT, "/kill")
        resp = handler.handle_message(CHAT, "/kill CONFIRM")
        assert "FAILED" in resp
        assert "connection lost" in resp

    def test_dict_callback_result_formatted(self, handler):
        """If callback returns a dict, it is JSON-formatted in <pre> tags."""
        handler._callbacks["status"] = MagicMock(return_value={"equity": 100000, "positions": 3})
        resp = handler.handle_message(CHAT, "/status")
        assert "<pre>" in resp
        assert "100000" in resp

    def test_close_ticker_uppercased(self, handler):
        """/close aapl normalizes ticker to AAPL."""
        handler.handle_message(CHAT, "/close aapl")
        handler.handle_message(CHAT, "/close AAPL CONFIRM")
        handler._callbacks["close"].assert_called_once_with("AAPL")


# =============================================================================
# TEST: Polling infrastructure (no network)
# =============================================================================

class TestPolling:
    def test_stop_polling_sets_flag(self, handler):
        """stop_polling sets _running to False."""
        handler._running = True
        handler.stop_polling()
        assert handler._running is False

    def test_send_response_no_token(self, handler):
        """_send_response returns False when token is empty."""
        handler._token = ""
        result = handler._send_response(CHAT, "test")
        assert result is False

    def test_poll_updates_no_token(self, handler):
        """_poll_updates returns empty list when token is empty."""
        handler._token = ""
        result = handler._poll_updates()
        assert result == []


# =============================================================================
# TEST: Constructor defaults
# =============================================================================

class TestConstructor:
    def test_default_env_vars(self):
        """Constructor reads from env vars when no explicit args given."""
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "env-token", "TELEGRAM_CHAT_ID": "env-chat"}):
            h = TelegramCommandHandler()
            assert h._token == "env-token"
            assert "env-chat" in h._authorized

    def test_chat_id_coerced_to_string(self):
        """Integer chat_ids are coerced to strings."""
        h = TelegramCommandHandler(authorized_chat_ids=[123456], bot_token="t")
        assert "123456" in h._authorized
