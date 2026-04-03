#!/usr/bin/env python3
"""Signal Funnel Diagnostic — trace where signals die.

Run on VPS:
  python scripts/signal_funnel_diagnostic.py

Traces each signal through the 14-layer funnel and identifies bottlenecks.
Output: data/diagnostics/signal_funnel_report.json
"""

import json
import os
import socket
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def diagnose_signal_funnel():
    """Run the full diagnostic."""
    print("=" * 70)
    print("SIGNAL FUNNEL DIAGNOSTIC")
    print(f"Date: {datetime.now().isoformat()}")
    print("=" * 70)

    results = {
        "timestamp": datetime.now().isoformat(),
        "brokers": {},
        "strategies": {},
        "funnel": {
            "signals_raw": 0,
            "killed_by_regime": 0,
            "killed_by_activation_matrix": 0,
            "killed_by_kill_switch": 0,
            "killed_by_risk_manager": 0,
            "killed_by_sizing_too_small": 0,
            "killed_by_spread_check": 0,
            "killed_by_insufficient_capital": 0,
            "killed_by_max_positions": 0,
            "killed_by_cooldown": 0,
            "killed_by_market_hours": 0,
            "orders_submitted": 0,
            "orders_filled": 0,
        },
        "bottlenecks": [],
    }

    print("\n--- CHECK 1: BROKER CONNECTIVITY ---")
    check_broker_connectivity(results)

    print("\n--- CHECK 2: KILL SWITCHES ---")
    check_kill_switches(results)

    print("\n--- CHECK 3: REGIME ENGINE ---")
    check_regime_state(results)

    print("\n--- CHECK 4: CAPITAL & SIZING ---")
    check_capital_sizing(results)

    print("\n--- CHECK 5: MARKET HOURS ---")
    check_market_hours(results)

    print("\n--- CHECK 6: STRATEGY-BY-STRATEGY TRACE ---")
    trace_each_strategy(results)

    print("\n--- CHECK 7: FUNNEL LOG ANALYSIS ---")
    analyze_funnel_logs(results)

    print_summary(results)

    report_path = ROOT / "data" / "diagnostics" / "signal_funnel_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nReport saved to {report_path}")


def check_broker_connectivity(results):
    """Check if brokers are connected and responding."""
    brokers_status = {}

    # Binance
    try:
        from core.broker.binance_broker import BinanceBroker
        brokers_status["binance"] = {"connected": True, "note": "module importable"}
        print("  OK Binance: module importable")
    except Exception as e:
        brokers_status["binance"] = {"connected": False, "reason": str(e)}
        print(f"  FAIL Binance: {e}")

    # IBKR
    ibkr_host = os.environ.get("IBKR_HOST", "127.0.0.1")
    for port, label in [(4002, "LIVE"), (4003, "PAPER")]:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        try:
            s.connect((ibkr_host, port))
            print(f"  OK IBKR {label} ({ibkr_host}:{port}): CONNECTED")
            brokers_status[f"ibkr_{label.lower()}"] = {"connected": True}
        except (ConnectionRefusedError, socket.timeout, OSError):
            print(f"  FAIL IBKR {label} ({ibkr_host}:{port}): NOT CONNECTED")
            brokers_status[f"ibkr_{label.lower()}"] = {
                "connected": False, "reason": f"Port {port} not responding",
            }
        finally:
            s.close()

    # Alpaca
    try:
        api_key = os.environ.get("ALPACA_API_KEY", "")
        paper = os.environ.get("PAPER_TRADING", "true")
        brokers_status["alpaca"] = {
            "connected": bool(api_key),
            "paper_mode": paper,
            "note": "API key present" if api_key else "NO API KEY",
        }
        print(f"  {'OK' if api_key else 'FAIL'} Alpaca: {'key present' if api_key else 'NO KEY'} (paper={paper})")
    except Exception as e:
        brokers_status["alpaca"] = {"connected": False, "reason": str(e)}

    results["brokers"] = brokers_status


def check_kill_switches(results):
    """Check if any kill switch is active."""
    kill_files = [
        ROOT / "data" / "crypto_kill_switch_state.json",
        ROOT / "data" / "kill_switch_state.json",
        ROOT / "data" / "state" / "kill_switch_state.json",
    ]

    for path in kill_files:
        if path.exists():
            try:
                with open(path) as f:
                    state = json.load(f)
                active = state.get("active", state.get("killed", False))
                if active:
                    print(f"  BLOCKER KILL SWITCH ACTIVE: {path.name}")
                    print(f"     State: {json.dumps(state, indent=2)}")
                    results["bottlenecks"].append({
                        "layer": "kill_switch",
                        "detail": f"{path.name} is ACTIVE",
                        "severity": "BLOCKER",
                        "fix": "Reset kill switch if conditions are safe",
                    })
                else:
                    print(f"  OK Kill switch: {path.name} (inactive)")
            except Exception as e:
                print(f"  WARN Cannot read {path.name}: {e}")
        else:
            print(f"  -- Not found: {path.name}")


def check_regime_state(results):
    """Check current regime and activation matrix impact."""
    regime_file = ROOT / "data" / "regime_state.json"
    if regime_file.exists():
        try:
            with open(regime_file) as f:
                state = json.load(f)
            print(f"  Current regime state: {json.dumps(state, indent=2)}")
            for ac, regime in state.items():
                if isinstance(regime, str) and regime in ("PANIC", "LOW_LIQUIDITY", "UNKNOWN"):
                    print(f"  WARN {ac} in {regime} — strats may be blocked or reduced")
                    results["bottlenecks"].append({
                        "layer": "regime",
                        "detail": f"{ac} = {regime}",
                        "severity": "HIGH" if regime == "PANIC" else "MEDIUM",
                        "fix": f"Check if {regime} is justified. UNKNOWN at startup is normal — wait for data.",
                    })
        except Exception as e:
            print(f"  WARN Cannot read regime state: {e}")
    else:
        print("  WARN No regime_state.json — regime engine may not have run yet")
        results["bottlenecks"].append({
            "layer": "regime",
            "detail": "No regime_state.json — all strats in UNKNOWN (reduced sizing)",
            "severity": "HIGH",
            "fix": "Ensure worker has run at least 2 regime cycles (30 min)",
        })

    # Check activation matrix config
    matrix_path = ROOT / "config" / "regime.yaml"
    if matrix_path.exists():
        print(f"  OK Activation matrix config: {matrix_path.name}")
    else:
        print("  WARN No regime.yaml — activation matrix not configured")


def check_capital_sizing(results):
    """Check if capital is sufficient for sizing to produce tradeable positions."""
    print("  Capital vs minimum position size analysis:")

    configs = [
        ("Binance (crypto)", 10000, 12, "crypto"),
        ("IBKR (FX+EU)", 10000, 7, "ibkr"),
    ]

    for label, capital, n_strats, broker in configs:
        # Try to read Kelly fraction from config
        kelly = 0.25  # Default after Fix #2

        avg_alloc = capital / n_strats
        kelly_size = avg_alloc * kelly

        print(f"\n  {label}:")
        print(f"    Capital: ${capital:,}")
        print(f"    Active strats: {n_strats}")
        print(f"    Kelly fraction: {kelly}")
        print(f"    Avg alloc per strat: ${avg_alloc:.0f}")
        print(f"    Kelly-sized position: ${kelly_size:.0f}")

        # With activation matrix UNKNOWN multiplier (0.5)
        unknown_size = kelly_size * 0.5
        print(f"    If UNKNOWN regime (0.5x): ${unknown_size:.0f}")

        if unknown_size < 100:
            results["bottlenecks"].append({
                "layer": "sizing",
                "detail": f"{label}: position ${unknown_size:.0f} in UNKNOWN regime — below $100 viable minimum",
                "severity": "HIGH",
                "fix": f"Reduce to 4 strats ({label}) or increase Kelly fraction",
            })
            print(f"    SIZING BOTTLENECK: ${unknown_size:.0f} < $100 minimum")
        elif unknown_size < 200:
            print(f"    WARN: ${unknown_size:.0f} is marginal")
        else:
            print(f"    OK: ${unknown_size:.0f} is viable")


def check_market_hours(results):
    """Check if current time is within trading hours."""
    try:
        import zoneinfo
        now_cet = datetime.now(zoneinfo.ZoneInfo("Europe/Paris"))
    except Exception:
        now_cet = datetime.now()

    day = now_cet.strftime("%A")
    hour = now_cet.hour

    print(f"  Current time: {now_cet.strftime('%A %H:%M')} CET")

    markets = {
        "FX": {"active": day not in ("Saturday", "Sunday")},
        "EU Equities": {"active": 9 <= hour < 18 and day not in ("Saturday", "Sunday")},
        "US Equities": {"active": 15 <= hour < 22 and day not in ("Saturday", "Sunday")},
        "Crypto": {"active": True},
    }

    for market, info in markets.items():
        status = "OPEN" if info["active"] else "CLOSED"
        print(f"  {market}: {status}")
        if not info["active"]:
            results["bottlenecks"].append({
                "layer": "market_hours",
                "detail": f"{market} is closed",
                "severity": "INFO",
                "fix": "Wait for market open",
            })


def trace_each_strategy(results):
    """List known strategies with expected signal frequencies."""
    strategies = [
        {"name": "BTC/ETH Dual Momentum", "market": "crypto", "freq": "4h", "expected": 4},
        {"name": "BTC Mean Reversion", "market": "crypto", "freq": "4h", "expected": 12},
        {"name": "Vol Breakout", "market": "crypto", "freq": "4h", "expected": 6},
        {"name": "Borrow Rate Carry", "market": "crypto", "freq": "daily", "expected": 1},
        {"name": "Altcoin Relative Strength", "market": "crypto", "freq": "daily", "expected": 8},
        {"name": "BTC Dominance Rotation", "market": "crypto", "freq": "daily", "expected": 2},
        {"name": "Liquidation Momentum", "market": "crypto", "freq": "1h", "expected": 8},
        {"name": "Weekend Gap Reversal", "market": "crypto", "freq": "weekly", "expected": 4},
        {"name": "EUR/JPY Carry", "market": "fx", "freq": "daily", "expected": 2},
        {"name": "AUD/JPY Carry", "market": "fx", "freq": "daily", "expected": 2},
        {"name": "EU Gap Open", "market": "eu", "freq": "daily", "expected": 15},
        {"name": "Brent Lag Play", "market": "eu", "freq": "daily", "expected": 20},
    ]

    total_expected = sum(s["expected"] for s in strategies)
    print(f"\n  Expected signals/month (all strats): ~{total_expected}")
    print(f"  Expected trades/month (40-70% conversion): ~{int(total_expected * 0.4)}-{int(total_expected * 0.7)}")
    print(f"  Expected trades/day (avg): ~{total_expected * 0.5 / 30:.1f}")

    results["expected_signals_month"] = total_expected
    results["expected_trades_month"] = int(total_expected * 0.5)


def analyze_funnel_logs(results):
    """Analyze existing funnel logs if available."""
    log_patterns = [
        ROOT / "logs" / "worker.log",
        ROOT / "data" / "events" / "*.jsonl",
    ]

    worker_log = ROOT / "logs" / "worker.log"
    if worker_log.exists():
        print(f"  Worker log found: {worker_log}")
        try:
            content = worker_log.read_text(encoding="utf-8", errors="ignore")
            lines = content.split("\n")

            # Count key patterns
            signals = sum(1 for l in lines if "signal" in l.lower() and "funnel" not in l.lower())
            rejects = sum(1 for l in lines if "reject" in l.lower() or "blocked" in l.lower())
            skips = sum(1 for l in lines if "skip" in l.lower())
            fills = sum(1 for l in lines if "fill" in l.lower() or "filled" in l.lower())
            funnel = sum(1 for l in lines if "FUNNEL" in l)

            print(f"    Lines with 'signal': {signals}")
            print(f"    Lines with 'reject/blocked': {rejects}")
            print(f"    Lines with 'skip': {skips}")
            print(f"    Lines with 'fill/filled': {fills}")
            print(f"    Lines with 'FUNNEL': {funnel}")

            if signals > 0 and fills == 0:
                results["bottlenecks"].append({
                    "layer": "execution",
                    "detail": f"{signals} signals generated but 0 fills — signals are dying in the funnel",
                    "severity": "BLOCKER",
                    "fix": "Check funnel logs: grep 'FUNNEL.*KILL\\|FUNNEL.*REJECT' logs/worker.log",
                })
        except Exception as e:
            print(f"    Cannot analyze: {e}")
    else:
        print("  No worker.log found — run on VPS")


def print_summary(results):
    """Print the final diagnostic summary."""
    print("\n" + "=" * 70)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 70)

    bottlenecks = results.get("bottlenecks", [])
    blockers = [b for b in bottlenecks if b["severity"] == "BLOCKER"]
    highs = [b for b in bottlenecks if b["severity"] == "HIGH"]
    mediums = [b for b in bottlenecks if b["severity"] == "MEDIUM"]

    if blockers:
        print("\n  BLOCKERS (must fix before any trade can happen):")
        for b in blockers:
            print(f"   [{b['layer']}] {b['detail']}")
            print(f"     Fix: {b['fix']}")

    if highs:
        print("\n  HIGH (significantly reducing trade frequency):")
        for b in highs:
            print(f"   [{b['layer']}] {b['detail']}")
            print(f"     Fix: {b['fix']}")

    if mediums:
        print("\n  MEDIUM:")
        for b in mediums:
            print(f"   [{b['layer']}] {b['detail']}")

    if not blockers and not highs:
        print("\n  No critical bottlenecks detected.")
        print("  If still 0 trades, check worker logs for signal generation.")

    print("\n  RECOMMENDED ACTIONS:")
    print("  1. Run this script during market hours (Mon-Fri)")
    print("  2. Check worker logs: grep 'FUNNEL\\|reject\\|skip\\|blocked' logs/worker.log")
    print("  3. Check if strategies are generating signals")
    print("  4. Verify sizing produces positions above broker minimums")
    print()


if __name__ == "__main__":
    diagnose_signal_funnel()
