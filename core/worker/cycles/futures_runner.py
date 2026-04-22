"""Futures cycle runner — extracted from worker.py (Phase C post-XXL).

Cycle execution shared between live (port 4002) and paper (port 4003) IBKR.
Strategies executed:
  - Live: cross_asset_momentum, gold_oil_rotation (live_core)
  - Paper: gold_trend_mgc + 25+ paper_only strategies

Behavior unchanged from worker.py:_run_futures_cycle. Imports module-level
helpers (logger, ROOT, ibkr_lock, send_alert, log_event) from explicit
core.worker.* modules instead of relying on worker.py globals.

Extracted 2026-04-19 (Phase C post-XXL plan) to reduce worker.py from
6390 -> ~5200 lines and improve testability of futures execution path.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from core.worker.alerts import log_event as _log_event
from core.worker.alerts import send_alert as _send_alert
from core.worker.config import ibkr_lock as _ibkr_lock

logger = logging.getLogger("worker")
ROOT = Path(__file__).resolve().parents[3]


def _get_canonical_ibkr_account(ib) -> str | None:
    """Retourne l'account canonique IBKR pour ce cycle.

    Priorite:
    1. env IBKR_LIVE_ACCOUNT (si defini, source explicite)
    2. ib.managedAccounts()[0] (premier compte gere par la session)
    3. None (si echec -> caller garde comportement historique: pas de filtre)

    Raison d'etre: le live gateway (port 4002) peut reporter des positions
    sur des sub-accounts "DUP*" (duplicated/linked demo) qui polluent
    `_ibkr_real_pos` et font SKIP les paper strats MES sur position fantome.
    Bug observe 2026-04-20: MES -1 lot sur DUP573894 bloque 7 paper strats.
    """
    env_acct = os.environ.get("IBKR_LIVE_ACCOUNT", "").strip()
    if env_acct:
        return env_acct
    try:
        managed = ib.managedAccounts() if hasattr(ib, "managedAccounts") else []
        if managed:
            return str(managed[0])
    except Exception as exc:
        logger.warning(f"_get_canonical_ibkr_account: managedAccounts failed: {exc}")
    return None


def _filter_positions_by_account(positions, canonical_account: str | None) -> dict:
    """Filtre les positions IBKR pour ne garder que le compte canonique.

    Args:
        positions: iterable de ib_insync Position objects (avec p.account et p.contract.symbol)
        canonical_account: account id canonique (ex "U25023333") ou None (pas de filtre)

    Returns:
        dict {symbol: position_qty} pour les positions actives sur le bon compte.
    """
    if canonical_account is None:
        # Fallback: comportement historique (aucun filtre)
        return {p.contract.symbol: p.position for p in positions if abs(p.position) > 0}

    out = {}
    skipped = []
    for p in positions:
        if abs(p.position) == 0:
            continue
        if p.account != canonical_account:
            skipped.append((p.account, p.contract.symbol, p.position))
            continue
        out[p.contract.symbol] = p.position
    if skipped:
        logger.info(
            f"  Positions skipped (not on canonical account {canonical_account}): {skipped}"
        )
    return out


def run_futures_cycle(live: bool = False):
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
        # 2026-04-19 (Phase C XXL): Path(__file__).parent ne pointe plus a la racine
        # apres extraction (file est dans core/worker/cycles/), utiliser ROOT explicit.
        data_dir = ROOT / "data" / "futures"
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

        # LIVE-capable 1: Cross-Asset Momentum (PRIORITE MAX, first-refusal revisee)
        # corr MES = 0.003, 5/5 WF, alpha each year 2021-2026 (incl 2 bears).
        #
        # Phase 3.5 desk productif 2026-04-22 (decision Marc):
        # CAM ne reserve un symbole que si elle PORTE DEJA une position live
        # OU si elle est eligible a entrer aujourd'hui (rebal window ouverte).
        # En cooldown sans position, CAM ne reserve rien => GOR et mcl_overnight
        # peuvent trader MCL librement.
        # Backtest 15/04: CAM first-refusal avait donne Sharpe 0.85 -> 1.06 (+25%),
        # mais au prix de bloquer GOR (live_core) dans ~90% des cas => net
        # desk-level negatif. Nouvelle regle: live_core ne neutralise pas live_core.
        _cam_top_pick = None
        try:
            from strategies_v2.futures.cross_asset_momentum import CrossAssetMomentum
            _cam_strat = CrossAssetMomentum()
            _cam_strat.set_data_feed(feed)
            bar = feed.get_latest_bar("MES")
            if bar:
                # Note: get_top_pick(bar, portfolio_state) retourne None si CAM
                # est en cooldown + sans position. Sinon retourne le symbole
                # reserve (position active OU top momentum si eligible).
                _cam_top_pick = _cam_strat.get_top_pick(bar=bar, portfolio_state=portfolio_state)
                sig = _cam_strat.on_bar(bar, portfolio_state)
                if sig:
                    signals.append(("Cross-Asset Mom", sig))
                    logger.info(f"    Cross-Asset Mom ({_mode}): BUY {sig.symbol}")
                else:
                    logger.info(f"    Cross-Asset Mom ({_mode}): pas de rebal "
                                f"(top pick: {_cam_top_pick or 'none (cooldown+no position)'})")
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

        # PAPER STRATS - UNIQUEMENT SLEEVES CANONIQUES V16
        # ============================================================
        # Refonte 2026-04-22 (Phase 3.5 desk productif): le paper block ne
        # contient PLUS les 13+ strats legacy (MES Trend/Trend+MR/3-Day/Overnight
        # MES V2/Overnight MNQ/TSMOM/M2K ORB/MCL Brent Lag/MGC VIX Hedge/Thursday
        # Rally/Friday-Monday MNQ/Multi-TF Mom/BB Squeeze/RS MES/MNQ). Toutes
        # ces strats ont ete retirees du catalogue canonique (V15.3 "9 DISABLED
        # backtest portefeuille negatif" + drains bucket A/C). Les garder hard-
        # codees ici creait un drift code <> registry qui polluait logs +
        # executait des fills paper sur compte DUP573894 (observe 22/04).
        # Seules les sleeves paper_only presentes dans quant_registry/live_whitelist
        # sont conservees: mes_monday, mes_wednesday, mes_pre_holiday (MES calendar)
        # + mcl_overnight_mon_trend10. Ajouts futurs passent par le registry.
        if not live:
            # 3a-bis. MES calendar paper strats (T1-A INT-C promotion 2026-04-16)
            # Promus en paper_only via INT-A WF/MC validation (cf docs/research/wf_reports/INT-A_tier1_validation.md).
            # Transition paper -> live_probation apres 30j sans divergence > 2 sigma.
            try:
                from strategies_v2.futures.mes_calendar_paper import (
                    MESMondayLong, MESWednesdayLong, MESPreHolidayLong,
                )
                # Phase 3.1 desk productif 2026-04-22: skip frozen strats
                from core.governance.live_whitelist import is_strategy_frozen as _is_frozen
                _cal_classes_active = []
                for _cal_cls, _cal_sid in (
                    (MESMondayLong, "mes_monday_long_oc"),
                    (MESWednesdayLong, "mes_wednesday_long_oc"),
                    (MESPreHolidayLong, "mes_pre_holiday_long"),
                ):
                    if _is_frozen(_cal_sid):
                        logger.debug(f"    {_cal_sid}: FROZEN, skip signal gen")
                        continue
                    _cal_classes_active.append(_cal_cls)

                import pandas as _cal_pd
                # Fix 2026-04-21: passer runtime_today pour que la detection
                # weekday utilise le jour actuel du cycle, pas bar.timestamp
                # (qui peut etre close vendredi quand cycle tourne lundi 14:00).
                _runtime_today = _cal_pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
                for _cal_cls in _cal_classes_active:
                    _cal = _cal_cls()
                    _cal.set_data_feed(feed)
                    _cal.set_runtime_today(_runtime_today)
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

            # 3a-ter. MCL overnight mon trend10 paper (T3-A1 validation 2026-04-18)
            # Source: docs/research/wf_reports/T3A-01_mcl_overnight.md + INT-B
            # Sharpe +0.80, MaxDD -4.3%, WF 4/5, MC P(DD>30%) 0.0% -> VALIDATED
            # Paper -> live_probation apres 30j sans divergence > 1 sigma (resserre
            # car trigger shift friday vs monday backtest, cf docstring strat).
            if _cam_top_pick == "MCL":
                logger.info("    mcl_overnight_mon_trend10 (paper): SKIP — CAM reserved MCL")
            else:
                try:
                    from strategies_v2.futures.mcl_overnight_mon_trend import MCLOvernightMonTrend
                    _mcl_strat = MCLOvernightMonTrend()
                    _mcl_strat.set_data_feed(feed)
                    bar = feed.get_latest_bar("MCL")
                    if bar:
                        sig = _mcl_strat.on_bar(bar, portfolio_state)
                        if sig:
                            signals.append((_mcl_strat.name, sig))
                            logger.info(f"    {_mcl_strat.name} (paper): BUY @ {bar.close:.2f}")
                        else:
                            logger.info(f"    {_mcl_strat.name} (paper): pas un jour/trend pattern")
                except Exception as e:
                    logger.error(f"    MCL overnight mon trend paper error: {e}")

            # NOTE 2026-04-22 (Phase 3.5 desk productif):
            # Blocs supprimes de la rotation paper (retrait catalogue V16):
            #   - Overnight MES V2 (OvernightBuyClose) / Overnight MNQ
            #   - TSMOM MES/MNQ / M2K ORB / MCL Brent Lag / MGC VIX Hedge
            #   - Thursday Rally MES+MNQ / Friday-Monday MNQ / Multi-TF Mom MES
            #   - BB Squeeze MES / RS MES/MNQ rotation
            #   - VIX Mean Reversion (deja ARCHIVED 2026-04-19)
            # Raison: drift code <> registry. Ces strats n'etaient PAS dans
            # quant_registry.yaml ni live_whitelist.yaml, pourtant executees
            # a chaque cycle paper avec fills sur DUP573894 (paper ghost acct).
            # Les fichiers strategies_v2/futures/*.py restent presents comme
            # ref historique. Pour reactiver une strat: passage par registry.

            # (Cross-Asset Mom + Gold Trend MGC handled in live-capable block above)
        else:
            # LIVE MODE : uniquement les sleeves live_core (CAM + GOR).
            # Plus de mention de strats legacy (retirees Phase 3.5 2026-04-22).
            pass
        # NOTE 2026-04-22 (Phase 3.5 desk productif) - cleanup code<>registry drift:
        # Blocs supprimes de la rotation futures_runner (non presents dans registry V16):
        #   - `for tsmom_sym in []:` (dead code, empty loop)
        #   - TSMOM Multi / EU Gap Open / Sector Rotation EU / Gold-Equity Divergence
        #     (archived_rejected bucket C 2026-04-19)
        #   - Commodity Season MCL/MGC (jamais promu au registry)
        #   - MES/MNQ Pairs (jamais promu)
        #   - MIB/ESTX50 Spread (frozen, deja execute par run_mib_estx50_spread_paper_cycle
        #     dans core/worker/cycles/paper_cycles.py - pas besoin de 2e path)
        # Toutes les archives restent dans strategies_v2/_archive/ comme ref historique.

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
        # Fix 2026-04-21: filter sub-accounts (DUP*) pour ne garder que le live
        _canonical_acct = _get_canonical_ibkr_account(ibkr._ib)
        try:
            _ibkr_real_pos = _filter_positions_by_account(ibkr._ib.positions(), _canonical_acct)
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
                                _sl_ord.tif = "GTC"; _sl_ord.ocaGroup = _rb_oca; _sl_ord.ocaType = 1; _sl_ord.outsideRth = True
                                _tp_ord = LimitOrder(_rb_side, _rb_qty, _rb_tp)
                                _tp_ord.tif = "GTC"; _tp_ord.ocaGroup = _rb_oca; _tp_ord.ocaType = 1; _tp_ord.outsideRth = True
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
            _ibkr_real_pos = _filter_positions_by_account(ibkr._ib.positions(), _canonical_acct)
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
            _side = _pos_info.get("side", "BUY")
            if _pe > 0 and _ps > 0:
                # Trailing SL above entry = locked-in gain, risk = $0
                if _side == "BUY" and _ps >= _pe:
                    pass  # no risk, SL guarantees profit
                elif _side == "SELL" and _ps <= _pe:
                    pass  # no risk for short
                else:
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
            # T3-A1 INT-B 2026-04-18 paper promotion
            "mcl_overnight_mon_trend10": "mcl_overnight_mon_trend10",
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
                        f"(add to _STRAT_DISPLAY_TO_ID in core/worker/cycles/futures_runner.py)"
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

                # G4 iter2 plan 9.5 (2026-04-19): OSM wire symetrique crypto.
                # Create_order + validate avant placeOrder IBKR, puis submit +
                # fill OU error apres resultat. Parite avec run_crypto_cycle
                # pour crash recovery symetrique via OrderTracker atomic save.
                _osm_order = None
                try:
                    import worker as _w
                    _osm_tracker = _w.get_order_tracker() if hasattr(_w, "get_order_tracker") else None
                    if _osm_tracker is not None:
                        _osm_order = _osm_tracker.create_order(
                            symbol=sym, side=sig.side, quantity=qty,
                            broker="ibkr", strategy=name,
                        )
                        # Risk check upstream (budget + whitelist) -> validate
                        _osm_tracker.validate(_osm_order.order_id, risk_approved=True)
                except Exception as _osm_err:
                    logger.warning(f"    {name}: OSM create/validate: {_osm_err}")
                    _osm_order = None

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
                    # G4: transition OSM -> ERROR si entry pas fill
                    if _osm_order is not None:
                        try:
                            import worker as _w
                            _tracker = _w.get_order_tracker() if hasattr(_w, "get_order_tracker") else None
                            if _tracker:
                                _tracker.error(_osm_order.order_id)
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
                _tp_trade = ibkr._ib.placeOrder(_contract, _tp)
                time.sleep(2); ibkr._ib.sleep(1)

                # G4 iter2: transition OSM SUBMITTED -> FILLED avec SL id.
                # Entry DEJA remplie par IBKR (status=Filled assured above),
                # on log fill + invariant has_sl=True (SL place via _sl above).
                if _osm_order is not None:
                    try:
                        import worker as _w
                        _tracker = _w.get_order_tracker() if hasattr(_w, "get_order_tracker") else None
                        if _tracker:
                            _tracker.submit(_osm_order.order_id, str(_entry_trade.order.orderId))
                            _tracker.fill(
                                _osm_order.order_id,
                                has_sl=True,
                                sl_order_id=str(_sl.orderId) if hasattr(_sl, "orderId") else None,
                            )
                    except Exception as _osm_err:
                        logger.warning(f"    {name}: OSM submit/fill: {_osm_err}")

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
        logger.error(f"FUTURES {_mode} CYCLE ERROR: {e}", exc_info=True)
    finally:
        # Disconnect IBKR to free clientId for next cycle
        try:
            ibkr.disconnect()
        except Exception:
            pass
        _ibkr_lock.release()


