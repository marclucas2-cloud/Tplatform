"""
Dry-Run Execution Pipeline V12 — validation quotidienne proactive.

Pour chaque strat active, traverse TOUTE la chaine SANS envoyer d'ordre :
  1. Fetch vrais prix (Binance / IBKR)
  2. Generer le signal
  3. Calculer qty avec LOT_SIZE correct
  4. Verifier solde (spot pour BUY, margin collateral pour SHORT)
  5. Verifier risk manager (validate_order)
  6. Verifier regime filter (get_activation_multiplier)

Usage:
  python scripts/dry_run_pipeline.py              # Toutes les strats
  python scripts/dry_run_pipeline.py STRAT-001     # Une seule strat
"""
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "archive" / "intraday-backtesterV2"))
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("dry_run")

KELLY_FRACTION = 0.125  # Must match worker.py


def dry_run_crypto(filter_strat: str | None = None) -> list[dict]:
    """Dry-run all crypto strategies. Returns list of results."""
    results = []

    if not os.getenv("BINANCE_API_KEY"):
        logger.error("BINANCE_API_KEY not set — cannot dry-run crypto")
        return results

    import pandas as pd
    import yaml
    from strategies.crypto import CRYPTO_STRATEGIES

    from core.broker.binance_broker import BinanceBroker
    from core.crypto.risk_manager_crypto import CryptoRiskManager

    # Config
    alloc_path = ROOT / "config" / "crypto_allocation.yaml"
    crypto_config = {}
    if alloc_path.exists():
        crypto_config = yaml.safe_load(
            alloc_path.read_text(encoding="utf-8")
        ).get("crypto_allocation", {})
    total_capital = crypto_config.get("total_capital", 10_000)

    # Broker + account
    broker = BinanceBroker()
    acct = broker.get_account_info()
    equity = float(acct.get("equity", 0))
    cash = float(acct.get("cash", 0))
    positions = broker.get_positions()
    earn_positions = broker.get_earn_positions()

    stable_earn = sum(
        float(ep.get("amount", 0))
        for ep in earn_positions
        if ep.get("asset") in ("USDT", "USDC", "BUSD")
    )
    cash_available = cash + float(acct.get("spot_total_usd", 0)) + stable_earn

    logger.info(f"Crypto equity=${equity:,.0f}, cash=${cash:.0f}, cash_available=${cash_available:,.0f}")

    # Risk manager
    risk_mgr = CryptoRiskManager(capital=max(equity, total_capital))
    risk_result = risk_mgr.check_all(
        positions=positions,
        current_equity=equity,
        cash_available=cash_available,
        earn_total=float(acct.get("earn_total_usd", 0)),
    )

    # Regime engine (try to load)
    regime_mult_fn = None
    try:
        from core.regime.regime_scheduler import RegimeScheduler
        rs = RegimeScheduler()
        regime_mult_fn = rs.get_activation_multiplier
    except Exception:
        logger.info("Regime engine not available — using mult=1.0")

    for strat_id, strat_data in CRYPTO_STRATEGIES.items():
        if filter_strat and strat_id != filter_strat:
            continue

        config = strat_data["config"]
        signal_fn = strat_data["signal_fn"]
        strat_name = config.get("name", strat_id)
        r = {"strat_id": strat_id, "name": strat_name, "steps": {}, "passed": True}

        # Step 1: Fetch prices
        primary_symbol = config.get("symbols", ["BTCUSDT"])[0]
        trade_symbol = primary_symbol.replace("USDT", "USDC") if primary_symbol.endswith("USDT") else primary_symbol
        timeframe = config.get("timeframe", "4h")

        try:
            price_data = broker.get_prices(trade_symbol, timeframe=timeframe, bars=100)
            bars = price_data.get("bars", [])
            if not bars:
                r["steps"]["fetch_prices"] = "FAIL: no bars returned"
                r["passed"] = False
                results.append(r)
                continue
            last_bar = bars[-1]
            price = last_bar["c"]
            df_full = pd.DataFrame(bars)
            df_full.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}, inplace=True)
            r["steps"]["fetch_prices"] = f"OK: {len(bars)} bars, last=${price:,.0f}"
        except Exception as e:
            r["steps"]["fetch_prices"] = f"FAIL: {e}"
            r["passed"] = False
            results.append(r)
            continue

        # Step 2: Generate signal
        candle = pd.Series({
            "close": price, "open": last_bar["o"], "high": last_bar["h"],
            "low": last_bar["l"], "volume": last_bar["v"],
            "timestamp": datetime.now(UTC).isoformat(),
        })
        alloc_pct = config.get("allocation_pct", 0.10)
        strat_capital = equity * alloc_pct * KELLY_FRACTION
        state = {
            "capital": equity, "equity": equity, "positions": positions,
            "i": len(df_full) - 1 if not df_full.empty else 0,
        }
        kwargs = {"df_full": df_full, "symbol": primary_symbol}

        try:
            signal = signal_fn(candle, state, **kwargs)
            if signal is None:
                r["steps"]["signal"] = "OK: no signal (normal)"
            else:
                action = signal.get("action", "?")
                r["steps"]["signal"] = f"OK: {action} — {json.dumps({k: v for k, v in signal.items() if k != 'df_full'}, default=str)[:200]}"
        except Exception as e:
            r["steps"]["signal"] = f"FAIL: {e}"
            r["passed"] = False
            results.append(r)
            continue

        # Step 3: LOT_SIZE qty calculation
        if signal and signal.get("action") in ("BUY", "SELL", "LONG", "SHORT"):
            side = "BUY" if signal["action"] in ("BUY", "LONG") else "SELL"
            raw_qty = strat_capital / price if price > 0 else 0
            if "BTC" in trade_symbol:
                qty = float(f"{raw_qty:.5f}")
            elif "ETH" in trade_symbol:
                qty = float(f"{raw_qty:.4f}")
            else:
                qty = float(f"{raw_qty:.3f}")
            min_notional = 5.0  # Binance min $5
            notional = qty * price
            if notional < min_notional:
                r["steps"]["lot_size"] = f"FAIL: notional ${notional:.2f} < min $5"
                r["passed"] = False
            else:
                r["steps"]["lot_size"] = f"OK: {side} {qty} {trade_symbol} @ ${price:,.0f} = ${notional:,.0f}"
        else:
            r["steps"]["lot_size"] = "SKIP: no directional signal"

        # Step 4: Balance check
        market_type = config.get("market_type", "spot")
        if signal and signal.get("action") in ("BUY", "SELL", "LONG", "SHORT"):
            if market_type == "margin" and side == "SELL":
                # Check margin collateral
                try:
                    has_collateral = broker.ensure_margin_collateral(trade_symbol)
                    r["steps"]["balance"] = f"OK: margin collateral {'present' if has_collateral else 'ABSENT'}"
                    if not has_collateral:
                        r["passed"] = False
                        r["steps"]["balance"] = "FAIL: no margin collateral"
                except Exception as e:
                    r["steps"]["balance"] = f"FAIL: margin check error: {e}"
                    r["passed"] = False
            else:
                # Spot: check cash
                if cash_available >= strat_capital:
                    r["steps"]["balance"] = f"OK: cash_available=${cash_available:,.0f} >= needed=${strat_capital:,.0f}"
                else:
                    r["steps"]["balance"] = f"FAIL: cash_available=${cash_available:,.0f} < needed=${strat_capital:,.0f}"
                    r["passed"] = False
        else:
            r["steps"]["balance"] = "SKIP: no directional signal"

        # Step 5: Risk manager validate_order
        if signal and signal.get("action") in ("BUY", "SELL", "LONG", "SHORT"):
            try:
                valid, msg = risk_mgr.validate_order(
                    notional=strat_capital,
                    strategy=strat_id,
                    current_equity=equity,
                )
                r["steps"]["risk_validate"] = f"{'OK' if valid else 'FAIL'}: {msg}"
                if not valid:
                    r["passed"] = False
            except Exception as e:
                r["steps"]["risk_validate"] = f"FAIL: {e}"
                r["passed"] = False
        else:
            r["steps"]["risk_validate"] = "SKIP: no directional signal"

        # Step 6: Regime filter
        if regime_mult_fn:
            try:
                mult = regime_mult_fn(strat_id)
                if mult <= 0:
                    r["steps"]["regime"] = "BLOCKED: mult=0 (regime filter)"
                else:
                    r["steps"]["regime"] = f"OK: mult={mult:.2f}"
            except Exception as e:
                r["steps"]["regime"] = f"FAIL: {e}"
        else:
            r["steps"]["regime"] = "SKIP: regime engine not loaded"

        results.append(r)

    return results


def dry_run_fx() -> list[dict]:
    """Dry-run FX carry strategy."""
    results = []

    import socket
    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = int(os.getenv("IBKR_PORT", "4002"))

    try:
        with socket.create_connection((host, port), timeout=5):
            pass
    except Exception:
        logger.warning(f"IBKR not connected ({host}:{port}) — skip FX dry-run")
        return [{"strat_id": "fx_carry_momentum", "name": "FX Carry-Mom", "steps": {"connect": f"FAIL: {host}:{port} unreachable"}, "passed": False}]

    try:
        import pandas as pd

        from core.broker.ibkr_adapter import IBKRBroker

        ibkr = IBKRBroker(client_id=98)  # Dedicated dry-run clientId
        try:
            info = ibkr.get_account_info()
            equity = float(info.get("equity", 0))

            r = {"strat_id": "fx_carry_momentum", "name": "FX Carry-Mom Filter", "steps": {}, "passed": True}
            r["steps"]["connect"] = f"OK: equity=${equity:,.0f}"

            # Load data
            data_dir = ROOT / "data" / "fx"
            pair_data = {}
            for pair in ["AUDJPY", "USDJPY", "EURJPY", "NZDUSD"]:
                fpath = data_dir / f"{pair}_1D.parquet"
                if fpath.exists():
                    df = pd.read_parquet(fpath)
                    df["datetime"] = pd.to_datetime(df["datetime"])
                    df = df.set_index("datetime").sort_index()
                    pair_data[pair] = df

            if not pair_data:
                r["steps"]["data"] = "FAIL: no FX daily data"
                r["passed"] = False
            else:
                r["steps"]["data"] = f"OK: {len(pair_data)} pairs loaded"

                # Generate signal
                from strategies_v2.fx.fx_carry_momentum_filter import FXCarryMomentumFilter
                strat = FXCarryMomentumFilter()
                state = {"equity": equity, "i": len(list(pair_data.values())[0])}
                signal = strat.signal_fn(None, state, pair_data=pair_data, equity=equity)

                if signal is None:
                    r["steps"]["signal"] = "OK: no signal (momentum negative)"
                elif signal.get("action") == "CLOSE_ALL":
                    r["steps"]["signal"] = f"OK: CLOSE_ALL — {signal.get('reason')}"
                else:
                    pairs = signal.get("pairs", [])
                    r["steps"]["signal"] = f"OK: {len(pairs)} pairs, total ${signal.get('total_notional', 0):,.0f}"

            results.append(r)
        finally:
            ibkr.disconnect()

    except Exception as e:
        results.append({"strat_id": "fx_carry_momentum", "name": "FX Carry-Mom", "steps": {"error": f"FAIL: {e}"}, "passed": False})

    return results


def main():
    filter_strat = sys.argv[1] if len(sys.argv) > 1 else None

    print("=" * 60)
    print("  DRY-RUN EXECUTION PIPELINE V12")
    print(f"  {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    all_results = []

    # Crypto strategies
    print("\n--- CRYPTO STRATEGIES ---")
    crypto_results = dry_run_crypto(filter_strat)
    all_results.extend(crypto_results)
    for r in crypto_results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"\n[{status}] {r['strat_id']} ({r['name']})")
        for step, msg in r["steps"].items():
            print(f"  {step}: {msg}")

    # FX carry (unless filtering crypto only)
    if not filter_strat or filter_strat == "fx_carry_momentum":
        print("\n--- FX STRATEGIES ---")
        fx_results = dry_run_fx()
        all_results.extend(fx_results)
        for r in fx_results:
            status = "PASS" if r["passed"] else "FAIL"
            print(f"\n[{status}] {r['strat_id']} ({r['name']})")
            for step, msg in r["steps"].items():
                print(f"  {step}: {msg}")

    # Summary
    passed = sum(1 for r in all_results if r["passed"])
    total = len(all_results)
    failed = [r["strat_id"] for r in all_results if not r["passed"]]

    print(f"\n{'=' * 60}")
    print(f"  SUMMARY: {passed}/{total} strategies PASS")
    if failed:
        print(f"  FAILED: {', '.join(failed)}")
    print(f"{'=' * 60}")

    # Persist result
    out_path = ROOT / "data" / "monitoring" / "dry_run_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "timestamp": datetime.now(UTC).isoformat(),
        "passed": passed,
        "total": total,
        "failed": failed,
        "results": all_results,
    }, indent=2, default=str))

    # Alert if any fail
    if failed:
        try:
            from core.telegram_alert import send_alert
            send_alert(
                f"DRY-RUN: {passed}/{total} PASS\n"
                f"FAILED: {', '.join(failed)}",
                level="warning",
            )
        except Exception:
            pass

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
