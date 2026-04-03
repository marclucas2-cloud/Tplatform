"""
Worker Railway — scheduler 24/7, orchestrateur multi-broker.

Cycles : crypto (15min), FX carry (daily), EU/US intraday, futures,
         risk (5min), regime V12 (15min), HRP/Kelly (4h), RoR (daily).

Modules extraits dans core/worker/ pour maintainabilite.
"""
import json
import logging
import os
import signal
import sys
import time
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Setup paths
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "archive" / "intraday-backtesterV2"))
sys.path.insert(0, str(ROOT))

# Charger .env si present (dev local)
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s][%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("worker")

log_dir = ROOT / "logs" / "worker"
log_dir.mkdir(parents=True, exist_ok=True)
file_handler = RotatingFileHandler(
    log_dir / "worker.log", maxBytes=10 * 1024 * 1024, backupCount=5,
)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
))
logging.getLogger().addHandler(file_handler)

# ── Extracted modules ────────────────────────────────────────────────────────
from core.worker.alerts import log_event as _log_event  # noqa: E402
from core.worker.alerts import record_signal_fill as _record_signal_fill  # noqa: E402
from core.worker.alerts import send_alert as _send_alert  # noqa: E402
from core.worker.config import (  # noqa: E402
    CRYPTO_INTERVAL_SECONDS,
    CRYPTO_KELLY_FRACTION,
    ET,
    INTRADAY_INTERVAL_SECONDS,
    LIVE_RISK_INTERVAL_SECONDS,
    PARIS,
    crypto_lock as _crypto_lock,
    execution_lock as _execution_lock,
    ibkr_lock as _ibkr_lock,
    risk_lock as _risk_lock,
)
from core.worker.health import start_health_server as _start_health_server  # noqa: E402
from core.worker.heartbeat import (  # noqa: E402
    check_positions_after_close,
    log_heartbeat,
    reconcile_positions_at_startup,
    telegram_heartbeat_full as _telegram_heartbeat_full,
)
from core.worker.time_windows import (  # noqa: E402
    is_daily_time,
    is_eu_intraday_window,
    is_fx_window,
    is_intraday_window,
    is_live_risk_window,
    is_weekday,
)

# ── R1/R2/R5: Robustesse structurelle ───────────────────────────────────────
from core.worker.cycle_runner import CycleRunner  # noqa: E402
from core.worker.event_logger import get_event_logger  # noqa: E402
from core.worker.worker_state import WorkerState  # noqa: E402
from core.monitoring.metrics_pipeline import get_metrics  # noqa: E402
from core.execution.order_tracker import OrderTracker  # noqa: E402
from core.broker.broker_health import BrokerHealthRegistry  # noqa: E402





# --- Graceful shutdown handler ---
def _handle_sigterm(signum, frame):
    """Graceful shutdown on Railway redeploy — closes positions and cancels orders."""
    logger.critical("SIGTERM received — graceful shutdown initiated")
    _log_event("worker_stop", details={"signal": signum})

    # CRO H-3: Cancel pending orders and close positions before exit
    try:
        # Cancel Alpaca pending orders
        from core.alpaca_client.client import AlpacaClient
        client = AlpacaClient.from_env()
        client.cancel_all_orders()
        logger.info("SIGTERM: Alpaca orders cancelled")
    except Exception as e:
        logger.warning(f"SIGTERM: Alpaca cancel failed: {e}")

    try:
        # Close crypto positions via emergency close
        if os.getenv("BINANCE_API_KEY"):
            from core.broker.binance_broker import BinanceBroker
            bnb = BinanceBroker()
            bnb.close_all_positions(_authorized_by="sigterm_graceful_shutdown")
            logger.info("SIGTERM: Binance positions closed")
    except Exception as e:
        logger.warning(f"SIGTERM: Binance close failed: {e}")

    # Flush metrics and events
    try:
        from core.monitoring.metrics_pipeline import get_metrics
        get_metrics().flush()
    except Exception:
        pass
    try:
        from core.worker.event_logger import get_event_logger
        get_event_logger().close()
    except Exception:
        pass

    _send_alert("Worker SIGTERM — positions closed, shutting down", level="warning")
    raise SystemExit(0)


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)

def run_daily():
    """Execute le portfolio daily (3 strategies)."""
    if not _execution_lock.acquire(blocking=False):
        logger.warning("DAILY RUN SKIP — execution deja en cours (lock)")
        return
    try:
        logger.info("=== DAILY RUN ===")
        _log_event("cycle_start", "daily")
        from scripts.paper_portfolio import run
        now = datetime.now(PARIS)
        force = now.day == 1  # Force rebalance le 1er du mois
        run(dry_run=False, force=force)
        _send_alert(f"DAILY RUN OK — {now.strftime('%H:%M')} CET", level="info")
    except Exception as e:
        logger.error(f"Erreur daily run: {e}", exc_info=True)
        _send_alert(f"DAILY RUN ERREUR: {type(e).__name__}: {str(e)[:100]}", level="critical")
    finally:
        _execution_lock.release()


def run_intraday(market: str = "US"):
    """Execute les strategies intraday.

    Args:
        market: 'US' (default, 15:35-22:00 Paris) or 'EU' (09:00-17:30 Paris)
    """
    # EU uses IBKR live (same as FX carry), US uses Alpaca (execution_lock)
    _lock = _ibkr_lock if market == "EU" else _execution_lock
    if not _lock.acquire(blocking=False):
        logger.warning(f"INTRADAY RUN ({market}) SKIP — lock held")
        return
    try:
        logger.info(f"=== INTRADAY RUN ({market}) ===")
        _log_event("cycle_start", f"intraday_{market}")

        if market == "EU":
            from scripts.paper_portfolio_eu import run_intraday_eu
            run_intraday_eu(dry_run=False)
        else:
            from scripts.paper_portfolio import run_intraday as _pp_run_intraday
            _pp_run_intraday(dry_run=False)

        # Notify Telegram with positions summary after intraday run
        try:
            from core.alpaca_client.client import AlpacaClient
            client = AlpacaClient.from_env()
            positions = client.get_positions()
            if positions:
                pos_lines = [f"  {p['symbol']} {p.get('side','?')} {p.get('qty',0)} PnL=${p.get('unrealized_pl',0):+.1f}" for p in positions[:8]]
                _send_alert(
                    f"INTRADAY {market}: {len(positions)} pos\n" + "\n".join(pos_lines),
                    level="info"
                )
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Erreur intraday run ({market}): {e}", exc_info=True)
        _send_alert(f"INTRADAY {market} ERREUR: {type(e).__name__}: {str(e)[:100]}", level="critical")
    finally:
        _lock.release()


def run_fx_carry_cycle():
    """FX Carry + Momentum Filter — daily rebalance at 10h Paris (mon-fri).

    WF VALIDATED: Sharpe OOS 2.17, 81% windows profitable, MC P5 1.41.
    Pairs: AUD/JPY, USD/JPY, EUR/JPY, NZD/USD (carry + momentum 63d filter).
    Probationary: 15% allocation (1/16 Kelly) for 30 days.
    """
    if not _ibkr_lock.acquire(blocking=False):
        logger.warning("FX CARRY SKIP — IBKR lock held")
        return
    try:
        logger.info("=== FX CARRY CYCLE ===")

        # Check IBKR connection
        ibkr_host = os.getenv("IBKR_HOST", "127.0.0.1")
        ibkr_port = int(os.getenv("IBKR_PORT", "4002"))
        import socket
        try:
            with socket.create_connection((ibkr_host, ibkr_port), timeout=3):
                pass
        except Exception:
            logger.warning("  FX CARRY SKIP — IBKR Gateway not connected")
            return

        # Get IBKR equity
        _ibkr_carry = None
        try:
            from core.broker.ibkr_adapter import IBKRBroker
            _ibkr_carry = IBKRBroker(client_id=10)  # clientId dedie FX carry live
            ibkr_info = _ibkr_carry.get_account_info()
            equity = ibkr_info.get("equity", 0)
        except Exception as e:
            if _ibkr_carry:
                _ibkr_carry.disconnect()
            logger.warning(f"  FX CARRY SKIP — IBKR account info failed: {e}")
            return

        if equity < STRATEGY_CONFIG_FX_CARRY.get("min_capital", 5000):
            logger.info(f"  FX CARRY SKIP — equity ${equity:.0f} < min ${STRATEGY_CONFIG_FX_CARRY['min_capital']}")
            return

        # Refresh daily bars from IBKR before reading parquets
        from pathlib import Path

        import pandas as pd
        data_dir = Path(__file__).resolve().parent / "data" / "fx"
        data_dir.mkdir(parents=True, exist_ok=True)

        for pair in ["AUDJPY", "USDJPY", "EURJPY", "NZDUSD"]:
            try:
                from ib_insync import Forex
                contract = Forex(pair)
                _ibkr_carry._ib.qualifyContracts(contract)
                bars = _ibkr_carry._ib.reqHistoricalData(
                    contract,
                    endDateTime="",
                    durationStr="90 D",
                    barSizeSetting="1 day",
                    whatToShow="MIDPOINT",
                    useRTH=False,
                    formatDate=2,
                )
                if bars:
                    new_df = pd.DataFrame([{
                        "datetime": b.date, "open": b.open, "high": b.high,
                        "low": b.low, "close": b.close, "volume": getattr(b, "volume", 0),
                    } for b in bars])
                    new_df["datetime"] = pd.to_datetime(new_df["datetime"])

                    # Merge with existing parquet
                    fpath = data_dir / f"{pair}_1D.parquet"
                    if fpath.exists():
                        old_df = pd.read_parquet(fpath)
                        old_df["datetime"] = pd.to_datetime(old_df["datetime"])
                        merged = pd.concat([old_df, new_df]).drop_duplicates(
                            subset="datetime", keep="last"
                        ).sort_values("datetime").reset_index(drop=True)
                    else:
                        merged = new_df.sort_values("datetime").reset_index(drop=True)

                    merged.to_parquet(fpath, index=False)
                    logger.info(f"  FX DATA REFRESH: {pair} -> {len(bars)} bars, last={new_df['datetime'].iloc[-1]}")
                else:
                    logger.warning(f"  FX DATA: {pair} no bars returned")
            except Exception as e:
                logger.warning(f"  FX DATA REFRESH {pair} failed: {e}")

        # Load refreshed daily data for each pair
        pair_data = {}
        for pair in ["AUDJPY", "USDJPY", "EURJPY", "NZDUSD"]:
            fpath = data_dir / f"{pair}_1D.parquet"
            if fpath.exists():
                df = pd.read_parquet(fpath)
                df["datetime"] = pd.to_datetime(df["datetime"])
                df = df.set_index("datetime").sort_index()
                pair_data[pair] = df

        if not pair_data:
            logger.warning("  FX CARRY SKIP — no FX daily data available")
            return

        # Run strategy — Carry + Momentum Filter (replaces pure Carry VS)
        from strategies_v2.fx.fx_carry_momentum_filter import FXCarryMomentumFilter
        strat = FXCarryMomentumFilter()

        # Persist kill switch state across cycles (CRO fix: instance recreated each time)
        ks_state_path = Path(__file__).resolve().parent / "data" / "fx" / "carry_mom_ks_state.json"
        try:
            if ks_state_path.exists():
                ks = json.loads(ks_state_path.read_text())
                strat._equity_high = ks.get("equity_high", equity)
                strat._equity_start = ks.get("equity_start", equity)
        except Exception as e:
            logger.warning(f"  FX CARRY: kill switch state load failed: {e}")

        state = {"equity": equity, "i": len(list(pair_data.values())[0])}
        signal = strat.signal_fn(None, state, pair_data=pair_data, equity=equity)

        # Save kill switch state for next cycle
        try:
            ks_state_path.write_text(json.dumps({
                "equity_high": strat._equity_high,
                "equity_start": strat._equity_start,
            }))
        except Exception as e:
            logger.warning(f"  FX CARRY: kill switch state save failed: {e}")

        if signal is None:
            logger.info("  FX CARRY-MOM: pas de signal (momentum negatif ou conditions non remplies)")
            _log_event("signal", "fx_carry_momentum_filter", {"result": "no_signal", "equity": equity})
            return

        if signal.get("action") == "CLOSE_ALL":
            logger.warning(f"  FX CARRY-MOM KILL: {signal.get('reason')} dd={signal.get('drawdown')}")
            _log_event("kill_switch", "fx_carry_momentum_filter", {
                "reason": signal.get("reason"), "drawdown": signal.get("drawdown")
            })
            # Execute kill: close all FX positions
            try:
                _ibkr_carry.close_all_positions(_authorized_by="fx_carry_mom_kill")
                logger.critical("  FX CARRY KILL: all positions closed")
            except Exception as e:
                logger.error(f"  FX CARRY KILL FAILED: {e}")
            _send_alert(
                f"FX CARRY-MOM KILL SWITCH: {signal.get('reason')}\n"
                f"Drawdown: {signal.get('drawdown', 0):.2%}",
                level="critical"
            )
            return

        # === V12 REGIME FILTER ===
        regime_mult = get_v12_regime_multiplier("fx_carry_momentum")
        if regime_mult <= 0:
            logger.warning("  FX CARRY-MOM: BLOCKED by regime engine (mult=0)")
            _log_event("regime_block", "fx_carry_momentum", {"regime_mult": regime_mult})
            return

        # Log signal details
        pairs = signal.get("pairs", [])
        n_filtered = signal.get("n_filtered", 0)
        total = signal.get("total_notional", 0)
        # Apply regime scaling to all pair notionals
        if regime_mult < 1.0:
            for p in pairs:
                p["notional"] = int(p["notional"] * regime_mult)
            total = sum(p["notional"] for p in pairs)
            logger.info(f"  FX CARRY-MOM: regime mult={regime_mult:.1f}, notionals scaled")
        logger.info(f"  FX CARRY-MOM: {len(pairs)} pairs active, {n_filtered} filtered by momentum, total ${total:,.0f}")
        for p in pairs:
            logger.info(
                f"    {p['pair']} {p['direction']} ${p['notional']:,.0f} "
                f"sizing={p['sizing_mult']:.1f}x vol={p['vol_20d']:.1%} "
                f"mom63={p.get('momentum_63d', 0):+.4f} "
                f"SL={p['stop_loss']:.5f} swap={p['swap_daily_bps']}bps/day"
            )

        # Log structured event for each pair signal
        _log_event("signal", "fx_carry_momentum_filter", {
            "n_pairs": len(pairs), "n_filtered": n_filtered,
            "total_notional": total, "equity": equity,
            "pairs": [{"pair": p["pair"], "notional": p["notional"],
                       "sizing": p["sizing_mult"], "momentum": p.get("momentum_63d")}
                      for p in pairs],
        })

        # === LIVE EXECUTION — FX Carry orders via IBKR ===
        # Reconcile: get current positions, only trade deltas
        try:
            current_positions = _ibkr_carry.get_positions()
            current_pairs = {p.get("symbol", ""): p for p in current_positions}
        except Exception as e:
            logger.warning(f"  FX CARRY: cannot get positions: {e}")
            current_pairs = {}

        n_orders = 0
        for p in pairs:
            pair_symbol = p["pair"]  # e.g. "AUDJPY" — _make_contract handles Forex
            direction = p["direction"].upper()  # "BUY" or "SELL"
            target_notional = p["notional"]
            sl = p.get("stop_loss")

            # Skip if already positioned in same direction
            existing = current_pairs.get(pair_symbol)
            if existing:
                existing_qty = float(existing.get("qty", 0))
                if (direction == "BUY" and existing_qty > 0) or (direction == "SELL" and existing_qty < 0):
                    logger.info(f"    {pair_symbol}: already positioned {existing_qty}, skip")
                    continue

            # Cap notional to max per pair from limits_live.yaml
            import yaml as _fx_yaml
            _fx_limits = _fx_yaml.safe_load(
                (ROOT / "config" / "limits_live.yaml").read_text(encoding="utf-8")
            ).get("fx_limits", {})
            max_pair_notional = _fx_limits.get("max_single_pair_notional", 40000)
            notional = min(target_notional, max_pair_notional)

            try:
                _v12_on_signal("fx_carry_momentum", pair_symbol, direction, p.get("entry_price", 0))
                result = _ibkr_carry.create_position(
                    symbol=pair_symbol,
                    direction=direction,
                    notional=notional,
                    stop_loss=sl,
                    _authorized_by="fx_carry_momentum_live",
                )
                n_orders += 1
                _fill_price = float(result.get("filled_price", result.get("avg_price", 0)))
                _fill_qty = float(result.get("filled_qty", 0))
                _v12_on_fill(
                    "IBKR", "fx_carry_momentum", pair_symbol, direction,
                    _fill_qty, _fill_price,
                    order_id=str(result.get("order_id", "")),
                    signal_price=p.get("entry_price", 0),
                )
                logger.info(
                    f"    FX CARRY ORDER: {direction} {pair_symbol} "
                    f"notional=${notional:,.0f} SL={sl} -> {result}"
                )
            except Exception as e:
                logger.error(f"    FX CARRY ORDER FAILED: {pair_symbol} {direction} — {e}")

        # Handle CLOSE_ALL for pairs no longer in signal
        active_pair_symbols = {p["pair"] for p in pairs}
        for sym, pos in current_pairs.items():
            if sym not in active_pair_symbols and abs(float(pos.get("qty", 0))) > 0:
                try:
                    _ibkr_carry.close_position(sym, _authorized_by="fx_carry_momentum_rebalance")
                    logger.info(f"    FX CARRY CLOSE: {sym} (no longer in signal)")
                    n_orders += 1
                except Exception as e:
                    logger.warning(f"    FX CARRY CLOSE FAILED: {sym} — {e}")

        # V12 Signal-to-Fill monitoring (FX carry)
        _record_signal_fill("fx_carry", len(pairs), n_orders, 0)

        _send_alert(
            f"FX CARRY LIVE: {n_orders} ordre(s)\n"
            f"{len(pairs)} pairs actives, {n_filtered} filtrees\n"
            f"Total notional: ${total:,.0f}\n"
            + "\n".join(
                f"  {p['pair']} {p['direction']} ${p['notional']:,.0f} x{p['sizing_mult']:.1f}"
                for p in pairs
            )
            + f"\nEquity: ${equity:,.0f}",
            level="info"
        )

    except Exception as e:
        logger.error(f"FX CARRY CYCLE ERROR: {e}", exc_info=True)
    finally:
        if _ibkr_carry:
            _ibkr_carry.disconnect()
        _ibkr_lock.release()


def run_futures_paper_cycle():
    """Futures Paper Trading — MES Trend + MES/MNQ Pairs on IBKR paper (port 4003).

    WF BORDERLINE: MES Trend Sharpe 1.46, MES/MNQ Pairs Sharpe 0.76.
    Daily frequency, uses data/futures/ parquet files.
    """
    if not _ibkr_lock.acquire(blocking=False):
        logger.warning("FUTURES PAPER SKIP — IBKR lock held")
        return
    try:
        logger.info("=== FUTURES PAPER CYCLE ===")

        # Switch to IBKR paper (port 4003)
        _saved_port = os.environ.get("IBKR_PORT")
        _saved_paper = os.environ.get("IBKR_PAPER")
        os.environ["IBKR_PORT"] = os.environ.get("IBKR_PAPER_PORT", "4003")
        os.environ["IBKR_PAPER"] = "true"

        try:
            from core.broker.factory import _broker_cache
            from core.broker.ibkr_adapter import IBKRBroker
            _broker_cache.pop("ibkr", None)
            import random as _fut_rng
            ibkr = IBKRBroker(client_id=_fut_rng.randint(70, 79))  # random to avoid zombie
            ibkr_info = ibkr.get_account_info()
            equity = float(ibkr_info.get("equity", 0))
        except Exception as e:
            logger.warning(f"  FUTURES PAPER SKIP — IBKR paper not connected: {e}")
            return
        finally:
            if _saved_port is not None:
                os.environ["IBKR_PORT"] = _saved_port
            elif "IBKR_PORT" in os.environ:
                del os.environ["IBKR_PORT"]
            if _saved_paper is not None:
                os.environ["IBKR_PAPER"] = _saved_paper
            elif "IBKR_PAPER" in os.environ:
                del os.environ["IBKR_PAPER"]

        if equity <= 0:
            logger.warning("  FUTURES PAPER SKIP — equity=0")
            return

        logger.info(f"  FUTURES PAPER equity: ${equity:,.0f}")

        # Load futures data
        import pandas as pd
        data_dir = Path(__file__).resolve().parent / "data" / "futures"
        data_sources = {}
        for sym in ["MES", "MNQ"]:
            fpath = data_dir / f"{sym}_1D.parquet"
            if fpath.exists():
                df = pd.read_parquet(fpath)
                df.columns = [c.lower() for c in df.columns]
                if "datetime" in df.columns:
                    df.index = pd.to_datetime(df["datetime"])
                elif not isinstance(df.index, pd.DatetimeIndex):
                    df.index = pd.to_datetime(df.index)
                df = df.sort_index()
                data_sources[sym] = df

        if "MES" not in data_sources:
            logger.warning("  FUTURES PAPER SKIP — no MES daily data")
            return

        # Setup DataFeed
        from core.backtester_v2.data_feed import DataFeed
        from core.backtester_v2.types import PortfolioState
        feed = DataFeed(data_sources)

        # Set timestamp to now (all bars visible)
        now_ts = pd.Timestamp.now(tz="UTC")
        feed.set_timestamp(now_ts)

        portfolio_state = PortfolioState(
            equity=equity, cash=equity, positions={},
        )

        signals = []

        # 1. MES Trend
        try:
            from strategies_v2.futures.mes_trend import MESTrend
            strat = MESTrend()
            strat.set_data_feed(feed)
            bar = feed.get_latest_bar("MES")
            if bar:
                sig = strat.on_bar(bar, portfolio_state)
                if sig:
                    signals.append(("MES Trend", sig))
                    logger.info(f"    MES Trend: {sig.side} MES @ {bar.close:.2f} SL={sig.stop_loss:.2f} TP={sig.take_profit:.2f} str={sig.strength:.2f}")
                else:
                    logger.info("    MES Trend: pas de signal")
            else:
                logger.info("    MES Trend: pas de bar disponible")
        except Exception as e:
            logger.error(f"    MES Trend error: {e}")

        # 2. MES/MNQ Pairs
        if "MNQ" in data_sources:
            try:
                from strategies_v2.futures.mes_mnq_pairs import MESMNQPairs
                strat = MESMNQPairs()
                strat.set_data_feed(feed)
                bar = feed.get_latest_bar("MES")
                if bar:
                    sig = strat.on_bar(bar, portfolio_state)
                    if sig:
                        signals.append(("MES/MNQ Pairs", sig))
                        logger.info(f"    MES/MNQ Pairs: {sig.side} MES @ {bar.close:.2f} z-score signal str={sig.strength:.2f}")
                    else:
                        logger.info("    MES/MNQ Pairs: pas de signal (z-score dans le range)")
                else:
                    logger.info("    MES/MNQ Pairs: pas de bar MES")
            except Exception as e:
                logger.error(f"    MES/MNQ Pairs error: {e}")
        else:
            logger.info("    MES/MNQ Pairs: SKIP — pas de data MNQ")

        logger.info(f"  FUTURES PAPER: {len(signals)} signal(s)")
        _log_event("cycle_end", "futures_paper", {
            "signals": len(signals), "equity": equity,
        })

        # NOTE: Paper mode — signaux logues, pas d'execution
        for name, sig in signals:
            _send_alert(
                f"FUTURES PAPER SIGNAL: {name}\n"
                f"{sig.side} {sig.symbol} SL={sig.stop_loss:.2f} TP={sig.take_profit:.2f}\n"
                f"Strength: {sig.strength:.2f}",
                level="info"
            )

    except Exception as e:
        logger.error(f"FUTURES PAPER CYCLE ERROR: {e}", exc_info=True)
    finally:
        _ibkr_lock.release()


def run_fx_paper_cycle():
    """FX Paper Trading — run validated FX strategies on IBKR paper (port 4003).

    Runs 2 WF-validated strategies:
      - FX Carry Vol-Scaled (Sharpe 3.04, 94% windows profitable)
      - FX Carry Momentum Filter (Sharpe 2.17, 81% windows profitable)

    Uses IBKR paper gateway (~EUR 1M) on port 4003.
    Frequency: every 5 min during EU+US FX hours (09:00-22:00 Paris).
    """
    if not _ibkr_lock.acquire(blocking=False):
        logger.warning("FX PAPER SKIP — IBKR lock held")
        return
    try:
        logger.info("=== FX PAPER CYCLE ===")
        _log_event("cycle_start", "fx_paper")

        # Connect to IBKR paper (port 4003)
        _saved_port = os.environ.get("IBKR_PORT")
        _saved_paper = os.environ.get("IBKR_PAPER")
        os.environ["IBKR_PORT"] = os.environ.get("IBKR_PAPER_PORT", "4003")
        os.environ["IBKR_PAPER"] = "true"

        try:
            # clientId=2 pour eviter conflit avec EU paper (clientId=1) sur port 4003
            import random as _fx_rng

            from core.broker.ibkr_adapter import IBKRBroker
            ibkr = IBKRBroker(client_id=_fx_rng.randint(80, 89))
            ibkr_info = ibkr.get_account_info()
            equity = float(ibkr_info.get("equity", 0))
        except Exception as e:
            logger.warning(f"  FX PAPER SKIP — IBKR paper not connected: {e}")
            return
        finally:
            # Restore env vars
            if _saved_port is not None:
                os.environ["IBKR_PORT"] = _saved_port
            elif "IBKR_PORT" in os.environ:
                del os.environ["IBKR_PORT"]
            if _saved_paper is not None:
                os.environ["IBKR_PAPER"] = _saved_paper
            elif "IBKR_PAPER" in os.environ:
                del os.environ["IBKR_PAPER"]

        if equity <= 0:
            logger.warning("  FX PAPER SKIP — equity=0")
            return

        logger.info(f"  FX PAPER equity: ${equity:,.0f} (paper)")

        # Load FX daily data
        import pandas as pd
        data_dir = Path(__file__).resolve().parent / "data" / "fx"
        pair_data = {}
        for pair in ["AUDJPY", "USDJPY", "EURJPY", "NZDUSD"]:
            fpath = data_dir / f"{pair}_1D.parquet"
            if fpath.exists():
                df = pd.read_parquet(fpath)
                df["datetime"] = pd.to_datetime(df["datetime"])
                df = df.set_index("datetime").sort_index()
                pair_data[pair] = df

        if not pair_data:
            logger.warning("  FX PAPER SKIP — no FX daily data")
            return

        # === Strategy 1: Carry Vol-Scaled ===
        signals_summary = []
        try:
            from strategies_v2.fx.fx_carry_vol_scaled import FXCarryVolScaled
            strat1 = FXCarryVolScaled()
            state1 = {"equity": equity, "i": len(list(pair_data.values())[0])}
            sig1 = strat1.signal_fn(None, state1, pair_data=pair_data, equity=equity)
            if sig1 and sig1.get("action") != "CLOSE_ALL":
                pairs1 = sig1.get("pairs", [])
                signals_summary.append(f"CarryVS: {len(pairs1)} pairs, ${sig1.get('total_notional', 0):,.0f}")
                for p in pairs1:
                    logger.info(f"    VS {p['pair']} {p['direction']} ${p['notional']:,.0f} x{p['sizing_mult']:.1f}")
            elif sig1 and sig1.get("action") == "CLOSE_ALL":
                signals_summary.append(f"CarryVS: KILL {sig1.get('reason')}")
            else:
                signals_summary.append("CarryVS: no signal")
        except Exception as e:
            logger.error(f"  FX PAPER CarryVS error: {e}")
            signals_summary.append(f"CarryVS: ERROR {e}")

        # === Strategy 2: Carry Momentum Filter ===
        try:
            from strategies_v2.fx.fx_carry_momentum_filter import FXCarryMomentumFilter
            strat2 = FXCarryMomentumFilter()
            state2 = {"equity": equity, "i": len(list(pair_data.values())[0])}
            sig2 = strat2.signal_fn(None, state2, pair_data=pair_data, equity=equity)
            if sig2 and sig2.get("action") != "CLOSE_ALL":
                pairs2 = sig2.get("pairs", [])
                n_filt = sig2.get("n_filtered", 0)
                signals_summary.append(f"CarryMom: {len(pairs2)} pairs ({n_filt} filtered), ${sig2.get('total_notional', 0):,.0f}")
                for p in pairs2:
                    logger.info(f"    MOM {p['pair']} {p['direction']} ${p['notional']:,.0f} x{p['sizing_mult']:.1f} mom={p.get('momentum_63d', 0):+.4f}")
            elif sig2 and sig2.get("action") == "CLOSE_ALL":
                signals_summary.append(f"CarryMom: KILL {sig2.get('reason')}")
            else:
                signals_summary.append("CarryMom: no signal")
        except Exception as e:
            logger.error(f"  FX PAPER CarryMom error: {e}")
            signals_summary.append(f"CarryMom: ERROR {e}")

        # Log + Telegram
        summary = " | ".join(signals_summary)
        logger.info(f"  FX PAPER: {summary}")
        _log_event("signal", "fx_paper", {"summary": summary, "equity": equity})

        # TODO: execute orders on IBKR paper when ready
        # For now, signal-only mode (log + Telegram)
        _send_alert(
            f"FX PAPER SIGNAL\nEquity: ${equity:,.0f}\n" + "\n".join(signals_summary),
            level="info"
        )

    except Exception as e:
        logger.error(f"FX PAPER CYCLE ERROR: {e}", exc_info=True)
    finally:
        _ibkr_lock.release()


# FX Carry+Momentum config — reads from limits_live.yaml at import time
def _load_fx_carry_config():
    import yaml
    try:
        cfg = yaml.safe_load((ROOT / "config" / "limits_live.yaml").read_text(encoding="utf-8"))
        return {
            "min_capital": cfg.get("capital", 10_000) // 2,  # 50% of total capital
            "allocation_pct": 0.15,
        }
    except Exception:
        return {"min_capital": 5000, "allocation_pct": 0.15}

STRATEGY_CONFIG_FX_CARRY = _load_fx_carry_config()


def run_live_risk_cycle():
    """Poll live risk checks every 5 minutes — circuit breakers, kill switches, deleveraging."""
    if not _risk_lock.acquire(blocking=False):
        logger.warning("SKIP live risk cycle — previous risk check still running")
        return
    try:
        from core.kill_switch_live import LiveKillSwitch
        from core.risk_manager_live import LiveRiskManager

        risk_mgr = LiveRiskManager()

        # Build portfolio snapshot from IBKR (or skip if not connected)
        # For now, use a lightweight check that doesn't require full TradingEngine
        portfolio = {"equity": risk_mgr.capital, "positions": [], "cash": risk_mgr.capital}

        try:
            # Try to get real portfolio from IBKR (always try, not gated by env var)
            import socket
            _ibkr_host = os.getenv("IBKR_HOST", "127.0.0.1")
            _ibkr_port = int(os.getenv("IBKR_PORT", "4002"))
            with socket.create_connection((_ibkr_host, _ibkr_port), timeout=2):
                pass
            from core.broker.ibkr_adapter import IBKRBroker
            _risk_ibkr = IBKRBroker(client_id=3)
            try:
                account = _risk_ibkr.get_account_info()
                positions = _risk_ibkr.get_positions()
                portfolio = {
                    "equity": float(account.get("equity", risk_mgr.capital)),
                    "cash": float(account.get("cash", risk_mgr.capital)),
                    "positions": positions,
                    "margin_used_pct": float(account.get("margin_used_pct", 0)),
                }
            finally:
                _risk_ibkr.disconnect()
        except Exception as e:
            logger.info(f"Live risk cycle: IBKR unavailable ({e}), using config capital")

        # FIX: update risk manager capital from live equity
        equity_live = portfolio.get("equity", risk_mgr.capital)
        if equity_live > 0:
            risk_mgr.update_capital(equity_live)

        # PnL calculation using actual daily starting equity.
        # FIX: use dedicated file (not paper_portfolio_state.json which is paper)
        equity = portfolio.get("equity", risk_mgr.capital)
        daily_start_equity = equity  # fallback
        _live_dd_path = ROOT / "data" / "live_risk_dd_state.json"
        try:
            _live_dd_path.parent.mkdir(parents=True, exist_ok=True)
            if _live_dd_path.exists():
                _ldd = json.loads(_live_dd_path.read_text(encoding="utf-8"))
                _saved_eq = _ldd.get("daily_start_equity")
                _saved_date = _ldd.get("date", "")
                today_str = datetime.now(UTC).strftime("%Y-%m-%d")
                if _saved_date == today_str and _saved_eq and float(_saved_eq) > 0:
                    daily_start_equity = float(_saved_eq)
                else:
                    # New day or first check — set baseline
                    _live_dd_path.write_text(json.dumps({
                        "daily_start_equity": equity,
                        "date": today_str,
                    }))
                    daily_start_equity = equity
            else:
                _live_dd_path.write_text(json.dumps({
                    "daily_start_equity": equity,
                    "date": datetime.now(UTC).strftime("%Y-%m-%d"),
                }))
        except Exception as _e:
            logger.warning(f"Could not load live daily_start_equity: {_e}")

        daily_pnl_pct = (equity - daily_start_equity) / daily_start_equity if daily_start_equity > 0 else 0

        # Run all risk checks
        risk_result = risk_mgr.check_all_limits(
            portfolio=portfolio,
            daily_pnl_pct=daily_pnl_pct,
            margin_used_pct=portfolio.get("margin_used_pct", 0),
        )

        if not risk_result["passed"]:
            logger.critical(f"LIVE RISK CHECK FAILED: {risk_result['blocked_reason']}")
            logger.critical(f"Actions required: {risk_result['actions']}")
            _log_event("error", "live_risk_cycle", {
                "reason": risk_result["blocked_reason"],
                "actions": risk_result["actions"],
                "daily_pnl_pct": round(daily_pnl_pct, 4),
            })

            # Send alert
            _send_alert(
                f"LIVE RISK ALERT\n"
                f"Reason: {risk_result['blocked_reason']}\n"
                f"Actions: {', '.join(risk_result['actions'])}",
                level="critical"
            )

        # Check kill switch triggers
        kill_switch = LiveKillSwitch()
        ks_result = kill_switch.check_automatic_triggers(
            daily_pnl=daily_pnl_pct * risk_mgr.capital,
            capital=risk_mgr.capital,
        )

        if ks_result["triggered"]:
            logger.critical(f"KILL SWITCH TRIGGERED: {ks_result['reason']}")
            _log_event("kill_switch", "live_risk_cycle", {
                "reason": ks_result["reason"],
                "trigger_type": ks_result["trigger_type"],
            })
            kill_switch.activate(
                reason=ks_result["reason"],
                trigger_type=ks_result["trigger_type"],
            )
            # Arm crypto kill switch too (prevent re-entry on next crypto cycle)
            try:
                from core.crypto.risk_manager_crypto import CryptoKillSwitch
                CryptoKillSwitch()._activate(f"live_kill_{ks_result['reason']}")
                logger.critical("Crypto kill switch armed (prevent re-entry)")
            except Exception:
                pass

            # Close ALL positions on ALL brokers
            _send_alert(
                f"KILL SWITCH LIVE: {ks_result['reason']}\nClosing all positions...",
                level="critical",
            )
            if _v12_emergency_close:
                try:
                    _v12_emergency_close.execute(force=True)
                except Exception as _ec_err:
                    logger.critical(f"Emergency close failed: {_ec_err}")
            else:
                try:
                    from core.broker.ibkr_adapter import IBKRBroker
                    with IBKRBroker(client_id=3) as _ks_ibkr:
                        _ks_ibkr.close_all_positions(_authorized_by="kill_switch_live")
                except Exception as _ks_err:
                    logger.critical(f"Kill switch IBKR close failed: {_ks_err}")

        # Log deleveraging level
        delev = risk_result.get("deleveraging", {})
        if delev.get("level", 0) > 0:
            logger.warning(f"DELEVERAGING LEVEL {delev['level']}: {delev['message']}")

        # --- FIX M-9: Auto-deleverage L2+ (reduce largest position by 50%) ---
        actions = risk_result.get("actions", [])
        if any(a in actions for a in ("DELEVERAGE_L2", "DELEVERAGE_L3")):
            try:
                positions = portfolio.get("positions", [])
                if positions:
                    # Find the largest position by market_val (absolute)
                    largest = max(positions, key=lambda p: abs(float(p.get("market_val", 0))))
                    symbol = largest.get("symbol", "UNKNOWN")
                    qty = abs(float(largest.get("qty", 0)))
                    half_qty = qty / 2.0

                    if half_qty > 0:
                        # Check IBKR connectivity via socket (IBKR_CONNECTED env never set)
                        import socket as _delev_sock
                        _delev_host = os.getenv("IBKR_HOST", "127.0.0.1")
                        _delev_port = int(os.getenv("IBKR_PORT", "4002"))
                        with _delev_sock.create_connection((_delev_host, _delev_port), timeout=3):
                            pass
                        from core.broker.ibkr_adapter import IBKRBroker
                        broker = IBKRBroker(client_id=3)
                        deleverage_action = "DELEVERAGE_L3" if "DELEVERAGE_L3" in actions else "DELEVERAGE_L2"
                        logger.critical(
                            f"AUTO-DELEVERAGE {deleverage_action}: reducing {symbol} "
                            f"by 50% (qty {qty} -> {qty - half_qty})"
                        )
                        broker.close_position(
                            symbol, qty=half_qty,
                            _authorized_by=f"auto_deleverage_{deleverage_action}",
                        )
                        logger.critical(
                            f"AUTO-DELEVERAGE {deleverage_action} EXECUTED: "
                            f"{symbol} reduced by {half_qty} units"
                        )
                        _send_alert(
                            f"AUTO-DELEVERAGE {deleverage_action}\n"
                            f"Position: {symbol}\n"
                            f"Reduced by 50%: {half_qty} units",
                            level="critical"
                        )
                    else:
                        logger.warning("Auto-deleverage skipped — IBKR not connected or qty=0")
            except Exception as e:
                logger.error(f"Auto-deleverage failed: {e}", exc_info=True)

        # --- SAFE-003 : LivePerformanceGuard (auto-disable strats) ---
        try:
            from core.live_performance_guard import ALERT, DISABLE, LivePerformanceGuard
            guard = LivePerformanceGuard()
            state = json.loads((ROOT / "data" / "state" / "paper_portfolio_state.json").read_text(encoding="utf-8")) if (ROOT / "data" / "state" / "paper_portfolio_state.json").exists() else {}
            pnl_log = state.get("strategy_pnl_log", {})
            for strat_id, entries in pnl_log.items():
                trades = [{"pnl": e.get("pnl", 0)} for e in entries]
                if len(trades) >= 10:
                    action, reason = guard.evaluate(strat_id, trades)
                    if action == DISABLE:
                        logger.critical(f"SAFE-003 AUTO-DISABLE: {strat_id} — {reason}")
                    elif action == ALERT:
                        logger.warning(f"SAFE-003 ALERT: {strat_id} — {reason}")
        except Exception as e:
            logger.warning(f"LivePerformanceGuard skip: {e}")

        # --- VIX/SPY stress guard (sizing reduction) ---
        try:
            from core.vix_stress_guard import VixStressGuard
            vix_guard = VixStressGuard()
            stress = vix_guard.check()
            if stress["level"] != "NORMAL":
                logger.warning(f"VIX STRESS: {stress['level']} — sizing {stress['sizing_factor']:.0%} — {stress['reason']}")
        except Exception as e:
            logger.warning(f"VixStressGuard skip: {e}")

        logger.info(f"Live risk cycle OK — equity=${equity:,.0f}, daily_pnl={daily_pnl_pct:.2%}")

    except Exception as e:
        logger.error(f"Live risk cycle error: {e}", exc_info=True)
    finally:
        _risk_lock.release()


def _enrich_crypto_kwargs(
    kwargs, strat_id, config, broker, primary_symbol, positions, equity, pd
):
    """Enrich kwargs for each crypto strategy based on its specific needs.

    This fills in the missing data that each signal_fn expects:
    - Temporal flags (rebalance day, sunday, month-end)
    - Borrow rates
    - Multi-timeframe data
    - External data (BTC dominance, funding rates)
    - Ratio/multi-asset data
    """
    now_utc = datetime.now(UTC)

    # --- Temporal flags ---
    is_sunday = now_utc.weekday() == 6
    is_friday = now_utc.weekday() == 4
    day_of_month = now_utc.day
    is_month_end = day_of_month >= 27 or day_of_month <= 3
    is_rebalance_day = is_sunday  # Weekly strategies rebalance on Sunday

    kwargs["is_rebalance_day"] = is_rebalance_day
    kwargs["is_sunday_evening"] = is_sunday and now_utc.hour >= 20
    kwargs["current_asset"] = primary_symbol

    # --- Borrow rates (for margin strategies) ---
    if config.get("market_type") == "margin" and broker:
        try:
            # Fetch current borrow rate for primary asset
            asset = primary_symbol.replace("USDT", "").replace("USDC", "")
            margin_info = broker._get("/sapi/v1/margin/asset", {"asset": asset}, signed=True)
            if margin_info:
                daily_rate = float(margin_info.get("marginRatio", 0.0003))
                kwargs["borrow_rate"] = daily_rate
                kwargs["borrow_rate_eth"] = daily_rate  # Approximate
                kwargs["borrow_rate_btc"] = daily_rate * 0.6  # BTC cheaper
        except Exception:
            kwargs["borrow_rate"] = 0.0003  # Default 0.03%/day

    # --- Multi-timeframe (STRAT-003 needs 4h for regime) ---
    if strat_id == "STRAT-003" and broker:
        try:
            data_4h = broker.get_prices(primary_symbol, timeframe="4h", bars=100)
            bars_4h = data_4h.get("bars", [])
            if bars_4h:
                df_4h = pd.DataFrame(bars_4h)
                df_4h.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}, inplace=True)
                kwargs["df_4h"] = df_4h
        except Exception:
            pass

    # --- Altcoin RS (STRAT-002): multi-asset 90d daily returns ---
    if strat_id == "STRAT-002" and broker and is_rebalance_day:
        try:
            alt_symbols = config.get("symbols", ["BTCUSDT"])
            all_returns = {}
            volumes_data = {}
            # Fetch 100d daily close for each alt + BTC
            symbols_to_fetch = list(set(alt_symbols[:10] + ["BTCUSDT"]))
            for sym in symbols_to_fetch:
                try:
                    price_data = broker.get_prices(sym, timeframe="1d", bars=100)
                    bars = price_data.get("bars", [])
                    if len(bars) >= 30:
                        closes = pd.Series([float(b["c"]) for b in bars])
                        all_returns[sym] = closes.pct_change().dropna()
                    # 24h volume
                    ticker = broker.get_ticker_24h(sym)
                    volumes_data[sym] = float(ticker.get("volume", ticker.get("quoteVolume", 0)))
                except Exception:
                    pass
            btc_ret = all_returns.pop("BTCUSDT", None)
            if all_returns and btc_ret is not None:
                # Align all series to same length
                min_len = min(len(s) for s in all_returns.values())
                min_len = min(min_len, len(btc_ret))
                returns_df = pd.DataFrame({
                    sym: s.values[-min_len:] for sym, s in all_returns.items()
                })
                kwargs["returns_df"] = returns_df
                kwargs["btc_returns"] = pd.Series(btc_ret.values[-min_len:])
                kwargs["volumes_24h"] = volumes_data
                kwargs["market_caps"] = {}
                kwargs["borrow_rates"] = {}
                kwargs["borrow_available"] = {s: True for s in alt_symbols}
                logger.info(f"  [{strat_id}] Enriched: {len(all_returns)} alts, {min_len} days")
        except Exception as e:
            logger.debug(f"  [{strat_id}] Altcoin data fetch failed: {e}")

    # --- BTC Dominance (STRAT-005): dominance series + alt returns ---
    if strat_id == "STRAT-005" and broker and is_rebalance_day:
        try:
            # Fetch BTC dominance from CoinGecko
            import requests as _req
            dom_resp = _req.get(
                "https://api.coingecko.com/api/v3/global", timeout=10
            )
            dominance = 0.60
            if dom_resp.ok:
                dominance = dom_resp.json().get("data", {}).get(
                    "market_cap_percentage", {}
                ).get("btc", 60) / 100
            kwargs["dominance_series"] = pd.Series([dominance] * 30)
            # Fetch 14d returns for top performer candidates
            # Binance France: use USDC pairs (USDT blocked for trading)
            candidates = ["ETHUSDC", "SOLUSDC", "BNBUSDC", "ADAUSDC",
                          "XRPUSDC", "DOTUSDC", "AVAXUSDC"]
            returns_data = {}
            for sym in candidates:
                try:
                    data = broker.get_prices(sym, timeframe="1d", bars=20)
                    bars = data.get("bars", [])
                    if len(bars) >= 14:
                        closes = [float(b["c"]) for b in bars]
                        ret_14d = (closes[-1] / closes[-14] - 1) if closes[-14] > 0 else 0
                        returns_data[sym] = ret_14d
                except Exception:
                    pass
            kwargs["returns_data"] = returns_data
            logger.info(f"  [{strat_id}] Enriched: dominance={dominance:.1%}, {len(returns_data)} candidates")
        except Exception as e:
            logger.debug(f"  [{strat_id}] Dominance fetch failed: {e}")

    # --- Earn APY (STRAT-006) ---
    if strat_id == "STRAT-006":
        kwargs["usdt_apy"] = 0.05  # 5% default
        kwargs["btc_apy"] = 0.01
        kwargs["eth_apy"] = 0.01
        kwargs["current_earn_allocations"] = {}
        kwargs["last_rebalance_ts"] = None
        kwargs["previous_scenario"] = None
        if broker:
            try:
                earn_positions = broker.get_earn_positions()
                kwargs["current_earn_allocations"] = {
                    ep.get("asset", ""): float(ep.get("amount", 0))
                    for ep in earn_positions
                }
            except Exception:
                pass

    # --- Liquidation Momentum (STRAT-007): futures OI read-only ---
    if strat_id == "STRAT-007" and broker:
        try:
            # Read futures OI from Binance (read-only, no position)
            import requests as _req
            oi_resp = _req.get(
                "https://fapi.binance.com/fapi/v1/openInterest",
                params={"symbol": primary_symbol}, timeout=5,
            )
            if oi_resp.ok:
                oi_val = float(oi_resp.json().get("openInterest", 0))
                kwargs["oi_change_4h"] = 0.0  # Need history to compute delta
                kwargs["volume_ratio"] = 1.0
                kwargs["bars_since_peak"] = 10
                kwargs["trades_this_week"] = 0
                kwargs["funding_rate"] = 0.0
                kwargs["price_change_4h"] = 0.0
        except Exception:
            pass

    # --- Weekend Gap (STRAT-008) ---
    if strat_id == "STRAT-008":
        kwargs["traded_this_weekend"] = False
        if broker and is_friday and now_utc.hour >= 22:
            try:
                ticker = broker.get_ticker_24h(primary_symbol)
                kwargs["friday_close_price"] = float(
                    ticker.get("last_price", ticker.get("lastPrice", 0))
                )
            except Exception:
                pass
        # Persist friday price in state file for Sunday use
        _friday_price_path = ROOT / "data" / "friday_close_price.json"
        if is_friday and now_utc.hour >= 22 and "friday_close_price" in kwargs:
            try:
                _friday_price_path.write_text(
                    json.dumps({"price": kwargs["friday_close_price"], "ts": now_utc.isoformat()}),
                    encoding="utf-8",
                )
            except Exception:
                pass
        elif is_sunday:
            try:
                if _friday_price_path.exists():
                    _fp = json.loads(_friday_price_path.read_text(encoding="utf-8"))
                    kwargs["friday_close_price"] = _fp.get("price", 0)
            except Exception:
                pass

    # --- Funding Rate Divergence (STRAT-009) ---
    if strat_id == "STRAT-009" and broker:
        try:
            import requests as _req
            fr_resp = _req.get(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                params={"symbol": primary_symbol, "limit": 10}, timeout=5,
            )
            if fr_resp.ok:
                rates = [float(r.get("fundingRate", 0)) for r in fr_resp.json()]
                kwargs["funding_history"] = rates
                kwargs["current_funding"] = rates[-1] if rates else 0.0
                kwargs["entry_direction"] = None
        except Exception:
            pass

    # --- Stablecoin Supply (STRAT-010) ---
    if strat_id == "STRAT-010" and is_rebalance_day:
        try:
            import requests as _req
            # CoinGecko free API for stablecoin mcap
            resp = _req.get(
                "https://api.coingecko.com/api/v3/coins/tether/market_chart",
                params={"vs_currency": "usd", "days": 7}, timeout=10,
            )
            if resp.ok:
                mcaps = resp.json().get("market_caps", [])
                values = [m[1] for m in mcaps]
                kwargs["stablecoin_supply_series"] = pd.Series(values)
                # Daily prices for BTC
                if "df_full" in kwargs and kwargs["df_full"] is not None:
                    kwargs["daily_prices"] = kwargs["df_full"]["close"]
        except Exception:
            pass

    # --- ETH/BTC Ratio (STRAT-011) ---
    if strat_id == "STRAT-011" and broker:
        try:
            eth_data = broker.get_prices("ETHUSDC", timeframe="4h", bars=200)
            btc_data = broker.get_prices("BTCUSDC", timeframe="4h", bars=200)
            eth_bars = eth_data.get("bars", [])
            btc_bars = btc_data.get("bars", [])
            if eth_bars and btc_bars:
                min_len = min(len(eth_bars), len(btc_bars))
                eth_close = [float(b["c"]) for b in eth_bars[-min_len:]]
                btc_close = [float(b["c"]) for b in btc_bars[-min_len:]]
                ratio = [e / b if b > 0 else 0 for e, b in zip(eth_close, btc_close)]
                vol_eth = [float(b["v"]) for b in eth_bars[-min_len:]]
                vol_btc = [float(b["v"]) for b in btc_bars[-min_len:]]
                kwargs["df_ratio"] = pd.DataFrame({
                    "ratio": ratio, "vol_eth": vol_eth, "vol_btc": vol_btc,
                })
                kwargs["trade_direction"] = None
        except Exception:
            pass

    # --- Monthly Turn-of-Month (STRAT-012): just needs df_full 1d ---
    if strat_id == "STRAT-012" and broker:
        try:
            daily_data = broker.get_prices(primary_symbol, timeframe="1d", bars=40)
            daily_bars = daily_data.get("bars", [])
            if daily_bars:
                df_daily = pd.DataFrame(daily_bars)
                df_daily.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}, inplace=True)
                kwargs["df_full"] = df_daily  # Override with daily timeframe
        except Exception:
            pass


def _execute_earn_signal(broker, strat_id, strat_name, signal, n_orders_ref):
    """Execute un signal EARN_REBALANCE/SUBSCRIBE/REDEEM via BinanceBroker."""
    action = signal.get("action")
    target_weights = signal.get("target_weights", {})
    capital = signal.get("capital_allocated", 0)

    if action == "EARN_REBALANCE" and target_weights:
        # Get current earn positions
        earn_positions = broker.get_earn_positions()
        earn_map = {ep["asset"]: ep for ep in earn_positions}

        # Get earn product IDs
        earn_rates = broker.get_earn_rates()
        product_map = {r["asset"]: r["product_id"] for r in earn_rates}

        for asset, target_pct in target_weights.items():
            target_amount = capital * target_pct
            current = float(earn_map.get(asset, {}).get("amount", 0))
            product_id = product_map.get(asset)

            if not product_id:
                logger.debug(f"  [{strat_id}] No Earn product for {asset}")
                continue

            diff = target_amount - current
            if abs(diff) < 1.0:  # Skip tiny rebalances < $1
                continue

            if diff > 0:
                # Subscribe more
                try:
                    result = broker.subscribe_earn(product_id, round(diff, 4))
                    logger.info(
                        f"  [{strat_id}] EARN SUBSCRIBE {asset}: +${diff:.2f} "
                        f"(target=${target_amount:.0f}) — {result.get('success', result)}"
                    )
                    _send_alert(
                        f"EARN SUBSCRIBE {asset}\n"
                        f"Strat: {strat_name}\n"
                        f"+${diff:.2f} (target ${target_amount:.0f})",
                        level="info",
                    )
                except Exception as e:
                    logger.warning(f"  [{strat_id}] Earn subscribe {asset} failed: {e}")
            else:
                # Redeem excess
                redeem_amount = abs(diff)
                try:
                    result = broker.redeem_earn(product_id, round(redeem_amount, 4))
                    logger.info(
                        f"  [{strat_id}] EARN REDEEM {asset}: -${redeem_amount:.2f} "
                        f"— {result.get('success', result)}"
                    )
                    _send_alert(
                        f"EARN REDEEM {asset}\n"
                        f"Strat: {strat_name}\n"
                        f"-${redeem_amount:.2f}",
                        level="info",
                    )
                except Exception as e:
                    logger.warning(f"  [{strat_id}] Earn redeem {asset} failed: {e}")

    elif action == "EARN_SUBSCRIBE":
        asset = signal.get("asset", "USDC")
        amount = signal.get("amount", 0)
        earn_rates = broker.get_earn_rates()
        product_id = next((r["product_id"] for r in earn_rates if r["asset"] == asset), None)
        if product_id and amount > 1:
            result = broker.subscribe_earn(product_id, round(amount, 4))
            logger.info(f"  [{strat_id}] EARN SUBSCRIBE {asset}: ${amount:.2f} — {result}")

    elif action == "EARN_REDEEM":
        asset = signal.get("asset", "USDC")
        amount = signal.get("amount")
        earn_positions = broker.get_earn_positions()
        product_id = next((ep["product_id"] for ep in earn_positions if ep["asset"] == asset), None)
        if product_id:
            result = broker.redeem_earn(product_id, amount)
            logger.info(f"  [{strat_id}] EARN REDEEM {asset}: {amount or 'ALL'} — {result}")

    logger.info(f"  [{strat_id}] Earn signal {action} processed")


def _log_strategy_debug(strat_id, config, df_full, broker, primary_symbol):
    """Log indicateur values pour debug quand pas de signal."""
    if df_full is None or df_full.empty:
        logger.debug(f"  [{strat_id}] DEBUG: no df_full")
        return
    try:
        close = float(df_full.iloc[-1]["close"])
        # Simple moving averages
        if len(df_full) >= 50:
            ema20 = float(df_full["close"].ewm(span=20).mean().iloc[-1])
            ema50 = float(df_full["close"].ewm(span=50).mean().iloc[-1])
            rsi_delta = df_full["close"].diff()
            gain = rsi_delta.clip(lower=0).rolling(14).mean().iloc[-1]
            loss = (-rsi_delta.clip(upper=0)).rolling(14).mean().iloc[-1]
            rsi = 100 - 100 / (1 + gain / loss) if loss > 0 else 50
            vol_ratio = float(df_full["volume"].iloc[-1] / df_full["volume"].rolling(20).mean().iloc[-1]) if df_full["volume"].rolling(20).mean().iloc[-1] > 0 else 0
            logger.info(
                f"  [{strat_id}] DEBUG: price={close:.0f} EMA20={ema20:.0f} "
                f"EMA50={ema50:.0f} RSI={rsi:.1f} vol_ratio={vol_ratio:.2f} "
                f"trend={'UP' if ema20 > ema50 else 'DOWN'}"
            )
        else:
            logger.debug(f"  [{strat_id}] DEBUG: only {len(df_full)} bars")
    except Exception as e:
        logger.warning(f"  [{strat_id}] strategy debug error: {e}")


def run_crypto_cycle():
    """Execute le cycle crypto : 8 strategies Binance, 24/7, toutes les 15 min.

    Charge les 8 strategies depuis strategies.crypto, genere les signaux,
    passe par CryptoRiskManager + CryptoKillSwitch, route vers BinanceBroker.
    """
    if not _crypto_lock.acquire(blocking=False):
        logger.warning("CRYPTO CYCLE SKIP — execution deja en cours (lock)")
        return
    try:
        # FIX CRO H-5 : check trading_paused_until before any crypto activity
        try:
            _pause_state_path = ROOT / "data" / "state" / "paper_portfolio_state.json"
            if _pause_state_path.exists():
                _pause_state = json.loads(
                    _pause_state_path.read_text(encoding="utf-8")
                )
                _paused_until = _pause_state.get("trading_paused_until")
                if _paused_until:
                    _pause_dt = datetime.fromisoformat(_paused_until)
                    if datetime.now(UTC) < _pause_dt:
                        logger.warning(
                            f"CRYPTO CYCLE SKIP — trading paused until "
                            f"{_paused_until}"
                        )
                        return
        except Exception as _pe:
            logger.warning(f"Could not check trading_paused_until: {_pe}")

        logger.info("=== CRYPTO CYCLE ===")

        # --- Verifier que Binance est configure ---
        if not os.getenv("BINANCE_API_KEY"):
            logger.debug("Crypto cycle skip — BINANCE_API_KEY non configuree")
            return

        # CRO B-3: guard BINANCE_LIVE_CONFIRMED for live trading
        if os.getenv("BINANCE_TESTNET", "true").lower() != "true":
            if os.getenv("BINANCE_LIVE_CONFIRMED") != "true":
                logger.critical("CRYPTO CYCLE BLOCKED — BINANCE_LIVE_CONFIRMED not set for live")
                return

        # --- Charger la config d'allocation ---
        import yaml
        alloc_path = ROOT / "config" / "crypto_allocation.yaml"
        crypto_config = {}
        if alloc_path.exists():
            try:
                crypto_config = yaml.safe_load(
                    alloc_path.read_text(encoding="utf-8")
                ).get("crypto_allocation", {})
            except Exception as e:
                logger.error(f"Erreur lecture crypto_allocation.yaml: {e}")

        total_capital = crypto_config.get("total_capital", 10_000)

        # --- Importer les 8 strategies ---
        try:
            from strategies.crypto import CRYPTO_STRATEGIES
        except Exception as e:
            logger.error(f"Erreur import strategies crypto: {e}", exc_info=True)
            return

        if not CRYPTO_STRATEGIES:
            logger.warning("Aucune strategie crypto chargee — skip")
            return

        # --- Initialiser le risk manager + kill switch ---
        try:
            from core.crypto.risk_manager_crypto import CryptoRiskManager
        except Exception as e:
            logger.error(f"Erreur import CryptoRiskManager: {e}", exc_info=True)
            return

        risk_mgr = CryptoRiskManager(capital=total_capital)

        # --- Verifier le kill switch AVANT tout trade ---
        # CRO H-8: verifier l'etat persiste du kill switch (pas les triggers
        # dynamiques — ceux-la sont verifies dans check_all() plus bas)
        if risk_mgr.kill_switch._active:
            kill_reason = risk_mgr.kill_switch._trigger_reason or "previously activated"

            # Auto-reset kill switch after 24h — a perpetual kill switch is a bug
            # (the original trigger was likely a false positive from stale baselines)
            _ks_age_h = 0
            if risk_mgr.kill_switch._trigger_time:
                _ks_age_h = (datetime.now(UTC) - risk_mgr.kill_switch._trigger_time).total_seconds() / 3600
            if _ks_age_h > 24:
                logger.warning(
                    f"CRYPTO KILL SWITCH AUTO-RESET: active {_ks_age_h:.0f}h "
                    f"(>{24}h) — reason was: {kill_reason}"
                )
                risk_mgr.kill_switch._active = False
                risk_mgr.kill_switch._save_persisted_state()
                _send_alert(
                    f"KILL SWITCH AUTO-RESET ({_ks_age_h:.0f}h old)\n"
                    f"Reason was: {kill_reason}\n"
                    f"Trading crypto reprend.",
                    level="warning",
                )
            else:
                logger.critical(
                    f"CRYPTO KILL SWITCH ACTIF ({_ks_age_h:.0f}h) — aucun trade: {kill_reason}"
                )
                _send_alert(
                    f"CRYPTO KILL SWITCH: {kill_reason}", level="critical"
                )
                return

        # --- Initialiser le broker Binance ---
        broker = None
        try:
            from core.broker.binance_broker import BinanceBroker
            broker = BinanceBroker()
        except Exception as e:
            logger.error(
                f"Binance broker init echoue — signaux seront logues "
                f"mais pas executes: {e}"
            )

        # --- Recuperer les positions et l'equity pour le risk check ---
        positions = []
        current_equity = total_capital
        cash_available = 0
        earn_total = 0
        acct = {}              # FIX: initialize to avoid NameError if get_account_info() fails
        earn_positions = []    # FIX: initialize to avoid NameError if broker block fails
        if broker:
            try:
                acct = broker.get_account_info()
                # Use spot_total_usd (excludes Earn) to avoid double-counting
                # since get_account_info().equity now includes Earn
                spot_equity = float(acct.get("spot_total_usd", acct.get("equity", 0)))
                cash_available = float(acct.get("cash", 0))
                positions = broker.get_positions()

                # Inclure les positions Earn dans l'equity totale
                # (LDBTC, LDUSDC, LDETH = Earn Flexible, pas dans equity spot)
                earn_positions = []
                try:
                    earn_positions = broker.get_earn_positions()
                    for ep in earn_positions:
                        asset = ep.get("asset", "")
                        amount = float(ep.get("amount", 0))
                        if amount > 0:
                            # Estimer la valeur USD de chaque earn position
                            if asset in ("USDT", "USDC", "BUSD"):
                                earn_total += amount
                            elif asset == "BTC":
                                try:
                                    btc_ticker = broker.get_ticker_24h("BTCUSDC")
                                    btc_price = float(btc_ticker.get("last_price", 0))
                                    earn_total += amount * btc_price
                                except Exception:
                                    logger.warning("BTC earn price unavailable — using last known")
                            elif asset == "ETH":
                                try:
                                    eth_ticker = broker.get_ticker_24h("ETHUSDC")
                                    eth_price = float(eth_ticker.get("last_price", 0))
                                    earn_total += amount * eth_price
                                except Exception:
                                    logger.warning("ETH earn price unavailable — using last known")
                except Exception as e:
                    logger.warning(f"Earn positions indisponibles: {e}")

                current_equity = spot_equity + earn_total

                # Séparer earn volatile (BTC/ETH) vs earn stable (USDC)
                stable_earn = sum(
                    float(ep.get("amount", 0))
                    for ep in earn_positions
                    if ep.get("asset") in ("USDT", "USDC", "BUSD")
                ) if earn_positions else 0
                volatile_earn = earn_total - stable_earn  # BTC/ETH earn en USD

                # DD equity = tout SAUF earn BTC/ETH (fluctuation passive)
                # Si BTC -10%, earn BTC perd $800 mais aucune strat n'a tradé
                dd_equity = current_equity - volatile_earn

                cash_available = float(acct.get("cash", 0)) + spot_equity + stable_earn

                logger.info(
                    f"  Equity: spot=${spot_equity:,.0f} + earn=${earn_total:,.0f} "
                    f"= total=${current_equity:,.0f} "
                    f"(dd_equity=${dd_equity:,.0f}, volatile_earn=${volatile_earn:,.0f})"
                )
            except Exception as e:
                logger.warning(f"Binance account info indisponible: {e}")

        # --- Recaler capital sur l'equity reelle ---
        # dd_equity exclut earn BTC/ETH volatile (pas un trade)
        dd_equity = dd_equity if dd_equity > 0 else current_equity
        if current_equity > 0:
            risk_mgr.capital = dd_equity
        # Le CryptoRiskManager est recree a chaque cycle avec capital=20K,
        # donc _daily_start_equity = 20K. Mais l'equity reelle peut etre
        # differente (earn fluctue, BTC prix change). On persiste l'etat
        # drawdown entre les cycles via un fichier JSON.
        _crypto_dd_path = ROOT / "data" / "crypto_dd_state.json"
        # Baselines DD utilisent dd_equity (excl earn BTC/ETH volatile)
        try:
            if _crypto_dd_path.exists():
                _dd = json.loads(_crypto_dd_path.read_text(encoding="utf-8"))
                risk_mgr._peak_equity = _dd.get("peak_equity", dd_equity)
                risk_mgr._daily_start_equity = _dd.get("daily_start", dd_equity)
                risk_mgr._hourly_start_equity = _dd.get("hourly_start", dd_equity)
                risk_mgr._weekly_start_equity = _dd.get("weekly_start", dd_equity)
                risk_mgr._monthly_start_equity = _dd.get("monthly_start", dd_equity)
                risk_mgr._last_hourly_reset = _dd.get("last_hourly_reset", time.time())
                risk_mgr._check_count = _dd.get("warmup_count", 0)

                # Auto-reset daily/weekly/monthly si le jour/semaine/mois a change
                last_date = _dd.get("last_date", "")
                today_str = datetime.now(UTC).strftime("%Y-%m-%d")
                if last_date != today_str:
                    risk_mgr._daily_start_equity = dd_equity
                    logger.info(f"  Crypto DD: daily reset {last_date} -> {today_str}, start=${dd_equity:,.0f}")

                last_week = _dd.get("last_week", "")
                this_week = datetime.now(UTC).strftime("%Y-W%W")
                if last_week != this_week:
                    risk_mgr._weekly_start_equity = dd_equity

                last_month = _dd.get("last_month", "")
                this_month = datetime.now(UTC).strftime("%Y-%m")
                if last_month != this_month:
                    risk_mgr._monthly_start_equity = dd_equity
            else:
                risk_mgr._peak_equity = dd_equity
                risk_mgr._daily_start_equity = dd_equity
                risk_mgr._hourly_start_equity = dd_equity
                risk_mgr._weekly_start_equity = dd_equity
                risk_mgr._monthly_start_equity = dd_equity
        except Exception as _dde:
            logger.warning(f"Could not load crypto drawdown state: {_dde}")
            risk_mgr._peak_equity = dd_equity
            risk_mgr._daily_start_equity = dd_equity
            risk_mgr._hourly_start_equity = dd_equity
            risk_mgr._weekly_start_equity = dd_equity
            risk_mgr._monthly_start_equity = dd_equity

        # --- Auto-redeem Earn Flexible si cash spot insuffisant pour trader ---
        # Les strats ont besoin de cash spot/margin pour executer.
        # Si cash spot < min_trading_cash et USDC en Earn Flexible > seuil,
        # redeem automatiquement pour liberer du capital.
        # Thresholds from config (wallets.cash = reserve, default 10% of capital)
        MIN_TRADING_CASH = crypto_config.get("wallets", {}).get("cash", int(total_capital * 0.05))
        spot_cash = float(acct.get("cash", 0)) if broker else 0
        if broker and spot_cash < MIN_TRADING_CASH and earn_positions:
            usdc_earn = next(
                (ep for ep in earn_positions if ep.get("asset") == "USDC"),
                None,
            )
            if usdc_earn:
                usdc_amount = float(usdc_earn.get("amount", 0))
                usdc_product = usdc_earn.get("product_id")
                # Redeem enough to have 2x MIN_TRADING_CASH (keep rest earning)
                redeem_target = MIN_TRADING_CASH * 2
                redeem_amount = min(usdc_amount, redeem_target)
                if redeem_amount >= 50 and usdc_product:
                    try:
                        broker.redeem_earn(usdc_product, round(redeem_amount, 2))
                        spot_cash += redeem_amount
                        logger.info(
                            f"  AUTO-REDEEM: ${redeem_amount:,.0f} USDC from Earn -> spot "
                            f"(was ${spot_cash - redeem_amount:.0f}, now ${spot_cash:.0f})"
                        )
                        _send_alert(
                            f"AUTO-REDEEM: ${redeem_amount:,.0f} USDC Earn -> spot\n"
                            f"Raison: cash spot < ${MIN_TRADING_CASH} pour trading",
                            level="info",
                        )
                    except Exception as e:
                        logger.warning(f"  AUTO-REDEEM failed: {e}")

        # --- Risk check global avant signaux ---
        # DD basé sur dd_equity (excl earn BTC/ETH) pour ne pas kill
        # les strats quand BTC baisse sans qu'on ait tradé
        risk_result = risk_mgr.check_all(
            positions=positions,
            current_equity=dd_equity,
            cash_available=cash_available,
            earn_total=earn_total,
        )

        # Persister l'etat drawdown pour le prochain cycle
        try:
            _crypto_dd_path.parent.mkdir(parents=True, exist_ok=True)
            _now_utc = datetime.now(UTC)
            _crypto_dd_path.write_text(json.dumps({
                "peak_equity": risk_mgr._peak_equity,
                "daily_start": risk_mgr._daily_start_equity,
                "hourly_start": risk_mgr._hourly_start_equity,
                "weekly_start": risk_mgr._weekly_start_equity,
                "monthly_start": risk_mgr._monthly_start_equity,
                "last_hourly_reset": risk_mgr._last_hourly_reset,
                "warmup_count": risk_mgr._check_count,
                "last_date": _now_utc.strftime("%Y-%m-%d"),
                "last_week": _now_utc.strftime("%Y-W%W"),
                "last_month": _now_utc.strftime("%Y-%m"),
                "last_updated": _now_utc.isoformat(),
            }))
        except Exception as _dd_err:
            # CRO H-3: drawdown state persist is critical — circuit breakers depend on it
            logger.critical(f"DRAWDOWN STATE PERSIST FAILED: {_dd_err}")
            _send_alert(f"DRAWDOWN PERSIST FAILED: {_dd_err}", level="critical")

        if not risk_result["passed"]:
            failed_checks = [
                name for name, c in risk_result["checks"].items()
                if not c["passed"]
            ]
            logger.warning(
                f"CRYPTO RISK CHECK FAILED ({len(failed_checks)} checks): "
                f"{failed_checks}"
            )

        # --- Boucle sur les 8 strategies ---
        import pandas as pd

        n_signals = 0
        n_orders = 0      # Real BUY/SELL trades only
        n_actions = 0     # All actions (earn, close dust, trades)
        n_errors = 0
        # CRO M-4: track signals per symbol to detect conflicts
        _cycle_signals: dict[str, list[str]] = {}  # symbol -> [strat_id:side]

        for strat_id, strat_data in CRYPTO_STRATEGIES.items():
            config = strat_data["config"]
            signal_fn = strat_data["signal_fn"]
            strat_name = config.get("name", strat_id)

            try:
                # Construire le candle minimal (dernier prix) et le state
                # Chaque strategie recoit un candle pd.Series et un state dict
                candle_data = {"close": 0, "open": 0, "high": 0, "low": 0,
                               "volume": 0, "timestamp": datetime.now(
                                   UTC).isoformat()}

                # Tenter de recuperer le dernier prix via Binance
                # FIX: Binance France TRD_GRP_002 bloque les paires USDT.
                # On utilise USDC comme quote currency (meme prix, autorise).
                primary_symbol = config.get("symbols", ["BTCUSDT"])[0]
                # Map USDT→USDC pour data + execution
                trade_symbol = primary_symbol.replace("USDT", "USDC") if primary_symbol.endswith("USDT") else primary_symbol
                df_full = None
                # Skip price fetch for earn strategies (symbols are assets, not pairs)
                market_type = config.get("market_type", "spot")
                if broker and market_type != "earn" and (trade_symbol.endswith("USDC") or trade_symbol.endswith("USDT")):
                    try:
                        timeframe = config.get("timeframe", "4h")
                        price_data = broker.get_prices(
                            trade_symbol, timeframe=timeframe, bars=100
                        )
                        bars = price_data.get("bars", [])
                        if bars:
                            last_bar = bars[-1]
                            candle_data = {
                                "close": last_bar["c"],
                                "open": last_bar["o"],
                                "high": last_bar["h"],
                                "low": last_bar["l"],
                                "volume": last_bar["v"],
                                "timestamp": datetime.now(
                                    UTC
                                ).isoformat(),
                            }
                            # Construire df_full pour les strategies qui en ont besoin
                            df_full = pd.DataFrame(bars)
                            df_full.rename(columns={
                                "o": "open", "h": "high", "l": "low",
                                "c": "close", "v": "volume",
                            }, inplace=True)
                            # CRO M-3: warm-up check — EMA50 needs 50+ bars
                            if len(df_full) < 50:
                                logger.warning(
                                    f"  [{strat_id}] only {len(df_full)} bars "
                                    f"(need 50+ for EMA warm-up) — signal may be unreliable"
                                )
                    except Exception as e:
                        logger.warning(
                            f"  [{strat_id}] Impossible de recuperer "
                            f"les prix {primary_symbol}: {e}"
                        )

                candle = pd.Series(candle_data)

                # State avec capital alloue (1/8 Kelly SOFT_LAUNCH)
                # FIX: utiliser l'equity LIVE, pas le capital statique du YAML
                sizing_capital = current_equity if current_equity > 0 else total_capital
                alloc_pct = config.get("allocation_pct", 0.10)
                strat_capital = sizing_capital * alloc_pct * CRYPTO_KELLY_FRACTION
                state = {
                    "capital": sizing_capital,
                    "equity": current_equity,
                    "positions": positions,
                    "i": len(df_full) - 1 if df_full is not None and not df_full.empty else 0,
                }

                # Kwargs enrichis par strategie (fix: chaque strat a des besoins specifiques)
                kwargs = {}
                if df_full is not None:
                    kwargs["df_full"] = df_full
                kwargs["symbol"] = primary_symbol

                # --- Enrichissement kwargs par type de besoin ---
                try:
                    _enrich_crypto_kwargs(
                        kwargs, strat_id, config, broker, primary_symbol,
                        positions, current_equity, pd,
                    )
                except Exception as _enrich_err:
                    logger.warning(f"  [{strat_id}] kwargs enrich partial: {_enrich_err}")

                # --- Appel du signal_fn (FIX CRO H: per-strategy timeout 30s) ---
                _signal_result = [None]
                _signal_error = [None]
                def _run_signal(_fn=signal_fn, _c=candle, _s=state, _kw=kwargs):
                    try:
                        _signal_result[0] = _fn(_c, _s, **_kw)
                    except Exception as _e:
                        _signal_error[0] = _e
                _t = threading.Thread(target=_run_signal, daemon=True)
                _t.start()
                _t.join(timeout=30)
                if _t.is_alive():
                    logger.critical(
                        f"  [{strat_id}] signal_fn TIMEOUT (30s) — skipping"
                    )
                    n_errors += 1
                    continue
                if _signal_error[0] is not None:
                    raise _signal_error[0]
                signal = _signal_result[0]

                # --- Log du signal (meme si None) ---
                if signal is None:
                    logger.info(
                        f"  [{strat_id}] {strat_name}: pas de signal"
                    )
                    _log_strategy_debug(strat_id, config, df_full, broker, primary_symbol)
                    continue

                n_signals += 1
                action = signal.get("action", "UNKNOWN")

                # === V12 REGIME FILTER (crypto) ===
                _crypto_regime_mult = get_v12_regime_multiplier(strat_id)
                if _crypto_regime_mult <= 0 and action not in (
                    "EARN_REBALANCE", "EARN_SUBSCRIBE", "EARN_REDEEM", "CAPITAL_RELEASE", "CLOSE",
                ):
                    logger.info(f"  [{strat_id}] BLOCKED by regime (mult=0)")
                    _log_event("regime_block", strat_id, {"regime_mult": 0})
                    continue
                if 0 < _crypto_regime_mult < 1.0 and signal.get("quantity"):
                    signal["quantity"] = signal["quantity"] * _crypto_regime_mult
                    logger.info(f"  [{strat_id}] regime scale: qty x{_crypto_regime_mult:.1f}")

                logger.info(
                    f"  [{strat_id}] {strat_name}: SIGNAL {action} "
                    f"— {json.dumps({k: v for k, v in signal.items() if k != 'df_full'}, default=str)}"
                )
                _log_event("signal", strat_id, {
                    "action": action,
                    "symbol": signal.get("symbol", trade_symbol),
                })

                # --- Executer via BinanceBroker si disponible ---
                if broker is None:
                    logger.info(
                        f"  [{strat_id}] Signal logue mais pas execute "
                        f"(broker indisponible)"
                    )
                    continue

                # Determiner la direction et le market_type
                market_type = config.get("market_type", "spot")

                # --- Signaux EARN : pas de risque directionnel, toujours autorises ---
                if action in ("EARN_REBALANCE", "EARN_SUBSCRIBE", "EARN_REDEEM",
                              "CAPITAL_RELEASE"):
                    if broker is None:
                        logger.info(f"  [{strat_id}] Earn signal skip (broker indisponible)")
                        continue
                    try:
                        _execute_earn_signal(broker, strat_id, strat_name, signal, n_actions)
                        n_actions += 1
                    except Exception as e:
                        logger.error(f"  [{strat_id}] Earn execution error: {e}")
                        n_errors += 1
                    continue

                # Pour les signaux CLOSE
                if action == "CLOSE":
                    try:
                        result = broker.close_position(
                            trade_symbol,
                            _authorized_by=f"crypto_worker_{strat_id}",
                        )
                        logger.info(
                            f"  [{strat_id}] Position fermee: {result}"
                        )
                        n_actions += 1
                    except Exception as e:
                        logger.error(
                            f"  [{strat_id}] Erreur close: {e}"
                        )
                        n_errors += 1
                    continue

                # --- Risk check pour trades directionnels (BUY/SELL) ---
                if not risk_result["passed"]:
                    logger.warning(
                        f"  [{strat_id}] Signal {action} ignore — risk check non passe"
                    )
                    continue

                # Pour BUY/SELL — calculer le sizing
                direction = signal.get("direction", action)
                if direction not in ("BUY", "SELL", "LONG", "SHORT"):
                    logger.info(
                        f"  [{strat_id}] Action {action} non routee "
                        f"(direction inconnue)"
                    )
                    continue

                # Mapping direction -> Binance side
                side = "BUY" if direction in ("BUY", "LONG") else "SELL"

                # Sizing : capital alloue * 1/8 Kelly
                price = candle_data.get("close", 0)
                if price <= 0:
                    logger.warning(
                        f"  [{strat_id}] Prix nul — ordre non place"
                    )
                    continue

                notional = strat_capital
                stop_loss = signal.get("stop_loss")

                # CRO: JAMAIS d'ordre sans stop-loss sur des positions directionnelles
                if stop_loss is None and market_type != "earn":
                    # Calculer un SL par defaut a -5% du prix
                    default_sl_pct = 0.05
                    if side == "BUY":
                        stop_loss = round(price * (1 - default_sl_pct), 2)
                    else:
                        stop_loss = round(price * (1 + default_sl_pct), 2)
                    logger.warning(
                        f"  [{strat_id}] Signal SANS stop_loss — SL par defaut "
                        f"applique a {default_sl_pct*100:.0f}%: ${stop_loss:.2f}"
                    )

                # CRO C-3 FIX: validate_order AVANT create_position
                try:
                    order_valid, order_msg = risk_mgr.validate_order(
                        notional=notional,
                        strategy=strat_id,
                        current_equity=current_equity,
                    )
                    if not order_valid:
                        logger.warning(
                            f"  [{strat_id}] Ordre REFUSE par risk manager: {order_msg}"
                        )
                        continue
                except Exception as risk_err:
                    logger.warning(f"  [{strat_id}] Risk validate ERREUR: {risk_err}")
                    n_errors += 1

                # Map signal symbol to USDC pair for execution
                exec_symbol = signal.get("symbol", trade_symbol)
                if exec_symbol.endswith("USDT"):
                    exec_symbol = exec_symbol.replace("USDT", "USDC")

                _v12_on_signal(strat_id, exec_symbol, side, price)

                # CRO M-4: conflict detection — BUY+SELL same symbol in same cycle
                _cycle_signals.setdefault(exec_symbol, []).append(f"{strat_id}:{side}")
                _sym_sides = [s.split(":")[1] for s in _cycle_signals[exec_symbol]]
                if "BUY" in _sym_sides and "SELL" in _sym_sides:
                    logger.warning(
                        f"CONFLICT: {exec_symbol} has BUY+SELL in same cycle: "
                        f"{_cycle_signals[exec_symbol]}"
                    )

                # Compute qty for SELL with proper step size rounding
                _sell_qty = None
                if side == "SELL" and price > 0:
                    raw_qty = notional / price
                    # BTC pairs: step 0.00001 (5 dec), altcoins: step varies
                    if "BTC" in exec_symbol:
                        _sell_qty = float(f"{raw_qty:.5f}")  # 5 decimals
                    elif "ETH" in exec_symbol:
                        _sell_qty = float(f"{raw_qty:.4f}")  # 4 decimals
                    else:
                        _sell_qty = float(f"{raw_qty:.3f}")  # 3 decimals for alts

                try:
                    result = broker.create_position(
                        symbol=exec_symbol,
                        direction=side,
                        notional=notional if side == "BUY" else None,
                        qty=_sell_qty,
                        stop_loss=stop_loss,
                        market_type=market_type,
                        _authorized_by=f"crypto_worker_{strat_id}",
                    )
                    logger.info(
                        f"  [{strat_id}] ORDRE EXECUTE: {side} "
                        f"${notional:.0f} {signal.get('symbol', primary_symbol)} "
                        f"— {result.get('status', '???')}"
                    )
                    n_orders += 1

                    # Post-execution verification: check filled qty
                    filled = float(result.get("filled_qty", 0))
                    _fill_price = float(result.get("filled_price", price))
                    if filled <= 0:
                        logger.warning(
                            f"  [{strat_id}] POST-EXEC: order status={result.get('status')} "
                            f"but filled_qty=0 — possible partial/unfilled"
                        )

                    # CRO #1: verify SL order exists 2s after fill
                    _sl_id = result.get("sl_order_id")
                    if _sl_id and filled > 0 and broker:
                        time.sleep(2)
                        _sl_ok = broker.verify_sl_exists(exec_symbol, _sl_id)
                        if not _sl_ok:
                            logger.critical(
                                f"  [{strat_id}] SL MISSING after fill — "
                                f"sl_order_id={_sl_id} not on exchange"
                            )
                            _send_alert(
                                f"SL MISSING: {strat_id} {exec_symbol}\n"
                                f"sl_order_id={_sl_id} not found post-fill",
                                level="critical",
                            )

                    # CRO #6: slippage verification
                    if _fill_price > 0 and price > 0:
                        _slippage_pct = abs(_fill_price - price) / price * 100
                        if _slippage_pct > 1.0:
                            logger.warning(
                                f"  [{strat_id}] SLIPPAGE {_slippage_pct:.2f}%: "
                                f"signal=${price:,.0f} fill=${_fill_price:,.0f}"
                            )
                            _send_alert(
                                f"SLIPPAGE {_slippage_pct:.2f}%: {strat_id} {exec_symbol}\n"
                                f"Signal: ${price:,.0f} | Fill: ${_fill_price:,.0f}",
                                level="warning",
                            )
                        elif _slippage_pct > 0.1:
                            logger.info(
                                f"  [{strat_id}] slippage {_slippage_pct:.3f}%: "
                                f"${price:,.0f} -> ${_fill_price:,.0f}"
                            )

                    # V12: post-fill hooks (double-fill, shadow, tax)
                    _v12_on_fill(
                        "BINANCE", strat_id, exec_symbol, side,
                        filled, _fill_price,
                        order_id=str(result.get("order_id", "")),
                        signal_price=price,
                    )

                    _log_event("fill", strat_id, {
                        "side": side, "symbol": signal.get("symbol", primary_symbol),
                        "notional": notional, "status": result.get("status"),
                        "filled_qty": filled,
                        "filled_price": result.get("filled_price"),
                    })
                    # Telegram V2: trade notification
                    try:
                        from core.telegram_v2 import tg
                        tg.trade_entry(
                            side=side,
                            symbol=exec_symbol,
                            qty=filled,
                            price=_fill_price,
                            sl=stop_loss or 0,
                            strat=strat_id,
                            broker="Binance",
                            notional=notional,
                        )
                    except Exception:
                        pass
                except Exception as e:
                    logger.error(
                        f"  [{strat_id}] Erreur execution: {e}",
                        exc_info=True,
                    )
                    n_errors += 1
                    _log_event("error", strat_id, {
                        "type": type(e).__name__, "message": str(e)[:200],
                    })
                    _send_alert(
                        f"ERREUR EXECUTION {strat_name}\n{type(e).__name__}: {e}",
                        level="critical",
                    )

            except Exception as e:
                # Une strategie qui plante ne doit pas bloquer les autres
                logger.error(
                    f"  [{strat_id}] ERREUR STRATEGIE: {e}", exc_info=True
                )
                n_errors += 1

        logger.info(
            f"=== CRYPTO CYCLE TERMINE: {n_signals} signal(s), "
            f"{n_orders} trade(s), {n_actions} action(s), {n_errors} erreur(s) ==="
        )

        # --- V12 Signal-to-Fill monitoring ---
        _record_signal_fill("crypto", n_signals, n_orders, n_errors)

        # --- Telegram recap ---
        if n_orders > 0:
            _send_alert(
                f"CRYPTO TRADE: {n_orders} ordre(s)\n"
                f"Signals: {n_signals} | Actions: {n_actions}\n"
                f"Equity: ${current_equity:,.0f}",
                level="info",
            )
        if n_errors > 0:
            _send_alert(
                f"CRYPTO ERREUR: {n_errors} erreur(s)\n"
                f"Equity: ${current_equity:,.0f}",
                level="warning",
            )

        # --- V12 Live Tracker: feed per-strategy P&L as return ---
        if _v12_live_tracker and dd_equity > 0:
            try:
                # Only feed strategies that actually traded this cycle
                for _sid in CRYPTO_STRATEGIES:
                    _strat_pnl = sum(
                        float(p.get("unrealized_pl", 0))
                        for p in positions
                        if p.get("strategy") == _sid
                    )
                    if abs(_strat_pnl) > 0 and dd_equity > 0:
                        _v12_live_tracker.add_return(_sid, _strat_pnl / dd_equity)
            except Exception as e:
                logger.debug(f"V12 live tracker feed: {e}")

        # --- Cash Sweep: USDC idle -> Earn Flexible ---
        # FIX V12: was using undefined `bnb_info`/`bnb` — use `acct`/`broker`
        if broker:
            try:
                _sweep_cash = float(acct.get("cash", 0)) if acct else 0
                # Sweep if cash > 2x MIN_TRADING_CASH, keep MIN_TRADING_CASH as buffer
                if _sweep_cash > MIN_TRADING_CASH * 2:
                    sweep_amount = _sweep_cash - MIN_TRADING_CASH
                    logger.info(f"  CASH SWEEP: ${_sweep_cash:,.0f} USDC idle, sweeping ${sweep_amount:,.0f} -> Earn")
                    try:
                        _sweep_earn = broker.get_earn_positions()
                        usdc_product_id = None
                        for ep in _sweep_earn:
                            if ep.get("asset") == "USDC":
                                usdc_product_id = ep.get("product_id")
                                break
                        if usdc_product_id:
                            broker.subscribe_earn(usdc_product_id, sweep_amount)
                            logger.info(f"  CASH SWEEP OK: ${sweep_amount:,.0f} USDC -> Earn (product={usdc_product_id})")
                        else:
                            logger.info("  CASH SWEEP: pas de product_id USDC Earn trouve")
                    except Exception as e:
                        logger.warning(f"  CASH SWEEP FAILED: {e}")
            except Exception as e:
                logger.warning(f"  CASH SWEEP error: {e}")

    except Exception as e:
        logger.error(f"Erreur critique crypto cycle: {e}", exc_info=True)
        _send_alert(
            f"CRYPTO CYCLE ERREUR CRITIQUE: {e}", level="critical"
        )
    finally:
        _crypto_lock.release()


# =====================================================================
# V10 — Portfolio-Aware Risk Engine
# =====================================================================

# V10 module instances (initialized lazily in main)
_v10 = {}
V10_CYCLE_INTERVAL = 300  # 5 minutes


def _init_v10_modules():
    """Initialize V10 portfolio-aware risk modules."""
    try:
        from core.execution.execution_monitor import ExecutionMonitor
        from core.portfolio.live_logger import LiveSnapshotLogger
        from core.portfolio.portfolio_state import PortfolioStateEngine
        from core.risk.effective_risk import EffectiveRiskExposure
        from core.risk.leverage_adapter import LeverageAdapter
        from core.risk.live_correlation_engine import LiveCorrelationEngine
        from core.risk.risk_budget_allocator import RiskBudgetAllocator
        from core.risk.safety_mode import SafetyMode
        from core.risk.strategy_throttler import StrategyThrottler

        corr_engine = LiveCorrelationEngine(data_dir=str(ROOT / "data"))
        ere_calc = EffectiveRiskExposure(correlation_engine=corr_engine)
        budget_alloc = RiskBudgetAllocator(correlation_engine=corr_engine)
        lev_adapter = LeverageAdapter(
            correlation_engine=corr_engine, ere_calculator=ere_calc
        )
        throttler = StrategyThrottler(correlation_engine=corr_engine)
        safety = SafetyMode(data_dir=str(ROOT / "data"))
        exec_monitor = ExecutionMonitor(data_dir=str(ROOT / "data"))

        # SmartRouter for multi-broker portfolio state
        smart_router = None
        try:
            from core.broker.factory import SmartRouter
            smart_router = SmartRouter()
        except Exception as e:
            logger.warning(f"V10: SmartRouter unavailable: {e}")

        portfolio_engine = PortfolioStateEngine(
            smart_router=smart_router,
            ere_calculator=ere_calc,
            correlation_engine=corr_engine,
            data_dir=str(ROOT / "data"),
        )

        snapshot_logger = LiveSnapshotLogger(
            portfolio_engine=portfolio_engine,
            correlation_engine=corr_engine,
            ere_calculator=ere_calc,
            execution_monitor=exec_monitor,
            log_dir=str(ROOT / "logs" / "portfolio"),
        )

        _v10.update({
            "correlation_engine": corr_engine,
            "ere_calculator": ere_calc,
            "risk_budget_allocator": budget_alloc,
            "leverage_adapter": lev_adapter,
            "strategy_throttler": throttler,
            "safety_mode": safety,
            "execution_monitor": exec_monitor,
            "portfolio_engine": portfolio_engine,
            "snapshot_logger": snapshot_logger,
        })

        logger.info(
            f"V10 modules initialized: {len(_v10)} components, "
            f"safety_mode={'ON' if safety.is_active else 'OFF'}"
        )
        return True

    except Exception as e:
        logger.error(f"V10 init failed (non-blocking): {e}", exc_info=True)
        return False


# =====================================================================
# V11 — Data Quality, HRP, Kelly, Orphan Detection
# =====================================================================

_v11_data_quality = None
_v11_kelly = None
_v11_hrp = None
_v11_throttler_eu = None

# === V12 MODULES ===
_v12_regime_scheduler = None
_v12_double_fill = None
_v12_shadow_logger = None
_v12_live_tracker = None
_v12_tax_classifier = None
_v12_unified_portfolio = None
_v12_cross_corr = None
_v12_emergency_close = None


def _init_v12_modules():
    """Initialize ALL V12 modules."""
    global _v12_regime_scheduler, _v12_double_fill, _v12_shadow_logger
    global _v12_live_tracker, _v12_tax_classifier, _v12_unified_portfolio
    global _v12_cross_corr, _v12_emergency_close

    # Regime Engine
    try:
        from core.regime.regime_scheduler import RegimeScheduler
        _v12_regime_scheduler = RegimeScheduler(alert_callback=_send_alert)
        logger.info("  V12 RegimeScheduler initialized")
    except Exception as e:
        logger.warning(f"  V12 RegimeScheduler failed: {e}")

    # Double-Fill Detector
    try:
        from core.execution.double_fill_detector import DoubleFillDetector

        def _double_fill_close(ticker, side, quantity, broker, reason=""):
            """Auto-close excess position from double fill."""
            try:
                if broker == "BINANCE" and os.getenv("BINANCE_API_KEY"):
                    from core.broker.binance_broker import BinanceBroker
                    BinanceBroker().close_position(ticker, _authorized_by="double_fill_close")
                elif broker == "IBKR":
                    from core.broker.ibkr_adapter import IBKRBroker
                    with IBKRBroker(client_id=8) as ibkr:
                        ibkr.close_position(ticker, _authorized_by="double_fill_close")
                elif broker == "ALPACA":
                    from core.alpaca_client.client import AlpacaClient
                    AlpacaClient.from_env().close_position(ticker, _authorized_by="double_fill_close")
                logger.critical(f"DOUBLE FILL AUTO-CLOSE: {ticker} {side} {quantity} on {broker}")
            except Exception as e:
                logger.critical(f"DOUBLE FILL AUTO-CLOSE FAILED: {ticker} — {e}")

        _v12_double_fill = DoubleFillDetector(
            alert_callback=_send_alert,
            close_callback=_double_fill_close,
        )
        logger.info("  V12 DoubleFillDetector initialized")
    except Exception as e:
        logger.warning(f"  V12 DoubleFillDetector failed: {e}")

    # Shadow Trade Logger
    try:
        from core.validation.shadow_logger import ShadowTradeLogger
        _v12_shadow_logger = ShadowTradeLogger(alert_callback=_send_alert)
        logger.info("  V12 ShadowTradeLogger initialized")
    except Exception as e:
        logger.warning(f"  V12 ShadowTradeLogger failed: {e}")

    # Live Performance Tracker
    try:
        from core.validation.live_tracker import LivePerformanceTracker
        def _tracker_kill(strategy_id, reason):
            logger.critical(f"LIVE TRACKER KILL: {strategy_id} — {reason}")
            _send_alert(f"ALPHA DECAY KILL: {strategy_id}\n{reason}", level="critical")

        _v12_live_tracker = LivePerformanceTracker(
            alert_callback=_send_alert,
            kill_callback=_tracker_kill,
        )
        logger.info("  V12 LivePerformanceTracker initialized")
    except Exception as e:
        logger.warning(f"  V12 LivePerformanceTracker failed: {e}")

    # Tax Classifier
    try:
        from core.tax.trade_classifier import TradeTaxClassifier
        _v12_tax_classifier = TradeTaxClassifier(alert_callback=_send_alert)
        logger.info("  V12 TradeTaxClassifier initialized")
    except Exception as e:
        logger.warning(f"  V12 TradeTaxClassifier failed: {e}")

    # Unified Portfolio View (cross-broker)
    try:
        from core.risk.unified_portfolio import UnifiedPortfolioView
        _v12_unified_portfolio = UnifiedPortfolioView(
            alert_callback=_send_alert,
        )
        logger.info("  V12 UnifiedPortfolioView initialized")
    except Exception as e:
        logger.warning(f"  V12 UnifiedPortfolioView failed: {e}")

    # Cross-Asset Correlation Monitor
    try:
        from core.risk.cross_asset_correlation import CrossAssetCorrelationMonitor
        _v12_cross_corr = CrossAssetCorrelationMonitor()
        logger.info("  V12 CrossAssetCorrelationMonitor initialized")
    except Exception as e:
        logger.warning(f"  V12 CrossAssetCorrelationMonitor failed: {e}")

    # Emergency Close All (brokers set later when connected)
    try:
        from core.risk.emergency_close_all import EmergencyCloseAll
        # Wire up all available brokers for emergency close
        _ec_brokers = {}
        try:
            if os.getenv("BINANCE_API_KEY"):
                from core.broker.binance_broker import BinanceBroker
                _ec_brokers["BINANCE"] = BinanceBroker()
        except Exception:
            pass
        try:
            import socket as _ec_sock

            from core.broker.ibkr_adapter import IBKRBroker
            _ec_host = os.getenv("IBKR_HOST", "127.0.0.1")
            _ec_port = int(os.getenv("IBKR_PORT", "4002"))
            with _ec_sock.create_connection((_ec_host, _ec_port), timeout=2):
                pass
            _ec_brokers["IBKR"] = IBKRBroker(client_id=9)
        except Exception:
            pass
        def _ec_kill_switch(level):
            """Arm both kill switches on emergency close."""
            try:
                from core.kill_switch_live import LiveKillSwitch
                LiveKillSwitch().activate(reason=f"emergency_{level}", trigger_type="EMERGENCY")
            except Exception:
                pass
            try:
                from core.crypto.risk_manager_crypto import CryptoKillSwitch
                CryptoKillSwitch()._activate(f"emergency_{level}")
            except Exception:
                pass

        _v12_emergency_close = EmergencyCloseAll(
            brokers=_ec_brokers,
            alert_callback=_send_alert,
            kill_switch_callback=_ec_kill_switch,
        )
        logger.info(f"  V12 EmergencyCloseAll initialized ({list(_ec_brokers.keys())})")
    except Exception as e:
        logger.warning(f"  V12 EmergencyCloseAll failed: {e}")


def _v12_on_fill(broker_name: str, strategy: str, ticker: str, side: str,
                 quantity: float, price: float, order_id: str = "",
                 signal_price: float = 0, pnl: float = 0):
    """Central V12 post-fill hook — called after every trade execution.

    Feeds: double-fill detector, shadow logger, tax classifier, live tracker.
    """
    import time as _time

    # 1. Double-fill detection
    if _v12_double_fill:
        try:
            from core.execution.double_fill_detector import Fill
            fill = Fill(
                timestamp=_time.time(),
                order_id=str(order_id),
                ticker=ticker,
                side=side,
                quantity=quantity,
                price=price,
                broker=broker_name,
                strategy=strategy,
            )
            _v12_double_fill.check_fill(fill)
        except Exception as e:
            logger.debug(f"V12 double-fill check error: {e}")

    # 2. Shadow trade logger (fill side)
    if _v12_shadow_logger:
        try:
            _v12_shadow_logger.log_fill(
                strategy=strategy,
                ticker=ticker,
                fill_price=price,
                fill_qty=quantity,
                side=side,
                broker=broker_name,
            )
        except Exception as e:
            logger.debug(f"V12 shadow logger fill error: {e}")

    # 3. Tax classification (classify ALL trades, not just realized P&L)
    if _v12_tax_classifier:
        try:
            _v12_tax_classifier.classify(
                broker=broker_name,
                ticker=ticker,
                side=side,
                quantity=quantity,
                entry_price=signal_price if signal_price > 0 else price,
                exit_price=price,
                pnl=pnl,
                strategy=strategy,
            )
        except Exception as e:
            logger.warning(f"V12 tax classify error: {e}")


def _v12_on_signal(strategy: str, ticker: str, side: str, signal_price: float):
    """Central V12 pre-execution hook — called at signal generation."""
    if _v12_shadow_logger:
        try:
            _v12_shadow_logger.log_signal(
                strategy=strategy,
                ticker=ticker,
                side=side,
                signal_price=signal_price,
            )
        except Exception as e:
            logger.debug(f"V12 shadow signal error: {e}")


def run_v12_regime_cycle():
    """V12 regime detection — runs every 15 min.

    Computes regime per asset class using available market data.
    Updates activation multipliers for all strategies.
    """
    if not _v12_regime_scheduler:
        return

    try:
        # Collect metrics from available sources
        fx_metrics = _collect_fx_regime_metrics()
        crypto_metrics = _collect_crypto_regime_metrics()

        snapshot = _v12_regime_scheduler.run_cycle(
            fx_metrics=fx_metrics,
            crypto_metrics=crypto_metrics,
        )
        logger.info(
            f"V12 Regime: global={snapshot.get('global', '?')} "
            f"detail={snapshot.get('regimes', {})}"
        )
        _log_event("v12_regime_cycle", details=snapshot)

    except Exception as e:
        logger.error(f"V12 regime cycle error: {e}", exc_info=True)


def _collect_fx_regime_metrics() -> dict:
    """Collect FX regime metrics from available data."""
    metrics = {
        "realized_vol_20d": 0.0,
        "realized_vol_5d": 0.0,
        "cross_corr": 0.0,
        "spread_zscore": 0.0,
        "trend_strength": 0.0,
        "volume_ratio": 1.0,
    }
    try:
        import numpy as np
        import pandas as pd
        # Try loading FX data from parquet files
        fx_data_dir = ROOT / "data" / "fx"
        if fx_data_dir.exists():
            # Use EURUSD as proxy for FX regime
            for pair_file in ["EURUSD_1D.parquet", "EUR.USD_1D.parquet"]:
                fp = fx_data_dir / pair_file
                if fp.exists():
                    df = pd.read_parquet(fp)
                    if len(df) >= 20:
                        returns = df["close"].pct_change().dropna()
                        metrics["realized_vol_20d"] = float(returns.tail(20).std() * np.sqrt(252))
                        metrics["realized_vol_5d"] = float(returns.tail(5).std() * np.sqrt(252))
                        # ADX proxy: use rolling std of returns as trend strength
                        if len(returns) >= 14:
                            abs_ret = returns.abs().tail(14).mean()
                            metrics["trend_strength"] = float(abs_ret * 100 * 14)  # rough ADX proxy
                    break
    except Exception as e:
        logger.debug(f"FX regime metrics collection: {e}")
    return metrics


def _collect_crypto_regime_metrics() -> dict:
    """Collect crypto regime metrics from crypto monitor/broker."""
    metrics = {
        "realized_vol_20d": 0.0,
        "realized_vol_5d": 0.0,
        "cross_corr": 0.0,
        "spread_zscore": 0.0,
        "trend_strength": 0.0,
        "volume_ratio": 1.0,
    }
    try:
        import numpy as np
        import pandas as pd
        crypto_data_dir = ROOT / "data" / "crypto"
        if crypto_data_dir.exists():
            for btc_file in ["BTCUSDC_1D.parquet", "BTCUSDT_1D.parquet", "BTC_1D.parquet"]:
                fp = crypto_data_dir / btc_file
                if fp.exists():
                    df = pd.read_parquet(fp)
                    if len(df) >= 20:
                        returns = df["close"].pct_change().dropna()
                        metrics["realized_vol_20d"] = float(returns.tail(20).std() * np.sqrt(365))
                        metrics["realized_vol_5d"] = float(returns.tail(5).std() * np.sqrt(365))
                        if len(returns) >= 14:
                            abs_ret = returns.abs().tail(14).mean()
                            metrics["trend_strength"] = float(abs_ret * 100 * 14)
                    break
    except Exception as e:
        logger.debug(f"Crypto regime metrics collection: {e}")
    return metrics


def get_v12_regime_multiplier(strategy_id: str) -> float:
    """Get regime activation multiplier for a strategy. Called before signals."""
    if _v12_regime_scheduler is None:
        return 1.0  # No regime engine = no filtering
    return _v12_regime_scheduler.get_activation_multiplier(strategy_id)


def _init_v11_modules():
    """Initialize V11 roadmap modules: data quality, HRP, Kelly, EU throttler."""
    global _v11_data_quality, _v11_kelly, _v11_hrp, _v11_throttler_eu

    try:
        from core.data.data_quality import DataQualityGuard
        _v11_data_quality = DataQualityGuard()
        logger.info("  V11 DataQualityGuard initialized")
    except Exception as e:
        logger.warning(f"  V11 DataQualityGuard failed: {e}")

    try:
        from core.alloc.kelly_dynamic import DynamicKellyManager
        _v11_kelly = DynamicKellyManager(sma_lookback=20, hysteresis_pct=0.02)
        logger.info("  V11 DynamicKellyManager initialized")
    except Exception as e:
        logger.warning(f"  V11 DynamicKellyManager failed: {e}")

    try:
        from core.alloc.hrp_allocator import HRPAllocator
        _v11_hrp = HRPAllocator(min_weight=0.02, max_weight=0.25)
        logger.info("  V11 HRPAllocator initialized")
    except Exception as e:
        logger.warning(f"  V11 HRPAllocator failed: {e}")

    try:
        from core.risk.v10_throttler_eu import V10ThrottlerEU
        _v11_throttler_eu = V10ThrottlerEU()
        logger.info("  V11 V10ThrottlerEU initialized")
    except Exception as e:
        logger.warning(f"  V11 V10ThrottlerEU failed: {e}")


def run_v11_hrp_rebalance():
    """V11 HRP rebalance — runs every 4 hours.

    Recomputes strategy weights using Hierarchical Risk Parity.
    Logs allocation changes and sends Telegram alert on mode change.
    """
    if not _v11_hrp or not _v11_kelly:
        return

    try:
        # Update Kelly equity tracking
        total_equity = 0
        try:
            if os.getenv("BINANCE_API_KEY"):
                from core.broker.binance_broker import BinanceBroker
                bnb = BinanceBroker()
                acct = bnb.get_account_info()
                total_equity += float(acct.get("equity", 0))
        except Exception:
            pass

        if total_equity > 0:
            _v11_kelly.update_equity(datetime.now(UTC), total_equity)
            mode = _v11_kelly.get_kelly_mode()
            logger.info(
                f"V11 KELLY: mode={mode['mode']}, fraction={mode['fraction']:.3f}, "
                f"equity=${total_equity:,.0f}"
            )

            # Alert on mode change
            if hasattr(run_v11_hrp_rebalance, '_last_mode'):
                if mode['mode'] != run_v11_hrp_rebalance._last_mode:
                    try:
                        from core.alloc.allocation_report import AllocationReport
                        report = AllocationReport()
                        msg = report.format_telegram_alert(
                            run_v11_hrp_rebalance._last_mode,
                            mode['mode'],
                            f"equity=${total_equity:,.0f}"
                        )
                        _send_alert(msg, level="warning")
                    except Exception:
                        _send_alert(
                            f"KELLY MODE: {run_v11_hrp_rebalance._last_mode} -> {mode['mode']}",
                            level="warning"
                        )
            run_v11_hrp_rebalance._last_mode = mode['mode']

        _log_event("v11_hrp_rebalance", details={
            "kelly_mode": mode['mode'] if total_equity > 0 else "UNKNOWN",
            "equity": total_equity,
        })

    except Exception as e:
        logger.error(f"V11 HRP rebalance error: {e}", exc_info=True)


def run_v11_eod_cleanup():
    """V11 End-of-day orphan order cleanup.

    Runs 5 min after EU close (17:35 CET) and US close (16:05 ET).
    Detects and cancels orphan SL/TP orders without matching positions.
    """
    try:
        from core.execution.orphan_detector import OrphanDetector
        detector = OrphanDetector(alert_callback=_send_alert)

        # Try IBKR cleanup
        try:
            from core.broker.ibkr_adapter import IBKRBroker
            with IBKRBroker(client_id=3) as ibkr:
                result = detector.run_eod_cleanup(ibkr, datetime.now(PARIS))
            if result.get("orphans_found", 0) > 0:
                logger.warning(
                    f"V11 EOD: {result['orphans_found']} orphans found, "
                    f"{result.get('cancelled', 0)} cancelled"
                )
                _send_alert(
                    f"EOD CLEANUP: {result['orphans_found']} orphans, "
                    f"{result.get('cancelled', 0)} cancelled",
                    level="warning"
                )
        except Exception as e:
            logger.debug(f"V11 EOD IBKR cleanup skip: {e}")

        _log_event("v11_eod_cleanup", details={"status": "completed"})

    except Exception as e:
        logger.error(f"V11 EOD cleanup error: {e}", exc_info=True)


def run_v10_portfolio_cycle():
    """V10 portfolio-aware risk cycle — runs every 5 minutes.

    1. Record portfolio snapshot (JSONL)
    2. Check correlation alerts
    3. Check ERE thresholds
    4. Run safety mode anomaly check
    5. Log leverage decision
    """
    if not _v10:
        return

    try:
        # 1. Portfolio snapshot
        snapshot_logger = _v10.get("snapshot_logger")
        if snapshot_logger:
            snapshot = snapshot_logger.record()
            if snapshot:
                portfolio_data = snapshot.get("portfolio", {})
                logger.info(
                    f"V10 SNAPSHOT: capital=${portfolio_data.get('total_capital', 0):,.0f}, "
                    f"ERE={portfolio_data.get('capital_at_risk_pct', 0):.1%}, "
                    f"corr={portfolio_data.get('correlation_score', 0):.2f}, "
                    f"DD={portfolio_data.get('drawdown_pct', 0):.1%}"
                )

        # 2. Correlation alerts
        corr_engine = _v10.get("correlation_engine")
        if corr_engine:
            alerts = corr_engine.check_alerts()
            for alert in alerts:
                if alert.level == "CRITICAL":
                    logger.critical(
                        f"V10 CORR CRITICAL: {alert.strategies[0]} <-> "
                        f"{alert.strategies[1]} = {alert.correlation:.2f}"
                    )
                    _send_alert(
                        f"CORRELATION CRITICAL: {alert.strategies[0]} <-> "
                        f"{alert.strategies[1]} = {alert.correlation:.2f}",
                        level="critical",
                    )
                elif alert.level == "WARNING":
                    logger.warning(
                        f"V10 CORR WARNING: {alert.strategies[0]} <-> "
                        f"{alert.strategies[1]} = {alert.correlation:.2f}"
                    )

        # 3. Safety mode anomaly check
        safety = _v10.get("safety_mode")
        portfolio_engine = _v10.get("portfolio_engine")
        if safety and safety.is_active and portfolio_engine:
            try:
                state = portfolio_engine.get_state()
                anomaly = safety.check_anomaly(
                    ere_pct=state.capital_at_risk_pct,
                    drawdown_pct=state.drawdown_pct,
                    correlation_score=state.correlation_score,
                )
                if anomaly.get("action") == "DISABLE_TRADING":
                    logger.critical(
                        f"V10 SAFETY: DISABLE_TRADING — {anomaly['details']}"
                    )
                    _send_alert(
                        f"SAFETY MODE: Trading disabled — {anomaly['details']}",
                        level="critical",
                    )
            except Exception as e:
                logger.debug(f"V10 safety check skip: {e}")

        # 4. Leverage decision (informational)
        lev_adapter = _v10.get("leverage_adapter")
        if lev_adapter and portfolio_engine:
            try:
                state = portfolio_engine.get_state()
                decision = lev_adapter.get_multiplier(
                    drawdown_pct=state.drawdown_pct,
                )
                if decision.multiplier < 0.8:
                    logger.warning(
                        f"V10 LEVERAGE: multiplier={decision.multiplier:.2f} "
                        f"({decision.reason})"
                    )
            except Exception as e:
                logger.debug(f"V10 leverage check skip: {e}")

        # 5. Cash drag monitor — DISABLED
        # V10 portfolio_data mixes paper (Alpaca $100K) + live (IBKR $10K + Binance $10K).
        # Cash drag alert is meaningless until Alpaca goes live.
        # Use V12 UnifiedPortfolioView (live-only) for real cash monitoring instead.

    except Exception as e:
        logger.error(f"V10 portfolio cycle error: {e}", exc_info=True)


def main():
    _start_health_server()
    logger.info("=" * 60)
    logger.info("  TRADING WORKER — demarrage")
    logger.info(f"  Paris: {datetime.now(PARIS).strftime('%Y-%m-%d %H:%M')}")
    logger.info(f"  New York: {datetime.now(ET).strftime('%Y-%m-%d %H:%M')}")
    logger.info(f"  Alpaca API: {'SET' if os.getenv('ALPACA_API_KEY') else 'NOT SET'}")
    logger.info(f"  Binance API: {'SET' if os.getenv('BINANCE_API_KEY') else 'NOT SET'}")
    logger.info("=" * 60)

    _log_event("worker_start", details={
        "alpaca": bool(os.getenv("ALPACA_API_KEY")),
        "binance": bool(os.getenv("BINANCE_API_KEY")),
        "ibkr_host": os.getenv("IBKR_HOST", "127.0.0.1"),
    })

    _send_alert(
        f"WORKER START {datetime.now(PARIS).strftime('%H:%M')} CET\n"
        f"Alpaca: {'OK' if os.getenv('ALPACA_API_KEY') else 'NO'}\n"
        f"Binance: {'LIVE' if os.getenv('BINANCE_API_KEY') else 'NO'}\n"
        f"IBKR: {os.getenv('IBKR_HOST', '?')}:{os.getenv('IBKR_PORT', '?')}",
        level="info"
    )

    daily_done_today = False
    last_intraday = 0
    last_eu_intraday = 0
    last_fx_paper = 0
    last_live_risk = 0
    last_heartbeat = 0
    last_cross_portfolio = 0
    last_crypto = 0
    last_v10_cycle = 0
    last_v11_hrp = 0
    last_v12_regime = 0
    V12_REGIME_INTERVAL = 900  # 15 min
    v11_eod_done_today = False
    after_close_checked_today = False
    FX_PAPER_INTERVAL = 300  # 5 min
    HEARTBEAT_INTERVAL = 1800  # 30 min
    CROSS_PORTFOLIO_INTERVAL = 14400  # 4 hours
    V11_HRP_INTERVAL = 14400  # 4 hours

    # Verifier que les imports fonctionnent au demarrage
    # NOTE: ne PAS importer run_intraday ici — ca shadow la wrapper locale
    # qui accepte le param market="US"/"EU"
    try:
        logger.info("  Imports paper_portfolio OK")
    except Exception as e:
        logger.error(f"  ERREUR IMPORT: {e}", exc_info=True)
        logger.error("  Le worker ne peut pas demarrer sans paper_portfolio")
        sys.exit(1)

    # === V12 PRE-FLIGHT CHECK (BLOCKING) ===
    logger.info("  Running pre-flight checks...")
    try:
        from scripts.preflight_check import run_preflight
        _pf = run_preflight(block_on_failure=True)
        if _pf.blockers:
            logger.critical(f"PRE-FLIGHT: {len(_pf.blockers)} blocker(s) detected")
            for b in _pf.blockers:
                logger.critical(f"  {b}")
            _send_alert(
                f"PRE-FLIGHT BLOCKED STARTUP: {len(_pf.blockers)} blocker(s)\n"
                + "\n".join(_pf.blockers[:5]),
                level="critical",
            )
            # Wait and retry once after 60s — transient issues (API cold start)
            logger.info("  Pre-flight retry in 60s...")
            time.sleep(60)
            _pf2 = run_preflight(block_on_failure=True)
            if _pf2.blockers:
                # Non-critical blockers (e.g. margin check) → continue degraded
                # Critical blockers (Binance auth, IBKR down) → would have sys.exit
                _critical = [b for b in _pf2.blockers
                            if any(kw in b.lower() for kw in ("auth", "binance", "ibkr", "4002", "gateway"))]
                if _critical:
                    logger.critical(f"PRE-FLIGHT CRITICAL FAIL — {_critical}")
                    _send_alert(f"PRE-FLIGHT CRITICAL: {_critical}", level="critical")
                logger.critical(f"PRE-FLIGHT RETRY FAILED ({len(_pf2.blockers)} blockers) — worker starting degraded")
                _send_alert(f"PRE-FLIGHT DEGRADED: {len(_pf2.blockers)} blockers\n{chr(10).join(_pf2.blockers[:3])}", level="critical")
            else:
                logger.info(f"  Pre-flight retry OK: {len(_pf2.checks)} checks passed")
        else:
            logger.info(f"  Pre-flight OK: {len(_pf.checks)} checks passed")
    except Exception as _pf_err:
        logger.warning(f"  Pre-flight check failed to run: {_pf_err}")

    # === RECONCILIATION AU DEMARRAGE ===
    logger.info("  Reconciliation des positions au demarrage...")
    reconcile_positions_at_startup()

    # Crypto reconciliation
    if os.getenv("BINANCE_API_KEY"):
        try:
            from core.broker.binance_broker import BinanceBroker
            broker = BinanceBroker()
            acct = broker.get_account_info()
            positions = broker.get_positions()
            earn = broker.get_earn_positions()
            logger.info(f"CRYPTO RECONCILIATION: equity=${acct.get('equity',0):.0f}, "
                        f"{len(positions)} positions, {len(earn)} earn products")
            if positions:
                for p in positions:
                    logger.info(f"  Binance position: {p.get('symbol')} {p.get('side')} "
                               f"qty={p.get('qty',0)}")
        except Exception as e:
            logger.warning(f"Crypto reconciliation failed: {e}")

    # === V10 PORTFOLIO-AWARE RISK MODULES ===
    logger.info("  Initializing V10 portfolio-aware risk modules...")
    _init_v10_modules()

    # === V11 ROADMAP MODULES ===
    logger.info("  Initializing V11 roadmap modules (data quality, HRP, Kelly, EU throttler)...")
    _init_v11_modules()

    # === V12 REGIME ENGINE ===
    logger.info("  Initializing V12 regime engine...")
    _init_v12_modules()

    # Premier heartbeat
    log_heartbeat()
    last_heartbeat = time.time()

    # CRO #4: automated dry-run and smoke test flags
    _dry_run_done_today = False
    _smoke_test_done_this_week = False
    _ror_done_today = False

    # ── R1/R2/R5: Initialisation robustesse ─────────────────────────────
    _event_logger = get_event_logger()
    _metrics = get_metrics()
    _worker_state = WorkerState()
    _order_tracker = OrderTracker(alert_callback=lambda msg: _send_alert(msg, level="critical"))
    _broker_health = BrokerHealthRegistry()
    _broker_health.register("binance")
    _broker_health.register("ibkr")
    _broker_health.register("alpaca")

    def _cycle_alert(msg):
        _send_alert(msg, level="warning")

    def _cycle_metrics_cb(name, duration, success, error):
        _metrics.emit(f"cycle.{name}.duration_seconds", duration,
                      tags={"success": str(success)})
        if not success:
            _metrics.emit(f"cycle.{name}.error", 1.0, tags={"error": error or ""})

    _runners = {
        "crypto": CycleRunner("crypto", run_crypto_cycle,
                              alert_callback=_cycle_alert,
                              metrics_callback=_cycle_metrics_cb),
        "fx_carry": CycleRunner("fx_carry", run_fx_carry_cycle,
                                alert_callback=_cycle_alert,
                                metrics_callback=_cycle_metrics_cb),
        "futures": CycleRunner("futures", run_futures_paper_cycle,
                               alert_callback=_cycle_alert,
                               metrics_callback=_cycle_metrics_cb),
        "fx_paper": CycleRunner("fx_paper", run_fx_paper_cycle,
                                alert_callback=_cycle_alert,
                                metrics_callback=_cycle_metrics_cb),
        "live_risk": CycleRunner("live_risk", run_live_risk_cycle,
                                 alert_callback=_cycle_alert,
                                 metrics_callback=_cycle_metrics_cb),
        "v10_portfolio": CycleRunner("v10_portfolio", run_v10_portfolio_cycle,
                                     alert_callback=_cycle_alert,
                                     metrics_callback=_cycle_metrics_cb),
        "v11_hrp": CycleRunner("v11_hrp", run_v11_hrp_rebalance,
                               alert_callback=_cycle_alert,
                               metrics_callback=_cycle_metrics_cb),
        "v12_regime": CycleRunner("v12_regime", run_v12_regime_cycle,
                                  alert_callback=_cycle_alert,
                                  metrics_callback=_cycle_metrics_cb),
        "v11_eod": CycleRunner("v11_eod", run_v11_eod_cleanup,
                               alert_callback=_cycle_alert,
                               metrics_callback=_cycle_metrics_cb),
    }
    logger.info(f"  CycleRunners initialized: {list(_runners.keys())}")

    while True:
        now_paris = datetime.now(PARIS)
        now_et = datetime.now(ET)
        today = now_paris.date()

        # Reset daily flags au changement de jour
        if daily_done_today and now_paris.hour < DAILY_HOUR:
            daily_done_today = False
            after_close_checked_today = False

        # === CRYPTO CYCLE 24/7 (y compris weekends) — toutes les 15 min ===
        if time.time() - last_crypto >= CRYPTO_INTERVAL_SECONDS:
            _runners["crypto"].run()
            last_crypto = time.time()

        # === FX CARRY DAILY (lun-ven, 10h Paris) ===
        if is_weekday() and now_paris.hour == 10 and not getattr(run_fx_carry_cycle, '_done_today', False):
            _runners["fx_carry"].run()
            run_fx_carry_cycle._done_today = True
        if is_weekday() and now_paris.hour < 10:
            run_fx_carry_cycle._done_today = False

        # === FUTURES PAPER DAILY (lun-ven, 16h Paris = ouverture US) ===
        if is_weekday() and now_paris.hour == 16 and not getattr(run_futures_paper_cycle, '_done_today', False):
            _runners["futures"].run()
            run_futures_paper_cycle._done_today = True
        if is_weekday() and now_paris.hour < 16:
            run_futures_paper_cycle._done_today = False

        # === HEARTBEAT toutes les 30 min (local log only, y compris weekends) ===
        if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
            log_heartbeat()
            last_heartbeat = time.time()

            # Telegram V2 digest every 4h (not every 30 min)
            if time.time() - last_heartbeat <= 60:  # Just after heartbeat
                try:
                    _h = now_paris.hour
                    if _h in (7, 11, 15, 19, 23):  # 5x/day digest
                        from core.telegram_v2 import tg
                        _bnb_eq = 0
                        _ibkr_eq = 0
                        _alp_eq = 0
                        try:
                            from core.broker.binance_broker import BinanceBroker
                            _bnb_eq = float(BinanceBroker().get_account_info().get("equity", 0))
                        except Exception:
                            pass
                        try:
                            from core.alpaca_client.client import AlpacaClient
                            _alp_eq = float(AlpacaClient.from_env().get_account_info().get("equity", 0))
                        except Exception:
                            pass
                        # FIX: fetch IBKR equity (was always $0)
                        try:
                            import socket as _dig_sock
                            _dig_host = os.getenv("IBKR_HOST", "127.0.0.1")
                            _dig_port = int(os.getenv("IBKR_PORT", "4002"))
                            with _dig_sock.create_connection((_dig_host, _dig_port), timeout=3):
                                pass
                            from core.broker.ibkr_adapter import IBKRBroker
                            _dig_ibkr = IBKRBroker(client_id=5)
                            try:
                                _ibkr_eq = float(_dig_ibkr.get_account_info().get("equity", 0))
                            finally:
                                _dig_ibkr.disconnect()
                        except Exception:
                            pass
                        tg.send_digest(
                            equity_binance=_bnb_eq,
                            equity_ibkr=_ibkr_eq,
                            equity_alpaca=_alp_eq,
                            regime="BEAR_NORMAL",
                        )
                except Exception:
                    pass

        # === CRO #4: AUTOMATED DRY-RUN (06:00 CET daily) ===
        if now_paris.hour == 6 and not _dry_run_done_today:
            _dry_run_done_today = True
            try:
                from scripts.dry_run_pipeline import dry_run_crypto
                _dr = dry_run_crypto()
                _dr_fail = [r["strat_id"] for r in _dr if not r["passed"]]
                if _dr_fail:
                    logger.warning(f"DRY-RUN: {len(_dr_fail)} FAIL: {_dr_fail}")
                    _send_alert(f"DRY-RUN: {len(_dr_fail)} FAIL\n{', '.join(_dr_fail)}", level="warning")
                else:
                    logger.info(f"DRY-RUN: {len(_dr)}/{len(_dr)} PASS")
            except Exception as _dr_err:
                logger.warning(f"DRY-RUN error: {_dr_err}")
        if now_paris.hour < 6:
            _dry_run_done_today = False

        # === CRO #4: AUTOMATED SMOKE TEST (dimanche 04:00 CET) ===
        if now_paris.weekday() == 6 and now_paris.hour == 4 and not _smoke_test_done_this_week:
            _smoke_test_done_this_week = True
            try:
                from scripts.smoke_test_strategies import smoke_test_crypto
                _sm = smoke_test_crypto()
                _sm_fail = [r["strat_id"] for r in _sm if not r["passed"]]
                if _sm_fail:
                    logger.warning(f"SMOKE TEST: {len(_sm_fail)} FAIL: {_sm_fail}")
                    _send_alert(f"SMOKE TEST: {len(_sm_fail)} FAIL\n{', '.join(_sm_fail)}", level="warning")
                else:
                    logger.info(f"SMOKE TEST: {len(_sm)}/{len(_sm)} PASS")
            except Exception as _sm_err:
                logger.warning(f"SMOKE TEST error: {_sm_err}")
        if now_paris.weekday() == 0:
            _smoke_test_done_this_week = False

        # === CRO #7: MONTE CARLO RoR CHECK (07:00 CET daily) ===
        if now_paris.hour == 7 and not _ror_done_today:
            _ror_done_today = True
            try:
                import numpy as np

                from core.risk.ruin_scheduler import RuinScheduler

                # Build returns matrix from crypto equity tracking
                _dd_path = ROOT / "data" / "crypto_dd_state.json"
                _equity_log = ROOT / "data" / "monitoring" / "signal_fill_ratio.jsonl"

                # Read capital from config (same source as run_crypto_cycle)
                import yaml as _ror_yaml
                _ror_alloc = _ror_yaml.safe_load(
                    (ROOT / "config" / "crypto_allocation.yaml").read_text(encoding="utf-8")
                ).get("crypto_allocation", {})
                _ror_capital = _ror_alloc.get("total_capital", 10_000)
                if _dd_path.exists():
                    _dd_data = json.loads(_dd_path.read_text(encoding="utf-8"))
                    _ror_capital = _dd_data.get("peak_equity", _ror_capital)

                # Estimate strategy returns from allocation (simplified)
                # Each strategy gets equal weight, daily return estimated from equity delta
                n_strats = 11  # crypto strategies
                _ror_returns = np.random.default_rng(42).normal(
                    0.0005, 0.02, (60, n_strats)  # 60 days of synthetic data as bootstrap
                )
                _ror_weights = np.ones(n_strats) / n_strats

                scheduler = RuinScheduler(
                    alert_callback=_send_alert,
                    n_simulations=5_000,  # Faster for daily check
                )
                _ror_result = scheduler.run_check(
                    _ror_returns, _ror_weights,
                    capital=_ror_capital,
                    kelly_fraction=CRYPTO_KELLY_FRACTION,
                )
                logger.info(
                    f"RoR CHECK: {_ror_result.alert_level} — "
                    f"P(DD>10%)={_ror_result.prob_dd_10pct:.1%}, "
                    f"P(ruin)={_ror_result.prob_ruin:.1%}"
                )
            except Exception as _ror_err:
                logger.warning(f"RoR check error: {_ror_err}")
        if now_paris.hour < 7:
            _ror_done_today = False

        # === CHECK POSITIONS APRES FERMETURE (16:05-16:30 ET, weekdays only) ===
        if (is_weekday() and not after_close_checked_today
                and now_et.hour == 16 and 5 <= now_et.minute <= 30):
            logger.info("  Check des positions apres fermeture du marche...")
            check_positions_after_close()
            after_close_checked_today = True

        # Daily run a 15:35 Paris (une seule fois par jour)
        # FIX CRO H-6 : check trading_paused_until before run_daily
        _daily_paused = False
        try:
            _d_state_path = ROOT / "data" / "state" / "paper_portfolio_state.json"
            if _d_state_path.exists():
                _d_state = json.loads(
                    _d_state_path.read_text(encoding="utf-8")
                )
                _d_paused_until = _d_state.get("trading_paused_until")
                if _d_paused_until:
                    _d_pause_dt = datetime.fromisoformat(_d_paused_until)
                    if datetime.now(UTC) < _d_pause_dt:
                        _daily_paused = True
        except Exception:
            pass

        if is_weekday() and is_daily_time() and not daily_done_today:
            if _daily_paused:
                logger.warning(
                    f"DAILY RUN SKIP — trading paused until {_d_paused_until}"
                )
            else:
                run_daily()
            daily_done_today = True

        # Intraday US toutes les 5 min pendant la fenetre (15:35-22:00 Paris)
        if is_intraday_window():
            elapsed = time.time() - last_intraday
            if elapsed >= INTRADAY_INTERVAL_SECONDS:
                run_intraday(market="US")
                last_intraday = time.time()

        # Intraday EU toutes les 5 min pendant la fenetre (09:00-17:30 Paris)
        if is_eu_intraday_window():
            elapsed = time.time() - last_eu_intraday
            if elapsed >= INTRADAY_INTERVAL_SECONDS:
                run_intraday(market="EU")
                last_eu_intraday = time.time()

        # FX Paper strategies every 5 min (24h lun-ven) on IBKR paper port 4003
        if is_fx_window():
            elapsed = time.time() - last_fx_paper
            if elapsed >= FX_PAPER_INTERVAL:
                _runners["fx_paper"].run()
                last_fx_paper = time.time()

        # Live risk monitoring every 5 min (09:00-22:00 Paris)
        if is_live_risk_window():
            elapsed = time.time() - last_live_risk
            if elapsed >= LIVE_RISK_INTERVAL_SECONDS:
                _runners["live_risk"].run()
                last_live_risk = time.time()

        # === V10 PORTFOLIO CYCLE every 5 min (always, including weekends for crypto) ===
        if time.time() - last_v10_cycle >= V10_CYCLE_INTERVAL:
            _runners["v10_portfolio"].run()
            last_v10_cycle = time.time()

        # CRO H-4: Periodic reconciliation every 4 hours
        if time.time() - last_cross_portfolio >= CROSS_PORTFOLIO_INTERVAL:
            logger.info("=== PERIODIC RECONCILIATION (4h) ===")
            reconcile_positions_at_startup()  # Reuse startup reconciliation
            if os.getenv("BINANCE_API_KEY"):
                try:
                    from core.broker.binance_broker import BinanceBroker
                    _recon_bnb = BinanceBroker()
                    _recon_pos = _recon_bnb.get_positions()
                    _recon_acct = _recon_bnb.get_account_info()
                    logger.info(
                        f"CRYPTO RECONCILIATION (4h): equity=${_recon_acct.get('equity',0):.0f}, "
                        f"{len(_recon_pos)} positions"
                    )
                except Exception as _re:
                    logger.warning(f"Crypto periodic reconciliation failed: {_re}")

        # Cross-portfolio exposure check every 4 hours (V12: Unified Portfolio + Correlation)
        if time.time() - last_cross_portfolio >= CROSS_PORTFOLIO_INTERVAL:
            try:
                ibkr_data = {"equity": 0, "positions": [], "cash": 0}
                binance_data = {"equity": 0, "positions": [], "cash": 0}
                alpaca_data = {"equity": 0, "positions": [], "cash": 0}

                # Collect IBKR (check connectivity via socket, not env var)
                try:
                    import socket as _cp_sock
                    _cp_host = os.getenv("IBKR_HOST", "127.0.0.1")
                    _cp_port = int(os.getenv("IBKR_PORT", "4002"))
                    with _cp_sock.create_connection((_cp_host, _cp_port), timeout=3):
                        pass
                    from core.broker.ibkr_adapter import IBKRBroker
                    with IBKRBroker(client_id=3) as ibkr:
                            acct = ibkr.get_account_info()
                            ibkr_positions = ibkr.get_positions()
                            ibkr_data = {
                                "equity": float(acct.get("equity", 0)),
                                "positions": ibkr_positions,
                                "cash": float(acct.get("cash", 0)),
                            }
                except Exception as e:
                    logger.debug(f"Cross-portfolio: IBKR unavailable: {e}")

                # Collect Binance
                try:
                    if os.environ.get("BINANCE_API_KEY"):
                        from core.broker.binance_broker import BinanceBroker
                        bnb = BinanceBroker()
                        acct = bnb.get_account_info()
                        binance_data = {
                            "equity": float(acct.get("equity", 0)),
                            "positions": bnb.get_positions(),
                            "cash": float(acct.get("cash", 0)),
                        }
                except Exception as e:
                    logger.debug(f"Cross-portfolio: Binance unavailable: {e}")

                # Collect Alpaca (FIX: use from_env() and get_account_info())
                try:
                    if os.environ.get("ALPACA_API_KEY"):
                        from core.alpaca_client.client import AlpacaClient
                        alp = AlpacaClient.from_env()
                        alp_acct = alp.get_account_info()
                        alpaca_data = {
                            "equity": float(alp_acct.get("equity", 0)),
                            "positions": alp.get_positions() or [],
                            "cash": float(alp_acct.get("cash", 0)),
                            "paper": True,  # Alpaca is paper until $25K arrives
                        }
                except Exception as e:
                    logger.debug(f"Cross-portfolio: Alpaca unavailable: {e}")

                # V12 Unified Portfolio View
                if _v12_unified_portfolio and (ibkr_data["equity"] > 0 or binance_data["equity"] > 0):
                    snap = _v12_unified_portfolio.update(
                        binance_data=binance_data,
                        ibkr_data=ibkr_data,
                        alpaca_data=alpaca_data,
                    )
                    logger.info(
                        f"V12 Unified: NAV=${snap.nav_total:,.0f} "
                        f"DD_peak={snap.dd_from_peak_pct:.1f}% "
                        f"DD_daily={snap.dd_daily_pct:.1f}% "
                        f"alert={snap.alert_level}"
                    )
                    _log_event("v12_unified_portfolio", details={
                        "nav": snap.nav_total, "dd_peak": snap.dd_from_peak_pct,
                        "alert": snap.alert_level,
                    })

                # V12 Cross-Asset Correlation (if data available)
                if _v12_cross_corr:
                    try:
                        import numpy as np
                        import pandas as pd
                        returns_by_asset = {}
                        # BTC returns from crypto data
                        for btc_file in ["BTCUSDC_1D.parquet", "BTCUSDT_1D.parquet"]:
                            fp = ROOT / "data" / "crypto" / btc_file
                            if fp.exists():
                                df = pd.read_parquet(fp)
                                if len(df) >= 10:
                                    returns_by_asset["BTC"] = df["close"].pct_change().dropna().tail(20).tolist()
                                break
                        # FX returns
                        for fx_file in ["EURUSD_1D.parquet", "EUR.USD_1D.parquet"]:
                            fp = ROOT / "data" / "fx" / fx_file
                            if fp.exists():
                                df = pd.read_parquet(fp)
                                if len(df) >= 10:
                                    returns_by_asset["EURUSD"] = df["close"].pct_change().dropna().tail(20).tolist()
                                break

                        if len(returns_by_asset) >= 2:
                            corr_report = _v12_cross_corr.update(returns_by_asset)
                            if corr_report.get("alerts"):
                                for a in corr_report["alerts"]:
                                    _send_alert(a["message"], level="warning")
                            logger.info(
                                f"V12 CrossCorr: div_score={corr_report.get('diversification_score', '?')} "
                                f"avg_corr={corr_report.get('avg_abs_correlation', '?')}"
                            )
                    except Exception as e:
                        logger.debug(f"V12 cross-corr error: {e}")

                # Legacy cross-portfolio guard (keep for backwards compatibility)
                try:
                    from core.cross_portfolio_guard import check_combined_exposure
                    ibkr_long = sum(abs(float(p.get("market_val", 0))) for p in ibkr_data["positions"] if float(p.get("qty", 0)) >= 0)
                    ibkr_short = sum(abs(float(p.get("market_val", 0))) for p in ibkr_data["positions"] if float(p.get("qty", 0)) < 0)
                    crypto_long = sum(abs(float(p.get("market_val", 0))) for p in binance_data["positions"] if p.get("side") != "SHORT")
                    crypto_short = sum(abs(float(p.get("market_val", 0))) for p in binance_data["positions"] if p.get("side") == "SHORT")
                    if ibkr_data["equity"] > 0 or binance_data["equity"] > 0:
                        result = check_combined_exposure(
                            ibkr_long, ibkr_short, ibkr_data["equity"],
                            crypto_long, crypto_short, binance_data["equity"],
                        )
                        if result["level"] != "OK":
                            logger.warning(f"CROSS-PORTFOLIO: {result['message']}")
                            _send_alert(f"CROSS-PORTFOLIO: {result['message']}", level="warning")
                        else:
                            logger.info(f"Cross-portfolio check OK: {result['combined_pct']}% combined")
                except Exception as e:
                    logger.debug(f"Legacy cross-portfolio guard: {e}")

            except Exception as e:
                logger.error(f"Cross-portfolio check error: {e}", exc_info=True)
            last_cross_portfolio = time.time()

        # === V11 HRP REBALANCE every 4 hours ===
        if time.time() - last_v11_hrp >= V11_HRP_INTERVAL:
            _runners["v11_hrp"].run()
            last_v11_hrp = time.time()

        # === V12 REGIME ENGINE every 15 min ===
        if time.time() - last_v12_regime >= V12_REGIME_INTERVAL:
            _runners["v12_regime"].run()
            last_v12_regime = time.time()

        # === V12 RoR DAILY CHECK (07:00 CET, before EU open) ===
        if (not getattr(main, '_v12_ror_done_today', False)
                and now_paris.hour == 7 and now_paris.minute < 15):
            try:
                import numpy as np

                from core.risk.ruin_scheduler import RuinScheduler
                # Build returns matrix from available data
                _ror_returns = []
                _ror_weights = []
                fx_data_dir = ROOT / "data" / "fx"
                crypto_data_dir = ROOT / "data" / "crypto"
                import pandas as pd

                for name, ddir, pattern in [
                    ("FX", fx_data_dir, "*_1D.parquet"),
                    ("CRYPTO", crypto_data_dir, "*_1D.parquet"),
                ]:
                    if ddir.exists():
                        for fp in sorted(ddir.glob(pattern))[:5]:
                            try:
                                df = pd.read_parquet(fp)
                                if len(df) >= 30:
                                    rets = df["close"].pct_change().dropna().tail(60).values
                                    if len(rets) >= 30:
                                        _ror_returns.append(rets[-30:])
                                        _ror_weights.append(1.0)
                            except Exception:
                                pass

                if len(_ror_returns) >= 2:
                    min_len = min(len(r) for r in _ror_returns)
                    matrix = np.column_stack([r[-min_len:] for r in _ror_returns])
                    weights = np.array(_ror_weights)
                    weights /= weights.sum()

                    ror = RuinScheduler(alert_callback=_send_alert, n_simulations=5000)
                    result = ror.run_check(matrix, weights, capital=45_000)
                    logger.info(f"V12 RoR: {result.alert_level} P(DD>10%)={result.prob_dd_10pct:.1%}")
                else:
                    logger.info("V12 RoR: insufficient data, skipping")
            except Exception as e:
                logger.error(f"V12 RoR check error: {e}", exc_info=True)
            main._v12_ror_done_today = True
        if now_paris.hour < 7:
            main._v12_ror_done_today = False

        # === V11 EOD ORPHAN CLEANUP (17:35 CET, weekdays only) ===
        if (is_weekday() and not v11_eod_done_today
                and now_paris.hour == 17 and 35 <= now_paris.minute <= 45):
            _runners["v11_eod"].run()
            v11_eod_done_today = True
        if now_paris.hour < 17:
            v11_eod_done_today = False

        # Emit system metrics every tick
        try:
            import psutil
            _proc = psutil.Process()
            _mem_mb = _proc.memory_info().rss / 1024 / 1024
            _metrics.emit("system.cpu.percent", psutil.cpu_percent())
            _metrics.emit("system.ram.percent", psutil.virtual_memory().percent)
            _metrics.emit("system.disk.percent", psutil.disk_usage("/").percent)
            _metrics.emit("system.memory.worker_mb", _mem_mb)

            # CRO L-1: Proactive memory management
            if _mem_mb > 300:
                import gc
                gc.collect()
                logger.warning(f"GC triggered: worker at {_mem_mb:.0f}MB (>300MB)")
        except ImportError:
            pass
        except Exception:
            pass

        # CRO M-3: Dead man's switch — check heartbeat age
        try:
            _hb_file = ROOT / "data" / "monitoring" / "heartbeat.json"
            if _hb_file.exists():
                import json as _hb_json
                _hb_data = _hb_json.loads(_hb_file.read_text(encoding="utf-8"))
                _hb_ts = datetime.fromisoformat(_hb_data["timestamp"])
                _hb_age_min = (datetime.now(UTC) - _hb_ts).total_seconds() / 60
                _metrics.emit("system.heartbeat.age_minutes", _hb_age_min)
                if _hb_age_min > 35:  # Heartbeat should be every 30min
                    _send_alert(
                        f"DEAD MAN'S SWITCH: heartbeat stale ({_hb_age_min:.0f}min old)",
                        level="critical",
                    )
        except Exception:
            pass

        # Record cycle health into worker state
        for _rn, _rr in _runners.items():
            _worker_state.record_cycle_metrics(_rn, _rr.metrics.to_dict())

        # Periodic metrics flush (every tick = 30s)
        _metrics.flush()

        # Sleep 30s entre les checks
        time.sleep(30)


if __name__ == "__main__":
    main()
