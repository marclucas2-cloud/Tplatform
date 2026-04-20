"""Regression test for DD -53% false positive on IBKR down (2026-04-20).

Scenario : IB Gateway unreachable overnight (2FA IB Key push waits for user
wake-up). The fail-open path makes ibkr equity=0 while Binance stays online.
Before the fix, unified_portfolio.py computed peak=32k, nav=10.5k → DD -67%
→ spurious CLOSE ALL EMERGENCY alert.

The fix adds a broker-vanished guard that skips DD calc when a major broker
component goes from >$500 to exactly 0 while another stays online.
"""
from __future__ import annotations

from core.risk.unified_portfolio import UnifiedPortfolioView


def _make_data(equity: float, cash: float = 0.0) -> dict:
    return {"equity": equity, "positions": [], "cash": cash}


def test_ibkr_down_skips_dd_calc_when_binance_alive():
    """Scenario from 2026-04-19 night: IBGW down 22:18 → 03:48 UTC."""
    alerts: list[tuple[str, str]] = []
    view = UnifiedPortfolioView(
        alert_callback=lambda msg, level="info": alerts.append((level, msg)),
    )

    # Tick 1 — normal state, both brokers up, establishes peak and last_*
    snap1 = view.update(
        binance_data=_make_data(10_500),
        ibkr_data=_make_data(11_300),
        alpaca_data=_make_data(0),
        eur_usd_rate=1.08,
    )
    assert snap1.nav_total > 20_000
    assert snap1.alert_level in ("OK", "WARNING", "DEFENSIVE")
    peak_after_tick1 = view._peak_nav

    # Tick 2 — IBKR unreachable (fail-open returns 0), Binance still online
    snap2 = view.update(
        binance_data=_make_data(10_500),
        ibkr_data=_make_data(0),
        alpaca_data=_make_data(0),
        eur_usd_rate=1.08,
    )
    # DD MUST be 0 (skipped), not ~-50%
    assert snap2.dd_from_peak_pct == 0, (
        f"DD must be skipped when IBKR vanished, got {snap2.dd_from_peak_pct}%"
    )
    # No EMERGENCY alert triggered
    emergencies = [(l, m) for l, m in alerts if l == "critical" and "EMERGENCY" in m]
    assert not emergencies, f"Spurious EMERGENCY alert: {emergencies}"
    # Peak preserved (not reset to degraded NAV)
    assert view._peak_nav == peak_after_tick1


def test_real_drawdown_still_detected_when_both_brokers_up():
    """Make sure the guard doesn't hide a legitimate DD when both brokers are up."""
    alerts: list[tuple[str, str]] = []
    view = UnifiedPortfolioView(
        alert_callback=lambda msg, level="info": alerts.append((level, msg)),
    )

    # Build peak
    view.update(
        binance_data=_make_data(10_000),
        ibkr_data=_make_data(12_000),
        alpaca_data=_make_data(0),
        eur_usd_rate=1.08,
    )
    # Real -10% drop — NAV drops from $22k to $19.8k, both brokers online
    snap = view.update(
        binance_data=_make_data(9_000),
        ibkr_data=_make_data(10_800),
        alpaca_data=_make_data(0),
        eur_usd_rate=1.08,
    )
    # DD must be approximately -10%
    assert -11.0 < snap.dd_from_peak_pct < -9.0, (
        f"Real DD should be ~-10%, got {snap.dd_from_peak_pct}"
    )


def test_binance_down_skips_dd_calc_when_ibkr_alive():
    """Symmetric case: Binance unreachable, IBKR online."""
    alerts: list[tuple[str, str]] = []
    view = UnifiedPortfolioView(
        alert_callback=lambda msg, level="info": alerts.append((level, msg)),
    )
    view.update(
        binance_data=_make_data(10_500),
        ibkr_data=_make_data(11_300),
        alpaca_data=_make_data(0),
        eur_usd_rate=1.08,
    )
    snap = view.update(
        binance_data=_make_data(0),
        ibkr_data=_make_data(11_300),
        alpaca_data=_make_data(0),
        eur_usd_rate=1.08,
    )
    assert snap.dd_from_peak_pct == 0
    emergencies = [(l, m) for l, m in alerts if l == "critical" and "EMERGENCY" in m]
    assert not emergencies


def test_guard_not_applied_when_bot_never_had_broker():
    """If last_ibkr_eq == 0 from init (fresh start, IBKR never connected),
    the guard should NOT trigger — peer continues normally."""
    view = UnifiedPortfolioView()
    # First tick with IBKR=0 from the start (not a vanish, it's just absent)
    snap = view.update(
        binance_data=_make_data(10_500),
        ibkr_data=_make_data(0),
        alpaca_data=_make_data(0),
        eur_usd_rate=1.08,
    )
    # Normal path: peak set to 10_500, DD = 0 (no prior peak)
    assert snap.nav_total == 10_500
    assert snap.dd_from_peak_pct == 0
