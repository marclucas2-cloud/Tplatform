"""Paper-only cycle runners extracted from worker.py.

These functions are scheduled by APScheduler and execute log-only paper trades
for strategies in observation phase before live promotion (see live_whitelist).

Extracted 2026-04-19 (Phase 2 XXL plan) for worker.py decomposition.
Behavior unchanged — pure mechanical extraction.

Cycles:
  - run_mib_estx50_spread_paper_cycle  (17h45 Paris weekday)
  - run_alt_rel_strength_paper_cycle   (03h00 Paris daily)
  - run_btc_asia_mes_leadlag_paper_cycle (10h30 Paris weekday)
  - run_us_sector_ls_paper_cycle       (22h30 Paris weekday)
  - run_eu_relmom_paper_cycle          (18h00 Paris weekday)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from core.worker.alerts import send_alert as _send_alert

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]


# -----------------------------------------------------------------------------
# MIB / ESTX50 spread paper runner
# -----------------------------------------------------------------------------

def run_mib_estx50_spread_paper_cycle():
    """MIB/ESTX50 spread — paper runner isole, 1x/jour apres close EU.

    Fetch via yfinance (^FTSEMIB.MI + ^STOXX50E), passe au runner qui
    gere etat + journal. Pas de broker, pas de capital. Trades simules
    dans data/state/mib_estx50_spread/paper_trades.jsonl.

    WF corrige: avg Sharpe 3.91, WF 4/5, +EUR22.6K sur 12 trades / 24 mois OOS.
    Promotion live a discuter apres 30j paper data.
    """
    try:
        import yfinance as yf

        from core.runtime.spread_paper_runner import SpreadPaperRunner

        runner = SpreadPaperRunner.for_mib_estx50()

        mib_raw = yf.download("^FTSEMIB.MI", period="90d", interval="1d",
                              progress=False, auto_adjust=False)
        estx_raw = yf.download("^STOXX50E", period="90d", interval="1d",
                               progress=False, auto_adjust=False)

        if mib_raw.empty or estx_raw.empty:
            logger.warning("MIB/ESTX50 SPREAD: yfinance returned empty data")
            return

        mib_close = mib_raw["Close"].squeeze() if hasattr(mib_raw["Close"], "squeeze") else mib_raw["Close"]
        estx_close = estx_raw["Close"].squeeze() if hasattr(estx_raw["Close"], "squeeze") else estx_raw["Close"]

        df = pd.DataFrame({"mib": mib_close, "estx": estx_close}).dropna()
        if len(df) < 65:
            logger.warning(f"MIB/ESTX50 SPREAD: insufficient history ({len(df)} bars)")
            return

        today_a = float(df["mib"].iloc[-1])
        today_b = float(df["estx"].iloc[-1])
        as_of = df.index[-1].date().isoformat()

        result = runner.tick(
            today_a=today_a,
            today_b=today_b,
            history_a=df["mib"],
            history_b=df["estx"],
            as_of=as_of,
        )

        action = result.get("action")
        if action == "entry":
            _send_alert(
                f"SPREAD ENTRY {result['direction']} @ {as_of}\n"
                f"{result['n_a']} MIB + {result['n_b']} ESTX50, z={result['z']:.2f}\n"
                f"PAPER ONLY (mib_estx50_spread)",
                level="info",
            )
        elif action == "exit":
            _send_alert(
                f"SPREAD EXIT @ {as_of}\n"
                f"PnL net EUR{result['pnl_net']:+.0f} ({result['reason']})\n"
                f"PAPER ONLY (mib_estx50_spread)",
                level="info",
            )
        elif action in ("hold", "no_signal", "skip"):
            logger.debug(f"MIB/ESTX50 SPREAD: {action} (z={result.get('z')})")

    except ImportError as ie:
        logger.warning(f"MIB/ESTX50 SPREAD: missing dep: {ie}")
    except Exception as e:
        logger.warning(f"MIB/ESTX50 SPREAD cycle error: {e}", exc_info=True)


# -----------------------------------------------------------------------------
# Alt relative strength paper runner (T4-A2)
# -----------------------------------------------------------------------------

def run_alt_rel_strength_paper_cycle():
    """alt_rel_strength_14_60_7 — paper runner atomic 6-leg (T4-A2 VALIDATED).

    T4-A2 VALIDATED bull/bear robust (Sharpe +1.11, WF 3/5, MC 0.5%,
    bull +$3,591, bear +$515). Source:
    scripts/research/backtest_t4_crypto_relative_strength.py +
    docs/research/wf_reports/T4A-02_crypto_relative_strength.md + INT-D.

    Cycle: daily tick (pas weekly) pour check SL + portfolio stop. Le runner
    interne decide du rebalance (Sunday + >= 7j since last). Atomic 6-leg.

    Paper mode: pas d'ordre reel. State JSON + journal JSONL:
      data/state/alt_rel_strength/state.json
      data/state/alt_rel_strength/paper_journal.jsonl
    """
    try:
        from core.runtime.alt_rel_strength_runner import (
            UNIVERSE,
            AltRelStrengthRunner,
            load_panel,
        )

        data_dir = ROOT / "data" / "crypto" / "candles"
        state_dir = ROOT / "data" / "state" / "alt_rel_strength"
        state_dir.mkdir(parents=True, exist_ok=True)

        panel = load_panel(data_dir, UNIVERSE)
        if len(panel) < 100:
            logger.warning(
                f"alt_rel_strength: insufficient history ({len(panel)} days), skip"
            )
            return

        last_bar = panel.index[-1]
        now_utc = pd.Timestamp.utcnow().tz_localize(None).normalize()
        age_days = (now_utc - last_bar).days
        if age_days > 7:
            logger.warning(
                f"alt_rel_strength: data stale ({age_days}d since {last_bar.date()}), "
                f"skip tick. Fix parquet refresh cron."
            )
            return

        runner = AltRelStrengthRunner(
            state_path=state_dir / "state.json",
            journal_path=state_dir / "paper_journal.jsonl",
            paper=True,
        )
        result = runner.tick(last_bar, panel)

        if result.action == "rebalance":
            closes = result.rotation_plan.get("closes", [])
            ol = result.rotation_plan.get("opens_long", [])
            os_ = result.rotation_plan.get("opens_short", [])
            logger.info(
                f"alt_rel_strength paper: REBALANCE @ {last_bar.date()} "
                f"close={len(closes)} open_long={ol} open_short={os_} "
                f"realized=${result.daily_pnl_usd:+.0f}"
            )
        elif result.action == "init":
            ol = result.rotation_plan.get("opens_long", [])
            os_ = result.rotation_plan.get("opens_short", [])
            logger.info(
                f"alt_rel_strength paper: INIT @ {last_bar.date()} "
                f"longs={ol} shorts={os_}"
            )
        elif result.action in ("cascade_close", "portfolio_stop"):
            logger.warning(
                f"alt_rel_strength paper: {result.action.upper()} @ {last_bar.date()} "
                f"stops={result.stops_triggered} pnl=${result.daily_pnl_usd:+.0f}"
            )
        elif result.action == "hold":
            logger.debug(
                f"alt_rel_strength paper: hold @ {last_bar.date()} "
                f"n_pos={len(result.positions_after)} unrealized={result.portfolio_unrealized_pct:+.3%}"
            )
        elif result.action == "warmup":
            logger.debug(f"alt_rel_strength paper: warmup @ {last_bar.date()}")
    except ImportError as ie:
        logger.warning(f"alt_rel_strength: missing dep: {ie}")
    except Exception as e:
        logger.error(f"alt_rel_strength cycle error: {e}", exc_info=True)


# -----------------------------------------------------------------------------
# BTC/MES Asia session lead-lag paper runner (T3-A2)
# -----------------------------------------------------------------------------

def run_btc_asia_mes_leadlag_paper_cycle():
    """BTC/MES Asia session lead-lag — paper retrospective log-only.

    T3-A2 VALIDATED (Sharpe +1.07, WF 4/5, MC P(DD>30%)=0%). Source:
    scripts/research/backtest_t3a_mes_btc_leadlag.py, docs/research/wf_reports/
    T3A-02_mes_btc_asia_leadlag.md.

    Tourne 1x/jour vers 10:30 Paris (~08:30 UTC summer) apres close BTC Asia
    session (08:00 UTC). Calcule retrospectivement le signal pour la session
    qui vient de se terminer (yesterday UTC), simule entry at open 00:00 UTC /
    exit at close 07:59 UTC, logge dans paper journal JSONL.
    """
    try:
        from strategies.crypto.btc_asia_mes_leadlag import (
            build_daily_dataset,
            compute_signal_for_date,
            data_is_fresh,
            simulate_paper_trade,
        )

        mes_path = ROOT / "data" / "futures" / "MES_1H_YF2Y.parquet"
        btc_path = ROOT / "data" / "crypto" / "candles" / "BTCUSDT_1h.parquet"
        journal_path = ROOT / "data" / "state" / "btc_asia_mes_leadlag" / "paper_journal.jsonl"
        journal_path.parent.mkdir(parents=True, exist_ok=True)

        if not mes_path.exists() or not btc_path.exists():
            logger.warning("btc_asia_mes_leadlag: data files missing (MES_1H_YF2Y or BTCUSDT_1h)")
            return

        mes = pd.read_parquet(mes_path)
        btc = pd.read_parquet(btc_path)

        stale_counter_path = journal_path.parent / "stale_counter.json"
        if not data_is_fresh(mes, btc, max_age_days=3):
            try:
                counter = json.loads(stale_counter_path.read_text(encoding="utf-8")) if stale_counter_path.exists() else {"count": 0, "last_skip_utc": None}
            except Exception:
                counter = {"count": 0, "last_skip_utc": None}
            counter["count"] = int(counter.get("count", 0)) + 1
            counter["last_skip_utc"] = pd.Timestamp.utcnow().isoformat()
            stale_counter_path.write_text(json.dumps(counter), encoding="utf-8")
            if counter["count"] >= 3:
                logger.error(
                    f"btc_asia_mes_leadlag: DATA STALE {counter['count']} cycles consecutifs. "
                    f"Fix BTCUSDT_1h.parquet + MES_1H_YF2Y cron refresh."
                )
            else:
                logger.warning("btc_asia_mes_leadlag: data stale (>3j), skip")
            return
        if stale_counter_path.exists():
            try:
                stale_counter_path.unlink()
            except Exception:
                pass

        daily = build_daily_dataset(mes, btc)
        now_utc = pd.Timestamp.utcnow().tz_localize(None)
        target_date = (now_utc - pd.Timedelta(days=1)).normalize()

        if target_date not in daily.index:
            logger.info(f"btc_asia_mes_leadlag: target {target_date.date()} not in daily")
            return

        signal = compute_signal_for_date(daily, target_date, rolling_window=365, mode="both")
        if signal is None:
            logger.info(f"btc_asia_mes_leadlag: no signal computable for {target_date.date()}")
            return

        trade = simulate_paper_trade(daily, signal)

        journaled_dates: set[str] = set()
        if journal_path.exists():
            for line in journal_path.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                    d = entry.get("target_date")
                    if d:
                        journaled_dates.add(d)
                except Exception:
                    pass
        if target_date.isoformat() in journaled_dates:
            logger.debug(f"btc_asia_mes_leadlag: already journaled {target_date.date()}")
            return

        entry_dict = {
            "target_date": target_date.isoformat(),
            "logged_at_utc": now_utc.isoformat(),
            "side": signal.side,
            "mes_sig": signal.mes_sig,
            "mes_vol": signal.mes_vol,
            "signal_thr": signal.signal_thr,
            "vol_thr": signal.vol_thr,
            "rolling_window": signal.rolling_window_used,
            "entry_price": trade.entry_price,
            "exit_price": trade.exit_price,
            "notional_usd": trade.notional_usd,
            "gross_ret": trade.gross_ret,
            "cost_pct": trade.cost_pct,
            "pnl_usd": trade.pnl_usd,
        }
        with journal_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry_dict) + "\n")

        logger.info(
            f"btc_asia_mes_leadlag paper: {signal.side} @ {target_date.date()} "
            f"pnl ${trade.pnl_usd:+.0f} (mes_sig={signal.mes_sig:+.4f}, thr={signal.signal_thr:.4f})"
        )
    except ImportError as ie:
        logger.warning(f"btc_asia_mes_leadlag: missing dep: {ie}")
    except Exception as e:
        logger.error(f"btc_asia_mes_leadlag cycle error: {e}", exc_info=True)


# -----------------------------------------------------------------------------
# Shared relmom paper tick (us_sector + eu_relmom)
# -----------------------------------------------------------------------------

def _run_relmom_paper_tick(
    name: str,
    returns: pd.DataFrame,
    state_path: Path,
    journal_path: Path,
    lookback: int,
    hold_days: int,
    capital_per_leg: float,
    rt_cost_pct: float,
    as_of_date: pd.Timestamp,
) -> None:
    """Shared logic for us_sector_ls + eu_relmom paper runners.

    Load state, call tick(), save new state, append journal. Idempotent per
    (name, as_of_date): skip if journal already has an entry for this date.
    """
    from strategies_v2.us.us_sector_ls import SectorLSPositions, tick

    state_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.parent.mkdir(parents=True, exist_ok=True)

    as_of_iso = as_of_date.isoformat()
    if journal_path.exists():
        for line in journal_path.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
                if e.get("as_of_date") == as_of_iso:
                    logger.debug(f"{name} paper: already journaled {as_of_date.date()}")
                    return
            except Exception:
                pass

    state = SectorLSPositions()
    if state_path.exists():
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
            state.positions = raw.get("positions", {})
            lr = raw.get("last_rebalance")
            state.last_rebalance = pd.Timestamp(lr) if lr else None
        except Exception as e:
            logger.warning(f"{name}: state reload failed, reset: {e}")

    new_state, result = tick(
        state, as_of_date, returns,
        lookback=lookback, hold_days=hold_days,
        capital_per_leg=capital_per_leg, rt_cost_pct=rt_cost_pct,
    )

    # Journal entry FIRST (review N2 fix: si crash entre journal et state,
    # journal aura l'entry -> next run skip via dedup, state mis a jour
    # plus tard sans double-trade. L'inverse perdrait le journal definitivement.)
    entry = {
        "as_of_date": as_of_iso,
        "logged_at_utc": pd.Timestamp.utcnow().isoformat(),
        "action": result.action,
        "long_sector": result.long_sector,
        "short_sector": result.short_sector,
        "positions_after": result.positions_after,
        "day_pnl_usd": result.day_pnl_usd,
        "turnover_cost_usd": result.turnover_cost_usd,
        "net_pnl_usd": result.net_pnl_usd,
    }
    with journal_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    state_path.write_text(json.dumps({
        "positions": new_state.positions,
        "last_rebalance": new_state.last_rebalance.isoformat() if new_state.last_rebalance else None,
    }), encoding="utf-8")

    logger.info(
        f"{name} paper: {result.action} @ {as_of_date.date()} "
        f"pnl ${result.net_pnl_usd:+.2f} "
        f"(long={result.long_sector or 'hold'}, short={result.short_sector or 'hold'})"
    )


# -----------------------------------------------------------------------------
# US sector long/short 40_5 paper runner (T3-B1)
# -----------------------------------------------------------------------------

def run_us_sector_ls_paper_cycle():
    """US sector long/short 40_5 — paper retrospective log-only.

    T3-B1 VALIDATED (Sharpe +0.39, MaxDD -2.1%, WF 3/5, MC P(DD>30%)=0%).
    Source: scripts/research/backtest_t3b_us_sector_ls.py.

    Cycle 22h30 Paris weekday (apres close US 22h00 ete/21h00 hiver). Calcule
    tick pour yesterday UTC (dernier trading day US), etat + journal.
    """
    try:
        from strategies_v2.us.us_sector_ls import (
            DEFAULT_CAPITAL_PER_LEG,
            DEFAULT_HOLD_DAYS,
            DEFAULT_LOOKBACK,
            DEFAULT_RT_COST_PCT,
            load_sector_return_matrix,
        )

        us_dir = ROOT / "data" / "us_stocks"
        meta_path = us_dir / "_metadata.csv"
        if not meta_path.exists():
            logger.warning("us_sector_ls: _metadata.csv missing")
            return

        returns = load_sector_return_matrix(us_dir, meta_path)
        if len(returns) < DEFAULT_LOOKBACK + 1:
            logger.warning(f"us_sector_ls: insufficient history ({len(returns)} days)")
            return

        as_of = returns.index[-1]
        state_path = ROOT / "data" / "state" / "us_sector_ls" / "state.json"
        journal_path = ROOT / "data" / "state" / "us_sector_ls" / "paper_journal.jsonl"

        _run_relmom_paper_tick(
            name="us_sector_ls_40_5",
            returns=returns,
            state_path=state_path,
            journal_path=journal_path,
            lookback=DEFAULT_LOOKBACK,
            hold_days=DEFAULT_HOLD_DAYS,
            capital_per_leg=DEFAULT_CAPITAL_PER_LEG,
            rt_cost_pct=DEFAULT_RT_COST_PCT,
            as_of_date=as_of,
        )
    except ImportError as ie:
        logger.warning(f"us_sector_ls: missing dep: {ie}")
    except Exception as e:
        logger.error(f"us_sector_ls cycle error: {e}", exc_info=True)


# -----------------------------------------------------------------------------
# EU indices relmom 40_3 paper runner (T3-A3)
# -----------------------------------------------------------------------------

def run_eu_relmom_paper_cycle():
    """EU indices relmom 40_3 — paper retrospective log-only.

    T3-A3 VALIDATED (Sharpe +0.71, MaxDD -0.8%, WF 4/5, MC P(DD>30%)=0%).
    Source: scripts/research/backtest_t3a_eu_indices_relmom.py.

    Cycle 18h00 Paris weekday (apres close EU 17h30). Calcule tick pour today
    (dernier trading day EU dispo), etat + journal.
    """
    try:
        from strategies_v2.eu.eu_relmom import (
            DEFAULT_CAPITAL_PER_LEG,
            DEFAULT_HOLD_DAYS,
            DEFAULT_LOOKBACK,
            DEFAULT_RT_COST_PCT,
            EU_UNIVERSE,
            load_eu_returns,
        )

        data_dir = ROOT / "data" / "futures"
        available = [s for s in EU_UNIVERSE if (data_dir / f"{s}_1D.parquet").exists()]
        if len(available) < 2:
            logger.warning(f"eu_relmom: insufficient indices ({available})")
            return

        returns = load_eu_returns(data_dir)
        if len(returns) < DEFAULT_LOOKBACK + 1:
            logger.warning(f"eu_relmom: insufficient history ({len(returns)} days)")
            return

        as_of = returns.index[-1]
        state_path = ROOT / "data" / "state" / "eu_relmom" / "state.json"
        journal_path = ROOT / "data" / "state" / "eu_relmom" / "paper_journal.jsonl"

        _run_relmom_paper_tick(
            name="eu_relmom_40_3",
            returns=returns,
            state_path=state_path,
            journal_path=journal_path,
            lookback=DEFAULT_LOOKBACK,
            hold_days=DEFAULT_HOLD_DAYS,
            capital_per_leg=DEFAULT_CAPITAL_PER_LEG,
            rt_cost_pct=DEFAULT_RT_COST_PCT,
            as_of_date=as_of,
        )
    except ImportError as ie:
        logger.warning(f"eu_relmom: missing dep: {ie}")
    except Exception as e:
        logger.error(f"eu_relmom cycle error: {e}", exc_info=True)
