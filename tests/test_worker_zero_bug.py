"""
Regression tests for Zero-Bug Audit — Phase 4.

Tests every bug found during Phase 1-3 audit to prevent recurrence:
  1. IBKR_CONNECTED env var no longer used (socket check instead)
  2. check_positions_after_close doesn't crash on successful close
  3. acct/earn_positions initialized before use
  4. Weekend loop doesn't skip V10/V12/heartbeat cycles
  5. Digest includes IBKR equity
  6. Alpaca cross-portfolio uses from_env() and get_account_info()
  7. total_capital not referenced in main() scope
  8. Kill switch attribute is _trigger_reason not _reason
  9. Kill switch auto-resets after 24h
  10. All _send_alert calls use lowercase level
  11. Kill switch warmup (3 cycles) prevents false positives on restart
  12. Kill switch check() doesn't re-trigger when already active
  13. Hourly baseline guard prevents stale state file triggers
"""
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ── Helper: read worker.py source ──────────────────────────────────

WORKER_SOURCE = (ROOT / "worker.py").read_text(encoding="utf-8")

# Also read extracted modules (refactored from worker.py)
_HEARTBEAT_SOURCE = ""
_heartbeat_path = ROOT / "core" / "worker" / "heartbeat.py"
if _heartbeat_path.exists():
    _HEARTBEAT_SOURCE = _heartbeat_path.read_text(encoding="utf-8")

# Combined source for tests that search across worker + extracted modules
WORKER_COMBINED_SOURCE = WORKER_SOURCE + "\n" + _HEARTBEAT_SOURCE


class TestIBKRConnectedRemoved:
    """BUG #1: IBKR_CONNECTED env var was never set, blocking auto-deleverage
    and V12 unified portfolio IBKR collection."""

    def test_no_ibkr_connected_env_check(self):
        """worker.py should NOT check IBKR_CONNECTED env var in code (comments OK)."""
        # Find non-comment references to IBKR_CONNECTED
        lines = WORKER_SOURCE.split("\n")
        code_refs = [
            (i + 1, line) for i, line in enumerate(lines)
            if "IBKR_CONNECTED" in line and not line.strip().startswith("#")
        ]
        assert len(code_refs) == 0, (
            f"Found {len(code_refs)} code references to IBKR_CONNECTED — "
            f"should use socket check instead: {code_refs}"
        )

    def test_auto_deleverage_uses_socket(self):
        """Auto-deleverage section should use socket.create_connection."""
        # Find the auto-deleverage section
        idx = WORKER_SOURCE.find("AUTO-DELEVERAGE")
        assert idx > 0, "Auto-deleverage section not found"
        section = WORKER_SOURCE[max(0, idx - 500):idx + 200]
        assert "create_connection" in section, (
            "Auto-deleverage should use socket.create_connection, not env var"
        )


class TestCheckPositionsAfterClose:
    """BUG #2: close_err was undefined when all closes succeeded."""

    def test_no_close_err_reference(self):
        """check_positions_after_close should track failures in _failed list,
        not reference close_err outside its except block."""
        # Find the function (may be in worker.py or extracted to heartbeat.py)
        source = WORKER_COMBINED_SOURCE
        func_start = source.find("def check_positions_after_close")
        func_end = source.find("\ndef ", func_start + 10)
        func_source = source[func_start:func_end]

        # Should have _failed list
        assert "_failed" in func_source, (
            "check_positions_after_close should track failures in _failed list"
        )

        # Should NOT reference close_err outside except block
        lines = func_source.split("\n")
        for i, line in enumerate(lines):
            if "close_err" in line and "except" not in line and "_failed" not in line:
                # Allow close_err in except clause and in _failed.append
                stripped = line.strip()
                if stripped.startswith("except"):
                    continue
                if "_failed.append" in stripped:
                    continue
                if "logger.critical" in stripped and "close_err" in stripped:
                    continue
                # Any other reference to close_err is suspicious
                assert False, (
                    f"Line {i}: '{stripped}' references close_err outside except block"
                )


class TestAcctEarnPositionsInitialized:
    """BUG #3: acct and earn_positions could be undefined if
    get_account_info() raised an exception."""

    def test_acct_initialized_before_use(self):
        """acct should be initialized to {} before the broker try block."""
        func_start = WORKER_SOURCE.find("def run_crypto_cycle")
        func_end = WORKER_SOURCE.find("\ndef ", func_start + 10)
        func_source = WORKER_SOURCE[func_start:func_end]

        assert "acct = {}" in func_source, (
            "acct must be initialized to {} before broker.get_account_info()"
        )

    def test_earn_positions_initialized_before_use(self):
        """earn_positions should be initialized to [] before broker try block."""
        func_start = WORKER_SOURCE.find("def run_crypto_cycle")
        func_end = WORKER_SOURCE.find("\ndef ", func_start + 10)
        func_source = WORKER_SOURCE[func_start:func_end]

        assert "earn_positions = []" in func_source.split("if broker:")[0], (
            "earn_positions must be initialized to [] BEFORE the 'if broker:' block"
        )


class TestWeekendContinueRemoved:
    """BUG #4: weekend 'continue' was skipping ALL cycles including
    V10, V12, heartbeat, HRP, cross-portfolio."""

    def test_no_weekend_continue(self):
        """Main loop should NOT have a blanket 'continue' on weekends."""
        main_start = WORKER_SOURCE.find("def main():")
        main_source = WORKER_SOURCE[main_start:]

        # The old pattern was: if not is_weekday(): sleep(60); continue
        lines = main_source.split("\n")
        for i, line in enumerate(lines):
            if "is_weekday()" in line and i + 2 < len(lines):
                next_lines = lines[i + 1] + lines[i + 2]
                if "continue" in next_lines and "sleep" in next_lines:
                    assert False, (
                        f"Found weekend sleep+continue at line ~{i}: "
                        f"this kills V10/V12/heartbeat on weekends"
                    )

    def test_weekday_guard_on_eod_cleanup(self):
        """V11 EOD cleanup should only run on weekdays."""
        assert "is_weekday() and not v11_eod_done_today" in WORKER_SOURCE

    def test_weekday_guard_on_check_positions(self):
        """check_positions_after_close should only run on weekdays."""
        assert "is_weekday() and not after_close_checked_today" in WORKER_SOURCE


class TestDigestIBKREquity:
    """BUG #5: Telegram V2 digest always showed IBKR=$0."""

    def test_digest_fetches_ibkr(self):
        """Digest section should fetch IBKR equity via IBKRBroker."""
        # Find the digest section (need more chars to include IBKR block)
        digest_start = WORKER_SOURCE.find("3x/day digest")
        assert digest_start > 0, "Digest section not found"
        digest_section = WORKER_SOURCE[digest_start:digest_start + 1500]

        assert "IBKRBroker" in digest_section, (
            "Digest should fetch IBKR equity via IBKRBroker"
        )
        assert "_ibkr_eq" in digest_section, (
            "Digest should set _ibkr_eq from IBKR broker"
        )


class TestAlpacaCrossPortfolio:
    """BUG #6: Alpaca cross-portfolio used AlpacaClient() instead of
    from_env() and get_account() instead of get_account_info()."""

    def test_alpaca_uses_from_env(self):
        """Cross-portfolio Alpaca collection should use from_env()."""
        # Find the cross-portfolio Alpaca section
        idx = WORKER_SOURCE.find("# Collect Alpaca")
        assert idx > 0
        section = WORKER_SOURCE[idx:idx + 500]

        assert "from_env()" in section, (
            "Alpaca cross-portfolio should use AlpacaClient.from_env()"
        )

    def test_alpaca_uses_get_account_info(self):
        """Cross-portfolio should use get_account_info(), not get_account()."""
        idx = WORKER_SOURCE.find("# Collect Alpaca")
        assert idx > 0
        section = WORKER_SOURCE[idx:idx + 500]

        assert "get_account_info()" in section, (
            "Should use get_account_info(), not get_account()"
        )
        # Verify get_account() WITHOUT _info is not present
        lines = section.split("\n")
        for line in lines:
            if "get_account()" in line and "get_account_info()" not in line:
                assert False, f"Found get_account() without _info: {line.strip()}"


class TestTotalCapitalNotInMain:
    """BUG #7: total_capital referenced in main() but only defined
    in run_crypto_cycle() — NameError."""

    def test_no_total_capital_in_main(self):
        """main() function should not reference total_capital as a VARIABLE
        (string keys like 'total_capital' in dict.get() are OK)."""
        main_start = WORKER_SOURCE.find("def main():")
        main_source = WORKER_SOURCE[main_start:]

        lines = main_source.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # Skip string references like .get("total_capital", ...)
            if '"total_capital"' in stripped or "'total_capital'" in stripped:
                continue
            # Check for bare variable reference
            if re.search(r'\btotal_capital\b', stripped):
                assert False, (
                    f"main() references total_capital as variable at line ~{i}: "
                    f"'{stripped}' — should use _ror_capital instead"
                )


class TestKillSwitchAttribute:
    """BUG #8: worker used _reason instead of _trigger_reason."""

    def test_uses_trigger_reason(self):
        """Kill switch check should use _trigger_reason, not _reason."""
        # Find the kill switch check section
        idx = WORKER_SOURCE.find("kill_reason = risk_mgr.kill_switch")
        assert idx > 0, "Kill switch reason extraction not found"
        line = WORKER_SOURCE[idx:idx + 100].split("\n")[0]

        assert "_trigger_reason" in line, (
            f"Should use _trigger_reason, not _reason: {line}"
        )
        assert "._reason" not in line.replace("_trigger_reason", ""), (
            f"Found ._reason (wrong attribute): {line}"
        )


class TestKillSwitchAutoReset:
    """BUG #8b: Kill switch stays active forever — no auto-reset."""

    def test_auto_reset_after_24h(self):
        """Kill switch should auto-reset after 24h."""
        idx = WORKER_SOURCE.find("KILL SWITCH AUTO-RESET")
        assert idx > 0, (
            "Kill switch auto-reset logic not found — "
            "perpetual kill switch blocks all crypto trading"
        )

    def test_auto_reset_threshold_24h(self):
        """Auto-reset should use 24h threshold."""
        func_start = WORKER_SOURCE.find("def run_crypto_cycle")
        func_end = WORKER_SOURCE.find("\ndef ", func_start + 10)
        func_source = WORKER_SOURCE[func_start:func_end]

        assert "_ks_age_h > 24" in func_source, (
            "Kill switch auto-reset should trigger after >24h"
        )


class TestSendAlertLowercase:
    """BUG #9: Some _send_alert calls used uppercase level ("CRITICAL")
    but V2 router expects lowercase ("critical")."""

    def test_all_send_alert_levels_lowercase(self):
        """All _send_alert level= arguments should be lowercase."""
        # Find all _send_alert calls with level=
        pattern = r'_send_alert\([^)]*level\s*=\s*"([^"]+)"'
        matches = re.findall(pattern, WORKER_SOURCE)

        uppercase = [m for m in matches if m != m.lower()]
        assert len(uppercase) == 0, (
            f"Found uppercase level= in _send_alert: {uppercase}. "
            f"V2 router expects lowercase (critical, warning, info)."
        )


class TestNoTelegramNotifyLegacy:
    """BUG #9b: _telegram_notify was the V1 legacy path, should be migrated."""

    def test_no_telegram_notify_calls(self):
        """No calls to _telegram_notify should remain (migrated to _send_alert)."""
        # Skip the deprecated function definition itself
        calls = re.findall(r'(?<!def )_telegram_notify\(', WORKER_SOURCE)
        assert len(calls) == 0, (
            f"Found {len(calls)} calls to _telegram_notify — "
            f"should use _send_alert (V2 router) instead"
        )


# ════════════════════════════════════════════════════════════════════
# Kill Switch False Positive Tests (bugs from April 2 audit)
# ════════════════════════════════════════════════════════════════════

class TestKillSwitchWarmup:
    """BUG #10: Kill switch triggers on stale baselines after worker restart."""

    def test_warmup_skips_kill_switch_first_3_cycles(self):
        """Kill switch should NOT trigger during warmup (first 3 check_drawdown calls)."""
        from core.crypto.risk_manager_crypto import CryptoRiskManager
        rm = CryptoRiskManager(capital=10_000)
        # Ensure kill switch is clean (not polluted by other tests via state file)
        rm.kill_switch._active = False
        rm.kill_switch._trigger_reason = ""
        # Simulate: baselines at 10K, current equity at 9K (-10%)
        rm._hourly_start_equity = 10_000
        rm._daily_start_equity = 10_000

        # First 3 cycles: should pass despite -10% drop
        for i in range(3):
            passed, msg = rm.check_drawdown(9_000)
            assert passed, f"Cycle {i+1}: kill switch triggered during warmup: {msg}"

        # 4th cycle: NOW it should check (but baselines were stabilized at cycle 3)
        passed, msg = rm.check_drawdown(9_000)
        # Should still pass because baselines were reset to 9K at cycle 3
        assert passed, f"Cycle 4 after warmup should pass (baselines stabilized): {msg}"

    def test_warmup_stabilizes_baselines(self):
        """After warmup, baselines should match current equity."""
        from core.crypto.risk_manager_crypto import CryptoRiskManager
        rm = CryptoRiskManager(capital=10_000)
        # Run 3 warmup cycles at 23K
        for _ in range(3):
            rm.check_drawdown(23_000)
        # Baselines should now be at 23K, not 10K
        assert rm._daily_start_equity == 23_000
        assert rm._hourly_start_equity == 23_000


class TestKillSwitchNoRetrigger:
    """BUG #11: Kill switch re-triggers in a loop when already active."""

    def test_check_returns_cached_when_already_active(self):
        """check() should return cached reason when already active, not re-activate."""
        from core.crypto.risk_manager_crypto import CryptoKillSwitch
        ks = CryptoKillSwitch()
        # Activate with a known reason
        ks._activate("test_reason_123")
        assert ks._active

        # Call check with different params — should return cached, not re-trigger
        killed, reason = ks.check(daily_pnl_pct=-99)
        assert killed
        assert reason == "test_reason_123", (
            f"Expected cached reason 'test_reason_123', got '{reason}'"
        )


class TestHourlyBaselineGuard:
    """BUG #12: Stale hourly baseline from state file triggers false kill."""

    def test_stale_hourly_baseline_ignored(self):
        """Hourly PnL should be 0 if baseline is stale (>2h old)."""
        import time

        from core.crypto.risk_manager_crypto import CryptoRiskManager
        rm = CryptoRiskManager(capital=10_000)
        # Skip warmup and ensure kill switch is fresh (not active from other tests)
        rm._check_count = 10
        rm.kill_switch._active = False

        # Simulate stale hourly baseline (set 3h ago)
        rm._last_hourly_reset = time.time() - 3 * 3600
        rm._hourly_start_equity = 15_000  # Would cause -33% if used

        # Check with current equity 10K
        passed, msg = rm.check_drawdown(10_000)
        # Should pass because stale hourly was reset, not used for kill switch
        assert passed, f"Stale hourly baseline caused false trigger: {msg}"

    def test_baseline_mismatch_resets_all(self):
        """When any baseline is >1.5x off, ALL baselines reset."""
        from core.crypto.risk_manager_crypto import CryptoRiskManager
        rm = CryptoRiskManager(capital=10_000)
        rm._check_count = 10  # Skip warmup

        # Set one baseline to 2x current (would trigger 1.5x guard)
        rm._weekly_start_equity = 30_000
        rm._daily_start_equity = 10_000
        rm._hourly_start_equity = 10_000
        rm._peak_equity = 10_000

        passed, msg = rm.check_drawdown(10_000)
        assert passed, f"Baseline mismatch guard failed: {msg}"
        # All baselines should now be 10K
        assert rm._weekly_start_equity == 10_000
