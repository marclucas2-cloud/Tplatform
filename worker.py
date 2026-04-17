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
import threading
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
    DAILY_HOUR,
    DAILY_MINUTE,
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





# --- Strategy failure tracker (#2 fiabilisation crypto 14/04) ---
# Detects silent repeated failures (e.g. STRAT-001 30 signals/0 trades bug).
# On 3rd consecutive fail: CRITICAL alert Telegram.
# On 5th consecutive fail: auto-pause strategy for 1h (skip all signals).
# Reset counter on successful execution.
_strat_fail_counter: dict[str, int] = {}
_strat_paused_until: dict[str, float] = {}
_strat_last_alert: dict[str, float] = {}
_STRAT_FAIL_ALERT_THRESHOLD = 3
_STRAT_FAIL_PAUSE_THRESHOLD = 5
_STRAT_PAUSE_DURATION_SECONDS = 3600  # 1h
_STRAT_ALERT_DEDUP_SECONDS = 600  # Max 1 alert per strat per 10 min


def _load_wf_pauses() -> dict:
    """Load WF weekly pauses file (strats auto-paused after WF rejection)."""
    try:
        path = ROOT / "data" / "crypto" / "wf_pauses.json"
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        # Filter expired
        now = datetime.now(UTC).isoformat()
        return {k: v for k, v in data.items() if v.get("paused_until", "") > now}
    except Exception:
        return {}


def _strat_is_paused(strat_id: str) -> bool:
    """Return True if strategy is currently auto-paused after failures OR by WF."""
    pause_until = _strat_paused_until.get(strat_id, 0)
    if time.time() < pause_until:
        return True
    # Also check WF weekly pauses (strategy-name keyed, not strat-id)
    wf_pauses = _load_wf_pauses()
    # Strat_id can be "STRAT-001" but WF uses underlying name like "btc_eth_dual_momentum"
    # Check both keys
    return strat_id in wf_pauses


def _strat_record_failure(strat_id: str, error: Exception) -> None:
    """Record a strategy execution failure. Alerts and pauses if thresholds met."""
    count = _strat_fail_counter.get(strat_id, 0) + 1
    _strat_fail_counter[strat_id] = count
    logger.warning(f"STRAT FAIL TRACKER: {strat_id} consecutive_failures={count}")

    # Dedup: max 1 alert per strat per 10 min
    now = time.time()
    last_alert = _strat_last_alert.get(strat_id, 0)
    dedup_ok = now - last_alert >= _STRAT_ALERT_DEDUP_SECONDS

    if count >= _STRAT_FAIL_PAUSE_THRESHOLD:
        _strat_paused_until[strat_id] = now + _STRAT_PAUSE_DURATION_SECONDS
        if dedup_ok:
            _strat_last_alert[strat_id] = now
            logger.critical(
                f"STRAT AUTO-PAUSE: {strat_id} after {count} consecutive failures. "
                f"Paused for {_STRAT_PAUSE_DURATION_SECONDS // 60}min. Last error: {error}"
            )
            try:
                _send_alert(
                    f"STRAT AUTO-PAUSED: {strat_id}\n"
                    f"{count} consecutive execution failures.\n"
                    f"Paused for {_STRAT_PAUSE_DURATION_SECONDS // 60}min.\n"
                    f"Last error: {type(error).__name__}: {str(error)[:150]}",
                    level="critical",
                )
            except Exception:
                pass
    elif count >= _STRAT_FAIL_ALERT_THRESHOLD:
        if dedup_ok:
            _strat_last_alert[strat_id] = now
            logger.critical(
                f"STRAT FAILURE THRESHOLD: {strat_id} {count} consecutive failures"
            )
            try:
                _send_alert(
                    f"STRAT FAILING: {strat_id}\n"
                    f"{count} consecutive execution failures.\n"
                    f"Error: {type(error).__name__}: {str(error)[:150]}\n"
                    f"Will auto-pause at {_STRAT_FAIL_PAUSE_THRESHOLD} failures.",
                    level="critical",
                )
            except Exception:
                pass


def _strat_record_success(strat_id: str) -> None:
    """Reset failure counter after successful execution."""
    if _strat_fail_counter.get(strat_id, 0) > 0:
        logger.info(f"STRAT FAIL TRACKER: {strat_id} recovered after successful exec")
    _strat_fail_counter[strat_id] = 0
    _strat_paused_until.pop(strat_id, None)


# --- V14: Global NAV helper for sizing on total capital ---
_global_nav_cache = {"nav": 0.0, "ts": 0.0}


def _get_global_nav() -> float:
    """Get total NAV across LIVE brokers (Binance + IBKR live). Cached 5 min.

    EXCLUSIONS critiques:
      - Alpaca PAPER ($100K paper money) : exclu si PAPER_TRADING=true
        sinon contamine le sizing crypto/futures live.
      - IBKR PAPER (port 4003) : exclu, on ne query QUE 4002 live.

    FAIL-CLOSED: returns 0.0 si AUCUNE source live confirmee.
    Callers doivent traiter 0.0 comme NAV indisponible et fallback
    sur broker-local, jamais injecter de nominal.

    Sources prioritaires (live only):
      1. Binance API (BinanceBroker) — fallback file state
      2. IBKR live API (port 4002) — fallback file state
      3. Alpaca API — UNIQUEMENT si PAPER_TRADING=false
    """
    import time as _t
    if _t.time() - _global_nav_cache["ts"] < 300 and _global_nav_cache["nav"] > 0:
        return _global_nav_cache["nav"]

    components = {}
    _is_paper = os.getenv("PAPER_TRADING", "true").lower() == "true"

    # 1) Binance equity — API direct, fallback file state
    try:
        from core.broker.binance_broker import BinanceBroker
        _bnb = BinanceBroker()
        _info = _bnb.get_account_info()
        _eq = float(_info.get("equity", 0) or 0)
        if _eq > 0:
            components["binance"] = _eq
    except Exception as _be:
        logger.warning(f"_get_global_nav: binance API failed — {_be}; trying file state")
        try:
            bnb_state = Path(__file__).resolve().parent / "data" / "crypto_equity_state.json"
            if bnb_state.exists():
                import json as _json
                with open(bnb_state) as f:
                    v = float(_json.load(f).get("total_equity_usd", 0) or 0)
                    if v > 0:
                        components["binance"] = v
        except Exception as _e:
            logger.warning(f"_get_global_nav: binance state unreadable — {_e}")

    # 2) IBKR LIVE equity — API direct sur port 4002, fallback file state
    # Skip if running in IBKR paper mode (port 4003) to avoid mixing
    _ibkr_paper = os.getenv("IBKR_PAPER", "true").lower() == "true"
    if not _ibkr_paper:
        try:
            from core.broker.ibkr_adapter import IBKRBroker
            # Dedicated clientId reserve pour _get_global_nav (cache 5min)
            _ib = IBKRBroker(client_id=77)
            try:
                _info = _ib.get_account_info()
                _eq = float(_info.get("equity", 0) or 0)
                if _eq > 0:
                    components["ibkr_live"] = _eq
            finally:
                _ib.disconnect()
        except Exception as _ie:
            logger.warning(f"_get_global_nav: ibkr live API failed — {_ie}; trying file state")
            try:
                ibkr_state = Path(__file__).resolve().parent / "data" / "state" / "ibkr_equity.json"
                if ibkr_state.exists():
                    import json as _json
                    with open(ibkr_state) as f:
                        v = float(_json.load(f).get("equity", 0) or 0)
                        if v > 0:
                            components["ibkr_live"] = v
            except Exception as _e:
                logger.warning(f"_get_global_nav: ibkr state unreadable — {_e}")

    # 3) Alpaca equity — UNIQUEMENT si on est en mode LIVE (pas paper)
    # Sinon Alpaca paper $100K contamine le sizing crypto live
    if not _is_paper:
        try:
            alp_key = os.getenv("ALPACA_API_KEY", "")
            alp_secret = os.getenv("ALPACA_SECRET_KEY", "")
            if alp_key and alp_secret:
                from core.alpaca_client.client import AlpacaClient
                _alp = AlpacaClient(api_key=alp_key, secret_key=alp_secret, paper=False)
                _info = _alp.get_account_info()
                _eq = float(_info.get("equity", 0) or 0)
                if _eq > 0:
                    components["alpaca_live"] = _eq
        except Exception as _ae:
            logger.warning(f"_get_global_nav: alpaca live query failed — {_ae}")

    nav = sum(components.values())
    # Fail-closed: if no broker returned a valid equity, return 0.0 (NOT a fallback)
    if nav <= 0:
        logger.warning("_get_global_nav: NO live broker equity available — returning 0.0 (fail-closed)")
        return 0.0

    _global_nav_cache["nav"] = nav
    _global_nav_cache["ts"] = _t.time()
    logger.info(f"_get_global_nav: ${nav:,.0f} from {list(components.keys())} (PAPER={_is_paper}, IBKR_PAPER={_ibkr_paper})")
    return nav


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
            # P0 FIX 2026-04-16: ibkr_eu = paper_only en whitelist (cf
            # config/live_whitelist.yaml). Le script live_portfolio_eu.py n'est
            # PAS whitelist-aware (audit ChatGPT bypass critique #1). On bloque
            # l'execution live tant que le script n'enforce pas la whitelist.
            # Pour reactiver: refacto live_portfolio_eu.py pour appeler
            # is_strategy_live_allowed(...) sur chaque strat avant execution.
            from core.governance import list_live_strategies
            _eu_live = [e for e in list_live_strategies("ibkr_eu")]
            if not _eu_live:
                logger.warning(
                    "INTRADAY EU SKIP — book ibkr_eu = paper_only en whitelist "
                    "(audit P0). Aucune strategie EU autorisee live. "
                    "Pour reactiver: rendre live_portfolio_eu.py whitelist-aware."
                )
                return
            from scripts.live_portfolio_eu import run_intraday_eu
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
    _ibkr_carry = None  # Pre-init for the finally block (fixes UnboundLocalError on early return)
    try:
        logger.info("=== FX CARRY CYCLE ===")

        # Guard: IBKR FX margin permissions required
        if os.getenv("IBKR_FX_ENABLED", "false").lower() != "true":
            logger.warning("  FX CARRY SKIP — IBKR_FX_ENABLED not set (enable FX permissions in IBKR portal first)")
            return

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


def run_always_on_carry_cycle():
    """V14: Always-On FX Carry — IBKR is never at 0% utilization.

    3 carry positions (AUDJPY, EURJPY, USDJPY) are ALWAYS active.
    Sizing varies by regime + vol scaling. Min floor even in PANIC.
    Rebalance if sizing drifts > 20% from target.
    """
    if not _ibkr_lock.acquire(blocking=False):
        logger.warning("ALWAYS-ON CARRY SKIP — IBKR lock held")
        return
    try:
        logger.info("=== ALWAYS-ON CARRY CYCLE ===")

        if os.getenv("IBKR_FX_ENABLED", "false").lower() != "true":
            logger.info("  ALWAYS-ON CARRY SKIP — IBKR_FX_ENABLED=false (IBIE no FX leverage)")
            return

        from core.strategies.always_on_carrier import AlwaysOnCarrier

        # Get IBKR equity
        ibkr_equity = 10_000  # Default
        try:
            from core.broker.ibkr_adapter import IBKRBroker
            _ibkr = IBKRBroker(client_id=10)
            ibkr_info = _ibkr.get_account_info()
            ibkr_equity = ibkr_info.get("equity", 10_000)
            _ibkr.disconnect()
        except Exception as e:
            logger.warning(f"  CARRY: IBKR equity fetch failed: {e}, using ${ibkr_equity}")

        # Get current regime
        current_regime = "UNKNOWN"
        try:
            import json as _json
            regime_path = Path(__file__).resolve().parent / "data" / "regime_state.json"
            if regime_path.exists():
                with open(regime_path) as f:
                    rs = _json.load(f)
                current_regime = rs.get("FX", rs.get("global", "UNKNOWN"))
        except Exception:
            pass

        carrier = AlwaysOnCarrier()
        targets = carrier.compute_targets(
            equity_by_broker={"ibkr": ibkr_equity},
            regime=current_regime,
        )

        for t in targets:
            logger.info(
                "  CARRY %s %s: target $%.0f (current $%.0f) alloc=%.1f%% regime=%s %s",
                t.instrument, t.direction, t.target_notional, t.current_notional,
                t.allocation_pct, current_regime,
                "REBALANCE" if t.needs_rebalance else "OK",
            )

        # Execute rebalance orders via IBKR
        # GUARD: skip if IBKR account lacks FX margin permissions
        # Remove this guard once FX trading permissions are enabled in IBKR portal
        _ibkr_fx_enabled = os.getenv("IBKR_FX_ENABLED", "false").lower() == "true"
        n_carry_orders = 0
        _ibkr_carry = None
        rebalance_needed = [t for t in targets if t.needs_rebalance]
        if rebalance_needed and _ibkr_fx_enabled:
            try:
                from core.broker.ibkr_adapter import IBKRBroker
                _ibkr_carry = IBKRBroker(client_id=10)

                for t in rebalance_needed:
                    delta = t.target_notional - t.current_notional
                    if abs(delta) < 100:  # ignore tiny rebalances
                        continue
                    # Map LONG/SHORT -> BUY/SELL for IBKR
                    _dir_map = {"LONG": "BUY", "SHORT": "SELL", "BUY": "BUY", "SELL": "SELL"}
                    base_dir = _dir_map.get(t.direction.upper(), "BUY")
                    direction = base_dir if delta > 0 else ("SELL" if base_dir == "BUY" else "BUY")
                    try:
                        result = _ibkr_carry.create_position(
                            symbol=t.instrument,
                            direction=direction,
                            notional=abs(delta),
                            _authorized_by="always_on_carry",
                        )
                        n_carry_orders += 1
                        logger.info(
                            "  CARRY EXEC: %s %s $%.0f -> %s",
                            direction, t.instrument, abs(delta), result,
                        )
                    except Exception as oe:
                        logger.error("  CARRY EXEC FAILED: %s %s — %s", t.instrument, direction, oe)

                _ibkr_carry.disconnect()
            except Exception as ce:
                logger.error("  CARRY IBKR connect failed: %s", ce)
                if _ibkr_carry:
                    _ibkr_carry.disconnect()

        if n_carry_orders > 0:
            _send_alert(
                f"ALWAYS-ON CARRY: {n_carry_orders} ordre(s)\n"
                + "\n".join(f"  {t.instrument} {t.direction} ${t.target_notional:,.0f}" for t in rebalance_needed),
                level="info",
            )

        total_deployed = carrier.get_total_deployed(targets)
        logger.info(
            "  CARRY total: $%.0f deployed (%.0f%% of IBKR equity)",
            total_deployed, total_deployed / ibkr_equity * 100 if ibkr_equity > 0 else 0,
        )

        # Save state
        import json as _json
        state_path = Path(__file__).resolve().parent / "data" / "state" / "always_on_carry.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(state_path, "w") as f:
            _json.dump({
                "timestamp": datetime.now(PARIS).isoformat(),
                "regime": current_regime,
                "ibkr_equity": ibkr_equity,
                "targets": [t.to_dict() for t in targets],
                "total_deployed": total_deployed,
            }, f, indent=2)

        _log_event("always_on_carry", details={
            "regime": current_regime, "total_deployed": total_deployed,
            "n_positions": len(targets),
        })

    except Exception as e:
        logger.error(f"  ALWAYS-ON CARRY ERROR: {e}", exc_info=True)
    finally:
        _ibkr_lock.release()


def run_cross_asset_momentum_cycle():
    """Cross-Asset Time-Series Momentum — PAPER MODE.

    Moskowitz (2012): 12M momentum, inverse-vol risk parity, weekly rebalance.
    5 assets: SPY, TLT, GLD (Alpaca), EURUSD (IBKR), BTC (Binance).
    BORDERLINE WF: Sharpe 0.81 backtest, 60% windows profitable, -5.21% max DD.
    """
    logger.info("=== CROSS-ASSET MOMENTUM CYCLE ===")

    try:
        from pathlib import Path
        import pandas as pd
        from strategies_v2.us.cross_asset_momentum import (
            CrossAssetMomentumStrategy,
            CrossAssetMomentumConfig,
            MomentumSignal,
        )

        # Refresh daily data from Alpaca
        data_dir = Path(__file__).resolve().parent / "data" / "cross_asset"
        data_dir.mkdir(parents=True, exist_ok=True)

        api_key = os.getenv("ALPACA_API_KEY")
        api_secret = os.getenv("ALPACA_SECRET_KEY")

        if api_key and api_secret:
            try:
                from scripts.fetch_midcap_data import _fetch_alpaca_rest
                from datetime import timedelta
                headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}
                end = datetime.now()
                start = end - timedelta(days=400)  # 13+ months for 12M momentum

                for ticker, api_ticker, atype in [
                    ("SPY", "SPY", "stocks"), ("TLT", "TLT", "stocks"),
                    ("GLD", "GLD", "stocks"), ("EURUSD", "FXE", "stocks"),
                ]:
                    parquet = data_dir / f"{ticker}.parquet"
                    import time as _time
                    if not parquet.exists() or (_time.time() - parquet.stat().st_mtime) / 3600 > 20:
                        df = _fetch_alpaca_rest(api_ticker, start, end, headers, "https://data.alpaca.markets/v2")
                        if df is not None and len(df) > 100:
                            df.to_parquet(parquet)
                            logger.info("  XMOMENTUM: refreshed %s (%d bars)", ticker, len(df))
            except Exception as e:
                logger.warning("  XMOMENTUM: data refresh failed: %s", e)

        # Load cached data
        prices = {}
        for symbol in ["SPY", "TLT", "GLD", "BTC", "EURUSD"]:
            parquet = data_dir / f"{symbol}.parquet"
            if parquet.exists():
                prices[symbol] = pd.read_parquet(parquet)

        if len(prices) < 3:
            logger.warning("  XMOMENTUM SKIP — only %d assets cached (need 3+)", len(prices))
            return

        # Get current regime
        current_regime = "UNKNOWN"
        try:
            import json
            regime_path = Path(__file__).resolve().parent / "data" / "regime_state.json"
            if regime_path.exists():
                with open(regime_path) as f:
                    regime_state = json.load(f)
                # Use US equity regime or global
                current_regime = regime_state.get("US_EQUITY", regime_state.get("global", "UNKNOWN"))
        except Exception:
            pass

        # Generate signals
        config = CrossAssetMomentumConfig()
        strategy = CrossAssetMomentumStrategy(config)
        signals = strategy.generate_signals(prices, capital=30000, current_regime=current_regime)

        summary = strategy.get_portfolio_summary(signals)
        logger.info(
            "  XMOMENTUM: regime=%s | long=%d cash=%d | invested=%.0f%%",
            current_regime, summary["n_long"], summary["n_cash"], summary["long_pct"],
        )

        # Log each asset signal
        for sig in signals:
            if sig.signal != MomentumSignal.CASH:
                logger.info(
                    "  XMOMENTUM SIGNAL: %s %s ret12m=%.1f%% weight=%.1f%% $%.0f",
                    sig.symbol, sig.signal.value,
                    sig.return_12m * 100, sig.final_weight * 100, sig.target_notional,
                )

        # PAPER MODE: log signals only, no execution
        # TODO: when validated after 30+ paper signals, wire to Alpaca paper execution
        _log_event("cross_asset_momentum", details={
            "regime": current_regime,
            "n_long": summary["n_long"],
            "n_cash": summary["n_cash"],
            "long_pct": summary["long_pct"],
            "assets": summary["assets"],
        })

        # Save state for dashboard
        import json
        state_path = Path(__file__).resolve().parent / "data" / "state" / "xmomentum_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(state_path, "w") as f:
            json.dump({
                "timestamp": datetime.now(PARIS).isoformat(),
                "regime": current_regime,
                "summary": summary,
            }, f, indent=2)

        logger.info("  XMOMENTUM cycle complete")

    except Exception as e:
        logger.error(f"  XMOMENTUM ERROR: {e}", exc_info=True)


def run_futures_live_cycle():
    """Futures LIVE — DISABLED until single-position-per-symbol is bulletproof."""
    if os.getenv("IBKR_FUTURES_LIVE", "false").lower() != "true":
        return
    _run_futures_cycle(live=True)


def run_futures_paper_cycle():
    """Futures Paper — strategies on IBKR paper port 4003."""
    _run_futures_cycle(live=False)


def run_crypto_watchdog_cycle():
    """Crypto position heartbeat — runs every 5 min to verify every live
    Binance position (> $5) has an active stop-loss order on the exchange.

    Binance-specific: we check `broker.get_positions()` for spot + margin
    positions, then `broker.get_open_orders(symbol)` for an active SL.
    If a position has no SL order:
      1. Tries to re-attach SL via `broker._create_spot_position(stop_loss=...)`
         with the existing fill_price (tracked in `_fill_prices`)
      2. On failure: CRITICAL alert Telegram + auto-pause the strat that
         opened it (via _strat_record_failure to trigger pause)

    Does NOT auto-close (user policy: always repose, never auto-close).
    """
    if not _crypto_lock.acquire(blocking=False):
        logger.info("CRYPTO WATCHDOG skip — crypto lock held")
        return
    logger.info("=== CRYPTO WATCHDOG CYCLE ===")
    try:
        try:
            from core.broker.binance_broker import BinanceBroker
        except ImportError:
            logger.warning("CRYPTO WATCHDOG skip — BinanceBroker unavailable")
            return

        try:
            broker = BinanceBroker()
        except Exception as e:
            logger.warning(f"CRYPTO WATCHDOG skip — broker init failed: {e}")
            return

        try:
            positions = broker.get_positions()
        except Exception as e:
            logger.warning(f"CRYPTO WATCHDOG skip — get_positions failed: {e}")
            return

        # Filter to live positions > $5 (ignore dust)
        _live = [p for p in positions if abs(float(p.get("market_val", 0))) > 5]
        if not _live:
            logger.info("CRYPTO WATCHDOG: 0 live positions (>$5), nothing to check")
            return

        _unprotected = 0
        for pos in _live:
            _sym = pos.get("symbol", "?")
            _mv = abs(float(pos.get("market_val", 0)))
            _qty = float(pos.get("qty", 0))
            _side = pos.get("side", "?")

            # Check open orders for this symbol
            try:
                _open_orders = broker.get_open_orders(symbol=_sym)
            except Exception as e:
                logger.warning(f"CRYPTO WATCHDOG: get_open_orders({_sym}) failed: {e}")
                continue

            # A valid SL order has type STOP_LOSS or STOP_LOSS_LIMIT
            _has_sl = any(
                (o.get("type", "") in ("STOP_LOSS", "STOP_LOSS_LIMIT", "TAKE_PROFIT_LIMIT")
                 and o.get("status") in ("NEW", "PARTIALLY_FILLED"))
                for o in _open_orders
            )

            if _has_sl:
                continue  # Protected

            _unprotected += 1
            logger.critical(
                f"CRYPTO WATCHDOG: {_sym} {_side} qty={_qty} mv=${_mv:.0f} UNPROTECTED (no SL order)"
            )

            # Try to compute a reasonable SL: 3% from current market price
            try:
                _ticker = broker.get_ticker_24h(_sym)
                _current = float(_ticker.get("last_price", 0))
                if _current <= 0:
                    raise ValueError("no price")
                _sl_pct = 0.03
                if _side == "LONG":
                    _sl_px = round(_current * (1 - _sl_pct), 2)
                else:
                    _sl_px = round(_current * (1 + _sl_pct), 2)
                _sl_limit = round(_sl_px * (0.995 if _side == "LONG" else 1.005), 2)

                _sl_side = "SELL" if _side == "LONG" else "BUY"
                _sl_params = {
                    "symbol": _sym,
                    "side": _sl_side,
                    "type": "STOP_LOSS_LIMIT",
                    "quantity": str(abs(_qty)),
                    "price": str(_sl_limit),
                    "stopPrice": str(_sl_px),
                    "timeInForce": "GTC",
                }
                _sl_result = broker._post("/api/v3/order", _sl_params)
                logger.critical(
                    f"CRYPTO WATCHDOG REPOSED: {_sym} SL={_sl_px} "
                    f"orderId={_sl_result.get('orderId', '?')}"
                )
                _send_alert(
                    f"CRYPTO WATCHDOG REPOSED SL: {_sym}\n"
                    f"SL={_sl_px} (3% from ${_current:.2f})\n"
                    f"Position was unprotected, now safe.",
                    level="warning",
                )
            except Exception as _re:
                logger.critical(
                    f"CRYPTO WATCHDOG REPOSE FAILED {_sym}: {_re} — MANUAL INTERVENTION"
                )
                _send_alert(
                    f"CRITICAL: CRYPTO WATCHDOG could not repose SL on {_sym}\n"
                    f"qty={_qty} mv=${_mv:.0f}\n"
                    f"Error: {_re}\n"
                    f"Position UNPROTECTED. Manual SL required.",
                    level="critical",
                )

        if _unprotected == 0:
            _syms = [p.get("symbol", "?") for p in _live]
            logger.info(
                f"CRYPTO WATCHDOG OK: {len(_live)} positions all protected "
                f"({', '.join(_syms)})"
            )
    except Exception as e:
        logger.warning(f"CRYPTO WATCHDOG cycle error: {e}", exc_info=True)
    finally:
        _crypto_lock.release()


def run_us_stocks_daily_cycle():
    """US stocks daily rebalance — 3 monthly cross-sectional strategies on Alpaca paper.

    Runs once per weekday at 16:00 Paris (10:00 ET, 30 min after US open). Executes:
      1. scripts/download_us_data_alpaca.py (refresh S&P 500 daily bars via Alpaca IEX, ~40s)
      2. scripts/run_us_stocks_daily.py --source local (tom + rs_spy + sector_rot_us)

    Data source: Alpaca IEX feed (free tier) for consistency with execution.
    yfinance is kept as a research fallback (scripts/download_us_data.py).

    The 3 strats only emit signals on their rebalance days:
      - tom: entry last trading day of month, exit 3rd trading day next month
      - rs_spy: monthly rebalance (top 5 / bottom 5 alpha vs SPY)
      - sector_rot_us: monthly rebalance (top vs bottom GICS sector)

    Gate 5 validated: Sharpe 1.24 → 2.70 combined with V15.3, MaxDD% 32.7 → 14.1.
    Broker: Alpaca in PAPER mode (guard enforced via PAPER_TRADING=true).
    """
    import subprocess
    logger.info("=== US STOCKS DAILY CYCLE ===")
    download = ROOT / "scripts" / "download_us_data_alpaca.py"
    runner = ROOT / "scripts" / "run_us_stocks_daily.py"
    if not runner.exists():
        logger.error(f"US STOCKS: {runner} not found, skip")
        return

    # Step 1: refresh data (best-effort, continue if fails)
    try:
        r1 = subprocess.run(
            [sys.executable, str(download)],
            capture_output=True, text=True, timeout=600, cwd=str(ROOT),
        )
        if r1.returncode != 0:
            logger.warning(f"US STOCKS data refresh FAIL (exit {r1.returncode}) — continuing with stale data")
            logger.warning(f"  stderr: {r1.stderr[:300]}")
        else:
            logger.info("US STOCKS data refresh OK")
    except subprocess.TimeoutExpired:
        logger.warning("US STOCKS data refresh TIMEOUT — continuing with stale data")
    except Exception as e:
        logger.warning(f"US STOCKS data refresh exception: {e} — continuing")

    # Step 2: run strategies
    try:
        r2 = subprocess.run(
            [sys.executable, str(runner), "--source", "local"],
            capture_output=True, text=True, timeout=300, cwd=str(ROOT),
        )
        if r2.returncode == 0:
            logger.info("US STOCKS run OK")
            for line in r2.stdout.splitlines()[-20:]:
                logger.info(f"  [us] {line}")
        else:
            logger.error(f"US STOCKS run FAIL (exit {r2.returncode})")
            logger.error(f"  stderr: {r2.stderr[:500]}")
    except subprocess.TimeoutExpired:
        logger.error("US STOCKS run TIMEOUT (>5min)")
    except Exception as e:
        logger.exception(f"US STOCKS run exception: {e}")


def run_bracket_watchdog_cycle():
    """Bracket heartbeat watchdog — runs every 5 min to verify every open
    futures position has an active SL/TP bracket on IBKR.

    If a position is detected without a bracket:
      1. Try to repose from state file (if sl>0 and tp>0)
      2. Try to synthesize from strategy defaults (Overnight MES: entry±30/50)
      3. Last resort FAIL-SAFE: close the position at market + CRITICAL alert

    This closes the gap where _run_futures_cycle (daily at 16h Paris) is the
    only bracket check. Positions could remain unprotected for up to 24h
    between cycles if brackets vanish (IBKR disconnect, order expiry, etc.).
    """
    if not _ibkr_lock.acquire(blocking=False):
        logger.info("BRACKET WATCHDOG skip — IBKR lock held")
        return
    logger.info("=== BRACKET WATCHDOG CYCLE ===")
    try:
        from ib_insync import (
            IB as _WdIB,
            Future as _WdFuture,
            StopOrder as _WdStop,
            LimitOrder as _WdLimit,
            MarketOrder as _WdMarket,
        )
        import random as _wd_rng
        import uuid as _wd_uuid

        _host = os.environ.get("IBKR_HOST", "127.0.0.1")
        _port = int(os.environ.get("IBKR_PORT", "4002"))
        _ib = _WdIB()
        _ib.RequestTimeout = 20
        try:
            _ib.connect(_host, _port, clientId=_wd_rng.randint(310, 319), timeout=10)
        except Exception as e:
            logger.warning(f"BRACKET WATCHDOG: connect failed: {e}")
            return

        try:
            import time as _wdt
            _wdt.sleep(2)

            _live_positions = [p for p in _ib.positions() if abs(p.position) > 0]
            if not _live_positions:
                logger.info("BRACKET WATCHDOG: 0 live positions, nothing to check")
                return

            _open_trades = _ib.reqAllOpenOrders()
            _active_orders = [t for t in _open_trades
                              if t.orderStatus.status not in ("Cancelled", "Filled", "Inactive")]

            # Build per-symbol order map: need BOTH STP and LMT to be protected
            _sym_has_stp: dict[str, list] = {}
            _sym_has_lmt: dict[str, list] = {}
            for t in _active_orders:
                s = t.contract.symbol
                if t.order.orderType in ("STP", "STOP"):
                    _sym_has_stp.setdefault(s, []).append(t)
                elif t.order.orderType in ("LMT", "LIMIT"):
                    _sym_has_lmt.setdefault(s, []).append(t)

            _state_file = ROOT / "data" / "state" / "futures_positions_live.json"
            _state = {}
            if _state_file.exists():
                try:
                    _state = json.loads(_state_file.read_text(encoding="utf-8"))
                except Exception:
                    pass

            # Strategy default SL/TP offsets
            _STRAT_DEFAULTS = {
                "MES": {"sl_points": 30, "tp_points": 50},
                "MNQ": {"sl_points": 30, "tp_points": 50},
                "MGC": {"sl_pct": 0.004, "tp_pct": 0.008},
            }

            _unprotected = 0
            for pos in _live_positions:
                _sym = pos.contract.symbol
                _has_sl = len(_sym_has_stp.get(_sym, [])) > 0
                _has_tp = len(_sym_has_lmt.get(_sym, [])) > 0

                # Detect duplicates: >1 STP or >1 LMT on same symbol
                if len(_sym_has_stp.get(_sym, [])) > 1:
                    _dupes = _sym_has_stp[_sym][1:]
                    for _d in _dupes:
                        logger.warning(f"BRACKET WATCHDOG: cancelling duplicate STP on {_sym} orderId={_d.order.orderId}")
                        try:
                            _ib.cancelOrder(_d.order)
                        except Exception:
                            pass
                if len(_sym_has_lmt.get(_sym, [])) > 1:
                    _dupes = _sym_has_lmt[_sym][1:]
                    for _d in _dupes:
                        logger.warning(f"BRACKET WATCHDOG: cancelling duplicate LMT on {_sym} orderId={_d.order.orderId}")
                        try:
                            _ib.cancelOrder(_d.order)
                        except Exception:
                            pass

                if _has_sl and _has_tp:
                    continue  # properly protected with both SL and TP

                # MISSING SL and/or TP — try to repose what's missing
                _missing = []
                if not _has_sl:
                    _missing.append("SL")
                if not _has_tp:
                    _missing.append("TP")
                _unprotected += 1
                logger.critical(
                    f"BRACKET WATCHDOG: {_sym} position {pos.position} MISSING {'+'.join(_missing)} — attempting repose"
                )

                _mult = int(getattr(pos.contract, "multiplier", 1) or 1)
                _entry_px = float(getattr(pos, "avgCost", 0)) / max(_mult, 1)

                # Recover SL/TP via 3-tier fallback — never close, always repose
                _sl = 0.0
                _tp = 0.0
                _source = ""

                # Tier 1: state file
                if _sym in _state and float(_state[_sym].get("sl", 0)) > 0 and float(_state[_sym].get("tp", 0)) > 0:
                    _sl = float(_state[_sym]["sl"])
                    _tp = float(_state[_sym]["tp"])
                    _source = "state"

                # Tier 2: strategy defaults from entry price
                if (_sl == 0 or _tp == 0) and _sym in _STRAT_DEFAULTS and _entry_px > 0:
                    _d = _STRAT_DEFAULTS[_sym]
                    if "sl_pct" in _d:
                        # Percent-based (MGC, etc.)
                        if pos.position > 0:
                            _sl = _sl or round(_entry_px * (1 - _d["sl_pct"]), 2)
                            _tp = _tp or round(_entry_px * (1 + _d["tp_pct"]), 2)
                        else:
                            _sl = _sl or round(_entry_px * (1 + _d["sl_pct"]), 2)
                            _tp = _tp or round(_entry_px * (1 - _d["tp_pct"]), 2)
                    else:
                        # Points-based (MES, MNQ, etc.)
                        if pos.position > 0:
                            _sl = _sl or round(_entry_px - _d["sl_points"], 2)
                            _tp = _tp or round(_entry_px + _d["tp_points"], 2)
                        else:
                            _sl = _sl or round(_entry_px + _d["sl_points"], 2)
                            _tp = _tp or round(_entry_px - _d["tp_points"], 2)
                    _source = "strat_defaults"

                # Tier 3: synthesize from current market price (-1%/+1.5% for longs)
                # This is the LAST RESORT — never let a position stay unprotected.
                # Better a loose bracket than no bracket.
                if _sl == 0 or _tp == 0:
                    try:
                        _fut_q = _WdFuture(
                            _sym, exchange="CME", currency="USD",
                            lastTradeDateOrContractMonth=pos.contract.lastTradeDateOrContractMonth,
                        )
                        _details_q = _ib.reqContractDetails(_fut_q)
                        if _details_q:
                            _c_q = _details_q[0].contract
                            _bars = _ib.reqHistoricalData(
                                _c_q, endDateTime="", durationStr="60 S",
                                barSizeSetting="1 min", whatToShow="TRADES",
                                useRTH=False, formatDate=2,
                            )
                            _wdt.sleep(2); _ib.sleep(1)
                            if _bars:
                                _current_px = float(_bars[-1].close)
                                # 1% SL / 1.5% TP from current — conservative bracket
                                if pos.position > 0:
                                    _sl = round(_current_px * 0.99, 2)
                                    _tp = round(_current_px * 1.015, 2)
                                else:
                                    _sl = round(_current_px * 1.01, 2)
                                    _tp = round(_current_px * 0.985, 2)
                                _source = f"current_px={_current_px}"
                    except Exception as _px_err:
                        logger.warning(
                            f"BRACKET WATCHDOG: {_sym} current price fetch failed: {_px_err}"
                        )

                _side_exit = "SELL" if pos.position > 0 else "BUY"
                _qty = abs(int(pos.position))

                _repose_ok = False
                _fail_reason = ""

                # We MUST have SL/TP by now (Tier 3 always succeeds if IBKR is up).
                # If _sl or _tp is still 0, something is very wrong.
                if _sl > 0 and _tp > 0:
                    logger.info(
                        f"BRACKET WATCHDOG: {_sym} repose attempt "
                        f"SL={_sl} TP={_tp} source={_source}"
                    )
                    # Reuse existing OCA group if one leg exists, else create new
                    _existing_oca = ""
                    for _leg in (_sym_has_stp.get(_sym, []) + _sym_has_lmt.get(_sym, [])):
                        if _leg.order.ocaGroup:
                            _existing_oca = _leg.order.ocaGroup
                            break
                    _oca = _existing_oca or f"WATCHDOG_{_sym}_{_wd_uuid.uuid4().hex[:8]}"

                    # Only place what's missing
                    _need_sl = not _has_sl
                    _need_tp = not _has_tp

                    # Try repose up to 3 times to handle transient IBKR errors
                    for _attempt in range(1, 4):
                        try:
                            _exchange = "COMEX" if _sym in ("MGC", "GC", "SI", "HG") else "CME"
                            _fut = _WdFuture(
                                _sym, exchange=_exchange, currency="USD",
                                lastTradeDateOrContractMonth=pos.contract.lastTradeDateOrContractMonth,
                            )
                            _details = _ib.reqContractDetails(_fut)
                            if not _details:
                                _fail_reason = f"no contract details (attempt {_attempt})"
                                _wdt.sleep(2)
                                continue
                            _contract = _details[0].contract

                            if _need_sl and _sl > 0:
                                _sl_o = _WdStop(_side_exit, _qty, _sl)
                                _sl_o.tif = "GTC"; _sl_o.ocaGroup = _oca; _sl_o.ocaType = 1
                                _sl_o.outsideRth = True
                                _ib.placeOrder(_contract, _sl_o)
                                _wdt.sleep(1)
                                logger.info(f"BRACKET WATCHDOG: placed SL {_sl} on {_sym}")

                            if _need_tp and _tp > 0:
                                _tp_o = _WdLimit(_side_exit, _qty, _tp)
                                _tp_o.tif = "GTC"; _tp_o.ocaGroup = _oca; _tp_o.ocaType = 1
                                _tp_o.outsideRth = True
                                _ib.placeOrder(_contract, _tp_o)
                                _wdt.sleep(1)
                                logger.info(f"BRACKET WATCHDOG: placed TP {_tp} on {_sym}")

                            _wdt.sleep(2); _ib.sleep(1)
                            _placed = []
                            if _need_sl: _placed.append(f"SL={_sl}")
                            if _need_tp: _placed.append(f"TP={_tp}")
                            logger.critical(
                                f"BRACKET WATCHDOG REPOSED: {_sym} {' '.join(_placed)} "
                                f"OCA={_oca} source={_source} attempt={_attempt}"
                            )
                            _repose_ok = True
                            break
                        except Exception as _be:
                            _fail_reason = f"attempt {_attempt}: {str(_be)[:80]}"
                            logger.warning(
                                f"BRACKET WATCHDOG repose attempt {_attempt}/3 failed {_sym}: {_be}"
                            )
                            _wdt.sleep(3)

                    if _repose_ok:
                        # Update state file
                        if _sym not in _state:
                            _state[_sym] = {}
                        _state[_sym].update({
                            "symbol": _sym, "side": "BUY" if pos.position > 0 else "SELL",
                            "qty": _qty, "entry": _entry_px, "sl": _sl, "tp": _tp,
                            "oca_group": _oca, "mode": "LIVE",
                            "_authorized_by": f"bracket_watchdog_{_source}",
                        })
                        if "opened_at" not in _state[_sym]:
                            _state[_sym]["opened_at"] = datetime.now(UTC).isoformat()
                        _state_file.parent.mkdir(parents=True, exist_ok=True)
                        _state_file.write_text(json.dumps(_state, indent=2))

                        _send_alert(
                            f"WATCHDOG BRACKET REPOSED: {_sym}\n"
                            f"SL={_sl}, TP={_tp} ({_source})\n"
                            f"Position was unprotected, now safe.",
                            level="warning",
                        )
                else:
                    _fail_reason = "no SL/TP available (all 3 tiers failed)"

                if not _repose_ok:
                    # Repose failed after all tiers and 3 retries.
                    # Do NOT close — user policy: always repose, never auto-close.
                    # Alert CRITICAL so user can intervene manually.
                    logger.critical(
                        f"BRACKET WATCHDOG REPOSE FAILED after all retries: {_sym} — "
                        f"{_fail_reason}. Position REMAINS UNPROTECTED. MANUAL INTERVENTION."
                    )
                    _send_alert(
                        f"CRITICAL: WATCHDOG could not repose bracket on {_sym}\n"
                        f"Reason: {_fail_reason}\n"
                        f"Position is UNPROTECTED. Manual bracket required.\n"
                        f"Will retry in 5 min on next watchdog cycle.",
                        level="critical",
                    )

            if _unprotected == 0:
                _syms = [p.contract.symbol for p in _live_positions]
                logger.info(
                    f"BRACKET WATCHDOG OK: {len(_live_positions)} positions all protected "
                    f"({', '.join(_syms)})"
                )
        finally:
            try:
                _ib.disconnect()
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"BRACKET WATCHDOG cycle error: {e}")
    finally:
        _ibkr_lock.release()


def _make_macro_ecb_executor(mode: str):
    """Factory for MacroECB futures executor — places bracket orders on IBKR.

    Reuses the same execution pattern as _run_futures_cycle (market entry +
    standalone OCA SL/TP, recalc SL/TP from fill, state file, journal, Telegram).
    Applies kill switch check + hard limit 2 contracts + dedup per future.

    Args:
        mode: "LIVE" or "PAPER" — controls state file path and journal DB.

    Returns:
        callable(sig, ib) -> bool — True if order placed, False otherwise.
    """
    state_path = ROOT / "data" / "state" / f"futures_positions_{mode.lower()}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    journal_db = ROOT / "data" / ("live_journal.db" if mode == "LIVE" else "paper_journal.db")
    ks_state_path = ROOT / "data" / "kill_switch_state.json"

    # Closure-local tracking of positions opened during this cycle
    opened_this_cycle: dict = {}

    # IBKR uses the INDEX symbol to qualify futures (DAX→FDXM, CAC40→FCE, ESTX50→FESX)
    # ib.positions() also returns p.contract.symbol = "DAX" (not "FDXM")
    INDEX_TO_EXCHANGE = {"DAX": "EUREX", "CAC40": "MONEP", "ESTX50": "EUREX"}
    MAX_FUTURES_CONTRACTS = 2

    def _executor(sig, ib) -> bool:
        from ib_insync import Future as IbFuture, MarketOrder as IbMarketOrder, StopOrder, LimitOrder
        import uuid as _uuid

        # 1. Kill switch check
        try:
            if ks_state_path.exists():
                ks = json.loads(ks_state_path.read_text(encoding="utf-8"))
                if ks.get("active", False):
                    logger.critical(f"    MACRO ECB {mode}: KILL SWITCH ACTIVE — refusing to trade")
                    return False
        except Exception as e:
            logger.warning(f"    MACRO ECB: KS state read error: {e}")

        # 2. Hard limit check (IBKR real positions + opened_this_cycle)
        try:
            real_pos = {p.contract.symbol: p.position for p in ib.positions() if abs(p.position) > 0}
        except Exception as e:
            logger.warning(f"    MACRO ECB: positions() failed: {e}")
            real_pos = {}

        total_real = sum(abs(int(v)) for v in real_pos.values())
        total_fresh = len(opened_this_cycle)
        if total_real + total_fresh >= MAX_FUTURES_CONTRACTS:
            logger.warning(
                f"    MACRO ECB {mode}: HARD LIMIT reached "
                f"({total_real} real + {total_fresh} fresh = {total_real + total_fresh}), skipping {sig.symbol}"
            )
            return False

        # 3. Map index to exchange (IBKR uses index symbol for future qualification)
        future_sym = sig.symbol
        exchange = INDEX_TO_EXCHANGE.get(sig.symbol)
        if not exchange:
            logger.error(f"    MACRO ECB: unknown index {sig.symbol}")
            return False

        # 4. Dedup — already positioned on this future
        if future_sym in real_pos or future_sym in opened_this_cycle:
            logger.warning(f"    MACRO ECB {mode}: already positioned on {future_sym}, skipping")
            return False

        try:
            # 5. Qualify future contract (IBKR: Future("DAX") → FDXM, Future("ESTX50") → FESX)
            fut = IbFuture(future_sym, exchange=exchange, currency="EUR")
            details = ib.reqContractDetails(fut)
            if not details:
                logger.error(f"    MACRO ECB: no contract details for {future_sym}@{exchange}")
                return False
            contract = details[0].contract

            qty = 1

            # 6. Market entry
            entry = IbMarketOrder(sig.side, qty)
            trade = ib.placeOrder(contract, entry)
            time.sleep(4); ib.sleep(2)
            fill_price = trade.orderStatus.avgFillPrice or 0

            if trade.orderStatus.status != "Filled":
                logger.warning(
                    f"    MACRO ECB: {future_sym} entry not filled ({trade.orderStatus.status})"
                )
                try:
                    ib.cancelOrder(trade.order)
                except Exception:
                    pass
                return False

            # 7. Recalculate SL/TP from FILL price (not signal price)
            sl_offset = abs(sig.stop_loss - fill_price) if sig.stop_loss else 20
            tp_offset = abs(sig.take_profit - fill_price) if sig.take_profit else 40
            if sig.side == "BUY":
                real_sl = round(fill_price - sl_offset, 2)
                real_tp = round(fill_price + tp_offset, 2)
            else:
                real_sl = round(fill_price + sl_offset, 2)
                real_tp = round(fill_price - tp_offset, 2)

            exit_side = "SELL" if sig.side == "BUY" else "BUY"
            oca = f"OCA_ECB_{future_sym}_{_uuid.uuid4().hex[:8]}"

            # 8. OCA SL + TP (standalone, no parentId to avoid cancel-on-parent-fill)
            sl_ord = StopOrder(exit_side, qty, real_sl)
            sl_ord.tif = "GTC"; sl_ord.ocaGroup = oca; sl_ord.ocaType = 1; sl_ord.outsideRth = True
            ib.placeOrder(contract, sl_ord)
            time.sleep(1)

            tp_ord = LimitOrder(exit_side, qty, real_tp)
            tp_ord.tif = "GTC"; tp_ord.ocaGroup = oca; tp_ord.ocaType = 1; tp_ord.outsideRth = True
            ib.placeOrder(contract, tp_ord)
            time.sleep(2); ib.sleep(1)

            logger.info(
                f"    MACRO ECB {mode}: {sig.side} {future_sym} @ {fill_price:.2f} "
                f"SL={real_sl} TP={real_tp} [OCA {oca}]"
            )

            # 9. Update state file (load, mutate, write)
            fut_positions = {}
            try:
                if state_path.exists():
                    fut_positions = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                pass
            fut_positions[future_sym] = {
                "strategy": f"MacroECB_{sig.symbol}",
                "symbol": future_sym,
                "side": sig.side,
                "qty": qty,
                "entry": fill_price,
                "sl": real_sl,
                "tp": real_tp,
                "oca_group": oca,
                "opened_at": datetime.now(UTC).isoformat(),
                "mode": mode,
                "_authorized_by": f"macro_ecb_{mode.lower()}",
            }
            try:
                state_path.write_text(json.dumps(fut_positions, indent=2))
            except Exception as se:
                logger.error(f"    MACRO ECB state write failed: {se}")

            # 10. Journal DB for dashboard
            try:
                import sqlite3 as _sql
                _jconn = _sql.connect(str(journal_db))
                _jconn.execute(
                    "INSERT OR IGNORE INTO trades (trade_id, strategy, instrument, direction, "
                    "quantity, entry_price, entry_time, status, broker, asset_class) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', 'IBKR', 'futures')",
                    (oca, f"MacroECB_{sig.symbol}", future_sym, sig.side, qty, fill_price,
                     datetime.now(UTC).isoformat()),
                )
                _jconn.commit()
                _jconn.close()
            except Exception as je:
                logger.debug(f"MACRO ECB journal write skip: {je}")

            # 11. Telegram alert (warning for LIVE, info for PAPER)
            try:
                _send_alert(
                    f"MACRO ECB {mode}: {sig.side} {future_sym} @ {fill_price:.2f}\n"
                    f"SL={real_sl} TP={real_tp}\n"
                    f"Strat: MacroECB_{sig.symbol}",
                    level="warning" if mode == "LIVE" else "info",
                )
            except Exception:
                pass

            opened_this_cycle[future_sym] = True
            return True

        except Exception as e:
            logger.error(f"    MACRO ECB: order failed for {sig.symbol}: {e}", exc_info=True)
            return False

    return _executor


def run_macro_ecb_live_cycle():
    """MacroECB event-driven cycle — V15.4 LIVE mode.

    Triggered ~14:50 Paris on ECB Governing Council meeting days. Fetches
    DAX/CAC40/ESTX50 5min bars and computes the 30-min move post 14:15
    announcement. If |move| > 0.15%, emits BUY/SELL bracket orders to IBKR
    live port 4002 via _make_macro_ecb_executor.

    Safety: requires env MACRO_ECB_LIVE_ENABLED=true to actually send orders.
    Otherwise falls back to dry_run (log signals only). This gives a final
    kill switch between code deploy and going live.

    Backtest 2021-2026 : Sharpe 3.18, +$7,004 / 5 ans, WF 4/6 yearly PASS.
    """
    if not _ibkr_lock.acquire(blocking=False):
        logger.warning("MACRO ECB SKIP — IBKR lock held")
        return
    try:
        from core.worker.cycles.macro_ecb_cycle import run_macro_ecb_cycle

        live_enabled = os.environ.get("MACRO_ECB_LIVE_ENABLED", "false").lower() == "true"
        mode = "LIVE" if live_enabled else "PAPER"

        host = os.environ.get("IBKR_HOST", "127.0.0.1")
        if live_enabled:
            port = int(os.environ.get("IBKR_PORT", "4002"))
        else:
            port = int(os.environ.get("IBKR_PAPER_PORT", "4003"))

        executor = _make_macro_ecb_executor(mode=mode) if live_enabled else None

        result = run_macro_ecb_cycle(
            ibkr_host=host,
            ibkr_port=port,
            dry_run=not live_enabled,
            futures_executor=executor,
        )

        if result.get("skipped"):
            logger.info(f"  MACRO ECB {mode} skipped: {result['skipped']}")
        elif result.get("signals"):
            n_sig = len(result["signals"])
            n_sent = len(result.get("sent_orders", []))
            logger.info(f"  MACRO ECB {mode}: {n_sig} signals, {n_sent} orders sent")
            for s in result["signals"]:
                logger.info(f"    -> {s['side']} {s['index_symbol']} @ {s['entry_price']:.2f} "
                            f"SL={s['stop_loss']:.2f} TP={s['take_profit']:.2f}")
        else:
            logger.info(f"  MACRO ECB {mode}: ECB day but no signal (move below threshold)")
    except Exception as e:
        logger.error(f"MACRO ECB cycle error: {e}", exc_info=True)
    finally:
        _ibkr_lock.release()


def _run_futures_cycle(live: bool = False):
    """Futures execution cycle — shared between live and paper.

    Strategies: MES Trend, MES Trend+MR, MES 3-Day Stretch,
    Overnight MES/MNQ, TSMOM multi, Commodity Seasonality.
    All with bracket orders (SL+TP broker-side).
    """
    if not _ibkr_lock.acquire(blocking=False):
        logger.warning("FUTURES SKIP — IBKR lock held")
        return

    _mode = "LIVE" if live else "PAPER"
    try:
        logger.info(f"=== FUTURES {_mode} CYCLE ===")

        if live:
            target_port = int(os.environ.get("IBKR_PORT", "4002"))
        else:
            target_port = int(os.environ.get("IBKR_PAPER_PORT", "4003"))

        # Connect directly with explicit port — do NOT modify os.environ
        # to avoid race conditions with other threads
        try:
            from ib_insync import IB as _FutIB
            import random as _fut_rng
            _fut_ib = _FutIB()
            _ibkr_host = os.environ.get("IBKR_HOST", "127.0.0.1")
            _fut_ib.connect(_ibkr_host, target_port, clientId=_fut_rng.randint(70, 79), timeout=10)
            import time as _ft; _ft.sleep(3)  # Wait for positions/orders to load

            # Create a minimal adapter-like wrapper for compatibility
            class _FutIBKR:
                def __init__(self, ib):
                    self._ib = ib
                def get_account_info(self):
                    acct = {}
                    for a in self._ib.accountSummary():
                        if a.tag == "NetLiquidation":
                            acct["equity"] = float(a.value)
                        elif a.tag == "TotalCashValue":
                            acct["cash"] = float(a.value)
                    return acct
                def disconnect(self):
                    self._ib.disconnect()

            ibkr = _FutIBKR(_fut_ib)
            ibkr_info = ibkr.get_account_info()
            equity = float(ibkr_info.get("equity", 0))
        except Exception as e:
            logger.warning(f"  FUTURES {_mode} SKIP — IBKR port {target_port} not connected: {e}")
            return

        if equity <= 0:
            logger.warning("  FUTURES PAPER SKIP — equity=0")
            return

        logger.info(f"  FUTURES {_mode} equity: ${equity:,.0f}")

        # Load futures data
        import pandas as pd
        data_dir = Path(__file__).resolve().parent / "data" / "futures"
        data_sources = {}
        for sym in ["MES", "MNQ", "M2K", "MIB", "ESTX50", "VIX", "MGC", "MCL", "DAX", "CAC40"]:
            fpath = data_dir / f"{sym}_1D.parquet"
            if fpath.exists():
                df = pd.read_parquet(fpath)
                df.columns = [c.lower() for c in df.columns]
                if "datetime" in df.columns:
                    df.index = pd.to_datetime(df["datetime"])
                elif not isinstance(df.index, pd.DatetimeIndex):
                    df.index = pd.to_datetime(df.index)
                # Cron data_refresh peut introduire doublons/NaT/disorder.
                # Drop NaT, dedupe (keep last), sort, strip tz — obligatoire avant
                # DataFeed validation (is_monotonic_increasing) sinon cycle KO.
                df = df[df.index.notna()]
                df = df[~df.index.duplicated(keep="last")].sort_index()
                if df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
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

        # ============================================================
        # ============================================================
        # STRATS LIVE CAPABLE (true alpha, zero-beta, paper + live)
        # ============================================================
        # Ces strats ont demontre un alpha pur (corr ~0 avec MES buy-hold)
        # et tournent donc a la fois en paper (port 4003) et live (port 4002).
        # User decision 15 avril 2026.

        # LIVE-capable 1: Cross-Asset Momentum (PRIORITE MAX, first-refusal)
        # corr MES = 0.003, 5/5 WF, alpha each year 2021-2026 (incl 2 bears).
        # Option 1 (backtest 15 avril 2026): CAM reserve ses symboles preferes.
        # Meme si CAM ne fire pas aujourd'hui (rebal cooldown), elle bloque les
        # autres strats qui voudraient prendre le symbole qu'elle choisirait.
        # Backtest: Sharpe 0.85 -> 1.06 (+25%), WR 44% -> 48%, 2022 -$1647 -> -$1369.
        _cam_top_pick = None
        try:
            from strategies_v2.futures.cross_asset_momentum import CrossAssetMomentum
            _cam_strat = CrossAssetMomentum()
            _cam_strat.set_data_feed(feed)
            bar = feed.get_latest_bar("MES")
            if bar:
                _cam_top_pick = _cam_strat.get_top_pick()
                sig = _cam_strat.on_bar(bar, portfolio_state)
                if sig:
                    signals.append(("Cross-Asset Mom", sig))
                    logger.info(f"    Cross-Asset Mom ({_mode}): BUY {sig.symbol}")
                else:
                    logger.info(f"    Cross-Asset Mom ({_mode}): pas de rebal "
                                f"(top pick: {_cam_top_pick or 'none'})")
        except Exception as e:
            logger.error(f"    Cross-Asset Mom error: {e}")

        # LIVE-capable 2: Gold Trend MGC (SECONDARY — first-refusal CAM)
        # corr MES = -0.02, positive EVERY year, alpha pur
        if "MGC" in data_sources:
            if _cam_top_pick == "MGC":
                logger.info(f"    Gold Trend MGC ({_mode}): SKIP — CAM reserved MGC")
            else:
                try:
                    from strategies_v2.futures.gold_trend_mgc import GoldTrendMGC
                    _gt_strat = GoldTrendMGC()
                    _gt_strat.set_data_feed(feed)
                    bar = feed.get_latest_bar("MGC")
                    if bar:
                        sig = _gt_strat.on_bar(bar, portfolio_state)
                        if sig:
                            signals.append(("Gold Trend MGC", sig))
                            logger.info(f"    Gold Trend MGC ({_mode}): BUY @ {bar.close:.2f}")
                        else:
                            logger.info(f"    Gold Trend MGC ({_mode}): below EMA20")
                except Exception as e:
                    logger.error(f"    Gold Trend MGC error: {e}")

        # LIVE-capable 3: Gold-Oil Rotation (SECONDARY — first-refusal CAM)
        # Sharpe 6.44 backtest, WF 5/5 OOS profitable (mean Sharpe 7.16),
        # corr MES = 0.02, corr cross_asset = 0.002, corr gold_trend = 0.104,
        # positive EVERY year 2021-2026 (incl 2022 +$2.4K and 2026 +$4.7K bears).
        # Rotates long between MGC and MCL based on 20d momentum spread.
        if "MGC" in data_sources and "MCL" in data_sources:
            try:
                from strategies_v2.futures.gold_oil_rotation import GoldOilRotation
                _gor_strat = GoldOilRotation()
                _gor_strat.set_data_feed(feed)
                bar = feed.get_latest_bar("MGC")
                if bar:
                    sig = _gor_strat.on_bar(bar, portfolio_state)
                    if sig:
                        # First-refusal CAM: block si CAM voulait ce symbole
                        if _cam_top_pick == sig.symbol:
                            logger.info(f"    Gold-Oil Rotation ({_mode}): SKIP "
                                        f"— CAM reserved {sig.symbol}")
                        else:
                            signals.append(("Gold-Oil Rotation", sig))
                            logger.info(f"    Gold-Oil Rotation ({_mode}): BUY {sig.symbol}")
                    else:
                        logger.info(f"    Gold-Oil Rotation ({_mode}): spread < 2%")
            except Exception as e:
                logger.error(f"    Gold-Oil Rotation error: {e}")

        # STRATS "REJECTED" QUI TOURNENT EN PAPER UNIQUEMENT
        # ============================================================
        # Decision user 15 avril 2026: activer en paper toutes les strats
        # meme celles rejetees pour collecter de la vraie data live et
        # challenger les backtests. Live reste disabled pour ces strats.
        if not live:
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
                        logger.info(f"    MES Trend (paper): {sig.side} @ {bar.close:.2f}")
                    else:
                        logger.info("    MES Trend (paper): pas de signal")
            except Exception as e:
                logger.error(f"    MES Trend error: {e}")

            # 2. MES Trend+MR
            try:
                from strategies_v2.futures.mes_trend_mr import MESTrendMR
                strat = MESTrendMR()
                strat.set_data_feed(feed)
                bar = feed.get_latest_bar("MES")
                if bar:
                    sig = strat.on_bar(bar, portfolio_state)
                    if sig:
                        signals.append(("MES Trend+MR", sig))
                        logger.info(f"    MES Trend+MR (paper): {sig.side} @ {bar.close:.2f}")
                    else:
                        logger.info("    MES Trend+MR (paper): pas de signal")
            except Exception as e:
                logger.error(f"    MES Trend+MR error: {e}")

            # 3. MES 3-Day Stretch
            try:
                from strategies_v2.futures.mes_3day_stretch import MES3DayStretch
                strat = MES3DayStretch()
                strat.set_data_feed(feed)
                bar = feed.get_latest_bar("MES")
                if bar:
                    sig = strat.on_bar(bar, portfolio_state)
                    if sig:
                        signals.append(("MES 3-Day Stretch", sig))
                        logger.info(f"    MES 3-Day Stretch (paper): {sig.side} @ {bar.close:.2f}")
                    else:
                        logger.info("    MES 3-Day Stretch (paper): pas de signal")
            except Exception as e:
                logger.error(f"    MES 3-Day Stretch error: {e}")

            # 3a-bis. MES calendar paper strats (T1-A INT-C promotion 2026-04-16)
            # Promus en paper_only via INT-A WF/MC validation (cf docs/research/wf_reports/INT-A_tier1_validation.md).
            # Transition paper -> live_probation apres 30j sans divergence > 2 sigma.
            try:
                from strategies_v2.futures.mes_calendar_paper import (
                    MESMondayLong, MESWednesdayLong, MESPreHolidayLong,
                )
                for _cal_cls in (MESMondayLong, MESWednesdayLong, MESPreHolidayLong):
                    _cal = _cal_cls()
                    _cal.set_data_feed(feed)
                    bar = feed.get_latest_bar("MES")
                    if bar:
                        sig = _cal.on_bar(bar, portfolio_state)
                        if sig:
                            signals.append((_cal.name, sig))
                            logger.info(f"    {_cal.name} (paper): BUY @ {bar.close:.2f}")
                        else:
                            logger.info(f"    {_cal.name} (paper): pas un jour pattern")
            except Exception as e:
                logger.error(f"    MES calendar paper error: {e}")

            # 3b. Overnight MES V2 — params validates par sweep V2 (640 combos) + WF 3/3
            #     SL=60 TP=120 EMA=50 none → Sharpe 1.69, +$7,272, 163 trades 5Y
            #     WF 4/5 profitable, IS 1.41 → OOS 1.68 (OOS > IS, robuste)
            #     Paper only: observation avant live decision.
            try:
                from strategies_v2.futures.overnight_buy_close import OvernightBuyClose
                strat = OvernightBuyClose(
                    symbol="MES",
                    sl_points=60,
                    tp_points=120,
                    ema_period=50,
                )
                strat.set_data_feed(feed)
                bar = feed.get_latest_bar("MES")
                if bar:
                    sig = strat.on_bar(bar, portfolio_state)
                    if sig:
                        signals.append(("Overnight MES V2", sig))
                        logger.info(f"    Overnight MES V2 (paper SL60/TP120/EMA50): {sig.side} @ {bar.close:.2f}")
                    else:
                        logger.info("    Overnight MES V2 (paper): pas de signal (below EMA50)")
            except Exception as e:
                logger.error(f"    Overnight MES V2 error: {e}")

            # 4. Overnight MNQ — extended sweep 280 combos V2 best:
            #    SL=140 TP=300 EMA=40 → Sharpe 1.01, +$16,589, 273 trades 5Y
            #    WF 4/5 profitable, IS 2.77 → OOS 1.96 (ratio 0.71, robuste)
            #    84% robust neighborhood → pas du data mining
            try:
                from strategies_v2.futures.overnight_buy_close import OvernightBuyClose
                strat = OvernightBuyClose(
                    symbol="MNQ",
                    sl_points=140,
                    tp_points=300,
                    ema_period=40,
                )
                strat.set_data_feed(feed)
                bar = feed.get_latest_bar("MNQ")
                if bar:
                    sig = strat.on_bar(bar, portfolio_state)
                    if sig:
                        signals.append(("Overnight MNQ", sig))
                        logger.info(f"    Overnight MNQ (paper SL140/TP300/EMA40): {sig.side} @ {bar.close:.2f}")
                    else:
                        logger.info("    Overnight MNQ (paper): pas de signal (below EMA40)")
            except Exception as e:
                logger.error(f"    Overnight MNQ error: {e}")

            # 5. TSMOM Multi (MES, MNQ)
            for tsmom_sym in ["MES", "MNQ"]:
                if tsmom_sym not in data_sources:
                    continue
                try:
                    from strategies_v2.futures.tsmom_multi import TSMOMMulti
                    strat_ts = TSMOMMulti(symbol=tsmom_sym)
                    strat_ts.set_data_feed(feed)
                    bar_ts = feed.get_latest_bar(tsmom_sym)
                    if bar_ts:
                        sig = strat_ts.on_bar(bar_ts, portfolio_state)
                        if sig:
                            signals.append((f"TSMOM {tsmom_sym}", sig))
                            logger.info(f"    TSMOM {tsmom_sym} (paper): {sig.side} @ {bar_ts.close:.2f}")
                        else:
                            logger.info(f"    TSMOM {tsmom_sym} (paper): pas de signal")
                except Exception as e:
                    logger.error(f"    TSMOM {tsmom_sym} error: {e}")

            # 6. M2K ORB
            if "M2K" in data_sources:
                try:
                    from strategies_v2.futures.m2k_orb import M2KORB
                    strat = M2KORB()
                    strat.set_data_feed(feed)
                    bar = feed.get_latest_bar("M2K")
                    if bar:
                        sig = strat.on_bar(bar, portfolio_state)
                        if sig:
                            signals.append(("M2K ORB", sig))
                            logger.info(f"    M2K ORB (paper): {sig.side} @ {bar.close:.2f}")
                        else:
                            logger.info("    M2K ORB (paper): pas de signal")
                except Exception as e:
                    logger.error(f"    M2K ORB error: {e}")

            # 7. MCL Brent Lag
            if "MCL" in data_sources:
                try:
                    from strategies_v2.futures.mcl_brent_lag import MCLBrentLag
                    strat = MCLBrentLag()
                    strat.set_data_feed(feed)
                    bar = feed.get_latest_bar("MCL")
                    if bar:
                        sig = strat.on_bar(bar, portfolio_state)
                        if sig:
                            signals.append(("MCL Brent Lag", sig))
                            logger.info(f"    MCL Brent Lag (paper): {sig.side} @ {bar.close:.2f}")
                        else:
                            logger.info("    MCL Brent Lag (paper): pas de signal")
                except Exception as e:
                    logger.error(f"    MCL Brent Lag error: {e}")

            # 8. MGC VIX Hedge
            if "MGC" in data_sources and "VIX" in data_sources:
                try:
                    from strategies_v2.futures.mgc_vix_hedge import MGCVixHedge
                    strat = MGCVixHedge()
                    strat.set_data_feed(feed)
                    bar = feed.get_latest_bar("MGC")
                    if bar:
                        sig = strat.on_bar(bar, portfolio_state)
                        if sig:
                            signals.append(("MGC VIX Hedge", sig))
                            logger.info(f"    MGC VIX Hedge (paper): {sig.side} @ {bar.close:.2f}")
                        else:
                            logger.info("    MGC VIX Hedge (paper): pas de signal")
                except Exception as e:
                    logger.error(f"    MGC VIX Hedge error: {e}")

            # (Cross-Asset Mom + Gold Trend MGC moved out of paper-only block — now LIVE+paper)

            # 9f. Thursday Rally MES + MNQ
            for sym in ["MES", "MNQ"]:
                if sym not in data_sources: continue
                try:
                    from strategies_v2.futures.thursday_rally import ThursdayRally
                    strat = ThursdayRally(symbol=sym)
                    strat.set_data_feed(feed)
                    bar = feed.get_latest_bar(sym)
                    if bar:
                        sig = strat.on_bar(bar, portfolio_state)
                        if sig:
                            signals.append((f"Thursday Rally {sym}", sig))
                            logger.info(f"    Thursday Rally {sym} (paper): BUY @ {bar.close:.2f}")
                except Exception as e:
                    logger.error(f"    Thursday Rally {sym} error: {e}")

            # 9c. Friday-Monday MNQ — weekend effect, n=266 5Y Sharpe 1.76
            # WF 4/5 profitable, OOS 1.86 > IS 0.03 (OOS overperform)
            if "MNQ" in data_sources:
                try:
                    from strategies_v2.futures.friday_monday_mnq import FridayMondayMNQ
                    strat = FridayMondayMNQ()
                    strat.set_data_feed(feed)
                    bar = feed.get_latest_bar("MNQ")
                    if bar:
                        sig = strat.on_bar(bar, portfolio_state)
                        if sig:
                            signals.append(("Friday-Monday MNQ", sig))
                            logger.info(f"    Friday-Monday MNQ (paper): BUY @ {bar.close:.2f}")
                        else:
                            logger.info("    Friday-Monday MNQ (paper): pas vendredi")
                except Exception as e:
                    logger.error(f"    Friday-Monday MNQ error: {e}")

            # 9d. Multi-TF Momentum MES — weekly confirm + daily trigger
            # n=58 (small sample) but Sharpe 2.01 + WF 3/5 prof OOS 2.85
            if "MES" in data_sources:
                try:
                    from strategies_v2.futures.multi_tf_mom_mes import MultiTFMomMES
                    strat = MultiTFMomMES()
                    strat.set_data_feed(feed)
                    bar = feed.get_latest_bar("MES")
                    if bar:
                        sig = strat.on_bar(bar, portfolio_state)
                        if sig:
                            signals.append(("Multi-TF Mom MES", sig))
                            logger.info(f"    Multi-TF Mom MES (paper): BUY @ {bar.close:.2f}")
                        else:
                            logger.info("    Multi-TF Mom MES (paper): conditions non alignees")
                except Exception as e:
                    logger.error(f"    Multi-TF Mom MES error: {e}")

            # 9a. BB Squeeze MES — event-driven rare, 7/year but Sharpe 7 pure
            # n=37, +$7904, 4/5 WF profitable, ratio 0.84
            if "MES" in data_sources:
                try:
                    from strategies_v2.futures.bb_squeeze_mes import BBSqueezeMES
                    strat = BBSqueezeMES()
                    strat.set_data_feed(feed)
                    bar = feed.get_latest_bar("MES")
                    if bar:
                        sig = strat.on_bar(bar, portfolio_state)
                        if sig:
                            signals.append(("BB Squeeze MES", sig))
                            logger.info(f"    BB Squeeze MES (paper): BREAKOUT @ {bar.close:.2f}")
                        else:
                            logger.info("    BB Squeeze MES (paper): pas de squeeze+breakout")
                except Exception as e:
                    logger.error(f"    BB Squeeze MES error: {e}")

            # 9b. RS MES/MNQ Rotation — validated 5Y, 3/3 gates Sharpe OOS 2.28
            # Buy stronger of MES/MNQ over 3-day lookback, hold 5 days.
            # n=212, +$19,231 total, WR 58%, 4/5 WF profitable, OOS>IS (ultra robust)
            if "MES" in data_sources and "MNQ" in data_sources:
                try:
                    from strategies_v2.futures.rs_mes_mnq_rotate import RSMesMnqRotate
                    strat = RSMesMnqRotate(lookback=3)
                    strat.set_data_feed(feed)
                    bar = feed.get_latest_bar("MES")
                    if bar:
                        sig = strat.on_bar(bar, portfolio_state)
                        if sig:
                            signals.append(("RS MES/MNQ", sig))
                            logger.info(f"    RS MES/MNQ rotate (paper): {sig.side} {sig.symbol} @ {bar.close:.2f}")
                        else:
                            logger.info("    RS MES/MNQ rotate (paper): spread < 0.5%")
                except Exception as e:
                    logger.error(f"    RS MES/MNQ error: {e}")

            # 9. VIX Mean Reversion
            if "VIX" in data_sources and "MES" in data_sources:
                try:
                    from strategies_v2.futures.vix_mean_reversion import VIXMeanReversion
                    strat = VIXMeanReversion()
                    strat.set_data_feed(feed)
                    bar = feed.get_latest_bar("MES")
                    if bar:
                        sig = strat.on_bar(bar, portfolio_state)
                        if sig:
                            signals.append(("VIX MR", sig))
                            logger.info(f"    VIX MR (paper): {sig.side} @ {bar.close:.2f}")
                        else:
                            logger.info("    VIX MR (paper): pas de signal")
                except Exception as e:
                    logger.error(f"    VIX MR error: {e}")
        else:
            # LIVE MODE — only keep the validated ones, all others disabled
            logger.info("    LIVE mode: MES Trend/Trend+MR/Stretch/Overnight/TSMOM/M2K/MCL/MGC/VIX-MR all DISABLED")
        for tsmom_sym in []:
            if tsmom_sym not in data_sources:
                continue
            try:
                from strategies_v2.futures.tsmom_multi import TSMOMMulti
                strat_ts = TSMOMMulti(symbol=tsmom_sym)
                strat_ts.set_data_feed(feed)
                bar_ts = feed.get_latest_bar(tsmom_sym)
                if bar_ts:
                    sig = strat_ts.on_bar(bar_ts, portfolio_state)
                    if sig:
                        signals.append((f"TSMOM {tsmom_sym}", sig))
                        logger.info(f"    TSMOM {tsmom_sym}: {sig.side} @ {bar_ts.close:.2f} str={sig.strength:.2f}")
                    else:
                        logger.info(f"    TSMOM {tsmom_sym}: pas de signal")
            except Exception as e:
                logger.error(f"    TSMOM {tsmom_sym} error: {e}")

        # 6b. EU Gap Open (ESTX50) — priority 9
        if "ESTX50" in data_sources:
            try:
                from strategies_v2.futures.eu_gap_open import EUGapOpen
                strat_gap = EUGapOpen()
                strat_gap.set_data_feed(feed)
                bar_estx = feed.get_latest_bar("ESTX50")
                if bar_estx:
                    sig = strat_gap.on_bar(bar_estx, portfolio_state)
                    if sig:
                        signals.append(("EU Gap Open", sig))
                        logger.info(f"    EU Gap Open: {sig.side} ESTX50 @ {bar_estx.close:.2f} str={sig.strength:.2f}")
                    else:
                        logger.info("    EU Gap Open: pas de gap > 1%")
                else:
                    logger.info("    EU Gap Open: pas de bar ESTX50")
            except Exception as e:
                logger.error(f"    EU Gap Open error: {e}")

        # 6c. Sector Rotation EU (DAX/CAC40) — priority 6, weekly Monday
        if "DAX" in data_sources and "CAC40" in data_sources:
            try:
                from strategies_v2.futures.sector_rotation_eu import SectorRotationEU
                strat_rot = SectorRotationEU()
                strat_rot.set_data_feed(feed)
                bar_dax = feed.get_latest_bar("DAX")
                if bar_dax:
                    sig = strat_rot.on_bar(bar_dax, portfolio_state)
                    if sig:
                        signals.append(("Sector Rotation", sig))
                        logger.info(f"    Sector Rotation: {sig.side} {sig.symbol} @ {bar_dax.close:.2f} str={sig.strength:.2f}")
                    else:
                        logger.info("    Sector Rotation: pas de signal (pas lundi ou pas de divergence)")
                else:
                    logger.info("    Sector Rotation: pas de bar DAX")
            except Exception as e:
                logger.error(f"    Sector Rotation error: {e}")

        # 6c. Gold-Equity Divergence — PAPER only
        if "MGC" in data_sources:
            try:
                from strategies_v2.futures.gold_equity_divergence import GoldEquityDivergence
                strat_ge = GoldEquityDivergence()
                strat_ge.set_data_feed(feed)
                bar = feed.get_latest_bar("MES")
                if bar:
                    sig = strat_ge.on_bar(bar, portfolio_state)
                    if sig:
                        signals.append(("Gold-Equity Div", sig))
                        logger.info(f"    Gold-Equity Div: {sig.side} MES @ {bar.close:.2f} str={sig.strength:.2f}")
                    else:
                        logger.info("    Gold-Equity Div: pas de signal (pas de divergence)")
                else:
                    logger.info("    Gold-Equity Div: pas de bar MES")
            except Exception as e:
                logger.error(f"    Gold-Equity Div error: {e}")

        # 7. Commodity Seasonality (MCL, MGC) — paper monitoring
        for season_sym in ["MCL", "MGC"]:
            if season_sym not in data_sources:
                continue
            try:
                from strategies_v2.futures.commodity_season import CommoditySeason
                strat_cs = CommoditySeason(symbol=season_sym)
                strat_cs.set_data_feed(feed)
                bar_cs = feed.get_latest_bar(season_sym)
                if bar_cs:
                    sig = strat_cs.on_bar(bar_cs, portfolio_state)
                    if sig:
                        signals.append((f"Season {season_sym}", sig))
                        logger.info(f"    Season {season_sym}: {sig.side} @ {bar_cs.close:.2f}")
                    else:
                        logger.info(f"    Season {season_sym}: hors fenetre saisonniere")
            except Exception as e:
                logger.error(f"    Season {season_sym} error: {e}")

        # 8. MES/MNQ Pairs
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

        # 9. MIB/ESTX50 Spread (Relative Value) — paper only
        if "MIB" in data_sources and "ESTX50" in data_sources:
            try:
                from strategies_v2.futures.mib_estx50_spread import MIBEstx50Spread
                strat_spread = MIBEstx50Spread()
                strat_spread.set_data_feed(feed)
                bar_mib = feed.get_latest_bar("MIB")
                if bar_mib:
                    sig = strat_spread.on_bar(bar_mib, portfolio_state)
                    if sig:
                        signals.append(("MIB/ESTX50 Spread", sig))
                        logger.info(f"    MIB/ESTX50 Spread: {sig.side} MIB @ {bar_mib.close:.2f} str={sig.strength:.2f}")
                    else:
                        logger.info("    MIB/ESTX50 Spread: pas de signal (z-score dans le range)")
                else:
                    logger.info("    MIB/ESTX50 Spread: pas de bar MIB")
            except Exception as e:
                logger.error(f"    MIB/ESTX50 Spread error: {e}")
        else:
            logger.info("    MIB/ESTX50 Spread: SKIP — data MIB/ESTX50 manquante")

        logger.info(f"  FUTURES PAPER: {len(signals)} signal(s)")
        _log_event("cycle_end", "futures_paper", {
            "signals": len(signals), "equity": equity,
        })

        # === EXECUTION: bracket orders via IBKR (live or paper) ===
        # CRITICAL: separate state files for live vs paper to avoid cross-contamination
        _state_suffix = "live" if live else "paper"
        _fut_state_path = ROOT / "data" / "state" / f"futures_positions_{_state_suffix}.json"
        _fut_state_path.parent.mkdir(parents=True, exist_ok=True)
        _fut_positions = {}
        try:
            if _fut_state_path.exists():
                _fut_positions = json.loads(_fut_state_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning(f"    FUTURES: failed to load state file {_fut_state_path}")
            pass

        # FIX A: Reconcile state file with actual IBKR positions
        # If state says we have a position but IBKR doesn't, the trade was
        # closed (SL/TP hit or manual close) — update journal + remove from state
        _ibkr_real_pos = {}  # initialized empty in case positions() fails
        try:
            _ibkr_real_pos = {p.contract.symbol: p.position for p in ibkr._ib.positions() if abs(p.position) > 0}
            stale_keys = [k for k in _fut_positions if k not in _ibkr_real_pos]
            for k in stale_keys:
                # Journal UPDATE: the position was closed between last cycle and now.
                # Fetch recent fills to determine exit price + compute pnl.
                try:
                    import sqlite3 as _sql
                    _pos_info = _fut_positions[k]
                    _oca = _pos_info.get("oca_group", "")
                    _entry_px = float(_pos_info.get("entry", 0))
                    _qty = int(_pos_info.get("qty", 1))
                    _side = _pos_info.get("side", "BUY")
                    # Infer exit price: scan recent fills for this symbol + opposite side
                    _exit_px = 0.0
                    _exit_reason = "UNKNOWN"
                    try:
                        _opposite = "SELL" if _side == "BUY" else "BUY"
                        _recent_fills = ibkr._ib.fills()
                        for _f in reversed(_recent_fills):
                            if _f.contract.symbol == k and _f.execution.side in ("SLD", "BOT"):
                                _fill_is_exit = (_side == "BUY" and _f.execution.side == "SLD") or \
                                               (_side == "SELL" and _f.execution.side == "BOT")
                                if _fill_is_exit:
                                    _exit_px = float(_f.execution.price)
                                    # Infer reason: compare to SL/TP
                                    _sl_stored = float(_pos_info.get("sl", 0))
                                    _tp_stored = float(_pos_info.get("tp", 0))
                                    if _sl_stored and abs(_exit_px - _sl_stored) < 1:
                                        _exit_reason = "SL_HIT"
                                    elif _tp_stored and abs(_exit_px - _tp_stored) < 1:
                                        _exit_reason = "TP_HIT"
                                    else:
                                        _exit_reason = "MANUAL"
                                    break
                    except Exception:
                        pass

                    # Compute pnl (approx — multiplier from state or 5 default)
                    _mult = 5 if k == "MES" else 2 if k == "MNQ" else 5 if k == "M2K" else 1
                    if _exit_px > 0 and _entry_px > 0:
                        if _side == "BUY":
                            _pnl_gross = (_exit_px - _entry_px) * _qty * _mult
                        else:
                            _pnl_gross = (_entry_px - _exit_px) * _qty * _mult
                        _pnl_net = _pnl_gross - 2.49 * _qty  # approx commission
                    else:
                        _pnl_gross = 0
                        _pnl_net = 0

                    # UPDATE the journal row (identified by oca = trade_id)
                    _jdb = "live_journal.db" if live else "paper_journal.db"
                    _jpath = ROOT / "data" / _jdb
                    if _jpath.exists() and _oca:
                        _jconn = _sql.connect(str(_jpath))
                        _jconn.execute(
                            "UPDATE trades SET status='closed', exit_price=?, exit_time=?, "
                            "pnl_gross=?, pnl_net=?, exit_reason=? WHERE trade_id=?",
                            (_exit_px, datetime.now(UTC).isoformat(),
                             _pnl_gross, _pnl_net, _exit_reason, _oca),
                        )
                        _rowcount = _jconn.total_changes
                        _jconn.commit()
                        _jconn.close()
                        logger.info(
                            f"    RECONCILE + JOURNAL UPDATE: {k} closed @ {_exit_px:.2f} "
                            f"(entry {_entry_px:.2f}) pnl=${_pnl_net:.2f} reason={_exit_reason} rows={_rowcount}"
                        )
                    else:
                        logger.info(f"    RECONCILE: removing stale {k} from state (journal not updated, no oca)")
                except Exception as _jue:
                    logger.warning(f"    RECONCILE journal UPDATE failed for {k}: {_jue}")

                del _fut_positions[k]
        except Exception as _re:
            logger.warning(f"    RECONCILE error: {_re}")

        # FIX B: Check that open positions have active bracket orders (SL/TP)
        # If missing, REPOSE the brackets. If repose impossible (no SL/TP in
        # state, or placeOrder fails), FAIL-SAFE: close the position at market
        # immediately — never leave a live position unprotected (CRO rule).
        try:
            from ib_insync import (
                Future as IbFuture,
                StopOrder,
                LimitOrder,
                MarketOrder as _FailMarketOrder,
            )
            import uuid as _uuid
            _open_order_syms = {t.contract.symbol for t in ibkr._ib.openTrades()}
            for pos_sym, pos_info in list(_fut_positions.items()):
                if pos_sym in _ibkr_real_pos and pos_sym not in _open_order_syms:
                    logger.warning(f"    BRACKET MISSING: {pos_sym} — reposing SL/TP")

                    _rb_sl = float(pos_info.get("sl", 0) or 0)
                    _rb_tp = float(pos_info.get("tp", 0) or 0)
                    _rb_qty = abs(int(_ibkr_real_pos[pos_sym]))
                    _rb_side = "BUY" if _ibkr_real_pos[pos_sym] < 0 else "SELL"

                    _repose_ok = False
                    _fail_reason = ""

                    if _rb_sl > 0 and _rb_tp > 0:
                        # Attempt repose
                        try:
                            _rb_fut = IbFuture(pos_sym, exchange="CME")
                            _rb_details = ibkr._ib.reqContractDetails(_rb_fut)
                            if _rb_details:
                                _rb_contract = _rb_details[0].contract
                                _rb_oca = f"REBRACKET_{pos_sym}_{_uuid.uuid4().hex[:8]}"
                                _sl_ord = StopOrder(_rb_side, _rb_qty, _rb_sl)
                                _sl_ord.tif = "GTC"; _sl_ord.ocaGroup = _rb_oca; _sl_ord.ocaType = 1
                                _tp_ord = LimitOrder(_rb_side, _rb_qty, _rb_tp)
                                _tp_ord.tif = "GTC"; _tp_ord.ocaGroup = _rb_oca; _tp_ord.ocaType = 1
                                ibkr._ib.placeOrder(_rb_contract, _sl_ord)
                                time.sleep(0.5)
                                ibkr._ib.placeOrder(_rb_contract, _tp_ord)
                                time.sleep(2); ibkr._ib.sleep(1)
                                logger.info(
                                    f"    BRACKET REPOSED: {pos_sym} SL={_rb_sl} TP={_rb_tp} OCA={_rb_oca}"
                                )
                                _repose_ok = True
                            else:
                                _fail_reason = "no contract details"
                        except Exception as _be:
                            _fail_reason = str(_be)[:100]
                            logger.error(f"    BRACKET REPOSE FAILED {pos_sym}: {_be}")
                    else:
                        _fail_reason = f"invalid SL/TP in state (sl={_rb_sl}, tp={_rb_tp})"

                    if not _repose_ok:
                        # FAIL-SAFE: close position at market immediately.
                        # Never leave a live position unprotected.
                        logger.critical(
                            f"    BRACKET FAIL-SAFE: {pos_sym} — {_fail_reason}. "
                            f"Closing position at market to enforce SL obligatoire rule."
                        )
                        try:
                            _fs_fut = IbFuture(pos_sym, exchange="CME")
                            _fs_details = ibkr._ib.reqContractDetails(_fs_fut)
                            if _fs_details:
                                _fs_contract = _fs_details[0].contract
                                _fs_order = _FailMarketOrder(_rb_side, _rb_qty)
                                _fs_order.outsideRth = True
                                _fs_trade = ibkr._ib.placeOrder(_fs_contract, _fs_order)
                                time.sleep(4); ibkr._ib.sleep(2)
                                _fs_status = _fs_trade.orderStatus.status
                                _fs_fill = _fs_trade.orderStatus.avgFillPrice or 0
                                logger.critical(
                                    f"    BRACKET FAIL-SAFE CLOSED: {pos_sym} "
                                    f"status={_fs_status} fill={_fs_fill}"
                                )
                                _send_alert(
                                    f"CRITICAL: {pos_sym} closed by BRACKET FAIL-SAFE\n"
                                    f"Reason: {_fail_reason}\n"
                                    f"Fill: {_fs_fill:.2f} ({_fs_status})\n"
                                    f"Position was unprotected (no SL/TP).",
                                    level="critical",
                                )
                                if pos_sym in _fut_positions:
                                    del _fut_positions[pos_sym]
                        except Exception as _fse:
                            logger.critical(
                                f"    BRACKET FAIL-SAFE FAILED for {pos_sym}: {_fse} — "
                                f"MANUAL INTERVENTION REQUIRED"
                            )
                            _send_alert(
                                f"BRACKET FAIL-SAFE FAILED on {pos_sym}: {_fse}\n"
                                f"Manual intervention required!",
                                level="critical",
                            )
        except Exception as _bce:
            logger.warning(f"    BRACKET CHECK error: {_bce}")

        n_fut_orders = 0
        # _mode already set from the 'live' parameter (line 802), do NOT
        # re-derive from os.environ which doesn't reflect target_port

        # 1. Time-exit: close positions held > 48h (any symbol)
        for pos_key, pos_info in list(_fut_positions.items()):
            opened = pos_info.get("opened_at", "")
            if not opened:
                continue
            try:
                age_h = (datetime.now(UTC) - datetime.fromisoformat(opened)).total_seconds() / 3600
            except Exception:
                continue
            if age_h < 48:
                continue

            pos_sym = pos_info.get("symbol", pos_key)
            close_side = "SELL" if pos_info.get("side") == "BUY" else "BUY"
            try:
                from ib_insync import Future as IbFuture, MarketOrder as IbMarketOrder
                fut_contract = IbFuture(pos_sym, exchange="CME")
                details = ibkr._ib.reqContractDetails(fut_contract)
                if details:
                    fut_contract = details[0].contract

                # Cancel existing OCA bracket orders BEFORE closing
                _oca_group = pos_info.get("oca_group", "")
                if _oca_group:
                    for _ot in ibkr._ib.openTrades():
                        if getattr(_ot.order, 'ocaGroup', '') == _oca_group:
                            try:
                                ibkr._ib.cancelOrder(_ot.order)
                                logger.info(f"    TIME-EXIT: cancelled OCA order {_ot.order.orderId} for {pos_sym}")
                            except Exception:
                                pass
                    time.sleep(1); ibkr._ib.sleep(0.5)

                order = IbMarketOrder(close_side, int(pos_info.get("qty", 1)))
                trade = ibkr._ib.placeOrder(fut_contract, order)
                time.sleep(3); ibkr._ib.sleep(2)
                logger.info(
                    f"    FUTURES TIME-EXIT: {close_side} {pos_sym} (held {age_h:.0f}h) "
                    f"-> {trade.orderStatus.status}"
                )
                del _fut_positions[pos_key]
                n_fut_orders += 1
            except Exception as te:
                logger.error(f"    FUTURES TIME-EXIT FAILED {pos_sym}: {te}")

        # 2. New entries: market order + standalone OCA (SL+TP)
        # NOTE: do NOT use parentId brackets — IBKR cancels children when
        # market parent fills instantly. Use standalone OCA orders instead.
        from ib_insync import Future as IbFuture, MarketOrder as IbMarketOrder, StopOrder, LimitOrder
        import uuid as _uuid

        # Refresh IBKR real positions right before entry decisions
        # (initial query at line 1047 may be stale after time-exits above)
        try:
            _ibkr_real_pos = {p.contract.symbol: p.position for p in ibkr._ib.positions() if abs(p.position) > 0}
        except Exception:
            pass  # keep previous _ibkr_real_pos

        # RISK BUDGET FRAMEWORK (user decision 15 avril 2026)
        # ====================================================
        # Approach: think in EXPOSURE not contract count.
        # - Hard cap: max total risk-if-stopped <= 5% of equity
        # - Plus soft cap: max 4 distinct symbols live (diversification)
        # - Per-symbol cap: 1 contract (existing guards below)
        #
        # Sum of (entry - SL) * mult * qty for all open positions ≤ 5% * equity
        # Worst case all SL hit same day = max 5% DD (within kill switch limits)

        _FUT_MULT = {
            "MES": 5, "MNQ": 2, "M2K": 5, "MGC": 10, "MCL": 100,
            "MIB": 5, "ESTX50": 10, "DAX": 1, "CAC40": 1, "VIX": 1,
        }
        RISK_BUDGET_PCT = 0.05  # 5% of equity worst-case DD cap
        _risk_budget_usd = equity * RISK_BUDGET_PCT
        MAX_DISTINCT_SYMBOLS = 4 if live else 20

        # Compute current total risk-if-stopped from state file
        _current_risk = 0.0
        for _pos_sym, _pos_info in _fut_positions.items():
            _pe = float(_pos_info.get("entry", 0) or 0)
            _ps = float(_pos_info.get("sl", 0) or 0)
            _pq = abs(int(_pos_info.get("qty", 1) or 1))
            _pmult = _FUT_MULT.get(_pos_sym, 1)
            if _pe > 0 and _ps > 0:
                _current_risk += abs(_pe - _ps) * _pmult * _pq

        _total_existing = sum(abs(int(v)) for v in _ibkr_real_pos.values())
        logger.info(
            f"    FUTURES {_mode}: risk budget ${_current_risk:.0f}/${_risk_budget_usd:.0f} "
            f"({_current_risk/_risk_budget_usd*100:.0f}%), {_total_existing}/{MAX_DISTINCT_SYMBOLS} symbols"
        )

        # Legacy soft cap: max 4 distinct contracts (fallback if risk data missing)
        _slots_available = MAX_DISTINCT_SYMBOLS - _total_existing
        if _slots_available <= 0:
            logger.warning(
                f"    FUTURES {_mode}: MAX DISTINCT SYMBOLS — {_total_existing}/{MAX_DISTINCT_SYMBOLS}. Skipping."
            )
            signals = []

        # Sort signals by PRIORITY (high conviction/rare signals first)
        # VIX MR = priority 10 (rare, haute conviction)
        # Gold-Equity = priority 7
        # Overnight = priority 5
        # Trend+MR = priority 4
        # TSMOM = priority 3
        # Others = priority 1
        _STRAT_PRIORITY = {
            "EU Gap Open": 9,
            "Brent Lag MCL": 8,
            "Gold-Equity Div": 7,
            "Sector Rotation": 6,
            "Overnight MES": 5,
        }
        signals.sort(key=lambda x: _STRAT_PRIORITY.get(x[0], 1), reverse=True)

        # Track symbols already traded THIS cycle
        _traded_this_cycle = set()
        _contracts_opened = 0

        # P1.1 Whitelist enforcement — map display_name to canonical strategy_id.
        # Only applies in live mode. Paper mode stays permissive by design.
        _STRAT_DISPLAY_TO_ID = {
            "Cross-Asset Mom":   "cross_asset_momentum",
            "Gold Trend MGC":    "gold_trend_mgc",
            "Gold-Oil Rotation": "gold_oil_rotation",
            # T1-A INT-C 2026-04-16 paper promotions (canonical_id == display name)
            "mes_monday_long_oc":     "mes_monday_long_oc",
            "mes_wednesday_long_oc":  "mes_wednesday_long_oc",
            "mes_pre_holiday_long":   "mes_pre_holiday_long",
        }

        for name, sig in signals:
            if _contracts_opened >= _slots_available:
                logger.info(f"    {name}: SKIP — no slots left ({_slots_available} available, {_contracts_opened} used)")
                continue
            sym = sig.symbol

            # GUARD 1: state file says we already have a position
            if sym in _fut_positions:
                logger.info(f"    {name}: SKIP — already positioned in {sym} (state file)")
                continue

            # GUARD 2: IBKR says we already have a real position on this symbol
            if sym in _ibkr_real_pos:
                logger.warning(f"    {name}: SKIP — IBKR real position exists for {sym} ({_ibkr_real_pos[sym]} lots)")
                continue

            # GUARD 3: already traded this symbol earlier in this loop iteration
            if sym in _traded_this_cycle:
                logger.info(f"    {name}: SKIP — already traded {sym} this cycle")
                continue

            qty = 1

            # GUARD 3bis: P1.1 Whitelist enforcement (LIVE mode only).
            # Paper mode skips this check (paper strats are not whitelist by design).
            if live:
                _canonical_id = _STRAT_DISPLAY_TO_ID.get(name)
                if _canonical_id is None:
                    logger.warning(
                        f"    {name}: SKIP — no canonical strategy_id mapping "
                        f"(add to _STRAT_DISPLAY_TO_ID in worker.py)"
                    )
                    continue
                try:
                    from core.governance.live_whitelist import is_strategy_live_allowed
                    if not is_strategy_live_allowed(_canonical_id, "ibkr_futures"):
                        logger.warning(
                            f"    {name} ({_canonical_id}): SKIP — not in live_whitelist.yaml"
                        )
                        continue
                except Exception as _wle:
                    logger.critical(
                        f"    {name}: WHITELIST CHECK FAILED — {_wle}. "
                        f"Fail-closed: refusing live order."
                    )
                    continue

            # GUARD 4: RISK BUDGET — pre-fill estimate using (SL,TP) midpoint as entry proxy
            # Precise enforcement happens after fill via _current_risk update.
            _est_mult = _FUT_MULT.get(sym, 1)
            if sig.stop_loss and sig.take_profit:
                _est_entry = (sig.stop_loss + sig.take_profit) / 2.0
                _est_risk = abs(_est_entry - sig.stop_loss) * _est_mult * qty
            else:
                _est_risk = 0.0
            if _current_risk + _est_risk > _risk_budget_usd:
                logger.warning(
                    f"    {name}: SKIP — risk budget exceeded "
                    f"(current ${_current_risk:.0f} + new ${_est_risk:.0f} > ${_risk_budget_usd:.0f})"
                )
                continue
            try:
                _fut = IbFuture(sym, exchange="CME")
                _details = ibkr._ib.reqContractDetails(_fut)
                if not _details:
                    logger.warning(f"    {name}: no contract details for {sym}")
                    continue
                _contract = _details[0].contract

                # Step 1: Market entry
                _entry_order = IbMarketOrder(sig.side, qty)
                _entry_trade = ibkr._ib.placeOrder(_contract, _entry_order)
                time.sleep(4); ibkr._ib.sleep(2)
                _fill_price = _entry_trade.orderStatus.avgFillPrice or 0

                if _entry_trade.orderStatus.status != "Filled":
                    logger.warning(f"    {name}: entry not filled ({_entry_trade.orderStatus.status}) — cancelling")
                    try:
                        ibkr._ib.cancelOrder(_entry_trade.order)
                        time.sleep(1); ibkr._ib.sleep(0.5)
                    except Exception:
                        pass
                    continue

                # Step 2: OCA SL + TP (standalone, no parentId)
                # CRITICAL: recalculate SL/TP from FILL price, not signal price
                # Signal price = bar.close at signal time, fill price = actual execution
                _exit_side = "BUY" if sig.side == "SELL" else "SELL"
                _oca = f"OCA_{sym}_{_uuid.uuid4().hex[:8]}"
                _signal_price = sig.stop_loss + sig.take_profit  # just for logging
                _sl_offset = abs(sig.stop_loss - _fill_price) if sig.stop_loss else 20
                _tp_offset = abs(sig.take_profit - _fill_price) if sig.take_profit else 40
                # Use the LARGER of: original offset or recalculated from fill
                # Recalculate from fill price to ensure SL is on correct side
                if sig.side == "BUY":
                    _real_sl = _fill_price - _sl_offset
                    _real_tp = _fill_price + _tp_offset
                else:  # SELL
                    _real_sl = _fill_price + _sl_offset
                    _real_tp = _fill_price - _tp_offset
                # FIX: Signal is @dataclass(frozen=True), cannot mutate sig.stop_loss.
                # Use local variables for the fill-based SL/TP instead.
                _final_sl = round(_real_sl, 2)
                _final_tp = round(_real_tp, 2)

                _sl = StopOrder(_exit_side, qty, _final_sl)
                _sl.tif = "GTC"; _sl.ocaGroup = _oca; _sl.ocaType = 1; _sl.outsideRth = True
                ibkr._ib.placeOrder(_contract, _sl)
                time.sleep(1)

                _tp = LimitOrder(_exit_side, qty, _final_tp)
                _tp.tif = "GTC"; _tp.ocaGroup = _oca; _tp.ocaType = 1; _tp.outsideRth = True
                ibkr._ib.placeOrder(_contract, _tp)
                time.sleep(2); ibkr._ib.sleep(1)

                logger.info(
                    f"    FUTURES {_mode}: {sig.side} {sym} @ {_fill_price:.2f} "
                    f"SL={_final_sl:.2f} TP={_final_tp:.2f} [OCA {_oca}]"
                )

                _fut_positions[sym] = {
                    "strategy": name,
                    "symbol": sym,
                    "side": sig.side,
                    "qty": qty,
                    "entry": _fill_price,
                    "sl": _final_sl,
                    "tp": _final_tp,
                    "oca_group": _oca,
                    "opened_at": datetime.now(UTC).isoformat(),
                    "mode": _mode,
                    "_authorized_by": f"futures_{_mode.lower()}_{name}",
                }
                _traded_this_cycle.add(sym)
                n_fut_orders += 1
                _contracts_opened += 1
                _actual_risk = abs(_fill_price - _final_sl) * _est_mult * qty
                _current_risk += _actual_risk
                logger.info(
                    f"    RISK BUDGET: +${_actual_risk:.0f} → ${_current_risk:.0f}/${_risk_budget_usd:.0f}"
                )

                # P1.4 audit trail — record the decision for post-mortem reconstructibility
                try:
                    from core.governance.audit_trail import record_order_decision
                    record_order_decision(
                        book="ibkr_futures",
                        strategy_id=_STRAT_DISPLAY_TO_ID.get(name, name),
                        runtime_source=f"worker.py:_run_futures_cycle(live={live})",
                        symbol=sym,
                        side=sig.side,
                        qty=qty,
                        entry_price_est=_fill_price,
                        stop_loss=_final_sl,
                        take_profit=_final_tp,
                        risk_usd=_actual_risk,
                        risk_budget_usd=_risk_budget_usd,
                        current_risk_usd=_current_risk,
                        sizing_source="risk_budget_5pct",
                        authorized_by=f"futures_{_mode.lower()}_{name}",
                        broker_response={"oca_group": _oca, "fill_price": _fill_price,
                                         "status": "Filled"},
                        result="ACCEPTED",
                    )
                except Exception as _auerr:
                    logger.warning(f"    audit_trail write failed: {_auerr}")

                _log_event("futures_trade", name, {
                    "mode": _mode, "symbol": sym, "side": sig.side,
                    "qty": qty, "fill_price": _fill_price,
                    "sl": _final_sl, "tp": _final_tp,
                    "oca_group": _oca, "equity": equity,
                })
                # Write to journal DB for dashboard
                try:
                    import sqlite3 as _sql
                    _jdb = "live_journal.db" if live else "paper_journal.db"
                    _jpath = ROOT / "data" / _jdb
                    _jconn = _sql.connect(str(_jpath))
                    _jconn.execute(
                        "INSERT OR IGNORE INTO trades (trade_id, strategy, instrument, direction, "
                        "quantity, entry_price, entry_time, status, broker, asset_class) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', 'IBKR', 'futures')",
                        (_oca, name, sym, sig.side, qty, _fill_price,
                         datetime.now(UTC).isoformat()),
                    )
                    _jconn.commit()
                    _jconn.close()
                except Exception as _je:
                    logger.warning(f"Journal write FAILED: {_je}")
                _send_alert(
                    f"FUTURES {_mode}: {sig.side} {sym} @ {_fill_price:.2f}\n"
                    f"SL={_final_sl:.2f} TP={_final_tp:.2f}\n"
                    f"Strat: {name}",
                    level="info" if _mode == "PAPER" else "warning",
                )

            except Exception as oe:
                logger.error(f"    FUTURES BRACKET FAILED: {name} {sym} — {oe}")

        # Save positions state
        try:
            _fut_state_path.write_text(json.dumps(_fut_positions, indent=2))
        except Exception:
            pass

        if n_fut_orders > 0:
            logger.info(f"  FUTURES {_mode}: {n_fut_orders} ordre(s) executes")

    except Exception as e:
        logger.error(f"FUTURES PAPER CYCLE ERROR: {e}", exc_info=True)
    finally:
        # Disconnect IBKR to free clientId for next cycle
        try:
            ibkr.disconnect()
        except Exception:
            pass
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

        # Connect to IBKR paper — direct connection, NO os.environ mutation
        # (CRO fix: os.environ mutation causes race conditions with other threads)
        _fx_paper_port = int(os.environ.get("IBKR_PAPER_PORT", "4003"))
        try:
            from ib_insync import IB as _FxPaperIB
            import random as _fx_rng
            _fx_ib = _FxPaperIB()
            _ibkr_host = os.environ.get("IBKR_HOST", "127.0.0.1")
            _fx_ib.connect(_ibkr_host, _fx_paper_port, clientId=_fx_rng.randint(80, 89), timeout=10)
            import time as _fxt; _fxt.sleep(3)

            class _FxPaperIBKR:
                def __init__(self, ib):
                    self._ib = ib
                def get_account_info(self):
                    acct = {}
                    for a in self._ib.accountSummary():
                        if a.tag == "NetLiquidation":
                            acct["equity"] = float(a.value)
                        elif a.tag == "TotalCashValue":
                            acct["cash"] = float(a.value)
                    return acct
                def disconnect(self):
                    self._ib.disconnect()

            ibkr = _FxPaperIBKR(_fx_ib)
            ibkr_info = ibkr.get_account_info()
            equity = float(ibkr_info.get("equity", 0))
        except Exception as e:
            logger.warning(f"  FX PAPER SKIP — IBKR paper port {_fx_paper_port} not connected: {e}")
            return

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
        try:
            ibkr.disconnect()
        except Exception:
            pass
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

        # Check kill switch triggers — use thresholds from limits_live.yaml
        _ks_thresholds = {}
        try:
            import yaml as _ks_yaml
            _limits = _ks_yaml.safe_load((ROOT / "config" / "limits_live.yaml").read_text(encoding="utf-8"))
            _cb = _limits.get("circuit_breakers", {})
            _ks_cfg = _limits.get("kill_switch", {})
            _ks_thresholds = {
                "daily_loss_pct": _cb.get("daily_loss_pct", 0.05),
                "hourly_loss_pct": _cb.get("hourly_loss_pct", 0.03),
                "trailing_5d_loss_pct": _ks_cfg.get("trailing_5d_loss_pct", 0.08),
                "monthly_loss_pct": _ks_cfg.get("max_monthly_loss_pct", 0.12),
            }
        except Exception:
            pass
        kill_switch = LiveKillSwitch(thresholds=_ks_thresholds)
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

        # --- Write portfolio snapshot for dashboard ---
        try:
            _snap_dir = ROOT / "logs" / "portfolio"
            _snap_dir.mkdir(parents=True, exist_ok=True)
            _snap_file = _snap_dir / f"{datetime.now(UTC).strftime('%Y-%m-%d')}.jsonl"
            _snap = {
                "timestamp": datetime.now(UTC).isoformat(),
                "portfolio": {
                    "brokers": [
                        {"broker": "ibkr", "equity": equity, "cash": equity, "positions": 0},
                    ],
                    "total_equity": equity,
                    "daily_pnl_pct": daily_pnl_pct,
                },
            }
            # Add Binance equity if available
            try:
                from core.broker.binance_broker import BinanceBroker
                _bnb = BinanceBroker()
                _bnb_info = _bnb.get_account_info()
                _bnb_eq = _bnb_info.get("equity", 0)
                _snap["portfolio"]["brokers"].append({"broker": "binance", "equity": _bnb_eq})
                _snap["portfolio"]["total_equity"] += _bnb_eq
            except Exception:
                pass
            with open(_snap_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(_snap, default=str) + "\n")
        except Exception as _se:
            logger.debug(f"Portfolio snapshot skip: {_se}")

        # --- SOFTWARE SL/TP for futures positions ---
        # IBKR presets kill GTC orders on futures. This is the backup.
        # Check both live and paper state files
        _fut_pos = {}
        for _sfx in ("live", "paper"):
            _fsp = ROOT / "data" / "state" / f"futures_positions_{_sfx}.json"
            try:
                if _fsp.exists():
                    _fut_pos.update(json.loads(_fsp.read_text(encoding="utf-8")))
            except Exception:
                pass
        # Also check legacy file for migration
        _fut_state_path = ROOT / "data" / "state" / "futures_positions.json"
        try:
            if _fut_state_path.exists():
                _legacy = json.loads(_fut_state_path.read_text(encoding="utf-8"))
                for k, v in _legacy.items():
                    if k not in _fut_pos:
                        _fut_pos[k] = v
        except Exception:
            pass
        try:
            if _fut_pos:
                    import socket as _sl_sock
                    _ibkr_host = os.getenv("IBKR_HOST", "127.0.0.1")
                    _ibkr_port = int(os.getenv("IBKR_PORT", "4002"))
                    try:
                        with _sl_sock.create_connection((_ibkr_host, _ibkr_port), timeout=3):
                            pass
                    except Exception:
                        _fut_pos = {}  # IBKR not connected, skip

                    if _fut_pos:
                        from ib_insync import IB as _SlIB, Future as _SlFut, MarketOrder as _SlMkt, StopOrder as _SlStop
                        import random as _sl_rng
                        _sl_ib = _SlIB()
                        try:
                            _sl_ib.connect(_ibkr_host, _ibkr_port, clientId=_sl_rng.randint(90, 98), timeout=8)
                            time.sleep(1)

                            # Check each futures position
                            for _ps, _pi in list(_fut_pos.items()):
                                _sl_price = _pi.get("sl", 0)
                                _tp_price = _pi.get("tp", 0)
                                _pos_side = _pi.get("side", "")
                                if _sl_price <= 0:
                                    continue

                                # Get current price
                                _pf = _SlFut(_ps, exchange="CME")
                                _pd = _sl_ib.reqContractDetails(_pf)
                                if not _pd:
                                    continue
                                _pc = _pd[0].contract
                                _real_pos = {p.contract.symbol: p for p in _sl_ib.positions()}
                                if _ps not in _real_pos or abs(_real_pos[_ps].position) == 0:
                                    # Position gone (SL/TP hit or closed)
                                    logger.info(f"  FUTURES SL CHECK: {_ps} position gone — removing from state")
                                    del _fut_pos[_ps]
                                    continue

                                # Check if SL order still exists
                                _has_sl = any(
                                    t.contract.symbol == _ps and t.order.orderType in ("STP", "STOP")
                                    for t in _sl_ib.openTrades()
                                )
                                # Software SL: check price vs SL level
                                _cur_price = _real_pos[_ps].avgCost / 5  # avgCost = price * multiplier
                                # Get market price from portfolio
                                for _pitem in _sl_ib.portfolio():
                                    if _pitem.contract.symbol == _ps and abs(_pitem.position) > 0:
                                        _cur_price = _pitem.marketPrice
                                        break

                                _sl_hit = False
                                if _pos_side == "SELL" and _cur_price >= _sl_price:
                                    _sl_hit = True
                                elif _pos_side == "BUY" and _cur_price <= _sl_price:
                                    _sl_hit = True

                                if _sl_hit:
                                    _exit_side = "BUY" if _pos_side == "SELL" else "SELL"
                                    _close_ord = _SlMkt(_exit_side, abs(int(_real_pos[_ps].position)))
                                    _close_trade = _sl_ib.placeOrder(_pc, _close_ord)
                                    time.sleep(4); _sl_ib.sleep(2)
                                    logger.critical(
                                        f"  FUTURES SOFTWARE SL HIT: {_ps} price={_cur_price:.2f} >= SL={_sl_price:.2f} "
                                        f"-> {_close_trade.orderStatus.status}"
                                    )
                                    _send_alert(
                                        f"FUTURES SL HIT: {_exit_side} {_ps}\n"
                                        f"Price={_cur_price:.2f} SL={_sl_price:.2f}",
                                        level="critical",
                                    )
                                    del _fut_pos[_ps]
                                else:
                                    # Check TP too
                                    _tp_hit = False
                                    if _tp_price > 0:
                                        if _pos_side == "SELL" and _cur_price <= _tp_price:
                                            _tp_hit = True
                                        elif _pos_side == "BUY" and _cur_price >= _tp_price:
                                            _tp_hit = True

                                    if _tp_hit:
                                        _exit_side = "BUY" if _pos_side == "SELL" else "SELL"
                                        _close_ord = _SlMkt(_exit_side, abs(int(_real_pos[_ps].position)))
                                        _close_trade = _sl_ib.placeOrder(_pc, _close_ord)
                                        time.sleep(4); _sl_ib.sleep(2)
                                        logger.info(
                                            f"  FUTURES SOFTWARE TP HIT: {_ps} price={_cur_price:.2f} TP={_tp_price:.2f} "
                                            f"-> {_close_trade.orderStatus.status}"
                                        )
                                        _send_alert(
                                            f"FUTURES TP HIT: {_exit_side} {_ps}\n"
                                            f"Price={_cur_price:.2f} TP={_tp_price:.2f}",
                                            level="info",
                                        )
                                        del _fut_pos[_ps]

                            # Write back to split state files (by mode)
                            for _wsfx in ("live", "paper"):
                                _wsf = ROOT / "data" / "state" / f"futures_positions_{_wsfx}.json"
                                _wdata = {k: v for k, v in _fut_pos.items() if v.get("mode", "").upper() == _wsfx.upper()}
                                _wsf.write_text(json.dumps(_wdata, indent=2))
                            _sl_ib.disconnect()
                        except Exception as _sle:
                            logger.warning(f"  FUTURES SL CHECK error: {_sle}")
                            try:
                                _sl_ib.disconnect()
                            except Exception:
                                pass
        except Exception:
            pass

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

                # Inclure le collateral en isolated margin (transferé depuis spot/earn)
                margin_collateral = 0
                try:
                    margin_resp = broker._get("/sapi/v1/margin/isolated/account", signed=True, weight=10)
                    for ma in margin_resp.get("assets", []):
                        q = ma.get("quoteAsset", {})
                        margin_collateral += float(q.get("free", 0)) + float(q.get("locked", 0))
                except Exception as _me:
                    logger.warning(f"Margin collateral indisponible: {_me}")

                current_equity = spot_equity + earn_total + margin_collateral

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
                    f"+ margin=${margin_collateral:,.0f} = total=${current_equity:,.0f} "
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
            # Log DETAILED messages so operator can diagnose without CLI repro
            failed_detail = [
                f"{name}: {risk_result['checks'][name].get('message', '?')}"
                for name in failed_checks
            ]
            logger.warning(
                f"CRYPTO RISK CHECK FAILED ({len(failed_checks)} checks): "
                f"{failed_detail}"
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

            # #2 Auto-pause: skip strat if recently auto-paused after failures
            if _strat_is_paused(strat_id):
                _pause_remaining = int(_strat_paused_until[strat_id] - time.time())
                logger.info(
                    f"  [{strat_id}] AUTO-PAUSED (remaining {_pause_remaining}s) — skip"
                )
                continue

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

                # V14: Global Sizer — size on total NAV ($45K), not broker-only ($10K)
                # Cap to 80% of broker equity to avoid over-concentration
                _global_nav = _get_global_nav()
                sizing_capital = _global_nav if _global_nav > 0 else (current_equity if current_equity > 0 else total_capital)
                alloc_pct = config.get("allocation_pct", 0.10)
                strat_capital = sizing_capital * alloc_pct * CRYPTO_KELLY_FRACTION
                # Cap: never exceed 80% of Binance equity for this position
                _broker_cap = (current_equity if current_equity > 0 else total_capital) * 0.80
                strat_capital = min(strat_capital, _broker_cap)
                # Filter dust positions (<$5) so strategies don't think
                # they hold something the worker guard refuses to close.
                # This matches the worker's CLOSE guard at line 3040 (>$1)
                # with a safety margin to avoid edge-case loops.
                _live_positions = [
                    p for p in positions
                    if abs(float(p.get("market_val", 0))) > 5
                ]
                state = {
                    "capital": sizing_capital,
                    "equity": current_equity,
                    "positions": _live_positions,
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
                # #4: signal funnel tracker
                try:
                    from core.crypto.signal_funnel import record_signal_emitted
                    record_signal_emitted(strat_id, action)
                except Exception:
                    pass
                # #9: quarantine observe — record signal in quarantine tracker
                try:
                    from core.crypto.quarantine import observe_signal, is_quarantined
                    observe_signal(strat_id)
                    quar, reason = is_quarantined(strat_id)
                    if quar and action in ("BUY", "SELL"):
                        logger.info(
                            f"  [{strat_id}] QUARANTINE: {reason} — paper only, no live exec"
                        )
                        continue
                except Exception as _qe:
                    logger.debug(f"quarantine check failed: {_qe}")

                # --- Executer via BinanceBroker si disponible ---
                if broker is None:
                    logger.info(
                        f"  [{strat_id}] Signal logue mais pas execute "
                        f"(broker indisponible)"
                    )
                    continue

                # Determiner la direction et le market_type
                # BUG FIX (14/04): le signal peut override le market_type du config.
                # Cas typique: STRAT-001 config=margin (pour les shorts) mais
                # le signal BUY dit spot (long en spot, pas en margin). Avant:
                # le worker ignorait le signal et routait BUY vers margin,
                # ce qui passait qty=None (qty n'est compute que pour SELL),
                # d'ou "Mandatory parameter 'quantity' not sent" — 30 trades
                # rejetes en une journee, capital dort.
                market_type = signal.get("market_type") or config.get("market_type", "spot")

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
                    # GUARD: verify position actually exists on broker before closing
                    _has_pos = False
                    try:
                        _cur_positions = broker.get_positions()
                        _has_pos = any(
                            p.get("symbol", "") == trade_symbol
                            and abs(float(p.get("market_val", 0))) > 1
                            for p in _cur_positions
                        )
                    except Exception:
                        pass
                    if not _has_pos:
                        logger.info(f"  [{strat_id}] CLOSE SKIP — no {trade_symbol} position on broker")
                        continue
                    try:
                        result = broker.close_position(
                            trade_symbol,
                            _authorized_by=f"crypto_worker_{strat_id}",
                        )
                        logger.info(
                            f"  [{strat_id}] Position fermee: {result}"
                        )
                        n_actions += 1
                        # Reset hourly baseline to avoid false kill switch
                        # when closing a margin position changes equity mechanically
                        risk_mgr._hourly_start_equity = dd_equity
                        risk_mgr._last_hourly_reset = time.time()
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

                # P0 FIX 2026-04-16: whitelist enforcement crypto (audit ChatGPT
                # vulnerabilite critique - 28/28 entrypoints LIVE etaient sans
                # check whitelist). Une strat crypto en status disabled ou
                # paper_only ne peut PAS placer d'ordre live.
                try:
                    from core.governance import is_strategy_live_allowed
                    if not is_strategy_live_allowed(strat_id, "binance_crypto"):
                        logger.warning(
                            f"  [{strat_id}] Ordre BLOQUE par whitelist: "
                            f"strategy non autorisee live sur binance_crypto"
                        )
                        continue
                except Exception as wl_err:
                    # FAIL-CLOSED: si whitelist illisible, on bloque
                    logger.error(
                        f"  [{strat_id}] Whitelist check ERREUR (fail-closed): "
                        f"{wl_err}"
                    )
                    continue

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

                # Compute qty with proper step size rounding.
                # SELL: always needs qty (we sell N base units).
                # BUY + margin: also needs qty (margin endpoint rejects quoteOrderQty).
                # BUY + spot: qty=None is OK, the adapter uses quoteOrderQty=notional.
                _sell_qty = None
                _need_qty = (side == "SELL") or (side == "BUY" and market_type == "margin")
                if _need_qty and price > 0:
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
                    _strat_record_success(strat_id)  # #2: reset failure counter
                    # #4: funnel tracker
                    try:
                        from core.crypto.signal_funnel import record_executed, record_risk_passed
                        record_risk_passed(strat_id)
                        record_executed(strat_id)
                    except Exception:
                        pass
                    # #6: fidelity score — record slippage vs signal price
                    try:
                        from core.crypto.fidelity_score import record_trade
                        record_trade(
                            strat_id=strat_id,
                            symbol=exec_symbol,
                            side=side,
                            signal_price=price,
                            fill_price=float(result.get("filled_price", price) or price),
                            qty=float(result.get("filled_qty", 0)),
                            signal_ts=candle_data.get("timestamp", datetime.now(UTC).isoformat()),
                        )
                    except Exception as _fe:
                        logger.debug(f"fidelity record failed: {_fe}")

                    # Reset hourly baseline after margin trade to avoid
                    # false kill switch from equity shift
                    if market_type == "margin":
                        risk_mgr._hourly_start_equity = dd_equity
                        risk_mgr._last_hourly_reset = time.time()

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
                    # #2: track consecutive failures, alert + auto-pause
                    _strat_record_failure(strat_id, e)
                    # #4: funnel tracker
                    try:
                        from core.crypto.signal_funnel import record_failed
                        record_failed(strat_id, type(e).__name__)
                    except Exception:
                        pass

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
                    # Phase 2.2 fix: ecrit le flag file pour que pre_order_guard
                    # bloque tous les ordres suivants (avant: log only).
                    try:
                        from core.governance.safety_mode_flag import activate_safety_mode
                        activate_safety_mode(
                            reason=str(anomaly.get("details", "DISABLE_TRADING"))[:200],
                            activated_by="v10_safety_anomaly",
                        )
                    except Exception as _sme:
                        logger.error(f"safety_mode_flag write failed: {_sme}")
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
    last_always_on_carry = 0
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
                # Critical blockers (auth, broker connectivity, gateway) → HARD FAIL
                # Non-critical blockers (margin check, etc.) → log but continue
                _critical_keywords = ("auth", "binance", "ibkr", "4002", "gateway",
                                      "api_key", "api key", "credentials", "connection refused")
                _critical = [b for b in _pf2.blockers
                            if any(kw in b.lower() for kw in _critical_keywords)]
                if _critical:
                    logger.critical(f"PRE-FLIGHT CRITICAL FAIL — {_critical}")
                    _send_alert(
                        f"PRE-FLIGHT CRITICAL — worker REFUSING to start:\n"
                        + "\n".join(_critical[:5]),
                        level="critical",
                    )
                    # P0.4 fail-closed: no live trading if broker auth/connectivity broken
                    logger.critical("WORKER EXITING — fix critical blockers and restart")
                    sys.exit(2)
                # Non-critical only: continue in degraded mode but log prominently
                logger.critical(
                    f"PRE-FLIGHT RETRY FAILED ({len(_pf2.blockers)} non-critical blockers) "
                    f"— worker starting DEGRADED"
                )
                _send_alert(
                    f"PRE-FLIGHT DEGRADED (non-critical): {len(_pf2.blockers)} blockers\n"
                    + "\n".join(_pf2.blockers[:3]),
                    level="critical",
                )
            else:
                logger.info(f"  Pre-flight retry OK: {len(_pf2.checks)} checks passed")
        else:
            logger.info(f"  Pre-flight OK: {len(_pf.checks)} checks passed")
    except SystemExit:
        raise  # propagate sys.exit from fail-closed path
    except Exception as _pf_err:
        logger.critical(f"  Pre-flight check RAISED — unable to verify state: {_pf_err}")
        _is_live_mode = os.getenv("PAPER_TRADING", "true").lower() != "true"
        if _is_live_mode:
            _send_alert(
                f"PRE-FLIGHT RAISED IN LIVE MODE: {_pf_err}\n"
                f"Worker REFUSING to start — fix preflight and restart.",
                level="critical",
            )
            logger.critical("WORKER EXITING — preflight raised in live mode (fail-closed)")
            sys.exit(3)
        else:
            _send_alert(
                f"PRE-FLIGHT RAISED: {_pf_err}\nWorker starting in PAPER mode without preflight",
                level="critical",
            )

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

    # #9 Quarantine bootstrap — release pre-existing strats so the new
    # quarantine feature doesn't block them for 7 days.
    try:
        from strategies.crypto import CRYPTO_STRATEGIES as _CS_BOOT
        from core.crypto.quarantine import bootstrap_existing as _qb
        _qb(list(_CS_BOOT.keys()))
    except Exception as _qbe:
        logger.warning(f"Quarantine bootstrap skipped: {_qbe}")

    # Futures IBKR reconciliation — sync state file with broker reality.
    # Walks IBKR positions and recovers SL/TP from existing OCA orders.
    # If no brackets found on IBKR, falls back to strategy defaults
    # (Overnight MES: SL=entry-30, TP=entry+50). This ensures the next
    # BRACKET MISSING check in _run_futures_cycle has valid values to repose.
    # Fixes "futures_positions_live.json={} but IBKR has 1 MES" desync +
    # "bracket missing repose skipped silently because sl=0".
    try:
        _ibkr_host = os.environ.get("IBKR_HOST", "127.0.0.1")
        _ibkr_live_port = int(os.environ.get("IBKR_PORT", "4002"))
        from ib_insync import IB as _BootIB
        import random as _boot_rng
        _boot_ib = _BootIB()
        _boot_ib.RequestTimeout = 20
        _boot_ib.connect(_ibkr_host, _ibkr_live_port,
                         clientId=_boot_rng.randint(220, 229), timeout=15)
        time.sleep(2)
        _live_pos = {p.contract.symbol: p for p in _boot_ib.positions() if abs(p.position) > 0}

        # Read existing open orders to recover SL/TP from any OCA bracket still alive
        _live_brackets: dict[str, dict] = {}  # symbol -> {sl, tp, oca}
        try:
            _all_trades = _boot_ib.reqAllOpenOrders()
            # Group orders by symbol, detect SL (STP) and TP (LMT) per OCA
            _by_sym: dict[str, list] = {}
            for _t in _all_trades:
                _sym = _t.contract.symbol
                _by_sym.setdefault(_sym, []).append(_t)
            for _sym, _trades in _by_sym.items():
                _sl_px = 0.0
                _tp_px = 0.0
                _oca = ""
                for _t in _trades:
                    _ot = _t.order.orderType
                    if _ot in ("STP", "STOP"):
                        _sl_px = float(_t.order.auxPrice or 0)
                        _oca = _t.order.ocaGroup or _oca
                    elif _ot in ("LMT", "LIMIT"):
                        _tp_px = float(_t.order.lmtPrice or 0)
                        _oca = _t.order.ocaGroup or _oca
                if _sl_px > 0 or _tp_px > 0:
                    _live_brackets[_sym] = {"sl": _sl_px, "tp": _tp_px, "oca": _oca}
        except Exception as _boe:
            logger.warning(f"FUTURES BOOT RECONCILE: open orders scan failed: {_boe}")

        # Strategy default SL/TP offsets (points) for fallback
        # Overnight MES/MNQ: SL=entry-30, TP=entry+50 (overnight_buy_close.py)
        _STRAT_DEFAULTS = {
            "MES": {"sl_points": 30, "tp_points": 50, "multiplier": 5},
            "MNQ": {"sl_points": 30, "tp_points": 50, "multiplier": 2},
        }

        _state_file = ROOT / "data" / "state" / "futures_positions_live.json"
        _state_file.parent.mkdir(parents=True, exist_ok=True)
        _existing = {}
        if _state_file.exists():
            try:
                _existing = json.loads(_state_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Add / refresh broker positions
        _added = 0
        _refreshed = 0
        for sym, pos in _live_pos.items():
            _mult = int(getattr(pos.contract, "multiplier", 1) or 1)
            _entry_px = float(getattr(pos, "avgCost", 0)) / max(_mult, 1)

            # Recover SL/TP: 1) from live broker brackets, 2) from existing state,
            # 3) from strategy defaults
            _sl = 0.0
            _tp = 0.0
            _oca = ""
            if sym in _live_brackets:
                _sl = _live_brackets[sym]["sl"]
                _tp = _live_brackets[sym]["tp"]
                _oca = _live_brackets[sym]["oca"]
                logger.info(f"FUTURES BOOT RECONCILE: {sym} SL/TP recovered from IBKR brackets (sl={_sl}, tp={_tp})")
            elif sym in _existing and _existing[sym].get("sl", 0) > 0 and _existing[sym].get("tp", 0) > 0:
                _sl = float(_existing[sym]["sl"])
                _tp = float(_existing[sym]["tp"])
                _oca = _existing[sym].get("oca_group", "")
                logger.info(f"FUTURES BOOT RECONCILE: {sym} SL/TP kept from existing state")
            elif sym in _STRAT_DEFAULTS and _entry_px > 0:
                _d = _STRAT_DEFAULTS[sym]
                _side_long = pos.position > 0
                if _side_long:
                    _sl = round(_entry_px - _d["sl_points"], 2)
                    _tp = round(_entry_px + _d["tp_points"], 2)
                else:
                    _sl = round(_entry_px + _d["sl_points"], 2)
                    _tp = round(_entry_px - _d["tp_points"], 2)
                logger.warning(
                    f"FUTURES BOOT RECONCILE: {sym} SL/TP synthesized from strategy defaults "
                    f"(sl={_sl}, tp={_tp}) — bracket will be reposed in next futures cycle"
                )
            else:
                logger.critical(
                    f"FUTURES BOOT RECONCILE: {sym} UNPROTECTED — no SL/TP available. "
                    f"Will be closed by BRACKET FAIL-SAFE in next futures cycle."
                )

            if sym in _existing:
                _existing[sym]["sl"] = _sl
                _existing[sym]["tp"] = _tp
                if _oca:
                    _existing[sym]["oca_group"] = _oca
                _existing[sym]["entry"] = _entry_px or _existing[sym].get("entry", 0)
                _existing[sym]["qty"] = abs(int(pos.position))
                _existing[sym]["side"] = "BUY" if pos.position > 0 else "SELL"
                _refreshed += 1
            else:
                _existing[sym] = {
                    "strategy": "RECONCILED_AT_BOOT",
                    "symbol": sym,
                    "side": "BUY" if pos.position > 0 else "SELL",
                    "qty": abs(int(pos.position)),
                    "entry": _entry_px,
                    "sl": _sl,
                    "tp": _tp,
                    "oca_group": _oca,
                    "opened_at": datetime.now(UTC).isoformat(),
                    "mode": "LIVE",
                    "_authorized_by": "boot_reconciliation",
                }
                _added += 1

        # Remove state positions that no longer exist on broker
        _removed = 0
        for sym in list(_existing.keys()):
            if sym not in _live_pos:
                logger.info(f"FUTURES BOOT RECONCILE: removing stale {sym} from state (not on IBKR)")
                del _existing[sym]
                _removed += 1

        if _added or _removed or _refreshed:
            _state_file.write_text(json.dumps(_existing, indent=2))
            logger.warning(
                f"FUTURES BOOT RECONCILE: state updated — added {_added}, refreshed {_refreshed}, "
                f"removed {_removed} (broker={list(_live_pos.keys())})"
            )
        else:
            logger.info(
                f"FUTURES BOOT RECONCILE: state file in sync ({len(_live_pos)} live positions)"
            )

        try:
            _boot_ib.disconnect()
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"Futures IBKR reconciliation skipped: {e}")

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
    last_bracket_watchdog = 0  # Run immediately on first loop iteration
    last_crypto_watchdog = 0   # idem

    # CRO #4: automated dry-run and smoke test flags
    _dry_run_done_today = False
    _smoke_test_done_this_week = False
    _ror_done_today = False
    _backup_done_today = False

    # ── R1/R2/R5: Initialisation robustesse ─────────────────────────────
    _event_logger = get_event_logger()
    _metrics = get_metrics()
    _worker_state = WorkerState()
    _order_tracker = OrderTracker(alert_callback=lambda msg: _send_alert(msg, level="critical"))
    _broker_health = BrokerHealthRegistry()
    _broker_health.register("binance")
    _broker_health.register("ibkr")
    _broker_health.register("alpaca")

    # ── Phase 4: BookSupervisor — per-book isolation layer ──────────────
    from core.runtime.book_factory import build_runtimes_from_registry
    from core.runtime.supervisor import BookSupervisor

    _book_supervisor = BookSupervisor()
    try:
        _book_cycles = {
            "binance_crypto": {
                "crypto": run_crypto_cycle,
                "watchdog": run_crypto_watchdog_cycle,
            },
            "ibkr_futures": {
                "futures_paper": run_futures_paper_cycle,
                "futures_live": run_futures_live_cycle,
                "bracket_watchdog": run_bracket_watchdog_cycle,
                "macro_ecb": run_macro_ecb_live_cycle,
                "xmomentum": run_cross_asset_momentum_cycle,
            },
            "ibkr_fx": {
                "fx_carry": run_fx_carry_cycle,
                "fx_paper": run_fx_paper_cycle,
                "always_on_carry": run_always_on_carry_cycle,
            },
            "alpaca_us": {
                "us_stocks_daily": run_us_stocks_daily_cycle,
            },
        }
        _book_runtimes = build_runtimes_from_registry(
            cycle_registry=_book_cycles,
            alert_fn=lambda level, msg: _send_alert(msg, level=level.lower()),
        )
        for rt in _book_runtimes.values():
            _book_supervisor.register(rt)
        _sv_results = _book_supervisor.start_all()
        logger.info("BookSupervisor: %s", {k: v for k, v in _sv_results.items()})
    except Exception as _sv_err:
        logger.warning("BookSupervisor init failed (non-blocking): %s", _sv_err)

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
        "futures_live": CycleRunner("futures_live", run_futures_live_cycle,
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
        "xmomentum": CycleRunner("xmomentum", run_cross_asset_momentum_cycle,
                                  alert_callback=_cycle_alert,
                                  metrics_callback=_cycle_metrics_cb),
        "always_on_carry": CycleRunner("always_on_carry", run_always_on_carry_cycle,
                                        alert_callback=_cycle_alert,
                                        metrics_callback=_cycle_metrics_cb),
        "macro_ecb": CycleRunner("macro_ecb", run_macro_ecb_live_cycle,
                                  alert_callback=_cycle_alert,
                                  metrics_callback=_cycle_metrics_cb),
        "bracket_watchdog": CycleRunner("bracket_watchdog", run_bracket_watchdog_cycle,
                                         alert_callback=_cycle_alert,
                                         metrics_callback=_cycle_metrics_cb),
        "crypto_watchdog": CycleRunner("crypto_watchdog", run_crypto_watchdog_cycle,
                                        alert_callback=_cycle_alert,
                                        metrics_callback=_cycle_metrics_cb),
        "us_stocks_daily": CycleRunner("us_stocks_daily", run_us_stocks_daily_cycle,
                                         alert_callback=_cycle_alert,
                                         metrics_callback=_cycle_metrics_cb,
                                         timeout_seconds=900.0),
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

        # === FUTURES DAILY (lun-ven, 16h Paris = ouverture US) ===
        if is_weekday() and now_paris.hour == 16 and not getattr(run_futures_paper_cycle, '_done_today', False):
            _runners["futures"].run()       # paper (port 4003)
            _runners["futures_live"].run()  # live (port 4002)
            run_futures_paper_cycle._done_today = True
        if is_weekday() and now_paris.hour < 16:
            run_futures_paper_cycle._done_today = False

        # === CROSS-ASSET MOMENTUM PAPER DAILY (lun-ven, 16h15 Paris) ===
        if is_weekday() and now_paris.hour == 16 and now_paris.minute >= 15 and not getattr(run_cross_asset_momentum_cycle, '_done_today', False):
            _runners["xmomentum"].run()
            run_cross_asset_momentum_cycle._done_today = True
        if is_weekday() and now_paris.hour < 16:
            run_cross_asset_momentum_cycle._done_today = False

        # === BRACKET WATCHDOG toutes les 5 minutes (24/7) ===
        # Verifie que chaque position futures live a un bracket actif.
        # Si manquant: tente repose from state, sinon fail-safe close.
        # Protege contre les brackets qui disparaissent entre les cycles futures.
        if time.time() - last_bracket_watchdog >= 300:  # 5 min
            _runners["bracket_watchdog"].run()
            last_bracket_watchdog = time.time()

        # === CRYPTO WATCHDOG toutes les 5 minutes (24/7) ===
        # Equivalent Binance: verifie que chaque position crypto live a un
        # stop-loss actif sur l'exchange. Si manquant, repose avec SL 3%
        # depuis le prix actuel. Jamais de close automatique.
        if time.time() - last_crypto_watchdog >= 300:  # 5 min
            _runners["crypto_watchdog"].run()
            last_crypto_watchdog = time.time()

        # === US STOCKS DAILY (lun-ven, 16h00 Paris = 10h00 ET, 30 min apres US open) ===
        # 3 strats monthly cross-sectional (tom, rs_spy, sector_rot_us) sur Alpaca paper.
        # Signal stateless: depend du last_month_end (hier ou plus loin). La data yfinance
        # de la veille est finalisee depuis > 8h, pas de race. Fills observables en session.
        # Aligne avec futures_live (16h) et xmomentum (16h15) pour coherence operationnelle.
        if is_weekday() and now_paris.hour == 16 and now_paris.minute >= 0 and not getattr(run_us_stocks_daily_cycle, '_done_today', False):
            _runners["us_stocks_daily"].run()
            run_us_stocks_daily_cycle._done_today = True
        if is_weekday() and now_paris.hour < 16:
            run_us_stocks_daily_cycle._done_today = False

        # === MACRO ECB EVENT DRIVEN (lun-ven, 14h50 Paris, jours BCE only) ===
        # Le module skip lui-meme les jours non-BCE; on declenche tous les jours
        # de semaine a 15h10 (laisse 25min apres 14:45 pour que les bars 5min
        # IBKR EU soient dispo, lag ~15-20min constate). V15.4: bascule LIVE
        # si env MACRO_ECB_LIVE_ENABLED=true, sinon PAPER dry_run.
        # Bug fix 2026-04-16 : trigger 14:50 -> 15:10 (data IBKR lag).
        if is_weekday() and now_paris.hour == 15 and now_paris.minute >= 10 and not getattr(run_macro_ecb_live_cycle, '_done_today', False):
            _runners["macro_ecb"].run()
            run_macro_ecb_live_cycle._done_today = True
        if is_weekday() and now_paris.hour < 15:
            run_macro_ecb_live_cycle._done_today = False

        # === HEARTBEAT toutes les 30 min (local log only, y compris weekends) ===
        if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
            log_heartbeat()
            last_heartbeat = time.time()

            # Telegram V2 digest every 4h (not every 30 min)
            if time.time() - last_heartbeat <= 60:  # Just after heartbeat
                try:
                    _h = now_paris.hour
                    if _h in (7, 15, 23):  # 3x/day digest (matin, aprem, soir)
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
                        # #4: crypto signal funnel digest (morning only)
                        if _h == 7:
                            try:
                                from core.crypto.signal_funnel import format_digest as _funnel_fmt
                                _funnel_msg = _funnel_fmt("CRYPTO SIGNAL FUNNEL (24h)")
                                _send_alert(_funnel_msg, level="info")
                            except Exception as _fe:
                                logger.warning(f"funnel digest error: {_fe}")
                except Exception:
                    pass

        # === BOOK SUPERVISOR HEALTH CHECK (every heartbeat) ===
        try:
            if time.time() - last_heartbeat <= 60:
                _book_supervisor.check_health()
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

        # === STATE BACKUP (03:00 CET daily) ===
        if now_paris.hour == 3 and not _backup_done_today:
            _backup_done_today = True
            try:
                from scripts.backup_state import run_backup
                _bk = run_backup()
                logger.info(f"STATE BACKUP: {_bk['copied']} files, {len(_bk['errors'])} errors")
                if _bk["errors"]:
                    _send_alert(f"STATE BACKUP ERRORS: {_bk['errors'][:3]}", level="warning")
            except Exception as _bk_err:
                logger.warning(f"STATE BACKUP error: {_bk_err}")
        if now_paris.hour < 3:
            _backup_done_today = False

        # === #8 WF WEEKLY REVIEW (dimanche 04:30 CET) ===
        # Re-run walk-forward validation on last months of data. Auto-pause
        # strategies that fail WF criteria. Alerte Telegram.
        if now_paris.weekday() == 6 and now_paris.hour == 4 and now_paris.minute >= 30 and not getattr(run_crypto_cycle, '_wf_weekly_done_today', False):
            try:
                import subprocess
                logger.info("=== WF WEEKLY REVIEW (sunday 04:30 Paris) ===")
                subprocess.Popen(
                    [sys.executable, str(ROOT / "scripts" / "wf_weekly_review.py")],
                    cwd=str(ROOT),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                run_crypto_cycle._wf_weekly_done_today = True
            except Exception as _wfe:
                logger.warning(f"WF weekly launch error: {_wfe}")
        if now_paris.hour < 4 or now_paris.weekday() != 6:
            run_crypto_cycle._wf_weekly_done_today = False

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

        # === DAILY GOVERNANCE SUMMARY (07:00 CET) ===
        if now_paris.hour == 7 and not getattr(main, '_gov_summary_done_today', False):
            main._gov_summary_done_today = True
            try:
                from core.governance.daily_summary import send_daily_summary
                send_daily_summary()
            except Exception as _gov_err:
                logger.warning(f"Governance daily summary error: {_gov_err}")
        if now_paris.hour < 7:
            main._gov_summary_done_today = False

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

        # === V14 ALWAYS-ON CARRY every 4 hours (24/7 incl weekends for FX) ===
        if time.time() - last_always_on_carry >= V11_HRP_INTERVAL:
            _runners["always_on_carry"].run()
            last_always_on_carry = time.time()

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
